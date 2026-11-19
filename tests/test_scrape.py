"""Parser tests against real App Store pages saved on 2026-11-19
(scripts, styles and CSS classes stripped; the parser never relies on them)."""
from pathlib import Path

import pytest

from tracker import scrape

FIX = Path(__file__).parent / "fixtures"


def page(name: str, slug: str) -> scrape.PageResult:
    return scrape.parse_reviews_page((FIX / f"{name}.html").read_text(encoding="utf-8"), slug)


def test_kwik_first_page_header_and_count():
    r = page("kwik_page1", "kwikcheckout")
    assert len(r.reviews) == 10
    assert r.malformed == 0
    assert r.declared_total == 398
    assert r.overall_rating == 4.6
    assert r.rating_counts == {"5": 363, "4": 10, "3": 2, "2": 0, "1": 23}
    assert sum(r.rating_counts.values()) == r.declared_total


def test_review_fields_are_parsed_exactly():
    r = page("kwik_page1", "kwikcheckout")
    first = r.reviews[0]
    assert first == {
        "id": "2433162",
        "app": "kwikcheckout",
        "url": "https://apps.shopify.com/reviews/2433162",
        "rating": 1,
        "date": "2026-11-17",
        "store": "Azukai",
        "country": "India",
        "usage": "1 minute using the app",
        "text": "no support",
        "has_reply": False,
        "content_hash": scrape.content_hash("no support", 1),
    }


def test_pages_are_newest_first_and_ids_unique():
    r = page("kwik_page1", "kwikcheckout")
    dates = [x["date"] for x in r.reviews]
    assert dates == sorted(dates, reverse=True)
    assert len({x["id"] for x in r.reviews}) == 10


def test_every_review_has_valid_core_fields():
    for name, slug in [("kwik_page1", "kwikcheckout"), ("razorpay_page1", "razorpay-checkout"),
                       ("kwik_last_page", "kwikcheckout")]:
        for x in page(name, slug).reviews:
            assert x["id"].isdigit()
            assert 1 <= x["rating"] <= 5
            assert len(x["date"]) == 10
            assert x["store"]


def test_razorpay_page():
    r = page("razorpay_page1", "razorpay-checkout")
    assert len(r.reviews) == 10
    assert r.declared_total == 40
    assert r.overall_rating == 1.9
    assert r.reviews[0]["text"] == "third class app"


def test_app_with_no_reviews_reads_as_zero_not_unknown():
    r = page("simpl_page1", "simpl-checkout")
    assert r.reviews == []
    assert r.declared_total == 0      # "No reviews yet" is a real 0, not a parse failure


def test_last_and_past_end_pages():
    assert len(page("kwik_last_page", "kwikcheckout").reviews) == 8
    past = page("kwik_past_end", "kwikcheckout")
    assert past.reviews == [] and past.declared_total == 398


def test_unrecognisable_page_gives_unknown_total():
    r = scrape.parse_reviews_page("<html><body><h1>Something else</h1></body></html>", "x")
    assert r.reviews == [] and r.declared_total is None


def test_block_missing_fields_counts_as_malformed():
    html = '<div data-review-content-id="1"><p>no stars or date</p></div>'
    r = scrape.parse_reviews_page(html, "x")
    assert r.reviews == [] and r.malformed == 1


# --- crawl_app pagination logic, with the network replaced by fixtures -------
@pytest.fixture
def fake_pages(monkeypatch):
    pages = {
        1: (FIX / "kwik_page1.html").read_text(encoding="utf-8"),
        2: (FIX / "kwik_last_page.html").read_text(encoding="utf-8"),
        3: (FIX / "kwik_past_end.html").read_text(encoding="utf-8"),
    }
    calls = []

    def fake_fetch(session, url):
        n = int(url.rsplit("page=", 1)[1])
        calls.append(n)
        return pages.get(n, pages[3])

    monkeypatch.setattr(scrape, "fetch", fake_fetch)
    return calls


def test_full_crawl_walks_until_an_empty_page(fake_pages):
    c = scrape.crawl_app(None, "kwikcheckout", set(), full=True, delay=0)
    assert fake_pages == [1, 2, 3]
    assert len(c.reviews) == 18 and c.error is None
    assert c.first_page_count == 10 and c.declared_total == 398


def test_incremental_crawl_stops_at_first_fully_known_page(fake_pages):
    known = {r["id"] for r in page("kwik_page1", "kwikcheckout").reviews}
    c = scrape.crawl_app(None, "kwikcheckout", known, full=False, delay=0)
    assert fake_pages == [1]
    assert len(c.reviews) == 10


def test_simulated_empty_result(fake_pages):
    c = scrape.crawl_app(None, "kwikcheckout", set(), full=True, simulate_empty=True, delay=0)
    assert c.reviews == [] and c.first_page_count == 0 and c.declared_total == 398


def test_fetch_error_is_recorded_not_raised(monkeypatch):
    def boom(session, url):
        raise scrape.FetchError("HTTP 403")
    monkeypatch.setattr(scrape, "fetch", boom)
    c = scrape.crawl_app(None, "kwikcheckout", set(), full=True, delay=0)
    assert c.error and "403" in c.error
