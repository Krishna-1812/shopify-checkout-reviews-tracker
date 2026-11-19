"""Deterministic tagger: star rating + keyword rules.

This is the fallback whenever the AI tagger is unavailable (no token, quota
used up, API down), so every review always gets a tag and a run never fails
because of tagging. It is also a useful baseline to compare the AI against.
"""
from __future__ import annotations

import re

from .taxonomy import THEME_KEYS, rating_prior

VERSION = "rules-v1"

# Weighted keyword patterns per theme. Matched case-insensitively on word
# boundaries. Weights: 3 = unambiguous, 2 = strong, 1 = weak hint.
THEME_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "support": [
        (r"support(?:ive)?", 2), (r"sapport", 2), (r"customer (?:service|care)", 3),
        (r"service", 1), (r"team", 1), (r"respon(?:d|se|sive|ding)", 1),
        (r"reply|replies|revert(?:ing)?", 2), (r"ticket", 2), (r"helpful", 1),
        (r"help(?:ed)? me", 1), (r"assist(?:ed|ance)?", 1), (r"executive", 2),
        (r"thank(?:s| you)(?: to)? (?:mr\.? |ms\.? )?[A-Z][a-z]+", 2),
        (r"(?:email|e-mail|mail)ed", 1), (r"no one|nobody", 1), (r"ghost", 2),
        (r"merchant care", 3),
    ],
    "setup": [
        (r"kyc", 3), (r"onboard(?:ing|ed)?", 3), (r"on boarding", 3),
        (r"install(?:ation|ed|ing)?", 2), (r"integrat(?:e|ed|ion|ing)", 2),
        (r"set ?up|setting up|configur(?:e|ed|ation)", 2), (r"activat(?:e|ion|ed)", 2),
        (r"implementation", 2), (r"uninstall(?:ed|ing)?|remove from", 2),
        (r"go(?:ne)? live|went live", 1), (r"approval|cpv|documents?", 2),
        (r"log ?in to|unable to log", 1),
    ],
    "reliability": [
        (r"not working|doesn'?t work|does not work|never seems to work|stopped working", 3),
        (r"bugs?|buggy|glitch", 3), (r"errors?", 3), (r"white screen|blank screen", 3),
        (r"crash(?:es|ed)?", 3), (r"disable[sd]? itself|automatically disable", 3),
        (r"discrepanc(?:y|ies)|mismatch", 2), (r"issue", 1), (r"problem", 1),
        (r"stopping my customer|fail(?:ed|s|ing)? to (?:pay|load)", 3),
        (r"works? (?:fine|perfectly|well|flawless(?:ly)?)", 1),
    ],
    "conversion": [
        (r"conversions?", 3), (r"checkout (?:experience|process)", 2),
        (r"cart abandon(?:ment)?|abandon(?:ed)? carts?", 3), (r"auto-?fill(?:ed|ing)?", 3),
        (r"1[- ]click|one[- ]click", 2), (r"address", 1), (r"seamless", 1),
        (r"smooth(?:er)?", 1), (r"faster|fast checkout", 1), (r"uplift|growth|increase", 2),
        (r"sales|revenue", 1), (r"prepaid", 1), (r"customer[- ]friendly|user experience", 1),
    ],
    "cod_rto": [
        (r"\bcod\b", 2), (r"cash on delivery", 3), (r"\brto\b", 3), (r"return to origin", 3),
        (r"fake orders?", 3), (r"otp", 2), (r"cod (?:fee|charge|verification|hide)", 3),
        (r"verification", 1),
    ],
    "pricing": [
        (r"pric(?:e|ing|ey)", 3), (r"costly|expensive|cheap", 3), (r"fees?", 2),
        (r"charge[ds]?|charging", 2), (r"commission", 3), (r"billing|billed|invoice", 3),
        (r"₹|rs\.? ?\d|inr", 2), (r"\d(?:\.\d+)? ?%", 1), (r"refund", 2), (r"value for money", 3),
        (r"premium", 1),
    ],
    "features": [
        (r"features?", 2), (r"option(?:s)?", 1), (r"\bemi\b", 3), (r"\bapi\b", 2),
        (r"customi[sz](?:e|ation|able)", 2), (r"functionalit(?:y|ies)", 1),
        (r"no option", 2),
    ],
}

_COMPILED = {
    theme: [(re.compile(rf"(?<![\w]){p}(?![\w])", re.I), w) for p, w in pats]
    for theme, pats in THEME_PATTERNS.items()
}
# Theme preference when scores tie (more specific themes first).
_TIE_ORDER = ("cod_rto", "pricing", "reliability", "setup", "conversion",
              "features", "support", "general")

NEG_CUES = re.compile(
    r"\b(?:not working|doesn'?t work|worst|wrost|terrible|pathetic|poor|bad|"
    r"horrible|disappoint\w*|frustrat\w*|unprofessional|waste[d]?|no (?:support|"
    r"response|reply|resolution|solution)|didn'?t get any|never received|"
    r"not (?:good|helpful|satisfied)|unable to|third[- ]class|scam|issue persists|"
    r"waiting|still (?:no|not)|costly|expensive|overpriced|charged)\b",
    re.I,
)
POS_CUES = re.compile(
    r"\b(?:great|excellent|amazing|awesome|good|nice|best|helpful|smooth|"
    r"seamless|love|recommend(?:ed)?|happy|satisfied|thank(?:s| you)|"
    r"wonderful|perfect(?:ly)?|quick|prompt|easy|superb|fantastic)\b",
    re.I,
)


def theme_scores(text: str) -> dict[str, int]:
    scores = {t: 0 for t in THEME_KEYS}
    for theme, pats in _COMPILED.items():
        for rx, weight in pats:
            if rx.search(text):
                scores[theme] += weight
    return scores


def tag_theme(text: str) -> str:
    if not text.strip():
        return "general"
    scores = theme_scores(text)
    best = max(scores.values())
    if best < 2:   # a single weak hint is not enough to leave "general"
        return "general"
    return next(t for t in _TIE_ORDER if scores.get(t) == best)


def tag_sentiment(text: str, rating: int) -> str:
    prior = rating_prior(rating)
    if not text.strip():
        return prior
    neg = len(NEG_CUES.findall(text))
    pos = len(POS_CUES.findall(text))
    if prior == "positive" and neg >= 2 and neg > pos:
        return "neutral"          # 5 stars but the text is mostly a complaint
    if prior == "negative" and pos >= 2 and neg == 0:
        return "neutral"          # 1-2 stars but the text reads positive
    if prior == "neutral":
        if neg > pos and neg >= 1:
            return "negative"
        if pos > neg and pos >= 2:
            return "positive"
    return prior


def tag(review: dict) -> dict:
    text = review.get("text") or ""
    return {
        "sentiment": tag_sentiment(text, int(review["rating"])),
        "theme": tag_theme(text),
        "tag_source": "rules",
        "tag_model": VERSION,
        "tag_reason": "",
    }
