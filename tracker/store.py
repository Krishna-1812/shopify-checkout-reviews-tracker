"""Review history: load, merge (dedupe) and save.

``data/reviews.json`` is a JSON list with one object per review, keyed by
Shopify's own review id. Merging a fresh crawl into it is idempotent:

* a review id we have never seen is added (``first_seen`` = today);
* a review id we already hold only gets ``last_seen`` bumped, so a review
  seen on ten different days is still stored and counted once;
* if the merchant edited the review (text or stars changed) the new text
  replaces the old one and its tags are cleared, so it gets re-tagged.

The file is sorted by id and pretty-printed so every daily commit is a
small, readable git diff.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config

# Fields that come from the App Store page; tags and bookkeeping live alongside.
SCRAPED_FIELDS = (
    "app", "url", "rating", "date", "store", "country", "usage", "text",
    "has_reply", "content_hash",
)
TAG_FIELDS = ("sentiment", "theme", "tag_source", "tag_model", "tag_version", "tag_reason")


def load_reviews(path: Path = config.REVIEWS_FILE) -> dict[str, dict]:
    if not path.exists():
        return {}
    items = json.loads(path.read_text(encoding="utf-8"))
    return {r["id"]: r for r in items}


def save_reviews(reviews: dict[str, dict], path: Path = config.REVIEWS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(reviews.values(), key=lambda r: int(r["id"]))
    path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


@dataclass
class MergeStats:
    new: int = 0
    edited: int = 0
    unchanged: int = 0


def merge(store: dict[str, dict], scraped: list[dict], today: str) -> MergeStats:
    """Merge freshly scraped reviews into ``store`` in place."""
    stats = MergeStats()
    seen_this_run: set[str] = set()
    for item in scraped:
        rid = item["id"]
        if rid in seen_this_run:      # same review on two pages (list shifted mid-crawl)
            continue
        seen_this_run.add(rid)
        existing = store.get(rid)
        if existing is None:
            store[rid] = {
                "id": rid,
                **{k: item.get(k) for k in SCRAPED_FIELDS},
                "first_seen": today,
                "last_seen": today,
            }
            stats.new += 1
            continue
        existing["last_seen"] = today
        if existing.get("content_hash") != item["content_hash"]:
            for k in SCRAPED_FIELDS:
                existing[k] = item.get(k)
            existing["edited_seen"] = today
            for k in TAG_FIELDS:
                existing.pop(k, None)
            stats.edited += 1
        else:
            # cheap metadata that can change without the review text changing
            existing["has_reply"] = item.get("has_reply", existing.get("has_reply"))
            existing["usage"] = item.get("usage") or existing.get("usage")
            stats.unchanged += 1
    return stats


def load_runs(path: Path = config.RUNS_FILE) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def append_run(run: dict, path: Path = config.RUNS_FILE) -> None:
    runs = load_runs(path)
    runs.append(run)
    runs = runs[-config.MAX_RUNS_KEPT:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
