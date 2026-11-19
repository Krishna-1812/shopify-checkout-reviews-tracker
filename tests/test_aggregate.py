from datetime import date

from tracker import aggregate


def r(rid, app, d, sentiment="positive", theme="support", rating=5):
    return {"id": str(rid), "app": app, "date": d, "rating": rating, "sentiment": sentiment,
            "theme": theme, "text": "t", "url": "u", "store": "s", "country": "India"}


def test_week_start_is_monday():
    assert aggregate.week_start(date(2026, 11, 19)) == date(2026, 11, 16)   # Thu -> Mon
    assert aggregate.week_start(date(2026, 11, 16)) == date(2026, 11, 16)   # Mon
    assert aggregate.week_start(date(2026, 11, 22)) == date(2026, 11, 16)   # Sun


def test_series_are_continuous_with_zero_weeks():
    reviews = [r(1, "kwikcheckout", "2026-10-26"), r(2, "kwikcheckout", "2026-11-18", "negative", "setup", 1)]
    out = aggregate.build_series(reviews, date(2026, 11, 19))
    assert out["weeks"] == ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
    s = out["series"]["kwikcheckout"]
    assert s["total"] == [1, 0, 0, 1]
    assert s["positive"] == [1, 0, 0, 0]
    assert s["negative"] == [0, 0, 0, 1]
    assert s["themes"]["setup"] == [0, 0, 0, 1]
    assert s["themes_negative"]["setup"] == [0, 0, 0, 1]
    assert s["themes_negative"]["support"] == [0, 0, 0, 0]
    assert s["rating_sum"] == [5, 0, 0, 1]
    assert s["no_text"] == [0, 0, 0, 0]
    # every app gets a series, even with no reviews
    assert out["series"]["simpl-checkout"]["total"] == [0, 0, 0, 0]


def test_sentiment_parts_add_up_to_total():
    reviews = [r(i, "razorpay-checkout", "2026-11-1%d" % (i % 9), s)
               for i, s in enumerate(["positive", "neutral", "negative"] * 5)]
    s = aggregate.build_series(reviews, date(2026, 11, 19))["series"]["razorpay-checkout"]
    for i in range(len(s["total"])):
        assert s["positive"][i] + s["neutral"][i] + s["negative"][i] == s["total"][i]


def test_latest_is_newest_first_and_capped_per_app():
    reviews = [r(i, "kwikcheckout", f"2026-0{1 + i % 9}-1{i % 10}") for i in range(30)]
    reviews += [r(100, "razorpay-checkout", "2026-11-18")]
    latest = aggregate.latest_reviews(reviews, n=20)
    assert latest[0]["id"] == "100"
    assert sum(1 for x in latest if x["app"] == "kwikcheckout") == 20
    keys = [(x["date"], int(x["id"])) for x in latest]
    assert keys == sorted(keys, reverse=True)


def test_build_data_shape():
    reviews = {"1": r(1, "kwikcheckout", "2026-11-18")}
    runs = [{"started_at": "2026-11-19T03:17:00+00:00", "status": "ok", "totals": {"new": 1},
             "apps": {"kwikcheckout": {"declared_total": 1, "overall_rating": 5.0}}}]
    d = aggregate.build_data(reviews, runs, today=date(2026, 11, 19))
    kwik = d["apps"][0]
    assert kwik["stored_total"] == 1 and kwik["listed_total"] == 1 and kwik["themes"]["support"] == 1
    assert d["runs"]["last_ok_at"] == "2026-11-19T03:17:00+00:00"
    assert len(d["weeks"]) == len(d["series"]["kwikcheckout"]["total"])


def test_rating_only_reviews_are_counted_but_kept_out_of_theme_mix():
    blank = r(3, "kwikcheckout", "2026-11-17", theme="general")
    blank["text"] = ""
    s = aggregate.build_series([blank], date(2026, 11, 19))["series"]["kwikcheckout"]
    assert s["total"] == [1] and s["positive"] == [1]
    assert s["no_text"] == [1] and s["themes"]["general"] == [0]
