"""One tracker run: scrape -> merge -> tag -> save -> health check -> build page.

    python -m tracker.run                 # normal daily run
    python -m tracker.run --full          # re-walk every page of every app
    python -m tracker.run --build-only    # just rebuild _site/ from data/
    python -m tracker.run --simulate empty_result   # prove the alert works
    python -m tracker.run --simulate crash

The process exits 0 when it finished, healthy or not; the health verdict is
written to $GITHUB_OUTPUT (health=ok|alert) and to alert.md so the workflow
can still commit the run log and publish the page before failing the job.
It exits non-zero only if it crashed.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from . import aggregate, config, health, scrape, store
from .tagging import tag_all

log = logging.getLogger("tracker")


def _run_url() -> str | None:
    server, repo, rid = (os.environ.get(k) for k in
                         ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{rid}" if server and repo and rid else None


def _write_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _write_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")


def scrape_all(reviews: dict, today: str, force_full: bool, simulate: str) -> dict:
    session = scrape.make_session()
    apps_report: dict[str, dict] = {}
    for app in config.APPS:
        known = {rid for rid, r in reviews.items() if r["app"] == app.slug}
        full = force_full or not known
        crawl = scrape.crawl_app(session, app.slug, known, full=full,
                                 simulate_empty=simulate == "empty_result")
        merged = store.merge(reviews, crawl.reviews, today)
        stored = sum(1 for r in reviews.values() if r["app"] == app.slug)

        # Self-healing: if we hold fewer reviews than Shopify lists, walk every page once.
        if (not full and not crawl.error and crawl.declared_total
                and stored < crawl.declared_total and simulate == "none"):
            log.info("%s: stored %d < listed %d, running a full crawl",
                     app.slug, stored, crawl.declared_total)
            full_crawl = scrape.crawl_app(session, app.slug, known, full=True)
            more = store.merge(reviews, full_crawl.reviews, today)
            merged.new += more.new
            merged.edited += more.edited
            full_crawl.reviews = crawl.reviews + full_crawl.reviews
            full_crawl.pages_fetched += crawl.pages_fetched
            crawl = full_crawl
            stored = sum(1 for r in reviews.values() if r["app"] == app.slug)

        apps_report[app.slug] = {
            "mode": crawl.mode,
            "pages_fetched": crawl.pages_fetched,
            "parsed": len({r["id"] for r in crawl.reviews}),
            "first_page_count": crawl.first_page_count,
            "declared_total": crawl.declared_total,
            "overall_rating": crawl.overall_rating,
            "rating_counts": crawl.rating_counts,
            "malformed": crawl.malformed,
            "new": merged.new,
            "edited": merged.edited,
            "stored_total": stored,
            "error": crawl.error,
        }
        log.info("%s: %s", app.slug, apps_report[app.slug])
    return apps_report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full", action="store_true", help="walk every review page")
    p.add_argument("--build-only", action="store_true", help="only rebuild the page")
    p.add_argument("--simulate", choices=["none", "empty_result", "crash"],
                   default=os.environ.get("SIMULATE") or "none")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.build_only:
        aggregate.build_site()
        return 0

    if args.simulate == "crash":
        raise RuntimeError("simulated crash (--simulate crash) to test the failure alert")

    started = datetime.now(timezone.utc)
    today = started.date().isoformat()
    reviews = store.load_reviews()

    apps_report = scrape_all(reviews, today, args.full, args.simulate)
    tag_stats = tag_all(reviews)
    if args.simulate == "none":
        store.save_reviews(reviews)

    run = {
        "run_id": os.environ.get("GITHUB_RUN_ID") or started.strftime("local-%Y%m%dT%H%M%S"),
        "run_url": _run_url(),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "simulate": None if args.simulate == "none" else args.simulate,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "apps": apps_report,
        "totals": {
            "parsed": sum(a["parsed"] for a in apps_report.values()),
            "new": sum(a["new"] for a in apps_report.values()),
            "edited": sum(a["edited"] for a in apps_report.values()),
            "stored": len(reviews),
        },
        "tagging": tag_stats.as_dict(),
    }
    problems = health.evaluate(run)
    healthy = health.is_healthy(problems)
    run["status"] = "ok" if healthy else "alert"
    run["problems"] = problems
    store.append_run(run)
    aggregate.build_site()

    summary = (f"### Tracker run: {'healthy' if healthy else 'NEEDS ATTENTION'}\n\n"
               f"New reviews: {run['totals']['new']} · stored: {run['totals']['stored']} · "
               f"AI tagger: {tag_stats.llm_status} ({tag_stats.llm_tagged} tagged, "
               f"model {tag_stats.llm_model})\n")
    for prob in problems:
        summary += f"\n- {prob['level']}: {prob.get('app') or 'all'}: {prob['message']}"
    print(summary)
    _write_summary(summary)
    _write_output("health", "ok" if healthy else "alert")
    _write_output("new_reviews", str(run["totals"]["new"]))
    if not healthy:
        (config.ROOT / "alert.md").write_text(
            health.alert_markdown(run, problems, run["run_url"]), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
