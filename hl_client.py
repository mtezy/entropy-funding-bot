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
    def _sign(self, action):
        data = msgpack.packb(action, use_bin_type=True)
        connection_id = "0x" + keccak(data).hex()
        typed = {
            "types": AGENT_TYPES,
            "primaryType": "Agent",
            "domain": DOMAIN,
            "message": {"source": "a" if self.mainnet else "b",
                        "connectionId": connection_id},
        }
        signed = self.acct.sign_message(encode_typed_data(full_message=typed))
        return {
            "r": "0x" + signed.r.to_bytes(32, "big").hex(),
            "s": "0x" + signed.s.to_bytes(32, "big").hex(),
            "v": signed.v,
        }

    def post_action(self, action, dry=False):
        nonce = int(time.time() * 1000)
        if dry or not self.acct:
            return {"dry": True, "action": action, "nonce": nonce}
        body = {
            "action": action,
            "nonce": nonce,
            "signature": self._sign(action),
            "vaultAddress": None,
        }
        r = self.s.post(EXCHANGE_URL, json=body, timeout=self.timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "parse_error", "code": r.status_code, "text": r.text[:400]}

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
