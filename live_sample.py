"""
Live sampling: watch the touch for N seconds and measure how much a resting
maker would actually get filled.

Counts:
  touch_changes   how often best bid/ask price moved (= flow at the touch)
  trades          trades printed
  vol_usd         notional traded
  hit_bid / hit_ask  aggressor side (which side a resting order would have met)
  size_at_touch   median size resting at best bid/ask (competition you must beat)
"""
import json
import statistics
import sys
import time

import requests

INFO = "https://api.hyperliquid.xyz/info"
S = requests.Session()
S.headers.update({"content-type": "application/json"})


def info(p):
    for i in range(3):
        try:
            r = S.post(INFO, json=p, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.3)
    return None


def sample(coin, seconds=90, interval=1.5):
    seen_trades = set()
    touch_changes = 0
    prev = None
    trades = 0
    vol = 0.0
    hit_bid = hit_ask = 0
    bid_sizes, ask_sizes = [], []
    spreads = []
    t0 = time.time()
    samples = 0

    while time.time() - t0 < seconds:
        b = info({"type": "l2Book", "coin": coin})
        if b and b["levels"][0] and b["levels"][1]:
            bb, ba = float(b["levels"][0][0]["px"]), float(b["levels"][1][0]["px"])
            bs, as_ = float(b["levels"][0][0]["sz"]), float(b["levels"][1][0]["sz"])
            cur = (bb, ba)
            if prev and cur != prev:
                touch_changes += 1
            prev = cur
            bid_sizes.append(bs * bb)
            ask_sizes.append(as_ * ba)
            spreads.append((ba - bb) / ((ba + bb) / 2) * 1e4)
            samples += 1

        tr = info({"type": "recentTrades", "coin": coin}) or []
        for t in tr:
            k = (t.get("tid"), t.get("time"))
            if k in seen_trades:
                continue
            seen_trades.add(k)
            # only count trades inside our sampling window
            if t.get("time", 0) < t0 * 1000:
                continue
            trades += 1
            n = float(t["px"]) * float(t["sz"])
            vol += n
            if t.get("side") == "B":
                hit_ask += 1
            else:
                hit_bid += 1

        time.sleep(interval)

    return {
        "coin": coin, "seconds": seconds, "samples": samples,
        "touch_changes": touch_changes,
        "trades": trades, "vol_usd": vol,
        "hit_bid": hit_bid, "hit_ask": hit_ask,
        "med_bid_usd": statistics.median(bid_sizes) if bid_sizes else 0,
        "med_ask_usd": statistics.median(ask_sizes) if ask_sizes else 0,
        "med_spread_bps": statistics.median(spreads) if spreads else 0,
    }


if __name__ == "__main__":
    coins = sys.argv[1:] or ["xyz:HO", "xyz:QNT", "xyz:PURRDAT", "mkts:USBOND", "xyz:HYUNDAI"]
    secs = 90
    out = []
    for c in coins:
        r = sample(c, secs)
        out.append(r)
        print(f"{c:<16} touch_chg={r['touch_changes']:>3}  trades={r['trades']:>3}  "
              f"vol=${r['vol_usd']:>9,.0f}  hitBid={r['hit_bid']} hitAsk={r['hit_ask']}  "
              f"medTouch=${r['med_bid_usd']:,.0f}/${r['med_ask_usd']:,.0f}  "
              f"spread={r['med_spread_bps']:.1f}b", flush=True)
    json.dump(out, open("/root/pentest/entropy/notes/live_sample.json", "w"), indent=1)
