"""
Road traffic volume ingestion — JARTIC open traffic data (via MLIT's
xROAD portal), CCTV AI-counter layer (t_travospublic_measure_1h_img).

This replaces the old FY2005 static road census as the traffic signal,
where a nearby monitoring point actually exists — coverage is
national-roads-only and sparse (~2,600 points nationwide), confirmed
empirically per node rather than assumed (see config notes below):

  - Tojinbo:      nearest point ~14km away  -> unavailable (config: enabled=false)
  - Katsuyama:    nearest point ~22.5km away -> unavailable (config: enabled=false)
  - Fukui Station: point 6810150, ~2.7km away -> included, flagged
  - Rainbow Line:  point 6810590, ~5km away   -> included, flagged

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

from ..validation import SourceReport, unavailable_report, validate_daily

API_BASE = "https://api.jartic-open-traffic.org/geoserver"
LAYER = "t_travospublic_measure_1h_img"  # CCTV AI counter, hourly — the layer with coverage at these 2 nodes
DEFAULT_WINDOW_DAYS = 90


def _query_point_range(point_code: int, start: datetime, end: datetime) -> list[dict]:
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
        "typeNames": LAYER, "srsName": "EPSG:4326",
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

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)

    try:
        features = _query_point_range(point_code, start, end)
    except requests.RequestException as e:
        return None, SourceReport(source="traffic", node_key=node_key, status="error",
                                   notes=[f"JARTIC API request failed: {e!r}"])

    if not features:
        return None, SourceReport(
            source="traffic", node_key=node_key, status="error",
            notes=[f"point {point_code} returned zero rows for the last {window_days} days — "
                   f"point may be offline, or the trailing window has already aged out of JARTIC's retention"],
        )

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

    notes = [
        f"point_code={point_code}, ~{distance_km}km from node coordinates — "
        f"NOT confirmed to be on the site's actual access road, see module docstring",
        f"live API pull, {window_days}-day trailing window only — no historical backfill available",
    ]
    report = validate_daily(daily, source="traffic", node_key=node_key, notes=notes)
    return daily, report
