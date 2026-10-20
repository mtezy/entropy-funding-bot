"""
config.py — single source of truth for the referral gate.

Two SEPARATE referral systems are in play. Do not confuse them:

  1. ENTROPY referral  (entropy.io)
     Code lives in the URL:  https://entropy.io/?r=langris
     The SPA stores it in localStorage key `entropy.referral-code.v1` and binds it
     to the account at signup / first trade via POST /api/referrals/pending.
     It is an ENTROPY account attribute — it is NOT readable through the
     Hyperliquid API. The only way a user gets it is by opening the link BEFORE
     creating their account.

  2. HYPERLIQUID referral  (the venue underneath)
     A separate code, applied with the `setReferrer` action. This one IS readable
     via the API: GET /info {"type":"referral"} -> "referredBy".

The bot can hard-enforce #2 (it can read it). It cannot read #1, so for #1 the gate
is: the link is printed everywhere, and --live refuses to start unless the operator
has confirmed the user came through it.

Override at runtime with env vars:
    ENTROPY_REFERRAL_CODE=langris
    HL_REFERRAL_CODE=langris
"""

# ---- the referral everything must go through ---------------------------------
ENTROPY_REFERRAL_CODE = "langris"
ENTROPY_REFERRAL_URL = f"https://entropy.io/?r={ENTROPY_REFERRAL_CODE}"

# Hyperliquid-side code, if you also registered one (needs $10k volume first).
# Leave as None to skip the HL referral check.
HL_REFERRAL_CODE = None

# Require the HL referral check before --live will run.
REQUIRE_HL_REFERRAL = False

# Require the operator to acknowledge the entropy link before --live will run.
REQUIRE_ENTROPY_ACK = True


def _box(lines, width=74):
    out = ["┌" + "─" * width + "┐"]
    for ln in lines:
        out.append("│" + ln.ljust(width)[:width] + "│")
    out.append("└" + "─" * width + "┘")
    return "\n".join(out)


BANNER = "\n" + _box([
    "  REFERRAL REQUIRED",
    "",
    "  This bot is distributed under a referral. Every user must sign up",
    "  through this link BEFORE creating an account:",
    "",
    "      " + ENTROPY_REFERRAL_URL,
    "",
    "  The code is stored in localStorage as `entropy.referral-code.v1`",
    "  and is bound to the account at signup. It cannot be added afterwards.",
]) + "\n"


def print_banner():
    print(BANNER)


def require_ack(args):
    """
    Gate for --live. Returns True if the operator may proceed.

    Checks, in order:
      1. entropy acknowledgement flag (--i-used-referral)
      2. HL referral code on the wallet (if HL_REFERRAL_CODE is set)
    """
    if REQUIRE_ENTROPY_ACK and not getattr(args, "i_used_referral", False):
        print_banner()
        print("  BLOCKED: --live requires --i-used-referral")
        print(f"  Open {ENTROPY_REFERRAL_URL} first, create the account there,")
        print("  then re-run with --i-used-referral\n")
        return False

    if HL_REFERRAL_CODE and REQUIRE_HL_REFERRAL:
        from hl_client import HL
        try:
            ref = HL(private_key=getattr(args, "_key", None)).referral()
            got = ref.get("referredBy")
            if got != HL_REFERRAL_CODE:
                print(f"  BLOCKED: wallet referredBy={got!r}, expected {HL_REFERRAL_CODE!r}")
                print(f"  Apply the code: python3 setup_builder.py --set-referrer "
                      f"{HL_REFERRAL_CODE} --live\n")
                return False
            print(f"  referral check: referredBy={got} OK")
        except Exception as e:
            print(f"  referral check failed ({e}) — blocking to be safe")
            return False

    return True
