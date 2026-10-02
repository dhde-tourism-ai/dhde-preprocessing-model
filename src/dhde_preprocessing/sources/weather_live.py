"""
Hourly weather, observed and forecast, kept as one history per node.

The daily pipeline's JMA weather (sources/weather.py) is observations only,
fetched once a day, so the dashboard had no real weather for the coming
days and its weather nudges ran on demo data. This module runs with the
hourly collector (scripts/collect_live.py) and keeps one row per node and
hour in `{live_data_root}/weather_hourly/{node_key}.csv`:

- **forecast**: Open-Meteo's JMA model (`jma_seamless`, free, no key) at
  the node's coordinates, for today and the next FORECAST_DAYS - 1 days. Each run
  replaces the forecast rows with the newer forecast; `issued_at` says
  when it was fetched.
- **observed**: the node's JMA station (the same ETRN pages and station as
  sources/weather.py) for the last OBSERVED_DAYS days. ETRN shows a day only
  once it's over, so today's hours stay forecast until tomorrow. A day that
  already has all 24 observed hours saved isn't fetched again, so most runs
  ask JMA for nothing and yesterday is fetched once it appears. An observation
  replaces the forecast for its hour and is never replaced by a forecast,
  so the file becomes the observed history as the hours pass.

Older history (from each node's start_date) is the daily pipeline's
`jma_cache/{node_key}_hourly.csv`; load_hourly_history() joins the two.
Open-Meteo's forecast is for the node's grid point and JMA's observation
is the station's, so the two can differ a little at the same hour (the
`source` column tells them apart). In the older jma_cache days, an hour
where JMA wrote `--` (no rain) is blank rather than 0.0: weather.py only
reads `--` as 0.0 since this module was added, for days fetched after that.

Forecast data: Open-Meteo (open-meteo.com), CC BY 4.0, based on JMA's models.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_live_path, resolve_path, write_csv
from ..validation import SourceReport, unavailable_report
from . import weather

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 9  # today + 8: the dashboard shows today and the 7 days after it, with a day to spare
OBSERVED_DAYS = 3  # yesterday and the 2 days before (ETRN has no page for today yet)
HISTORY_DIR = "weather_hourly"
COLS = ["timestamp", "source", "temp_c", "precip_1h_mm", "wind_speed_ms", "humidity_pct",
        "weather_code", "issued_at"]
JST = timezone(timedelta(hours=9))
FORECAST_RETRIES = 3
POINT_TOLERANCE_DEG = 0.1  # Open-Meteo snaps a point to its model grid (a few km)
RETRY_PAUSE_S = 5.0  # x attempt, between Open-Meteo retries
FETCH_PAUSE_S = 1.0  # between JMA page requests, as in weather.py's backfill


def _get_forecast(lats: list[float], lons: list[float]) -> list[dict]:
    """Open-Meteo's answer for these points, one per point in order. Retried: from
    GitHub's runners about half the one-point requests timed out."""
    last: Exception | None = None
    for attempt in range(1, FORECAST_RETRIES + 1):
        try:
            resp = requests.get(FORECAST_URL, timeout=60, params={
                "latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
                "models": "jma_seamless", "timezone": "Asia/Tokyo",
                "forecast_days": FORECAST_DAYS, "wind_speed_unit": "ms",
                "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m,weather_code",
            })
            if resp.status_code != 200:
                raise RuntimeError(f"Open-Meteo returned HTTP {resp.status_code}")
            body = resp.json()
            out = body if isinstance(body, list) else [body]  # one point: a single object
            if len(out) != len(lats):
                raise RuntimeError(f"Open-Meteo returned {len(out)} points for {len(lats)}")
            # The answer is in request order; check it, so a mix-up can't give a node another's weather.
            for p, la, lo in zip(out, lats, lons):
                if abs(p.get("latitude", 999) - la) > POINT_TOLERANCE_DEG or abs(p.get("longitude", 999) - lo) > POINT_TOLERANCE_DEG:
                    raise RuntimeError(f"Open-Meteo point {p.get('latitude')},{p.get('longitude')} is not {la},{lo}")
            return out
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last = e
            if attempt < FORECAST_RETRIES:
                time.sleep(RETRY_PAUSE_S * attempt)
    raise last  # type: ignore[misc]


def _frame(point: dict, issued_at: str) -> pd.DataFrame:
    h = point["hourly"]
    return pd.DataFrame({
        "timestamp": pd.to_datetime(h["time"]),
        "source": "forecast",
        "temp_c": h["temperature_2m"],
        "precip_1h_mm": h["precipitation"],
        "wind_speed_ms": h["wind_speed_10m"],
        "humidity_pct": h["relative_humidity_2m"],
        "weather_code": h["weather_code"],
        "issued_at": issued_at,
    })


def fetch_forecasts(node_cfgs: list[dict], now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """Every node's forecast in one request, for collect(forecasts=...). Empty if it fails:
    then Open-Meteo is likely down, and the nodes keep their saved forecast rather than
    each retrying on its own (22 nodes x 3 tries took over an hour)."""
    nodes = [n for n in node_cfgs if {"lat", "lon"} <= set(n.get("coordinates") or {})]
    if not nodes:
        return {}
    issued_at = (now or datetime.now(timezone.utc)).isoformat(timespec="minutes")
    try:
        points = _get_forecast([n["coordinates"]["lat"] for n in nodes], [n["coordinates"]["lon"] for n in nodes])
        return {n["node_key"]: _frame(p, issued_at) for n, p in zip(nodes, points)}
    except (requests.RequestException, RuntimeError, KeyError, ValueError, TypeError):
        return {}


def _complete_days(saved: pd.DataFrame) -> set[date]:
    """Days with all 24 hours observed. JMA labels an hour by its end, so a day's
    hours run 01:00 to 24:00 (00:00 the next day)."""
    obs = saved[saved["source"] == "observed"]
    if obs.empty:
        return set()
    counts = (pd.to_datetime(obs["timestamp"]) - pd.Timedelta(hours=1)).dt.date.value_counts()
    return set(counts[counts >= 24].index)


def _observed(w_cfg: dict, today: date, saved: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """JMA rows for the last OBSERVED_DAYS days not already complete in `saved`,
    how many days were fetched, and how many failed."""
    done = _complete_days(saved)
    days = [today - timedelta(days=k) for k in range(OBSERVED_DAYS, 0, -1)]
    days = [d for d in days if d not in done]
    rows, failed = [], 0
    for i, d in enumerate(days):
        if i:
            time.sleep(FETCH_PAUSE_S)
        try:
            rows += weather._fetch_day(w_cfg["prec_no"], w_cfg["block_no"], w_cfg["page"], d)
        except Exception:  # noqa: BLE001 - one bad day mustn't drop the others
            failed += 1
    if not rows:
        return pd.DataFrame(columns=COLS), len(days), failed
    df = pd.DataFrame(rows)
    out = pd.DataFrame({"timestamp": pd.to_datetime(df["timestamp"]), "source": "observed"})
    for c in ("temp_c", "precip_1h_mm", "wind_speed_ms", "humidity_pct"):
        out[c] = pd.to_numeric(df.get(c), errors="coerce")
    out["weather_code"] = None
    out["issued_at"] = None
    # A row JMA hasn't filled yet (all blank) is not an observation.
    return out.dropna(subset=["temp_c", "precip_1h_mm", "wind_speed_ms"], how="all")[COLS], len(days), failed


def merge(saved: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """One row per hour: an observation beats a forecast, a newer forecast beats an older one."""
    frames = [f for f in (saved, new) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["_rank"] = (df["source"] == "observed").astype(int)
    df["_order"] = range(len(df))  # later rows (this run) win ties
    df = df.sort_values(["timestamp", "_rank", "_order"]).drop_duplicates("timestamp", keep="last")
    return df.drop(columns=["_rank", "_order"]).reset_index(drop=True)[COLS]


def _read(path: str) -> pd.DataFrame:
    try:
        df = read_csv_if_exists(path)
    except pd.errors.EmptyDataError:  # 0-byte file
        df = None
    if df is None:
        return pd.DataFrame(columns=COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def collect(node_cfg: dict, now: datetime | None = None,
            forecasts: dict[str, pd.DataFrame] | None = None) -> tuple[pd.DataFrame | None, SourceReport]:
    """Fetch this hour's forecast and the latest observations, merge them into the saved file.
    `forecasts` (from fetch_forecasts) replaces the node's own request; a node missing from it
    (the batch failed) keeps its saved forecast. Without `forecasts`, the node asks for itself."""
    node_key = node_cfg["node_key"]
    w_cfg = node_cfg["sources"].get("weather", {})
    c = node_cfg.get("coordinates") or {}
    if "lat" not in c or "lon" not in c:
        return None, unavailable_report("weather_live", node_key, "no coordinates for this node")

    now = now or datetime.now(timezone.utc)
    path = resolve_live_path(f"{HISTORY_DIR}/{node_key}.csv")
    saved = _read(path)
    notes, new = [], []

    try:
        if forecasts is not None:
            if node_key not in forecasts:
                raise RuntimeError("not in this run's one-request forecast")
            new.append(forecasts[node_key])
        else:
            new.append(_frame(_get_forecast([c["lat"]], [c["lon"]])[0], now.isoformat(timespec="minutes")))
    except (requests.RequestException, RuntimeError, KeyError, ValueError, TypeError) as e:
        msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
        notes.append(f"forecast failed ({msg}): kept the saved forecast")

    if w_cfg.get("enabled"):
        obs, fetched, failed = _observed(w_cfg, now.astimezone(JST).date(), saved)
        new.append(obs)
        notes.append(f"JMA: fetched {fetched} of the last {OBSERVED_DAYS} days (the others were already complete)")
        if failed:
            notes.append(f"{failed} of {fetched} JMA day(s) failed: retried next run")
    else:
        notes.append("no JMA station for this node: forecast only")

    fetched = [f for f in new if not f.empty]
    merged = merge(saved, pd.concat(fetched, ignore_index=True) if fetched else pd.DataFrame(columns=COLS))
    if merged.empty:
        return None, SourceReport(source="weather_live", node_key=node_key, status="error",
                                   notes=notes + ["nothing fetched and nothing saved yet"])
    # Written every run: the live-data commit step only commits when the content changed.
    write_csv(merged.assign(timestamp=merged["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")), path)

    n_obs = int((merged["source"] == "observed").sum())
    n_fc = int((merged["source"] == "forecast").sum())
    notes.append(f"{len(merged)} hour(s) saved: {n_obs} observed, {n_fc} forecast")
    return merged, SourceReport(source="weather_live", node_key=node_key, status="ok", row_count=len(merged),
                                date_min=f"{merged['timestamp'].min():%Y-%m-%d %H:00}",
                                date_max=f"{merged['timestamp'].max():%Y-%m-%d %H:00}", notes=notes)


def load_hourly_history(node_key: str) -> pd.DataFrame:
    """The full hourly weather for a node: the daily pipeline's JMA history, then the
    collector's file (its observations and the forecast) for the hours after it."""
    live = _read(resolve_live_path(f"{HISTORY_DIR}/{node_key}.csv"))
    cache = Path(resolve_path(weather.CACHE_DIR)) / f"{node_key}_hourly.csv"
    if not cache.exists():
        return live
    old = weather._load_cache(cache)
    old = pd.DataFrame({
        "timestamp": old["timestamp"], "source": "observed",
        **{c: pd.to_numeric(old[c], errors="coerce") for c in ("temp_c", "precip_1h_mm", "wind_speed_ms", "humidity_pct")},
        "weather_code": None, "issued_at": None,
    })
    # The collector's rows go last, so for an hour in both its newer value wins
    # (an observation over the cache's, or the cache's observation over a forecast).
    return merge(old, live)
