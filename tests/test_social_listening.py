import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

from dhde_preprocessing import sentiment
from dhde_preprocessing.sentiment import Result
from dhde_preprocessing.sources import instagram as ig
from dhde_preprocessing.sources import social_listening as sl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_social  # noqa: E402


@pytest.fixture
def live_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DHDE_LIVE_DATA_ROOT", str(tmp_path))
    return tmp_path


# ---- sentiment helpers (the model itself isn't loaded in tests) ----

def test_from_probs_score_and_label():
    assert sentiment.from_probs({"positive": 0.7, "neutral": 0.2, "negative": 0.1}) == (0.6, "positive")
    assert sentiment.from_probs({"Positive": 0.1, "Neutral": 0.3, "Negative": 0.6}) == (-0.5, "negative")


@pytest.mark.parametrize("lang,route", [
    ("ja", "native"), ("zh-Hans", "native"), ("ar", "native"), ("en", "native"), (None, "native"),
    ("zh-Hant", "converted"),
    ("ko", "translated"), ("th", "translated"), ("vi", "translated"),
])
def test_route(lang, route):
    assert sentiment.route(lang) == route


@pytest.mark.parametrize("lang,group", [
    ("zh-Hant", "zh_hant"), ("zh-Hans", "zh_hans"), ("zh", "zh_hans"), ("ar", "ar"), ("ko", "ko"),
    ("fr", "other"), (None, "other"),
])
def test_lang_group(lang, group):
    assert sentiment.lang_group(lang) == group


def test_clean_drops_links_and_mentions_keeps_hashtags():
    assert sentiment.clean("最高 @someone https://x.co/a #東尋坊") == "最高 #東尋坊"
    assert sentiment.clean(None) == ""


# ---- matching ----

@pytest.mark.parametrize("text,keyword,hit", [
    ("福井県立恐竜博物館に行った", "恐竜博物館 福井", True),
    ("群馬の恐竜博物館", "恐竜博物館 福井", False),     # every word must appear
    ("visited the FUKUI Prefectural Dinosaur Museum", "Fukui dinosaur", True),
    ("진보 정당", "도진보", False),                       # Bluesky search hit that doesn't name Tojinbo
    ("후쿠이 도진보 여행", "도진보", True),
    (None, "東尋坊", False),
])
def test_matches(text, keyword, hit):
    assert sl.matches(text, keyword) is hit


def test_nodes_named_can_be_several():
    kws = {"tojinbo": ["東尋坊"], "awara_onsen": ["あわら温泉"], "eiheiji": ["永平寺"]}
    assert sl.nodes_named("東尋坊からあわら温泉へ", kws) == ["tojinbo", "awara_onsen"]


# ---- log ----

def _items(rows):
    base = {"platform": "bluesky", "kind": "post", "text": "x", "like_count": 1}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_normalize_keeps_no_text_and_uses_jst_dates():
    items = _items([
        {"id": "a", "created_at": "2026-09-27 16:00:00", "text": "東尋坊 最高", "like_count": -3},
        {"id": "b", "created_at": "2026-09-28 01:00:00", "kind": "comment"},
    ])
    out = sl.normalize(items, [Result(0.8, "positive", "ja", "native"), None], [None, "ko"])
    assert list(out.columns) == sl.LOG_COLS
    assert "東尋坊" not in out.to_csv()
    assert out["date"].tolist() == ["2026-09-28", "2026-09-28"]
    assert out["language"].tolist() == ["ja", "ko"]  # a comment without a score still has its language
    assert pd.isna(out["likes"].iloc[0])  # Reddit-style negative score isn't a like count
    assert out["route"].iloc[0] == "native" and pd.isna(out["sentiment"].iloc[1])


def test_coverage():
    assert sl.coverage("2026-10-05", "2026-09-28", None) == ("2026-09-28", "2026-10-04")
    assert sl.coverage("2026-10-05", "2026-09-28", "2026-10-01") == ("2026-10-02", "2026-10-04")
    assert sl.coverage("2026-10-05", "2026-09-28", None, failed=True) == (None, None)


def _run(platform, frm, to, run_date="2026-10-05"):
    return {"run_date": run_date, "platform": platform, "fetched": 0, "capped": False,
            "covered_from": frm, "covered_to": to}


def test_append_dedupes_and_since_is_per_platform(live_root):
    first = sl.normalize(_items([{"id": "a", "created_at": "2026-10-01 01:00:00", "like_count": 2}]))
    runs = [_run("bluesky", "2026-09-28", "2026-10-04"), _run("youtube", None, None)]
    assert sl.append("tojinbo", first, runs) == (1, 1)
    again = sl.normalize(_items([{"id": "a", "created_at": "2026-10-01 01:00:00", "like_count": 38}]))
    assert sl.append("tojinbo", again, runs) == (0, 1)
    assert pd.read_csv(sl.log_path("tojinbo"))["likes"].tolist() == [40]  # latest count, rounded
    assert sl.since_date("tojinbo", "bluesky") == "2026-10-02"
    assert sl.since_date("tojinbo", "youtube") is None  # failed run: start over from first-days


def test_to_daily_platform_missing_outside_its_coverage_and_mean_missing_without_scores():
    log = sl.normalize(_items([
        {"id": "a", "created_at": "2026-10-01 01:00:00"},
        {"id": "b", "created_at": "2026-10-01 02:00:00", "kind": "comment"},
        {"id": "c", "created_at": "2026-10-02 01:00:00", "platform": "youtube"},
        {"id": "d", "created_at": "2026-09-01 01:00:00"},  # outside coverage
    ]), [Result(0.6, "positive", "ja", "native"), Result(-0.4, "negative", "zh-Hant", "converted"),
         Result(0.0, "neutral", "ko", "translated"), None])
    runs = pd.DataFrame([_run("bluesky", "2026-09-30", "2026-10-02"), _run("youtube", "2026-10-02", "2026-10-02")])
    d = sl.to_daily(log, runs).set_index("date")
    assert d.index.strftime("%Y-%m-%d").tolist() == ["2026-09-30", "2026-10-01", "2026-10-02"]
    day = d.loc["2026-10-01"]
    assert (day["social_mentions"], day["social_posts"], day["social_comments"]) == (2, 1, 1)
    assert day["social_sentiment_mean"] == pytest.approx(0.1)
    assert (day["social_lang_ja"], day["social_lang_zh_hant"]) == (1, 1)
    assert pd.isna(day["social_youtube_mentions"]) and day["social_platforms"] == 1
    assert d.loc["2026-10-02", "social_translated"] == 1 and d.loc["2026-10-02", "social_platforms"] == 2
    quiet = d.loc["2026-09-30"]
    assert quiet["social_mentions"] == 0 and pd.isna(quiet["social_sentiment_mean"])


def test_load_without_a_log_is_an_error_not_a_crash(live_root):
    cfg = {"node_key": "tojinbo", "sources": {"social_listening": {"enabled": True, "keywords": ["東尋坊"]}}}
    df, report = sl.load_social_listening(cfg)
    assert df is None and report.status == "error"


# ---- collect_social, on a database shaped like social-collector's ----

def _db(path: Path, posts, comments, queries):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE posts (platform, id, text, created_at, like_count, language, query)")
    con.execute("CREATE TABLE comments (platform, id, post_id, text, created_at, like_count)")
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, platform, summary)")
    con.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?)", posts)
    con.executemany("INSERT INTO comments VALUES (?,?,?,?,?,?)", comments)
    for platform, qs in queries.items():
        con.execute("INSERT INTO runs (platform, summary) VALUES (?, ?)", (platform, json.dumps({"queries": qs})))
    con.commit()
    con.close()


def test_collect_social_from_a_database(tmp_path, monkeypatch):
    db = tmp_path / "social.db"
    _db(db, posts=[
        ("youtube", "v1", "東尋坊 vlog", "2026-09-29 03:00:00", 10, "ja", "東尋坊"),
        ("youtube", "v2", "群馬 旅行", "2026-09-29 04:00:00", 5, "ja", "東尋坊"),  # search hit, doesn't name it
    ], comments=[
        ("youtube", "c1", "v1", "정말 멋져요", "2026-09-30 01:00:00", 1),
        ("youtube", "c2", "v2", "nice", "2026-09-30 01:00:00", 1),
    ], queries={"youtube": [{"query": "東尋坊", "posts_seen": 2, "completed": True, "error": None},
                            {"query": "Tojinbo", "posts_seen": 0, "completed": True, "error": None}]})
    seen = {}

    def fake_scorer(texts, hints=None):
        seen["texts"] = list(texts)
        return [Result(0.5, "positive", "ko" if "멋" in t else "ja", "translated" if "멋" in t else "native")
                for t in texts]

    monkeypatch.setattr(collect_social, "node_keywords", lambda: {"tojinbo": ["東尋坊", "Tojinbo"]})
    monkeypatch.setattr(collect_social, "try_load_scorer", lambda: fake_scorer)
    out = tmp_path / "history"
    monkeypatch.setattr(sys, "argv", ["collect_social.py", "--out", str(out), "--db", str(db),
                                      "--platform", "youtube"])
    assert collect_social.main() == 0

    assert sorted(seen["texts"]) == sorted(["東尋坊 vlog", "정말 멋져요"])  # v2 and its comment dropped
    log = pd.read_csv(out / "social" / "tojinbo.csv")
    assert sorted(log["kind"]) == ["comment", "post"]
    assert sorted(log["language"]) == ["ja", "ko"]
    assert "東尋坊" not in (out / "social" / "tojinbo.csv").read_text(encoding="utf-8")
    runs = pd.read_csv(out / "social" / "tojinbo_runs.csv")
    assert runs.loc[0, "platform"] == "youtube" and runs.loc[0, "covered_from"] == "2026-09-29"


def test_node_coverage_failed_and_capped():
    posts = pd.DataFrame({"platform": ["youtube"] * 2, "query": ["東尋坊"] * 2, "date": ["2026-09-20", "2026-09-25"]})
    q_ok = {"query": "東尋坊", "posts_seen": 2, "completed": True, "error": None}
    assert collect_social.node_coverage("youtube", ["東尋坊", "Tojinbo"], posts, [q_ok], "2026-10-05",
                                        "2026-09-10")[:2] == (None, None)  # Tojinbo never ran (quota)
    capped = {**q_ok, "posts_seen": collect_social.LIMITS["youtube"]}
    frm, to, was_capped = collect_social.node_coverage("youtube", ["東尋坊"], posts, [capped], "2026-10-05",
                                                       "2026-09-10")
    assert (frm, to, was_capped) == ("2026-09-21", "2026-10-04", True)


def test_sample_out_refused_inside_the_repo(tmp_path):
    with pytest.raises(SystemExit):
        collect_social.check_sample_path(collect_social.REPO / "sample.csv", tmp_path / "history")


def test_reddit_gets_only_latin_keywords():
    assert collect_social.platform_keywords("reddit", ["東尋坊", "Tojinbo"]) == ["Tojinbo"]


# ---- Instagram captions ----

def test_instagram_caption_sentiment_and_old_logs_without_it():
    items = pd.DataFrame([
        {"id": "1", "timestamp": "2026-09-28T01:00:00Z", "type": "Image", "likesCount": 1, "commentsCount": 0,
         "caption": "最高"},
        {"id": "2", "timestamp": "2026-09-28T02:00:00Z", "type": "Image", "likesCount": 1, "commentsCount": 0,
         "caption": None},
    ])
    scorer = lambda texts: [Result(0.9, "positive", "ja", "native") if t else None for t in texts]  # noqa: E731
    posts = ig.normalize(items, "123", scorer)
    assert posts["label"].tolist()[0] == "positive" and pd.isna(posts["sentiment"].iloc[1])
    runs = pd.DataFrame([{"run_date": "2026-09-30", "location_id": "123", "fetched": 2, "capped": False,
                          "covered_from": "2026-09-27", "covered_to": "2026-09-29"}])
    d = ig.to_daily(posts, runs).set_index("date")
    assert d.loc["2026-09-28", "instagram_positive"] == 1 and d.loc["2026-09-28", "instagram_scored"] == 1
    assert d.loc["2026-09-28", "instagram_sentiment_mean"] == pytest.approx(0.9)
    old = posts.drop(columns=["sentiment", "label"])  # a log written before sentiment
    assert pd.isna(ig.to_daily(old, runs).set_index("date").loc["2026-09-28", "instagram_sentiment_mean"])


def test_the_log_keeps_the_day_not_the_time(live_root):
    items = sl.normalize(_items([{"id": "a", "created_at": "2026-10-01 01:23:45"}]))
    sl.append("tojinbo", items, [_run("bluesky", "2026-09-28", "2026-10-04")])
    text = open(sl.log_path("tojinbo"), encoding="utf-8").read()
    assert "01:23" not in text and "2026-10-01" in text


def test_social_likes_are_rounded():
    out = sl.normalize(_items([{"id": "a", "created_at": "2026-10-01 01:00:00", "like_count": 23}]))
    assert out["likes"].tolist() == [20]


# ---- no false zeros, no forced labels (first live run, 2026-10-02) ----

def test_instagram_empty_result_records_nothing(live_root, capsys):
    import collect_instagram
    collect_instagram.store(pd.DataFrame(), "katsuyama", "307321308", 200, "2026-09-04", "2026-10-02")
    assert not Path(ig.run_log_path("katsuyama")).exists()  # missing, not 28 days of 0
    assert "nothing recorded" in capsys.readouterr().out


def test_a_platform_with_no_posts_for_any_keyword_covers_nothing():
    posts = pd.DataFrame(columns=["platform", "query", "date"])
    empty = [{"query": k, "posts_seen": 0, "completed": True, "error": None} for k in ("東尋坊", "Tojinbo")]
    assert collect_social.node_coverage("bluesky", ["東尋坊"], posts, empty, "2026-10-05", "2026-09-28")[:2] \
        == (None, None)
    # Reddit: a week with no post naming a Fukui site can be real, so it stays 0.
    assert collect_social.node_coverage("reddit", ["Tojinbo"], posts, empty, "2026-10-05", "2026-09-28")[:2]         == ("2026-09-28", "2026-10-04")
    # One keyword with posts is enough to trust the others' zeros.
    some = [{**empty[0], "posts_seen": 3}, empty[1]]
    assert collect_social.node_coverage("bluesky", ["Tojinbo"], posts, some, "2026-10-05", "2026-09-28")[:2] \
        == ("2026-09-28", "2026-10-04")


@pytest.mark.parametrize("score,label", [(0.6, "positive"), (0.2, "positive"), (0.19, "neutral"),
                                         (-0.14, "neutral"), (-0.2, "negative"), (None, None)])
def test_label_comes_from_the_score(score, label):
    assert sentiment.label_of(score) == label


def test_daily_counts_relabel_old_rows_from_the_score():
    # A row written before NEUTRAL_BAND: the model said "negative" for -0.14.
    log = sl.normalize(_items([{"id": "a", "created_at": "2026-10-01 01:00:00"}]),
                       [Result(-0.14, "negative", "ja", "native")])
    runs = pd.DataFrame([_run("bluesky", "2026-10-01", "2026-10-01")])
    day = sl.to_daily(log, runs).set_index("date").iloc[0]
    assert (day["social_neutral"], day["social_negative"]) == (1, 0)


def test_a_second_run_on_the_same_day_keeps_the_first_runs_coverage(live_root):
    # 2026-10-02: a manual rerun replaced the morning's run row, so 4 to 28 Sep read uncovered.
    empty = sl.normalize(_items([]))
    sl.append("tojinbo", empty, [_run("bluesky", "2026-09-04", "2026-10-01", "2026-10-02")])
    sl.append("tojinbo", empty, [_run("bluesky", "2026-09-29", "2026-10-01", "2026-10-02")])
    sl.append("tojinbo", empty, [_run("bluesky", "2026-09-29", "2026-10-01", "2026-10-02")])  # same run again: no-op
    runs = pd.read_csv(sl.run_log_path("tojinbo"))
    assert len(runs) == 2
    assert len(sl.covered_days(runs)) == 28  # 4 Sep to 1 Oct
