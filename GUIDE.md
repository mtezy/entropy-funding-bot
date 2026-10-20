# Funding Carry Bot — Operator's Guide

> ## ⚠ STEP 0 — REFERRAL IS MANDATORY
>
> Create your account through this link **before** anything else:
>
> # https://entropy.io/?r=langris
>
> The referral is bound **at account creation**. It cannot be added later — if you deposit
> first and click the link afterwards, you are not attributed. Do this first.
>
> The bot enforces it: `--live` will not start without `--i-used-referral`.

---

## 1. What the edge actually is

Hyperliquid lists the same asset on multiple **builder-deployed dexes** (`io`, `xyz`, `para`,
`mkts`). Each dex runs its **own independent funding market** on the same underlying price.

Funding is a periodic payment between longs and shorts:

```
funding < 0  ->  shorts pay longs  ->  a LONG position RECEIVES
funding > 0  ->  longs pay shorts  ->  a SHORT position RECEIVES
```

If `para:UNITREE` pays longs and `xyz:UNITREE` pays shorts less, you go:

```
LONG  para:UNITREE
SHORT xyz:UNITREE
```

Price up or price down, the two legs cancel. You keep the **funding differential**.

Live example, measured 2026-10-20:

```
para:UNITREE   funding -4.21 bps/hr   ->  long leg receives
xyz:UNITREE    funding -0.46 bps/hr   ->  short leg pays a little
differential   +1.49 bps/hr average over 168h  =  +131% APR
```

At $1,000 per leg that is **$0.36/day** gross, on roughly **$667 of margin** at 3x.
Small in absolute terms. Real, repeatable, and it scales with capital.

---

## 2. Why this is the honest answer, not a promise

You asked for a strategy that does not lose. No strategy does that. What can be done is
**enumerate every way it loses, measure the probability, and guard each one.** That is what
this bot does.

### The measured distribution (UNITREE, 168 hours of live funding)

```
mean differential        +1.491 bps/hr     = +131% APR
stdev                    1.978 bps/hr
positive hours           77%  of 168
24h windows negative     6%   of 144
worst 24h window         -9.8 bps
best 24h window          +87.4 bps
entry + exit cost        ~18 bps (4 taker fills)
payback                  12.1 hours
```

Read that honestly:

- **94% of 24-hour holds were profitable.**
- The worst 24h window lost 9.8 bps. Add 18 bps of entry/exit and the realistic bad case on a
  short hold is about **-28 bps**.
- You need to hold roughly **12 hours** to clear the entry cost. Anything shorter is a coin flip
  weighted against you.

Compare against the alternative that looked better and was not:

```
SMH   mean +1.507 bps/hr   stdev 7.317   40% positive hours
```

Same average, five times the noise, and only 40% of hours positive. A snapshot scanner would have
ranked SMH first at +4308% APR. It is a spike, not a regime. The bot scores on 168h persistence,
so it picks UNITREE and ignores SMH.

### The five ways it loses, and the guard for each

| # | Failure | Guard |
|---|---|---|
| 1 | Funding flips sign | Exit immediately when the differential goes ≤ 0 |
| 2 | Basis blows out between the two dexes | Refuse entry above `--max-basis`; exit above `--basis-exit` |
| 3 | One leg fills, the other does not | Verify both legs after entry; unwind both on mismatch |
| 4 | Liquidation | Default 3x leverage, isolated margin, equity check before entry |
| 5 | The carry was a spike | Require `--min-persist` of 168h same sign (default 75%) |

Plus a hard drawdown stop (`--max-dd-usd`) that halts the bot if realized equity drops more than
you allow.

---

## 3. Requirements

- **An EVM wallet.** Use a fresh one, not your main wallet.
- **USDC on Hyperliquid.** Start with ~$500–$1,000. Deposit via the Arbitrum bridge at
  [app.hyperliquid.xyz](https://app.hyperliquid.xyz).
- **A machine that stays on.** The bot polls every 30 s. A VPS is correct; a laptop that sleeps is not.
- **Python 3.9+** with `requests`, `msgpack`, `eth-account`.

```bash
pip install requests msgpack eth-account
```

---

## 4. Setup

### 4.0 Create the account through the referral link (MANDATORY)

```
https://entropy.io/?r=langris
```

Open it **first**. The SPA stores the code in `localStorage["entropy.referral-code.v1"]`
and binds it to your account at signup. There is no way to attach it afterwards.

### 4.1 Wallet file

```bash
echo "0xYOUR_PRIVATE_KEY" > wallet.key
chmod 600 wallet.key
```

Verify it derives:

```bash
python3 preflight.py --key-file wallet.key --notional 1000
```

If an address appears on the first line, the key is valid.

### 4.2 Deposit USDC

Bridge USDC (Arbitrum → Hyperliquid) at [app.hyperliquid.xyz](https://app.hyperliquid.xyz).
Confirm with `preflight.py` — the `account value` line must show your balance.

---

## 5. Preflight — always run this first

Never sends an order. Safe to run any time.

```bash
python3 preflight.py --auto --key-file wallet.key --notional 1000
```

`--auto` scans every dual-listed pair and picks the best one. Sample output:

```
=== PREFLIGHT ===

  [OK ] wallet address                 0x4e37...
  [OK ] auto-selected                  UNITREE  para:UNITREE / xyz:UNITREE
  [OK ] account value                  $1,000.00   withdrawable $1,000.00
  [OK ] LONG  para:UNITREE             id=200019 szDec=2 maxLev=10x mode=noCross
          price 77.1530   funding -4.21 bps/hr   OI $1,830,177   vol24 $1,259,548
  [OK ] SHORT xyz:UNITREE              id=200104 szDec=2 maxLev=10x mode=noCross
          price 77.2840   funding -0.46 bps/hr   OI $19,532,503  vol24 $42,255,264

  [OK ] carry (168h mean)              +1.491 bps/hr = +131% APR   (min 80%)
  [OK ] persistence                    77% of 168h same sign   (min 75%)
  [OK ] payback                        12.1h to recover 18 bps entry cost
  [OK ] worst 24h window               -9.8 bps   (this is your bad case)
  [OK ] volatility                     stdev 1.98 bps/hr
  [OK ] basis                          -12.4 bps   (entry blocked above 40)
  [OK ] capacity                       notional $1,000 vs 25% OI $457,544
  [OK ] margin                         need ~$667 at 3x + buffer, have $1,000
  [--] leverage not set (use --fix to apply 3x isolated)

=== RESULT: READY TO RUN ===
```

### How to read each line

| Line | Meaning | If it shows `!!` |
|---|---|---|
| `carry (168h mean)` | Real average carry, not a snapshot | Below minimum → wait, do not force it |
| `persistence` | Share of last week the carry held | Below 75% → it is a spike, skip |
| `payback` | Hours to recover entry cost | Over 48h → the edge is too thin |
| `worst 24h window` | Your realistic bad case | Large negative → size down |
| `basis` | Price gap between the two listings | Over 40 bps → prices have decoupled |
| `capacity` | Your notional vs open interest | Too large → reduce size |
| `margin` | Equity vs required margin | Short → deposit more |

**If everything is `OK`, proceed. If anything is `!!`, do not force it.** The bot will refuse
entry on the same conditions, so this is just a preview.

Apply leverage once:

```bash
python3 preflight.py --auto --key-file wallet.key --notional 1000 --fix
```

---

## 6. Dry run

No money moves. Watch it for 15–30 minutes.

```bash
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --poll 30
```

```
[DRY] notional $1,000/leg  lev 3x  addr -
       entry cost ~18.0 bps (4 taker fills)

scanning pairs...
  15 dual-listed assets, scoring pairs...
selected UNITREE: LONG para:UNITREE / SHORT xyz:UNITREE  (+131% APR, persist 77%, cap $1,831,297)
       LONG  para:UNITREE (id=200019, sz=2, max 10x)
       SHORT xyz:UNITREE (id=200104, sz=2, max 10x)
       using 3x isolated

[out] carry=+1.491b/hr (+131% APR) persist=77% sd=1.98 basis=-12.2b worst24h=-9.8b cap=$1,831,582 payback=12.1h
       >>> ENTER  (apr +131%, persist 77%, payback 12.1h)
[IN ] carry=+1.491b/hr (+131% APR) persist=77% sd=1.98 basis=-13.0b worst24h=-9.8b cap=$1,830,749 payback=12.1h
```

What to check before going live:

- `carry` stable, not bouncing between positive and negative
- `persist` stays above your minimum
- `basis` small and stable (single digits to low tens of bps)
- `cap` far above your notional

If carry oscillates wildly, the regime is unstable. Wait.

---

## 7. Go live

```bash
HL_PRIVATE_KEY=0x... python3 arb_bot.py --auto --notional 1000 --min-apr 80 \
    --live --i-used-referral
```

Or with a key file:

```bash
python3 arb_bot.py --auto --notional 1000 --min-apr 80 \
    --live --i-used-referral --key-file wallet.key
```

`--i-used-referral` confirms the account was created through
`https://entropy.io/?r=langris`. Without it the bot refuses to start:

```
  BLOCKED: --live requires --i-used-referral
  Open https://entropy.io/?r=langris first, create the account there,
  then re-run with --i-used-referral
```

On entry the bot:

1. Sets leverage on both legs (isolated)
2. Sends both legs as marketable IOC so they fill together
3. **Verifies both fills.** If one leg lands and the other does not, it unwinds both —
   a single leg is a naked directional bet, not an arbitrage
4. Reports the delta between legs

```
       >>> ENTER  (apr +131%, persist 77%, payback 12.1h)
       long : {"status":"ok",...}
       short: {"status":"ok",...}
       filled long +12.96 short -12.94 delta +0.02
```

`delta` near zero means the position is properly neutral.

### Run it persistently

```bash
tmux new -s arb
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --live --i-used-referral --key-file wallet.key
# Ctrl+B then D to detach
```

Or as a systemd service:

```bash
sudo tee /etc/systemd/system/arb-bot.service > /dev/null <<'EOF'
[Unit]
Description=Hyperliquid funding carry bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/pentest/entropy
ExecStart=/usr/bin/python3 -u arb_bot.py --auto --notional 1000 --min-apr 80 \
    --live --i-used-referral --key-file wallet.key
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now arb-bot
sudo journalctl -u arb-bot -f
```

The bot recovers state on restart — if a position already exists it will **not** open a second one.
It switches to monitor-and-exit mode:

```
RECOVER: existing position long=+12.96 short=-12.94 delta=+0.02
         will NOT open a new one. Monitoring + exit only.
```

---

## 8. Monitor

```bash
python3 status.py --key-file wallet.key
```

```
wallet 0x4e37...

account value   $1,004.55
withdrawable    $612.30

LONG  para:UNITREE   size +12.96  entry 77.1530  uPnL $+0.42  lev 3x isolated
SHORT xyz:UNITREE    size -12.94  entry 77.2420  uPnL $-0.31  lev 3x isolated

carry sekarang  +1.491 bps/hr  =  +131% APR
total uPnL      $+0.11
```

**The two uPnL values must offset each other.** One positive, one negative, summing near zero.
That is the proof the hedge is working. The profit shows up in `account value` growing from funding,
not from uPnL.

---

## 9. Exit

The bot exits automatically when:

- the differential goes ≤ 0 (carry flipped)
- APR drops below `--exit-apr` (default 25%)
- basis exceeds `--basis-exit` (default 120 bps)
- `--max-hours` is reached (default 168 = 7 days)
- realized drawdown exceeds `--max-dd-usd` (if set)

Manual exit — stop the bot, then close both legs:

```bash
python3 - <<'EOF'
from hl_client import HL
hl = HL(private_key=open('wallet.key').read().strip())
for coin in ('para:UNITREE', 'xyz:UNITREE'):
    p = hl.position(coin)
    if p:
        szi = float(p['szi'])
        aid = None
        dex = coin.split(':')[0]
        meta = hl.meta(dex)
        for i, a in enumerate(meta['universe']):
            if a['name'] == coin:
                aid = 200000 + i
        r = hl.order(aid, szi < 0, 0, abs(szi), tif='Ioc', reduce_only=True)
        print(coin, szi, r)
EOF
```

Or close from the UI at [app.hyperliquid.xyz](https://app.hyperliquid.xyz).

---

## 10. Risks — read this, do not skip it

**1. The regime flips.**
Over the last 30 days, average UNITREE funding was **positive** — the opposite sign from now.
The current 131% carry is a regime, not a constant. That is exactly why the bot exits on sign flip
and why preflight checks 168h persistence. Do not hold through a flip hoping it comes back.

**2. Liquidation.**
At 3x, a 33% adverse move wipes the margin on one leg. Because the two legs offset, price moves do
not liquidate both at once — one profits while the other loses. The danger is one leg liquidating
first, leaving a naked position. Mitigation: low leverage (3x, not 10x) and margin buffer.

**3. Leg risk.**
The bot fires both legs and verifies. But if your connection dies **between** the two orders, you
can be left with one leg. Run on a stable VPS. This is the single most likely way to get hurt.

**4. Capacity is finite.**
`para:UNITREE` open interest is ~$1.83M. If you push size, your own position moves the funding you
are harvesting. The 25%-of-OI cap exists for this reason.

**5. Entry cost.**
Four taker fills ≈ 18 bps. At 1.49 bps/hr that is a 12-hour payback. **This is a multi-day position,
not a scalp.** Opening and closing every hour guarantees a loss.

**6. It is not free money.**
131% APR is on **notional**, not on the margin you post. $1,000 notional at 3x uses ~$667 of margin
across both legs, so the return on capital is roughly 131% × (1000/667) ≈ 196% APR — but the absolute
number is about **$3.60/day per $1,000 notional**. Real, modest, and it scales only as far as
capacity allows.

---

## 11. FAQ

**Why not market making? The spreads look wide.**
Measured. The wide-spread markets (`xyz:HO` at 54 bps, `mkts:USBOND` at 24 bps) printed **zero
trades in 90 seconds**. No flow means no fills. The markets that do have flow (`QNT`, `HYUNDAI`) run
73–93% one-sided sell flow, and the mid drifts −4 to −9 bps following that flow. A 15 bps spread is
eaten by drift plus the 3 bps of fees. Full data in `FINDINGS.md`.

**Can I find a bigger carry?**
```bash
python3 scanner.py --min-apr 50
```
It scores every dual-listed pair on 168h persistence, stability, basis and capacity. Anything with
high snapshot APR but low persistence is a spike — AAOI showed +904% snapshot and **−10%** over 7 days.

**How long do I hold?**
Until the carry collapses. Minimum ~12 hours to clear entry cost. Ideally days.

**Can I add capital?**
Check capacity first: `python3 preflight.py --auto --key-file wallet.key --notional 5000`.
If notional exceeds 25% of open interest, do not.

**What if the bot dies?**
Positions live on the exchange, not in the bot. Run `status.py` to see them. Restarting the bot is
safe — it detects the existing position and will not double up.

**Can I run several pairs at once?**
Yes, one bot process per pair with a different `--long/--short` and its own notional. Keep total
notional across pairs under your risk budget.

---

## 12. Command reference

```bash
# readiness check (auto-picks the best pair)
python3 preflight.py --auto --key-file wallet.key --notional 1000

# apply leverage once
python3 preflight.py --auto --key-file wallet.key --notional 1000 --fix

# dry run
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --poll 30

# live
python3 arb_bot.py --auto --notional 1000 --min-apr 80 \
    --live --i-used-referral --key-file wallet.key

# live with builder fee (see REFERRAL.md)
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --live --i-used-referral \
    --key-file wallet.key --builder 0xYOU --builder-fee 10

# monitor
python3 status.py --key-file wallet.key

# scan for better pairs
python3 scanner.py --min-apr 50
```

---

## 13. Files

| file | purpose |
|---|---|
| `preflight.py` | 10-point readiness check. **Never sends an order.** |
| `arb_bot.py` | The bot. Dry by default. Auto-selects pairs. |
| `status.py` | Position, uPnL, carry. Read-only. |
| `scanner.py` | Ranks every dual-listed pair on 168h persistence. |
| `setup_builder.py` | Builder fee + referral code setup (monetization). |
| `funding_arb.py` | Simpler snapshot scanner (kept for reference). |
| `hl_client.py` | Library: signing, orders, positions, builder fees. |
| `scan_markets.py` | 340-market MM scan (result: not viable, see FINDINGS). |
| `live_sample.py` | Real-time flow sampler. |
| `mm_anth.py` | Market maker. Works, but not deployed — see FINDINGS §5. |
| `FINDINGS.md` | Full analysis with every raw number. |
| `REFERRAL.md` | How to monetize and promote this. |
