"""Decide whether a run needs a human.

The tricky requirement is "alert when a run quietly collects nothing, stay
silent when everything is fine". On a normal day there may be zero *new*
reviews (Razorpay gets a few a month) and Simpl has no reviews at all, so
"0 new reviews" or "0 reviews for an app" cannot be the signal.

Instead each app's page header says how many reviews Shopify holds
("Reviews (398)"). We compare what we parsed against that number:

* Shopify says N > 0 but we parsed 0 on page 1  -> parser/markup broke (error)
* we could not read the header at all           -> markup changed (error)
* a review block was missing id/date/stars      -> markup drifted (error)
* after a full crawl we hold < 95% of N         -> reviews are being missed (error)
* the request itself failed after retries       -> blocked / down (error)
* nothing parsed across every app               -> run collected nothing (error)

Warnings (shown on the page, never alerted): the AI tagger fell back to rules.
"""
from __future__ import annotations

from . import config


def evaluate(run: dict) -> list[dict]:
    problems: list[dict] = []

    def add(level: str, app: str | None, msg: str) -> None:
        problems.append({"level": level, "app": app, "message": msg})

    total_parsed = 0
    total_declared = 0
    for slug, a in run.get("apps", {}).items():
        total_parsed += a.get("parsed") or 0
        declared = a.get("declared_total")
        total_declared += declared or 0
        if a.get("error"):
            add("error", slug, f"crawl failed: {a['error']}")
            continue
        if declared is None:
            add("error", slug, "could not read the review count on the page; "
                               "the App Store layout may have changed")
            continue
        if declared > 0 and not a.get("first_page_count"):
            add("error", slug, f"Shopify lists {declared} reviews but the parser found 0 on "
                               "page 1, so the run quietly collected nothing")
        if a.get("malformed"):
            add("error", slug, f"{a['malformed']} review block(s) were missing id, date or "
                               "stars; the review markup has probably changed")
        stored = a.get("stored_total") or 0
        if a.get("mode") == "full" and declared and stored < declared * (1 - config.COVERAGE_TOLERANCE):
            add("error", slug, f"after a full crawl we hold {stored} of the {declared} reviews "
                               "Shopify lists; reviews are being missed")

    if total_parsed == 0 and total_declared > 0:
        add("error", None, "the run collected no reviews from any app")

    tagging = run.get("tagging") or {}
    status = tagging.get("llm_status", "")
    if status and status not in ("ok", "not needed"):
        add("warning", None, f"AI tagger: {status}; rules were used instead")

    return problems


def is_healthy(problems: list[dict]) -> bool:
    return not any(p["level"] == "error" for p in problems)


def alert_markdown(run: dict, problems: list[dict], run_url: str | None) -> str:
    lines = ["The daily reviews tracker run needs attention.", ""]
    for p in problems:
        if p["level"] == "error":
            where = config.APPS_BY_SLUG[p["app"]].name if p.get("app") in config.APPS_BY_SLUG else "all apps"
            lines.append(f"- **{where}**: {p['message']}")
    lines += ["", "| App | Shopify count | Parsed | New | Stored | Mode | Pages |",
              "|---|---|---|---|---|---|---|"]
    for slug, a in run.get("apps", {}).items():
        lines.append(f"| {slug} | {a.get('declared_total')} | {a.get('parsed')} | {a.get('new')} | "
                     f"{a.get('stored_total')} | {a.get('mode')} | {a.get('pages_fetched')} |")
    lines += ["", f"Run: {run_url or run.get('run_id')}  ",
              f"Started: {run.get('started_at')}  ",
              f"Trigger: {run.get('trigger')}" + (f" (simulated: {run['simulate']})" if run.get("simulate") else "")]
    return "\n".join(lines) + "\n"
