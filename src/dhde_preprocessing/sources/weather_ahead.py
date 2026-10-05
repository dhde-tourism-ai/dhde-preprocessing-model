"""
The weather forecast for each day as it was 1 to 7 days before it, per node:
an input the visitor forecast can use without leakage.

The visitor model forecasts 1 to 7 days ahead. On the day a forecast is made,
the weather of the days ahead isn't known, only its forecast, so that is all
the model may use. Training on observed weather and forecasting with forecasts
would make the backtest look better than any live forecast can be. So day d,
forecast h days ahead, uses the weather forecast for d issued h days before d.

Open-Meteo's Previous Runs API keeps those old forecasts: `*_previous_dayN` is
the value for an hour from the model run N days before it (JMA's model,
`jma_seamless`, the same as the collector and the dashboard; from early 2024).
It covers the days ahead too: for each of them, the run N days earlier has
already happened.

Per node, day and lead N (1..7), over daytime hours (DAY_HOURS, when people visit):
- `rain_mm_lead{N}`: rain forecast for the daytime hours, mm
- `temp_max_lead{N}`: highest daytime temperature forecast, deg C

Cached at `{workspace_root}/open_meteo_cache/{node_key}_weather_ahead.csv`
(resolve_path, so an s3:// root works). An old run never changes, so a run
asks only for the days after the cache and the last RECHECK_DAYS days (whose
shorter leads appear day by day). A gap further back stays a gap: the archive
won't fill it later.

Forecast data: Open-Meteo (open-meteo.com), CC BY 4.0, based on JMA's models.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_path, write_csv

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
CACHE_DIR = "open_meteo_cache"
FIRST_DAY = date(2024, 3, 1)  # the archive's JMA runs start in early 2024; checked 2026-10-05
LEADS = range(1, 8)  # forecast.HORIZON days
# Hours labelled 10:00 to 18:00: Open-Meteo's rain at 10:00 fell 09:00 to 10:00, so this is 09:00-18:00.
DAY_HOURS = range(10, 19)
VALUES = {"rain_mm": "precipitation", "temp_max": "temperature_2m"}
COLUMNS = [f"{name}_lead{n}" for n in LEADS for name in VALUES]
RECHECK_DAYS = 14  # covers the 7 days ahead and a week of a late or failed run
RETRIES = 3
RETRY_PAUSE_S = 5.0
JST = timezone(timedelta(hours=9))


def _get(lat: float, lon: float, start: date, end: date) -> dict:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(URL, timeout=180, params={
                "latitude": lat, "longitude": lon, "models": "jma_seamless", "timezone": "Asia/Tokyo",
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "hourly": ",".join(f"{v}_previous_day{n}" for n in LEADS for v in VALUES.values()),
            })
            if resp.status_code != 200:
                raise RuntimeError(f"Open-Meteo returned HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last = e
            if attempt < RETRIES:
                time.sleep(RETRY_PAUSE_S * attempt)
    raise last  # type: ignore[misc]


def daily(body: dict) -> pd.DataFrame:
    """Open-Meteo's hourly answer -> one row per day with COLUMNS. A value with any
    daytime hour missing is left blank, not summed over the hours that are there."""
    h = body["hourly"]
    ts = pd.to_datetime(pd.Series(h["time"]))
    day = ts.dt.hour.isin(DAY_HOURS)
    out = pd.DataFrame(index=pd.DatetimeIndex(sorted(ts[day].dt.normalize().unique()), name="date"))
    for n in LEADS:
        for name, var in VALUES.items():
            v = pd.to_numeric(pd.Series(h[f"{var}_previous_day{n}"]), errors="coerce")[day]
            if name == "rain_mm":
                v = v.clip(lower=0)  # a few archived hours are slightly negative (-0.1 mm); rain can't be
            g = v.groupby(ts[day].dt.normalize().to_numpy())
            agg = (g.sum().round(1) if name == "rain_mm" else g.max()).where(g.count() == len(DAY_HOURS))
            out[f"{name}_lead{n}"] = agg.reindex(out.index)
    return out.reset_index()


def load(node_cfg: dict, until: date | None = None) -> pd.DataFrame:
    """COLUMNS per day for this node, FIRST_DAY to `until` (default: 7 days after
    today, JST), from the cache plus whatever it's missing. A failed fetch returns
    the cache as it is: the model then treats the missing days as unknown."""
    c = node_cfg.get("coordinates") or {}
    path = resolve_path(f"{CACHE_DIR}/{node_cfg['node_key']}_weather_ahead.csv")
    cached = read_csv_if_exists(path, parse_dates=["date"])
    if cached is None:
        cached = pd.DataFrame(columns=["date", *COLUMNS])
    if "lat" not in c or "lon" not in c:
        return cached
    today = datetime.now(JST).date()
    until = until or today + timedelta(days=max(LEADS))
    settled = set(pd.to_datetime(cached["date"]).dt.date) - {today - timedelta(days=k) for k in range(-max(LEADS), RECHECK_DAYS)}
    todo = [d for d in pd.date_range(FIRST_DAY, until).date if d not in settled]
    if not todo:
        return cached
    try:
        new = daily(_get(c["lat"], c["lon"], min(todo), max(todo)))
    except (requests.RequestException, RuntimeError, KeyError, ValueError):
        return cached
    keep = cached[pd.to_datetime(cached["date"]).dt.date.isin(settled)]
    new = new[new["date"].dt.date.isin(set(todo))]
    out = pd.concat([keep, new], ignore_index=True) if len(keep) else new.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").reset_index(drop=True)
    write_csv(out.assign(date=out["date"].dt.strftime("%Y-%m-%d")), path)
    return out
