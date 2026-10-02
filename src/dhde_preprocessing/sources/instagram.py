"""
Instagram posts tagged at each node's location: how many per day, of
which kind, their likes and comments, and the script their caption is
written in.

Optional source: only nodes that declare `instagram` with a
`location_id` get it (see join.OPTIONAL_SOURCES). It feeds the app's
Social media layer.

Where the data comes from: the Apify "Instagram Scraper" actor
(apify/instagram-scraper) run on the location's page,
instagram.com/explore/locations/{location_id}/. scripts/collect_instagram.py
runs it weekly in GitHub Actions and appends to the `live-data` branch,
like the Google reviews; it can also import an export someone ran by hand.

What's kept: this repo and the `live-data` branch are public, so the log
holds no usernames, captions, links or images. Only a hash of the post
id (to drop duplicates between runs), the day, the kind, the like and
comment counts at scrape time, the caption's script, and its sentiment
score and label (sentiment.py), worked out while the caption is in
memory. Logs from before sentiment have those empty. The day, not the
time: a post's exact time and location find it on Instagram, and with it
the account that posted it. Likes are rounded to the nearest 10 and
comments to the nearest 5 for the same reason: at a quiet location, the
day plus an exact pair of counts picks out one post. Daily sums of
rounded counts are close, not exact.

Caption script is a rough market proxy, like the review language: kana
means Japanese, hangul Korean, Latin letters only "latin" (English and
most European languages). Kanji without kana is Japanese unless it has a
character or word only Chinese uses (lang.py): short Japanese captions
are often kanji only ("大本山永平寺参拝"), so "zh" is a floor for Chinese
posts, not an estimate. It says nothing about who posted.

Coverage: a run asks for posts newer than the last covered day and
stops at a cap, so a capped run only covers back to its oldest post.
Each run records the days it fully covers (`covered_from` to
`covered_to`); on those days a day without posts is a real 0, outside
them it stays missing. Likes and comments keep growing after a post
goes up, so they're counts at scrape time, not final totals, and posts
aren't re-scraped once past the 2-day overlap. A weekly run sees the
day before it about a day old and the start of its week about a week
old, so likes per day follow the weekday of the run as much as
engagement: compare weekly totals, don't chart likes by day.

Files, under the live-data root:
    instagram/{node_key}.csv        one row per post
    instagram/{node_key}_runs.csv   one row per run: its coverage
"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta, timezone

import pandas as pd

from ..config import read_csv_if_exists, resolve_live_path, write_csv
from ..lang import chinese_variant
from ..sentiment import label_of
from ..validation import SourceReport, unavailable_report, validate_daily

LOG_DIR = "instagram"
POST_COLS = ["post_hash", "location_id", "date", "kind", "likes", "comments", "script", "sentiment",
             "label", "scraped_at"]
RUN_COLS = ["run_date", "location_id", "fetched", "capped", "covered_from", "covered_to"]
JST = timezone(timedelta(hours=9))
# Apify's type -> ours. Sidecar is a carousel of several images or videos.
KINDS = {"Image": "photo", "Sidecar": "carousel", "Video": "video"}
SCRIPTS = ["ja", "ko", "zh", "latin", "none"]

_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
_TAG = re.compile(r"[#@]\S+")


def post_log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}.csv")


def run_log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}_runs.csv")


def _hash(post_id) -> str:
    return hashlib.sha256(str(post_id).encode()).hexdigest()[:16]


def _jst_date(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True, errors="coerce").dt.tz_convert(JST).dt.strftime("%Y-%m-%d")


def caption_script(caption) -> str:
    """The script a caption is written in, hashtags and mentions left out.

    Kana wins over Chinese characters (Japanese mixes both); Korean wins
    over Latin (Korean captions often add English tags-as-words). Kanji
    alone is Japanese unless lang.chinese_variant finds a Chinese-only mark.
    """
    text = _TAG.sub(" ", caption) if isinstance(caption, str) else ""
    if _KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _HAN.search(text):
        return "zh" if chinese_variant(text) else "ja"
    if _LATIN.search(text):
        return "latin"
    return "none"


def round_to(values: pd.Series, step: int) -> pd.Series:
    """Nearest multiple of `step`, missing kept: exact counts help find a post."""
    return (values / step).round() * step


def normalize(items: pd.DataFrame, location_id: str, scorer=None) -> pd.DataFrame:
    """Scraper output (export or API items) -> POST_COLS, personal fields dropped.

    With a sentiment.Scorer, each caption is scored before it's dropped."""
    if items.empty or "id" not in items:
        return pd.DataFrame(columns=POST_COLS)
    items = items[items["id"].notna() & items.get("timestamp", pd.Series(index=items.index)).notna()]
    likes = pd.to_numeric(items.get("likesCount"), errors="coerce")
    captions = items.get("caption", pd.Series(index=items.index, dtype=object))
    scores = scorer([c if isinstance(c, str) else None for c in captions]) if scorer else [None] * len(items)
    out = pd.DataFrame({
        "post_hash": items["id"].map(_hash),
        "location_id": str(location_id),
        "date": _jst_date(items["timestamp"]),
        "kind": items.get("type", pd.Series(index=items.index, dtype=object)).map(KINDS).fillna("other"),
        # Instagram reports -1 when the owner hides the like count.
        "likes": round_to(likes.where(likes >= 0), 10),
        "comments": round_to(pd.to_numeric(items.get("commentsCount"), errors="coerce"), 5),
        "script": captions.map(caption_script),
        "sentiment": [r.score if r else None for r in scores],
        "label": [r.label if r else None for r in scores],
        "scraped_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return out.dropna(subset=["date"])


def run_summary(posts: pd.DataFrame, location_id: str, run_date: str, limit: int | None,
                since: str | None, returned: int | None = None) -> dict:
    """One run_log row: the days this run fully covers.

    A capped run (hit the per-location limit) covers from the day after
    its oldest post, since that day may have more posts it didn't get.
    An uncapped run covers from `since`, or from its oldest post on a
    first run without one. The run day itself is only partly over.

    `returned` is how many items the scraper gave back before any were
    dropped (other locations, before `since`): the cap applies to that,
    not to the posts kept.
    """
    capped = bool(limit) and (len(posts) if returned is None else returned) >= limit
    oldest = posts["date"].min() if len(posts) else None
    if capped:
        covered_from = (pd.Timestamp(oldest) + timedelta(days=1)).date().isoformat()
    else:
        covered_from = since or oldest
    return {
        "run_date": run_date, "location_id": str(location_id), "fetched": len(posts), "capped": capped,
        "covered_from": covered_from,
        "covered_to": (pd.Timestamp(run_date) - timedelta(days=1)).date().isoformat(),
    }


def append(node_key: str, posts: pd.DataFrame, run: dict) -> tuple[int, int]:
    """Add one run to a node's logs; returns (new posts, posts in the log).

    A post seen again replaces the earlier row, so its likes and comments
    are the latest count. Re-importing the same run is a no-op.
    """
    path = post_log_path(node_key)
    old = read_csv_if_exists(path, dtype={"location_id": str})
    before = set(old["post_hash"]) if old is not None else set()
    parts = [df for df in (old, posts) if df is not None and not df.empty]
    log = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=POST_COLS)
    log = log.drop_duplicates("post_hash", keep="last").sort_values("date", kind="stable")
    write_csv(log.reindex(columns=POST_COLS), path)

    runs_path = run_log_path(node_key)
    runs = read_csv_if_exists(runs_path, dtype={"location_id": str})
    new_run = pd.DataFrame([run])
    runs = pd.concat([runs, new_run], ignore_index=True) if runs is not None else new_run
    # Keyed on the coverage too: a second run on the same day (a manual rerun) covers
    # other days, and replacing the first run's row would drop days it covered.
    # The same run imported twice is still a no-op.
    runs = runs.drop_duplicates(["run_date", "location_id", "covered_from", "covered_to"], keep="last").sort_values("run_date")
    write_csv(runs[RUN_COLS], runs_path)
    return len(set(posts["post_hash"]) - before), len(log)


def since_date(node_key: str, overlap_days: int = 2) -> str | None:
    """Where the next run can start: a couple of days before the last covered
    day, so late-indexed posts aren't missed. None before the first run."""
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty or runs["covered_to"].isna().all():
        return None
    return (pd.Timestamp(runs["covered_to"].max()) - timedelta(days=overlap_days)).date().isoformat()


def covered_days(runs: pd.DataFrame) -> pd.DatetimeIndex:
    days = [pd.date_range(r.covered_from, r.covered_to) for r in runs.itertuples()
            if pd.notna(r.covered_from) and pd.notna(r.covered_to) and r.covered_from <= r.covered_to]
    return pd.DatetimeIndex(sorted(set().union(*days))) if days else pd.DatetimeIndex([])


def to_daily(posts: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Post log -> one row per covered day.

    instagram_posts, _photos (photos and carousels), _videos, _likes,
    _comments, and instagram_script_{ja,ko,zh,latin,none} splitting
    instagram_posts. instagram_positive / _neutral / _negative, _scored
    and instagram_sentiment_mean (missing on a day with no scored
    caption). Counts are 0 on a covered day without posts. Likes skip
    posts with hidden counts.
    """
    days = covered_days(runs)
    p = posts.reindex(columns=POST_COLS).copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p[p["date"].isin(days)]
    for col in ("likes", "comments", "sentiment"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    p["label"] = p["sentiment"].map(label_of)  # from the score: logs from before NEUTRAL_BAND too
    g = p.groupby("date")
    daily = pd.DataFrame({
        "instagram_posts": g.size(),
        "instagram_photos": g["kind"].apply(lambda k: int(k.isin(["photo", "carousel"]).sum())),
        "instagram_videos": g["kind"].apply(lambda k: int((k == "video").sum())),
        "instagram_likes": g["likes"].sum(),
        "instagram_comments": g["comments"].sum(),
        **{f"instagram_script_{s}": g["script"].apply(lambda x, s=s: int((x == s).sum())) for s in SCRIPTS},
        "instagram_scored": g["sentiment"].count(),
        **{f"instagram_{lab}": g["label"].apply(lambda x, lab=lab: int((x == lab).sum()))
           for lab in ("positive", "neutral", "negative")},
    }).reindex(days)
    # An empty log groups to object columns; make them numeric before filling.
    daily = daily.apply(pd.to_numeric).fillna(0).astype(int)
    daily["instagram_sentiment_mean"] = g["sentiment"].mean().reindex(days).round(4)
    return daily.rename_axis("date").reset_index()


def load_instagram(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    i_cfg = node_cfg["sources"].get("instagram", {})
    if not i_cfg.get("enabled"):
        return None, unavailable_report("instagram", node_key, i_cfg.get("reason", "instagram disabled for this node"))

    posts = read_csv_if_exists(post_log_path(node_key))
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty:
        return None, SourceReport(source="instagram", node_key=node_key, status="error",
                                   notes=["no post log yet: run scripts/collect_instagram.py, "
                                          "or point DHDE_LIVE_DATA_ROOT at a checkout of the live-data branch"])
    if posts is None:
        posts = pd.DataFrame(columns=POST_COLS)

    daily = to_daily(posts, runs)
    notes = [f"{len(posts)} post(s) in the log, {len(runs)} run(s), last on {runs.iloc[-1]['run_date']}",
             "days outside a run's coverage are missing, not 0; likes and comments are counts at scrape time"]
    return daily, validate_daily(daily, source="instagram", node_key=node_key, notes=notes)
