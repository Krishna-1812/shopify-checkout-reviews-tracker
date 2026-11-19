import json

from eval import evaluate
from tracker import store


def test_metric_and_confusion():
    m = evaluate.metric([("positive", "positive"), ("negative", "neutral")])
    assert m == {"agree": 1, "n": 2, "pct": 50.0}
    c = evaluate.confusion([("negative", "neutral")], ("positive", "neutral", "negative"))
    assert c["negative"]["neutral"] == 1 and c["positive"]["positive"] == 0


def test_end_to_end_scoring(tmp_path, monkeypatch):
    reviews = {
        "1": {"id": "1", "app": "kwikcheckout", "rating": 5, "text": "Great support by Nikita", "url": "u1",
              "sentiment": "positive", "theme": "support", "tag_source": "llm", "tag_reason": "thanks agent"},
        "2": {"id": "2", "app": "razorpay-checkout", "rating": 1, "text": "KYC pending for weeks, nobody replies",
              "url": "u2", "sentiment": "negative", "theme": "support", "tag_source": "llm", "tag_reason": "slow team"},
    }
    rfile = tmp_path / "reviews.json"
    store.save_reviews(reviews, rfile)
    original = store.load_reviews
    monkeypatch.setattr(evaluate.store, "load_reviews", lambda: original(rfile))
    labels = tmp_path / "hand_labels.csv"
    labels.write_text("id,human_sentiment,human_theme,note\n1,positive,support,\n2,negative,setup,blocked onboarding\n3,,,\n")
    monkeypatch.setattr(evaluate, "LABELS", labels)
    monkeypatch.setattr(evaluate, "REPORT_JSON", tmp_path / "report.json")
    monkeypatch.setattr(evaluate, "REPORT_MD", tmp_path / "REPORT.md")
    assert evaluate.main() == 0
    rep = json.loads((tmp_path / "report.json").read_text())
    assert rep["n"] == 2                                   # blank row skipped
    assert rep["tracker"]["sentiment"]["pct"] == 100.0
    assert rep["tracker"]["theme"] == {"agree": 1, "n": 2, "pct": 50.0}
    assert [m["id"] for m in rep["highlight"]] == ["2"]
    assert "Setup & onboarding" in rep["highlight"][0]["note"]
    assert "blocked onboarding" in rep["highlight"][0]["note"]
    assert "Two cases it got wrong" in (tmp_path / "REPORT.md").read_text()
