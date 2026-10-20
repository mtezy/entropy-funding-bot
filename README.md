# Entropy Funding Carry Bot

Delta-neutral funding arbitrage across Hyperliquid builder-deployed perp dexes.

Long one dex, short another, same asset. Price direction cancels. You collect the
**funding differential** between the two listings.

> **This bot is distributed under a referral.**
> Create your account here first: **https://entropy.io/?r=langris**
> The referral binds at account creation and cannot be added later.

Built by [mtezy](https://github.com/mtezy).

---

## What this actually is

Hyperliquid lists the same asset on multiple builder-deployed dexes (`io`, `xyz`, `para`,
`mkts`). Each dex runs its **own independent funding market** on the same underlying price.

```
funding < 0  ->  shorts pay longs  ->  a LONG position RECEIVES
funding > 0  ->  longs pay shorts  ->  a SHORT position RECEIVES
```

When the differential between two listings is large enough to beat entry cost, go long the
leg that pays longs and short the leg that pays shorts. Delta ≈ zero.

### The measured edge (UNITREE, 168 hours live)

```
mean differential        +1.491 bps/hr     = +131% APR
stdev                    1.978 bps/hr
positive hours           77%  of 168
24h windows negative     6%   of 144       (94% profitable)
worst 24h window         -9.8 bps
best 24h window          +87.4 bps
entry + exit cost        ~18 bps (4 taker fills)
payback                  12.1 hours
```

Honest read: 94% of 24-hour holds were profitable over the measured week. The worst 24h
window lost 9.8 bps; add 18 bps entry/exit and a bad short hold is about **−28 bps**. You
need roughly 12 hours to clear entry cost — this is a multi-day position, not a scalp.

---

## Why not market making

Measured, not assumed. The wide-spread markets have no flow:

```
xyz:HO       54 bps spread   0 trades in 90 seconds
mkts:USBOND  24 bps spread   0 trades in 90 seconds
xyz:QNT      16 bps spread   93% of flow one-sided sell, mid drifts -4.5 bps
xyz:HYUNDAI  15 bps spread   73% of flow one-sided sell, mid drifts -9.2 bps
```

At base fee tier (1.5 bps maker), a round trip costs 3 bps. Where the spread is wide there
is no flow; where there is flow it is one-sided and informed, and the drift eats the spread.
Full numbers in [FINDINGS.md](FINDINGS.md).

---

## The five ways this loses, and the guard for each

| # | Failure | Guard |
|---|---|---|
| 1 | Funding flips sign | Exit immediately when the differential goes ≤ 0 |
| 2 | Basis blows out between the two dexes | Refuse entry above `--max-basis`; exit above `--basis-exit` |
| 3 | One leg fills, the other does not | Verify both legs after entry; unwind both on mismatch |
| 4 | Liquidation | Default 3x leverage, isolated margin, equity check before entry |
| 5 | The carry was a spike | Require `--min-persist` of 168h same sign (default 75%) |

Plus a hard drawdown stop (`--max-dd-usd`) and restart recovery that will not stack a second
position on an existing one.

---

## Before your first order: USDC is separate per dex

Hyperliquid holds collateral in separate accounts: `spot`, `main`, and one per builder
dex (`io`, `xyz`, `para`, `mkts`). A deposit lands on spot/main. Trading `xyz:TSLA`
needs margin **on the xyz dex**.

```bash
python3 fund_dex.py --show --key-file wallet.key
python3 fund_dex.py --to xyz --amount 15 --key-file wallet.key
```

`doctor.py` detects this and prints the exact command.

## Install

```bash
pip install requests msgpack eth-account
```

Python 3.9+.

## Quick start

```bash
# 1. account first — referral binds at signup
open https://entropy.io/?r=langris

# 2. wallet
echo "0xYOUR_PRIVATE_KEY" > wallet.key && chmod 600 wallet.key

# 3. readiness check (never sends an order)
python3 preflight.py --auto --key-file wallet.key --notional 1000

# 4. dry run
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --poll 30

# 5. live
python3 arb_bot.py --auto --notional 1000 --min-apr 80 \
    --live --i-used-referral --key-file wallet.key

# 6. monitor
python3 status.py --key-file wallet.key
```

`--live` is blocked without `--i-used-referral`:

```
  BLOCKED: --live requires --i-used-referral
  Open https://entropy.io/?r=langris first, create the account there,
  then re-run with --i-used-referral
```

---

## Files

| file | purpose |
|---|---|
| `arb_bot.py` | The bot. Dry by default. Auto-selects pairs. |
| `preflight.py` | 10-point readiness check. **Never sends an order.** |
| `status.py` | Positions, uPnL, carry now vs 168h mean. Read-only. |
| `scanner.py` | Ranks every dual-listed pair on 168h persistence. |
| `setup_builder.py` | Builder fee approval + referral code setup. |
| `hl_client.py` | Library: EIP-712 signing, orders, positions, builder fees. |
| `config.py` | Referral code + gate. Single source of truth. |
| `fees.py` | The real fee model: HIP-3 2x, growth mode 0.1x, self-rebate tiers. |
| `volume_planner.py` | Capital -> achievable volume, per market type and tier. |
| `volume_run.py` | Post-only maker volume runner. Dry by default. |
| `tier_check.py` | Your rebate tier, referral progress, effective rates. Read-only. |
| `doctor.py` | Diagnoses why a command fails. Read-only, works on any address. |
| `fund_dex.py` | Move USDC between spot / main / builder dex accounts. |
| `funding_arb.py` | Simpler snapshot scanner (reference). |
| `scan_markets.py` | 340-market MM scan (result: not viable). |
| `deepdive.py` | Tick size, ticks-in-spread, flow stats. |
| `live_sample.py` | Real-time flow sampler. |
| `mm_anth.py` | Market maker — works, but not deployed. See FINDINGS §5. |

Docs: [GUIDE.md](GUIDE.md) operator guide · [REFERRAL.md](REFERRAL.md) monetization ·
[VOLUME.md](VOLUME.md) volume economics · [VOLUME_RUN.md](VOLUME_RUN.md) how to run it ·
[FINDINGS.md](FINDINGS.md) full analysis.

## Volume economics in one line

Total volume = capital / net fee rate. Leverage is irrelevant.

```
growth mode maker, OG tier     0.120 bps   ->  $83,333 volume per $1
HIP-3 normal taker, Tier 1     6.300 bps   ->   $1,587 volume per $1
```

$15 of capital reaches **$1.25M** of volume in growth-mode markets at OG tier, or $24k on a
normal market.

```bash
python3 volume_planner.py --capital 15 --target 1000000
```

## Generate volume

```bash
python3 doctor.py --key-file wallet.key --coin xyz:TSLA --notional 15  # diagnose first
python3 fund_dex.py --show --key-file wallet.key            # where is my USDC
python3 tier_check.py --key-file wallet.key                 # tier + referral progress
python3 volume_planner.py --capital 15 --live-flow          # which markets trade
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000    # dry run
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
    --live --i-used-referral --key-file wallet.key          # live
```

Posts post-only maker orders on both sides and reprices on the touch. Other people's flow
fills them — it does not self-match, because that is wash trading and forfeits every reward.

---

## Why the scanner matters more than the APR

Anyone can post an APR. The useful part is the filter:

```
ASSET       LONG            SHORT              NET APR  PERSIST   BASIS    CAPACITY
UNITREE     para:UNITREE    xyz:UNITREE          +131%      77%   -12.4b   1,829,120
SMH         xyz:SMH         para:SMH             +132%      40%   +18.9b      48,920
```

SMH has the same APR as UNITREE and is unusable: 40% persistence, $49k capacity, basis swings
100 bps in seconds. A snapshot scanner ranks SMH #1 at +4308% APR. It is a spike.

```bash
python3 scanner.py --min-apr 50
```

---

## Risks

- **The regime flips.** 30-day average UNITREE funding is the *opposite sign* from the current
  regime. This is a trade, not a yield farm. The bot exits on sign flip.
- **Leg risk.** Both legs are sent and verified, but a connection drop between the two orders
  leaves a naked position. Run on a stable VPS.
- **Liquidation.** 3x, not 10x. Isolated margin. Equity checked before entry.
- **Capacity.** `para:UNITREE` OI is ~$1.8M. The 25%-of-OI cap exists because your own position
  moves the funding you are harvesting.
- **Not free money.** 131% APR is on notional. At $1,000/leg and 3x that is ~$0.36/day on ~$667
  of margin.

---

## License

MIT. No warranty. Trade your own money at your own risk.
