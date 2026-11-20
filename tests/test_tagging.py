import pytest

from tracker import tagging
from tracker.tagging import llm, rules


def rv(rid, text, rating=5, date="2026-11-01"):
    return {"id": rid, "app": "kwikcheckout", "rating": rating, "date": date, "text": text}


# --- rules -----------------------------------------------------------------
@pytest.mark.parametrize("text,rating,sentiment,theme", [
    ("Customer support is very good - Nikita", 5, "positive", "support"),
    ("KYC pending for 2 months, cannot go live", 1, "negative", "setup"),
    ("App not working. Giving Error.", 1, "negative", "reliability"),
    ("Helped us OTP verify COD orders and avoid fake orders", 5, "positive", "cod_rto"),
    ("Very costly, 2.5% fees", 3, "negative", "pricing"),
    ("significant increase in our conversion rate", 5, "positive", "conversion"),
    ("Nice", 5, "positive", "general"),
    ("", 4, "positive", "general"),
])
def test_rules_examples(text, rating, sentiment, theme):
    t = rules.tag(rv("1", text, rating))
    assert (t["sentiment"], t["theme"]) == (sentiment, theme)


def test_rules_five_stars_but_complaint_is_neutral():
    text = "I talked in chat for 1 hour but didn't get any solution, very poor and unable to fix"
    assert rules.tag_sentiment(text, 5) == "neutral"


# --- llm response handling ---------------------------------------------------
def test_parse_json_handles_code_fences():
    assert llm._parse_json('```json\n{"results": []}\n```') == {"results": []}
    assert llm._parse_json('Sure! {"results": [1]}') == {"results": [1]}


def test_tag_batch_drops_invalid_labels(monkeypatch):
    t = llm.LLMTagger(provider=llm.PROVIDERS[0], key="x", models="m")
    t.model = "m"
    content = ('{"results": [{"id": "1", "sentiment": "Positive", "theme": "support", "reason": "thanks agent"},'
               '{"id": "2", "sentiment": "angry", "theme": "support"},'
               '{"id": "3", "sentiment": "negative", "theme": "COD/RTO"},'
               '{"id": "99", "sentiment": "negative", "theme": "setup"}]}')
    monkeypatch.setattr(t, "_post", lambda payload: {"choices": [{"message": {"content": content}}]})
    out = t.tag_batch([rv("1", "a"), rv("2", "b"), rv("3", "c")], {})
    assert set(out) == {"1", "3"}                     # bad sentiment and unknown id dropped
    assert out["1"]["sentiment"] == "positive"
    assert out["3"]["theme"] == "cod_rto"             # "COD/RTO" normalised


def test_prompt_lists_every_theme():
    p = llm.system_prompt()
    for key in tagging.taxonomy.THEME_KEYS:
        assert key in p


# --- orchestration -------------------------------------------------------------
class FakeTagger:
    configured = True

    def __init__(self, fail=None):
        self.fail, self.calls, self.model, self.seen = fail, 0, "fake-model", []
        self.label = "fake:fake-model"

    def tag_batch(self, batch, app_names):
        self.calls += 1
        self.seen += [r["id"] for r in batch]
        if self.fail:
            raise self.fail
        return {r["id"]: {"sentiment": "negative", "theme": "support", "tag_source": "llm",
                          "tag_model": "fake-model", "tag_reason": "x"} for r in batch}


def test_tag_all_uses_ai_and_caches(monkeypatch):
    monkeypatch.setenv("TAGGER", "auto")
    store = {"1": rv("1", "support slow"), "2": rv("2", "", rating=5)}
    t = FakeTagger()
    s = tagging.tag_all(store, t)
    assert (s.llm_tagged, s.no_text_tagged, s.rules_tagged) == (1, 1, 0)
    assert store["1"]["tag_source"] == "llm" and store["1"]["tag_version"] == llm.PROMPT_VERSION
    assert store["2"]["sentiment"] == "positive" and store["2"]["theme"] == "general"
    assert t.seen == ["1"]                      # no-text review never sent to the model
    t2 = FakeTagger()
    tagging.tag_all(store, t2)
    assert t2.calls == 0                        # already tagged: nothing re-sent


def test_tag_all_falls_back_to_rules_when_ai_unavailable(monkeypatch):
    monkeypatch.setenv("TAGGER", "auto")
    store = {"1": rv("1", "App not working. Giving Error.", 1)}
    s = tagging.tag_all(store, FakeTagger(fail=llm.LLMUnavailable("HTTP 429")))
    assert store["1"]["tag_source"] == "rules" and store["1"]["theme"] == "reliability"
    assert s.rules_tagged == 1 and s.pending_upgrade == 1
    assert s.llm_status.startswith("unavailable")
    # next run, AI is back: the rules tag is upgraded
    tagging.tag_all(store, FakeTagger())
    assert store["1"]["tag_source"] == "llm"


def test_edited_review_is_retagged(monkeypatch):
    monkeypatch.setenv("TAGGER", "auto")
    store = {"1": rv("1", "good")}
    tagging.tag_all(store, FakeTagger())
    for k in ("sentiment", "theme", "tag_source", "tag_version"):
        store["1"].pop(k)                       # what store.merge does on an edit
    t = FakeTagger()
    tagging.tag_all(store, t)
    assert t.seen == ["1"]


def test_tagger_rules_env_never_calls_ai(monkeypatch):
    monkeypatch.setenv("TAGGER", "rules")
    store = {"1": rv("1", "support slow", 1)}
    s = tagging.tag_all(store, FakeTagger())
    assert store["1"]["tag_source"] == "rules" and s.llm_calls == 0


# --- provider selection and broken endpoints -------------------------------------
def test_provider_picked_from_whichever_key_is_set(monkeypatch):
    for p in llm.PROVIDERS:
        monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.delenv("TAGGER_PROVIDER", raising=False)
    assert llm.pick_provider() == (None, None)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert llm.pick_provider()[0].name == "groq"
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert llm.pick_provider()[0].name == "gemini"          # first in order wins
    monkeypatch.setenv("TAGGER_PROVIDER", "groq")
    assert llm.pick_provider()[0].name == "groq"            # unless forced


class FakeResp:
    def __init__(self, status, text, headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def json(self):
        import json as _j
        return _j.loads(self.text)


def test_non_api_reply_marks_provider_unavailable_at_once(monkeypatch):
    """The retired GitHub Models endpoint answered 200 'OK' to everything."""
    t = llm.LLMTagger(provider=llm.Provider("x", "https://x", "X_KEY", "m", 0), key="k")
    t.model = "m"
    monkeypatch.setattr(t.session, "post", lambda *a, **kw: FakeResp(200, "OK"))
    with pytest.raises(llm.LLMUnavailable):
        t.tag_batch([rv("1", "a")], {})
    assert t.calls == 1


def test_bad_key_and_gone_model_are_unavailable(monkeypatch):
    t = llm.LLMTagger(provider=llm.Provider("x", "https://x", "X_KEY", "m", 0), key="k")
    t.model = "m"
    for status in (401, 403, 404, 410):
        monkeypatch.setattr(t.session, "post", lambda *a, s=status, **kw: FakeResp(s, "nope"))
        with pytest.raises(llm.LLMUnavailable):
            t.tag_batch([rv("1", "a")], {})


def test_json_mode_dropped_if_provider_rejects_it(monkeypatch):
    t = llm.LLMTagger(provider=llm.Provider("x", "https://x", "X_KEY", "m", 0), key="k")
    t.model = "m"
    sent = []
    ok = '{"choices": [{"message": {"content": "{\\"results\\": [{\\"id\\": \\"1\\", \\"sentiment\\": \\"positive\\", \\"theme\\": \\"general\\"}]}"}}]}'

    def post(url, json, timeout):
        sent.append("response_format" in json)
        return FakeResp(400, "response_format not supported") if len(sent) == 1 else FakeResp(200, ok)
    monkeypatch.setattr(t.session, "post", post)
    out = t.tag_batch([rv("1", "a")], {})
    assert sent == [True, False] and out["1"]["sentiment"] == "positive"
    assert out["1"]["tag_model"] == "x:m"


def test_missing_key_is_not_a_health_warning():
    from tracker import health
    r = {"apps": {}, "tagging": {"llm_status": "off (no AI key set: add GEMINI_API_KEY or GROQ_API_KEY)"}}
    assert health.evaluate(r) == []


def test_rank_models_prefers_newest_listed_flash():
    listed = {"gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.10-flash", "gemini-3.8-flash-lite",
              "gemini-4-pro", "text-embedding-005"}
    ranked = llm.rank_models(listed, ["gemini-2.5-flash", "gemini-2.0-flash"], llm.PROVIDERS[0].auto_pattern)
    assert ranked[:3] == ["gemini-3.10-flash", "gemini-3.8-flash", "gemini-2.5-flash"]
    assert ranked[-1] == "gemini-2.0-flash"          # unlisted preference kept as last resort
    assert llm.rank_models(set(), ["a", "b"]) == ["a", "b"]


def test_refused_model_falls_through_to_next(monkeypatch):
    """Gemini listed gemini-2.5-flash but answered 404 'no longer available to new users'."""
    t = llm.LLMTagger(provider=llm.Provider("x", "https://x", "X_KEY", "old", 0), key="k")
    t.model, t.candidates = "old", ["old", "new"]
    ok = ('{"choices": [{"message": {"content": "{\\"results\\": [{\\"id\\": \\"1\\", '
          '\\"sentiment\\": \\"negative\\", \\"theme\\": \\"support\\"}]}"}}]}')
    models_tried = []

    def post(url, json, timeout):
        models_tried.append(json["model"])
        if json["model"] == "old":
            return FakeResp(404, "This model models/old is no longer available to new users")
        return FakeResp(200, ok)
    monkeypatch.setattr(t.session, "post", post)
    out = t.tag_batch([rv("1", "slow support")], {})
    assert models_tried == ["old", "new"]
    assert out["1"]["tag_model"] == "x:new" and t.model == "new"


def test_ai_time_budget_stops_cleanly(monkeypatch):
    monkeypatch.setenv("TAGGER", "auto")
    monkeypatch.setattr(tagging, "AI_TIME_BUDGET_SECONDS", -1)   # already past the deadline
    store = {str(i): rv(str(i), "support slow", 1) for i in range(3)}
    t = FakeTagger()
    s = tagging.tag_all(store, t)
    assert t.calls == 0 and s.rules_tagged == 3 and s.pending_upgrade == 3
    assert s.llm_status.startswith("ok (time budget")
