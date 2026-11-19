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
    t = llm.LLMTagger(token="x", models="m")
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
