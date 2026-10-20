"""
scanner.py — find the best delta-neutral funding carry across Hyperliquid builder dexes.

The edge: the same asset listed on two different dexes runs two independent funding
markets on one price. Hold long the leg that pays longs, short the leg that pays shorts.
Delta is ~zero, so direction is irrelevant; you collect the differential.

Ranking is NOT by snapshot APR. A snapshot can be a one-hour spike. Every candidate is
scored on:

  persistence   fraction of the last 168h where the differential had the same sign
  net_apr       differential annualised
  capacity      min OI of the two legs (real fill ceiling)
  basis         price gap between the two listings (must be small)
  stability     1 - (stdev of hourly differential / mean)

confidence = persistence^2 * stability * min(net_apr, 1000) * sqrt(capacity)

Usage:
    python3 scanner.py                 # full scan, ranked table
    python3 scanner.py --json          # machine output
    python3 scanner.py --min-apr 100   # filter
"""
import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from hl_client import HL

HOURS_YEAR = 24 * 365
DEXES = ["", "io", "xyz", "para", "mkts"]


def load_all(hl):
    """base symbol -> list of legs across dexes."""
    out = {}
    for dex in DEXES:
        try:
            meta, ctxs = hl.meta_and_ctxs(dex)
        except Exception as e:
            print(f"  dex '{dex or 'main'}': {e}", file=sys.stderr)
            continue
        for i, (m, c) in enumerate(zip(meta["universe"], ctxs)):
            if m.get("isDelisted"):
                continue
            try:
                mid = float(c.get("midPx") or c.get("markPx") or 0)
                funding = float(c.get("funding") or 0)
                oi = float(c.get("openInterest") or 0)
                vol = float(c.get("dayNtlVlm") or 0)
            except (TypeError, ValueError):
                continue
            if mid <= 0:
                continue
            base = m["name"].split(":")[-1]
            out.setdefault(base, []).append({
                "dex": dex or "main", "coin": m["name"], "aid": hl.asset_id(dex, i),
                "mid": mid, "funding": funding, "oi_usd": oi * mid, "vol24": vol,
                "lev": m.get("maxLeverage") or 1, "szd": m.get("szDecimals", 3),
            })
    return out


def history(hl, coin, hours=168):
    """Hourly funding series -> stats."""
    try:
        raw = hl.funding_history(coin, hours)
    except Exception:
        return None
    rates = [float(x["fundingRate"]) for x in raw if "fundingRate" in x]
    if len(rates) < 12:
        return None
    return {
        "n": len(rates),
        "rates": rates,
        "mean": statistics.mean(rates),
        "stdev": statistics.pstdev(rates) if len(rates) > 1 else 0.0,
        "pos_pct": 100 * sum(1 for r in rates if r > 0) / len(rates),
        "neg_pct": 100 * sum(1 for r in rates if r < 0) / len(rates),
        "last": rates[-1],
    }


def evaluate(hl, base, legs):
    """Every ordered pair (long, short) for one base asset."""
    rows = []
    for a in legs:
        for b in legs:
            if a["coin"] == b["coin"]:
                continue
            ha = history(hl, a["coin"])
            hb = history(hl, b["coin"])
            if not ha or not hb:
                continue

            # differential per hour, computed from the historical series
            n = min(len(ha["rates"]), len(hb["rates"]))
            diff = [-ha["rates"][i] + hb["rates"][i] for i in range(n)]
            mean_diff = statistics.mean(diff)
            stdev_diff = statistics.pstdev(diff) if n > 1 else 0.0

            net_apr = mean_diff * HOURS_YEAR * 100
            if net_apr <= 0:
                continue

            # persistence: share of hours where the differential was positive
            persistence = sum(1 for d in diff if d > 0) / n

            # stability: low relative variance = a real regime, not noise
            stability = 1.0 - min(1.0, stdev_diff / abs(mean_diff)) if mean_diff else 0.0

            basis_bps = (a["mid"] / b["mid"] - 1) * 1e4
            cap = min(a["oi_usd"], b["oi_usd"])

            confidence = (persistence ** 2) * max(0.0, stability) \
                * min(net_apr, 1000) * math.sqrt(max(cap, 0))

            rows.append({
                "base": base,
                "long": a["coin"], "short": b["coin"],
                "long_aid": a["aid"], "short_aid": b["aid"],
                "long_szd": a["szd"], "short_szd": b["szd"],
                "lev": min(a["lev"], b["lev"]),
                "long_mid": a["mid"], "short_mid": b["mid"],
                "long_funding_bps_hr": a["funding"] * 1e4,
                "short_funding_bps_hr": b["funding"] * 1e4,
                "mean_diff_bps_hr": mean_diff * 1e4,
                "net_apr": net_apr,
                "persistence": persistence,
                "stability": stability,
                "basis_bps": basis_bps,
                "cap_usd": cap,
                "long_vol": a["vol24"], "short_vol": b["vol24"],
                "confidence": confidence,
            })
    return rows


def scan(hl=None, min_apr=50.0, max_basis=80.0, workers=6):
    hl = hl or HL()
    legs = load_all(hl)
    dupes = {k: v for k, v in legs.items() if len(v) > 1}
    print(f"  {len(dupes)} dual-listed assets, scoring pairs...", file=sys.stderr)

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(evaluate, hl, b, l) for b, l in dupes.items()]
        for f in futs:
            try:
                rows.extend(f.result())
            except Exception:
                pass

    rows = [r for r in rows
            if r["net_apr"] >= min_apr and abs(r["basis_bps"]) <= max_basis]
    rows.sort(key=lambda r: -r["confidence"])
    return rows


def show(rows, limit=20):
    print("=" * 132)
    print(f"{'ASSET':<12}{'LONG':<16}{'SHORT':<16}{'NET APR':>10}{'PERSIST':>9}"
          f"{'STAB':>7}{'BASIS':>9}{'CAPACITY':>12}{'LEV':>5}{'CONF':>10}")
    print("=" * 132)
    for r in rows[:limit]:
        print(f"{r['base']:<12}{r['long']:<16}{r['short']:<16}"
              f"{r['net_apr']:>+9.0f}%{r['persistence']*100:>8.0f}%{r['stability']:>7.2f}"
              f"{r['basis_bps']:>+8.1f}b{r['cap_usd']:>12,.0f}{r['lev']:>5}"
              f"{r['confidence']:>10,.0f}")
    print()
    print("  PERSIST = share of last 168h the carry had the same sign (want > 0.80)")
    print("  STAB    = 1 - relative stdev (want > 0.70; low = noisy/spiky)")
    print("  BASIS   = price gap between the two listings (want < 30 bps)")
    print(f"\n{len(rows)} pairs pass filters")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-apr", type=float, default=50)
    ap.add_argument("--max-basis", type=float, default=80)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = scan(min_apr=args.min_apr, max_basis=args.max_basis)
    if args.json:
        print(json.dumps(rows[:args.limit], indent=1))
    else:
        show(rows, args.limit)
    with open("/root/pentest/entropy/notes/scan_pairs.json", "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
