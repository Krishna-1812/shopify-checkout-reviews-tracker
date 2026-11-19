from tracker import store
from tracker.scrape import content_hash


def review(rid="1", text="good app", rating=5, date="2026-11-01"):
    return {"id": rid, "app": "kwikcheckout", "url": f"u/{rid}", "rating": rating, "date": date,
            "store": "S", "country": "India", "usage": "", "text": text, "has_reply": False,
            "content_hash": content_hash(text, rating)}


def test_review_seen_on_several_days_is_stored_once():
    st = {}
    assert store.merge(st, [review()], "2026-11-01").new == 1
    s2 = store.merge(st, [review()], "2026-11-02")
    s3 = store.merge(st, [review()], "2026-11-03")
    assert (s2.new, s2.unchanged, s3.new) == (0, 1, 0)
    assert len(st) == 1
    assert st["1"]["first_seen"] == "2026-11-01"
    assert st["1"]["last_seen"] == "2026-11-03"


def test_duplicate_within_one_crawl_counts_once():
    st = {}
    stats = store.merge(st, [review(), review()], "2026-11-01")
    assert stats.new == 1 and len(st) == 1


def test_edited_review_replaces_text_and_clears_tags():
    st = {}
    store.merge(st, [review()], "2026-11-01")
    st["1"].update(sentiment="positive", theme="general", tag_source="llm", tag_version="v")
    stats = store.merge(st, [review(text="stopped working", rating=1)], "2026-11-05")
    assert stats.edited == 1 and len(st) == 1
    r = st["1"]
    assert r["text"] == "stopped working" and r["rating"] == 1
    assert r["edited_seen"] == "2026-11-05"
    assert "sentiment" not in r and "theme" not in r
    assert r["first_seen"] == "2026-11-01"


def test_unchanged_review_keeps_tags():
    st = {}
    store.merge(st, [review()], "2026-11-01")
    st["1"]["sentiment"] = "positive"
    store.merge(st, [review()], "2026-11-02")
    assert st["1"]["sentiment"] == "positive"


def test_save_and_load_roundtrip_sorted(tmp_path):
    st = {}
    store.merge(st, [review("10"), review("9"), review("100")], "2026-11-01")
    path = tmp_path / "r.json"
    store.save_reviews(st, path)
    loaded = store.load_reviews(path)
    assert list(loaded) == ["9", "10", "100"]
    assert loaded == st


def test_run_log_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "MAX_RUNS_KEPT", 3)
    path = tmp_path / "runs.json"
    for i in range(5):
        store.append_run({"run_id": i}, path)
    assert [r["run_id"] for r in store.load_runs(path)] == [2, 3, 4]
