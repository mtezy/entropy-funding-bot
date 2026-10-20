"""
status.py — current positions and farming results. Read-only, never sends an order.

    python3 status.py --key-file wallet.key
    python3 status.py --key-file wallet.key --long para:UNITREE --short xyz:UNITREE
    python3 status.py --key-file wallet.key --all      # every open position
"""
import argparse
import os
import statistics
import time

from hl_client import HL

HOURS_YEAR = 24 * 365


def funding_now(hl, coin):
    dex = coin.split(":")[0] if ":" in coin else ""
    for name, c in hl.ctxs(dex).items():
        if name == coin:
            return float(c["funding"])
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--long", dest="long_coin", default="para:UNITREE")
    ap.add_argument("--short", dest="short_coin", default="xyz:UNITREE")
    ap.add_argument("--all", action="store_true", help="list every open position")
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if not key:
        raise SystemExit("need --key-file or HL_PRIVATE_KEY")

    hl = HL(private_key=key)
    print(f"\nwallet {hl.address}\n")

    import config
    print(f"referral required: {config.ENTROPY_REFERRAL_URL}\n")

    eq = hl.account_value()
    wd = hl.withdrawable()
    print(f"account value   ${eq:,.2f}")
    print(f"withdrawable    ${wd:,.2f}\n")

    if args.all:
        pos = hl.all_positions()
        if not pos:
            print("no open positions")
        for p in pos:
            szi = float(p["szi"])
            entry = float(p.get("entryPx") or 0)
            upnl = float(p.get("unrealizedPnl") or 0)
            lev = p.get("leverage", {})
            print(f"{p['coin']:<16} [{p['_dex']:<4}] size {szi:+.4f}  entry {entry:.4f}  "
                  f"uPnL ${upnl:+,.2f}  lev {lev.get('value','?')}x {lev.get('type','')}")
        print()
        return 0

    total_unreal = 0.0
    for tag, coin in (("LONG ", args.long_coin), ("SHORT", args.short_coin)):
        p = hl.position(coin)
        if not p:
            print(f"{tag} {coin:<16} - no position")
            continue
        szi = float(p["szi"])
        entry = float(p.get("entryPx") or 0)
        upnl = float(p.get("unrealizedPnl") or 0)
        total_unreal += upnl
        lev = p.get("leverage", {})
        print(f"{tag} {coin:<16} size {szi:+.4f}  entry {entry:.4f}  "
              f"uPnL ${upnl:+,.2f}  lev {lev.get('value','?')}x {lev.get('type','')}")

    # ---- carry now vs 168h mean ----
    print()
    try:
        fl = funding_now(hl, args.long_coin)
        fs = funding_now(hl, args.short_coin)
        bps = (-fl + fs) * 1e4
        apr = bps * HOURS_YEAR / 100
        print(f"carry now       {bps:+.3f} bps/hr  =  {apr:+.0f}% APR")

        hl_hist = hl.funding_history(args.long_coin, 168) or []
        hs_hist = hl.funding_history(args.short_coin, 168) or []
        rl = [float(x["fundingRate"]) for x in hl_hist]
        rs = [float(x["fundingRate"]) for x in hs_hist]
        n = min(len(rl), len(rs))
        if n >= 12:
            diff = [-rl[i] + rs[i] for i in range(n)]
            mean = statistics.mean(diff) * 1e4
            persist = sum(1 for d in diff if d > 0) / n
            print(f"carry 168h mean {mean:+.3f} bps/hr  =  "
                  f"{mean*HOURS_YEAR/100:+.0f}% APR   persist {persist*100:.0f}%")
    except Exception as e:
        print("carry error:", e)

    print(f"\ntotal uPnL      ${total_unreal:+,.2f}")
    if abs(total_unreal) > 0:
        print("  (the two legs should offset - that is the hedge working)")
    print()


if __name__ == "__main__":
    main()
