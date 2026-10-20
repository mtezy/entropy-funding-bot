"""
Deep-dive the top maker candidates: is the spread real, and can you quote inside it?

For each candidate:
  - tick size (px decimals) and how many ticks the spread is -> can you improve?
  - book shape (levels, size at each)
  - recent trade flow (trades/min, average size, aggressor side split)
  - how often the mid actually moves (a static book = no fills for a maker)
"""
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import requests

INFO = "https://api.hyperliquid.xyz/info"
S = requests.Session()
S.headers.update({"content-type": "application/json"})
MAKER = 1.5


def info(p):
    for i in range(3):
        try:
            r = S.post(INFO, json=p, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.5)
    return None


CANDIDATES = [
    "xyz:HYUNDAI", "xyz:HO", "W", "xyz:QNT", "xyz:PURRDAT",
    "mkts:USBOND", "mkts:SMALL2000", "para:10Y", "para:OTHERS",
    "xyz:XLE", "xyz:BMNR", "io:TCNT", "ACE", "xyz:GEV",
]


def decimals(s):
    s = s.rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def analyse(coin):
    b = info({"type": "l2Book", "coin": coin})
    tr = info({"type": "recentTrades", "coin": coin}) or []
    if not b:
        return None
    bids, asks = b["levels"][0], b["levels"][1]
    if not bids or not asks:
        return None

    bb, ba = float(bids[0]["px"]), float(asks[0]["px"])
    mid = (bb + ba) / 2
    spread = ba - bb
    spread_bps = spread / mid * 1e4

    # tick = smallest increment seen in the book
    px_strs = [l["px"] for l in bids[:5] + asks[:5]]
    decs = max(decimals(p) for p in px_strs)
    tick = 10 ** (-decs)
    ticks_in_spread = round(spread / tick) if tick else 0

    # can we quote INSIDE the spread and still clear 2x maker fee?
    # best improvement: half a tick inside each side
    improved_spread = spread - 2 * tick
    improved_bps = improved_spread / mid * 1e4 if improved_spread > 0 else 0
    net_inside = improved_bps - 2 * MAKER

    # flow
    n_tr = len(tr)
    now = time.time() * 1000
    recent = [t for t in tr if now - t.get("time", 0) < 3600_000]
    buys = sum(1 for t in recent if t.get("side") == "B")
    sells = len(recent) - buys
    szs = [float(t["sz"]) for t in recent] or [0]
    notionals = [float(t["px"]) * float(t["sz"]) for t in recent] or [0]

    # book balance
    d_bid = sum(float(l["px"]) * float(l["sz"]) for l in bids)
    d_ask = sum(float(l["px"]) * float(l["sz"]) for l in asks)

    return {
        "coin": coin, "mid": mid, "spread_bps": spread_bps,
        "tick": tick, "ticks_in_spread": ticks_in_spread,
        "net_at_touch": spread_bps - 2 * MAKER,
        "net_inside": net_inside,
        "trades_1h": len(recent), "buy_pct": 100 * buys / max(1, len(recent)),
        "avg_trade_usd": statistics.mean(notionals),
        "median_trade_usd": statistics.median(notionals),
        "book_bid_usd": d_bid, "book_ask_usd": d_ask,
        "levels": len(bids) + len(asks),
    }


def main():
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(analyse, CANDIDATES):
            if r:
                rows.append(r)
    rows.sort(key=lambda x: -x["net_inside"])

    print("=" * 122)
    print(f"{'MARKET':<16}{'MID':>10}{'SPREAD':>9}{'TICKS':>7}{'NET@TOUCH':>11}"
          f"{'NET INSIDE':>12}{'TRADES/H':>10}{'BUY%':>6}{'MED TRADE':>11}{'BOOK BID':>11}{'BOOK ASK':>11}")
    print("=" * 122)
    for r in rows:
        print(f"{r['coin']:<16}{r['mid']:>10.2f}{r['spread_bps']:>8.1f}b{r['ticks_in_spread']:>7}"
              f"{r['net_at_touch']:>+10.1f}b{r['net_inside']:>+11.1f}b"
              f"{r['trades_1h']:>10}{r['buy_pct']:>5.0f}%"
              f"{r['median_trade_usd']:>11,.0f}{r['book_bid_usd']:>11,.0f}{r['book_ask_usd']:>11,.0f}")

    print("\n--- read ---")
    for r in rows:
        if r["net_inside"] <= 0:
            verdict = "PASS - cannot improve enough to beat fees"
        elif r["trades_1h"] < 5:
            verdict = "DEAD - wide but almost no flow"
        elif r["net_inside"] < 3:
            verdict = "MARGINAL - thin edge, needs high fill rate"
        else:
            verdict = "CANDIDATE"
        print(f"  {r['coin']:<16} {verdict}")

    json.dump(rows, open("/root/pentest/entropy/notes/deepdive.json", "w"), indent=1)


if __name__ == "__main__":
    main()
