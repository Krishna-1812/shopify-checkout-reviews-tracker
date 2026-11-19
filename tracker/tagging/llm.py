"""AI tagger using GitHub Models (free tier).

Why GitHub Models: inside GitHub Actions the workflow's built-in
``GITHUB_TOKEN`` can call it once the workflow declares
``permissions: models: read``. No signup, no API key, no secret to leak or
rotate. Locally you can use a personal access token with the "models"
permission in ``GITHUB_MODELS_TOKEN``.

The free tier is rate limited (roughly 15 requests/minute and ~150/day for
the small models), so reviews are sent in batches of 15 and each review is
tagged once and cached in data/reviews.json. Only new or edited reviews are
sent on a normal day.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .taxonomy import SENTIMENT_RULES, SENTIMENTS, THEME_KEYS, THEME_RULES, THEMES

log = logging.getLogger(__name__)

ENDPOINT = "https://models.github.ai/inference/chat/completions"
CATALOG = "https://models.github.ai/catalog/models"
PROMPT_VERSION = "llm-v1"     # bump to re-tag everything after a prompt change
DEFAULT_MODELS = "openai/gpt-4.1-mini,openai/gpt-4o-mini,openai/gpt-4.1-nano"
BATCH_SIZE = 15
MAX_TEXT_CHARS = 1500
MIN_SECONDS_BETWEEN_CALLS = 4.5   # stay under ~15 requests/minute


class LLMUnavailable(RuntimeError):
    """The AI tagger cannot be used for the rest of this run (auth, quota)."""


def _token() -> str | None:
    return os.environ.get("GITHUB_MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")


def system_prompt() -> str:
    themes = "\n".join(f"{k:12s} - {desc}" for k, (_, desc) in THEMES.items())
    return f"""You label merchant reviews of Shopify checkout apps used by Indian D2C brands.
For each review return a sentiment and ONE main theme.

SENTIMENT (one of: {", ".join(SENTIMENTS)})
{SENTIMENT_RULES}

THEME (one of: {", ".join(THEME_KEYS)})
{themes}

{THEME_RULES}

Reviews can be terse, Hinglish or misspelt ("sapport", "wrost"); read them as a merchant would.
Reply with JSON only, exactly:
{{"results": [{{"id": "<id>", "sentiment": "...", "theme": "...", "reason": "<max 12 words>"}}]}}
Return one result per input review, same ids, nothing else."""


def _user_prompt(batch: list[dict], app_names: dict[str, str]) -> str:
    items = [
        {
            "id": r["id"],
            "app": app_names.get(r["app"], r["app"]),
            "stars": r["rating"],
            "text": (r.get("text") or "")[:MAX_TEXT_CHARS],
        }
        for r in batch
    ]
    return "Label these reviews:\n" + json.dumps(items, ensure_ascii=False)


def _parse_json(content: str) -> dict:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if fenced:
        content = fenced.group(1)
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1])


class LLMTagger:
    def __init__(self, token: str | None = None, models: str | None = None):
        self.token = token or _token()
        self.preferred = [
            m.strip() for m in (models or os.environ.get("TAGGER_MODELS") or DEFAULT_MODELS).split(",")
            if m.strip()
        ]
        self.model: str | None = None
        self.calls = 0
        self._last_call = 0.0
        self.session = requests.Session()
        if self.token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            })

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def pick_model(self) -> str:
        """First preferred model that the catalog lists (falls back to the first preference)."""
        if self.model:
            return self.model
        try:
            resp = self.session.get(CATALOG, timeout=20)
            if resp.status_code == 200:
                ids = {m.get("id") for m in resp.json()}
                for m in self.preferred:
                    if m in ids:
                        self.model = m
                        break
                if not self.model:
                    log.warning("none of %s in catalog; catalog has e.g. %s",
                                self.preferred, sorted(i for i in ids if i)[:10])
        except (requests.RequestException, ValueError) as exc:
            log.warning("could not read model catalog: %s", exc)
        self.model = self.model or self.preferred[0]
        return self.model

    def _post(self, payload: dict) -> dict:
        for attempt in range(1, 4):
            gap = time.monotonic() - self._last_call
            if gap < MIN_SECONDS_BETWEEN_CALLS:
                time.sleep(MIN_SECONDS_BETWEEN_CALLS - gap)
            self._last_call = time.monotonic()
            try:
                resp = self.session.post(ENDPOINT, json=payload, timeout=90)
            except requests.RequestException as exc:
                log.warning("models API network error: %s (attempt %d)", exc, attempt)
                time.sleep(5 * attempt)
                continue
            self.calls += 1
            if resp.status_code == 200:
                return resp.json()
            body = resp.text[:300]
            if resp.status_code in (401, 403):
                raise LLMUnavailable(f"HTTP {resp.status_code}: {body}")
            if resp.status_code == 429:
                wait = float(resp.headers.get("retry-after") or 60)
                if wait > 90:   # daily quota, not a per-minute burst
                    raise LLMUnavailable(f"rate limited for {wait:.0f}s: {body}")
                log.warning("rate limited, sleeping %.0fs", wait)
                time.sleep(wait)
                continue
            if resp.status_code in (404, 400) and "model" in body.lower():
                raise LLMUnavailable(f"model {self.model} unavailable: {body}")
            log.warning("models API HTTP %s: %s (attempt %d)", resp.status_code, body, attempt)
            time.sleep(5 * attempt)
        raise RuntimeError("models API failed 3 times for this batch")

    def tag_batch(self, batch: list[dict], app_names: dict[str, str]) -> dict[str, dict]:
        """Return {review_id: tags} for the reviews the model labelled validly."""
        model = self.pick_model()
        data = self._post({
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": _user_prompt(batch, app_names)},
            ],
        })
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json(content)
        wanted = {r["id"] for r in batch}
        out: dict[str, dict] = {}
        for item in parsed.get("results", []):
            rid = str(item.get("id", ""))
            sentiment = str(item.get("sentiment", "")).strip().lower()
            theme = str(item.get("theme", "")).strip().lower().replace("/", "_").replace(" ", "_")
            if rid in wanted and sentiment in SENTIMENTS and theme in THEME_KEYS:
                out[rid] = {
                    "sentiment": sentiment,
                    "theme": theme,
                    "tag_source": "llm",
                    "tag_model": model,
                    "tag_reason": str(item.get("reason", ""))[:140],
                }
        return out
