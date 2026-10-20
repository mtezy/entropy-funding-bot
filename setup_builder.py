"""
setup_builder.py — one-time setup for monetizing the bot.

Two independent revenue rails on Hyperliquid. You can use both.

  RAIL 1 — BUILDER FEE (recommended for a bot)
    Every order carries builder={"b": your_address, "f": fee_in_tenths_of_bps}.
    The fee is charged on fills, on top of protocol fees. Cap: 10 bps perps.
    Requires each user to approve you once via approveBuilderFee.

    Fee math at f=10 (1 bp):
      $1,000 notional per leg, 2 legs, open + close = $4,000 of flow
      -> 4,000 * 0.0001 = $0.40 per full round trip

    At f=25 (2.5 bps) it is $1.00 per round trip. Higher = more revenue but
    the arb's net carry shrinks, so the bot prices it in (--builder-fee) and
    refuses to enter when the carry no longer covers entry + builder cost.

  RAIL 2 — REFERRAL CODE
    Requires $10,000 cumulative volume before a code can be created.
    Referred users get a 4% fee discount; you earn 10% of their fees.

Usage:
    # see your status
    python3 setup_builder.py --key-file wallet.key --status

    # approve a builder fee for yourself (test / single wallet)
    python3 setup_builder.py --key-file wallet.key --approve --fee 10

    # create your referral code (needs $10k volume first)
    python3 setup_builder.py --key-file wallet.key --register-code YOURCODE

    # attach someone's referral code to this wallet
    python3 setup_builder.py --key-file wallet.key --set-referrer CODE

    # generate the approval blob users must sign to pay you builder fees
    python3 setup_builder.py --builder 0xYOU --fee 10 --print-approval
"""
import argparse
import json
import os
import sys

from hl_client import HL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--builder", default=None, help="builder address (yours)")
    ap.add_argument("--fee", type=int, default=10, help="tenths of a bp (10 = 1bp, max 100)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--register-code", default=None, metavar="CODE")
    ap.add_argument("--set-referrer", default=None, metavar="CODE")
    ap.add_argument("--print-approval", action="store_true")
    ap.add_argument("--live", action="store_true", help="required for write actions")
    args = ap.parse_args()

    if args.print_approval:
        b = args.builder or "<YOUR_BUILDER_ADDRESS>"
        print(json.dumps({
            "type": "approveBuilderFee",
            "maxFeeRate": f"{args.fee/10000:.4%}",
            "builder": b,
        }, indent=1))
        print(f"\nusers approve {args.fee/10:.1f} bps max. you charge "
              f"{args.fee/10:.1f} bps on each fill.")
        return 0

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if not key:
        raise SystemExit("need --key-file or HL_PRIVATE_KEY")

    hl = HL(private_key=key)
    print(f"\nwallet {hl.address}\n")

    if args.status or not (args.approve or args.register_code or args.set_referrer):
        # fee tier
        try:
            f = hl.fees()
            fs = f.get("feeSchedule", {})
            print(f"maker {float(fs.get('add',0))*1e4:.2f} bps   "
                  f"taker {float(fs.get('cross',0))*1e4:.2f} bps")
            print(f"14d volume: ", end="")
            dv = f.get("dailyUserVlm") or []
            tot = sum(float(x.get("userCross", 0)) + float(x.get("userAdd", 0))
                      for x in dv[-14:])
            print(f"${tot:,.0f}")
        except Exception as e:
            print("fees:", e)

        # referral state
        try:
            ref = hl.referral()
            print(f"\nreferredBy      {ref.get('referredBy') or '-'}")
            print(f"cumulative vol  ${float(ref.get('cumVlm', 0)):,.0f}")
            print(f"unclaimed       ${float(ref.get('unclaimedRewards', 0)):,.2f}")
            print(f"builder rewards ${float(ref.get('builderRewards', 0)):,.2f}")
            st = ref.get("referrerState") or {}
            print(f"referrer stage  {st.get('stage')}")
            if st.get("stage") == "needToTrade":
                need = float((st.get("data") or {}).get("required", 0))
                have = float(ref.get("cumVlm", 0))
                print(f"  -> need ${need:,.0f} volume, have ${have:,.0f} "
                      f"(${max(0, need-have):,.0f} to go)")
            elif st.get("stage") == "ready":
                print(f"  -> code available: {st.get('data')}")
        except Exception as e:
            print("referral:", e)

        # builder approvals
        if args.builder:
            try:
                mf = hl.max_builder_fee(args.builder)
                print(f"\nbuilder {args.builder}")
                print(f"  max approved  {int(mf or 0)} tenths-bps ({int(mf or 0)/10:.1f} bps)")
                if int(mf or 0) == 0:
                    print("  -> not approved yet")
            except Exception as e:
                print("builder:", e)
        try:
            ab = hl.approved_builders()
            print(f"approved builders: {len(ab)}")
            for x in ab[:5]:
                print("  ", json.dumps(x)[:120])
        except Exception:
            pass
        return 0

    if not args.live:
        print("write action needs --live")

    if args.approve:
        if not args.builder:
            raise SystemExit("--approve needs --builder 0xYOURADDRESS")
        r = hl.approve_builder_fee(args.builder, args.fee, dry=not args.live)
        print(f"approveBuilderFee -> {json.dumps(r)[:200]}")

    if args.register_code:
        r = hl.post_action({"type": "registerReferrer", "code": args.register_code},
                           dry=not args.live)
        print(f"registerReferrer -> {json.dumps(r)[:200]}")

    if args.set_referrer:
        r = hl.set_referrer(args.set_referrer, dry=not args.live)
        print(f"setReferrer -> {json.dumps(r)[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
