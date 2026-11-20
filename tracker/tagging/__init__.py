"""Decide which reviews need (re)tagging and tag them.

Order of preference for every review that has text:
  1. AI tag from a free OpenAI-compatible API (tag_source="llm", see llm.py)
  2. rules tag (tag_source="rules") when no AI key is set, or the AI fails

Reviews with no text are tagged from the stars alone (sentiment) and get the
"general" theme; there is nothing for a model to read.

A review keeps its tag until its text changes (store.merge clears tags on
edit) or PROMPT_VERSION is bumped. A rules tag is upgraded to an AI tag on a
later run once the AI is reachable again.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass

from .. import config
from . import llm, rules
from .taxonomy import rating_prior

log = logging.getLogger(__name__)

NO_TEXT_VERSION = "no-text-v1"
MAX_LLM_REVIEWS_PER_RUN = 600   # covers a full backfill; normal days send a handful
# Free tiers throttle hard. AI tagging stops after this many seconds so a slow
# backfill can never push the job into its timeout (which would be a false
# alert); whatever is left gets rules tags now and is upgraded on later runs.
AI_TIME_BUDGET_SECONDS = float(os.environ.get("AI_TIME_BUDGET_SECONDS", 480))


@dataclass
class TagStats:
    llm_tagged: int = 0
    rules_tagged: int = 0
    no_text_tagged: int = 0
    pending_upgrade: int = 0     # still rules-tagged; the AI retries them next run
    llm_calls: int = 0
    llm_model: str | None = None
    llm_status: str = "not needed"  # ok / not needed / disabled (...) / unavailable: ...

    def as_dict(self) -> dict:
        return asdict(self)


def _has_text(r: dict) -> bool:
    return bool((r.get("text") or "").strip())


def _is_current(r: dict) -> bool:
    if not _has_text(r):
        return r.get("tag_version") == NO_TEXT_VERSION
    return r.get("tag_source") == "llm" and r.get("tag_version") == llm.PROMPT_VERSION


def tag_all(store: dict[str, dict], tagger: llm.LLMTagger | None = None) -> TagStats:
    stats = TagStats()
    app_names = {a.slug: a.vendor for a in config.APPS}

    # 1. no-text reviews: deterministic, nothing for a model to read
    for r in store.values():
        if not _has_text(r) and r.get("tag_version") != NO_TEXT_VERSION:
            r.update(sentiment=rating_prior(int(r["rating"])), theme="general",
                     tag_source="stars", tag_model=NO_TEXT_VERSION,
                     tag_version=NO_TEXT_VERSION, tag_reason="no text; from stars")
            stats.no_text_tagged += 1

    # 2. reviews with text that are untagged, rules-tagged or tagged by an old prompt.
    #    Untagged first, then newest first, so today's reviews always get the AI.
    queue = [r for r in store.values() if not _is_current(r)]
    queue.sort(key=lambda r: r["date"], reverse=True)
    queue.sort(key=lambda r: r.get("sentiment") is not None)

    use_llm = os.environ.get("TAGGER", "auto").lower() != "rules"
    if tagger is None and use_llm:
        tagger = llm.LLMTagger()
    if queue and not use_llm:
        stats.llm_status = "disabled (TAGGER=rules)"
    elif queue and not tagger.configured:
        stats.llm_status = "off (no AI key set: add GEMINI_API_KEY or GROQ_API_KEY)"

    done: set[str] = set()
    if queue and use_llm and tagger.configured:
        stats.llm_status = "ok"
        todo = queue[:MAX_LLM_REVIEWS_PER_RUN]
        deadline = time.monotonic() + AI_TIME_BUDGET_SECONDS
        for i in range(0, len(todo), llm.BATCH_SIZE):
            if time.monotonic() > deadline:
                stats.llm_status = "ok (time budget reached; the rest continue next run)"
                log.info("AI time budget reached after %d reviews", stats.llm_tagged)
                break
            batch = todo[i:i + llm.BATCH_SIZE]
            try:
                tags = tagger.tag_batch(batch, app_names)
            except llm.LLMUnavailable as exc:
                stats.llm_status = f"unavailable: {exc}"[:300]
                log.warning("AI tagger unavailable, falling back to rules: %s", exc)
                break
            except Exception as exc:  # bad JSON, repeated 5xx: skip this batch only
                log.warning("AI batch failed (%s); rules will cover it", exc)
                stats.llm_status = f"partial: {type(exc).__name__}"
                continue
            for r in batch:
                if r["id"] in tags:
                    r.update(tags[r["id"]], tag_version=llm.PROMPT_VERSION)
                    done.add(r["id"])
                    stats.llm_tagged += 1
        stats.llm_calls = tagger.calls
        stats.llm_model = tagger.label

    # 3. anything still untagged gets a rules tag, so the page never has gaps
    for r in queue:
        if r["id"] in done:
            continue
        if r.get("sentiment") is None:
            r.update(rules.tag(r), tag_version=rules.VERSION)
            stats.rules_tagged += 1
        stats.pending_upgrade += 1
    return stats
