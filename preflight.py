"""
preflight.py — check everything before the bot runs. Never sends an order.

Usage:
    python3 preflight.py --key-file wallet.key
    python3 preflight.py --key-file wallet.key --notional 1000 --fix
    python3 preflight.py --auto --key-file wallet.key

Checks:
  1. private key valid, address derives
  2. account equity (must cover margin for both legs)
  3. leverage on each leg (strictIsolated markets must be isolated)
  4. current price, basis, carry APR
  5. OI capacity vs the notional you want to use
  6. 168h funding regime: same sign as now, or already flipped
  7. builder fee approval status (if you monetize)
  8. referral code status
"""
import argparse
import json
import os
import statistics
import sys
import time

from hl_client import HL

HOURS_YEAR = 24 * 365


def leg(hl, coin):
    dex = coin.split(":")[0] if ":" in coin else ""
    ctxs = hl.ctxs(dex)
    if coin not in ctxs:
        raise SystemExit(f"  !! {coin} not found in dex '{dex or 'main'}'")
    c = ctxs[coin]
    meta = hl.meta(dex)
    idx = next((i for i, a in enumerate(meta["universe"]) if a["name"] == coin), None)
    m = meta["universe"][idx]
    hist = hl.funding_history(coin, 168) or []
    rates = [float(x["fundingRate"]) for x in hist if "fundingRate" in x]
    return {
        "coin": coin, "dex": dex or "main", "aid": (200000 + idx) if dex else idx,
        "szd": m["szDecimals"], "lev": m["maxLeverage"],
        "mode": m.get("marginMode"),
        "funding": float(c["funding"]),
        "hist": rates,
        "mid": float(c.get("midPx") or c.get("markPx") or 0),
        "oi_usd": float(c["openInterest"]) * float(c.get("midPx") or 0),
        "vol24": float(c["dayNtlVlm"]),
    }


def bar(ok, label, detail=""):
    print(f"  [{'OK ' if ok else '!! '}] {label:<30} {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--auto", action="store_true", help="pick the best pair via scanner")
    ap.add_argument("--long", dest="long_coin", default=None)
    ap.add_argument("--short", dest="short_coin", default=None)
    ap.add_argument("--notional", type=float, default=1000)
    ap.add_argument("--lev", type=int, default=3)
    ap.add_argument("--min-apr", type=float, default=80)
    ap.add_argument("--min-persist", type=float, default=0.75)
    ap.add_argument("--builder", default=None, help="builder address to verify")
    ap.add_argument("--fix", action="store_true", help="set leverage if not set")
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if not key:
        raise SystemExit("need --key-file or HL_PRIVATE_KEY")

    hl = HL(private_key=key)
    print("\n=== PREFLIGHT ===\n")

    import config
    config.print_banner()

    bar(True, "wallet address", hl.address)

    # ---- pair selection ----
    if args.auto:
        from scanner import scan
        rows = [r for r in scan(hl, min_apr=args.min_apr, max_basis=40)
                if r["persistence"] >= args.min_persist]
        if not rows:
            print("\n  !! no pair passes the persistence filter")
            return 1
        best = rows[0]
        args.long_coin, args.short_coin = best["long"], best["short"]
        bar(True, "auto-selected", f"{best['base']}  {best['long']} / {best['short']}")
    if not (args.long_coin and args.short_coin):
        raise SystemExit("need --auto, or both --long and --short")

    # ---- equity ----
    eq = 0.0
    try:
        eq = hl.account_value()
        wd = hl.withdrawable()
        bar(True, "account value", f"${eq:,.2f}   withdrawable ${wd:,.2f}")
    except Exception as e:
        bar(False, "read equity", str(e)[:60])

    # ---- legs ----
    try:
        L = leg(hl, args.long_coin)
        S = leg(hl, args.short_coin)
    except SystemExit as e:
        print(e)
        return 1

    for x, tag in ((L, "LONG "), (S, "SHORT")):
        bar(True, f"{tag} {x['coin']}",
            f"id={x['aid']} szDec={x['szd']} maxLev={x['lev']}x mode={x['mode']}")
        print(f"          price {x['mid']:.4f}   funding {x['funding']*1e4:+.2f} bps/hr   "
              f"OI ${x['oi_usd']:,.0f}   vol24 ${x['vol24']:,.0f}")

    # ---- carry from history, not snapshot ----
    n = min(len(L["hist"]), len(S["hist"]))
    if n >= 12:
        diff = [-L["hist"][i] + S["hist"][i] for i in range(n)]
        mean = statistics.mean(diff)
        sd = statistics.pstdev(diff)
        persist = sum(1 for d in diff if d > 0) / n
        worst24 = min(sum(diff[i:i + 24]) for i in range(n - 24)) if n > 24 else min(diff)
    else:
        mean = -L["funding"] + S["funding"]
        sd = persist = 0.0
        worst24 = mean * 24

    bps_hr = mean * 1e4
    apr = mean * HOURS_YEAR * 100
    entry_bps = 18.0
    payback = entry_bps / bps_hr if bps_hr > 0 else float("inf")

    print()
    bar(apr >= args.min_apr, "carry (168h mean)",
        f"{bps_hr:+.3f} bps/hr = {apr:+.0f}% APR   (min {args.min_apr:.0f}%)")
    bar(persist >= args.min_persist, "persistence",
        f"{persist*100:.0f}% of 168h same sign   (min {args.min_persist*100:.0f}%)")
    bar(payback < 48, "payback",
        f"{payback:.1f}h to recover {entry_bps:.0f} bps entry cost")
    bar(True, "worst 24h window", f"{worst24*1e4:+.1f} bps   (this is your bad case)")
    bar(True, "volatility", f"stdev {sd*1e4:.2f} bps/hr")

    basis = (L["mid"] / S["mid"] - 1) * 1e4
    bar(abs(basis) < 40, "basis", f"{basis:+.1f} bps   (entry blocked above 40)")
    cap = min(L["oi_usd"], S["oi_usd"])
    bar(args.notional <= cap * 0.25, "capacity",
        f"notional ${args.notional:,.0f} vs 25% OI ${cap*0.25:,.0f}")

    lev = min(args.lev, L["lev"], S["lev"])
    margin = (args.notional / lev) * 2
    bar(eq >= margin * 1.2, "margin",
        f"need ~${margin:,.0f} at {lev}x + buffer, have ${eq:,.0f}")

    # ---- leverage ----
    if args.fix:
        for x in (L, S):
            r = hl.set_leverage(x["aid"], lev, is_cross=False)
            print(f"          set {x['coin']} -> {lev}x isolated : {json.dumps(r)[:80]}")
    else:
        print(f"  [--] leverage not set (use --fix to apply {lev}x isolated)")

    # ---- builder fee / referral ----
    print()
    if args.builder:
        try:
            mf = hl.max_builder_fee(args.builder)
            ok = int(mf or 0) > 0
            bar(ok, "builder fee approved",
                f"max {int(mf or 0)} tenths-bps = {int(mf or 0)/10:.1f} bps"
                + ("" if ok else "  -> run setup_builder.py"))
        except Exception as e:
            bar(False, "builder fee", str(e)[:50])
    try:
        ref = hl.referral()
        rb = ref.get("referredBy")
        stage = (ref.get("referrerState") or {}).get("stage")
        bar(True, "referral", f"referredBy={rb or '-'}  stage={stage or '-'}")
    except Exception:
        pass

    # ---- verdict ----
    ok_all = (apr >= args.min_apr and persist >= args.min_persist
              and args.notional <= cap * 0.25 and eq >= margin * 1.2
              and abs(basis) < 40)
    print()
    print("=== RESULT:", "READY TO RUN" if ok_all else "NOT READY — see !! above", "===\n")
    if ok_all:
        print("  next:")
        print(f"    python3 arb_bot.py --long {args.long_coin} --short {args.short_coin} \\")
        print(f"        --notional {args.notional:.0f} --min-apr {args.min_apr:.0f}")
        print("    (add --live when confident)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
