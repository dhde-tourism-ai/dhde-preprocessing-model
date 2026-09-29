"""
Google Maps reviews per node: how many new reviews each day, their stars,
and how many are written in a language other than Japanese.

Optional source: only nodes that declare `google_reviews` with a
`place_id` get it (see join.OPTIONAL_SOURCES). It complements the rsi
source's Business Profile columns (average_rating, review_count_change),
which are only a daily average and a count: this one has the full star
split per day and the review language, which the app's Reviews and
Sentiment layers need.

Where the data comes from: the Apify "Google Maps Reviews Scraper" actor
(compass/Google-Maps-Reviews-Scraper). scripts/collect_google_reviews.py
runs it weekly in GitHub Actions and appends to the `live-data` branch;
it can also import an export someone ran by hand (the first snapshot, of
28 Sep 2026, was imported that way). Point DHDE_LIVE_DATA_ROOT at a
checkout of that branch to build with it.

What's kept: this repo and the `live-data` branch are public, so the log
holds no reviewer names, profile links, photos or review text. Only a
hash of the review id (to drop duplicates between runs), the date, the
stars, the language and whether there's text or an owner reply.

Coverage: the scraper returns the newest N reviews, so a run that hits
its cap only covers back to its oldest review. Each run records the days
it fully covers (`covered_from` to `covered_to`); on those days a day
without reviews is a real 0, outside them it stays missing.

Files, under the live-data root:
    google_reviews/{node_key}.csv        one row per review
    google_reviews/{node_key}_runs.csv   one row per run: coverage, and the
                                         place's overall rating and total
                                         review count on that day
"""
from __future__ import annotations

import hashlib
from datetime import timedelta, timezone

import pandas as pd

from ..config import read_csv_if_exists, resolve_live_path, write_csv
from ..validation import SourceReport, unavailable_report, validate_daily

LOG_DIR = "google_reviews"
REVIEW_COLS = ["review_hash", "place_id", "published_at", "date", "stars", "language",
               "has_text", "owner_replied", "scraped_at"]
RUN_COLS = ["run_date", "place_id", "fetched", "capped", "covered_from", "covered_to",
            "rating_total", "count_total"]
JST = timezone(timedelta(hours=9))
STARS = [1, 2, 3, 4, 5]
# Review languages counted on their own, the rest go to "other". Google
# tags Chinese by script: Traditional (zh-Hant) points to Taiwan or Hong
# Kong, Simplified (zh-Hans) to mainland China. That's a proxy for the
# visitor's market, not their nationality, so columns are named by
# language and the app says so. Plain "zh" (script unknown) is "other".
LANGS = {"ja": "ja", "en": "en", "zh-Hant": "zh_hant", "zh-Hans": "zh_hans", "ko": "ko"}


def review_log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}.csv")


def run_log_path(node_key: str) -> str:
    return resolve_live_path(f"{LOG_DIR}/{node_key}_runs.csv")


def _hash(review_id: str) -> str:
    return hashlib.sha256(str(review_id).encode()).hexdigest()[:16]


def _jst_date(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True, errors="coerce").dt.tz_convert(JST).dt.strftime("%Y-%m-%d")


def normalize(items: pd.DataFrame) -> pd.DataFrame:
    """Scraper output (CSV export or API items) -> REVIEW_COLS, personal fields dropped."""
    text = items.get("text", pd.Series(index=items.index, dtype=object))
    reply = items.get("responseFromOwnerText", pd.Series(index=items.index, dtype=object))
    lang = items.get("originalLanguage", pd.Series(index=items.index, dtype=object))
    out = pd.DataFrame({
        "review_hash": items["reviewId"].map(_hash),
        "place_id": items["placeId"],
        "published_at": pd.to_datetime(items["publishedAtDate"], utc=True, errors="coerce")
                          .dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": _jst_date(items["publishedAtDate"]),
        "stars": pd.to_numeric(items["stars"], errors="coerce"),
        "language": lang.where(lang.notna(), None),
        "has_text": text.fillna("").astype(str).str.strip().ne(""),
        "owner_replied": reply.fillna("").astype(str).str.strip().ne(""),
        "scraped_at": items.get("scrapedAt"),
    })
    return out.dropna(subset=["date", "stars"])


def run_summary(items: pd.DataFrame, reviews: pd.DataFrame, place_id: str,
                max_reviews: int | None, since: str | None) -> dict:
    """One run_log row for one place: what the run covers and the place's totals.

    A capped run (hit max_reviews) covers from the day after its oldest
    review, since that day may have more reviews it didn't get. An
    uncapped run covers from `since` if one was asked for, else it got
    every review the place has.
    """
    p_items = items[items["placeId"] == place_id]
    p_reviews = reviews[reviews["place_id"] == place_id]
    run_date = _jst_date(p_items["scrapedAt"]).max()
    oldest = p_reviews["date"].min()
    capped = bool(max_reviews) and len(p_reviews) >= max_reviews
    if capped:
        covered_from = (pd.Timestamp(oldest) + timedelta(days=1)).date().isoformat()
    else:
        covered_from = since or oldest
    first = p_items.iloc[0]
    return {
        "run_date": run_date, "place_id": place_id, "fetched": len(p_reviews), "capped": capped,
        "covered_from": covered_from,
        # The run day itself is only partly over when the scraper runs.
        "covered_to": (pd.Timestamp(run_date) - timedelta(days=1)).date().isoformat(),
        "rating_total": pd.to_numeric(first.get("totalScore"), errors="coerce"),
        "count_total": pd.to_numeric(first.get("reviewsCount"), errors="coerce"),
    }


def empty_run(items: pd.DataFrame, place_id: str, run_date: str, since: str | None) -> dict:
    """The run_log row for a run that found no new reviews for a place.

    It still covers `since` to the day before the run, so those days read
    0 reviews instead of missing. The place's totals are kept when the
    scraper returned a place row without reviews; otherwise they stay missing.
    """
    p_items = items[items["placeId"] == place_id] if "placeId" in items else items.iloc[0:0]
    first = p_items.iloc[0] if len(p_items) else {}
    return {
        "run_date": run_date, "place_id": place_id, "fetched": 0, "capped": False,
        "covered_from": since,
        "covered_to": (pd.Timestamp(run_date) - timedelta(days=1)).date().isoformat(),
        "rating_total": pd.to_numeric(first.get("totalScore"), errors="coerce"),
        "count_total": pd.to_numeric(first.get("reviewsCount"), errors="coerce"),
    }


def append(node_key: str, reviews: pd.DataFrame, run: dict) -> tuple[int, int]:
    """Add one run to a node's logs; returns (new reviews, reviews in the log).

    A review seen again replaces the earlier row (its stars or owner
    reply can change). Re-importing the same run is a no-op.
    """
    path = review_log_path(node_key)
    old = read_csv_if_exists(path)
    before = set(old["review_hash"]) if old is not None else set()
    parts = [df for df in (old, reviews) if df is not None and not df.empty]
    log = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=REVIEW_COLS)
    log = log.drop_duplicates("review_hash", keep="last").sort_values("published_at")
    write_csv(log[REVIEW_COLS], path)

    runs_path = run_log_path(node_key)
    runs = read_csv_if_exists(runs_path)
    new_run = pd.DataFrame([run])
    runs = pd.concat([runs, new_run], ignore_index=True) if runs is not None else new_run
    runs = runs.drop_duplicates(["run_date", "place_id"], keep="last").sort_values("run_date")
    write_csv(runs[RUN_COLS], runs_path)
    return len(set(reviews["review_hash"]) - before), len(log)


def since_date(node_key: str, overlap_days: int = 3) -> str | None:
    """Where the next run can start: a few days before the last covered day,
    so reviews Google publishes late aren't missed. None before the first run."""
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty:
        return None
    return (pd.Timestamp(runs["covered_to"].max()) - timedelta(days=overlap_days)).date().isoformat()


def language_group(lang: pd.Series) -> pd.Series:
    """Google language code -> a LANGS column suffix, "other", or missing (no text).

    Regional English (en-GB) counts as en; Chinese keeps its script tag.
    """
    # fillna first: on pandas 3 astype(str) keeps NaN, and an all-missing
    # column (a star-only node) then has no .str accessor.
    text = lang.astype(object).fillna("").astype(str)
    base = text.where(text.str.startswith("zh"), text.str.split("-").str[0])
    return base.map(LANGS).where(lang.isna() | base.isin(LANGS), "other").where(lang.notna())


def covered_days(runs: pd.DataFrame) -> pd.DatetimeIndex:
    days = [pd.date_range(r.covered_from, r.covered_to) for r in runs.itertuples()
            if pd.notna(r.covered_from) and pd.notna(r.covered_to) and r.covered_from <= r.covered_to]
    return pd.DatetimeIndex(sorted(set().union(*days))) if days else pd.DatetimeIndex([])


def to_daily(reviews: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Review log -> one row per covered day.

    Counts are 0 on a covered day without reviews; the star mean is
    missing then (no reviews isn't a rating). reviews_foreign counts
    reviews whose text isn't Japanese; star-only reviews have no language,
    so compare it with reviews_with_text, not reviews_new.

    reviews_lang_{ja,en,zh_hant,zh_hans,ko,other} split reviews_with_text
    by language, each with its star mean (reviews_lang_*_stars_mean,
    missing on a day without reviews in that language). Weight the mean
    by the count when adding days up into weeks.
    """
    days = covered_days(runs)
    r = reviews.copy()
    r["date"] = pd.to_datetime(r["date"])
    # A header-only log (runs written, no reviews yet) reads stars as text.
    r["stars"] = pd.to_numeric(r["stars"], errors="coerce")
    r = r[r["date"].isin(days)]
    has_text = r["has_text"].astype(str).str.lower() == "true"
    lang = r["language"].where(has_text)
    group = language_group(lang)
    g = r.assign(with_text=has_text, foreign=lang.notna() & (lang != "ja")).groupby("date")
    by_lang = {}
    for suffix in [*LANGS.values(), "other"]:
        stars = r["stars"].where(group == suffix).groupby(r["date"])
        by_lang[f"reviews_lang_{suffix}"] = stars.count()
        by_lang[f"reviews_lang_{suffix}_stars_mean"] = stars.mean().round(3)
    daily = pd.DataFrame({
        "reviews_new": g.size(),
        "reviews_stars_mean": g["stars"].mean().round(3),
        **{f"reviews_stars_{s}": g["stars"].apply(lambda x, s=s: int((x == s).sum())) for s in STARS},
        "reviews_with_text": g["with_text"].sum(),
        "reviews_foreign": g["foreign"].sum(),
        **by_lang,
    }).reindex(days)
    counts = [c for c in daily.columns if not c.endswith("stars_mean")]
    daily[counts] = daily[counts].fillna(0).astype(int)

    # The place's own totals, on the day before each run (the last day it covers).
    totals = (runs.assign(date=pd.to_datetime(runs["covered_to"]))
                  .groupby("date")[["rating_total", "count_total"]].last()
                  .rename(columns={"rating_total": "reviews_rating_total", "count_total": "reviews_count_total"}))
    daily = daily.join(totals, how="left")
    return daily.rename_axis("date").reset_index()


def load_google_reviews(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    g_cfg = node_cfg["sources"].get("google_reviews", {})
    if not g_cfg.get("enabled"):
        return None, unavailable_report("google_reviews", node_key,
                                        g_cfg.get("reason", "google_reviews disabled for this node"))

    reviews = read_csv_if_exists(review_log_path(node_key))
    runs = read_csv_if_exists(run_log_path(node_key))
    if runs is None or runs.empty:
        return None, SourceReport(source="google_reviews", node_key=node_key, status="error",
                                   notes=["no review log yet: run scripts/collect_google_reviews.py, "
                                          "or point DHDE_LIVE_DATA_ROOT at a checkout of the live-data branch"])
    if reviews is None:
        reviews = pd.DataFrame(columns=REVIEW_COLS)

    daily = to_daily(reviews, runs)
    latest = runs.iloc[-1]
    notes = [f"{len(reviews)} review(s) in the log, {len(runs)} run(s), last on {latest['run_date']}",
             f"place totals on the last run: {latest['rating_total']} stars, {latest['count_total']} reviews",
             "days outside a run's coverage are missing, not 0"]
    return daily, validate_daily(daily, source="google_reviews", node_key=node_key, notes=notes)
