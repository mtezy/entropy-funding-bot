"""
hl_client.py — Hyperliquid client for builder-deployed perp dexes (entropy.io style).

Covers:
  - info queries (books, meta, funding, positions, referral, builder fees)
  - EIP-712 order signing (msgpack -> keccak -> phantom agent -> Agent typed data)
  - orders: post-only (Alo), IOC, GTC, reduce-only
  - leverage + isolated margin management
  - builder fee attachment (monetization) and referral code setting
  - fill verification (never trust an order response)

Signing flow:
  1. msgpack-encode the action
  2. connectionId = keccak256(msgpack bytes)
  3. phantom agent = {"source": "a" (mainnet) | "b" (testnet), "connectionId": <hash>}
  4. EIP-712 sign with primaryType "Agent"
  5. POST {"action", "nonce", "signature": {r,s,v}, "vaultAddress"}
"""
import time

import msgpack
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

INFO_URL = "https://api.hyperliquid.xyz/info"
EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"

DOMAIN = {
    "name": "Exchange",
    "version": "1",
    "chainId": 1337,
    "verifyingContract": "0x0000000000000000000000000000000000000000",
}
AGENT_TYPES = {
    "Agent": [
        {"name": "source", "type": "string"},
        {"name": "connectionId", "type": "bytes32"},
    ]
}

SEND_ASSET_TYPES = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "destination", "type": "string"},
    {"name": "sourceDex", "type": "string"},
    {"name": "destinationDex", "type": "string"},
    {"name": "token", "type": "string"},
    {"name": "amount", "type": "string"},
    {"name": "fromSubAccount", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]

SET_ABSTRACTION_TYPES = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "user", "type": "address"},
    {"name": "abstraction", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]

DEX_ABSTRACTION_TYPES = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "user", "type": "address"},
    {"name": "enabled", "type": "bool"},
    {"name": "nonce", "type": "uint64"},
]


def _fmt(x):
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


class HL:
    def __init__(self, private_key=None, mainnet=True, timeout=15,
                 builder=None, builder_fee=None):
        """
        builder      : 0x address that collects builder fees (your monetization address)
        builder_fee  : fee in TENTHS of a basis point (10 = 1 bp, 100 = 10 bps = 0.1% perp cap)
        """
        self.timeout = timeout
        self.mainnet = mainnet
        self.acct = Account.from_key(private_key) if private_key else None
        self.address = self.acct.address if self.acct else None
        self.builder = builder
        self.builder_fee = int(builder_fee) if builder_fee else None
        self.s = requests.Session()
        self.s.headers.update({"content-type": "application/json"})

    # ------------------------------------------------------------------ info
    def info(self, payload, tries=3):
        last = None
        for i in range(tries):
            try:
                r = self.s.post(INFO_URL, json=payload, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}: {r.text[:120]}"
            except Exception as e:
                last = str(e)
            time.sleep(0.4 * (i + 1))
        raise RuntimeError(f"info failed: {last}")

    def book(self, coin):
        return self.info({"type": "l2Book", "coin": coin})

    def meta(self, dex=""):
        return self.info({"type": "meta", "dex": dex} if dex else {"type": "meta"})

    def meta_and_ctxs(self, dex=""):
        return self.info({"type": "metaAndAssetCtxs", "dex": dex} if dex
                         else {"type": "metaAndAssetCtxs", "dex": ""})

    def ctxs(self, dex=""):
        meta, ctxs = self.meta_and_ctxs(dex)
        return {m["name"]: c for m, c in zip(meta["universe"], ctxs)}

    def funding_history(self, coin, hours=168):
        now = int(time.time() * 1000)
        return self.info({"type": "fundingHistory", "coin": coin,
                          "startTime": now - hours * 3600_000})

    def user_state(self, dex=""):
        p = {"type": "clearinghouseState", "user": self.address}
        if dex:
            p["dex"] = dex
        return self.info(p)

    def open_orders(self, dex=""):
        p = {"type": "openOrders", "user": self.address}
        if dex:
            p["dex"] = dex
        return self.info(p)

    def fills(self):
        return self.info({"type": "userFills", "user": self.address})

    def fees(self):
        return self.info({"type": "userFees", "user": self.address})

    def referral(self):
        return self.info({"type": "referral", "user": self.address})

    def max_builder_fee(self, builder):
        return self.info({"type": "maxBuilderFee", "user": self.address, "builder": builder})

    def approved_builders(self):
        return self.info({"type": "approvedBuilders", "user": self.address})

    # -------------------------------------------------------------- positions
    def position(self, coin, dex=""):
        st = self.user_state(dex)
        for p in st.get("assetPositions", []):
            if p["position"]["coin"] == coin:
                return p["position"]
        return None

    def all_positions(self):
        """Every non-zero position across all dexes."""
        out = []
        for dex in ("", "io", "xyz", "para", "mkts"):
            try:
                st = self.user_state(dex)
            except Exception:
                continue
            for p in st.get("assetPositions", []):
                pos = p["position"]
                if float(pos.get("szi", 0) or 0) != 0:
                    pos["_dex"] = dex or "main"
                    out.append(pos)
        return out

    def account_value(self, dex=""):
        st = self.user_state(dex)
        return float(st.get("marginSummary", {}).get("accountValue", 0) or 0)

    def withdrawable(self, dex=""):
        return float(self.user_state(dex).get("withdrawable", 0) or 0)

    # --------------------------------------------------------------- signing
    def _action_hash(self, action, nonce, vault_address=None, expires_after=None):
        """
        Hyperliquid's action hash. NOT just the msgpack of the action.

        keccak( msgpack(action)
                + nonce as 8-byte big-endian
                + 0x00 (no vault) | 0x01 + 20-byte vault address
                + [0x00 + expiresAfter as 8-byte big-endian, if set] )

        Omitting the nonce here makes every signature unrecoverable: the exchange
        computes a different hash, recovers a random address, and rejects with
        "User or API Wallet 0x... does not exist" — a different 0x every call.
        """
        data = msgpack.packb(action, use_bin_type=True)
        data += int(nonce).to_bytes(8, "big")
        if vault_address is None:
            data += b"\x00"
        else:
            data += b"\x01"
            data += bytes.fromhex(vault_address[2:].lower())
        if expires_after is not None:
            data += b"\x00"
            data += int(expires_after).to_bytes(8, "big")
        return keccak(data)

    def _sign(self, action, nonce, vault_address=None, expires_after=None):
        connection_id = "0x" + self._action_hash(
            action, nonce, vault_address, expires_after).hex()
        typed = {
            "types": AGENT_TYPES,
            "primaryType": "Agent",
            "domain": DOMAIN,
            "message": {"source": "a" if self.mainnet else "b",
                        "connectionId": connection_id},
        }
        signed = self.acct.sign_message(encode_typed_data(full_message=typed))
        v = signed.v
        if v in (0, 1):
            v += 27
        return {
            "r": "0x" + signed.r.to_bytes(32, "big").hex(),
            "s": "0x" + signed.s.to_bytes(32, "big").hex(),
            "v": v,
        }

    def recover_signer(self, action, nonce, signature, vault_address=None,
                       expires_after=None):
        """
        Recover the address the exchange will see. Use this to prove a signature
        is valid BEFORE sending — it is the only cheap way to catch a signing bug.
        """
        connection_id = "0x" + self._action_hash(
            action, nonce, vault_address, expires_after).hex()
        typed = {
            "types": AGENT_TYPES,
            "primaryType": "Agent",
            "domain": DOMAIN,
            "message": {"source": "a" if self.mainnet else "b",
                        "connectionId": connection_id},
        }
        return Account.recover_message(
            encode_typed_data(full_message=typed),
            vrs=[signature["v"], int(signature["r"], 16), int(signature["s"], 16)],
        )

    def post_action(self, action, dry=False):
        nonce = int(time.time() * 1000)
        if dry or not self.acct:
            return {"dry": True, "action": action, "nonce": nonce}
        sig = self._sign(action, nonce)
        body = {
            "action": action,
            "nonce": nonce,
            "signature": sig,
            "vaultAddress": None,
        }
        r = self.s.post(EXCHANGE_URL, json=body, timeout=self.timeout)
        try:
            out = r.json()
        except Exception:
            return {"status": "parse_error", "code": r.status_code, "text": r.text[:400]}
        # a signing fault shows up as a recovered address that is not ours
        if isinstance(out, dict) and out.get("status") == "err":
            msg = str(out.get("response", ""))
            if "does not exist" in msg:
                got = self.recover_signer(action, nonce, sig)
                if got.lower() != self.address.lower():
                    out["_diagnosis"] = (
                        f"SIGNING BUG: signature recovers to {got} but should be "
                        f"{self.address}. Fix the action hash, not the account."
                    )
                else:
                    out["_diagnosis"] = (
                        f"Signature is correct (recovers to {self.address}). "
                        f"This wallet has never deposited to Hyperliquid — "
                        f"fund it before trading."
                    )
        return out

    # ---------------------------------------------------------------- orders
    def _builder_field(self):
        if self.builder and self.builder_fee:
            return {"b": self.builder, "f": int(self.builder_fee)}
        return None

    def order(self, asset_id, is_buy, px, sz, tif="Alo", reduce_only=False,
              cloid=None, dry=False):
        """tif: Alo = post-only (maker only) | Ioc | Gtc."""
        o = {
            "a": asset_id, "b": bool(is_buy), "p": _fmt(px), "s": _fmt(sz),
            "r": bool(reduce_only), "t": {"limit": {"tif": tif}},
        }
        if cloid:
            o["c"] = cloid
        action = {"type": "order", "orders": [o], "grouping": "na"}
        b = self._builder_field()
        if b:
            action["builder"] = b
        return self.post_action(action, dry=dry)

    def bulk_orders(self, orders, dry=False):
        action = {"type": "order", "orders": orders, "grouping": "na"}
        b = self._builder_field()
        if b:
            action["builder"] = b
        return self.post_action(action, dry=dry)

    def cancel(self, asset_id, oid, dry=False):
        return self.post_action(
            {"type": "cancel", "cancels": [{"a": asset_id, "o": oid}]}, dry=dry)

    def cancel_all(self, dex, asset_id, dry=False):
        if dry or not self.acct:
            return {"dry": True, "note": "would cancel all"}
        oo = self.open_orders(dex)
        cancels = [{"a": asset_id, "o": o["oid"]} for o in oo]
        if not cancels:
            return {"status": "ok", "note": "nothing to cancel"}
        return self.post_action({"type": "cancel", "cancels": cancels})

    # -------------------------------------------------------------- account
    def set_leverage(self, asset_id, leverage, is_cross=False, dry=False):
        return self.post_action({
            "type": "updateLeverage", "asset": asset_id,
            "isCross": bool(is_cross), "leverage": int(leverage),
        }, dry=dry)

    def add_isolated_margin(self, asset_id, usd, is_buy=True, dry=False):
        return self.post_action({
            "type": "updateIsolatedMargin", "asset": asset_id,
            "isBuy": bool(is_buy), "ntli": int(round(usd * 1_000_000)),
        }, dry=dry)

    # -------------------------------------------------- builder fee / referral
    def approve_builder_fee(self, builder, max_fee_tenths_bps, dry=False):
        """Main wallet must approve before a builder fee can be charged."""
        return self.post_action({
            "type": "approveBuilderFee",
            "maxFeeRate": f"{max_fee_tenths_bps / 10000:.4%}",
            "builder": builder,
        }, dry=dry)

    def set_referrer(self, code, dry=False):
        """Attach a referral code. User gets a fee discount; referrer earns a share."""
        return self.post_action({"type": "setReferrer", "code": code}, dry=dry)

    # ------------------------------------------------------- user-signed actions
    def _sign_user_action(self, action, payload_types, primary_type):
        """
        User-signed actions (sendAsset, approveBuilderFee, ...) use a different
        EIP-712 domain from order actions: name HyperliquidSignTransaction,
        chainId taken from action["signatureChainId"].
        """
        data = {
            "domain": {
                "name": "HyperliquidSignTransaction",
                "version": "1",
                "chainId": int(action["signatureChainId"], 16),
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "types": {
                primary_type: payload_types,
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
            },
            "primaryType": primary_type,
            "message": action,
        }
        signed = self.acct.sign_message(encode_typed_data(full_message=data))
        return {
            "r": "0x" + signed.r.to_bytes(32, "big").hex(),
            "s": "0x" + signed.s.to_bytes(32, "big").hex(),
            "v": signed.v,
        }

    def send_asset(self, destination, source_dex, dest_dex, amount,
                   token="USDC", dry=False):
        """
        Move collateral between accounts. This is how you fund a builder dex.

            source_dex / dest_dex: "" = main perp account, "spot" = spot wallet,
                                   "xyz" / "para" / "io" / "mkts" = that dex

        USDC is separate per dex — a balance on the main perp account is NOT
        usable to trade xyz:TSLA. You must send it to the xyz dex first.
        """
        nonce = int(time.time() * 1000)
        action = {
            "type": "sendAsset",
            "destination": destination,
            "sourceDex": source_dex,
            "destinationDex": dest_dex,
            "token": token,
            "amount": _fmt(amount),
            "fromSubAccount": "",
            "nonce": nonce,
            "signatureChainId": "0x66eee",
            "hyperliquidChain": "Mainnet" if self.mainnet else "Testnet",
        }
        if dry or not self.acct:
            return {"dry": True, "action": action}

        sig = self._sign_user_action(action, SEND_ASSET_TYPES,
                                     "HyperliquidTransaction:SendAsset")
        body = {"action": action, "nonce": nonce, "signature": sig, "vaultAddress": None}
        r = self.s.post(EXCHANGE_URL, json=body, timeout=self.timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "parse_error", "code": r.status_code, "text": r.text[:400]}

    def balances(self):
        """USDC available on spot, main perp, and every builder dex."""
        out = {}
        try:
            st = self.info({"type": "spotClearinghouseState", "user": self.address})
            for b in st.get("balances", []):
                if b.get("coin") == "USDC":
                    out["spot"] = float(b.get("total", 0) or 0)
        except Exception:
            pass
        for dex in ("", "io", "xyz", "para", "mkts"):
            try:
                out[dex or "main"] = self.account_value(dex)
            except Exception:
                out[dex or "main"] = 0.0
        return out

    # ------------------------------------------------------- account abstraction
    def abstraction(self):
        """
        'disabled' | 'unifiedAccount' | 'portfolioMargin'

        This decides whether USDC has to be moved between dexes at all.

            disabled        collateral is held PER DEX. A balance on main does
                            nothing for xyz. You must sendAsset to each dex.
            unifiedAccount  one USDC balance on spot backs positions on every
                            dex. NO transfer needed — sendAsset is pointless.
            portfolioMargin cross-margin portfolio across the whole account.
        """
        try:
            m = self.info({"type": "userAbstraction", "user": self.address})
            # the API answers "default" for wallets that never set a mode,
            # which behaves exactly like per-dex collateral
            if m in (None, "default"):
                return "disabled"
            return m
        except Exception:
            return "disabled"

    def dex_abstraction_enabled(self):
        """Legacy per-dex abstraction flag."""
        try:
            return bool(self.info({"type": "userDexAbstraction", "user": self.address}))
        except Exception:
            return False

    def spot_usdc(self):
        """(total, hold, available) for spot USDC. hold = margin already committed."""
        try:
            st = self.info({"type": "spotClearinghouseState", "user": self.address})
            for b in st.get("balances", []):
                if b.get("coin") == "USDC":
                    tot = float(b.get("total", 0) or 0)
                    hold = float(b.get("hold", 0) or 0)
                    return tot, hold, tot - hold
        except Exception:
            pass
        return 0.0, 0.0, 0.0

    def spendable(self, dex=""):
        """
        USDC you can actually put to work on `dex`. This is the number that
        matters before an order — not the per-dex accountValue, which reads 0
        under unifiedAccount even when you have plenty.
        """
        mode = self.abstraction()
        if mode in ("unifiedAccount", "portfolioMargin"):
            tot, hold, avail = self.spot_usdc()
            # under unified, the per-dex value plus free spot is what you can use
            try:
                alloc = self.account_value(dex)
            except Exception:
                alloc = 0.0
            return avail + alloc
        return self.account_value(dex)

    def set_abstraction(self, mode, dry=False):
        """mode: 'disabled' | 'unifiedAccount' | 'portfolioMargin'"""
        if mode not in ("disabled", "unifiedAccount", "portfolioMargin"):
            raise ValueError("mode must be disabled | unifiedAccount | portfolioMargin")
        nonce = int(time.time() * 1000)
        action = {
            "type": "userSetAbstraction",
            "user": self.address.lower(),
            "abstraction": mode,
            "nonce": nonce,
            "signatureChainId": "0x66eee",
            "hyperliquidChain": "Mainnet" if self.mainnet else "Testnet",
        }
        if dry or not self.acct:
            return {"dry": True, "action": action}
        sig = self._sign_user_action(action, SET_ABSTRACTION_TYPES,
                                     "HyperliquidTransaction:UserSetAbstraction")
        body = {"action": action, "nonce": nonce, "signature": sig, "vaultAddress": None}
        r = self.s.post(EXCHANGE_URL, json=body, timeout=self.timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "parse_error", "code": r.status_code, "text": r.text[:400]}

    def set_dex_abstraction(self, enabled, dry=False):
        """Legacy per-dex abstraction toggle."""
        nonce = int(time.time() * 1000)
        action = {
            "type": "userDexAbstraction",
            "user": self.address.lower(),
            "enabled": bool(enabled),
            "nonce": nonce,
            "signatureChainId": "0x66eee",
            "hyperliquidChain": "Mainnet" if self.mainnet else "Testnet",
        }
        if dry or not self.acct:
            return {"dry": True, "action": action}
        sig = self._sign_user_action(action, DEX_ABSTRACTION_TYPES,
                                     "HyperliquidTransaction:UserDexAbstraction")
        body = {"action": action, "nonce": nonce, "signature": sig, "vaultAddress": None}
        r = self.s.post(EXCHANGE_URL, json=body, timeout=self.timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "parse_error", "code": r.status_code, "text": r.text[:400]}

    def deposit_history(self, limit=10):
        """Ledger entries — proves whether funds ever arrived at this address."""
        try:
            lu = self.info({"type": "userNonFundingLedgerUpdates", "user": self.address})
            return lu[:limit] if isinstance(lu, list) else []
        except Exception:
            return []

    # ---------------------------------------------------------- verification
    def verify_fill(self, coin, want_szi, tol=0.02, tries=8, wait=0.7, dex=""):
        """Poll until the position matches want_szi (signed). Returns (ok, actual)."""
        actual = 0.0
        for _ in range(tries):
            p = self.position(coin, dex)
            actual = float(p["szi"]) if p else 0.0
            if abs(actual - want_szi) <= max(tol, abs(want_szi) * 0.01):
                return True, actual
            time.sleep(wait)
        p = self.position(coin, dex)
        return False, (float(p["szi"]) if p else 0.0)
