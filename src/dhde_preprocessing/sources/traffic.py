"""
Road traffic volume ingestion — JARTIC open traffic data (via MLIT's
xROAD portal): CCTV AI-counter layer (t_travospublic_measure_1h_img)
by default, or the permanent-counter layer (t_travospublic_measure_1h).

This replaces the old FY2005 static road census as the traffic signal,
where a nearby monitoring point actually exists — coverage is
national-roads-only and sparse (~2,600 points nationwide), confirmed
empirically per node rather than assumed (see config notes below):

  - CCTV layer (default): Fukui Station 6810150 (~2.7km), Rainbow Line
    6810590 (~5km)
  - Permanent layer (`layer: t_travospublic_measure_1h` in config):
    Eiheiji 6110870 (~2.5km), Katsuyama 6110860 (~5.3km)
  - Tojinbo, Awara Onsen: nearest point on either layer is 8km+ away -> unavailable (config: enabled=false)

"Flagged" means: this module does NOT verify the point sits on the road
visitors actually use to reach the site, only that a point exists in the
general area. That's a real gap — see the distance_km and point_code
carried into every row's report/notes so a human can check it.

Unlike the other sources, this is a LIVE API, not a static export: the
provider only retains 5-minute data ~1 month and hourly data ~3 months,
so there's no way to backfill full history back to Dec 2024 the way
camera/weather data can. This module pulls a trailing window (default 90
days) of hourly data each run — in production this should run on a
schedule and accumulate its own history over time, since JARTIC itself
won't hold it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_live_path
from ..validation import SourceReport, unavailable_report, validate_daily

API_BASE = "https://api.jartic-open-traffic.org/geoserver"
LAYER = "t_travospublic_measure_1h_img"  # CCTV AI counter, hourly — default layer
# Permanent (loop-detector) counters, hourly. Same property names as the CCTV
# layer, different point set — closer than any CCTV point for some nodes
# (Eiheiji, Katsuyama). Selected per node via `layer:` in its config.
PERMANENT_LAYER = "t_travospublic_measure_1h"
DEFAULT_WINDOW_DAYS = 90
HISTORY_DIR = "jartic_history"  # written by scripts/collect_live.py


def merge_history(existing: pd.DataFrame | None, new: pd.DataFrame | None) -> pd.DataFrame:
    """Merge daily rows by date; the newer pull wins for dates in both
    (the most recent day is partial until the next pull completes it)."""
    frames = [d for d in (existing, new) if d is not None and not d.empty]
    if not frames:  # e.g. a saved history file with only a header row
        return pd.DataFrame(columns=["date"])
    both = pd.concat(frames, ignore_index=True)
    both["date"] = pd.to_datetime(both["date"])
    return both.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _query_point_range(point_code: int, start: datetime, end: datetime, layer: str = LAYER) -> list[dict]:
    time_code_geq = start.strftime("%Y%m%d%H00")
    time_code_leq = end.strftime("%Y%m%d%H00")
    # No quotes around the field name here — the spec document's own
    # examples show it quoted, but the live API 400s on that (it seems
    # to choke trying to parse the request as JSON); confirmed working
    # unquoted against the real endpoint.
    cql = (
        f"常時観測点コード={point_code} AND "
        f"時間コード>={time_code_geq} AND 時間コード<={time_code_leq}"
    )
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": layer, "srsName": "EPSG:4326",
        "outputFormat": "application/json", "exceptions": "application/json",
        "cql_filter": cql,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("features", [])


def load_traffic(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    t_cfg = node_cfg["sources"].get("traffic", {})
    if not t_cfg.get("enabled"):
        return None, unavailable_report("traffic", node_key, t_cfg.get("reason", "traffic disabled for this node"))

    point_code = t_cfg["point_code"]
    distance_km = t_cfg.get("distance_km")
    window_days = t_cfg.get("window_days", DEFAULT_WINDOW_DAYS)
    layer = t_cfg.get("layer", LAYER)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)

    notes = [
        f"point_code={point_code} (layer {layer}), ~{distance_km}km from node coordinates — "
        f"NOT confirmed to be on the site's actual access road, see module docstring",
    ]
    history_name = f"{node_key}_traffic_daily.csv"
    # A string, not Path(), so an s3:// live-data root works.
    history_path = resolve_live_path(f"{HISTORY_DIR}/{history_name}")
    try:
        history = read_csv_if_exists(history_path)
    except pd.errors.EmptyDataError:  # 0-byte file
        history = None
    if history is not None and history.empty:
        history = None

    try:
        features = _query_point_range(point_code, start, end, layer)
    except requests.RequestException as e:
        features, live_error = [], f"JARTIC API request failed: {e!r}"
    else:
        live_error = None if features else (
            f"point {point_code} returned zero rows for the last {window_days} days — "
            f"point may be offline, or the trailing window has already aged out of JARTIC's retention")

    if live_error:
        if history is None:
            return None, SourceReport(source="traffic", node_key=node_key, status="error", notes=notes + [live_error])
        daily = merge_history(history, None)
        notes += [live_error, f"using saved history only ({len(daily)} days from {history_name})"]
        return daily, validate_daily(daily, source="traffic", node_key=node_key, notes=notes)

    rows = []
    for f in features:
        p = f["properties"]
        rows.append({
            "datetime": pd.to_datetime(str(p["時間コード"]), format="%Y%m%d%H%M"),
            "volume_upstream": (p.get("上り・小型交通量") or 0) + (p.get("上り・大型交通量") or 0),
            "volume_downstream": (p.get("下り・小型交通量") or 0) + (p.get("下り・大型交通量") or 0),
        })
    hourly = pd.DataFrame(rows)
    hourly["volume_total"] = hourly["volume_upstream"] + hourly["volume_downstream"]
    daily = hourly.groupby(hourly["datetime"].dt.normalize()).agg(
        volume_total=("volume_total", "sum"),
        volume_upstream=("volume_upstream", "sum"),
        volume_downstream=("volume_downstream", "sum"),
        hours_observed=("volume_total", "size"),
    ).reset_index().rename(columns={"datetime": "date"})

    if history is not None:
        daily = merge_history(history, daily)
        notes.append(f"live {window_days}-day pull merged with saved history ({history_name})")
    else:
        notes.append(f"live API pull, {window_days}-day trailing window only — no saved history found "
                     f"under {HISTORY_DIR}/ (run scripts/collect_live.py on a schedule)")
    report = validate_daily(daily, source="traffic", node_key=node_key, notes=notes)
    return daily, report
