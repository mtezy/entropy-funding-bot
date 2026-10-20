"""
volume_planner.py — how much volume can a given amount of capital actually push?

    python3 volume_planner.py --capital 15
    python3 volume_planner.py --capital 50 --target 1000000
    python3 volume_planner.py --capital 15 --growth-only --live-flow

The one equation that matters:

    max volume = capital / net_fee_rate

Leverage does not enter it. Trade size does not enter it. Only the net fee rate.
Double the leverage and you do half the round trips at twice the size — same volume,
same total fee. The only ways to buy more volume per dollar are:

    1. trade growth-mode markets  (fees x0.1)
    2. get a higher rebate tier   (Tier 2 OG = 120% of Entropy's half)
    3. use maker orders           (half the fee of taker)

WARNING ON WASH TRADING
    entropy docs, verbatim: "Wash trading, pre-arranged trading, self-referral, and order
    forms of inauthentic behavior will lead to loss of all pending and future rewards."
    Self-matching to inflate volume is exactly what that clause covers. Real liquidity
    provision (maker orders filled by other people) is not wash trading — but if you are
    both sides of the trade, you forfeit everything and the fees are a pure loss.
"""
import argparse
import json
import time
import urllib.request

import fees as F


def post(payload):
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=25))


def growth_markets():
    out = []
    for dex in ("io", "para", "xyz", "mkts"):
        try:
            m = post({"type": "meta", "dex": dex})
        except Exception:
            continue
        for a in m.get("universe", []):
            if a.get("isDelisted"):
                continue
            if a.get("growthMode") == "enabled":
                out.append({
                    "coin": a["name"], "dex": dex,
                    "lev": a["maxLeverage"], "szd": a["szDecimals"],
                    "dfs": float(a.get("deployerFeeScale", 1.0)),
                })
    return out


def flow_scan(markets, min_trades=5, window_min=5):
    """Which growth markets are actually trading right now."""
    now = time.time() * 1000
    res = []
    for m in markets:
        try:
            tr = post({"type": "recentTrades", "coin": m["coin"]})
        except Exception:
            continue
        recent = [t for t in tr if now - float(t["time"]) < window_min * 60 * 1000]
        if len(recent) < min_trades:
            continue
        notional = sum(float(t["px"]) * float(t["sz"]) for t in recent)
        res.append({**m, "trades": len(recent), "vol": notional})
    res.sort(key=lambda r: -r["vol"])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, required=True, help="USD you are willing to spend on fees")
    ap.add_argument("--target", type=float, default=1_000_000, help="volume target")
    ap.add_argument("--buffer", type=float, default=2.0,
                    help="keep 1/buffer of equity intact (2.0 = keep 50%%)")
    ap.add_argument("--tier", default="tier1", choices=list(F.TIERS))
    ap.add_argument("--growth-only", action="store_true", help="only show growth-mode markets")
    ap.add_argument("--live-flow", action="store_true", help="also find growth markets trading right now")
    args = ap.parse_args()

    print(f"\ncapital ${args.capital:,.2f}   target ${args.target:,.0f} volume   "
          f"tier {args.tier} ({F.TIERS[args.tier]*100:.0f}% of Entropy's half)   "
          f"buffer {args.buffer:.1f}x\n")

    rows = [
        ("main dex maker",       True,  "",     False),
        ("main dex taker",       False, "",     False),
        ("HIP-3 normal maker",   True,  "para", False),
        ("HIP-3 normal taker",   False, "para", False),
        ("growth mode maker",    True,  "xyz",  True),
        ("growth mode taker",    False, "xyz",  True),
    ]
    if args.growth_only:
        rows = [r for r in rows if r[3]]

    print(f"{'setup':22} {'net bps':>8} {'vol/$':>10} {'max volume':>14} {'cap for target':>15}")
    print("-" * 74)
    for label, mk, dex, gm in rows:
        n = F.net_bps(mk, dex, gm, tier=args.tier)
        vpd = F.volume_per_dollar(mk, dex, gm, tier=args.tier)
        maxvol = args.capital * vpd
        need = args.target * (n / 1e4) * args.buffer
        flag = "  OK" if maxvol >= args.target else "   --"
        print(f"{label:22} {n:8.3f} {vpd:10,.0f} ${maxvol:13,.0f} ${need:14,.0f}{flag}")

    print(f"\n(vol/$ = volume one dollar of capital can push before fees eat it)")
    print(f"(cap for target = fee spend x {args.buffer:.0f} buffer, keeping {100/args.buffer:.0f}% equity)")

    # what the answer actually is
    best = max(rows, key=lambda r: F.volume_per_dollar(r[1], r[2], r[3], tier=args.tier))
    n = F.net_bps(best[1], best[2], best[3], tier=args.tier)
    need = args.target * (n / 1e4) * args.buffer
    print(f"\n=== ANSWER ===")
    print(f"cheapest route to ${args.target:,.0f}: {best[0]}")
    print(f"  net fee        {n:.3f} bps")
    print(f"  capital needed ${need:,.0f}  (with {args.buffer:.0f}x buffer)")
    print(f"  your ${args.capital:,.0f} reaches ${args.capital * F.volume_per_dollar(best[1], best[2], best[3], tier=args.tier):,.0f} volume")

    if args.live_flow:
        print(f"\n=== growth-mode markets trading RIGHT NOW ===")
        gm = growth_markets()
        print(f"scanning {len(gm)} growth-mode markets for flow...")
        live = flow_scan(gm)
        if not live:
            print("  none have flow in the last 5 minutes")
        else:
            print(f"  {len(live)} have flow\n")
            print(f"  {'market':22} {'lev':>4} {'trades/5m':>10} {'vol/5m':>12}")
            for r in live[:15]:
                print(f"  {r['coin']:22} {r['lev']:>4} {r['trades']:>10} ${r['vol']:>11,.0f}")
    print()


if __name__ == "__main__":
    main()
