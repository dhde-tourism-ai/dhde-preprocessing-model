"""
Road congestion ingestion — TomTom Orbis traffic-flow vector tiles.

What the dashboard needs from traffic is congestion (how slow roads are
vs. normal), not vehicle counts. JARTIC gives counts at only four nodes;
TomTom covers every node, including Tojinbo and Awara Onsen.

Why Orbis tiles and not TomTom's Flow Segment Data API: that older API
returns "Point too far from nearest existing segment" for every point in
Japan (checked 2026-09-27, including central Tokyo and Osaka), while the
Orbis flow tiles do carry Japanese road segments. Each segment has
`relative_speed` = current speed / free-flow speed (1.0 = free-flowing,
lower = more congested). Tiles report no per-segment confidence, so how
much is live probe data vs. historical on quiet rural roads is unknown —
cross-check against JARTIC at nodes that have both before relying on it.

Each run takes one live snapshot per node: every segment within
`radius_km` of the node (zoom-14 tiles, ~2km wide) is averaged. Like
JARTIC, TomTom keeps no history for us, so snapshots are appended to
`{live_data_root}/tomtom_cache/{node_key}_road_congestion.csv` and the
daily table is aggregated from that cache — history only builds up if
this runs on a schedule. NOTE: TomTom's terms on caching/storing Content
were not verified when this was written; check them before relying on
the stored history in production.

The API key is read from the TOMTOM_API_KEY environment variable and is
never written to reports or error messages (request errors are reduced
to their type/status because requests embeds the full URL, key included).
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

import mapbox_vector_tile
import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_live_path, write_csv
from ..validation import SourceReport, unavailable_report, validate_daily

TILE_URL = "https://api.tomtom.com/maps/orbis/traffic/tile/flow/{z}/{x}/{y}.pbf"
KEY_ENV = "TOMTOM_API_KEY"
DEFAULT_ZOOM = 14
DEFAULT_RADIUS_KM = 2.0
CACHE_DIR = "tomtom_cache"
SNAPSHOT_COLS = ["timestamp", "n_segments", "relative_speed_mean", "relative_speed_min"]


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.radians
    a = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def _tile_frac(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile_to_latlon(px: float, py: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lon = px / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return lat, lon


def _tiles_around(lat: float, lon: float, z: int, radius_km: float) -> list[tuple[int, int]]:
    tile_km = 40075 * math.cos(math.radians(lat)) / 2 ** z
    span = math.ceil(radius_km / tile_km)
    cx, cy = (int(v) for v in _tile_frac(lat, lon, z))
    return [(cx + dx, cy + dy) for dx in range(-span, span + 1) for dy in range(-span, span + 1)]


def _midpoint(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "MultiLineString":
        coords = max(coords, key=len, default=[])
    if not coords:
        return None
    return coords[len(coords) // 2]


def _segments(tile_bytes: bytes, z: int, x: int, y: int) -> list[tuple[float, float, float]]:
    """(lat, lon, relative_speed) for each flow segment in one tile."""
    out = []
    for layer in mapbox_vector_tile.decode(tile_bytes).values():
        extent = layer.get("extent", 4096)
        for f in layer["features"]:
            rel = f["properties"].get("relative_speed")
            mid = _midpoint(f["geometry"])
            if rel is None or mid is None:
                continue
            # decode() defaults to y-up tile coordinates (origin bottom-left).
            lat, lon = _tile_to_latlon(x + mid[0] / extent, y + 1 - mid[1] / extent, z)
            out.append((lat, lon, float(rel)))
    return out


def _snapshot(lat: float, lon: float, key: str, zoom: int, radius_km: float) -> dict:
    rels = []
    for x, y in _tiles_around(lat, lon, zoom, radius_km):
        resp = requests.get(TILE_URL.format(z=zoom, x=x, y=y), params={"key": key, "apiVersion": 1}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"TomTom tile request returned HTTP {resp.status_code}")
        rels += [r for (la, lo, r) in _segments(resp.content, zoom, x, y) if _distance_km(lat, lon, la, lo) <= radius_km]
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return {
        "timestamp": now.isoformat(),
        "n_segments": len(rels),
        "relative_speed_mean": round(sum(rels) / len(rels), 4) if rels else None,
        "relative_speed_min": round(min(rels), 4) if rels else None,
    }


def load_road_congestion(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    cfg = node_cfg["sources"].get("road_congestion", {})
    if not cfg.get("enabled"):
        return None, unavailable_report("road_congestion", node_key, cfg.get("reason", "road congestion disabled for this node"))

    radius_km = cfg.get("radius_km", DEFAULT_RADIUS_KM)
    zoom = cfg.get("zoom", DEFAULT_ZOOM)
    # A string, not Path(), so an s3:// live-data root works.
    cache_path = resolve_live_path(f"{CACHE_DIR}/{node_key}_road_congestion.csv")
    try:
        cache = read_csv_if_exists(cache_path)
    except pd.errors.EmptyDataError:  # 0-byte file
        cache = None
    if cache is None:
        cache = pd.DataFrame(columns=SNAPSHOT_COLS)
    notes = [f"TomTom Orbis flow tiles, segments within {radius_km}km, zoom {zoom}; "
             f"no per-segment confidence (rural reliability unverified)"]

    key = os.environ.get(KEY_ENV, "")
    if not key:
        if cache.empty:
            # Setup gap, not a failure: same status as any other missing source.
            return None, unavailable_report(
                "road_congestion", node_key, f"{KEY_ENV} not set and no saved snapshots yet")
        notes.append(f"{KEY_ENV} not set: no new snapshot this run, using saved snapshots only")
    else:
        c = node_cfg["coordinates"]
        try:
            snap = _snapshot(c["lat"], c["lon"], key, zoom, radius_km)
        except (requests.RequestException, RuntimeError) as e:
            msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
            notes.append(f"snapshot failed ({msg}): kept existing history only")
        else:
            if snap["n_segments"] == 0:
                notes.append(f"no road segments within {radius_km}km in this snapshot")
            new = pd.DataFrame([snap], columns=SNAPSHOT_COLS)
            cache = new if cache.empty else pd.concat([cache, new], ignore_index=True)
            write_csv(cache, cache_path)

    usable = cache.dropna(subset=["relative_speed_mean"])
    if usable.empty:
        return None, SourceReport(source="road_congestion", node_key=node_key, status="error",
                                   notes=notes + ["no snapshots in cache yet"])

    usable = usable.assign(date=pd.to_datetime(usable["timestamp"], utc=True)
                           .dt.tz_convert("Asia/Tokyo").dt.tz_localize(None).dt.normalize())
    daily = usable.groupby("date").agg(
        road_relative_speed_mean=("relative_speed_mean", "mean"),
        road_relative_speed_min=("relative_speed_min", "min"),
        road_snapshots=("relative_speed_mean", "size"),
    ).reset_index()
    daily["road_congestion"] = (1 - daily["road_relative_speed_mean"]).round(4)
    notes.append(f"{len(usable)} snapshot(s) in cache; history only grows if this runs on a schedule")
    return daily, validate_daily(daily, source="road_congestion", node_key=node_key, notes=notes)
