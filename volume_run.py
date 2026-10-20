"""
volume_run.py — generate trading volume with post-only maker orders.

    # see what it would do, no orders sent
    python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 --dry

    # live
    python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
        --live --i-used-referral --key-file wallet.key

WHAT THIS DOES AND DOES NOT DO
    It posts a bid at the best bid and an ask at the best ask, post-only, then reprices
    when the touch moves. Other people's orders fill yours. That is ordinary market
    making and it is legitimate.

    It does NOT match your own orders against each other. Doing that is wash trading,
    which entropy's docs say forfeits "all pending and future rewards" — you would pay
    the fee, generate the volume, and then lose the rebate you were counting on.

    Consequence: you cannot control fill rate. If nobody trades, you generate no volume.
    That is the honest limit of this approach, and no script can get around it.

WHAT IT COSTS
    Maker fee in growth mode is 0.30 bps gross (0.21 bps net at Tier 1, 0.12 at OG).
    Volume per dollar of capital = 1 / net_rate, so growth-mode maker at OG tier is
    ~$83,333 per $1. See VOLUME.md.

RISK
    Both-side quoting leaves you with inventory when one side fills and the other does
    not. `--max-inv` caps it, and the runner flattens on exit with IOC. Inventory that
    goes against you is a real loss, separate from fees.
"""
import argparse
import os
import signal
import sys
import time

import fees as F
from hl_client import HL

STOP = {"flag": False}


def _sig(*_):
    STOP["flag"] = True


# ------------------------------------------------------------------ market data
def resolve(hl, coin):
    dex = coin.split(":")[0] if ":" in coin else ""
    meta = hl.meta(dex)
    for i, a in enumerate(meta["universe"]):
        if a.get("name") == coin:
            aid = (200000 if dex else 0) + i
            return aid, a["szDecimals"], a["maxLeverage"], a
    raise SystemExit(f"{coin} not found")


def touch(hl, coin):
    b = hl.book(coin)
    bids, asks = b["levels"][0], b["levels"][1]
    if not bids or not asks:
        return None
    bb, ba = float(bids[0]["px"]), float(asks[0]["px"])
    return bb, ba, (bb + ba) / 2.0


def tick_of(px):
    """Hyperliquid perp tick: 5 significant figures, max 6 decimals."""
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


def round_px(px, szd, is_buy):
    t = tick_of(px)
    return round(px / t) * t


def round_sz(sz, szd):
    return round(sz, szd)


# ------------------------------------------------------------------ the runner
class Runner:
    def __init__(self, hl, coin, notional, lev, max_inv, tier, target, poll,
                 improve_ticks, builder_bps, live):
        self.hl = hl
        self.coin = coin
        self.notional = notional
        self.lev = lev
        self.max_inv = max_inv
        self.tier = tier
        self.target = target
        self.poll = poll
        self.improve = improve_ticks
        self.builder_bps = builder_bps
        self.live = live

        self.aid, self.szd, self.max_lev, self.asset = resolve(hl, coin)
        self.dex = coin.split(":")[0] if ":" in coin else ""
        self.gm = self.asset.get("growthMode") == "enabled"
        self.dfs = float(self.asset.get("deployerFeeScale", 1.0))
        self.lev = min(self.lev, self.max_lev)

        self.gross_bps = F.gross_bps(True, self.dex, self.gm, self.dfs)   # maker
        self.net_bps = F.net_bps(True, self.dex, self.gm, self.dfs, self.tier) + builder_bps

        self.volume = 0.0
        self.fills = 0
        self.fee_paid = 0.0
        self.inv = 0.0
        self.oids = {"buy": None, "sell": None}
        self.last_px = {"buy": None, "sell": None}
        self.started = time.time()
        self.err_streak = 0

    # ---- state
    def position(self):
        p = self.hl.position(self.coin, self.dex)
        return float(p["szi"]) if p else 0.0

    def equity(self):
        return self.hl.account_value(self.dex)

    def refresh_volume(self):
        """Sum fills since start — the only trustworthy volume source."""
        try:
            fl = self.hl.fills()
        except Exception:
            return
        t0 = self.started * 1000
        tot = 0.0
        fee = 0.0
        n = 0
        for f in fl:
            if f.get("coin") != self.coin:
                continue
            if float(f.get("time", 0)) < t0:
                continue
            tot += abs(float(f["px"]) * float(f["sz"]))
            fee += abs(float(f.get("fee", 0) or 0))
            n += 1
        self.volume, self.fee_paid, self.fills = tot, fee, n

    # ---- quoting
    def quote(self):
        t = touch(self.hl, self.coin)
        if not t:
            return
        bb, ba, mid = t
        tk = tick_of(mid)
        sz = round_sz(self.notional / mid, self.szd)
        if sz <= 0:
            print("  size rounds to zero — increase --notional")
            STOP["flag"] = True
            return

        # don't add to a position that is already at the cap
        if self.inv > 0:
            bid_px = None
        else:
            bid_px = round_px(bb, self.szd, True)
        if self.inv < 0:
            ask_px = None
        else:
            ask_px = round_px(ba, self.szd, False)

        # cancel stale quotes
        for side, px in (("buy", bid_px), ("sell", ask_px)):
            oid = self.oids[side]
            if oid and px is None:
                if self.live:
                    self.hl.cancel(self.aid, oid)
                self.oids[side] = None

        # place / reprice — only when the touch actually moved
        for side, px in (("buy", bid_px), ("sell", ask_px)):
            if px is None:
                continue
            if self.oids[side] and self.last_px[side] == px:
                continue          # already resting at this price; leave it
            if self.oids[side] and self.live:
                try:
                    self.hl.cancel(self.aid, self.oids[side])
                except Exception:
                    pass
                self.oids[side] = None
            if abs(self.inv) >= self.max_inv:
                continue
            if self.live:
                r = self.hl.order(self.aid, side == "buy", px, sz, tif="Alo")
                oid = None
                try:
                    st = r["response"]["data"]["statuses"][0]
                    if "resting" in st:
                        oid = st["resting"]["oid"]
                    elif "error" in st:
                        print(f"  {side} rejected: {st['error']}")
                except Exception:
                    print(f"  {side} response: {r}")
                if isinstance(r, dict) and r.get("status") == "err":
                    self.err_streak += 1
                    msg = str(r.get("response", ""))[:120]
                    print(f"  {side} REJECTED: {msg}")
                    if r.get("_diagnosis"):
                        print(f"  -> {r['_diagnosis']}")
                    if self.err_streak >= 3:
                        print("\n  3 rejections in a row — stopping so you can fix this.\n")
                        STOP["flag"] = True
                        return
                else:
                    self.err_streak = 0
                self.oids[side] = oid
            else:
                self.oids[side] = -1
            self.last_px[side] = px
            print(f"  quote {side:4} {sz:g} @ {px:g}")

    def cancel_all(self):
        self.last_px = {"buy": None, "sell": None}
        if not self.live:
            self.oids = {"buy": None, "sell": None}
            return
        for side in ("buy", "sell"):
            if self.oids[side] and self.oids[side] > 0:
                try:
                    self.hl.cancel(self.aid, self.oids[side])
                except Exception:
                    pass
        self.oids = {"buy": None, "sell": None}

    def flatten(self):
        self.cancel_all()
        if not self.live:
            return
        szi = self.position()
        if abs(szi) < 1e-9:
            return
        print(f"  flattening {szi:+.4f} with IOC")
        t = touch(self.hl, self.coin)
        if not t:
            return
        bb, ba, _ = t
        px = ba if szi > 0 else bb
        self.hl.order(self.aid, szi < 0, px, abs(szi), tif="Ioc", reduce_only=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="xyz:SP500", help="growth-mode market to quote")
    ap.add_argument("--notional", type=float, default=50, help="USD per side per quote")
    ap.add_argument("--lev", type=int, default=3)
    ap.add_argument("--target", type=float, default=100_000, help="stop at this volume")
    ap.add_argument("--max-inv", type=float, default=None,
                    help="max inventory in units (default: 2x one quote)")
    ap.add_argument("--tier", default="tier1", choices=list(F.TIERS))
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between requotes")
    ap.add_argument("--improve-ticks", type=int, default=0,
                    help="0 = join the touch, 1 = improve by one tick (may not fill)")
    ap.add_argument("--builder", default=None)
    ap.add_argument("--builder-fee", type=int, default=None)
    ap.add_argument("--max-minutes", type=float, default=240)
    ap.add_argument("--dry", action="store_true", help="explicit dry run (default anyway)")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--i-used-referral", action="store_true")
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if args.live and not key:
        raise SystemExit("live needs HL_PRIVATE_KEY or --key-file")

    import config
    if args.live:
        args._key = key
        if not config.require_ack(args):
            return 1
    else:
        config.print_banner()

    builder_bps = (args.builder_fee / 10.0) if args.builder_fee else 0.0
    hl = HL(private_key=key if args.live else None,
            builder=args.builder, builder_fee=args.builder_fee)

    if args.max_inv is None:
        _t = touch(hl, args.coin)
        _mid = _t[2] if _t else 1.0
        args.max_inv = round_sz(2 * args.notional / _mid, 4) or 0.001

    r = Runner(hl, args.coin, args.notional, args.lev, args.max_inv, args.tier,
               args.target, args.poll, args.improve_ticks, builder_bps, args.live)

    mode = "LIVE" if args.live else "DRY"
    print(f"\n[{mode}] {args.coin}")
    print(f"  growth mode      {'yes' if r.gm else 'NO — fees are 10x higher'}")
    print(f"  maker fee        {r.gross_bps:.2f} bps gross  ->  {r.net_bps:.2f} bps net "
          f"(tier {args.tier})")
    print(f"  volume per $1    {1/(r.net_bps/1e4):,.0f}")
    print(f"  target           ${args.target:,.0f}")
    print(f"  capital to spend ${args.target * r.net_bps/1e4:,.2f} on fees")
    print(f"  quote size       {round_sz(args.notional/1, r.szd)} max units/side, "
          f"${args.notional:,.0f} notional")
    print(f"  leverage         {r.lev}x isolated")
    print(f"  max inventory    {args.max_inv:g} units")

    if args.live:
        mode = hl.abstraction()
        unified = mode in ("unifiedAccount", "portfolioMargin")
        tot_spot, hold, avail_spot = hl.spot_usdc()
        eq = hl.spendable(r.dex)
        bal = hl.balances()
        elsewhere = sum(v for k, v in bal.items() if k not in (r.dex or "main",))
        print(f"  abstraction      {mode}")
        print(f"  spendable        ${eq:,.2f}  (for '{r.dex or 'main'}')")
        if unified:
            print(f"  spot USDC        ${tot_spot:,.2f} total, ${avail_spot:,.2f} free")
        need = args.notional / r.lev * 1.5
        if eq < need:
            print(f"\n  NOT ENOUGH MARGIN: need ~${need:,.2f}, spendable ${eq:,.2f}")
            if unified:
                print(f"\n  You are on '{mode}' — the spot balance backs every dex, so this")
                print(f"  is a capital problem, not a transfer problem. Deposit")
                print(f"  ${max(0, need - eq):,.2f} more, or lower --notional.")
            elif elsewhere > 0.5:
                print(f"\n  You have ${elsewhere:,.2f} on OTHER accounts. Collateral is held")
                print(f"  per dex, so a balance on main does not give you margin on {r.dex or 'main'}.")
                print(f"  Move it:\n")
                print(f"    python3 fund_dex.py --to {r.dex or 'main'} --amount {need:.2f} --key-file wallet.key\n")
            else:
                print(f"  Deposit at least ${need - eq:,.2f} more USDC.\n")
            print(f"  Full check:  python3 doctor.py --key-file wallet.key "
                  f"--coin {args.coin} --notional {args.notional}\n")
            return 1
        if not args.yes:
            print(f"\n  This will place real post-only orders on {args.coin}.")
            print(f"  Type YES to continue: ", end="", flush=True)
            if input().strip() != "YES":
                print("  aborted")
                return 1
        hl.set_leverage(r.aid, r.lev, is_cross=False)
        print("  leverage set")

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    last_print = 0
    try:
        while not STOP["flag"]:
            r.refresh_volume()
            r.inv = r.position() if args.live else r.inv

            if r.volume >= args.target:
                print(f"\n  TARGET REACHED ${r.volume:,.0f}")
                break
            if (time.time() - r.started) / 60 > args.max_minutes:
                print(f"\n  time limit reached")
                break

            r.quote()

            now = time.time()
            if now - last_print > 15:
                el = (now - r.started) / 60
                rate = r.volume / el if el > 0 else 0
                pct = 100 * r.volume / args.target if args.target else 0
                print(f"  vol ${r.volume:>12,.0f} / ${args.target:,.0f} "
                      f"({pct:5.1f}%)  fills {r.fills:>4}  inv {r.inv:+.3f}  "
                      f"fee ${r.fee_paid:.2f}  ${rate:,.0f}/min")
                last_print = now

            time.sleep(args.poll)
    finally:
        print("\n  shutting down")
        r.cancel_all()
        r.flatten()
        r.refresh_volume()
        print(f"\n  === RESULT ===")
        print(f"  volume       ${r.volume:,.0f}")
        print(f"  fills        {r.fills}")
        print(f"  fees paid    ${r.fee_paid:.2f}  (gross; rebate is bi-weekly)")
        print(f"  net cost     ${r.volume * r.net_bps/1e4:.2f} at tier {args.tier}")
        print(f"  runtime      {(time.time()-r.started)/60:.1f} min")
        if args.live:
            print(f"  equity now   ${r.equity():,.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
