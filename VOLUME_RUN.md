# How to Run the Volume Runner

Everything below is copy-paste. Read section 0 first — it decides whether the rest is worth doing.

---

## 0a. First, diagnose. Do not skip this.

```bash
python3 doctor.py --key-file wallet.key --coin xyz:TSLA --notional 15
```

It checks packages, key format, balances on every account, market metadata, minimum
order size, margin, fees, flow and referral attribution, then prints the fix.

**The most common failure, and the one that catches everyone:**

> USDC on Hyperliquid is held **separately per dex**. Depositing gives you `spot` and
> `main`. It does **not** give you margin on `xyz`, `para`, `io` or `mkts`.

So this is normal and not a bug:

```
  [WARN]   spot                     $0.00
  [OK  ]   main                     $18.00
  [WARN]   xyz                      $0.00  <- you need money HERE
```

Trading `xyz:TSLA` with $0 on the `xyz` dex fails no matter how much sits on `main`.
Move it:

```bash
python3 fund_dex.py --show --key-file wallet.key                    # where is it?
python3 fund_dex.py --to xyz --amount 15 --key-file wallet.key      # move it
```

Verified against real accounts: an address with $12.51 on spot and $9.96 on xyz shows
`main $0.00`. The separation is real.

---

## 0b. Then check your tier

```bash
python3 tier_check.py --key-file wallet.key
```

Output:

```
=== HYPERLIQUID FEE TIER ===
  maker  1.50 bps
  taker  4.50 bps
  14d volume  $0

=== HYPERLIQUID REFERRAL ===
  referredBy   -
  cumVlm       $0
  referrer     needToTrade
  -> need $10,000 volume, have $0 ($10,000 to go)

=== YOUR EFFECTIVE RATE, PER MARKET TYPE (maker) ===
  setup                      gross   tier1      OG    pref
  main dex                    1.50   1.050   0.600   0.300
  HIP-3 normal                3.00   2.100   1.200   0.600
  growth mode                 0.30   0.210   0.120   0.060

  volume per $1 of capital (growth mode maker):
    tier1  $    47,619
    og     $    83,333
```

**The tier that matters is Entropy's, not Hyperliquid's.** Entropy's self-rebate tier
(60% / 120% / 160% / 200%) is on your entropy.io profile page — the API does not expose it.
Open your profile and check before sizing. If you are Tier 1, `--tier tier1` (the default).
If you somehow have OG, pass `--tier og` and your capacity goes up 75%.

---

## 1. Pick a market that actually trades

Growth mode is necessary but not sufficient — 81 of the 151 growth markets have dead books.

```bash
python3 volume_planner.py --capital 15 --target 100000 --live-flow
```

```
=== growth-mode markets trading RIGHT NOW ===
  market                  lev  trades/5m       vol/5m
  xyz:SP500                50         10 $    155,190
  xyz:XYZ100               30         10 $     43,710
  xyz:SKHX                 10         10 $     29,850
  xyz:SPCX                 20         10 $     25,747
  xyz:AMD                  10         10 $     23,902
```

Pick from the top. `xyz:SP500` is the default for a reason: highest flow, 50x max leverage,
and the tightest spread of the group.

**Check the spread before you commit.** Maker orders only fill if price moves through your
level. A 1-tick spread means you fill often; a 40-tick spread means your order sits there
while everyone else trades around you.

---

## 2. Dry run — always

No orders. Watch the quotes it would place.

```bash
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 --poll 5
```

```
[DRY] xyz:SP500
  growth mode      yes
  maker fee        0.30 bps gross  ->  0.21 bps net (tier tier1)
  volume per $1    47,619
  target           $100,000
  capital to spend $2.10 on fees
  quote size       50.0 max units/side, $50 notional
  leverage         3x isolated
  max inventory    0.013 units
  quote buy  0.007 @ 7687.3
  quote sell 0.007 @ 7687.4
```

What to check:

- `growth mode yes` — if it says NO, stop. Fees are 10x higher and the whole plan changes.
- `maker fee` — should be 0.30 gross / 0.21 net. Anything near 3.00 means normal HIP-3.
- `quote size` — must be at least the market minimum. If it rounds to zero, raise `--notional`.
- The buy/sell prices should sit one tick apart, tracking the real book.

Let it run a few minutes. If the quotes never move, the book is frozen — pick another market.

---

## 3. Go live

```bash
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
    --live --i-used-referral --key-file wallet.key
```

It asks for confirmation before sending anything:

```
  account value    $52.30
  This will place real post-only orders on xyz:SP500.
  Type YES to continue:
```

Then:

```
  leverage set
  quote buy  0.006 @ 7687.3
  quote sell 0.006 @ 7687.4
  vol $        1,230 / $100,000 (  1.2%)  fills    3  inv +0.006  fee $0.00  $410/min
```

`vol` only counts **real fills** from the exchange — not orders placed. That is the number
that matters and the only one that cannot lie.

---

## 4. What it is actually doing

```
every --poll seconds:
  1. read the book
  2. if our resting quote is at a different price, cancel and re-quote at the new touch
  3. post-only (Alo) so we can never accidentally take
  4. read fills since start -> volume, fees
  5. if inventory exceeds --max-inv, stop quoting that side
  6. on exit: cancel everything, flatten with IOC
```

**It never matches your own orders against each other.** Both sides rest in the book and
only other people's orders fill them. That is the difference between market making and wash
trading, and it is the difference between keeping your rebate and losing it.

---

## 5. The honest limitation

**You cannot force fills.** Volume depends on other people trading through your price. On
`xyz:SP500` that happens often; on a dead market it never happens. No script fixes this.

So the realistic outcome varies:

```
liquid market, tight spread    ->  fills steadily, volume accrues
wide spread, thin book         ->  your order sits, volume stays at 0
frozen book                    ->  nothing, ever
```

If you run for an hour and `vol` is still $0, the market is wrong, not the script. Move on.

---

## 6. Sizing

The quote size is `--notional` in USD per side. It has to be:

- **large enough** to clear the minimum order size
- **small enough** that your inventory cap is affordable

```
--notional 50    $50 per side, inventory cap $100
--notional 200   $200 per side, inventory cap $400
```

Margin needed is `notional / lev`. At 3x and $50 per side that is ~$17 per leg. Keep
`account value` at least 2x that so fees and adverse moves do not liquidate you.

Fees come out of the same margin. At $100k of volume you spend $2.10 net, but **$3.00 gross**
is deducted as you trade and only rebated bi-weekly. Budget the gross number.

---

## 7. Stopping

`Ctrl+C` is safe. The runner cancels all orders and flattens any inventory before exiting:

```
  shutting down
  flattening +0.006 with IOC

  === RESULT ===
  volume       $12,480
  fills        31
  fees paid    $0.37  (gross; rebate is bi-weekly)
  net cost     $0.26 at tier tier1
  runtime      18.4 min
  equity now   $52.19
```

If it dies hard (`kill -9`, power loss), orders stay on the book. Clean up with:

```bash
python3 -c "
from hl_client import HL
hl = HL(private_key=open('wallet.key').read().strip())
print(hl.cancel_all('xyz', 200000+0))
print(hl.all_positions())
"
```

Or just use the Hyperliquid UI to cancel and close.

---

## 8. Run it in the background

```bash
tmux new -s vol
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
    --live --i-used-referral --key-file wallet.key
# Ctrl+B then D
```

Check on it:

```bash
tmux attach -t vol
```

Or as a service — but note `--yes` skips the confirmation prompt, so only use it after you
have run it interactively at least once:

```bash
sudo tee /etc/systemd/system/vol-run.service > /dev/null <<'EOF'
[Unit]
Description=Entropy volume runner
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/entropy
ExecStart=/usr/bin/python3 -u volume_run.py --coin xyz:SP500 --notional 50 \
    --target 100000 --live --i-used-referral --yes --key-file wallet.key
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF
```

---

## 9. Command reference

```bash
# check tier, referral progress, effective rates
python3 tier_check.py --key-file wallet.key

# what capital buys what volume
python3 volume_planner.py --capital 15 --target 1000000
python3 volume_planner.py --capital 15 --target 100000 --live-flow

# dry run
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 --poll 5

# live
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
    --live --i-used-referral --key-file wallet.key

# live at OG tier (if your entropy profile says so)
python3 volume_run.py --coin xyz:SP500 --notional 50 --target 100000 \
    --tier og --live --i-used-referral --key-file wallet.key

# tighter inventory control
python3 volume_run.py --coin xyz:SP500 --notional 100 --max-inv 0.02 ...
```

### Flags that matter

```
--coin          growth-mode market (check with volume_planner --live-flow)
--notional      USD per side. Too small = below min size. Too large = inventory risk.
--lev           margin efficiency only. Does not change volume capacity.
--target        stop when this volume is reached
--max-inv       units of inventory allowed before one side stops quoting
--tier          tier1 (60%) or og (120%) — from your entropy profile
--poll          requote interval. 3-5s on a liquid market, 10s+ on a slow one.
--max-minutes   hard stop
```

---

## 10. The three ways this loses money

**1. Inventory.** One side fills, price keeps going, you are holding a directional position.
`--max-inv` caps the size but not the direction. If `inv` grows and stays, you are being run
over — stop and flatten.

**2. Fees on volume that earns nothing.** If entropy's reward program changes or your
attribution is broken, you paid 0.30 bps per fill for volume that buys nothing. Check that
your account is actually attributed (`tier_check.py` shows `referredBy`) before doing size.

**3. Wash-trading classification.** If your own orders fill each other, entropy's docs say
all pending and future rewards are forfeit. This runner quotes post-only on both sides, so
it is structurally exposed to that outcome on a market with no third-party flow. That is why
you check `--live-flow` first and stop when volume is not accruing.

---

## 11. FAQ

**Why is my volume $0 after ten minutes?**
No third-party flow through your price. Pick a market from the top of `--live-flow`.

**Why did it only quote one side?**
Inventory hit `--max-inv`. That is the guard working. Flatten and it resumes both sides.

**Can I run several markets at once?**
Yes, one process each, different `--coin`. Keep total margin under your risk budget. They
share the same wallet, so `--max-inv` is per-process — watch combined inventory.

**Does leverage increase volume?**
No. `max volume = capital / net_fee_rate`. Leverage only reduces the margin you tie up.

**Is this the same as the funding bot?**
No. `arb_bot.py` harvests funding differentials for yield. `volume_run.py` generates volume
for tier/referral progress. Different goals, different risk. You can run both.

**Do I need $15 minimum?**
You need enough for margin plus gross fees. At $50 per side and 3x that is ~$17 of margin,
plus a buffer. $30-50 is a realistic floor. $15 works only at small `--notional` with no
buffer for a losing fill.
