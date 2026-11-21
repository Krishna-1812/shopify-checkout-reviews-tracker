# Checkout reviews tracker

A small product that runs by itself: every day it collects the public merchant reviews of three
Shopify checkout apps, stores each review once, tags it with a sentiment and a main theme, checks its
own health, and publishes a page. If a run fails, or quietly collects nothing, it opens a GitHub issue.
When everything is fine it stays silent.

| | |
|---|---|
| **Live page** | https://krishna-1812.github.io/shopify-checkout-reviews-tracker/: reviews per week, sentiment by week and theme mix for each app, the 20 latest reviews with links, tagging accuracy and run health |
| **Apps tracked** | [Kwik COD & Checkout](https://apps.shopify.com/kwikcheckout) (GoKwik) · [Razorpay COD & Magic Checkout](https://apps.shopify.com/razorpay-checkout) · [Simpl: 1-Click Checkout](https://apps.shopify.com/simpl-checkout) |
| **Schedule** | Every day at 03:17 and 15:17 UTC (08:47 and 20:47 IST) on GitHub Actions. GitHub can start scheduled runs hours late or skip one, so the second slot keeps a run on every day |
| **Cost** | ₹0: GitHub Actions, GitHub Pages, and an optional free AI tier (Google Gemini or Groq). No paid API, no card |
| **Tagging check** | I labelled 30 reviews blind on the [labelling page](https://krishna-1812.github.io/shopify-checkout-reviews-tracker/label.html). The tracker agrees with me on **sentiment 30/30 (100%)** and **theme 26/30 (87%)**; keyword rules alone get the theme right 23/30 (77%). The two wrong cases are on the page; every disagreement is in [eval/REPORT.md](eval/REPORT.md) |
| **Alert proof** | [Issue #1](https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/issues/1): simulated "parser found nothing" run, opened by the workflow and closed by the next healthy run. [Issue #2](https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/issues/2): a **real** failure the alert caught on day one (see §4). [Issue #3](https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/issues/3): test of the watchdog ("no run for 36 h") alert |

```
Shopify App Store ──► scrape ──► merge (dedupe by review id) ──► tag (AI → rules fallback)
   (public HTML)                       data/reviews.json                │
                                                                        ▼
         GitHub issue ◄── alert ◄── health check ──► data/runs.json ──► build page ──► GitHub Pages
```

---

## What the data says (20 Nov 2026)

438 reviews: Kwik 398, Razorpay 40, Simpl 0. 305 have text, all AI-tagged; 133 are rating-only.

- **Kwik COD & Checkout** is rated 4.6★, but sentiment is sliding.
  - Negative share by year: **3% in 2024, 6% in 2025, 19% in 2026**, while volume fell from
    ~25–35 reviews a month in 2025 to a handful in 2026.
  - Its last two reviews (Oct–Nov 2026) are both negative.
  - Complaints are mostly **setup/KYC onboarding (10 of 22)** and **support responsiveness (8)**.
  - Praise is overwhelmingly about named support agents: 55% of written reviews are support-themed.
- **Razorpay COD & Magic Checkout** is rated 1.9★, and **89% of its 2026 reviews are negative**,
  mainly about support (12 of 22 complaints) and the checkout not working (4).
  - Five 1★ reviews landed on **28 Oct 2026 with near-identical wording**, which looks like a
    coordinated campaign and is worth an anomaly rule (§6).
- **Simpl: 1-Click Checkout** has **no reviews** on the App Store (listed since April 2024). The
  tracker checks it daily and treats "listed 0, parsed 0" as healthy.

---

## 1. How to run it, and what runs when

### Automatically

[`.github/workflows/tracker.yml`](.github/workflows/tracker.yml) runs every day at **03:17 and 15:17 UTC**.
GitHub's scheduler can start a run several hours late, or skip one when it is busy (the first scheduled
run here started 8.5 hours late), so two slots 12 hours apart keep "runs daily" true even then. A second
run on the same day costs one page request per app and changes nothing if no review is new. It also
runs when code under `tracker/`, `site/` or `eval/` changes on `main`, and on demand from
**Actions → Daily reviews tracker → Run workflow**. Four jobs:

| Job | When | What it does |
|---|---|---|
| `track` | every run | Runs the unit tests, then `python -m tracker.run`: scrape, merge, tag, health check, build `_site/`. Then scores the tagging against the hand labels (if present), commits `data/` and `eval/` back to `main`, and uploads the page. Fails at the end if the health check said `alert`. |
| `deploy` | after `track` has built the page, **even if `track` then failed** | Publishes `_site/` to GitHub Pages, so the page shows the problem too. |
| `alert` | only if `track` or `deploy` failed, **timed out or was cancelled** | Opens a GitHub issue labelled `tracker-alert`, or comments on the one already open (no duplicates). Posts to Discord too if `DISCORD_WEBHOOK_URL` is set. |
| `recover` | only if everything passed | Closes any open `tracker-alert` issue with a link to the healthy run. |

A second workflow, [`.github/workflows/watchdog.yml`](.github/workflows/watchdog.yml), runs every 6 hours.
It checks `data/runs.json` and opens the same `tracker-alert` issue if no run has happened for 36 hours, because
a run that never starts can't raise its own alert (§4). It is silent otherwise, and it re-enables the tracker
workflow if GitHub switched it off.

A normal day reads one page per app (10 newest reviews). The crawler stops at the first page whose
reviews are all already stored. It walks every page only on the first run, when asked (`full_crawl`),
or by itself when it holds fewer reviews than Shopify lists. That last case is self-healing for
missed days.

**Run options** (Run workflow form): `simulate = empty_result` pretends the parser found nothing.
`simulate = crash` makes the run fail. Both exist to prove the alert works. `full_crawl` re-reads
every page.

### Locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                        # 73 tests, ~1 s, no network
TAGGER=rules python -m tracker.run         # real scrape, keyword tagging, builds _site/
python -m http.server -d _site 8000        # open http://localhost:8000
```

To use AI tagging locally, get a free key from [Google AI Studio](https://aistudio.google.com/apikey)
(or [Groq](https://console.groq.com/keys)) and run `GEMINI_API_KEY=... python -m tracker.run`.
Other commands:
`python -m tracker.run --full`, `python -m tracker.run --build-only`,
`python -m tracker.run --simulate empty_result`, `python -m eval.evaluate`, `python -m tracker.watchdog`.

### Repository layout

```
tracker/
  config.py           apps, paths, politeness and health thresholds (the only place to change them)
  scrape.py           fetch with retries/backoff + HTML parser (pure function, tested on saved pages)
  store.py            load / merge / save; dedupe by Shopify review id; edit detection
  tagging/
    taxonomy.py       the label set: 3 sentiments, 8 themes, with definitions (shared by AI, rules, humans)
    llm.py            client for free OpenAI-compatible APIs (Gemini, Groq): batching, validation, rate limits
    rules.py          keyword + star-rating tagger (fallback and baseline)
    __init__.py       decides what to (re)tag; AI first, rules for anything left
  health.py           the alert rules
  watchdog.py         the "no run for 36 h" check used by watchdog.yml
  aggregate.py        every number on the page (weekly series, theme mix, latest 20, run health)
  run.py              one run, end to end (the CLI)
site/                 the page: index.html, app.js (Chart.js), style.css, label.html (blind labelling)
eval/                 sample.py (picks the 30), evaluate.py (scores), hand_labels.csv, REPORT.md
data/                 reviews.json (history), runs.json (run log): written by the bot
tests/                parser, merge, health, watchdog, aggregation, tagging, evaluation (+ saved App Store pages)
.github/workflows/    tracker.yml (the daily run), watchdog.yml (dead-man's switch), ci.yml (tests on PRs)
```

---

## 2. How it works, and the decisions behind it

**Collecting.** The App Store review pages are server-rendered HTML
(`/<app>/reviews?sort_by=newest&page=N`, 10 per page). `robots.txt` allows them. Each review block
carries Shopify's own review id, which is the dedupe key. The parser reads only stable hooks
(`data-review-content-id`, `aria-label="N out of 5 stars"`, `data-truncate-content-copy`), never the
Tailwind CSS classes, which change often. Requests use a named User-Agent with a link to this repo,
a 1.5 s gap between pages, and retries with backoff on 429/5xx. A normal run makes 3 requests; a full
crawl makes about 47.

**History and dedupe.** `data/reviews.json` holds one record per review id, with `first_seen` and
`last_seen`. Seeing the same review again only moves `last_seen`, so a review seen on many days is
stored and counted once. If a merchant edits a review, the new text replaces the old one, the
review is counted once, and its tags are cleared so it gets re-tagged. Charts use the review's own
posted date, not the day we first saw it, so a backfill puts reviews in their real weeks.

**Tagging.** Every review gets a sentiment (positive / neutral / negative) and **one** main theme.
The themes are support, setup & onboarding (incl. KYC), bugs & reliability, checkout & conversion,
COD & RTO, pricing, features and general. I chose them after reading every review: most reviews
of these apps are about the support team, KYC/onboarding, or whether the checkout works. Definitions
live once in [`tracker/tagging/taxonomy.py`](tracker/tagging/taxonomy.py). The same text feeds the
AI prompt, the labelling page and the evaluation.

- **AI:** a free, OpenAI-compatible API, whichever has a key in the repo secrets: **Google Gemini**
  (`GEMINI_API_KEY`, free from AI Studio, no card) first, then **Groq** (`GROQ_API_KEY`).
  - Reviews go in batches of 10 at temperature 0, with a JSON reply format, and calls are spaced to
    stay under the free per-minute limits.
  - Any label outside the allowed set is rejected.
  - The model is picked from a preference list checked against the provider's live model list, so a
    retired model does not break the run.
  - *History:* it first used GitHub Models through the workflow's own token. The first production run
    showed that GitHub retired that service on 30 July 2026: the endpoint answers `200 OK` in plain
    text. The run stayed healthy (rules covered every review, the page showed a warning, no false
    alert). The client now treats any non-API reply as "provider unavailable" after one call.
- **Built for flaky free tiers.** Live runs hit read timeouts, `503 high demand` and `429`s on
  Gemini's free tier, so the client:
  - **rotates** to the next listed Flash or Flash-Lite model on a busy, rate-limited, slow or retired
    model (free quotas are per model), instead of sleeping;
  - **stops calling the AI** after 2 failed batches in a row (circuit breaker);
  - **caps AI work at 8 minutes per run**, enforced inside every wait and request.

  A struggling provider can never push the job into its timeout. Unfinished reviews keep rules tags
  and are upgraded on later runs.
- **Rules fallback:** if the AI is unavailable (quota, outage, no token), the keyword and star-rating
  tagger covers the gap. Those reviews are upgraded to AI tags on a later run. A tagging problem
  never fails a run; it shows as a warning on the page.
- **Rating-only reviews** (133 of 438 have no text) take their sentiment from the stars and the
  theme "general". They are left out of the theme-mix chart, which says so on the page.
- **Cost control:** each review is tagged once and cached. A normal day sends 0–1 requests.

**Checking the tagging.** [`eval/sample.py`](eval/sample.py) picks 30 written reviews, stratified by
app and star band. A purely random 30 would be ~90% "thanks Nikita, great support". They are
labelled by hand on [`label.html`](https://krishna-1812.github.io/shopify-checkout-reviews-tracker/label.html),
which hides the tracker's tags so the human isn't anchored, and uses the same definitions and
tie-breakers the AI is given. [`eval/evaluate.py`](eval/evaluate.py)
compares them with the stored tags on every run (so the score follows any re-tagging). It also
scores the rules tagger alone as a baseline, so you can see what the AI adds.

Result (labelled 21 Nov 2026): sentiment **30/30**, theme **26/30**. All four misses are theme
calls on positive reviews, and they show a pattern. When a review praises the app in general but
mentions one detail (OTP filtering, "quick process", a named agent), the AI makes that detail the
theme, whereas I read the review as general praise or about the checkout as a whole. The fix would
be a tie-breaker in the theme rules ("a passing mention is not the main topic"), then re-scoring. The
two highlighted misses are on the page; every disagreement is in [eval/REPORT.md](eval/REPORT.md).

**The alert, and why "0 new reviews" is not the signal.** On a normal day there may be no new
reviews (Razorpay gets a few a month), and Simpl has **no reviews at all**. So "collected 0" can't be
the alarm, or it would fire every day. Each review page states how many reviews Shopify holds
("Reviews (398)"). [`tracker/health.py`](tracker/health.py) compares what was parsed against that
number:

| Situation | Verdict |
|---|---|
| Shopify lists N > 0 reviews but the parser found 0 on page 1 | **alert**: quietly collected nothing |
| The review count can't be read from the page | **alert**: the layout changed |
| A review block is missing its id, date or stars | **alert**: the markup drifted |
| After a full crawl we hold < 95% of what Shopify lists | **alert**: reviews are being missed |
| A page can't be fetched after retries (403/429/5xx/timeout) | **alert** |
| Tests fail, the run crashes or hangs past its timeout, the data push or the Pages deploy fails | **alert** (the job fails or is cancelled) |
| No run at all for 36 h (workflow disabled, workflow file broken, scheduled runs dropped) | **alert** from the watchdog |
| No new reviews today; Simpl lists 0 and we parsed 0 | silent: healthy |
| AI tagger unavailable, rules used | silent: warning on the page only |

---

## 3. Where the data and keys live, and who can see them

| What | Where | Who can see it |
|---|---|---|
| Review history | `data/reviews.json` in this repo (git history = full audit trail and backup) | Anyone: the repo is public. It contains only what Shopify already shows publicly (store name, country, text, stars). |
| Run log | `data/runs.json` (last 400 runs) | Anyone |
| The page | GitHub Pages, built from `data/` on each run | Anyone |
| Alerts | GitHub issues labelled `tracker-alert` | Anyone can read. Repo watchers (the owner by default) get an email/notification. |
| `GITHUB_TOKEN` | Created by GitHub for each run, scoped to this repo, expires when the job ends | Nobody. It's never printed or stored. Each job asks only for what it needs: `contents: write` to commit data, `issues: write` for alerts, `pages: write` to deploy. |
| Optional `DISCORD_WEBHOOK_URL`, `HEARTBEAT_URL` | Repository → Settings → Secrets and variables → Actions | Encrypted. Only workflows can read them; GitHub masks them in logs. Repo admins can replace them but not read them. |
| `GEMINI_API_KEY` / `GROQ_API_KEY` (optional AI tagging) | Repository → Settings → Secrets and variables → Actions; locally only in your shell (`.env` is git-ignored) | Encrypted, readable only by the workflow, masked in logs. The page shows the provider and model name, never the key. |

There are **no API keys in the code or the repo**. A fork can't use this repo's secrets, and pull
requests from forks don't get write permissions.

---

## 4. What will break first, and how I'd find out

In rough order of likelihood:

1. **Shopify changes the review page markup.** This is the most likely failure. It shows up as
   "listed N but parsed 0", "count unreadable" or "malformed blocks", so **the alert fires the same
   day**. Fix: save the new page as a fixture in `tests/fixtures/`, update `scrape.py`, run the tests.
2. **Shopify starts blocking or rate-limiting GitHub's IP ranges** (403/429, a bot wall). The fetch
   fails after retries, so **the alert fires**. Fix: slow down, or run from a fixed IP (see §5).
3. **The free AI tier changes** (quota cut, model retired, key revoked, or a whole service shut down,
   as happened to GitHub Models in July 2026). This does
   *not* fail the run by design: tags fall back to rules. You'd see it as **"AI tagger: unavailable"**
   in the run-health panel and the job summary, and the rules share would grow in "How every review
   was tagged". The 30-label evaluation would show the accuracy cost. Fix: update `TAGGER_MODELS`,
   add the other provider's key, or add a provider entry in `llm.py` (any OpenAI-compatible API is
   about four lines).
4. **The scheduler stops running at all.** GitHub disables cron in public repos after 60 days without
   activity, and scheduled runs can be delayed or dropped when GitHub is busy. **A job that never runs
   can't send its own alert.** Mitigations in place:
   - Every run commits `data/runs.json`, which counts as repository activity.
   - Every run calls the "enable workflow" API for both workflows, and the watchdog re-enables the
     tracker.
   - **The watchdog** opens an alert issue when no run has happened for 36 hours. This also catches a
     bad edit to `tracker.yml` (a broken workflow file can't start, so it can't alert).
   - The page header turns **"Stale"** after 36 h without a healthy run.

   The watchdog runs on the same GitHub scheduler, so it can't catch GitHub Actions itself being down.
   For that, set `HEARTBEAT_URL` to a free [healthchecks.io](https://healthchecks.io) check: it emails
   you when the daily ping *doesn't* arrive.
5. **Tag drift.** The model's behaviour or the merchants' topics change, and themes stop fitting
   (e.g. a new "settlements" complaint lands in "general"). Watch the "general" share on the theme
   chart, and re-run the evaluation with fresh labels each quarter.
6. **Growth.** This is not a near-term risk. 438 reviews take about 290 KB, and history grows by a few
   KB a week. Past ~50k reviews I'd move to SQLite (see §6).

**What actually broke on day one** (each one is in the git history):

| Run | What happened | Caught by | Fix |
|---|---|---|---|
| 1 | GitHub Models answered `200 OK` (plain text) to every request: the service was retired on 30 Jul 2026 | AI-tagger warning on the page; rules covered every review; no false alert | Moved to free Gemini/Groq; a non-API reply now stops the AI after one call |
| 3 | Gemini listed `gemini-2.5-flash` but refused it to new keys (404) | Same warning | Model chosen from the provider's live list, newest first, with fallthrough |
| 4 | Gemini's free tier was overloaded (timeouts, 503, 429); retries ran into the 25-min job timeout, and GitHub reports a timeout as "cancelled", so **no alert fired** | Me, reading the run | Rotate models instead of sleeping, circuit breaker, hard 8-min AI budget; the alert job now also fires on cancelled/timed-out runs |
| 6 | A queued run started from its trigger commit, not the newest `main`, so its data commit conflicted and the push failed | **The alert** ([issue #2](https://github.com/Krishna-1812/shopify-checkout-reviews-tracker/issues/2)), closed automatically by the next healthy run | Check out `main` explicitly; abort conflicting rebases with a clear error |

---

## 5. Moving from GitHub Actions to a server you manage (e.g. AWS EC2)

Described, not set up. The code doesn't change: `python -m tracker.run` is the whole job. What
changes is everything GitHub Actions does around it: the schedule, the token, the push, the deploy,
and failure handling.

1. **Provision.** One `t4g.micro` (or `t3.micro`) running Ubuntu 24.04 LTS in `ap-south-1`.
   - Security group: no inbound ports. Use SSM Session Manager instead of SSH.
   - Enforce IMDSv2.
   - Attach an IAM instance role with only the permissions listed below.
   - A fixed egress IP (Elastic IP or NAT) also helps if Shopify ever rate-limits by IP.
2. **Base setup.** `apt install python3.12-venv git`, enable `unattended-upgrades`, create a
   non-login user `tracker`, and set the clock to UTC.
3. **Code and data.** Clone the repo to `/opt/tracker` as `tracker`, using a GitHub **deploy key
   with write access** (repo-scoped, not a personal token), so the box can keep committing `data/`.
   Then create `.venv` and `pip install -r requirements.txt`.
4. **Secrets.** Store them in **AWS SSM Parameter Store** (SecureString). The instance role can
   read only `/tracker/*`. A wrapper exports them at run time; nothing is written to disk.
   - `GEMINI_API_KEY` (or `GROQ_API_KEY`): the AI tagging key, moved from GitHub secrets.
   - `GH_ALERT_TOKEN`: Issues: write.
   - Optional: the Discord webhook.
5. **Wrapper script** `/opt/tracker/bin/run-daily.sh` (`set -euo pipefail`):
   1. `git pull --rebase`
   2. `pytest -q`
   3. `python -m tracker.run`
   4. `python -m eval.evaluate` (if labels exist)
   5. Commit and push `data/` and `eval/`.
   6. Publish `_site/`: `aws s3 sync _site s3://<bucket> --delete` behind CloudFront, or push it to
      a `gh-pages` branch to keep GitHub Pages.
   7. If the run printed `health=alert`, call `gh issue create` (same body as `alert.md`) and exit
      non-zero.
6. **Schedule.** Use a **systemd timer**, not cron: `tracker.service` runs the wrapper as `tracker`,
   and `tracker.timer` has `OnCalendar=*-*-* 03:17:00 UTC` and `Persistent=true` (one slot is enough:
   a systemd timer fires on time, unlike GitHub's shared scheduler), so a run missed
   during a reboot happens on boot. Logs go to journald (`journalctl -u tracker`).
7. **Alerting on crashes.** Add `OnFailure=tracker-alert.service` to the service. That unit opens the
   GitHub issue or posts the webhook, so a crash, a test failure or a failed push alerts just like
   the `alert` job does today. Put the "close on recovery" step at the end of the wrapper.
8. **Watch the watcher.** A dead box can't alert on itself:
   - The wrapper pings a healthchecks.io URL (or publishes a CloudWatch metric `LastSuccess`) on
     success.
   - A CloudWatch alarm, or healthchecks.io, emails if it doesn't arrive within 26 h.
   - A CloudWatch `StatusCheckFailed` alarm covers the instance itself.
   - The CloudWatch agent ships the journal for log search.
9. **Backups.** The data is already backed up in git. If you move data off git, enable S3
   versioning or daily EBS snapshots via Data Lifecycle Manager.
10. **Cut over safely.**
    - Run the EC2 job in `--simulate`/dry-run mode, or against a test branch, for 3–5 days.
    - Diff its `data/` output against the Actions output.
    - Then remove the `schedule:` trigger from the workflow. Keep `workflow_dispatch` for manual
      runs and keep `ci.yml` for pull-request tests.
    - Rollback = put the `schedule:` line back.

---

## 6. What I'd build next with two more days

1. **Alerts on what merchants say, not just on failures.** A daily check that pings the GTM channel
   when an app gets ≥3 negative reviews in 48 h, or when a theme's share jumps week on week. The data
   already shows why: Razorpay got **five 1-star reviews on 28 Oct 2026 with near-identical wording**,
   which looks like a coordinated campaign. A near-duplicate text detector would flag that.
2. **A weekly digest.** An AI-written summary each Monday per app (top complaints with quotes, what
   changed, notable reviews), emailed or posted to Slack, linking back to the page.
3. **More apps, from config.** Add other checkout and COD apps (and GoKwik's other listings) by
   adding lines to `config.APPS`, plus a daily snapshot of each listing's rating, review count and
   pricing, to chart rating trends.
4. **Developer response tracking.** Reply rate and time-to-reply per app (`has_reply` is already
   collected). This measures how each vendor handles public complaints.
5. **A stronger evaluation.** 100 labels from two people, to measure human-vs-human agreement as the
   ceiling. Re-run on every prompt change in CI, with a per-theme confusion matrix to tune the
   definitions.
6. **Storage.** Move from JSON files to SQLite (still committed, or on S3) once history is large,
   with a small query page for ad-hoc questions.

---

## Built with AI, owned by me

The assignment expects AI tools, and I used Claude (Claude Code) to research the App Store markup
and to write and test the code, the page and these notes. I reviewed and can explain and change every
line. Each module starts with a docstring saying what it does and why. The 30 hand labels in
`eval/hand_labels.csv` are my own judgement, made on the blind labelling page.
