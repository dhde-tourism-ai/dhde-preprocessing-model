"""
JMA weather ingestion — automated.

This does NOT use JMA's obsdl portal (https://www.data.jma.go.jp/risk/obsdl/) —
that one really is a form/session-driven download, not scriptable. Instead
it scrapes JMA's public ETRN hourly-observation pages
(https://www.data.jma.go.jp/obd/stats/etrn/view/{page}.php), one day at a
time — the same approach already proven working in the sibling
hokuriku-tourism-ai-governance-dashboard repo's jma/fetch_jma_monthly.py,
adapted here to fit this repo's config-driven pattern and to cache
incrementally instead of requiring a human to run it manually each time.

Each node's config carries prec_no/block_no/page (JMA's own station
addressing for this endpoint — a different numbering than the
jma_station.csv "station_id" used to originally confirm which physical
station is nearest each node) plus a start_date for how far back to
backfill.

Caching: results are merged into a per-node CSV under
`{workspace_root}/jma_cache/{node_key}_hourly.csv`. On each run, only
days missing from that cache are actually fetched from JMA — full
history is scraped once, and subsequent runs just top up the gap since
last time, rather than hammering JMA's site on every pipeline run. This
repo's cache was seeded from the sibling dashboard repo's already-fetched
2024-12 → 2026-03 history (see PR that added this module) rather than
re-scraping it from scratch.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

BASE_URL = "https://www.data.jma.go.jp/obd/stats/etrn/view"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
CACHE_DIR = "jma_cache"  # under the workspace root, alongside the sibling data repos

HOURLY_COLS = [
    "timestamp", "snow_depth_cm", "snowfall_1h_cm", "temp_c",
    "precip_1h_mm", "sun_1h_h", "wind_speed_ms", "weather_type", "humidity_pct",
]


def _find_col(cols: list, *keywords: str, level: int | None = None) -> int | None:
    for i, c in enumerate(cols):
        tup = c if isinstance(c, tuple) else (c,)
        if level is not None:
            if level >= len(tup):
                continue
            text = str(tup[level])
        else:
            text = " ".join(str(x) for x in tup)
        if all(k in text for k in keywords):
            return i
    return None


def _clean_cell(idx: int | None, vals: list) -> str:
    if idx is None or idx >= len(vals):
        return ""
    v = str(vals[idx]).strip()
    if v in ("--", "///", "×", "nan", "NaN", "#", ""):
        return ""
    return re.sub(r"[)\]]+$", "", v).strip()


def _extract_rows(df: pd.DataFrame, page_type: str) -> list[dict]:
    cols = df.columns.tolist()
    i_hour = _find_col(cols, "時")
    i_precip = _find_col(cols, "降水量")
    i_temp = _find_col(cols, "気温")
    i_humidity = _find_col(cols, "湿度")
    i_wind_speed = _find_col(cols, "風速", level=1)
    i_sun = _find_col(cols, "日照")
    i_snowfall = _find_col(cols, "降雪", level=1)
    i_snowdepth = _find_col(cols, "積雪", level=1)
    i_weather = _find_col(cols, "天気") if page_type == "hourly_s1" else None

    rows = []
    for _, row in df.iterrows():
        vals = row.values.tolist()
        hour = _clean_cell(i_hour, vals)
        if not hour or not hour.isdigit():
            continue
        rows.append({
            "hour": int(hour),
            "precip_1h_mm": _clean_cell(i_precip, vals),
            "temp_c": _clean_cell(i_temp, vals),
            "humidity_pct": _clean_cell(i_humidity, vals),
            "wind_speed_ms": _clean_cell(i_wind_speed, vals),
            "sun_1h_h": _clean_cell(i_sun, vals),
            "snowfall_1h_cm": _clean_cell(i_snowfall, vals),
            "snow_depth_cm": _clean_cell(i_snowdepth, vals),
            "weather_type": _clean_cell(i_weather, vals) if i_weather is not None else "",
        })
    return rows


def _fetch_html(url: str, timeout: int = 30, retries: int = 3) -> str:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"JMA fetch failed after {retries} attempts: {last_err}")


def _fetch_day(prec_no: str, block_no: str, page: str, day: date) -> list[dict]:
    url = f"{BASE_URL}/{page}.php?prec_no={prec_no}&block_no={block_no}&year={day.year}&month={day.month}&day={day.day}&view="
    html = _fetch_html(url)
    if html.count("<td") < 10:
        return []  # no data table (e.g. a future date, or a day with no observations yet)
    tables = pd.read_html(StringIO(html))
    data_tables = [t for t in tables if t.shape[0] >= 20]
    if not data_tables:
        return []

    rows = _extract_rows(data_tables[0], page)
    out = []
    for r in rows:
        hour = r.pop("hour")
        if hour == 24:
            ts = datetime(day.year, day.month, day.day) + timedelta(days=1)
            r["timestamp"] = ts.strftime("%Y-%m-%d 00:00:00")
        else:
            r["timestamp"] = f"{day.year}-{day.month:02d}-{day.day:02d} {hour:02d}:00:00"
        out.append(r)
    return out


def _load_cache(cache_path: Path) -> pd.DataFrame:
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    return pd.DataFrame(columns=HOURLY_COLS)


def _fetch_and_cache(prec_no: str, block_no: str, page: str, start_date: date, end_date: date,
                      cache_path: Path, sleep: float = 1.0,
                      max_consecutive_failures: int = 8) -> tuple[pd.DataFrame, int, int]:
    """Returns (data, n_days_fetched_ok, n_days_failed)."""
    cached = _load_cache(cache_path)
    have_days = set(cached["timestamp"].dt.normalize().dt.date) if not cached.empty else set()

    all_days = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    missing_days = [d for d in all_days if d not in have_days]

    # One bad day (a transient site hiccup, a genuinely missing/holiday
    # observation, an odd page layout) must not throw away every other
    # day already fetched this run — catch per day, keep going, and only
    # give up early if failures start stacking up consecutively (a real
    # outage), same pattern as the proven jma/fetch_jma_monthly.py script
    # this was ported from.
    new_rows: list[dict] = []
    consecutive_failures = 0
    n_ok = 0
    for d in missing_days:
        try:
            day_rows = _fetch_day(prec_no, block_no, page, d)
            new_rows.extend(day_rows)
            consecutive_failures = 0
            n_ok += 1
        except Exception:  # noqa: BLE001 - one bad day must not abort the whole backfill
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                break
        time.sleep(sleep)

    n_failed = len(missing_days) - n_ok

    if not new_rows:
        return cached, n_ok, n_failed

    new_df = pd.DataFrame(new_rows)
    for c in HOURLY_COLS:
        if c not in new_df.columns:
            new_df[c] = ""
    new_df["timestamp"] = pd.to_datetime(new_df["timestamp"])
    combined = pd.concat([cached, new_df[HOURLY_COLS]], ignore_index=True)
    combined = combined.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(cache_path, index=False)
    return combined, n_ok, n_failed


def load_weather(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    w_cfg = node_cfg["sources"].get("weather", {})
    if not w_cfg.get("enabled"):
        return None, unavailable_report("weather", node_key, w_cfg.get("reason", "weather disabled for this node"))

    prec_no, block_no, page = w_cfg["prec_no"], w_cfg["block_no"], w_cfg["page"]
    start_date = pd.to_datetime(w_cfg.get("start_date", "2024-12-01")).date()
    end_date = date.today()

    cache_path = Path(resolve_path(CACHE_DIR)) / f"{node_key}_hourly.csv"

    try:
        hourly, n_ok, n_failed = _fetch_and_cache(prec_no, block_no, page, start_date, end_date, cache_path)
    except Exception as e:  # noqa: BLE001 - a live scrape failure must not crash the whole run
        return None, SourceReport(source="weather", node_key=node_key, status="error",
                                   notes=[f"JMA scrape failed: {e!r}"])

    if hourly.empty:
        return None, SourceReport(source="weather", node_key=node_key, status="error",
                                   notes=["no rows scraped or cached"])

    for c in ("precip_1h_mm", "temp_c", "humidity_pct", "wind_speed_ms", "sun_1h_h", "snow_depth_cm"):
        hourly[c] = pd.to_numeric(hourly[c], errors="coerce")

    hourly["date"] = hourly["timestamp"].dt.normalize()
    daily = hourly.groupby("date").agg(
        precip=("precip_1h_mm", "sum"), temp=("temp_c", "mean"), wind=("wind_speed_ms", "mean"),
        sun=("sun_1h_h", "mean"), humidity=("humidity_pct", "mean"), snow_depth=("snow_depth_cm", "mean"),
    ).reset_index()

    notes = [f"station prec_no={prec_no} block_no={block_no} ({page})",
             f"{n_ok} day(s) freshly scraped this run, rest from cache at {cache_path}"]
    if n_failed:
        notes.append(f"{n_failed} day(s) failed to scrape this run (transient site issues or genuinely missing observations) — will retry next run")
    report = validate_daily(daily, source="weather", node_key=node_key, notes=notes)
    return daily, report
