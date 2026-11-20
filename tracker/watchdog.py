"""Dead-man's switch: alert when the daily run has not happened at all.

    python -m tracker.watchdog                    # exit 0; writes stale=true|false to $GITHUB_OUTPUT
    python -m tracker.watchdog --max-age-hours 0  # force a "stale" verdict, to prove the alert works

The tracker's own alert job only fires for a run that starts. A run that never
starts (the workflow got disabled, a bad edit broke the workflow file, GitHub
dropped the scheduled run) says nothing, so the separate watchdog workflow runs
this every few hours and opens the same kind of GitHub issue when the newest
recorded run is too old. The next healthy tracker run closes that issue.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from . import config, store

# The run is daily, and GitHub can start scheduled runs hours late under load,
# so "no run for 36 h" means a whole day was missed, not that a run was slow.
DEFAULT_MAX_AGE_HOURS = 36.0


def last_run_at(runs: list[dict]) -> datetime | None:
    real = [r for r in runs if not r.get("simulate") and r.get("started_at")]
    if not real:
        return None
    return max(datetime.fromisoformat(r["started_at"]) for r in real)


def check(runs: list[dict], now: datetime, max_age_hours: float) -> tuple[bool, str]:
    """(stale, message)."""
    last = last_run_at(runs)
    if last is None:
        return True, "No tracker run has ever been recorded in `data/runs.json`."
    age = (now - last).total_seconds() / 3600
    when = f"{last:%Y-%m-%d %H:%M} UTC ({age:.1f} hours ago)"
    if age > max_age_hours:
        return True, (f"The last tracker run started at {when}, more than {max_age_hours:g} hours ago. "
                      "The daily run has not happened, so nothing is being collected.")
    return False, f"Last run {when}: fine."


def alert_markdown(message: str, max_age_hours: float) -> str:
    return "\n".join([
        "**The daily reviews tracker has not run.** (watchdog)",
        "",
        message,
        "",
        "Likely causes, most likely first:",
        "- GitHub disabled or skipped the scheduled workflow (Actions tab: is *Daily reviews tracker* enabled?).",
        "- A recent change broke `.github/workflows/tracker.yml`, so the workflow can't start.",
        "- GitHub Actions is having an incident (githubstatus.com).",
        "",
        "Fix: open Actions → Daily reviews tracker → Run workflow. A healthy run closes this issue.",
        f"_Checked every 6 hours; alerts when there has been no run for {max_age_hours:g} hours._",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--max-age-hours", type=float,
                   default=float(os.environ.get("MAX_AGE_HOURS") or DEFAULT_MAX_AGE_HOURS))
    args = p.parse_args(argv)
    stale, message = check(store.load_runs(), datetime.now(timezone.utc), args.max_age_hours)
    print(message)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"stale={'true' if stale else 'false'}\n")
    if stale:
        (config.ROOT / "alert.md").write_text(alert_markdown(message, args.max_age_hours), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
