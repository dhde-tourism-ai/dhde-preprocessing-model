"""
Footfall proxy for nodes with no camera of their own.

Per tech lead review of PR #6: where a node has no people-flow sensor,
use activity nearby instead, widening the search circle until something
is found. This module does NOT invent a footfall number or blend signals
into a score (that's a modeling-stage decision). It only pulls
already-real signals from a wider area and labels them as proxies:

- proxy_camera_count: daily person count from the NEAREST other node's
  camera, searched in widening circles (config `radii_km`, default 5 →
  15km; 30km was tried and reached Fukui Station, a city hub too unlike
  Katsuyama/Ono to stand in). Only Person.csv sensors qualify — Rainbow Line's
  LicensePlate.csv gates count vehicles, so they're never used as a
  people proxy.
- proxy_survey_count: daily FTAS survey responses pooled across EVERY
  area.csv area inside the smallest circle that contains any area (not
  just the node's own area, which survey.py already covers exactly).

Hotel occupancy is the third proxy signal Sohail named, but it's already
in the master table as `occ` from sources/hotel.py — not duplicated here.

Every proxy column carries its source and radius in the report notes, so
a reader can see e.g. "Tojinbo camera, 3.3km away, 5km circle" rather
than mistaking the proxy for a local count.
"""
from __future__ import annotations

import math

import pandas as pd

from ..config import list_configured_nodes, load_node_config, resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily
from .camera import _load_count_csv

DEFAULT_RADII_KM = [5, 15]
CHUNK_SIZE = 50_000


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.radians
    a = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def _person_sensor(cfg: dict) -> str | None:
    """First Person.csv path of a node's camera, or None (no camera, or
    vehicle-only gates like Rainbow Line)."""
    cam = cfg["sources"].get("camera", {})
    if not cam.get("enabled"):
        return None
    for gate in cam.get("gates", []):
        if gate.get("person_csv"):
            return gate["person_csv"]
    return None


def _nearest_camera(node_cfg: dict, all_cfgs: list[dict], radii_km: list[float]):
    here = node_cfg["coordinates"]
    candidates = []
    for cfg in all_cfgs:
        if cfg["node_key"] == node_cfg["node_key"]:
            continue
        path = _person_sensor(cfg)
        if path:
            c = cfg["coordinates"]
            candidates.append((_distance_km(here["lat"], here["lon"], c["lat"], c["lon"]), cfg["node_key"], path))
    for radius in radii_km:
        within = sorted(c for c in candidates if c[0] <= radius)
        if within:
            dist, key, path = within[0]
            return dist, key, path, radius
    return None


def _camera_proxy(node_cfg, all_cfgs, radii_km, notes):
    found = _nearest_camera(node_cfg, all_cfgs, radii_km)
    if found is None:
        notes.append(f"no Person.csv camera within {max(radii_km)}km — proxy_camera_count unavailable")
        return None
    dist, key, path, radius = found
    notes.append(f"proxy_camera_count: {key} camera, {dist:.1f}km away ({radius}km circle)")
    return _load_count_csv(resolve_path(path), "proxy_camera_count")


def _survey_proxy(node_cfg, radii_km, notes):
    survey_cfg = node_cfg["sources"].get("survey", {})
    repo_root = resolve_path(survey_cfg.get("repo", "fukui-kanko-survey"))
    areas = pd.read_csv(f"{repo_root}/area.csv").dropna(subset=["緯度", "経度"])
    here = node_cfg["coordinates"]
    areas["dist"] = [_distance_km(here["lat"], here["lon"], la, lo) for la, lo in zip(areas["緯度"], areas["経度"])]

    for radius in radii_km:
        inside = areas[areas["dist"] <= radius]
        if not inside.empty:
            break
    else:
        notes.append(f"no survey area within {max(radii_km)}km — proxy_survey_count unavailable")
        return None

    names = set(inside["エリア名"].str.strip())
    counts = []
    for chunk in pd.read_csv(f"{repo_root}/all.csv", usecols=["回答日時", "回答エリア"],
                             chunksize=CHUNK_SIZE, low_memory=False):
        matched = chunk[chunk["回答エリア"].astype(str).str.strip().isin(names)]
        if not matched.empty:
            counts.append(pd.to_datetime(matched["回答日時"], errors="coerce").dt.normalize().dropna())
    if not counts:
        notes.append(f"survey areas within {radius}km had zero responses — proxy_survey_count unavailable")
        return None

    notes.append(f"proxy_survey_count: {len(names)} survey area(s) pooled within {radius}km circle")
    dates = pd.concat(counts)
    return dates.value_counts().sort_index().rename_axis("date").rename("proxy_survey_count").reset_index()


def load_footfall_proxy(node_cfg: dict, all_node_cfgs: list[dict] | None = None) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    proxy_cfg = node_cfg["sources"].get("footfall_proxy", {})
    if not proxy_cfg.get("enabled"):
        return None, unavailable_report(
            "footfall_proxy", node_key,
            proxy_cfg.get("reason", "not configured (node has its own camera, or no proxy set up)"))

    if all_node_cfgs is None:
        all_node_cfgs = [load_node_config(k) for k in list_configured_nodes()]
    radii_km = proxy_cfg.get("radii_km", DEFAULT_RADII_KM)
    notes: list[str] = []

    parts = [df for df in (_camera_proxy(node_cfg, all_node_cfgs, radii_km, notes),
                           _survey_proxy(node_cfg, radii_km, notes)) if df is not None]
    notes.append("hotel occupancy proxy = the existing `occ` column from the hotel source")
    if not parts:
        return None, SourceReport(source="footfall_proxy", node_key=node_key, status="error", notes=notes)

    merged = parts[0]
    for df in parts[1:]:
        merged = pd.merge(merged, df, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged, validate_daily(merged, source="footfall_proxy", node_key=node_key, notes=notes)
