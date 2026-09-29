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


def test_reviews_split_by_language_and_chinese_script(live):
    _store(_items([
        ("a", "2026-09-25T01:00:00Z", 5, "zh-Hant", "很好"),
        ("b", "2026-09-25T02:00:00Z", 3, "zh-Hant", "普通"),
        ("c", "2026-09-25T03:00:00Z", 2, "zh-Hans", "一般"),
        ("d", "2026-09-25T04:00:00Z", 4, "en-GB", "Lovely"),
        ("e", "2026-09-25T05:00:00Z", 1, "zh", "差"),        # script unknown
        ("f", "2026-09-25T06:00:00Z", 5, "fr", "Super"),
        ("g", "2026-09-25T07:00:00Z", 4, "ko", None),       # star only: no language
    ]))
    df, _ = gr.load_google_reviews(_cfg())
    row = df.set_index("date").loc["2026-09-25"]
    assert row["reviews_lang_zh_hant"] == 2 and row["reviews_lang_zh_hant_stars_mean"] == 4.0
    assert row["reviews_lang_zh_hans"] == 1 and row["reviews_lang_en"] == 1
    assert row["reviews_lang_other"] == 2 and row["reviews_lang_ko"] == 0
    assert row["reviews_lang_ja"] == 0 and pd.isna(row["reviews_lang_ja_stars_mean"])
    lang_counts = [c for c in df.columns if c.startswith("reviews_lang_") and not c.endswith("mean")]
    assert row[lang_counts].sum() == row["reviews_with_text"]


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


def test_a_run_with_no_new_reviews_still_counts_as_zero(live, monkeypatch):
    """A quiet week (Rainbow Line in winter) must read 0 reviews, not missing."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_google_reviews as collector

    _store(_items([("a", "2026-09-20T01:00:00Z", 4, "ja", "x")], scraped="2026-09-22T00:00:00Z"))
    since = gr.since_date("tojinbo")

    class _Now:
        @staticmethod
        def now(tz):
            return pd.Timestamp("2026-09-29T10:00:00", tz=tz).to_pydatetime()
    monkeypatch.setattr(collector, "datetime", _Now)
    # The scraper found the place but no reviews since `since`: one place row, no reviewId.
    place_only = pd.DataFrame([{"placeId": PLACE, "reviewId": None, "totalScore": 4.3, "reviewsCount": 1001}])
    collector.store(place_only, PLACE, "tojinbo", 300, since, live=True)

    df, report = gr.load_google_reviews(_cfg())
    by = df.set_index("date")
    assert report.status == "ok"
    assert by.loc["2026-09-22":"2026-09-28", "reviews_new"].eq(0).all()
    assert len(by.loc["2026-09-22":"2026-09-28"]) == 7
    assert by.loc["2026-09-28", "reviews_count_total"] == 1001

    # Without even a place row, the week is still covered; only the totals are missing.
    monkeypatch.setattr(collector, "datetime", type("N", (), {"now": staticmethod(
        lambda tz: pd.Timestamp("2026-10-06T10:00:00", tz=tz).to_pydatetime())}))
    collector.store(pd.DataFrame(), PLACE, "tojinbo", 300, "2026-09-26", live=True)
    df, _ = gr.load_google_reviews(_cfg())
    by = df.set_index("date")
    assert by.loc["2026-10-05", "reviews_new"] == 0 and pd.isna(by.loc["2026-10-05", "reviews_count_total"])


def test_a_header_only_log_reads_zero_reviews_not_a_crash(live):
    """A newly enabled place: runs written, no reviews yet. stars must stay numeric."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_google_reviews as collector

    collector.store(pd.DataFrame(), PLACE, "tojinbo", 300, "2026-09-20", live=True)
    assert pd.read_csv(live / "google_reviews" / "tojinbo.csv").empty
    df, report = gr.load_google_reviews(_cfg())
    assert report.status == "ok"
    assert df["reviews_new"].eq(0).all() and df["reviews_stars_mean"].isna().all()
    assert pd.api.types.is_numeric_dtype(df["reviews_stars_mean"])


def test_reviews_under_another_place_id_are_not_logged_as_zero(live, capsys):
    """Google moved the listing: the week must not be marked covered with 0 reviews."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_google_reviews as collector

    moved = _items([("a", "2026-09-25T01:00:00Z", 5, "ja", "x")]).assign(placeId="ChIJmoved")
    collector.store(moved, PLACE, "tojinbo", 300, "2026-09-20", live=True)
    assert not (live / "google_reviews" / "tojinbo_runs.csv").exists()
    assert "ChIJmoved" in capsys.readouterr().out


def test_an_import_without_the_place_records_nothing(live):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_google_reviews as collector

    collector.store(pd.DataFrame([{"placeId": "other", "reviewId": "z"}]), PLACE, "tojinbo", 1000, None)
    assert not (live / "google_reviews" / "tojinbo_runs.csv").exists()


def test_no_log_is_an_error_not_a_crash(live):
    df, report = gr.load_google_reviews(_cfg())
    assert df is None and report.status == "error"


def test_disabled_is_unavailable_with_its_reason():
    cfg = {"node_key": "awara_onsen", "sources": {"google_reviews": {"enabled": False, "reason": "no place yet"}}}
    df, report = gr.load_google_reviews(cfg)
    assert df is None and report.status == "unavailable" and report.notes == ["no place yet"]
