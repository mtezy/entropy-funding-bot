"""
tier_check.py — read your Entropy tier, rebate, and volume progress. Read-only.

    python3 tier_check.py --key-file wallet.key

Answers three questions the bot needs before you size anything:
  1. what self-rebate tier am I on (60% / 120% / 160% / 200%)
  2. how much volume have I done toward the next tier
  3. what is my effective fee rate right now, per market type
"""
import argparse
import os

import fees as F
from hl_client import HL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if not key:
        raise SystemExit("need --key-file or HL_PRIVATE_KEY")

    hl = HL(private_key=key)
    print(f"\nwallet {hl.address}\n")

    # ---- fees / volume tier ----
    try:
        f = hl.fees()
    except Exception as e:
        print("fees error:", e)
        f = {}

    sched = f.get("feeSchedule", {})
    daily = f.get("dailyUserVlm") or []
    print("=== HYPERLIQUID FEE TIER ===")
    print(f"  maker  {float(sched.get('add', 0)) * 1e4:.2f} bps")
    print(f"  taker  {float(sched.get('cross', 0)) * 1e4:.2f} bps")

    # 14d weighted volume from the daily series
    vlm14 = 0.0
    for row in daily[:14]:
        try:
            vlm14 += float(row.get("userCross", 0)) + float(row.get("userAdd", 0))
        except Exception:
            pass
    print(f"  14d volume  ${vlm14:,.0f}")
    for thresh, label in ((500_000_000, "0.00 maker"), (100_000_000, "0.40 maker"),
                          (25_000_000, "0.80 maker"), (5_000_000, "1.20 maker")):
        if vlm14 < thresh:
            print(f"  next tier at ${thresh:,.0f} ({label}) — "
                  f"${thresh - vlm14:,.0f} to go")

    # ---- referral state ----
    print("\n=== HYPERLIQUID REFERRAL ===")
    try:
        ref = hl.referral()
        print(f"  referredBy   {ref.get('referredBy') or '-'}")
        print(f"  cumVlm       ${float(ref.get('cumVlm') or 0):,.0f}")
        st = ref.get("referrerState", {})
        stage = st.get("stage", "?")
        print(f"  referrer     {stage}")
        if stage == "needToTrade":
            req = float(st.get("data", {}).get("required", 10000))
            have = float(ref.get("cumVlm") or 0)
            print(f"  -> need ${req:,.0f} volume, have ${have:,.0f} "
                  f"(${max(0, req - have):,.0f} to go)")
        elif stage == "ready":
            print("  -> eligible to register a referral code")
    except Exception as e:
        print("  error:", e)

    # ---- builder fees you have approved ----
    print("\n=== BUILDER APPROVALS ===")
    try:
        ab = hl.approved_builders()
        if not ab:
            print("  none approved")
        for b in ab:
            print(f"  {b.get('builder')}  max {b.get('maxFeeRate')}")
    except Exception as e:
        print("  error:", e)

    # ---- effective rates ----
    print("\n=== YOUR EFFECTIVE RATE, PER MARKET TYPE (maker) ===")
    print(f"  {'setup':24} {'gross':>7} {'tier1':>7} {'OG':>7} {'pref':>7}")
    for label, dex, gm in (("main dex", "", False),
                           ("HIP-3 normal", "para", False),
                           ("growth mode", "xyz", True)):
        g = F.gross_bps(True, dex, gm)
        row = " ".join(f"{F.net_bps(True, dex, gm, tier=t):7.3f}"
                       for t in ("tier1", "og", "pref"))
        print(f"  {label:24} {g:7.2f} {row}")

    print("\n  volume per $1 of capital (growth mode maker):")
    for t in ("tier1", "og", "pref", "part"):
        v = F.volume_per_dollar(True, "xyz", True, tier=t)
        print(f"    {t:6} " + ("unlimited (net fee 0)" if v == float("inf") else f"${v:>10,.0f}"))

    print("\n  NOTE: entropy's self-rebate tier is shown on your entropy.io profile,")
    print("  not through the Hyperliquid API. Confirm it there before sizing.")
    print()


if __name__ == "__main__":
    main()
