import pandas as pd
import pytest

from dhde_preprocessing.sources import instagram as ig


@pytest.fixture
def live_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DHDE_LIVE_DATA_ROOT", str(tmp_path))
    return tmp_path


def _items(rows: list[dict]) -> pd.DataFrame:
    base = {"type": "Image", "likesCount": 10, "commentsCount": 1, "caption": "", "ownerUsername": "someone"}
    return pd.DataFrame([{**base, **r} for r in rows])


@pytest.mark.parametrize("caption,script", [
    ("東尋坊きれいだった #fukui #tojinbo", "ja"),
    # Kanji only is Japanese unless a character or word only Chinese uses says otherwise (review of #25).
    ("大本山永平寺参拝", "ja"),
    ("恐竜博物館最高", "ja"),
    ("福井県立恐竜博物館 #恐竜", "ja"),
    ("勝山 恐竜 / 福井旅行 2026", "ja"),
    ("东寻坊太美了", "zh"),        # Simplified-only 东 寻
    ("東尋坊風景很美", "zh"),      # 很 is Chinese-only
    ("臺灣人來福井", "zh"),        # Traditional-only 臺 灣 來
    ("후쿠이 여행 Fukui trip", "ko"),
    ("Amazing cliffs! #japan #東尋坊", "latin"),  # tags don't count
    ("#東尋坊 🌊", "none"),
    (None, "none"),
])
def test_caption_script(caption, script):
    assert ig.caption_script(caption) == script


def test_normalize_drops_personal_fields_and_maps_kinds():
    items = _items([
        {"id": "1", "timestamp": "2026-09-27T16:00:00.000Z", "type": "Sidecar", "caption": "最高でした"},
        {"id": "2", "timestamp": "2026-09-28T01:00:00.000Z", "type": "Video", "likesCount": -1},
        {"id": None, "timestamp": None, "error": "not_found"},
    ])
    posts = ig.normalize(items, "123")
    assert list(posts.columns) == ig.POST_COLS
    assert len(posts) == 2
    assert posts["date"].tolist() == ["2026-09-28", "2026-09-28"]  # 16:00 UTC is the next day in JST
    assert posts["kind"].tolist() == ["carousel", "video"]
    assert pd.isna(posts["likes"].iloc[1])  # hidden like count
    assert posts["script"].tolist() == ["ja", "none"]
    assert posts["post_hash"].str.len().eq(16).all()


def test_run_summary_capped_run_covers_from_the_day_after_its_oldest_post():
    posts = pd.DataFrame({"date": ["2026-09-20", "2026-09-25"]})
    run = ig.run_summary(posts, "123", "2026-10-05", limit=2, since="2026-09-10")
    assert run["capped"] and run["covered_from"] == "2026-09-21"
    assert run["covered_to"] == "2026-10-04"
    uncapped = ig.run_summary(posts, "123", "2026-10-05", limit=200, since="2026-09-10")
    assert uncapped["covered_from"] == "2026-09-10"


def test_append_dedupes_and_keeps_the_latest_counts(live_root):
    first = ig.normalize(_items([{"id": "1", "timestamp": "2026-09-28T01:00:00Z", "likesCount": 5}]), "123")
    run = ig.run_summary(first, "123", "2026-09-30", 200, "2026-09-27")
    assert ig.append("tojinbo", first, run) == (1, 1)
    again = ig.normalize(_items([{"id": "1", "timestamp": "2026-09-28T01:00:00Z", "likesCount": 38}]), "123")
    assert ig.append("tojinbo", again, run) == (0, 1)
    log = pd.read_csv(ig.post_log_path("tojinbo"))
    assert log["likes"].tolist() == [40]  # the latest count, rounded to 10
    assert ig.since_date("tojinbo") == "2026-09-27"  # covered_to 09-29, minus 2 days


def test_to_daily_counts_and_zero_on_covered_days_without_posts():
    posts = ig.normalize(_items([
        {"id": "1", "timestamp": "2026-09-28T01:00:00Z", "type": "Image", "caption": "きれい", "likesCount": 47},
        {"id": "2", "timestamp": "2026-09-28T02:00:00Z", "type": "Video", "caption": "wow", "likesCount": -1,
         "commentsCount": 9},
        {"id": "3", "timestamp": "2026-09-01T02:00:00Z"},  # outside coverage
    ]), "123")
    runs = pd.DataFrame([{"run_date": "2026-09-30", "location_id": "123", "fetched": 3, "capped": False,
                          "covered_from": "2026-09-27", "covered_to": "2026-09-29"}])
    daily = ig.to_daily(posts, runs).set_index("date")
    assert daily.index.strftime("%Y-%m-%d").tolist() == ["2026-09-27", "2026-09-28", "2026-09-29"]
    day = daily.loc["2026-09-28"]
    assert (day["instagram_posts"], day["instagram_photos"], day["instagram_videos"]) == (2, 1, 1)
    assert day["instagram_likes"] == 50 and day["instagram_comments"] == 10  # 47 to 50; comments 1 to 0, 9 to 10
    assert day["instagram_script_ja"] == 1 and day["instagram_script_latin"] == 1
    assert daily.loc["2026-09-27", "instagram_posts"] == 0


def test_load_instagram_without_a_log_is_an_error_not_a_crash(live_root):
    cfg = {"node_key": "tojinbo", "sources": {"instagram": {"enabled": True, "location_id": "123"}}}
    df, report = ig.load_instagram(cfg)
    assert df is None and report.status == "error"


def test_a_run_that_hit_the_cap_is_capped_even_after_posts_are_dropped(live_root):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_instagram

    # 3 items returned, the cap: one tagged at another place, so 2 are kept.
    items = _items([
        {"id": "1", "timestamp": "2026-09-25T01:00:00Z", "locationId": "123"},
        {"id": "2", "timestamp": "2026-09-28T01:00:00Z", "locationId": "123"},
        {"id": "3", "timestamp": "2026-09-29T01:00:00Z", "locationId": "999"},
    ])
    collect_instagram.store(items, "tojinbo", "123", 3, "2026-09-10", "2026-10-05")
    run = pd.read_csv(ig.run_log_path("tojinbo")).iloc[0]
    assert bool(run["capped"]) and run["covered_from"] == "2026-09-26"  # not 09-10: older posts were cut off


def test_the_log_keeps_the_day_not_the_time(live_root):
    posts = ig.normalize(_items([{"id": "1", "timestamp": "2026-09-28T01:23:45Z"}]), "123")
    ig.append("tojinbo", posts, ig.run_summary(posts, "123", "2026-09-30", 200, "2026-09-27"))
    text = open(ig.post_log_path("tojinbo"), encoding="utf-8").read()
    assert "01:23" not in text and "2026-09-28" in text


def test_counts_are_rounded_so_a_post_cant_be_picked_out():
    posts = ig.normalize(_items([{"id": "1", "timestamp": "2026-09-28T01:00:00Z", "likesCount": 1234,
                                  "commentsCount": 17}]), "123")
    assert (posts["likes"].iloc[0], posts["comments"].iloc[0]) == (1230, 15)


def test_a_second_run_on_the_same_day_keeps_the_first_runs_coverage(live_root):
    empty = ig.normalize(pd.DataFrame(), "123")
    run = {"run_date": "2026-10-05", "location_id": "123", "fetched": 0, "capped": False}
    ig.append("tojinbo", empty, {**run, "covered_from": "2026-09-01", "covered_to": "2026-10-04"})
    ig.append("tojinbo", empty, {**run, "covered_from": "2026-10-02", "covered_to": "2026-10-04"})
    ig.append("tojinbo", empty, {**run, "covered_from": "2026-10-02", "covered_to": "2026-10-04"})  # no-op
    runs = pd.read_csv(ig.run_log_path("tojinbo"), dtype={"location_id": str})
    assert len(runs) == 2 and len(ig.covered_days(runs)) == 34  # 1 Sep to 4 Oct
