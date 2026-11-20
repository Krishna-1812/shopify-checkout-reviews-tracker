"""Score the tracker's tags against the hand labels.

    python -m eval.evaluate      # reads eval/hand_labels.csv, writes eval/report.json + eval/REPORT.md

"Agreement" = share of the hand-labelled reviews where the tracker's stored
tag equals the human's tag. Reported for sentiment, theme and both together,
plus the same numbers for the keyword-rules tagger alone as a baseline, so
it is visible what the AI adds.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import date

from tracker import config, store
from tracker.tagging import rules
from tracker.tagging.taxonomy import SENTIMENTS, THEME_KEYS, THEMES

LABELS = config.EVAL_DIR / "hand_labels.csv"
REPORT_JSON = config.EVAL_DIR / "report.json"
REPORT_MD = config.EVAL_DIR / "REPORT.md"
LABEL_PAGE = "https://krishna-1812.github.io/shopify-checkout-reviews-tracker/label.html"


def load_labels() -> list[dict]:
    with LABELS.open(encoding="utf-8", newline="") as fh:
        rows = [
            {k: (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]
    good = [r for r in rows if r.get("human_sentiment") in SENTIMENTS and r.get("human_theme") in THEME_KEYS]
    bad = [r.get("id") for r in rows if r not in good]
    if bad:
        print(f"skipping {len(bad)} rows with a missing/unknown label: {bad}")
    return good


def metric(pairs: list[tuple[str, str]]) -> dict:
    agree = sum(1 for a, b in pairs if a == b)
    return {"agree": agree, "n": len(pairs), "pct": round(100 * agree / len(pairs), 1) if pairs else 0.0}


def confusion(pairs: list[tuple[str, str]], keys) -> dict:
    """{human_label: {tracker_label: count}}"""
    c = {h: {t: 0 for t in keys} for h in keys}
    for h, t in pairs:
        c[h][t] += 1
    return c


SOURCE_NAME = {"llm": "AI", "rules": "keyword rules", "stars": "star rating"}


def explain(miss: dict) -> str:
    """One-line reading of why the tracker disagreed (REPORT.md and the page)."""
    parts = []
    h, t = miss["human"], miss["tracker"]
    if h["sentiment"] != t["sentiment"]:
        parts.append(f"sentiment {t['sentiment']} instead of {h['sentiment']}")
    if h["theme"] != t["theme"]:
        parts.append(f"theme {THEMES[t['theme']][0]} instead of {THEMES[h['theme']][0]}")
    reason = (t.get("reason") or "").strip().rstrip(".")
    why = f" Its reason: “{reason}”." if reason else ""
    note = f" My note: {miss['note']}" if miss.get("note") else ""
    return f"Got the {' and the '.join(parts)} ({SOURCE_NAME.get(t['source'], t['source'])} tag).{why}{note}"


def labels_fingerprint() -> str:
    return hashlib.sha256(LABELS.read_bytes()).hexdigest()[:16]


def labelled_on(fingerprint: str) -> str:
    """The date the labels were made, not the date of the latest scoring run:
    kept from the previous report while hand_labels.csv is unchanged."""
    try:
        old = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
        if old.get("labels_fingerprint") == fingerprint and old.get("labelled_on"):
            return old["labelled_on"]
    except (OSError, ValueError):
        pass
    return date.today().isoformat()


def pick_highlights(misses: list[dict]) -> list[dict]:
    """Two instructive misses: prefer one sentiment miss and one theme miss, AI tags first."""
    def rank(m):  # AI misses first, then longer texts (more to learn from)
        return (m["tracker"]["source"] != "llm", -len(m["text"]))
    sent = sorted([m for m in misses if m["human"]["sentiment"] != m["tracker"]["sentiment"]], key=rank)
    theme = sorted([m for m in misses if m["human"]["theme"] != m["tracker"]["theme"]], key=rank)
    out = []
    for group in (sent, theme, sorted(misses, key=rank)):
        for m in group:
            if m not in out:
                out.append(m)
                break
        if len(out) == 2:
            break
    return out


def main() -> int:
    labels = load_labels()
    if not labels:
        print("no usable hand labels yet")
        return 0
    reviews = store.load_reviews()
    rows, misses = [], []
    for lab in labels:
        r = reviews.get(lab["id"])
        if r is None:
            print(f"review {lab['id']} not in data/reviews.json, skipped")
            continue
        base = rules.tag(r)
        row = {
            "id": r["id"], "app": r["app"], "rating": r["rating"], "text": r["text"], "url": r["url"],
            "note": lab.get("note", ""),
            "human": {"sentiment": lab["human_sentiment"], "theme": lab["human_theme"]},
            "tracker": {"sentiment": r.get("sentiment"), "theme": r.get("theme"),
                        "source": r.get("tag_source"), "model": r.get("tag_model"), "reason": r.get("tag_reason", "")},
            "rules": {"sentiment": base["sentiment"], "theme": base["theme"]},
        }
        rows.append(row)
        if row["human"] != {k: row["tracker"][k] for k in ("sentiment", "theme")}:
            misses.append(row)
    for m in misses:
        m["explanation"] = explain(m)

    def pairs(who: str, field: str):
        return [(x["human"][field], x[who][field]) for x in rows]

    both = [(1, int(x["human"]["sentiment"] == x["tracker"]["sentiment"] and x["human"]["theme"] == x["tracker"]["theme"])) for x in rows]
    rboth = [(1, int(x["human"]["sentiment"] == x["rules"]["sentiment"] and x["human"]["theme"] == x["rules"]["theme"])) for x in rows]
    sources = Counter(x["tracker"]["source"] for x in rows)
    highlight = pick_highlights(misses)
    fingerprint = labels_fingerprint()
    report = {
        "labelled_on": labelled_on(fingerprint),
        "labels_fingerprint": fingerprint,
        "n": len(rows),
        "tracker_sources": ", ".join(f"{v} {SOURCE_NAME.get(k, k)}" for k, v in sources.most_common()),
        "tracker": {"sentiment": metric(pairs("tracker", "sentiment")), "theme": metric(pairs("tracker", "theme")),
                    "both": metric(both)},
        "rules_baseline": {"sentiment": metric(pairs("rules", "sentiment")), "theme": metric(pairs("rules", "theme")),
                           "both": metric(rboth)},
        "sentiment_confusion": confusion(pairs("tracker", "sentiment"), SENTIMENTS),
        "theme_confusion": confusion(pairs("tracker", "theme"), THEME_KEYS),
        "highlight": highlight,     # each keeps the labeller's own "note" plus the "explanation"
        "misses": misses,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REPORT_MD.write_text(markdown(report), encoding="utf-8")
    t = report["tracker"]
    print(f"n={report['n']}  sentiment {t['sentiment']['pct']}%  theme {t['theme']['pct']}%  both {t['both']['pct']}%")
    return 0


def markdown(rep: dict) -> str:
    t, b = rep["tracker"], rep["rules_baseline"]
    L = [
        "# Tagging evaluation",
        "",
        f"{rep['n']} reviews were tagged by hand on the [labelling page]({LABEL_PAGE}) on {rep['labelled_on']}, without seeing the "
        f"tracker's tags, and compared with the tags the tracker stored ({rep['tracker_sources']}). "
        "The sample is stratified by app and star band (see `eval/sample.py`) so it over-represents the hard, "
        "negative cases; rating-only reviews are excluded because their tag is a fixed rule.",
        "",
        "| | Tracker (as stored) | Keyword rules alone |",
        "|---|---|---|",
        f"| Sentiment agrees | **{t['sentiment']['pct']}%** ({t['sentiment']['agree']}/{rep['n']}) | {b['sentiment']['pct']}% ({b['sentiment']['agree']}/{rep['n']}) |",
        f"| Theme agrees | **{t['theme']['pct']}%** ({t['theme']['agree']}/{rep['n']}) | {b['theme']['pct']}% ({b['theme']['agree']}/{rep['n']}) |",
        f"| Both agree | **{t['both']['pct']}%** ({t['both']['agree']}/{rep['n']}) | {b['both']['pct']}% ({b['both']['agree']}/{rep['n']}) |",
        "",
        "## Two cases it got wrong" if len(rep["highlight"]) >= 2 else
        ("## The case it got wrong" if rep["highlight"] else "## No disagreements in this sample"),
        "",
    ]
    for m in rep["highlight"]:
        L += [f"**{config.APPS_BY_SLUG[m['app']].vendor}, {m['rating']}★** ([review]({m['url']}))", "",
              "> " + m["text"].replace("\n", "\n> "), "",
              f"- Me: {m['human']['sentiment']} / {THEMES[m['human']['theme']][0]}",
              f"- Tracker: {m['tracker']['sentiment']} / {THEMES[m['tracker']['theme']][0]}",
              f"- {m['explanation']}", ""]
    L += ["## Sentiment confusion (rows = me, columns = tracker)", "",
          "| me \\ tracker | " + " | ".join(SENTIMENTS) + " |", "|---" * (len(SENTIMENTS) + 1) + "|"]
    for h in SENTIMENTS:
        L.append(f"| {h} | " + " | ".join(str(rep["sentiment_confusion"][h][x]) for x in SENTIMENTS) + " |")
    L += ["", "## Every disagreement", "", "| Review | Stars | Me | Tracker | Rules |", "|---|---|---|---|---|"]
    for m in rep["misses"]:
        text = m["text"][:90].replace("|", "/").replace("\n", " ") + ("…" if len(m["text"]) > 90 else "")
        L.append(f"| [{text}]({m['url']}) | {m['rating']} | {m['human']['sentiment']} / {m['human']['theme']} | "
                 f"{m['tracker']['sentiment']} / {m['tracker']['theme']} | {m['rules']['sentiment']} / {m['rules']['theme']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
