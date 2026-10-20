"""
fund_dex.py — where is my USDC, and do I even need to move it?

    python3 fund_dex.py --show --key-file wallet.key
    python3 fund_dex.py --to io --amount 15 --key-file wallet.key
    python3 fund_dex.py --to main --from io --amount 15 --key-file wallet.key
    python3 fund_dex.py --set-abstraction unifiedAccount --key-file wallet.key

READ THIS FIRST — HYPERLIQUID HAS TWO COLLATERAL MODELS

  abstraction = "disabled"
      Collateral is held PER DEX. A balance on main does nothing for io or xyz.
      You must sendAsset to each dex before you can trade there.

  abstraction = "unifiedAccount"   (or "portfolioMargin")
      One USDC balance on spot backs positions on EVERY dex. The per-dex
      accountValue still reads 0 for dexes you have not traded, which looks
      like "no funds" but is not. NO transfer is needed — sendAsset is pointless.

  Check yours with `--show`. If you are on unifiedAccount, skip the transfer and
  go straight to trading. That is the most common source of confusion here: the
  API reports 0 on the dex while the money sits on spot, fully usable.
"""
import argparse
import os
import sys

from hl_client import HL

DECIMALS = ("spot", "main", "io", "xyz", "para", "mkts")

LABELS = {
    "spot": "spot wallet — unified collateral source",
    "main": "main perp account",
    "io": "io dex — entropy native markets",
    "xyz": "xyz dex — stocks, indices, commodities",
    "para": "para dex",
    "mkts": "mkts dex",
}

# a liquid growth-mode market per dex, for the "trade directly" suggestion
SAMPLE_COIN = {
    "io": "io:DRAM",
    "xyz": "xyz:TSLA",
    "para": "para:SMH",
    "mkts": "mkts:US500",
    "main": "BTC",
    "spot": "BTC",
}

MODE_HELP = {
    "disabled": "collateral held per dex — transfers REQUIRED",
    "unifiedAccount": "one spot balance backs all dexes — transfers NOT needed",
    "portfolioMargin": "cross-margin portfolio — transfers NOT needed",
}


def dex_arg(name):
    return "" if name == "main" else name


def show(hl):
    mode = hl.abstraction()
    tot, hold, avail = hl.spot_usdc()
    bal = hl.balances()

    print(f"\n  abstraction        {mode}")
    print(f"                     {MODE_HELP.get(mode, '?')}\n")

    print(f"  {'account':8} {'USDC':>14}   note")
    print(f"  {'-' * 8} {'-' * 14}   {'-' * 44}")
    print(f"  {'spot':8} {tot:>14,.6f}   total on spot")
    if hold > 0:
        print(f"  {'':8} {hold:>14,.6f}   held as margin for open positions")
        print(f"  {'':8} {avail:>14,.6f}   free / available")
    for k in ("main", "io", "xyz", "para", "mkts"):
        v = bal.get(k, 0.0)
        print(f"  {k:8} {v:>14,.6f}   {LABELS.get(k, '')}")

    print(f"\n  spendable per dex (the number that matters before an order):")
    for k in ("main", "io", "xyz", "para", "mkts"):
        print(f"    {k:6} ${hl.spendable(dex_arg(k)):>12,.4f}")

    if mode in ("unifiedAccount", "portfolioMargin"):
        print(f"\n  >>> You do NOT need to transfer anything.")
        print(f"      Your spot USDC (${avail:,.2f} free) backs every dex.")
        print(f"      Per-dex balances reading 0 is normal — trade directly.\n")
    elif avail > 0:
        print(f"\n  ${avail:,.2f} free on spot. Per-dex collateral is separate,")
        print(f"  so move it to the dex you want:")
        print(f"    python3 fund_dex.py --from spot --to io --amount {avail:.2f} --key-file wallet.key\n")
    else:
        print()

    return bal, mode, avail


def diagnose_zero(hl):
    """Nothing anywhere — figure out whether funds are at a different address."""
    print("  --- nothing found. checking whether funds exist elsewhere ---")
    hist = hl.deposit_history(20)
    if not hist:
        print("  no ledger history at all for this address.")
        print("  This wallet has NEVER received USDC on Hyperliquid.")
        print()
        print("  Most likely you deposited from a different wallet.")
        print("  entropy.io creates a Privy embedded wallet on login — that address")
        print("  is NOT the same as an exported private key. Compare the deposit")
        print("  address on app.hyperliquid.xyz against:")
        print(f"      {hl.address}")
        return
    print(f"  ledger history found ({len(hist)} entries) — funds did arrive:")
    for u in hist[:8]:
        d = u.get("delta", {})
        t = d.get("type")
        if t == "send":
            print(f"    send   src={d.get('sourceDex') or 'main'!r} "
                  f"dst={d.get('destinationDex') or 'main'!r} usdc={d.get('usdc')}")
        elif t in ("deposit", "withdraw"):
            print(f"    {t:6} usdc={d.get('usdc')}")
        else:
            print(f"    {t}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--from", dest="src", default=None, choices=list(DECIMALS))
    ap.add_argument("--to", dest="dst", default=None, choices=list(DECIMALS))
    ap.add_argument("--amount", type=float, default=None)
    ap.add_argument("--set-abstraction", dest="set_abs", default=None,
                    choices=["disabled", "unifiedAccount", "portfolioMargin"])
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("HL_PRIVATE_KEY")
    if not key and args.key_file:
        if not os.path.exists(args.key_file):
            raise SystemExit(f"{args.key_file} not found")
        key = open(args.key_file).read().strip()
    if not key:
        raise SystemExit("need --key-file or HL_PRIVATE_KEY")

    hl = HL(private_key=key)
    print(f"\nwallet {hl.address}")

    # ---- switch abstraction mode ----
    if args.set_abs:
        cur = hl.abstraction()
        print(f"\n  abstraction now: {cur}")
        print(f"  switching to   : {args.set_abs}")
        print(f"  ({MODE_HELP[args.set_abs]})")
        if args.set_abs in ("unifiedAccount", "portfolioMargin") and cur == "disabled":
            print(f"\n  NOTE: this consolidates per-dex collateral onto spot.")
            print(f"  Open positions stay open; the margin source changes.")
        if not args.yes:
            print("\n  Type YES to switch: ", end="", flush=True)
            if input().strip() != "YES":
                print("  aborted\n")
                return 1
        r = hl.set_abstraction(args.set_abs)
        print(f"\n  response: {r}")
        print(f"  abstraction now: {hl.abstraction()}\n")
        return 0

    bal, mode, avail = show(hl)

    if args.show or not args.dst:
        if not args.dst:
            if sum(bal.values()) <= 0 and avail <= 0:
                diagnose_zero(hl)
            else:
                print("  move funds:  --to <dex> --amount <usd> [--from <dex>]")
                print("  switch mode: --set-abstraction unifiedAccount|disabled\n")
        return 0

    # ---- unified mode: transfer is unnecessary ----
    if mode in ("unifiedAccount", "portfolioMargin"):
        print(f"\n  Your account is '{mode}'. USDC is shared across all dexes.")
        print(f"  You do NOT need to move ${args.amount:,.2f} to '{args.dst}'.")
        print(f"  Free collateral right now: ${avail:,.2f}\n")
        sample = SAMPLE_COIN.get(args.dst, "xyz:TSLA")
        print(f"  Trade directly:")
        print(f"    python3 volume_run.py --coin {sample} --notional {args.amount:.0f} \\")
        print(f"        --live --i-used-referral --key-file wallet.key\n")
        print(f"  If you specifically want per-dex isolation, switch first:")
        print(f"    python3 fund_dex.py --set-abstraction disabled --key-file wallet.key\n")
        return 0

    # ---- disabled mode: pick the source ----
    src = args.src
    if src is None:
        cands = [(k, bal.get(k, 0.0)) for k in ("main", "spot", "io", "xyz", "para", "mkts")]
        cands = [(k, v) for k, v in cands if v > 0 and k != args.dst]
        if not cands:
            print(f"\n  no funds on any account to move from.")
            diagnose_zero(hl)
            print()
            return 1
        cands.sort(key=lambda kv: -kv[1])
        src = cands[0][0]
        print(f"\n  auto-detected source: '{src}' (${cands[0][1]:,.2f})")

    if args.amount is None:
        raise SystemExit("need --amount")

    have = bal.get(src, 0.0)
    if args.amount > have:
        print(f"\n  cannot send ${args.amount:,.2f} — only ${have:,.4f} on '{src}'")
        if src != "spot" and avail > 0:
            print(f"  you have ${avail:,.2f} free on spot instead:")
            print(f"    --from spot --to {args.dst} --amount {min(args.amount, avail):.2f}")
        print()
        return 1
    if args.amount <= 0:
        raise SystemExit("amount must be positive")

    print(f"\n  send ${args.amount:,.2f}   {src} -> {args.dst}")
    print(f"    {LABELS.get(src, src)}")
    print(f"    -> {LABELS.get(args.dst, args.dst)}\n")

    if not args.yes:
        print("  Type YES to send: ", end="", flush=True)
        if input().strip() != "YES":
            print("  aborted\n")
            return 1

    r = hl.send_asset(destination=hl.address, source_dex=dex_arg(src),
                      dest_dex=dex_arg(args.dst), amount=args.amount, token="USDC")
    print(f"\n  response: {r}\n")

    after = hl.balances()
    print("  after:")
    for k in DECIMALS:
        print(f"    {k:<6} {after.get(k, 0):>14,.4f}")

    if after.get(args.dst, 0) > bal.get(args.dst, 0) + 1e-9:
        print(f"\n  OK — funds are on '{args.dst}'. Trade away.\n")
        return 0
    print("\n  transfer did not land — check the response above\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
