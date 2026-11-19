from tracker import health


def app(**kw):
    base = {"mode": "incremental", "pages_fetched": 1, "parsed": 10, "first_page_count": 10,
            "declared_total": 398, "malformed": 0, "new": 0, "stored_total": 398, "error": None}
    base.update(kw)
    return base


def run(apps, llm_status="ok"):
    return {"apps": apps, "tagging": {"llm_status": llm_status}}


def errors(r):
    return [p for p in health.evaluate(r) if p["level"] == "error"]


def test_normal_day_with_no_new_reviews_is_silent():
    r = run({"kwikcheckout": app(),
             "razorpay-checkout": app(declared_total=40, stored_total=40),
             "simpl-checkout": app(mode="full", parsed=0, first_page_count=0,
                                   declared_total=0, stored_total=0)})
    assert health.evaluate(r) == []
    assert health.is_healthy(health.evaluate(r))


def test_app_with_zero_reviews_is_not_an_alert():
    r = run({"kwikcheckout": app(),
             "simpl-checkout": app(parsed=0, first_page_count=0, declared_total=0, stored_total=0)})
    assert errors(r) == []


def test_listed_reviews_but_nothing_parsed_alerts():
    r = run({"kwikcheckout": app(parsed=0, first_page_count=0)})
    msgs = [p["message"] for p in errors(r)]
    assert any("quietly collected nothing" in m for m in msgs)
    assert any("no reviews from any app" in m for m in msgs)


def test_unreadable_header_alerts():
    assert errors(run({"kwikcheckout": app(declared_total=None)}))


def test_fetch_failure_alerts():
    e = errors(run({"kwikcheckout": app(error="HTTP 403", parsed=0, first_page_count=None)}))
    assert e and "403" in e[0]["message"]


def test_malformed_blocks_alert():
    assert errors(run({"kwikcheckout": app(malformed=2)}))


def test_coverage_gap_after_full_crawl_alerts():
    assert errors(run({"kwikcheckout": app(mode="full", stored_total=300)}))
    # small gaps (a review deleted mid-crawl) are tolerated
    assert not errors(run({"kwikcheckout": app(mode="full", stored_total=395)}))


def test_ai_fallback_is_a_warning_not_an_alert():
    probs = health.evaluate(run({"kwikcheckout": app()}, llm_status="unavailable: HTTP 429"))
    assert [p["level"] for p in probs] == ["warning"]
    assert health.is_healthy(probs)


def test_alert_markdown_mentions_problem_and_run():
    r = run({"kwikcheckout": app(parsed=0, first_page_count=0)})
    r.update(run_id="123", started_at="t", trigger="schedule")
    md = health.alert_markdown(r, health.evaluate(r), "https://example/run/123")
    assert "Kwik COD & Checkout" in md and "https://example/run/123" in md
