"""Pick the 30 reviews to tag by hand (run once; the result is committed).

    python -m eval.sample            # writes eval/sample.json

Stratified rather than purely random: a random 30 from these apps would be
~90% short 5-star "thanks <agent>" reviews and would say little about the hard
cases. Rating-only reviews are excluded: their tag is derived from the stars
by a fixed rule, so there is nothing to check.

  Kwik      10 x 4-5 stars, 6 x 1-3 stars
  Razorpay   8 x 1-2 stars, 6 x 3-5 stars

The file holds only what a human needs to label (no tracker tags), so the
hand labels are blind.
"""
from __future__ import annotations

import json
import random

from tracker import config, store

SEED = 20261119
PLAN = [
    ("kwikcheckout", lambda r: r >= 4, 10),
    ("kwikcheckout", lambda r: r <= 3, 6),
    ("razorpay-checkout", lambda r: r <= 2, 8),
    ("razorpay-checkout", lambda r: r >= 3, 6),
]
OUT = config.EVAL_DIR / "sample.json"


def main() -> None:
    reviews = sorted(store.load_reviews().values(), key=lambda r: int(r["id"]))
    rng = random.Random(SEED)
    picked: list[dict] = []
    for app, band, n in PLAN:
        pool = [r for r in reviews if r["app"] == app and band(r["rating"]) and r["text"].strip()]
        picked += rng.sample(pool, min(n, len(pool)))
    rng.shuffle(picked)   # so the labeller doesn't see them grouped by app/stars
    out = [{k: r[k] for k in ("id", "app", "rating", "date", "text", "url")} for r in picked]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} reviews to {OUT}")


if __name__ == "__main__":
    main()
