"""
Public social media posts and comments that name each node, with their
sentiment: how many a day, on which platform, how positive.

Optional source: only nodes that declare `social_listening` with
`keywords` get it (see join.OPTIONAL_SOURCES). It feeds the app's Social
media and Sentiment layers, next to the Instagram counts.

Where the data comes from: Anugra's social-collector
(github.com/anugraaa-rizky/social-media-scraping), which searches
Bluesky, YouTube and Reddit by keyword. scripts/collect_social.py runs it
weekly in GitHub Actions with every node's keywords, matches each post
to the nodes it names, scores it (sentiment.py) and appends to the
`live-data` branch. Comments count for the node of the post they're
under (a comment on a Tojinbo video rarely names Tojinbo again).

Matching: a post belongs to a node when its text contains one of the
node's keywords, every word of it (case ignored): "恐竜博物館 福井"
needs both. Keywords are searched for and matched on, so a search hit
that doesn't actually name the place isn't counted.

What's kept: this repo and the `live-data` branch are public, so the log
holds no text, usernames or links. Only a hash of the item id (to drop
duplicates between runs), its platform, post or comment, day, its
language, likes at scrape time, and the sentiment score, label and route
(scored as written, converted from Traditional Chinese, or translated to
English first; see sentiment.py). The day, not the time: an exact time
and a keyword find the post or comment, and with it who wrote it. Likes
are rounded to the nearest 10 for the same reason.

Coverage, per platform: a run asks for items newer than the last
covered day. A node's keyword search that hit its cap only covers back
to its oldest result, and one that failed (quota, outage) covers nothing
that week. On a covered day without mentions it's a real 0, outside
coverage missing. A platform without its API key isn't run, so its
column stays missing rather than 0. Comments only arrive for posts found
in the same run.

Files, under the live-data root:
    social/{node_key}.csv        one row per post or comment
    social/{node_key}_runs.csv   one row per run and platform: its coverage
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import pandas as pd

from ..config import read_csv_if_exists, resolve_live_path, write_csv
from ..validation import SourceReport, unavailable_report, validate_daily
from ..sentiment import lang_group
from .instagram import round_to

LOG_DIR = "social"
PLATFORMS = ["bluesky", "youtube", "reddit"]
LOG_COLS = ["item_hash", "platform", "kind", "date", "language", "likes", "sentiment", "label",
            "route", "scraped_at"]
RUN_COLS = ["run_date", "platform", "fetched", "capped", "covered_from", "covered_to"]
# Language groups, as in google_reviews, plus Arabic.
LANGS = ["ja", "en", "zh_hant", "zh_hans", "ko", "ar", "other"]
LABELS = ["positive", "neutral", "negative"]


def log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}.csv")


def run_log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}_runs.csv")


def node_keywords(node_cfg: dict) -> list[str]:
    s = node_cfg["sources"].get("social_listening", {})
    return [str(k) for k in s.get("keywords", [])] if s.get("enabled") else []


def matches(text, keyword: str) -> bool:
    """Every word of the keyword appears in the text, case ignored."""
    if not isinstance(text, str):
        return False
    t = text.casefold()
    return all(w in t for w in keyword.casefold().split())


def nodes_named(text, keywords: dict[str, list[str]]) -> list[str]:
    """Nodes whose keywords the text contains. {node_key: keywords} -> [node_key]."""
    return [n for n, kws in keywords.items() if any(matches(text, k) for k in kws)]


def _hash(platform: str, item_id) -> str:
    return hashlib.sha256(f"{platform}:{item_id}".encode()).hexdigest()[:16]


def normalize(items: pd.DataFrame, scores: list | None = None, languages: list | None = None) -> pd.DataFrame:
    """Collector items -> LOG_COLS, text dropped.

    `items` has platform, id, kind ("post"/"comment"), created_at (UTC)
    and like_count. `scores` is one sentiment.Result or None per row;
    `languages` the detected language per row, for rows without a score.
    """
    if items.empty:
        return pd.DataFrame(columns=LOG_COLS)
    ts = pd.to_datetime(items["created_at"], utc=True, errors="coerce")
    scores = scores if scores is not None else [None] * len(items)
    languages = languages if languages is not None else [None] * len(items)
    likes = pd.to_numeric(items.get("like_count"), errors="coerce")
    out = pd.DataFrame({
        "item_hash": [_hash(p, i) for p, i in zip(items["platform"], items["id"])],
        "platform": items["platform"].values,
        "kind": items["kind"].values,
        "date": ts.dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d").values,
        "language": [s.language if s else lang for s, lang in zip(scores, languages)],
        # Reddit scores go negative (downvotes); likes below 0 aren't likes.
        "likes": round_to(likes.where(likes >= 0), 10).values,
        "sentiment": [s.score if s else None for s in scores],
        "label": [s.label if s else None for s in scores],
        "route": [s.route if s else None for s in scores],
        "scraped_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return out.dropna(subset=["date"])


def coverage(run_date: str, since: str | None, capped_oldest: str | None, failed: bool = False) -> tuple:
    """(covered_from, covered_to) for one node and platform in one run.

    Up to the day before the run (that day is only partly over). From
    `since`, or from the day after the oldest result of a capped search,
    since that day may have more it didn't get. A failed search covers
    nothing: (None, None).
    """
    if failed:
        return None, None
    covered_to = (pd.Timestamp(run_date) - timedelta(days=1)).date().isoformat()
    if capped_oldest:
        covered_from = (pd.Timestamp(capped_oldest) + timedelta(days=1)).date().isoformat()
    else:
        covered_from = since
    return covered_from, covered_to


def append(node_key: str, items: pd.DataFrame, runs: list[dict]) -> tuple[int, int]:
    """Add one run to a node's logs; returns (new items, items in the log).

    An item seen again replaces the earlier row (latest likes). Rerunning
    the same run is a no-op.
    """
    path = log_path(node_key)
    old = read_csv_if_exists(path)
    before = set(old["item_hash"]) if old is not None else set()
    parts = [df for df in (old, items) if df is not None and not df.empty]
    log = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=LOG_COLS)
    log = log.drop_duplicates("item_hash", keep="last").sort_values("date", kind="stable")
    write_csv(log[LOG_COLS], path)

    runs_path = run_log_path(node_key)
    old_runs = read_csv_if_exists(runs_path)
    new_runs = pd.DataFrame(runs, columns=RUN_COLS)
    all_runs = pd.concat([old_runs, new_runs], ignore_index=True) if old_runs is not None else new_runs
    all_runs = all_runs.drop_duplicates(["run_date", "platform"], keep="last").sort_values(["run_date", "platform"])
    write_csv(all_runs[RUN_COLS], runs_path)
    new = set(items["item_hash"]) - before if not items.empty else set()
    return len(new), len(log)


def since_date(node_key: str, platform: str, overlap_days: int = 2) -> str | None:
    """Where a platform's next run can start for this node: a couple of days
    before its last covered day. None before its first covered run."""
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty:
        return None
    covered = runs.loc[runs["platform"] == platform, "covered_to"].dropna()
    if covered.empty:
        return None
    return (pd.Timestamp(covered.max()) - timedelta(days=overlap_days)).date().isoformat()


def covered_days(runs: pd.DataFrame) -> pd.DatetimeIndex:
    days = [pd.date_range(r.covered_from, r.covered_to) for r in runs.itertuples()
            if pd.notna(r.covered_from) and pd.notna(r.covered_to) and r.covered_from <= r.covered_to]
    return pd.DatetimeIndex(sorted(set().union(*days))) if days else pd.DatetimeIndex([])


def to_daily(log: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Item log -> one row per day some platform covers.

    social_mentions (posts + comments), _posts, _comments, _likes,
    _{platform}_mentions, _positive / _neutral / _negative, _scored and
    social_sentiment_mean (the mean score of the scored ones, missing
    when none were scored), _translated (scored via English),
    social_lang_{ja,en,zh_hant,zh_hans,ko,ar,other}, and
    social_platforms: how many platforms cover the day, since a day only
    Bluesky covered isn't comparable with one all three did. A platform's
    column is missing on days it didn't cover.
    """
    by_platform = {p: covered_days(runs[runs["platform"] == p]) for p in PLATFORMS}
    days = pd.DatetimeIndex(sorted(set().union(*by_platform.values())))
    items = log.copy()
    items["date"] = pd.to_datetime(items["date"])
    keep = pd.Series(False, index=items.index)
    for p, d in by_platform.items():
        keep |= (items["platform"] == p) & items["date"].isin(d)
    items = items[keep]
    items["sentiment"] = pd.to_numeric(items["sentiment"], errors="coerce")
    items["likes"] = pd.to_numeric(items["likes"], errors="coerce")
    items["lang"] = items["language"].map(lambda x: lang_group(x) if isinstance(x, str) else "other")

    g = items.groupby("date")
    count = lambda col, val: g[col].apply(lambda x: int((x == val).sum()))  # noqa: E731
    counts = pd.DataFrame({
        "social_mentions": g.size(),
        "social_posts": count("kind", "post"),
        "social_comments": count("kind", "comment"),
        "social_likes": g["likes"].sum(),
        **{f"social_{p}_mentions": count("platform", p) for p in PLATFORMS},
        **{f"social_{lab}": count("label", lab) for lab in LABELS},
        "social_scored": g["sentiment"].count(),
        "social_translated": count("route", "translated"),
        **{f"social_lang_{lang}": count("lang", lang) for lang in LANGS},
    }).reindex(days)
    # An empty log groups to object columns; make them numeric before filling.
    daily = counts.apply(pd.to_numeric).fillna(0).astype(int)
    daily["social_sentiment_mean"] = g["sentiment"].mean().reindex(days).round(4)
    daily["social_platforms"] = [sum(d in by_platform[p] for p in PLATFORMS) for d in days]
    for p in PLATFORMS:
        daily.loc[~daily.index.isin(by_platform[p]), f"social_{p}_mentions"] = float("nan")
    return daily.rename_axis("date").reset_index()


def load_social_listening(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    s_cfg = node_cfg["sources"].get("social_listening", {})
    if not s_cfg.get("enabled"):
        return None, unavailable_report("social_listening", node_key,
                                        s_cfg.get("reason", "social_listening disabled for this node"))

    log = read_csv_if_exists(log_path(node_key))
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty:
        return None, SourceReport(source="social_listening", node_key=node_key, status="error",
                                   notes=["no mention log yet: run scripts/collect_social.py, "
                                          "or point DHDE_LIVE_DATA_ROOT at a checkout of the live-data branch"])
    if log is None:
        log = pd.DataFrame(columns=LOG_COLS)

    daily = to_daily(log, runs)
    if daily.empty:
        return None, SourceReport(source="social_listening", node_key=node_key, status="error",
                                   notes=[f"{len(runs)} run(s) but none covered a full day yet"])
    platforms = sorted(runs.loc[runs["covered_to"].notna(), "platform"].unique())
    notes = [f"{len(log)} item(s) in the log from {', '.join(platforms) or 'no platform'}, "
             f"{runs['run_date'].nunique()} run(s), last on {runs['run_date'].max()}",
             "days a platform didn't cover are missing for it; social_platforms says how many covered each day",
             "sentiment is a model's guess per post (sentiment.py), likes are counts at scrape time"]
    return daily, validate_daily(daily, source="social_listening", node_key=node_key, notes=notes)
