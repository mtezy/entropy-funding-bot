# Farming Entropy — What Is Actually Farmable

Checked against entropy's docs and the live API on 2026-10-20. Three different things
get called "farming" here. They have wildly different risk.

```
1. FEE REBATE          live, paid in HYPE bi-weekly        <- guaranteed
2. REFERRAL REWARD     live, % of other users' fees        <- needs users, not volume
3. POINTS / AIRDROP    NOT ANNOUNCED                       <- a bet, not a farm
```

---

## 3. The airdrop situation, plainly

**There is no token, no points program, no season, no TGE.** Not "unannounced" in the
marketing sense — the shipped frontend has zero references to `points`, `airdrop`,
`season`, or `leaderboard rewards`, and the docs contain no rewards page at all.

What does exist:

- A **leaderboard** since 2026-09-23, ranked by **PNL**, not volume.
- Community expectation of a retroactive "Season 0" around a speculated Nov–Dec TGE.
- Entropy's own ~11 native markets (`io` dex) that the community assumes would count.

So farming volume for an airdrop is a **bet on an unannounced program**, priced at the
fees you pay. That is not the same as farming something real, and the numbers below
should be read with that in mind.

---

## 1. Fee rebate — the only guaranteed return

Entropy's Self Rebate is a percentage of **Entropy's half** of the protocol fee. The
protocol fee splits 50/50 between Hyperliquid and the deployer.

```
Tier 1  Trader       60%    no threshold
Tier 2  OG          120%    no threshold (grantable, manual review)
Tier 3  Preferred   160%    $10M rolling 30-day volume
Tier 4  Partner     200%    $50M rolling 30-day volume
```

### What each tier costs you

Growth-mode maker fee is **0.30 bps gross**. Entropy's half is 0.15 bps.

```
tier    rebate   net fee    volume per $1 of capital
Tier 1    60%    0.210 bps        47,619
Tier 2   120%    0.120 bps        83,333
Tier 3   160%    0.060 bps       166,667
Tier 4   200%    0.000 bps       unlimited
```

### The number that matters

Farming volume costs your capital. Here is what $18 actually does:

```
                    volume      gross fee    rebate back    net cost
Tier 1  60%        $857,143      $25.71        $7.71         $18.00
Tier 2 120%      $1,500,000      $45.00       $27.00         $18.00
Tier 3 160%      $3,000,000      $90.00       $72.00         $18.00
Tier 4 200%       unlimited          —            —            $0
```

**Every tier below 4 costs you your entire capital.** You are not earning — you are
converting capital into volume at a 43–70% discount, and the only return is whatever a
future airdrop pays.

At **Tier 4 the net fee is zero**. Volume becomes free. That is the only configuration
where volume farming is not a straight loss, and it is why tier is worth more than any
amount of capital you can bring.

### How to get a higher tier

Tier 2 (OG) has no volume threshold in the docs — it is **grantable**. The Partners
program is manual review, invite-oriented, and favours people with content or an
audience around Entropy. Apply through the form linked from the official announcement.

Going 60% → 120% cuts your net fee by 43%. That is worth more than doubling your
capital, and it costs nothing but an application.

---

## 2. Referral reward — needs other people

```
Tier 1   25% of Entropy's half of referred users' fees
Tier 2   50%
Tier 3   75%
Tier 4  100%
```

Plus cash bounties at higher tiers (~$10 per qualified referral who deposits $100+ and
trades, paid after a 30-day hold).

**Critical rule: Self Rebate and Referred-User Benefit do not stack.** An account gets
whichever is higher. So:

- Your own trading: Tier 1 self rebate 60% beats nothing, but a **Tier 4 partner's
  referred-user benefit (up to 100%) beats your own 60%**.
- If you trade under someone else's Tier 4 link, your fees halve relative to being your
  own Tier 1 referrer — but you earn no referral reward.

**Self-referral is explicitly banned.** You cannot be your own referee. The docs name it
alongside wash trading as grounds for forfeiting all pending and future rewards.

So there is a real fork:

```
goal: cheap volume        -> trade under a Tier 4 partner's link (100% referred benefit)
goal: referral income     -> build your own tier, then bring users
goal: both                -> impossible on one account
```

---

## Where to trade — native markets vs everything else

Entropy's own markets (`io` dex) are the ones a retroactive season would count. Live
scan, ranked for maker farming:

```
market        mid      spread bps  ticks  tr/5m   vol/5m     OI$
io:NBIS     163.8950      1.83       3     10     1,555    8,435,359
io:EWY      151.3250      3.30       5     10       415      475,283
io:DRAM      46.5420      0.86       4     10       424    1,409,229
io:PRL        1.9731     21.79      43     10       324    4,881,969
io:ANTH    1955.0500      0.51       1     10       391   43,343,211
io:OAI      1517.8500      0.66       1     10       235    7,060,031
io:SNDK    1331.4000      0.75       1     10       586   19,356,770
io:IONQ      30.3890     18.43      56      0         0      144,569
io:GPRO       1.0436     87.19      91      0         0      259,466
io:TCNT      52.5635     17.69      93      0         0      181,315
```

**Ticks in spread is the number to look at.** A 1-tick spread means you must join the
touch and compete on speed — no edge for a small maker. 4+ ticks means you can quote
inside and take queue priority.

- **Best fill odds:** `io:SNDK` — 1 tick, but the most flow in the group.
- **Best room to improve:** `io:EWY` — 5 ticks, real flow, small OI.
- **`io:PRL`** — 43 ticks and 21.79 bps spread, which looks great until you notice the
  flow is $324 per five minutes. Wide and dead.
- **Dead:** IONQ, GPRO, TCNT, XIAOMI — zero trades in five minutes.

If you only care about the rebate and not a potential season, `xyz` has 10x the flow.
If you care about a season, you have to be on `io` even though it is thinner.

---

## The $18 reality

$18 is not a farm. It is a test.

```
margin at 3x on $15 notional       $10.08 for both legs
fee per round trip (Tier 1)        $0.0006
volume to burn all $18             $857,143 at Tier 1
rebate you would receive           $7.71 in HYPE, two weeks later
net result                         -$10.29
```

You spend $18 to generate $857k of volume and get $7.71 back. The $10.29 difference is
the price of the airdrop bet. If the season pays more than that per unit of volume, you
win; if it never launches, you lost $10.29 and learned the mechanics.

For rebate income to be meaningful rather than symbolic you need roughly $500+ of
capital, or Tier 3+, or both.

---

## Action plan, in order

**1. Confirm your tier.** Open your entropy.io profile. The API does not expose it.
Everything below depends on which tier you are actually on — `farm.py` assumes `tier1`
unless you pass `--tier`.

**2. Apply to the Partners program.** Manual review, needs content or audience. This is
the highest-leverage action available and it is free. 60% → 120% is worth more than
doubling your capital.

**3. Move USDC to the dex you are trading.**
```bash
python3 fund_dex.py --to io --amount 15 --key-file wallet.key
```

**4. Quote, do not sweep.** Post-only maker only. Taker is 3x maker and no tier below
Tier 3 makes that back.

**5. Track the rebate.** Bi-weekly, $5 minimum. Nothing arrives before two weeks of real
volume. Check your profile page, not the API.

**6. Do not self-match.** Entropy's docs, verbatim:

> Wash trading, pre-arranged trading, self-referral, and order forms of inauthentic
> behavior will lead to loss of all pending and future rewards.

Being both sides of your own trade is exactly that. You would pay the fee, generate the
volume, and forfeit the rebate — the worst of all outcomes.

---

## Commands

```bash
# audit: what is farmable, what your tier is worth, which markets to quote
python3 farm.py --key-file wallet.key --capital 18
python3 farm.py --address 0x... --capital 18          # read-only, no key

# where is my money, and move it to the io dex
python3 fund_dex.py --show --key-file wallet.key
python3 fund_dex.py --to io --amount 15 --key-file wallet.key

# quote the best native market
python3 volume_run.py --coin io:SNDK --notional 15 --target 50000 \
    --live --i-used-referral --key-file wallet.key

# if you have a higher tier
python3 volume_run.py --coin io:EWY --notional 15 --target 50000 --tier og \
    --live --i-used-referral --key-file wallet.key
```

---

## Honest summary

- **The rebate is real.** It pays in HYPE, bi-weekly, and it is the only part of this
  that is not a bet.
- **The airdrop is not real yet.** No token, no points, no season. Volume farming for it
  is speculation priced at your fees.
- **Tier beats capital.** At Tier 4 volume is free; at Tier 1 you burn your whole
  balance. An application to the Partners program is worth more than $1,000 of capital.
- **$18 is a test, not a farm.** It buys $857k of volume at Tier 1 and returns $7.71.
- **Native markets are thinner but they are the ones that would count.** xyz has 10x the
  flow if you only want the rebate.
- **Self-matching forfeits everything.** Quote both sides, let other people fill you.
