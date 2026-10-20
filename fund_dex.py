"""
fund_dex.py — move USDC between your spot wallet, main perp account, and builder dexes.

    # where is my money?
    python3 fund_dex.py --show --key-file wallet.key

    # move $15 from the main perp account to the xyz dex (needed for xyz:TSLA)
    python3 fund_dex.py --to xyz --amount 15 --key-file wallet.key

    # move it back
    python3 fund_dex.py --to main --from xyz --amount 15 --key-file wallet.key

WHY THIS EXISTS
    On Hyperliquid, USDC is held separately per account:

        spot      the spot wallet
        main      the default perp account (BTC, ETH, ...)
        io        the entropy deployer dex
        xyz       the xyz dex (TSLA, SP500, AAPL, ...)
        para      the para dex
        mkts      the mkts dex

    Depositing USDC gives you spot and main. It does NOT give you margin on xyz.
    Trading xyz:TSLA with $0 on the xyz dex fails with "insufficient margin" no
    matter how much is sitting on main. That is the single most common reason a
    first order fails, and it is not obvious from the error.
"""
import argparse
import os
import sys

from hl_client import HL

LABELS = {
    "spot": "spot wallet",
    "main": "main perp account (default USDC dex)",
    "io": "io dex (entropy)",
    "xyz": "xyz dex (stocks, indices)",
    "para": "para dex",
    "mkts": "mkts dex",
}


def dex_arg(name):
    return "" if name == "main" else name


def show(hl):
    print("\n=== BALANCES ===\n")
    bal = hl.balances()
    total = 0.0
    for k in ("spot", "main", "io", "xyz", "para", "mkts"):
        v = bal.get(k, 0.0)
        total += v
        print(f"  {k:<6} ${v:>12,.2f}   {LABELS.get(k, '')}")
    print(f"  {'TOTAL':<6} ${total:>12,.2f}\n")
    if total == 0:
        print("  Nothing deposited. Send USDC to your Hyperliquid account first:")
        print("  https://app.hyperliquid.xyz  (deposit via Arbitrum)\n")
    return bal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="just print balances")
    ap.add_argument("--from", dest="src", default="main",
                    choices=["spot", "main", "io", "xyz", "para", "mkts"])
    ap.add_argument("--to", dest="dst", default=None,
                    choices=["spot", "main", "io", "xyz", "para", "mkts"])
    ap.add_argument("--amount", type=float, default=None)
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

    bal = show(hl)
    if args.show or not args.dst:
        if not args.dst:
            print("  To move funds: --to <dex> --amount <usd>")
            print("  Example:       --to xyz --amount 15\n")
        return 0

    if args.amount is None:
        raise SystemExit("need --amount")

    have = bal.get(args.src, 0.0)
    if args.amount > have:
        print(f"  cannot send ${args.amount:,.2f} — only ${have:,.2f} on '{args.src}'\n")
        return 1
    if args.amount <= 0:
        raise SystemExit("amount must be positive")

    print(f"  send ${args.amount:,.2f}  {args.src} -> {args.dst}")
    print(f"  {LABELS.get(args.src, args.src)}  ->  {LABELS.get(args.dst, args.dst)}\n")

    if not args.yes:
        print("  Type YES to send: ", end="", flush=True)
        if input().strip() != "YES":
            print("  aborted\n")
            return 1

    r = hl.send_asset(
        destination=hl.address,
        source_dex=dex_arg(args.src),
        dest_dex=dex_arg(args.dst),
        amount=args.amount,
        token="USDC",
    )
    print(f"\n  response: {r}\n")

    after = hl.balances()
    print("  after:")
    for k in ("spot", "main", "io", "xyz", "para", "mkts"):
        print(f"    {k:<6} ${after.get(k, 0):>12,.2f}")
    print()

    if after.get(args.dst, 0) > bal.get(args.dst, 0):
        print(f"  OK — funds are on '{args.dst}'. You can trade now.\n")
        return 0
    print("  transfer did not land — check the response above\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
