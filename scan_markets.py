"""
Scan every Hyperliquid builder-deployed market for maker-economics viability.

For each market compute:
  spread_bps      top-of-book spread
  gross_edge      spread captured by resting both sides (buy bid, sell ask)
  net_edge        gross - 2*maker_fee  (round trip)
  depth_usd       notional resting within N bps of mid (how much size you can actually get filled)
  funding_apr     funding rate annualised
  score           net_edge * sqrt(depth)  -> favours wide spread AND real depth
"""
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

INFO = "https://api.hyperliquid.xyz/info"
S = requests.Session()
S.headers.update({"content-type": "application/json"})

MAKER_FEE_BPS = 1.5     # HL base tier maker
TAKER_FEE_BPS = 4.5


def info(payload, tries=3):
    for i in range(tries):
        try:
            r = S.post(INFO, json=payload, timeout=15)
            if r.status_code == 200:
                return r.json()
            time.sleep(0.4 * (i + 1))
        except Exception:
            time.sleep(0.4 * (i + 1))
    return None


def depth_within(levels, mid, bps):
    lim = mid * bps / 1e4
    return sum(float(l["px"]) * float(l["sz"]) for l in levels
               if abs(float(l["px"]) - mid) <= lim)


def scan_coin(dex, coin, ctx):
    b = info({"type": "l2Book", "coin": coin})
    if not b:
        return None
    bids, asks = b["levels"][0], b["levels"][1]
    if not bids or not asks:
        return None
    bb, ba = float(bids[0]["px"]), float(asks[0]["px"])
    if bb <= 0 or ba <= 0 or ba < bb:
        return None
    mid = (bb + ba) / 2
    spread_bps = (ba - bb) / mid * 1e4

    # depth in USD resting within 25 bps of mid, both sides
    d_bid = depth_within(bids, mid, 25)
    d_ask = depth_within(asks, mid, 25)
    depth_usd = min(d_bid, d_ask)      # min of the two = balanced fill capacity

    gross = spread_bps
    net = gross - 2 * MAKER_FEE_BPS

    funding = float(ctx.get("funding") or 0)
    funding_apr = funding * 24 * 365 * 100

    oi = float(ctx.get("openInterest") or 0)
    vol24 = float(ctx.get("dayNtlVlm") or 0)

    score = net * math.sqrt(max(depth_usd, 0)) if net > 0 else 0.0

    return {
        "dex": dex, "coin": coin, "mid": mid, "spread_bps": spread_bps,
        "net_edge_bps": net, "depth_usd": depth_usd,
        "oi_usd": oi * mid, "vol24_usd": vol24,
        "funding_apr": funding_apr,
        "bid": bb, "ask": ba,
        "score": score,
    }


def main():
    mk = info({"type": "metaAndAssetCtxs", "dex": ""})
    print("fetching dex list...", file=sys.stderr)
    dexes = ["", "io", "xyz", "para", "mkts"]

    jobs = []
    for dex in dexes:
        d = info({"type": "metaAndAssetCtxs", "dex": dex}) if dex else mk
        if not d or not isinstance(d, list) or len(d) < 2:
            print(f"  dex '{dex or 'main'}': skip", file=sys.stderr)
            continue
        meta, ctxs = d[0], d[1]
        uni = meta.get("universe", [])
        print(f"  dex '{dex or 'main'}': {len(uni)} markets", file=sys.stderr)
        for m, c in zip(uni, ctxs):
            if m.get("isDelisted"):
                continue
            jobs.append((dex, m["name"], c))

    print(f"scanning {len(jobs)} markets...", file=sys.stderr)
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(scan_coin, d, n, c) for d, n, c in jobs]
        for i, f in enumerate(futs):
            try:
                r = f.result()
                if r:
                    rows.append(r)
            except Exception:
                pass
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(jobs)}", file=sys.stderr)

    rows.sort(key=lambda x: -x["score"])
    json.dump(rows, open("/root/pentest/entropy/notes/scan.json", "w"), indent=1)
    print(f"\nscanned {len(rows)} live books -> notes/scan.json\n")

    # ---- table: viable maker markets ----
    print("=" * 108)
    print(f"{'MARKET':<16}{'SPREAD':>8}{'NET EDGE':>10}{'DEPTH 25bp':>13}{'OI':>14}{'VOL24':>14}{'FUND APR':>10}")
    print("=" * 108)
    shown = 0
    for r in rows:
        if r["net_edge_bps"] <= 0:
            continue
        if r["depth_usd"] < 500:
            continue
        print(f"{r['coin']:<16}{r['spread_bps']:>7.1f}b{r['net_edge_bps']:>+9.1f}b"
              f"{r['depth_usd']:>13,.0f}{r['oi_usd']:>14,.0f}{r['vol24_usd']:>14,.0f}"
              f"{r['funding_apr']:>9.1f}%")
        shown += 1
        if shown >= 30:
            break
    if shown == 0:
        print("  (no market clears 2x maker fee with >=$500 depth)")

    # ---- stats ----
    pos = [r for r in rows if r["net_edge_bps"] > 0]
    print(f"\nmarkets with positive net edge after fees: {len(pos)} / {len(rows)}")
    print(f"markets with net edge > 2bps AND depth >= $1k: "
          f"{len([r for r in pos if r['net_edge_bps'] > 2 and r['depth_usd'] >= 1000])}")
    if rows:
        print(f"median spread across all live books: "
              f"{sorted(r['spread_bps'] for r in rows)[len(rows)//2]:.1f} bps")

    # ---- funding extremes (delta-neutral candidate) ----
    print("\n" + "=" * 108)
    print("FUNDING EXTREMES (annualised, |APR| > 30%) — delta-neutral candidates")
    print("=" * 108)
    fr = sorted(rows, key=lambda x: -abs(x["funding_apr"]))
    for r in fr[:15]:
        if abs(r["funding_apr"]) < 30:
            break
        side = "SHORT perp" if r["funding_apr"] > 0 else "LONG perp"
        print(f"{r['coin']:<16}{r['funding_apr']:>+9.1f}% APR  {side:<11} "
              f"OI ${r['oi_usd']:>12,.0f}  spread {r['spread_bps']:.1f}b")


if __name__ == "__main__":
    main()
