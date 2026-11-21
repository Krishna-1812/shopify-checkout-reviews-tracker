"""Turn data/reviews.json + data/runs.json into _site/ (the public page).

All the numbers the page shows are computed here, in Python, and written to
_site/data.json. The page's JavaScript only slices and draws them, so every
number on the page can be reproduced and is covered by tests.

Weeks are ISO weeks (Monday to Sunday) labelled by their Monday, and the series
are continuous: a week with no reviews is an explicit 0, never a gap.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone

from . import config, store
from .tagging.taxonomy import SENTIMENT_RULES, SENTIMENTS, THEME_KEYS, THEME_RULES, THEMES

LATEST_N = 20


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_axis(first: date, last: date) -> list[date]:
    weeks, w = [], week_start(first)
    end = week_start(last)
    while w <= end:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


def build_series(reviews: list[dict], today: date) -> dict:
    if reviews:
        first = min(date.fromisoformat(r["date"]) for r in reviews)
    else:
        first = today
    weeks = week_axis(first, today)
    index = {w: i for i, w in enumerate(weeks)}
    n = len(weeks)

    series: dict[str, dict] = {}
    for app in config.APPS:
        s = {
            "total": [0] * n,
            "rating_sum": [0] * n,
            **{k: [0] * n for k in SENTIMENTS},
            "themes": {t: [0] * n for t in THEME_KEYS},
            "themes_negative": {t: [0] * n for t in THEME_KEYS},
            "no_text": [0] * n,
        }
        series[app.slug] = s
    for r in reviews:
        s = series.get(r["app"])
        if s is None:
            continue
        i = index[week_start(date.fromisoformat(r["date"]))]
        s["total"][i] += 1
        s["rating_sum"][i] += int(r["rating"])
        if r.get("sentiment") in SENTIMENTS:
            s[r["sentiment"]][i] += 1
        if not (r.get("text") or "").strip():
            s["no_text"][i] += 1          # rating-only: no theme to read, kept out of the mix
        elif r.get("theme") in THEME_KEYS:
            s["themes"][r["theme"]][i] += 1
            if r.get("sentiment") == "negative":
                s["themes_negative"][r["theme"]][i] += 1
    return {"weeks": [w.isoformat() for w in weeks], "series": series}


def app_summary(app: config.App, reviews: list[dict], last_run_app: dict | None, today: date) -> dict:
    mine = [r for r in reviews if r["app"] == app.slug]
    d90 = (today - timedelta(days=90)).isoformat()
    recent = [r for r in mine if r["date"] >= d90]
    themes = {t: sum(1 for r in mine if r.get("theme") == t) for t in THEME_KEYS}
    sentiments = {s: sum(1 for r in mine if r.get("sentiment") == s) for s in SENTIMENTS}
    return {
        "slug": app.slug,
        "name": app.name,
        "vendor": app.vendor,
        "color": app.color,
        "store_url": f"{config.BASE_URL}/{app.slug}",
        "reviews_url": f"{config.BASE_URL}/{app.slug}/reviews?sort_by=newest",
        "stored_total": len(mine),
        "removed_total": sum(1 for r in mine if r.get("removed_on")),   # deleted on Shopify since we stored them
        "listed_total": (last_run_app or {}).get("declared_total"),
        "listed_rating": (last_run_app or {}).get("overall_rating"),
        "avg_rating": round(sum(r["rating"] for r in mine) / len(mine), 2) if mine else None,
        "last_90d": len(recent),
        "last_90d_negative": sum(1 for r in recent if r.get("sentiment") == "negative"),
        "latest_date": max((r["date"] for r in mine), default=None),
        "sentiments": sentiments,
        "themes": themes,
    }


def latest_reviews(reviews: list[dict], n: int = LATEST_N) -> list[dict]:
    """The n newest reviews per app (the page shows the 20 newest overall or per app)."""
    out = []
    for app in config.APPS:
        mine = [r for r in reviews if r["app"] == app.slug]
        mine.sort(key=lambda r: (r["date"], int(r["id"])), reverse=True)
        out.extend(mine[:n])
    out.sort(key=lambda r: (r["date"], int(r["id"])), reverse=True)
    keep = ("id", "app", "url", "date", "rating", "store", "country", "text",
            "sentiment", "theme", "tag_source", "tag_reason", "removed_on")
    return [{k: r.get(k) for k in keep} for r in out]


def run_summary(runs: list[dict]) -> dict:
    last = runs[-1] if runs else None
    real = [r for r in runs if not r.get("simulate")]
    last_ok = next((r for r in reversed(real) if r.get("status") == "ok"), None)
    recent = [
        {"at": r["started_at"], "status": r.get("status"), "new": r.get("totals", {}).get("new"),
         "simulate": r.get("simulate"), "url": r.get("run_url"), "trigger": r.get("trigger")}
        for r in runs[-30:]
    ]
    return {
        "last": {k: last.get(k) for k in ("started_at", "status", "problems", "totals",
                                          "tagging", "run_url", "simulate", "apps", "trigger")} if last else None,
        "last_ok_at": last_ok["started_at"] if last_ok else None,
        "count": len(real),
        "first_at": real[0]["started_at"] if real else None,
        "recent": recent,
    }


def tagging_mix(reviews: list[dict]) -> dict:
    mix: dict[str, int] = {}
    for r in reviews:
        key = r.get("tag_source") or "untagged"
        mix[key] = mix.get(key, 0) + 1
    return mix


def build_data(reviews_by_id: dict[str, dict], runs: list[dict], today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    reviews = list(reviews_by_id.values())
    last_real = next((r for r in reversed(runs) if not r.get("simulate")), None)
    last_apps = (last_real or {}).get("apps", {})
    eval_report = config.EVAL_DIR / "report.json"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "apps": [app_summary(a, reviews, last_apps.get(a.slug), today) for a in config.APPS],
        **build_series(reviews, today),
        "latest": latest_reviews(reviews),
        "themes": {k: {"label": lbl, "definition": d} for k, (lbl, d) in THEMES.items()},
        "tag_rules": {"sentiment": SENTIMENT_RULES, "theme": THEME_RULES},   # shown on label.html
        "tagging": tagging_mix(reviews),
        "runs": run_summary(runs),
        "evaluation": json.loads(eval_report.read_text()) if eval_report.exists() else None,
    }


def build_site() -> None:
    """Copy site/ to _site/ and write the generated data.json next to it."""
    if config.SITE_OUT.exists():
        shutil.rmtree(config.SITE_OUT)
    shutil.copytree(config.SITE_SRC, config.SITE_OUT)
    data = build_data(store.load_reviews(), store.load_runs())
    (config.SITE_OUT / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sample = config.EVAL_DIR / "sample.json"
    if sample.exists():   # reviews for the blind hand-labelling page
        shutil.copy(sample, config.SITE_OUT / "label-sample.json")
