"""
Market maker for entropy.io builder-deployed perp dex (Hyperliquid "io" dex).

Strategy: post-only (Alo) two-sided quoting around microprice with:
  - volatility-scaled half-spread  (widens when the book moves)
  - inventory skew                 (shifts quotes to unload position)
  - size taper near position limit
  - hard risk stops (max position, max notional, drawdown kill switch)

Run dry (default):
    python3 mm_anth.py --coin io:ANTH --size 0.5 --half-spread-bps 6 --once

Live (needs HL_PRIVATE_KEY in env, or --key-file):
    HL_PRIVATE_KEY=0x... python3 mm_anth.py --coin io:ANTH --size 0.5 --live
"""
import argparse
import json
import math
import os
import signal
import sys
import time

from hl_client import HL

DEX = "io"

# ---------------------------------------------------------------- market data
class Market:
    def __init__(self, hl, coin):
        self.hl = hl
        self.coin = coin
        self.asset_id = None
        self.sz_decimals = 3
        self.max_leverage = 6
        self._px_hist = []

    def resolve(self):
        """asset id + szDecimals from the exchange meta, not hardcoded."""
        meta = self.hl.meta(DEX)
        for i, a in enumerate(meta["universe"]):
            if a["name"] == self.coin:
                self.asset_id = self.hl.asset_id("io", i)
                self.sz_decimals = a["szDecimals"]
                self.max_leverage = a["maxLeverage"]
                return self
        raise SystemExit(f"{self.coin} not found in dex '{DEX}'")

    def snapshot(self):
        b = self.hl.book(self.coin)
        bids, asks = b["levels"][0], b["levels"][1]
        if not bids or not asks:
            return None
        bb, ba = float(bids[0]["px"]), float(asks[0]["px"])
        bsz, asz = float(bids[0]["sz"]), float(asks[0]["sz"])
        # microprice: size-weighted mid, leans toward the heavier side
        micro = (bb * asz + ba * bsz) / (bsz + asz)
        self._px_hist.append(micro)
        self._px_hist = self._px_hist[-120:]
        return {
            "bid": bb, "ask": ba, "bid_sz": bsz, "ask_sz": asz,
            "micro": micro, "spread_bps": (ba - bb) / micro * 1e4,
            "vol_bps": self._vol_bps(),
            "bids": bids, "asks": asks,
        }

    def _vol_bps(self):
        """Realized vol per tick in bps, from the last ~120 mid samples."""
        h = self._px_hist
        if len(h) < 10:
            return 0.0
        rets = [abs(h[i] / h[i - 1] - 1) * 1e4 for i in range(1, len(h)) if h[i - 1]]
        return sum(rets) / len(rets)


# ---------------------------------------------------------------- risk
class Risk:
    def __init__(self, max_pos, max_notional, max_dd_usd):
        self.max_pos = max_pos
        self.max_notional = max_notional
        self.max_dd = max_dd_usd
        self.peak_equity = None
        self.halted = False

    def check(self, pos, notional, equity):
        if self.peak_equity is None:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity - equity > self.max_dd:
            self.halted = True
            return f"KILL: drawdown ${self.peak_equity - equity:.2f} > ${self.max_dd}"
        if abs(pos) > self.max_pos:
            self.halted = True
            return f"KILL: position {pos} > max {self.max_pos}"
        if notional > self.max_notional:
            return f"throttle: notional ${notional:.0f} > ${self.max_notional}"
        return None


# ---------------------------------------------------------------- quoting
def build_quotes(snap, pos, args, tick):
    """Return (bid_px, ask_px, bid_sz, ask_sz) or None."""
    micro = snap["micro"]
    half_bps = max(args.half_spread_bps, snap["vol_bps"] * args.vol_mult)
    half = micro * half_bps / 1e4

    # inventory skew: long -> push quotes down to sell, short -> up to buy
    inv_frac = max(-1.0, min(1.0, pos / args.max_pos)) if args.max_pos else 0.0
    skew = inv_frac * half * args.skew_mult

    bid_px = micro - half - skew
    ask_px = micro + half - skew

    # size taper: shrink the side that would grow the position
    base = args.size
    bid_sz = base * (1.0 - max(0.0, inv_frac)) ** args.taper
    ask_sz = base * (1.0 - max(0.0, -inv_frac)) ** args.taper

    # respect exchange price rules: max 5 significant figures, tick from szDecimals
    bid_px = round_to_tick(bid_px, tick)
    ask_px = round_to_tick(ask_px, tick)
    bid_sz = round_step(bid_sz, args.sz_step)
    ask_sz = round_step(ask_sz, args.sz_step)

    if bid_px >= ask_px:
        return None
    if bid_sz <= 0 or ask_sz <= 0:
        return None
    return bid_px, ask_px, bid_sz, ask_sz


def round_to_tick(px, tick):
    """HL perp px: <=5 significant figures and <= MAX_DECIMALS-tick decimals."""
    p = round(px / tick) * tick
    # 5 sig figs
    if p != 0:
        digits = -int(math.floor(math.log10(abs(p)))) + 4
        p = round(p, max(0, digits))
    return float(f"{p:.10f}")


def round_step(x, step):
    return round(round(x / step) * step, 8)


# ---------------------------------------------------------------- main loop
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="io:ANTH")
    ap.add_argument("--size", type=float, default=0.5, help="base size per side")
    ap.add_argument("--sz-step", type=float, default=0.001)
    ap.add_argument("--half-spread-bps", type=float, default=6.0)
    ap.add_argument("--vol-mult", type=float, default=3.0, help="half-spread floor = vol*mult")
    ap.add_argument("--skew-mult", type=float, default=1.0)
    ap.add_argument("--taper", type=float, default=1.5)
    ap.add_argument("--max-pos", type=float, default=5.0)
    ap.add_argument("--max-notional", type=float, default=20000.0)
    ap.add_argument("--max-dd-usd", type=float, default=200.0)
    ap.add_argument("--refresh", type=float, default=3.0, help="seconds between requotes")
    ap.add_argument("--tick", type=float, default=0.1, help="price tick (io:ANTH = 0.1)")
    ap.add_argument("--once", action="store_true", help="one quote cycle then exit")
    ap.add_argument("--live", action="store_true", help="actually sign and send")
    ap.add_argument("--key-file", default=None)
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if args.live and not key:
        raise SystemExit("live mode needs HL_PRIVATE_KEY or --key-file")

    hl = HL(private_key=key if args.live else None)
    mkt = Market(hl, args.coin).resolve()
    risk = Risk(args.max_pos, args.max_notional, args.max_dd_usd)

    mode = "LIVE" if args.live else "DRY"
    print(f"[{mode}] {args.coin}  assetId={mkt.asset_id}  szDec={mkt.sz_decimals} "
          f"lev={mkt.max_leverage}x  addr={hl.address}")

    if args.live:
        st = hl.user_state(DEX)
        print("[acct]", json.dumps(st.get("marginSummary", {}))[:200])

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))

    while not stop["flag"]:
        snap = mkt.snapshot()
        if not snap:
            print("no book"); time.sleep(args.refresh); continue

        pos = 0.0
        equity = 0.0
        if args.live:
            st = hl.user_state(DEX)
            for p in st.get("assetPositions", []):
                if p["position"]["coin"] == args.coin:
                    pos = float(p["position"]["szi"])
            equity = float(st.get("marginSummary", {}).get("accountValue", 0))

        notional = abs(pos) * snap["micro"]
        warn = risk.check(pos, notional, equity) if args.live else None
        if warn and warn.startswith("KILL"):
            print(warn); break
        if warn:
            print(warn)

        q = build_quotes(snap, pos, args, args.tick)
        if not q:
            print("skip quote (crossed/degenerate)")
            time.sleep(args.refresh); continue
        bid_px, ask_px, bid_sz, ask_sz = q

        print(f"mid={snap['micro']:.2f} spr={snap['spread_bps']:.1f}bps "
              f"vol={snap['vol_bps']:.1f}bps pos={pos:+.3f} "
              f"| BID {bid_sz:.3f}@{bid_px:.1f}  ASK {ask_sz:.3f}@{ask_px:.1f}")

        if args.live:
            hl.cancel_all(DEX, mkt.asset_id)
            time.sleep(0.15)
            hl.bulk_orders([
                {"a": mkt.asset_id, "b": True,  "p": f"{bid_px}", "s": f"{bid_sz}",
                 "r": False, "t": {"limit": {"tif": "Alo"}}},
                {"a": mkt.asset_id, "b": False, "p": f"{ask_px}", "s": f"{ask_sz}",
                 "r": False, "t": {"limit": {"tif": "Alo"}}},
            ])

        if args.once:
            break
        time.sleep(args.refresh)


if __name__ == "__main__":
    main()
