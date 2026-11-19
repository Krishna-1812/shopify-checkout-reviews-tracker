"""The label set, written once and shared by the rules tagger, the AI prompt,
the hand-labelling page and the evaluation.

The themes were chosen after reading all 438 reviews collected on the first
run: most merchant reviews of these apps are about the support team, the
onboarding / KYC process, or whether the checkout actually works.
"""
from __future__ import annotations

SENTIMENTS = ("positive", "neutral", "negative")

SENTIMENT_RULES = """\
positive - the merchant is satisfied overall (praise, thanks, recommends it).
neutral  - mixed (clear praise AND a clear complaint of similar weight), a plain
           question/request with no judgement, or no opinion at all.
negative - the merchant is dissatisfied overall (complaint, warning, regret).
The written text decides; the star rating is only a tie-breaker. A review with
no text takes its sentiment from the stars: 4-5 positive, 3 neutral, 1-2 negative."""

# key -> (label shown on the page, definition used by the AI and the humans)
THEMES: dict[str, tuple[str, str]] = {
    "support": (
        "Support",
        "the support / service / account team: responsiveness, helpfulness, "
        "named agents being thanked, tickets, waiting for replies.",
    ),
    "setup": (
        "Setup & onboarding",
        "installing, integrating, configuring, KYC / activation / approval, "
        "onboarding calls, uninstalling.",
    ),
    "reliability": (
        "Bugs & reliability",
        "the app or checkout not working, errors, blank screens, bugs, "
        "payments failing, the app disabling itself, data mismatches.",
    ),
    "conversion": (
        "Checkout & conversion",
        "checkout speed and experience, address autofill, 1-click, "
        "conversion rate, cart abandonment, sales uplift, prepaid share.",
    ),
    "cod_rto": (
        "COD & RTO",
        "cash on delivery controls, COD fees, COD verification / OTP, "
        "fake orders, RTO (return to origin) reduction.",
    ),
    "pricing": (
        "Pricing & billing",
        "fees, commission, charges, billing, unexpected deductions, refunds "
        "of charges, value for money.",
    ),
    "features": (
        "Features",
        "the feature set itself: praise for specific features or requests "
        "for missing ones (EMI, API options, customisation) that are not COD.",
    ),
    "general": (
        "General",
        "generic praise or complaint with no specific topic ('nice app', "
        "'worst app'), or no text at all.",
    ),
}
THEME_KEYS = tuple(THEMES)

THEME_RULES = """\
Pick the ONE theme the review is mainly about: the topic that caused the rating
or that takes up most of the text.
- Thanking a named person or "the team" for help -> support, unless the text is
  mainly describing the setup process itself.
- A blocked onboarding/KYC is setup, even if support was slow; choose support
  only when the complaint is mainly about the team's responsiveness.
- "Not working" / errors -> reliability, even if support is also mentioned
  briefly.
- Use general only when no specific topic is mentioned."""


def rating_prior(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    return "negative"
