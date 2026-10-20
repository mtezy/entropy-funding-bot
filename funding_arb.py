"""
Cross-dex funding arb scanner for Hyperliquid builder dexes.

Same asset listed on 2+ dexes = two independent funding markets on one price.
If funding differs, go LONG the side that pays longs and SHORT the side that
charges longs. Delta neutral, collect the differential.

  funding < 0  ->  shorts pay longs  ->  LONG receives
  funding > 0  ->  longs pay shorts  ->  SHORT receives

net_apr = (funding_received_side - funding_paid_side) * 24 * 365 * 100
"""
import json
import time

import requests

INFO = "https://api.hyperliquid.xyz/info"
S = requests.Session()
S.headers.update({"content-type": "application/json"})
DEXES = ["", "io", "xyz", "para", "mkts"]
HOURS_YEAR = 24 * 365


def info(p):
    for i in range(3):
        try:
            r = S.post(INFO, json=p, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.4 * (i + 1))
    return None


def load():
    """base symbol -> list of {dex, coin, mid, oracle, funding, oi_usd, vol24, lev}"""
    out = {}
    for dex in DEXES:
        d = info({"type": "metaAndAssetCtxs", "dex": dex}) if dex else info({"type": "metaAndAssetCtxs", "dex": ""})
        if not d or not isinstance(d, list):
            continue
        meta, ctxs = d[0], d[1]
        for m, c in zip(meta["universe"], ctxs):
            if m.get("isDelisted"):
                continue
            try:
                mid = float(c.get("midPx") or c.get("markPx") or 0)
                oracle = float(c.get("oraclePx") or 0)
                funding = float(c.get("funding") or 0)
                oi = float(c.get("openInterest") or 0)
                vol = float(c.get("dayNtlVlm") or 0)
            except (TypeError, ValueError):
                continue
            if mid <= 0:
                continue
            base = m["name"].split(":")[-1]
            out.setdefault(base, []).append({
                "dex": dex or "main", "coin": m["name"], "mid": mid, "oracle": oracle,
                "funding": funding, "oi_usd": oi * mid, "vol24": vol,
                "lev": m.get("maxLeverage"),
            })
    return out


def funding_history(coin, hours=168):
    """Average funding over the last N hours — a single snapshot can be a spike."""
    now = int(time.time() * 1000)
    d = info({"type": "fundingHistory", "coin": coin, "startTime": now - hours * 3600_000})
    if not d:
        return None
    rates = [float(x["fundingRate"]) for x in d if "fundingRate" in x]
    if not rates:
        return None
    return {
        "n": len(rates),
        "avg": sum(rates) / len(rates),
        "last": rates[-1],
        "min": min(rates),
        "max": max(rates),
        "pos_pct": 100 * sum(1 for r in rates if r > 0) / len(rates),
    }


def main():
    m = load()
    dupes = {k: v for k, v in m.items() if len(v) > 1}

    rows = []
    for base, legs in dupes.items():
        for i in range(len(legs)):
            for j in range(len(legs)):
                if i == j:
                    continue
                a, b = legs[i], legs[j]
                # long a, short b
                # a pays longs when funding<0 -> receive -a.funding
                recv = -a["funding"]
                pay = b["funding"]        # short b: receives if funding>0, pays if <0
                # short b cashflow = +b.funding
                net_hourly = recv + b["funding"]
                net_apr = net_hourly * HOURS_YEAR * 100
                basis_bps = (a["mid"] / b["mid"] - 1) * 1e4 if b["mid"] else 0
                if net_apr <= 0:
                    continue
                cap = min(a["oi_usd"], b["oi_usd"])
                rows.append({
                    "base": base,
                    "long": a["coin"], "short": b["coin"],
                    "net_apr": net_apr,
                    "long_funding_apr": -a["funding"] * HOURS_YEAR * 100,
                    "short_funding_apr": b["funding"] * HOURS_YEAR * 100,
                    "basis_bps": basis_bps,
                    "cap_usd": cap,
                    "long_oi": a["oi_usd"], "short_oi": b["oi_usd"],
                    "long_vol": a["vol24"], "short_vol": b["vol24"],
                    "lev": min(a["lev"] or 1, b["lev"] or 1),
                })

    rows.sort(key=lambda x: -x["net_apr"])

    print("=" * 116)
    print(f"{'ASSET':<12}{'LONG':<16}{'SHORT':<16}{'NET APR':>10}{'BASIS':>9}"
          f"{'CAPACITY':>12}{'LONG OI':>12}{'SHORT OI':>12}{'LEV':>5}")
    print("=" * 116)
    seen = set()
    for r in rows:
        k = (r["base"], r["long"], r["short"])
        if k in seen:
            continue
        seen.add(k)
        print(f"{r['base']:<12}{r['long']:<16}{r['short']:<16}{r['net_apr']:>+9.0f}%"
              f"{r['basis_bps']:>+8.1f}b{r['cap_usd']:>12,.0f}{r['long_oi']:>12,.0f}"
              f"{r['short_oi']:>12,.0f}{r['lev']:>5}")
        if len(seen) >= 20:
            break

    print(f"\n{len(rows)} positive-carry pairs across {len(dupes)} dual-listed assets")

    # ---- verify the top 3 over 7 days ----
    print("\n" + "=" * 116)
    print("7-DAY FUNDING STABILITY CHECK (is the differential persistent or a spike?)")
    print("=" * 116)
    for r in rows[:3]:
        hl = funding_history(r["long"], 168)
        hs = funding_history(r["short"], 168)
        if not hl or not hs:
            print(f"{r['base']}: no history")
            continue
        avg_net = (-hl["avg"] + hs["avg"]) * HOURS_YEAR * 100
        print(f"{r['base']:<10} long {r['long']:<15} 7d avg {hl['avg']*HOURS_YEAR*100:>+7.0f}% "
              f"(pos {hl['pos_pct']:.0f}% of hrs)  short {r['short']:<15} 7d avg "
              f"{hs['avg']*HOURS_YEAR*100:>+7.0f}% (pos {hs['pos_pct']:.0f}%)")
        print(f"{'':10} -> persistent net APR {avg_net:+.0f}%   (snapshot said {r['net_apr']:+.0f}%)")

    json.dump(rows, open("/root/pentest/entropy/notes/funding_arb.json", "w"), indent=1)


if __name__ == "__main__":
    main()
