"""
fees.py — the real fee model. Everything else in this repo was using the main-dex
rate, which understates HIP-3 costs by 2x and ignores growth mode entirely.

Verified against docs.entropy.io/equity-perp-mechanics/fees.md and live
POST /info {"type":"meta","dex":...} which exposes per-market `growthMode` and
`deployerFeeScale`.

THE STACK
    base perp rate       maker 1.50 bps   taker 4.50 bps
    HIP-3 multiplier     x2.0             -> maker 3.00  taker 9.00
    growth mode          x0.1             -> maker 0.30  taker 0.90
    deployerFeeScale     x per market     (0.5 on one market, 1.0 everywhere else)

THE REBATE (this is the part that changes everything)
    Protocol fee splits 50/50: Hyperliquid / Entropy.
    Entropy's Self Rebate is a % of ENTROPY'S HALF, not of the whole fee.

        Tier 1 Trader      60%    -> net cost = fee - 0.5*fee*0.60 = 0.70*fee
        Tier 2 OG         120%    -> net cost = fee - 0.5*fee*1.20 = 0.40*fee
        Tier 3 Preferred  160%    -> net cost = 0.20*fee      ($10M/30d volume)
        Tier 4 Partner    200%    -> net cost = 0.00*fee      ($50M/30d volume)

    Tier 1 and Tier 2 have NO volume threshold. Tier 3/4 do.

    Self Rebate and Referred-User Benefit do NOT stack — you get whichever is higher.

THE CONSEQUENCE FOR VOLUME
    Total volume you can push = capital / net_fee_rate.
    Leverage and trade size do not change it. Only the net rate does.

        growth mode maker, OG tier   0.120 bps   ->  $83,333 volume per $1
        growth mode taker, OG tier   0.360 bps   ->  $27,778 per $1
        growth mode maker, Tier 1    0.210 bps   ->  $47,619 per $1
        HIP-3 normal taker, Tier 1   6.300 bps   ->  $1,587 per $1
"""

BASE_MAKER_BPS = 1.50
BASE_TAKER_BPS = 4.50

HIP3_MULT = 2.0
GROWTH_MULT = 0.1

ENTROPY_FEE_SHARE = 0.5          # protocol fee splits 50/50 with Hyperliquid

TIERS = {
    "tier1": 0.60,   # Trader    — no threshold
    "og":    1.20,   # Tier 2    — no threshold
    "pref":  1.60,   # Tier 3    — $10M rolling 30d
    "part":  2.00,   # Tier 4    — $50M rolling 30d
}


def gross_bps(maker: bool, dex: str, growth_mode: bool, deployer_fee_scale: float = 1.0) -> float:
    """Fee actually charged per fill, before any rebate."""
    base = BASE_MAKER_BPS if maker else BASE_TAKER_BPS
    mult = HIP3_MULT if dex else 1.0
    gm = GROWTH_MULT if growth_mode else 1.0
    return base * mult * gm * deployer_fee_scale


def net_bps(maker: bool, dex: str, growth_mode: bool,
            deployer_fee_scale: float = 1.0, tier: str = "tier1") -> float:
    """What the fill actually costs you after the Self Rebate."""
    g = gross_bps(maker, dex, growth_mode, deployer_fee_scale)
    rebate = TIERS.get(tier, 0.60)
    return g - g * ENTROPY_FEE_SHARE * rebate


def round_trip_bps(maker: bool, dex: str, growth_mode: bool,
                   deployer_fee_scale: float = 1.0, tier: str = "tier1",
                   legs: int = 2, builder_bps: float = 0.0) -> float:
    """Open + close, N legs. The number that decides whether a trade is worth it."""
    per_fill = net_bps(maker, dex, growth_mode, deployer_fee_scale, tier) + builder_bps
    return per_fill * 2 * legs


def volume_per_dollar(maker: bool, dex: str, growth_mode: bool,
                      deployer_fee_scale: float = 1.0, tier: str = "tier1") -> float:
    """How much volume $1 of capital can push before the fees eat it all."""
    n = net_bps(maker, dex, growth_mode, deployer_fee_scale, tier) / 1e4
    return 1.0 / n if n > 0 else float("inf")


def capital_for_volume(target_usd: float, maker: bool, dex: str, growth_mode: bool,
                       deployer_fee_scale: float = 1.0, tier: str = "tier1",
                       buffer: float = 2.0) -> float:
    """Capital needed to push target_usd of volume. buffer=2 keeps 50% equity intact."""
    n = net_bps(maker, dex, growth_mode, deployer_fee_scale, tier) / 1e4
    return target_usd * n * buffer


if __name__ == "__main__":
    print(f"{'setup':30} {'gross':>7} {'net T1':>8} {'net OG':>8} {'vol/$ T1':>10} {'vol/$ OG':>10}")
    for label, mk, dex, gm in (
        ("main dex maker",           True,  "",     False),
        ("main dex taker",           False, "",     False),
        ("HIP-3 normal maker",       True,  "para", False),
        ("HIP-3 normal taker",       False, "para", False),
        ("growth mode maker",        True,  "xyz",  True),
        ("growth mode taker",        False, "xyz",  True),
    ):
        g = gross_bps(mk, dex, gm)
        print(f"{label:30} {g:7.2f} {net_bps(mk,dex,gm,tier='tier1'):8.3f} "
              f"{net_bps(mk,dex,gm,tier='og'):8.3f} "
              f"{volume_per_dollar(mk,dex,gm,tier='tier1'):10,.0f} "
              f"{volume_per_dollar(mk,dex,gm,tier='og'):10,.0f}")
