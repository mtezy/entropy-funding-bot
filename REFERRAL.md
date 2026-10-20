# Monetizing & Promoting the Bot

> ## ⚠ REFERRAL IS MANDATORY
>
> Every user must create their account through:
>
> # https://entropy.io/?r=langris
>
> The code is captured into `localStorage["entropy.referral-code.v1"]` by the SPA and
> bound to the account at signup via `POST /api/referrals/pending`. It **cannot** be
> added afterwards — there is no retroactive attach. A user who skips the link is
> permanently unattributed.
>
> The bot enforces this: `--live` refuses to start without `--i-used-referral`.

Three revenue rails on Hyperliquid + entropy. They stack.

| | Entropy referral | Builder fee | Hyperliquid referral |
|---|---|---|---|
| Link / mechanism | `?r=langris` at signup | `builder:{b,f}` per order | `setReferrer` action |
| How it pays | % of referred users' fees | Per fill, every order | 10% of referred fees |
| Readable by API | **No** (entropy-side) | Yes | Yes (`referredBy`) |
| Requirement | none | user approves once | **$10,000 volume** |
| Enforceable by bot | ack flag | built in | hard API check |

Live API confirms the state of each:

```
GET /info {"type":"maxBuilderFee","user":U,"builder":B}   -> 0      (not approved)
GET /info {"type":"approvedBuilders","user":U}            -> []
GET /info {"type":"referral","user":U}
  -> {"referredBy": null, "cumVlm": "0.0", "builderRewards": "0.0",
      "referrerState": {"stage": "needToTrade", "data": {"required": "10000.0"}}}
```

---

## Rail 0 — Entropy referral (mandatory, the one you asked for)

**The link:**

```
https://entropy.io/?r=langris
```

**How it actually works** (reverse-engineered from the production bundle):

1. `https://entropy.io/?r=langris` → 301 → `/trade?r=langris`
2. `assets/main-BZ0h1BE2.js` checks `new URLSearchParams(location.search).has("r")`.
   If present, it loads the **app** instead of the landing page and stores the code
   under `localStorage["entropy.referral-code.v1"]`.
3. On signup / first trade, `assets/pending-referral-*.js` posts:

```js
POST /api/referrals/pending
{ "code": "langris", "entry": "link" }
```

4. The backend returns `pending`, then confirms on a poll loop (up to 13 × 1.5 s).
   Errors surface as `REFERRAL_WALLET_ROLE_NOT_ALLOWED` or `onboarding.referral_invalid`.

**The consequence that matters:** the code is bound **at account creation**. There is no
endpoint to attach a referral to an existing account. So the link is the whole game —
if a user deposits first and clicks the link later, they are not attributed.

### Enforcing it

The bot cannot read the entropy referral (it is an entropy account attribute, not on-chain).
What it does instead:

```bash
# --live is BLOCKED until the operator confirms
python3 arb_bot.py --auto --notional 1000 --live --key-file wallet.key
# -> BLOCKED: --live requires --i-used-referral

# after the user has signed up through the link
python3 arb_bot.py --auto --notional 1000 --live --i-used-referral --key-file wallet.key
```

The banner prints on **every** dry run, preflight and status call, so a user cannot reach
the live step without seeing the link.

If you later register a Hyperliquid referral code too, set `HL_REFERRAL_CODE` in
`config.py` and the bot will additionally verify `referredBy` against the API and refuse
to run on a mismatch — that one is a hard cryptographic-level check, not an honour system.

---

## Rail 1 — Builder fee (the one that actually pays per trade)

Every order action can carry:

```json
"builder": {"b": "0xYOUR_ADDRESS", "f": 10}
```

`f` is in **tenths of a basis point**. `f: 10` = 1 bp. `f: 25` = 2.5 bps. Cap is 10 bps (f = 100)
for perps.

The fee is charged on fills **on top of** protocol fees and goes straight to `0xYOUR_ADDRESS`.

### Step 1 — set your builder address

Pick an address you control. It does not need to trade; it only receives fees.

### Step 2 — users approve you (once per wallet)

Each user signs one `approveBuilderFee` action:

```bash
python3 setup_builder.py --builder 0xYOUR_ADDRESS --fee 10 --print-approval
```

```json
{
 "type": "approveBuilderFee",
 "maxFeeRate": "0.1000%",
 "builder": "0xYOUR_ADDRESS"
}
```

For your own test wallet:

```bash
python3 setup_builder.py --key-file wallet.key \
    --approve --builder 0xYOUR_ADDRESS --fee 10 --live
```

Verify:

```bash
python3 setup_builder.py --key-file wallet.key \
    --builder 0xYOUR_ADDRESS --status
```

```
builder 0xYOUR_ADDRESS
  max approved  10 tenths-bps (1.0 bps)
approved builders: 1
```

### Step 3 — run the bot with the builder fee attached

```bash
python3 arb_bot.py --auto --notional 1000 --min-apr 80 --live --i-used-referral \
    --key-file wallet.key --builder 0xYOUR_ADDRESS --builder-fee 10
```

The bot adds `"builder": {"b": ..., "f": 10}` to every order action and **prices the fee into its
own entry math** — `entry cost = 4 × (taker + builder)` bps. It will refuse to enter if the carry no
longer covers the extra cost. That is deliberate: a bot that ignores its own fee will slowly bleed
its users dry and they will leave.

### Fee math — what you actually earn

```
per round trip (open + close, 2 legs) = 4 fills
fee per round trip = 4 × notional × (f / 10000)
```

| `f` | rate | $1k notional | $5k notional | $20k notional |
|---|---|---|---|---|
| 10 | 1.0 bp | $0.40 | $2.00 | $8.00 |
| 25 | 2.5 bp | $1.00 | $5.00 | $20.00 |
| 50 | 5.0 bp | $2.00 | $10.00 | $40.00 |

Positions here are held **days**, not minutes, so round trips are infrequent — that is the honest
catch. A user holding a week does one round trip a week. Your revenue scales with **number of users
× notional**, not with trade frequency.

### Choosing `f`

The carry is ~1.49 bps/hr ≈ 35.8 bps/day. Entry cost at `f = 0` is 18 bps.

| `f` | entry cost | payback | verdict |
|---|---|---|---|
| 0 | 18.0 bps | 12.1 h | best for the user, no revenue |
| 10 | 22.0 bps | 14.8 h | **sweet spot** — 1 bp, barely dents the edge |
| 25 | 28.0 bps | 18.8 h | still fine, 2.5× the revenue |
| 50 | 38.0 bps | 25.5 h | aggressive; users feel it |
| 100 | 58.0 bps | 38.9 h | max cap; kills most of the edge |

**Recommendation: `f = 10` to launch, `f = 25` once you have retention.** At 1 bp the user barely
notices and your revenue is real. At 10 bps the strategy stops being worth running and you lose the
user — which costs you far more than the extra 9 bps.

---

## Rail 2 — Hyperliquid referral code

Two things happen with a Hyperliquid referral code:

- The **referred user** gets a 4% discount on their own fees
- **You** earn 10% of the fees they pay

### Getting a code

You need **$10,000 cumulative volume** first. Check progress:

```bash
python3 setup_builder.py --key-file wallet.key --status
```

```
referrer stage  needToTrade
  -> need $10,000 volume, have $0 ($10,000 to go)
```

Once you hit it, `stage` becomes `ready` and you can register:

```bash
python3 setup_builder.py --key-file wallet.key \
    --register-code YOURCODE --live
```

### Applying someone's code

```bash
python3 setup_builder.py --key-file wallet.key --set-referrer CODE --live
```

Note: the 4% discount applies to the **referred user's** fees, and your 10% is a share of those fees.
It is not a fee on top — it is a rebate split. So for a bot, **builder fee is the primary rail** and
referral is a secondary/community one.

---

## How the rails interact

```
user signs up:     https://entropy.io/?r=langris        -> entropy referral bound
user approves:     approveBuilderFee                     -> builder fee unlocked
user trades:       protocol fee + builder fee
                   you receive: builder fee (full)
                                + entropy referral share
                                + 10% of HL protocol fee (if HL code applied)
```

They do not conflict. Best configuration for distribution:

```
1. User opens https://entropy.io/?r=langris  ->  you get attribution
2. User approves your builder fee            ->  you earn 1 bp per fill
3. You ship the bot with both baked in
```

---

## Promoting it — what to actually say

The bot's credibility comes from numbers you can show, not claims. Lead with these:

**The measured edge**
> UNITREE funding differential, 168 hours live: +1.491 bps/hr average, 77% of hours positive,
> 94% of 24-hour windows profitable. Entry cost 18 bps, payback 12 hours.

**The honest downside**
> Worst 24-hour window over the last week: −9.8 bps. With entry cost, a bad short hold is about
> −28 bps. It is not risk-free and anyone who tells you otherwise is selling something.

**Why it is delta-neutral**
> Long one dex, short the other, same asset. Price direction cancels. You collect the funding
> differential, not a prediction.

**Why the guards matter**
> Five ways this loses, five hard stops: funding flip, basis blowout, one-leg fill, liquidation,
> and mistaking a spike for a regime. The bot refuses entry unless 168h persistence clears 75%.

### What NOT to claim

Do not say "risk-free", "guaranteed", "no loss", or "APR" without saying **on notional**. The first
person who loses 28 bps on a bad hold will screenshot your claim and that is the end of your
distribution. The honest framing — measured distribution plus explicit downside — is both more
credible and more durable.

### Positioning

The strongest angle is not the APR, it is **the scanner**. Anyone can post an APR. What you have is
a tool that rejects 13 of 15 dual-listed pairs because their carry is a spike. Show that:

```bash
python3 scanner.py --min-apr 50
```

```
ASSET       LONG            SHORT              NET APR  PERSIST   STAB    BASIS    CAPACITY
UNITREE     para:UNITREE    xyz:UNITREE          +131%      77%   0.00   -12.4b   1,829,120
SMH         xyz:SMH         para:SMH             +132%      40%   0.00   +18.9b      48,920
```

Two pairs pass. SMH looks identical on APR and is unusable — 40% persistence, $49k capacity, and a
basis that swings 100 bps in seconds. **That filter is the product.** Lead with it.

---

## Distribution checklist

- [ ] `config.py` has your `ENTROPY_REFERRAL_CODE` (currently `langris`)
- [ ] Referral link posted **before** any setup instructions — signup order matters
- [ ] `--live` tested to block without `--i-used-referral`
- [ ] Builder address created and controlled
- [ ] `approveBuilderFee` flow tested on your own wallet
- [ ] Bot runs with `--builder` + `--builder-fee` and still passes preflight
- [ ] Entry cost shown to users includes the builder fee
- [ ] `scanner.py` output in your pitch, showing rejected pairs
- [ ] Downside numbers stated (worst 24h, payback hours)
- [ ] Support path for "bot died with a position open" → `status.py`, restart is safe
- [ ] HL referral code registered once $10k volume is reached
