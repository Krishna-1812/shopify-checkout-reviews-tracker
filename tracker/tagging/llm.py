"""AI tagger for any free, OpenAI-compatible chat API.

Providers, tried in this order (the first one with a key set is used):

  gemini  Google AI Studio free tier   key: GEMINI_API_KEY  (aistudio.google.com/apikey, no card)
  groq    GroqCloud free tier          key: GROQ_API_KEY    (console.groq.com/keys, no card)

Set TAGGER_PROVIDER to force one, TAGGER_MODELS to override its model
preference list. With no key at all the tracker uses the rules tagger.

History: this first used GitHub Models through the workflow's own token, which
needed no key. GitHub retired that service on 2026-07-30; its endpoint now
answers HTTP 200 with a plain-text "OK". The first production run caught this
(every reply failed to parse, rules covered every review, no false alert), and
it is why a non-JSON reply now marks the provider unavailable at once instead of
being retried batch after batch.

Free tiers are rate limited (roughly 10-30 requests/minute), so reviews go in
batches of 15, calls are spaced out, and each review is tagged once and cached.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import requests

from .taxonomy import SENTIMENT_RULES, SENTIMENTS, THEME_KEYS, THEME_RULES, THEMES

log = logging.getLogger(__name__)

PROMPT_VERSION = "llm-v1"     # bump to re-tag everything after a prompt change
BATCH_SIZE = 15
MAX_TEXT_CHARS = 1500


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str              # OpenAI-compatible base, without /chat/completions
    key_env: str
    models: str                # comma-separated fallback preference list
    min_gap_seconds: float     # spacing between calls to stay under the free RPM
    auto_pattern: str = ""     # regex: prefer the newest listed model matching this


class ModelGone(RuntimeError):
    """This model is refused (retired, not offered to new users); try the next one."""


PROVIDERS: tuple[Provider, ...] = (
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
             "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash", 7.0,
             auto_pattern=r"^gemini-(\d+(?:\.\d+)?)-flash$"),
    Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
             "llama-3.3-70b-versatile,openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3-32b", 2.5),
)


class LLMUnavailable(RuntimeError):
    """The AI tagger cannot be used for the rest of this run (auth, quota, broken endpoint)."""


def pick_provider() -> tuple[Provider | None, str | None]:
    forced = (os.environ.get("TAGGER_PROVIDER") or "").strip().lower()
    for p in PROVIDERS:
        if forced and p.name != forced:
            continue
        key = (os.environ.get(p.key_env) or "").strip()
        if key:
            return p, key
    return None, None


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
    content = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if fenced:
        content = fenced.group(1)
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1])


def rank_models(listed: set[str], preferred: list[str], auto_pattern: str = "") -> list[str]:
    """Newest auto-matched listed models first, then listed preferences, then unlisted preferences."""
    auto: list[str] = []
    if auto_pattern and listed:
        rx = re.compile(auto_pattern)
        hits = [(m, rx.match(m)) for m in listed]
        def version(match):  # "3.8" -> (3, 8)
            return tuple(int(x) for x in match.group(1).split("."))
        auto = [m for m, mt in sorted(((m, mt) for m, mt in hits if mt), key=lambda x: version(x[1]), reverse=True)]
    ranked = auto + [m for m in preferred if m in listed] + [m for m in preferred if m not in listed]
    return list(dict.fromkeys(ranked))   # de-duplicate, keep order


class LLMTagger:
    def __init__(self, provider: Provider | None = None, key: str | None = None, models: str | None = None):
        if provider is None:
            provider, key = pick_provider()
        self.provider = provider
        self.key = key
        prefs = models or os.environ.get("TAGGER_MODELS") or (provider.models if provider else "")
        self.preferred = [m.strip() for m in prefs.split(",") if m.strip()]
        self.model: str | None = None
        self.candidates: list[str] = []
        self.calls = 0
        self.use_json_mode = True
        self._last_call = 0.0
        self.session = requests.Session()
        if key:
            self.session.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.key)

    @property
    def label(self) -> str | None:
        return f"{self.provider.name}:{self.model}" if self.provider and self.model else None

    def pick_model(self) -> str:
        """Choose the model, using the provider's live /models list.

        Model names go stale fast (the first Gemini choice here was listed but
        refused to new users within months), so for providers with an
        ``auto_pattern`` the newest matching listed model comes first, then the
        hand-written preferences. If a model is refused, ``next_model`` moves on.
        """
        if self.model:
            return self.model
        listed: set[str] = set()
        try:
            resp = self.session.get(f"{self.provider.base_url}/models", timeout=20)
            if resp.status_code in (401, 403):
                raise LLMUnavailable(f"{self.provider.name} rejected the key (HTTP {resp.status_code})")
            if resp.status_code == 200:
                listed = {str(m.get("id", "")).removeprefix("models/") for m in resp.json().get("data", [])}
        except (requests.RequestException, ValueError, AttributeError) as exc:
            log.warning("could not list %s models: %s", self.provider.name, exc)
        self.candidates = rank_models(listed, self.preferred, self.provider.auto_pattern)
        if listed and not any(c in listed for c in self.candidates):
            log.warning("none of %s offered by %s; it lists e.g. %s",
                        self.preferred, self.provider.name, sorted(listed)[:12])
        self.model = self.candidates[0]
        log.info("%s model candidates: %s", self.provider.name, self.candidates[:5])
        return self.model

    def next_model(self, reason: str) -> str:
        log.warning("%s refused model %s (%s); trying the next one", self.provider.name, self.model, reason[:160])
        i = self.candidates.index(self.model) if self.model in self.candidates else -1
        if i + 1 >= len(self.candidates):
            raise LLMUnavailable(f"{self.provider.name}: no usable model ({reason[:200]})")
        self.model = self.candidates[i + 1]
        return self.model

    def _post(self, payload: dict) -> dict:
        url = f"{self.provider.base_url}/chat/completions"
        for attempt in range(1, 4):
            gap = time.monotonic() - self._last_call
            if gap < self.provider.min_gap_seconds:
                time.sleep(self.provider.min_gap_seconds - gap)
            self._last_call = time.monotonic()
            try:
                resp = self.session.post(url, json=payload, timeout=90)
            except requests.RequestException as exc:
                log.warning("%s network error: %s (attempt %d)", self.provider.name, exc, attempt)
                time.sleep(5 * attempt)
                continue
            self.calls += 1
            body = resp.text[:300]
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    data["choices"][0]["message"]["content"]
                    return data
                except (ValueError, KeyError, IndexError, TypeError):
                    # e.g. a retired endpoint answering 200 "OK": retrying will not help
                    raise LLMUnavailable(f"{self.provider.name} returned a non-API reply: {body!r}")
            if resp.status_code in (401, 403):
                raise LLMUnavailable(f"{self.provider.name} HTTP {resp.status_code}: {body}")
            if resp.status_code in (404, 410) or (resp.status_code == 400 and "model" in body.lower()
                                                   and ("not found" in body.lower() or "no longer" in body.lower())):
                raise ModelGone(f"HTTP {resp.status_code}: {body}")
            if resp.status_code == 429:
                wait = float(resp.headers.get("retry-after") or 30 * attempt)
                if wait > 120 or "per day" in body.lower() or "daily" in body.lower():
                    raise LLMUnavailable(f"{self.provider.name} quota used up: {body}")
                log.warning("rate limited, sleeping %.0fs", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 400 and "response_format" in body and payload.get("response_format"):
                payload = {k: v for k, v in payload.items() if k != "response_format"}
                self.use_json_mode = False
                continue
            log.warning("%s HTTP %s: %s (attempt %d)", self.provider.name, resp.status_code, body, attempt)
            time.sleep(5 * attempt)
        raise RuntimeError(f"{self.provider.name} failed 3 times for this batch")

    def tag_batch(self, batch: list[dict], app_names: dict[str, str]) -> dict[str, dict]:
        """Return {review_id: tags} for the reviews the model labelled validly."""
        model = self.pick_model()
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": _user_prompt(batch, app_names)},
            ],
        }
        if self.use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        while True:
            try:
                data = self._post(payload)
                break
            except ModelGone as exc:
                payload["model"] = self.next_model(str(exc))   # raises LLMUnavailable when none left
        parsed = _parse_json(data["choices"][0]["message"]["content"])
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
                    "tag_model": self.label,
                    "tag_reason": str(item.get("reason", ""))[:140],
                }
        return out
