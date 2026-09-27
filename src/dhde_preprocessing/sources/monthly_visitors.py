"""
Monthly visitor counts per city and prefecture — the one demand signal
measured the same way everywhere in Japan, so it's what makes Fukui,
Ishikawa and Toyama nodes directly comparable.

Source: the Japan Tourism Agency's digital tourism statistics open data,
read straight from the publisher's page (OFFICIAL_PAGE): one CSV per year
for closed years, one per month for the current year, each split into
prefecture (2-digit lgcode, e.g. 17 = Ishikawa) and city (5-digit, e.g.
17201 = Kanazawa) files, from 2021-01.

Why not code4fukui/japan-kanko-stat, which mirrors the same files: its
downloader never re-fetches a file it already has. On 2026-04-14 the
publisher re-issued 2025 and Jan–Feb 2026 after prefectures revised their
tourism points, so the mirror holds pre-revision figures up to 2026-02
and revised ones from 2026-03 — a fake 2-6x jump for many Fukui towns
(Fukui city Feb 2026: 57,569 in the mirror, 186,829 revised). Reading
the publisher's files every run picks up any future revision too.

The revision did not reach back past 2025, so even the publisher's own
series breaks between 2024-12 and 2025-01 wherever a prefecture changed
its points a lot: city medians 2025/2024 are 1.65x for Fukui, against
1.09x nationally (Ishikawa 1.08x, Toyama 1.09x). For those prefectures
(REVISED_FROM) only 2025-01 onward is comparable month to month; the
report says so, and the monthly forecast uses nothing earlier.

The master table is daily, so each month's figure is repeated on every
day of that month in columns named `*_visitors_month` — the value is the
MONTH's total, not that day's. Dividing it into days (or not) is a
modeling-stage decision.

Optional source: only nodes that declare `monthly_visitors` in their
config get it (see join.OPTIONAL_SOURCES).
"""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_path, write_csv
from ..validation import SourceReport, unavailable_report, validate_daily
from .remote_csv import CACHE_DIR, fetch_csv

OFFICIAL_PAGE = "https://www.nihon-kankou.or.jp/home/jigyou/research/d-toukei/"
# e.g. https://d2eveo6c5xeu3l.cloudfront.net/city/city2025.csv, .../pref/pref202608.csv
CSV_LINK_RE = re.compile(r'href="(https://[^"]+/(?:city|pref)/((?:city|pref)\d{4}(?:\d{2})?)\.csv)"')
FILE_LIST_CACHE = f"{CACHE_DIR}/kanko_stat_files.csv"

# pref lgcode -> first month (YYYYMM) comparable with the months after it.
# Found by city-median 2025/2024 ratios above 1.3x: Fukui 1.65x, Ehime and
# Kochi similar, every other prefecture near the national 1.09x.
REVISED_FROM = {18: 202501, 38: 202501, 39: 202501}


def list_official_csvs() -> tuple[list[tuple[str, str]], str]:
    """[(url, stem)] for every city/pref CSV linked from the publisher's page.

    Falls back to the list cached by the last successful run, so a page
    outage doesn't blank the source when the files themselves are cached.
    """
    try:
        resp = requests.get(OFFICIAL_PAGE, timeout=60)
        resp.raise_for_status()
        files = sorted(set(CSV_LINK_RE.findall(resp.text)), key=lambda f: f[1])
        if not files:
            raise ValueError("no city/pref CSV links found on the page")
    except Exception as e:  # noqa: BLE001 - fall back to the cached list on any failure
        cached = read_csv_if_exists(resolve_path(FILE_LIST_CACHE))
        if cached is None:
            raise
        return list(cached.itertuples(index=False, name=None)), f"file list: page fetch failed ({e!r}), used cached list"
    write_csv(pd.DataFrame(files, columns=["url", "stem"]), resolve_path(FILE_LIST_CACHE))
    return files, f"file list: {len(files)} CSVs linked from {OFFICIAL_PAGE}"


@lru_cache(maxsize=1)
def load_official_table() -> tuple[pd.DataFrame, tuple[str, ...]]:
    """All publisher files as one (month YYYYMM, lgcode, n) table.

    Cached per process: every node reads the same national table, so it is
    downloaded once per build, not once per node.
    """
    files, list_note = list_official_csvs()
    frames, notes = [], [list_note]
    for url, stem in files:
        # The publisher's CSVs are Shift_JIS (cp932), not UTF-8.
        df, note = fetch_csv(url, f"kanko_stat_{stem}", encoding="cp932")
        if not note.startswith("fetched live"):
            notes.append(f"{stem}: {note}")
        frames.append(df[["年", "月", "地域コード", "人数"]])
    raw = pd.concat(frames, ignore_index=True)
    table = pd.DataFrame({
        "month": raw["年"].astype(int) * 100 + raw["月"].astype(int),
        "lgcode": raw["地域コード"].astype(int),
        "n": pd.to_numeric(raw["人数"], errors="coerce"),
    }).dropna(subset=["n"])
    # Files are read oldest first, so if a month ever appears in both a
    # yearly and a monthly file, the later-published one wins.
    table = table.drop_duplicates(["month", "lgcode"], keep="last").sort_values(["lgcode", "month"])
    return table.reset_index(drop=True), tuple(notes)


def expand_to_days(monthly: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """(month YYYYMM, n) -> one row per calendar day carrying its month's n."""
    months = pd.to_datetime(monthly["month"].astype(int).astype(str), format="%Y%m")
    days = pd.date_range(months.min(), months.max() + pd.offsets.MonthEnd(0), freq="D")
    by_month = pd.Series(monthly["n"].to_numpy(), index=months.dt.to_period("M"))
    return pd.DataFrame({"date": days, value_col: days.to_period("M").map(by_month).to_numpy()})


def load_monthly_visitors(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    m_cfg = node_cfg["sources"].get("monthly_visitors", {})
    if not m_cfg.get("enabled"):
        return None, unavailable_report("monthly_visitors", node_key, m_cfg.get("reason", "monthly visitors disabled for this node"))

    try:
        stats, fetch_notes = load_official_table()
    except Exception as e:  # noqa: BLE001 - a live fetch failure must not crash the whole run
        return None, SourceReport(source="monthly_visitors", node_key=node_key, status="error",
                                  notes=[f"official digital tourism statistics fetch failed: {e!r}"])
    frames, notes = [], list(fetch_notes)
    for level in ("city", "pref"):
        code = m_cfg[f"{level}_lgcode"]
        rows = stats[stats["lgcode"] == code].sort_values("month")
        if rows.empty:
            notes.append(f"lgcode {code} ({level}) not found in the official digital tourism statistics")
            continue
        frames.append(expand_to_days(rows, f"{level}_visitors_month"))
        notes.append(f"{level} lgcode {code}: {rows['month'].min()}–{rows['month'].max()}")

    if not frames:
        return None, SourceReport(source="monthly_visitors", node_key=node_key, status="error", notes=notes)

    daily = frames[0]
    for f in frames[1:]:
        daily = pd.merge(daily, f, on="date", how="outer")
    revised_from = REVISED_FROM.get(int(m_cfg["pref_lgcode"]))
    if revised_from:
        notes.append(f"only {revised_from} onward is comparable: the 2026-04 tourism-point revision "
                     "did not reach back, so earlier months count fewer points")
    notes.append("monthly totals repeated on every day of their month — not daily counts")
    report = validate_daily(daily, source="monthly_visitors", node_key=node_key, notes=notes)
    return daily, report
