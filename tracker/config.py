"""Single place for every setting the tracker uses.

Change the app list, paths or politeness settings here; nothing else in the
code hard-codes them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REVIEWS_FILE = DATA_DIR / "reviews.json"   # the review history (source of truth)
RUNS_FILE = DATA_DIR / "runs.json"         # one entry per run, newest last
SITE_SRC = ROOT / "site"                   # hand-written page (HTML/CSS/JS)
SITE_OUT = ROOT / "_site"                  # built page = site/ + generated data.json
EVAL_DIR = ROOT / "eval"

BASE_URL = "https://apps.shopify.com"


@dataclass(frozen=True)
class App:
    slug: str      # the handle in apps.shopify.com/<slug>
    name: str      # display name, as on the App Store
    vendor: str    # short label used on the page
    color: str     # series colour on the page


APPS: tuple[App, ...] = (
    App("kwikcheckout", "Kwik COD & Checkout", "GoKwik", "#2a78d6"),
    App("razorpay-checkout", "Razorpay COD & Magic Checkout", "Razorpay", "#eb6834"),
    App("simpl-checkout", "Simpl: 1-Click Checkout", "Simpl", "#1baf7a"),
)
APPS_BY_SLUG = {a.slug: a for a in APPS}

# --- Politeness / robustness of the scraper --------------------------------
USER_AGENT = (
    "checkout-reviews-tracker/1.0 "
    "(+https://github.com/Krishna-1812/shopify-checkout-reviews-tracker)"
)
REQUEST_DELAY_SECONDS = 1.5   # pause between page requests
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4               # per page, for 429 / 5xx / network errors
MAX_PAGES_PER_APP = 80        # hard stop so a pagination bug can't loop forever

# --- Run history ------------------------------------------------------------
MAX_RUNS_KEPT = 400           # older run-log entries are dropped

# --- Health thresholds ------------------------------------------------------
# After a full crawl we expect to hold (almost) every review Shopify says the
# app has. A bigger gap than this means reviews are being missed.
COVERAGE_TOLERANCE = 0.05
