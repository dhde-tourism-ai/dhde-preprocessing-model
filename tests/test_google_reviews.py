import pandas as pd
import pytest

from dhde_preprocessing.sources import google_reviews as gr

PLACE = "ChIJtest"


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("DHDE_LIVE_DATA_ROOT", str(tmp_path))
    return tmp_path


def _items(rows, scraped="2026-09-28T05:30:00Z"):
    """Scraper output rows: (review id, published UTC, stars, language, text)."""
    return pd.DataFrame([{
        "reviewId": rid, "placeId": PLACE, "publishedAtDate": ts, "stars": stars,
        "originalLanguage": lang, "text": text, "responseFromOwnerText": None,
        "scrapedAt": scraped, "totalScore": 4.2, "reviewsCount": 999,
        "name": "Reviewer Name", "reviewerUrl": "https://example.com/u", "reviewerId": "u1",
    } for rid, ts, stars, lang, text in rows])


def _cfg():
    return {"node_key": "tojinbo", "sources": {"google_reviews": {"enabled": True, "place_id": PLACE}}}


def _store(items, max_reviews=None, since=None):
    reviews = gr.normalize(items)
    return gr.append("tojinbo", reviews, gr.run_summary(items, reviews, PLACE, max_reviews, since))


def test_no_personal_fields_or_text_are_kept(live):
    _store(_items([("a", "2026-09-25T01:00:00Z", 5, "ja", "とても良い")]))
    log = pd.read_csv(live / "google_reviews" / "tojinbo.csv")
    assert list(log.columns) == gr.REVIEW_COLS
    assert "a" not in set(log["review_hash"])
    assert not log.astype(str).apply(lambda c: c.str.contains("Reviewer|example.com|とても")).any().any()


def test_dates_are_japanese_days(live):
    # 16:00 UTC on the 25th is 01:00 on the 26th in Japan.
    reviews = gr.normalize(_items([("a", "2026-09-25T16:00:00Z", 4, "ja", "x")]))
    assert reviews["date"].item() == "2026-09-26"


def test_daily_counts_are_zero_inside_coverage_and_missing_outside(live):
    items = _items([
        ("a", "2026-09-24T01:00:00Z", 3, None, None),   # oldest, capped run: its day isn't covered
        ("b", "2026-09-25T01:00:00Z", 5, "ja", "良い"),
        ("c", "2026-09-25T02:00:00Z", 4, "en", "Great"),
        ("d", "2026-09-27T01:00:00Z", 1, "zh-Hant", "差"),
    ])
    _store(items, max_reviews=4)
    df, report = gr.load_google_reviews(_cfg())
    assert report.status == "ok"
    by = df.set_index("date")
    assert list(by.index.strftime("%Y-%m-%d")) == ["2026-09-25", "2026-09-26", "2026-09-27"]
    assert by.loc["2026-09-25", "reviews_new"] == 2
    assert by.loc["2026-09-25", "reviews_stars_mean"] == 4.5
    assert by.loc["2026-09-25", "reviews_foreign"] == 1
    assert by.loc["2026-09-26", "reviews_new"] == 0 and pd.isna(by.loc["2026-09-26", "reviews_stars_mean"])
    # The place totals land on the last day the run covers.
    assert by.loc["2026-09-27", "reviews_count_total"] == 999
    assert pd.isna(by.loc["2026-09-25", "reviews_count_total"])


def test_star_only_reviews_are_not_counted_as_foreign(live):
    _store(_items([("a", "2026-09-25T01:00:00Z", 5, "en", None)]))
    df, _ = gr.load_google_reviews(_cfg())
    assert df["reviews_foreign"].sum() == 0 and df["reviews_with_text"].sum() == 0


def test_a_second_run_adds_only_new_reviews_and_extends_coverage(live):
    _store(_items([("a", "2026-09-20T01:00:00Z", 4, "ja", "x")], scraped="2026-09-22T00:00:00Z"))
    since = gr.since_date("tojinbo")
    assert since == "2026-09-18"
    added, total = _store(_items([("a", "2026-09-20T01:00:00Z", 5, "ja", "x"),
                                  ("b", "2026-09-26T01:00:00Z", 2, "ja", "y")]), since=since)
    assert (added, total) == (1, 2)
    df, _ = gr.load_google_reviews(_cfg())
    # The re-seen review replaced its earlier row (stars edited 4 -> 5).
    assert df.set_index("date").loc["2026-09-20", "reviews_stars_5"] == 1
    assert df["date"].max() == pd.Timestamp("2026-09-27")


def test_no_log_is_an_error_not_a_crash(live):
    df, report = gr.load_google_reviews(_cfg())
    assert df is None and report.status == "error"


def test_disabled_is_unavailable_with_its_reason():
    cfg = {"node_key": "awara_onsen", "sources": {"google_reviews": {"enabled": False, "reason": "no place yet"}}}
    df, report = gr.load_google_reviews(cfg)
    assert df is None and report.status == "unavailable" and report.notes == ["no place yet"]
