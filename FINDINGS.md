# entropy.io — Market Scan & Real Edge Assessment

Scanned 2026-10-20 07:45–08:45 UTC. Every number below is from live Hyperliquid API calls.

entropy.io is a **frontend for Hyperliquid builder-deployed perp dexes** — not a separate exchange.
`/api/markets` returns 326 markets across 5 dexes (`io`, `xyz`, `para`, `mkts`, main). Orders go to
`api.hyperliquid.xyz/exchange`, books come from `api.hyperliquid.xyz/info`. Matching, oracle and
settlement are HyperCore — the L1 itself.

---

## 1. Fee schedule (from the API, not the docs)

`POST /info {"type":"userFees"}` → `feeSchedule`:

| 14d volume | maker | taker |
|---|---|---|
| base | 1.50 bps | 4.50 bps |
| $5M | 1.20 | 4.00 |
| $25M | 0.80 | 3.50 |
| $100M | 0.40 | 3.00 |
| $500M | **0.00** | 2.80 |
| $2B | 0.00 | 2.60 |
| $7B | 0.00 | 2.40 |

Staking discount: 0 bps.

**This table decides everything.** A maker round trip costs 2 × maker = 3.0 bps of drag at base
tier. Any market whose spread is under 3 bps cannot be market-made profitably at base tier,
regardless of how good the bot is.

---

## 2. Full scan — 340 live books

`scan_markets.py` pulled `metaAndAssetCtxs` for all 5 dexes, then `l2Book` for every non-delisted
market. Depth = USD resting within 25 bps of mid, taking the **minimum** of the two sides (the real
balanced fill capacity).

```
markets scanned                 340
positive net edge after fees    167   (spread > 2x maker)
net edge > 2bps AND depth>$1k    73
median spread, all books        2.9 bps
```

Top 12 by `net_edge × sqrt(depth)`:

| market | spread | net edge | depth 25bp | OI | vol 24h |
|---|---|---|---|---|---|
| xyz:HO | 47.9 bps | +44.9 | $21,449 | $151k | $1.72M |
| para:SMCI | 47.9 | +44.9 | $10,518 | $148k | $76k |
| xyz:BMNR | 28.0 | +25.0 | $17,273 | $2.22M | $260k |
| para:IREN | 33.1 | +30.1 | $9,522 | $416k | $160k |
| xyz:CVX | 38.9 | +35.9 | $5,737 | $275k | $709k |
| W (main) | 15.4 | +12.4 | $39,023 | $4.13M | $645k |
| para:AAOI | 45.3 | +42.3 | $2,532 | $242k | $25k |
| mkts:SMALL2000 | 12.2 | +9.2 | $44,942 | $344k | $19k |
| mkts:USBOND | 11.8 | +8.8 | $45,129 | $1.11M | $177k |
| xyz:XBI | 35.9 | +32.9 | $2,809 | $89k | $9k |
| xyz:LRCX | 34.0 | +31.0 | $2,212 | $222k | $254k |
| para:GLW | 23.0 | +20.0 | $5,012 | $829k | $34k |

`io:ANTH` — the market originally asked about — has a **0.5 bps spread**. Net edge after fees is
**−2.5 bps**. It is not a market-making market at any tier below $500M volume.

---

## 3. Deep dive — is the wide spread reachable?

Spread alone is meaningless. Two things kill a maker: the spread being only 1–2 ticks wide (you
cannot improve inside it), and no flow (you never get filled).

`deepdive.py` measured tick size, ticks-in-spread and 1h trade count for the top 14:

| market | spread | ticks | net @ touch | net inside | trades/h | med trade |
|---|---|---|---|---|---|---|
| xyz:HO | 53.6 bps | 257 | +50.6 | +50.2 | 10 | $105 |
| xyz:BMNR | 35.9 | 77 | +32.9 | +32.0 | 3 | $107 |
| mkts:USBOND | 24.2 | 193 | +21.2 | +21.0 | 5 | $12 |
| xyz:QNT | 19.5 | 68 | +16.5 | +15.9 | 10 | $275 |
| xyz:PURRDAT | 16.3 | 146 | +13.3 | +13.1 | 10 | $80 |
| io:TCNT | 16.0 | 84 | +13.0 | +12.6 | 2 | $1,187 |
| mkts:SMALL2000 | 16.2 | 44 | +13.2 | +12.5 | 0 | — |
| para:10Y | 7.2 | 37 | +4.2 | +3.8 | 1 | $19 |
| xyz:HYUNDAI | 6.9 | 16 | +3.9 | +3.0 | 10 | $228 |
| **W (main)** | 15.4 | **2** | +12.4 | **−3.0** | 10 | $43 |
| **ACE (main)** | 13.3 | **2** | +10.3 | **−3.0** | 10 | $20 |
| xyz:XLE | 2.3 | 16 | −0.7 | −0.9 | 10 | $25 |
| xyz:GEV | 1.0 | 9 | −2.0 | −2.2 | 10 | $197 |

`W` and `ACE` are the trap: 15 and 13 bps spread looks great, but the spread is only **2 ticks**.
Quoting one tick inside each side leaves −3.0 bps net — you cannot beat the fee. The spread is wide
only because the tick is coarse relative to price, not because there is edge.

---

## 4. Live sampling — who actually has flow

`live_sample.py`, 90 seconds per market, polling the book every 1.5 s and counting new prints:

| market | touch changes | trades/90s | notional | hit bid | hit ask | med touch bid/ask |
|---|---|---|---|---|---|---|
| xyz:HO | 6 | **0** | $0 | 0 | 0 | $22,258 / $6,402 |
| mkts:USBOND | 3 | **0** | $0 | 0 | 0 | $5,000 / $5,000 |
| xyz:QNT | 27 | 14 | $6,586 | 13 | 1 | $1,108 / $205 |
| xyz:HYUNDAI | 29 | 22 | $5,244 | 16 | 6 | $463 / $2,687 |
| xyz:PURRDAT | 13 | 22 | $9,377 | 14 | 8 | $7,789 / $730 |

`xyz:HO` and `mkts:USBOND` — the two widest, deepest books in the entire scan — printed **zero
trades in 90 seconds**. Dead. Wide spread on a market nobody trades is worth nothing.

`xyz:PURRDAT`'s spread collapsed from 16.3 bps to **0.1 bps** during sampling. The scan caught a
momentary thin-book state, not a persistent spread.

---

## 5. Adverse selection — measured, and it is real

Second sampling round, 75 s, mid drift vs flow:

```
xyz:QNT        drift -4.5 bps   tick vol 0.2 bps   (earlier: 93% of flow was SELLS)
xyz:HYUNDAI    drift -9.2 bps   tick vol 1.0 bps   (earlier: 73% of flow was SELLS)
```

Both drifted **down** while flow was overwhelmingly sell-side. That is adverse selection by
definition: you rest a bid, informed sellers hit it, price falls, you are long into a falling market.
The 15 bps spread you captured is eaten by 9 bps of drift plus 3 bps of fees, leaving 3 bps inside
the noise.

The second round also recorded **zero trades in 75 s** on both markets that had 14–22 trades in the
first round. Flow is bursty, not steady. A maker's fill rate is therefore unpredictable — you cannot
size on "trades per hour" and expect it to hold.

### Verdict on maker MM

At base fee tier, on this venue, with these markets: **not a real edge.**

- gross spread capture: 15 bps (best live market)
- minus 2 × maker fee: −3 bps
- minus measured adverse drift: −5 to −9 bps
- net: **+3 to +7 bps, before inventory cost and before the bursts stop**

That is inside the noise, and it requires being top-of-book against whoever is already there with
better latency. Markets where the spread is genuinely wide (`xyz:HO`, `mkts:USBOND`) have no flow.
Markets with flow (`QNT`, `HYUNDAI`) have 15 bps spread but one-sided informed flow.

---

## 6. The real edge: cross-dex funding

32 assets are listed on **two or more dexes**. Each dex runs its own funding market on the same
underlying price. The funding differential between two listings of one asset is a delta-neutral carry.

```
funding < 0  ->  shorts pay longs  ->  LONG leg receives
funding > 0  ->  longs pay shorts  ->  SHORT leg receives
```

`scanner.py` scores every dual-listed pair on 168h persistence, stability, basis and capacity.
Snapshot APR is deliberately **not** the ranking metric.

| asset | long | short | snapshot APR | 168h persistent | persistence | capacity |
|---|---|---|---|---|---|---|
| SMH | xyz:SMH | para:SMH | +4308% | **+132%** | 40% | $48,920 |
| AAOI | xyz:AAOI | para:AAOI | +904% | **−10%** | — | $242,634 |
| **UNITREE** | **para:UNITREE** | **xyz:UNITREE** | +336% | **+131%** | **77%** | **$1,829,120** |
| RDDT | xyz:RDDT | para:RDDT | +155% | — | — | $56,075 |
| IREN | xyz:IREN | para:IREN | +131% | — | — | $415,307 |
| NET | xyz:NET | para:NET | +89% | — | — | $172,120 |
| GLW | para:GLW | xyz:GLW | +48% | — | — | $429,433 |
| SNDK | io:SNDK | xyz:SNDK | +46% | — | — | $19.6M |
| CRWD | xyz:CRWD | para:CRWD | +33% | — | — | $56,379 |
| DRAM | io:DRAM | xyz:DRAM | +15% | — | — | $1.39M |

**Snapshot APR lies.** The 168h check is what matters:

- **SMH**: snapshot 4308% → 168h **+132%**, but only **40% of hours positive**, capacity $37.6k, and
  the basis swung from −87 bps to +15 bps in 12 seconds during testing. Unusable at size.
- **AAOI**: snapshot 904% → 168h **−10%**. The snapshot was a spike on a market that flips sign.
  A snapshot-driven bot would have entered and lost.
- **UNITREE**: snapshot 336% → 168h **+131%**, persistence **77%**, capacity $1.83M, stable basis.

### UNITREE — the measured distribution

168 hours of live funding:

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

Current snapshot at time of writing (higher than the mean — this is what a snapshot scanner would
have shown, and why snapshot ranking is wrong):

```
para:UNITREE   funding -4.00 bps/hr  (longs receive)   OI $1.83M
xyz:UNITREE    funding -0.29 bps/hr  (longs receive)   OI $19.5M
instantaneous carry ~3.7 bps/hr  ->  ~320% APR
168h mean carry      1.49 bps/hr ->  ~131% APR   <- this is what you should plan on
```

At $1,000/leg and the 168h mean: **$0.36/day gross** on ~$667 of margin at 3x.
At 25% of the $1.83M capacity ceiling ($458k): ~$17.6/hour gross.

Return on margin is the number that matters: at 3x, $1,000 notional needs ~$333 margin per leg
(~$667 total), so 131% APR on notional is roughly **196% APR on posted margin** — and that is also
where the liquidation risk lives.

### The catch you must respect

30-day funding history, both legs:

```
para:UNITREE   30d avg +23% APR   median +23%   min -600%   max +1227%   negative 33% of hours
xyz:UNITREE    30d avg  +7% APR   median  +5%   min -187%   max  +368%   negative 25% of hours
```

Over 30 days the average funding is **positive** — the opposite sign from the current regime. The
current carry is a **regime, not a constant**. It has flipped before and will flip again. The bot
exits on sign flip rather than holding through it.

---

## 7. What was built

| file | purpose |
|---|---|
| `hl_client.py` | Hyperliquid client: info, EIP-712 signing (msgpack → keccak → phantom agent), post-only/IOC orders, leverage, isolated margin, **builder fee**, referral. Signature verified by recovering the signer. |
| `scanner.py` | Ranks every dual-listed pair on 168h persistence / stability / basis / capacity. |
| `arb_bot.py` | Delta-neutral funding harvester with five hard loss guards. Dry by default. |
| `preflight.py` | 10-point readiness check. Never sends an order. |
| `status.py` | Positions, uPnL, carry now vs 168h mean. Read-only. |
| `setup_builder.py` | Builder fee approval + referral code (monetization). |
| `scan_markets.py` | 340-market MM scan, ranks by `net_edge × sqrt(depth)`. |
| `deepdive.py` | Tick size, ticks-in-spread, flow stats. |
| `live_sample.py` | Real-time touch sampling — the only way to know a wide book has flow. |
| `funding_arb.py` | Simpler snapshot scanner, kept for reference. |
| `mm_anth.py` | Market maker (microprice, vol-scaled spread, inventory skew, risk stops). Works, but see §5 — not deployed. |

### Verification performed

```
EIP-712 signature    sign -> recover -> matches signer address      PASS
builder field        f:10 present in order action, signature valid  PASS
approveBuilderFee    maxFeeRate "0.1000%" for 10 bps                PASS
asset id resolution  io:ANTH -> 200001, para:UNITREE -> 200019      PASS
scanner              15 dual-listed assets, 2 pass filters          PASS
arb_bot --auto       selects UNITREE, 131% APR, 77% persist, 12.1h  PASS
arb_bot dry          enters/exits on thresholds, no orders sent     PASS
preflight --auto     all 10 checks, correctly blocks on $0 equity   PASS
status --all         reads positions, carry now vs 168h mean        PASS
setup_builder        fee tier, referral stage, builder approval     PASS
```

---

## 8. Honest bottom line

- **Market making on entropy.io: no.** Not at base fee tier. The wide spread is either 2 ticks wide
  (unimprovable) or on a market with zero flow. Where flow exists it is one-sided and informed, and
  the measured drift eats the spread.
- **Volume bot: no.** No points program exists (`points`, `airdrop`, `season`, `leaderboard` all
  return zero hits in the bundle). Wash volume at base tier costs 3 bps per round trip and buys
  nothing. It is a pure loss.
- **Cross-dex funding arb: yes, with conditions.** UNITREE at 131% APR (168h mean), 77% persistence,
  $1.83M capacity, stable basis. 94% of 24-hour windows profitable over the measured week. Worst
  measured 24h window −9.8 bps. It is a regime trade that must be monitored and exited, not a
  set-and-forget yield.
- **What would make maker MM work**: reach $500M 14-day volume for 0.00 bps maker fee. Then the 3 bps
  drag disappears and every 15 bps market becomes +15 bps gross. That is the real unlock, and it
  costs volume you do not have yet.
