"""
farm.py — what is actually farmable on entropy, ranked, with the real numbers.

    python3 farm.py --key-file wallet.key
    python3 farm.py --address 0x... --capital 18
    python3 farm.py --capital 18 --markets-only

Read-only. Sends nothing.

WHAT "FARMING" MEANS HERE (three different things, very different risk)

  1. FEE REBATE — real, live, paying now.
     Self Rebate 60% / 120% / 160% / 200% of Entropy's half of the fee,
     paid in HYPE bi-weekly, $5 minimum. This is the only guaranteed return.

  2. REFERRAL REWARD — real, live.
     25% / 50% / 75% / 100% of Entropy's half of fees paid by users who
     joined through your link. Requires other people, not more volume from you.

  3. POINTS / AIRDROP — NOT ANNOUNCED.
     No token, no points program, no season, no TGE as of 2026-10-20.
     The leaderboard ranks by PNL, not volume. Community expects a retroactive
     "Season 0" around a speculated Nov-Dec TGE. That is a bet, not a farm.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import fees as F

INFO = "https://api.hyperliquid.xyz/info"


def post(payload):
    req = urllib.request.Request(INFO, data=json.dumps(payload).encode(),
                                headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def tick_of(px):
    if px >= 10000:
        return 1.0
    if px >= 1000:
        return 0.1
    if px >= 100:
        return 0.01
    if px >= 10:
        return 0.001
    if px >= 1:
        return 0.0001
    return 0.00001


# ---------------------------------------------------------------- market scan
def scan_native():
    """Entropy's own markets (io dex) — the ones a retroactive season would count."""
    meta = post({"type": "meta", "dex": "io"})
    ctx = post({"type": "metaAndAssetCtxs", "dex": "io"})
    rows = []
    for i, a in enumerate(meta["universe"]):
        if a.get("isDelisted"):
            continue
        c = ctx[1][i]
        coin = a["name"]
        try:
            b = post({"type": "l2Book", "coin": coin})
            bb = float(b["levels"][0][0]["px"])
            ba = float(b["levels"][1][0]["px"])
        except Exception:
            continue
        mid = (bb + ba) / 2
        spr_bps = (ba - bb) / mid * 1e4
        ticks = max(1, round((ba - bb) / tick_of(mid)))

        try:
            tr = post({"type": "recentTrades", "coin": coin}) or []
            now = time.time() * 1000
            t5 = [x for x in tr if now - float(x["time"]) < 300000]
        except Exception:
            t5 = []
        vol5 = sum(float(x["px"]) * float(x["sz"]) for x in t5)

        oi_usd = float(c.get("openInterest", 0)) * float(c.get("oraclePx", 0))
        gm = a.get("growthMode") == "enabled"
        dfs = float(a.get("deployerFeeScale", 1.0))
        rows.append({
            "coin": coin, "mid": mid, "spread_bps": spr_bps, "ticks": ticks,
            "trades5": len(t5), "vol5": vol5, "oi": oi_usd,
            "lev": a["maxLeverage"], "szd": a["szDecimals"],
            "gm": gm, "dfs": dfs,
        })
    return rows


def rank_for_farming(rows, tier):
    """
    Score = how likely a maker order gets filled AND how much room it has.

    Ticks in spread matters: with 1 tick you must join the touch (no priority edge).
    With many ticks you can improve and be top of book.
    Flow matters more: a 9-tick spread on a dead book never fills.
    """
    for r in rows:
        net = F.net_bps(True, "io", r["gm"], r["dfs"], tier)
        r["net_bps"] = net
        flow = min(r["vol5"] / 1000.0, 20.0)          # cap the flow term
        room = min(r["ticks"], 5) / 5.0                # 1 tick = 0.2, 5+ = 1.0
        r["score"] = flow * (0.4 + 0.6 * room) * (1.0 if r["gm"] else 0.1)
    return sorted(rows, key=lambda r: -r["score"])


# ---------------------------------------------------------------- economics
def economics(capital, tier, net_bps, rebate_pct=None):
    """
    Volume capacity and net cost. volume = capital / net_rate.
    Also shows the gross drain, which is what your margin actually pays first.
    """
    factor = 1 - 0.5 * F.TIERS.get(tier, 0.60)
    gross = (net_bps / factor) if factor > 1e-9 else net_bps
    vol = capital / (net_bps / 1e4) if net_bps > 0 else float("inf")
    return {
        "volume": vol,
        "net_cost": vol * net_bps / 1e4,
        "gross_drain": vol * gross / 1e4,
        "rebate_back": vol * gross / 1e4 - vol * net_bps / 1e4,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--address", default=None)
    ap.add_argument("--capital", type=float, default=18)
    ap.add_argument("--tier", default="tier1", choices=list(F.TIERS))
    ap.add_argument("--markets-only", action="store_true")
    args = ap.parse_args()

    print("\n" + "=" * 74)
    print("  ENTROPY FARM AUDIT")
    print("=" * 74)

    # ---- 1. what is farmable ----
    print("""
  1. FEE REBATE          live, paying in HYPE bi-weekly      <- guaranteed
  2. REFERRAL REWARD     live, % of other users' fees        <- needs users
  3. POINTS / AIRDROP    NOT ANNOUNCED                       <- a bet, not a farm

  No token, no points program, no season as of 2026-10-20. The leaderboard
  ranks by PnL, not volume. A retroactive season is community expectation
  around a speculated Nov-Dec TGE — nothing official.
""")

    # ---- 2. account state ----
    hl = None
    if args.address or args.key_file or os.environ.get("HL_PRIVATE_KEY"):
        from hl_client import HL
        key = os.environ.get("HL_PRIVATE_KEY")
        if not key and args.key_file:
            key = open(args.key_file).read().strip()
        hl = HL(private_key=key) if key else HL()
        if args.address:
            hl.address = args.address

        print("  --- your account ---")
        try:
            bal = hl.balances()
            tot = sum(bal.values())
            print(f"  total USDC          ${tot:,.2f}")
            for k in ("spot", "main", "io", "xyz", "para", "mkts"):
                if bal.get(k, 0) > 0:
                    print(f"    {k:<6}            ${bal[k]:,.2f}")
            print(f"  on io dex (native)  ${bal.get('io', 0):,.2f}"
                  f"{'   <- needed to farm native markets' if bal.get('io', 0) == 0 else ''}")
        except Exception as e:
            print(f"  balances error: {e}")

        try:
            ref = hl.referral()
            rb = ref.get("referredBy")
            print(f"  referredBy          {rb or 'none'}")
            if rb:
                print(f"    -> referred-user benefit applies (up to 100% of Entropy's half)")
            cv = float(ref.get("cumVlm") or 0)
            print(f"  cumVlm              ${cv:,.0f}")
        except Exception as e:
            print(f"  referral error: {e}")

        try:
            f = hl.fees()
            sch = f.get("feeSchedule", {})
            print(f"  HL maker/taker      {float(sch.get('add',0))*1e4:.2f} / "
                  f"{float(sch.get('cross',0))*1e4:.2f} bps")
        except Exception:
            pass
        print()

    if args.markets_only:
        pass
    else:
        # ---- 3. tier economics ----
        print("  --- what your tier is worth (growth-mode maker, 0.30 bps gross) ---")
        print(f"  {'tier':8} {'self rebate':>12} {'net bps':>9} {'vol per $1':>12} {'$18 buys':>12}")
        for t, pct in F.TIERS.items():
            net = F.net_bps(True, "io", True, 1.0, t)
            vpd = F.volume_per_dollar(True, "io", True, tier=t)
            e = economics(args.capital, t, net)
            v = "unlimited" if e["volume"] == float("inf") else f"${e['volume']:,.0f}"
            print(f"  {t:8} {pct*100:>11.0f}% {net:>9.3f} {vpd:>12,.0f} {v:>12}")

        print("""
  Read that column: at Tier 1 (60%) $18 pushes ~$714k of volume before the fees
  eat the balance. At Tier 4 (200%) the net fee is zero and volume is unlimited
  by fee — limited only by margin and by whether anyone fills your orders.

  Tier 2 (OG) has NO volume threshold in the docs. It is grantable via the
  Partners program (manual review). That is the single highest-leverage action
  available: 60% -> 120% cuts your net fee by 43%.
""")

    # ---- 4. native markets ----
    print("  --- entropy native markets (io dex), ranked for maker farming ---")
    rows = rank_for_farming(scan_native(), args.tier)
    print(f"  {'market':12} {'mid':>10} {'spr bps':>8} {'ticks':>6} {'tr/5m':>6} "
          f"{'vol/5m':>10} {'OI$':>12} {'score':>6}")
    for r in rows:
        print(f"  {r['coin']:12} {r['mid']:>10.4f} {r['spread_bps']:>8.2f} "
              f"{r['ticks']:>6} {r['trades5']:>6} {r['vol5']:>10,.0f} "
              f"{r['oi']:>12,.0f} {r['score']:>6.2f}")

    best = [r for r in rows if r["trades5"] >= 5]
    print()
    if best:
        b = best[0]
        print(f"  best fill odds: {b['coin']}  ({b['ticks']} ticks, "
              f"{b['trades5']} trades/5m, ${b['vol5']:,.0f})")
        wide = [r for r in rows if r["ticks"] >= 4 and r["trades5"] >= 5]
        if wide:
            w = wide[0]
            print(f"  best room to improve: {w['coin']}  ({w['ticks']} ticks — "
                  f"quote inside the spread and be top of book)")
    else:
        print("  no native market has meaningful flow right now")

    print("""
  Ticks in spread is the thing to look at. A 1-tick spread means you must join
  the touch and compete on speed. 4+ ticks means you can improve and get queue
  priority — that is where a small maker actually earns.

  Native markets matter if you believe in a retroactive season. If you only
  care about the rebate, any growth-mode market works and xyz has far more flow.
""")

    # ---- 5. action plan ----
    print("  --- action plan, in order ---")
    print("""
  1. CONFIRM YOUR TIER on your entropy.io profile page. The API does not expose
     it. Everything below depends on which tier you are actually on.

  2. IF YOU ARE TIER 1: apply to the Partners program for a tier grant.
     Manual review, needs content or an audience. Going 60% -> 120% is worth
     more than any amount of volume you can push with $18.

  3. MOVE USDC TO THE IO DEX if you want to farm native markets:
        python3 fund_dex.py --to io --amount 15 --key-file wallet.key

  4. QUOTE, DO NOT SWEEP. Post-only maker orders only. Taker fees are 3x maker
     and no tier makes that back at 60%.

  5. TRACK THE REBATE. It pays bi-weekly with a $5 minimum. Do not expect
     anything before two weeks of real volume.

  6. DO NOT SELF-MATCH. Entropy's docs: "Wash trading, pre-arranged trading,
     self-referral, and order forms of inauthentic behavior will lead to loss
     of all pending and future rewards." Both-sides-of-your-own-trade is exactly
     that. You would pay the fee, generate the volume, and forfeit the rebate.
""")

    print("  run the volume generator:")
    if best:
        print(f"    python3 volume_run.py --coin {best[0]['coin']} --notional 15 --target 50000 \\")
        print(f"        --live --i-used-referral --key-file wallet.key")
    else:
        print(f"    python3 volume_run.py --coin io:DRAM --notional 15 --target 50000 \\")
        print(f"        --live --i-used-referral --key-file wallet.key")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
