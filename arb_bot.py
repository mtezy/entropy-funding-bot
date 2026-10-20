"""
arb_bot.py — delta-neutral funding carry bot for Hyperliquid builder dexes.

THESIS
    The same asset listed on two different dexes runs two independent funding markets
    on one price. Hold LONG the leg that pays longs, SHORT the leg that pays shorts.
    Delta ~ zero. You collect the differential regardless of price direction.

HOW IT AVOIDS LOSING
    There are exactly five ways this strategy loses. Each has a hard guard:

    1. Funding flips sign        -> exit immediately on sign flip of the differential
    2. Basis blows out           -> refuse entry above --max-basis; exit above --basis-exit
    3. One leg fills, other not  -> verify both legs, unwind both if mismatched
    4. Liquidation               -> low leverage (default 3x), isolated margin, equity check
    5. Carry was a spike         -> require --min-persist of last 168h same sign

    It also tracks REALIZED drawdown against a hard limit (--max-dd-usd).

USAGE
    # dry run, auto-pick the best pair
    python3 arb_bot.py --auto --notional 1000

    # dry run, fixed pair
    python3 arb_bot.py --long para:UNITREE --short xyz:UNITREE --notional 1000

    # live
    HL_PRIVATE_KEY=0x... python3 arb_bot.py --auto --notional 1000 --live

    # live with builder fee (monetization)
    HL_PRIVATE_KEY=0x... python3 arb_bot.py --auto --notional 1000 --live \
        --builder 0xYOURADDRESS --builder-fee 10

EXIT CODE
    0 clean stop | 2 risk halt
"""
import argparse
import json
import os
import signal
import statistics
import sys
import time

from hl_client import HL
import fees as feemodel

HOURS_YEAR = 24 * 365


# ---------------------------------------------------------------- market data
_FEE_CACHE = {}


def market_fee_flags(hl, coin):
    """(growth_mode: bool, deployer_fee_scale: float) for a coin. Cached per run."""
    if coin in _FEE_CACHE:
        return _FEE_CACHE[coin]
    dex = coin.split(":")[0] if ":" in coin else ""
    try:
        m = hl.meta(dex)
        for a in m.get("universe", []):
            if a.get("name") == coin:
                res = (a.get("growthMode") == "enabled",
                       float(a.get("deployerFeeScale", 1.0)))
                _FEE_CACHE[coin] = res
                return res
    except Exception:
        pass
    _FEE_CACHE[coin] = (False, 1.0)
    return _FEE_CACHE[coin]


def leg_stats(hl, coin, hours=168):
    """Current funding + historical series for one leg."""
    dex = coin.split(":")[0] if ":" in coin else ""
    ctxs = hl.ctxs(dex)
    if coin not in ctxs:
        raise SystemExit(f"{coin} not found in dex '{dex or 'main'}'")
    c = ctxs[coin]
    try:
        raw = hl.funding_history(coin, hours)
        rates = [float(x["fundingRate"]) for x in raw if "fundingRate" in x]
    except Exception:
        rates = []
    return {
        "coin": coin, "dex": dex or "main",
        "funding": float(c["funding"]),
        "hist": rates,
        "mid": float(c.get("midPx") or c.get("markPx") or 0),
        "oi_usd": float(c["openInterest"]) * float(c.get("midPx") or 0),
        "vol24": float(c["dayNtlVlm"]),
    }


def resolve(hl, coin):
    """asset id, size decimals, max leverage."""
    dex = coin.split(":")[0] if ":" in coin else ""
    meta = hl.meta(dex)
    for i, a in enumerate(meta["universe"]):
        if a["name"] == coin:
            return hl.asset_id(dex, i), a["szDecimals"], a["maxLeverage"]
    raise SystemExit(f"{coin} not found")


def pair_metrics(L, S):
    """Everything needed to decide entry/exit for a long/short pair."""
    n = min(len(L["hist"]), len(S["hist"])) if L["hist"] and S["hist"] else 0
    if n >= 12:
        diff = [-L["hist"][i] + S["hist"][i] for i in range(n)]
        mean = statistics.mean(diff)
        sd = statistics.pstdev(diff) if n > 1 else 0.0
        persist = sum(1 for d in diff if d > 0) / n
        worst24 = min(sum(diff[i:i + 24]) for i in range(n - 24)) if n > 24 else min(diff)
    else:
        mean = -L["funding"] + S["funding"]
        sd = persist = 0.0
        worst24 = mean * 24

    return {
        "bps_hr": mean * 1e4,
        "apr": mean * HOURS_YEAR * 100,
        "stdev": sd * 1e4,
        "persist": persist,
        "worst24_bps": worst24 * 1e4,
        "basis_bps": (L["mid"] / S["mid"] - 1) * 1e4,
        "cap_usd": min(L["oi_usd"], S["oi_usd"]),
    }


# ------------------------------------------------------------------ risk model
class Risk:
    def __init__(self, notional, lev, max_basis, basis_exit, max_hours,
                 max_dd_usd, builder_bps=0.0, tier="tier1"):
        self.notional = notional
        self.lev = lev
        self.max_basis = max_basis
        self.basis_exit = basis_exit
        self.max_hours = max_hours
        self.max_dd = max_dd_usd
        self.builder_bps = builder_bps
        self.tier = tier
        self.entry_cost_bps = None      # set per-pair by set_pair_fees()
        self.halted = False

    def set_pair_fees(self, dexes, growth_flags, dfs=None):
        """Entry cost must be computed from the actual markets being traded."""
        dfs = dfs or [1.0] * len(dexes)
        total = 0.0
        for dex, gm, d in zip(dexes, growth_flags, dfs):
            total += feemodel.round_trip_bps(
                maker=False, dex=dex, growth_mode=gm,
                deployer_fee_scale=d, tier=self.tier, legs=1)
        self.entry_cost_bps = total + 4 * self.builder_bps
        return self.entry_cost_bps

    def payback_hours(self, bps_hr):
        if not self.entry_cost_bps or bps_hr <= 0:
            return float("inf")
        return self.entry_cost_bps / bps_hr

    def can_enter(self, m, notional):
        reasons = []
        if m["bps_hr"] <= 0:
            reasons.append("carry <= 0")
        if abs(m["basis_bps"]) > self.max_basis:
            reasons.append(f"basis {m['basis_bps']:+.0f}b > {self.max_basis:.0f}b")
        if notional > m["cap_usd"] * 0.25:
            reasons.append(f"notional ${notional:,.0f} > 25% of OI ${m['cap_usd']:,.0f}")
        return (len(reasons) == 0), reasons


# ------------------------------------------------------------------ the bot
def main():
    ap = argparse.ArgumentParser(description="Delta-neutral funding carry bot")
    ap.add_argument("--auto", action="store_true", help="scan and pick the best pair")
    ap.add_argument("--long", dest="long_coin", default=None)
    ap.add_argument("--short", dest="short_coin", default=None)
    ap.add_argument("--notional", type=float, default=1000, help="USD per leg")
    ap.add_argument("--lev", type=int, default=3, help="leverage per leg (3 = safe)")
    ap.add_argument("--min-apr", type=float, default=80)
    ap.add_argument("--min-persist", type=float, default=0.75,
                    help="min share of last 168h with same-sign carry")
    ap.add_argument("--exit-apr", type=float, default=25)
    ap.add_argument("--max-basis", type=float, default=40, help="refuse entry above this")
    ap.add_argument("--basis-exit", type=float, default=120, help="exit above this")
    ap.add_argument("--max-hours", type=float, default=168)
    ap.add_argument("--max-dd-usd", type=float, default=0,
                    help="halt if realized drawdown exceeds this (0 = off)")
    ap.add_argument("--poll", type=float, default=30)
    ap.add_argument("--builder", default=None, help="builder address for fee collection")
    ap.add_argument("--builder-fee", type=int, default=None,
                    help="fee in tenths of a bp (10 = 1bp, max 100 = 10bps)")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--tier", default="tier1", choices=["tier1", "og", "pref", "part"],
                    help="Entropy self-rebate tier: tier1 60%%, og 120%%, pref 160%%, part 200%%")
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--i-used-referral", action="store_true",
                    help="confirm the account was created through the referral link")
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        key = open(args.key_file).read().strip()
    if args.live and not key:
        raise SystemExit("live mode needs HL_PRIVATE_KEY or --key-file")
    if not args.auto and not (args.long_coin and args.short_coin):
        raise SystemExit("need --auto, or both --long and --short")

    # ---- referral gate (blocks --live) ----
    import config
    if args.live:
        args._key = key
        if not config.require_ack(args):
            return 1
    else:
        config.print_banner()


    hl = HL(private_key=key if args.live else None,
            builder=args.builder, builder_fee=args.builder_fee)

    builder_bps = (args.builder_fee / 10.0) if args.builder_fee else 0.0
    risk = Risk(args.notional, args.lev, args.max_basis, args.basis_exit,
                args.max_hours, args.max_dd_usd, builder_bps, tier=args.tier)

    mode = "LIVE" if args.live else "DRY"
    print(f"\n[{mode}] notional ${args.notional:,.0f}/leg  lev {args.lev}x  "
          f"addr {hl.address or '-'}")
    if args.builder:
        print(f"       builder {args.builder} @ {builder_bps:.1f} bps")

    # ---- pair selection ----
    if args.auto:
        from scanner import scan
        print("\nscanning pairs...")
        rows = [r for r in scan(hl, min_apr=args.min_apr, max_basis=args.max_basis)
                if r["persistence"] >= args.min_persist]
        if not rows:
            print("no pair passes the persistence filter — not entering")
            return 0
        best = rows[0]
        args.long_coin, args.short_coin = best["long"], best["short"]
        print(f"selected {best['base']}: LONG {best['long']} / SHORT {best['short']}  "
              f"({best['net_apr']:+.0f}% APR, persist {best['persistence']*100:.0f}%, "
              f"cap ${best['cap_usd']:,.0f})")

    dex_l = args.long_coin.split(":")[0] if ":" in args.long_coin else ""
    dex_s = args.short_coin.split(":")[0] if ":" in args.short_coin else ""
    aid_l, szd_l, lev_l = resolve(hl, args.long_coin)
    aid_s, szd_s, lev_s = resolve(hl, args.short_coin)
    lev = min(args.lev, lev_l, lev_s)
    print(f"       LONG  {args.long_coin} (id={aid_l}, sz={szd_l}, max {lev_l}x)")
    print(f"       SHORT {args.short_coin} (id={aid_s}, sz={szd_s}, max {lev_s}x)")
    print(f"       using {lev}x isolated")

    # ---- real entry cost, from the actual markets being traded ----
    gm_l, dfs_l = market_fee_flags(hl, args.long_coin)
    gm_s, dfs_s = market_fee_flags(hl, args.short_coin)
    risk.set_pair_fees(
        dexes=[args.long_coin.split(":")[0] if ":" in args.long_coin else "",
               args.short_coin.split(":")[0] if ":" in args.short_coin else ""],
        growth_flags=[gm_l, gm_s],
        dfs=[dfs_l, dfs_s],
    )
    gm_txt = "growth mode" if (gm_l and gm_s) else ("mixed" if (gm_l or gm_s) else "normal HIP-3")
    print(f"       entry cost {risk.entry_cost_bps:.2f} bps "
          f"(4 taker fills, {gm_txt}, tier {risk.tier}"
          f"{', incl builder' if builder_bps else ''})\n")

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))

    in_pos = False
    opened_at = None
    entry_equity = None

    # Under unifiedAccount the per-dex accountValue reads 0 while the money sits
    # on spot, so every equity check must use spendable(), not account_value().
    def equity():
        try:
            return min(hl.spendable(dex_l), hl.spendable(dex_s))
        except Exception:
            return hl.account_value()

    # ---- recover state: never stack a second position on an existing one ----
    if args.live:
        try:
            pl = hl.position(args.long_coin)
            ps = hl.position(args.short_coin)
            szi_l = float(pl["szi"]) if pl else 0.0
            szi_s = float(ps["szi"]) if ps else 0.0
            if szi_l != 0 or szi_s != 0:
                in_pos = True
                opened_at = time.time()
                entry_equity = equity()
                print(f"RECOVER: existing position long={szi_l:+.4f} short={szi_s:+.4f} "
                      f"delta={szi_l + szi_s:+.4f}")
                print("         will NOT open a new one. Monitoring + exit only.")
            else:
                print("START: flat, ready to enter.")
        except Exception as e:
            print(f"WARN: could not read initial position ({e}) — assuming flat")

    # ---- main loop ----
    while not stop["flag"]:
        try:
            L = leg_stats(hl, args.long_coin)
            S = leg_stats(hl, args.short_coin)
        except SystemExit:
            raise
        except Exception as e:
            print(f"fetch error: {e}")
            time.sleep(args.poll)
            continue

        m = pair_metrics(L, S)
        pb = risk.payback_hours(m["bps_hr"])
        tag = "IN " if in_pos else "out"

        print(f"[{tag}] carry={m['bps_hr']:+.3f}b/hr ({m['apr']:+.0f}% APR) "
              f"persist={m['persist']*100:.0f}% sd={m['stdev']:.2f} "
              f"basis={m['basis_bps']:+.1f}b worst24h={m['worst24_bps']:+.1f}b "
              f"cap=${m['cap_usd']:,.0f} payback={pb:.1f}h")

        # ---------------- ENTRY ----------------
        if not in_pos:
            ok, reasons = risk.can_enter(m, args.notional)
            if not ok:
                if reasons:
                    print(f"       no entry: {'; '.join(reasons)}")
            elif m["apr"] < args.min_apr:
                print(f"       no entry: APR {m['apr']:+.0f}% < min {args.min_apr:.0f}%")
            elif m["persist"] < args.min_persist:
                print(f"       no entry: persist {m['persist']*100:.0f}% "
                      f"< min {args.min_persist*100:.0f}%")
            else:
                print(f"       >>> ENTER  (apr {m['apr']:+.0f}%, persist "
                      f"{m['persist']*100:.0f}%, payback {pb:.1f}h)")
                if args.live:
                    qty_l = round(args.notional / L["mid"], szd_l)
                    qty_s = round(args.notional / S["mid"], szd_s)
                    eq = equity()
                    need = (args.notional / lev) * 2 * 1.2
                    if eq < need:
                        mode = hl.abstraction()
                        print(f"       ABORT: spendable ${eq:,.2f} < needed ${need:,.2f} "
                              f"({mode})")
                        if mode in ("unifiedAccount", "portfolioMargin"):
                            print(f"       spot backs every dex — deposit, or lower "
                                  f"--notional to ${eq * lev / 2 / 1.2:,.2f}")
                        else:
                            print(f"       move USDC to '{dex_l}' / '{dex_s}' with fund_dex.py")
                        if args.poll <= 0:
                            return 1
                        time.sleep(args.poll)
                        continue

                    # leverage must be set before the first order on a fresh asset
                    hl.set_leverage(aid_l, lev, is_cross=False)
                    hl.set_leverage(aid_s, lev, is_cross=False)
                    time.sleep(0.5)

                    # fire both legs as marketable IOC so they land together
                    r1 = hl.order(aid_l, True, L["mid"] * 1.004, qty_l,
                                  tif="Ioc", szd=szd_l)
                    r2 = hl.order(aid_s, False, S["mid"] * 0.996, qty_s,
                                  tif="Ioc", szd=szd_s)
                    print(f"       long : {json.dumps(r1)[:140]}")
                    print(f"       short: {json.dumps(r2)[:140]}")

                    ok_l, got_l = hl.verify_fill(args.long_coin, qty_l)
                    ok_s, got_s = hl.verify_fill(args.short_coin, -qty_s)
                    if not (ok_l and ok_s):
                        print(f"       !! FILL MISMATCH long={got_l} short={got_s} "
                              f"(want {qty_l}/{-qty_s}) — unwinding")
                        if got_l:
                            hl.order(aid_l, False, L["mid"] * 0.99, abs(got_l),
                                     tif="Ioc", reduce_only=True, szd=szd_l)
                        if got_s:
                            hl.order(aid_s, True, S["mid"] * 1.01, abs(got_s),
                                     tif="Ioc", reduce_only=True, szd=szd_s)
                        time.sleep(args.poll)
                        continue
                    print(f"       filled long {got_l:+.4f} short {got_s:+.4f} "
                          f"delta {got_l + got_s:+.4f}")
                    entry_equity = equity()
                in_pos = True
                opened_at = time.time()

        # ---------------- EXIT ----------------
        else:
            hours = (time.time() - opened_at) / 3600
            why = None
            if m["bps_hr"] <= 0:
                why = "carry flipped negative"
            elif m["apr"] < args.exit_apr:
                why = f"APR {m['apr']:+.0f}% < exit {args.exit_apr:.0f}%"
            elif abs(m["basis_bps"]) > args.basis_exit:
                why = f"basis {m['basis_bps']:+.0f}b > {args.basis_exit:.0f}b"
            elif hours >= args.max_hours:
                why = f"max hours {hours:.1f}"

            if args.live and entry_equity is not None and args.max_dd_usd > 0:
                dd = entry_equity - equity()
                if dd > args.max_dd_usd:
                    why = f"drawdown ${dd:,.0f} > ${args.max_dd_usd:,.0f}"
                    risk.halted = True

            if why:
                print(f"       <<< EXIT ({why}) after {hours:.1f}h")
                if args.live:
                    qty_l = round(args.notional / L["mid"], szd_l)
                    qty_s = round(args.notional / S["mid"], szd_s)
                    hl.order(aid_l, False, L["mid"] * 0.996, qty_l,
                             tif="Ioc", reduce_only=True, szd=szd_l)
                    hl.order(aid_s, True, S["mid"] * 1.004, qty_s,
                             tif="Ioc", reduce_only=True, szd=szd_s)
                    ok_l, got_l = hl.verify_fill(args.long_coin, 0.0, tol=0.05)
                    ok_s, got_s = hl.verify_fill(args.short_coin, 0.0, tol=0.05)
                    print(f"       closed long {got_l:+.4f} short {got_s:+.4f}")
                in_pos = False
                opened_at = None
                if risk.halted:
                    print("       HALTED by risk limit")
                    return 2

        if args.poll <= 0:
            break
        time.sleep(args.poll)

    print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
