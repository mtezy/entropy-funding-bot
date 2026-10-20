# Volume Economics — Minimum Capital, Maximum Volume

How much volume can $15 actually push? The answer is not "no" — but it depends entirely
on three multipliers most people never look at.

---

## The one equation

```
max volume = capital / net_fee_rate
```

Leverage does **not** appear. Trade size does **not** appear. Double the leverage and you
do half the round trips at twice the size — identical volume, identical total fee.

Proof: with capital `C`, leverage `L`, notional per leg `N = C*L`:

```
fee per round trip   = N * 4 * r        (2 legs, open + close)
round trips possible = C / (N * 4 * r)
total volume         = round_trips * N * 4 = C / r
```

So the only thing that buys volume per dollar is a **lower net fee rate**. There are exactly
three ways to get one.

---

## Multiplier 1 — growth mode (x0.1)

`POST /info {"type":"meta","dex":...}` exposes per-market `growthMode` and
`deployerFeeScale`. **151 of 340 markets have growth mode enabled.**

```
normal HIP-3   maker 3.00 bps   taker 9.00 bps
growth mode    maker 0.30 bps   taker 0.90 bps     <- 10x cheaper
main dex       maker 1.50 bps   taker 4.50 bps
```

That is a 10x improvement on the single number that matters. Nothing else comes close.

## Multiplier 2 — self-rebate tier (up to x0.4)

The protocol fee splits 50/50 between Hyperliquid and Entropy. Entropy's Self Rebate is a
percentage of **Entropy's half**, not of the whole fee.

```
Tier 1  Trader       60%   -> net cost = 0.70 x fee    (no threshold)
Tier 2  OG          120%   -> net cost = 0.40 x fee    (no threshold)
Tier 3  Preferred   160%   -> net cost = 0.20 x fee    ($10M rolling 30d)
Tier 4  Partner     200%   -> net cost = 0.00 x fee    ($50M rolling 30d)
```

Tier 2 has **no volume requirement** — only the "early bird period" label. If you can get it,
every fee drops by another 43%.

## Multiplier 3 — maker vs taker (x0.5)

Obvious, but it compounds with the other two. Maker fees are half of taker on every tier.

---

## The result

Net fee rate and volume capacity, per $1 of capital:

```
setup                          net bps    vol per $1
main dex maker                   1.050         9,524
main dex taker                   3.150         3,175
HIP-3 normal maker               2.100         4,762
HIP-3 normal taker               6.300         1,587
growth mode maker                0.210        47,619
growth mode taker                0.630        15,873

growth mode maker, OG tier       0.120        83,333
growth mode taker, OG tier       0.360        27,778
```

### Capital needed for $1,000,000 of volume

```
setup                        tier    fee spend   with 2x buffer
growth mode maker            OG      $12         $24
growth mode maker            Tier 1  $21         $42
growth mode taker            OG      $36         $72
growth mode taker            Tier 1  $63         $126
HIP-3 normal maker           Tier 1  $210        $420
HIP-3 normal taker           Tier 1  $630        $1,260
```

### So: $15

```
Tier 1, growth mode maker   ->  $714,000 volume
OG tier, growth mode maker  ->  $1,250,000 volume
Tier 1, growth mode taker   ->  $238,000 volume
Tier 1, normal HIP-3 taker  ->  $23,810 volume
```

**$15 can generate $1M — at OG tier, with maker fills, in growth-mode markets.** At Tier 1
it tops out around $714k. On a normal HIP-3 market it is $24k.

Check your own numbers:

```bash
python3 volume_planner.py --capital 15 --target 1000000
python3 volume_planner.py --capital 15 --tier og --growth-only
python3 volume_planner.py --capital 50 --target 1000000 --live-flow
```

---

## Where the volume actually happens

Growth mode markets with real flow right now (5-minute sample):

```
market              lev   trades/5m      vol/5m
xyz:SP500            50          10    $155,190
xyz:XYZ100           30          10     $43,710
xyz:SKHX             10          10     $29,850
xyz:SPCX             20          10     $25,747
xyz:AMD              10          10     $23,902
xyz:ASML             10          10     $19,361
xyz:META             20          10     $13,873
xyz:LITE             10          10     $12,798
xyz:AAPL             20          10     $10,092
```

70 of 151 growth-mode markets had flow. The other 81 are dead books — growth mode makes
them cheap to trade, not possible to trade.

---

## The three things that kill this

### 1. Wash trading forfeits everything

entropy docs, verbatim:

> Wash trading, pre-arranged trading, self-referral, and order forms of inauthentic behavior
> will lead to loss of all pending and future rewards.

Being both sides of your own trade is precisely what that covers. You pay the fee, you
generate the volume, and then you lose every reward including the rebate you were counting
on. The fees become a **pure loss** — worse than doing nothing.

Real liquidity provision is different: you rest maker orders, other people fill them, you
capture the spread. That is a legitimate business, and it generates volume as a byproduct.

### 2. Rebates are bi-weekly, fees are immediate

The rebate is paid out bi-weekly with a $5 minimum withdrawal. You pay the **full** fee at
trade time. So your capital has to survive the gross fee drain, not the net one.

```
$15 capital, growth mode taker, Tier 1
gross fee per fill      0.90 bps   (paid now)
net cost per fill       0.63 bps   (0.27 comes back in ~2 weeks)
```

With $15 you can push $166k of volume before the gross fees drain you — not the $238k the
net number implies. Size your buffer against the gross rate.

### 3. You need margin, and fees eat it

Volume requires notional. Notional requires margin. Fees are deducted from that same margin.
At 10x on $15 you have $150 notional and $15 of margin — a 10% adverse move liquidates you
before the volume accumulates.

Use low leverage. The volume equation does not reward leverage; it only adds liquidation risk.

---

## Practical minimums

```
goal                          realistic capital   notes
$10k  (HL referral gate)      $5-10               one afternoon, growth mode
$100k                        $20-30               a few sessions
$1M                          $50-100              OG tier or sustained maker fills
$10M                        $500-1,000            needs consistent flow + Tier 3
```

Below $50 you are trading with essentially no margin buffer — technically possible, one bad
fill from a liquidation.

---

## What this means for the funding bot

Growth mode is not only about volume. It also cut the funding-arb entry cost from 18 bps to
**2.52 bps**, because UNITREE happens to be in growth mode on both dexes:

```
before (assumed main-dex 4.5 bps)   4 x 4.5        = 18.00 bps   payback 12.1 h
after  (growth mode 0.9 bps, T1)    4 x 0.63       =  2.52 bps   payback  1.7 h
```

The strategy is 7x better than first measured, and the same money can now be recycled far
more often. That correction came from reading `growthMode` off the market metadata instead
of trusting `userFees`, which only reports the main-dex rate.
