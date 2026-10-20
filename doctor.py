"""
doctor.py — why isn't my command working? Checks everything, prints the fix.

    python3 doctor.py --key-file wallet.key --coin xyz:TSLA --notional 15

Read-only. Sends no orders and moves no funds.

The most common failure by far: USDC on Hyperliquid is SEPARATE PER DEX.
Depositing to the main perp account does NOT give you margin on xyz, para,
io or mkts. You have to send it to that dex explicitly.
"""
import argparse
import json
import os
import sys

import fees as F

OK, BAD, WARN = "OK  ", "FAIL", "WARN"


def line(status, label, msg):
    print(f"  [{status}] {label:<26} {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--address", default=None,
                    help="read-only check of any address, no key needed")
    ap.add_argument("--coin", default="xyz:TSLA")
    ap.add_argument("--notional", type=float, default=15)
    ap.add_argument("--lev", type=int, default=3)
    ap.add_argument("--tier", default="tier1", choices=list(F.TIERS))
    args = ap.parse_args()

    print("\n=== DOCTOR ===\n")
    problems = []

    # ---- 1. python deps ----
    missing = []
    for mod in ("requests", "msgpack", "eth_account", "eth_utils"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        line(BAD, "python packages", "missing: " + ", ".join(missing))
        problems.append(f"pip install requests msgpack eth-account")
    else:
        line(OK, "python packages", "requests msgpack eth_account eth_utils")
    if missing:
        print("\n  FIX: " + problems[-1] + "\n")
        return 1

    from hl_client import HL

    # ---- 2. key file (or read-only address) ----
    if args.address:
        hl = HL()
        hl.address = args.address
        line(OK, "read-only mode", args.address)
        return _diagnose(hl, args, None)

    key = os.environ.get("HL_PRIVATE_KEY")
    src = "HL_PRIVATE_KEY env"
    if not key and args.key_file:
        if not os.path.exists(args.key_file):
            line(BAD, "key file", f"{args.key_file} does not exist")
            print(f"\n  FIX: echo \"0xYOUR_PRIVATE_KEY\" > {args.key_file} && chmod 600 {args.key_file}\n")
            return 1
        key = open(args.key_file).read().strip()
        src = args.key_file
    if not key:
        line(BAD, "key file", "no --key-file and no HL_PRIVATE_KEY")
        return 1

    # common paste mistakes
    raw = key
    if key.startswith("0x") or key.startswith("0X"):
        body = key[2:]
    else:
        body = key
        line(WARN, "key format", "no 0x prefix (accepted, but check it is the key not the address)")
    if len(body) != 64:
        line(BAD, "key length", f"{len(body)} hex chars, expected 64")
        print("\n  FIX: a private key is 64 hex chars. You may have pasted the address")
        print("       or a mnemonic instead. Check the file contents.\n")
        return 1
    if body.startswith("0x") or " " in raw or '"' in raw or "'" in raw:
        line(BAD, "key format", "contains quotes, spaces, or a doubled 0x")
        print("\n  FIX: the file must contain only the key, nothing else:\n")
        print("       echo '0xabc...' > wallet.key\n")
        return 1
    try:
        int(body, 16)
    except ValueError:
        line(BAD, "key format", "not valid hex")
        return 1
    line(OK, "key file", f"{src} (64 hex chars)")

    # ---- 3. address derives ----
    try:
        hl = HL(private_key=key)
    except Exception as e:
        line(BAD, "key derives", str(e)[:80])
        return 1
    line(OK, "wallet address", hl.address)

    return _diagnose(hl, args, key)


def _diagnose(hl, args, key):
    problems = []
    # ---- 4. balances, respecting the abstraction mode ----
    dex_of_coin = args.coin.split(":")[0] if ":" in args.coin else ""
    need_dex = dex_of_coin or "main"

    try:
        mode = hl.abstraction()
    except Exception:
        mode = "disabled"
    unified = mode in ("unifiedAccount", "portfolioMargin")
    tot_spot, hold, avail_spot = hl.spot_usdc()
    bal = hl.balances()

    print(f"\n  --- balances ---")
    line(OK, "abstraction", mode +
         ("  (one spot balance backs all dexes)" if unified
          else "  (collateral held per dex)"))

    if unified:
        line(OK if tot_spot > 0 else BAD, "spot USDC",
             f"${tot_spot:,.2f} total, ${hold:,.2f} held, ${avail_spot:,.2f} free")
    else:
        for k in ("spot", "main", "io", "xyz", "para", "mkts"):
            v = bal.get(k, 0.0)
            mark = "  <- you need money HERE" if k == need_dex else ""
            line(OK if v > 0 else WARN, f"  {k}", f"${v:,.2f}{mark}")

    total = sum(bal.values())
    have_dex = hl.spendable(dex_of_coin)
    line(OK if total > 0 else BAD, "total across accounts", f"${total:,.2f}")
    line(OK if have_dex > 0 else BAD, f"spendable on {need_dex}",
         f"${have_dex:,.2f}" + ("  (unified)" if unified else ""))

    # ---- 5. market exists ----
    print(f"\n  --- market {args.coin} ---")
    try:
        dex = dex_of_coin
        meta = hl.meta(dex)
        asset = None
        idx = None
        for i, a in enumerate(meta["universe"]):
            if a.get("name") == args.coin:
                asset, idx = a, i
                break
        if not asset:
            line(BAD, "market found", f"{args.coin} is not listed on dex '{dex or 'main'}'")
            names = [a["name"] for a in meta["universe"]][:10]
            print(f"       did you mean one of: {', '.join(names)}")
            return 1
        aid = (200000 if dex else 0) + idx
        szd = asset["szDecimals"]
        mlev = asset["maxLeverage"]
        gm = asset.get("growthMode") == "enabled"
        dfs = float(asset.get("deployerFeeScale", 1.0))
        line(OK, "market found", f"assetId={aid} szDecimals={szd} maxLev={mlev}x")
        line(OK if gm else WARN, "growth mode",
             "enabled (fees 0.1x)" if gm else "DISABLED — fees are 10x higher")
    except Exception as e:
        line(BAD, "market lookup", str(e)[:80])
        return 1

    # ---- 6. size + price ----
    try:
        b = hl.book(args.coin)
        bb = float(b["levels"][0][0]["px"])
        ba = float(b["levels"][1][0]["px"])
        mid = (bb + ba) / 2
        spread_bps = (ba - bb) / mid * 1e4
        line(OK, "book", f"bid {bb:g} / ask {ba:g}  spread {spread_bps:.2f} bps")
    except Exception as e:
        line(BAD, "book", f"cannot read order book: {e}")
        return 1

    minsz = 10 ** -szd
    min_notional = minsz * mid
    sz = round(args.notional / mid, szd)
    actual = sz * mid
    line(OK, "min order size", f"{minsz:g} units = ${min_notional:.2f} at this price")
    if sz < minsz:
        line(BAD, "your order size", f"${args.notional} -> {args.notional/mid:.6f} units, "
                                     f"rounds to {sz:g} < minimum {minsz:g}")
        problems.append(f"--notional must be at least ${min_notional * 1.05:.0f}")
    else:
        line(OK, "your order size", f"${args.notional} -> {sz:g} units = ${actual:.2f}")

    # ---- 7. margin ----
    lev = min(args.lev, mlev)
    margin_per_leg = actual / lev
    margin_both = margin_per_leg * 2
    buffer_need = margin_both * 1.5
    print(f"\n  --- margin ---")
    line(OK, "leverage", f"{lev}x isolated (market max {mlev}x)")
    line(OK, "margin per leg", f"${margin_per_leg:,.2f}")
    line(OK, "margin both legs", f"${margin_both:,.2f}")
    if have_dex < buffer_need:
        line(BAD, f"spendable on '{need_dex}'", f"${have_dex:,.2f} < ${buffer_need:,.2f} needed")
        if unified and avail_spot > 0:
            problems.append(
                f"you have ${avail_spot:,.2f} free on spot but need ${buffer_need:,.2f}. "
                f"Under '{mode}' the spot balance backs every dex, so this is a "
                f"capital problem, not a transfer problem — deposit "
                f"${buffer_need - have_dex:,.2f} more, or lower --notional")
        elif not unified and total > buffer_need:
            problems.append(
                f"your money is on another account — move ${buffer_need:.2f} "
                f"to the '{need_dex}' dex:\n"
                f"         python3 fund_dex.py --to {need_dex} --amount {buffer_need:.2f} --key-file wallet.key")
        else:
            problems.append(f"deposit at least ${buffer_need - total:,.2f} more USDC")
    else:
        line(OK, f"spendable on '{need_dex}'", f"${have_dex:,.2f} (need ${buffer_need:,.2f})")

    # ---- 8. fees ----
    print(f"\n  --- fees ---")
    gross = F.gross_bps(True, dex, gm, dfs)
    net = F.net_bps(True, dex, gm, dfs, args.tier)
    line(OK, "maker fee", f"{gross:.2f} bps gross -> {net:.2f} bps net ({args.tier})")
    cost = actual * 2 * net / 1e4
    line(OK, "round trip cost", f"${cost:.4f} per fill pair")
    if have_dex > 0 and cost > have_dex * 0.5:
        line(WARN, "fee vs balance", "fees are a large share of your balance")

    # ---- 9. flow ----
    print(f"\n  --- liquidity ---")
    try:
        tr = hl.info({"type": "recentTrades", "coin": args.coin}) or []
        import time as _t
        now = _t.time() * 1000
        t5 = [x for x in tr if now - float(x["time"]) < 300000]
        vol5 = sum(float(x["px"]) * float(x["sz"]) for x in t5)
        if len(t5) >= 5:
            line(OK, "flow (5 min)", f"{len(t5)} trades, ${vol5:,.0f} notional")
        else:
            line(WARN, "flow (5 min)", f"only {len(t5)} trades — fills will be slow")
    except Exception as e:
        line(WARN, "flow", str(e)[:60])

    # ---- 10. referral ----
    print(f"\n  --- account ---")
    try:
        ref = hl.referral()
        rb = ref.get("referredBy")
        line(OK if rb else WARN, "referredBy", rb or "none — attribution missing")
        st = ref.get("referrerState", {}).get("stage")
        cv = float(ref.get("cumVlm") or 0)
        line(OK, "referrer stage", f"{st}  cumVlm ${cv:,.0f}")
    except Exception as e:
        line(WARN, "referral", str(e)[:60])

    # ---- verdict ----
    print("\n=== VERDICT ===")
    if problems:
        print("  NOT READY\n")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print()
        return 1

    print("  READY\n")
    print(f"  python3 volume_run.py --coin {args.coin} --notional {args.notional} \\")
    print(f"      --target 100000 --live --i-used-referral --key-file wallet.key\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
