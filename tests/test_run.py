"""scrape_all with a fake Shopify: self-healing full crawl and removed-review detection."""
from tracker import config, run, scrape, store
from tests.test_store import review


def fake_shopify(listed: dict[str, list[dict]], calls: list):
    def crawl_app(session, slug, known, full, simulate_empty=False, delay=None):
        calls.append((slug, full))
        items = listed.get(slug, [])
        shown = items if full else items[:10]
        return scrape.AppCrawl(slug=slug, mode="full" if full else "incremental", reviews=list(shown),
                               pages_fetched=1, first_page_count=len(items[:10]), declared_total=len(items))
    return crawl_app


def run_once(monkeypatch, reviews, listed):
    calls = []
    monkeypatch.setattr(scrape, "crawl_app", fake_shopify(listed, calls))
    monkeypatch.setattr(scrape, "make_session", lambda: None)
    monkeypatch.setattr(config, "APPS", [config.App("kwikcheckout", "Kwik", "GoKwik", "#000")])
    report = run.scrape_all(reviews, "2026-11-21", force_full=False, simulate="none")
    return report["kwikcheckout"], calls


def test_review_deleted_on_shopify_triggers_full_crawl_and_is_marked(monkeypatch):
    reviews = {}
    store.merge(reviews, [review("1"), review("2", text="ok")], "2026-11-20")
    rep, calls = run_once(monkeypatch, reviews, {"kwikcheckout": [review("1")]})
    assert calls == [("kwikcheckout", False), ("kwikcheckout", True)]   # 2 stored vs 1 listed -> full crawl
    assert reviews["2"]["removed_on"] == "2026-11-21"
    assert rep["removed"] == 1 and rep["removed_total"] == 1 and rep["stored_total"] == 2
    # next run: counts match again (1 listed, 1 active), so no more full crawls
    _, calls = run_once(monkeypatch, reviews, {"kwikcheckout": [review("1")]})
    assert calls == [("kwikcheckout", False)]


def test_normal_day_reads_one_page_and_marks_nothing(monkeypatch):
    reviews = {}
    store.merge(reviews, [review("1")], "2026-11-20")
    rep, calls = run_once(monkeypatch, reviews, {"kwikcheckout": [review("1")]})
    assert calls == [("kwikcheckout", False)] and rep["removed"] == 0


def test_incomplete_full_crawl_never_marks_reviews_removed(monkeypatch):
    reviews = {}
    store.merge(reviews, [review("1"), review("2", text="ok")], "2026-11-20")
    calls = []

    def short_crawl(session, slug, known, full, simulate_empty=False, delay=None):
        calls.append(full)   # Shopify says 3, but the crawl only manages to see 1 (e.g. a page failed)
        return scrape.AppCrawl(slug=slug, mode="full" if full else "incremental", reviews=[review("1")],
                               pages_fetched=1, first_page_count=1, declared_total=3)
    monkeypatch.setattr(scrape, "crawl_app", short_crawl)
    monkeypatch.setattr(scrape, "make_session", lambda: None)
    monkeypatch.setattr(config, "APPS", [config.App("kwikcheckout", "Kwik", "GoKwik", "#000")])
    rep = run.scrape_all(reviews, "2026-11-21", force_full=False, simulate="none")["kwikcheckout"]
    assert rep["removed"] == 0 and not any(r.get("removed_on") for r in reviews.values())
