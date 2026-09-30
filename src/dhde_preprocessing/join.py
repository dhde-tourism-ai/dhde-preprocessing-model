"""
Per-node master table builder: loads every configured source, merges on
date into one wide table.

Deliberately no lag/rolling/derived features and no 0-100 scores here —
this stage stops at "clean, validated, date-keyed table." Whatever
modeling stage consumes this decides what feature engineering happens
per node (e.g. which nodes get hotel-reservation lag features is a
modeling-stage decision, not this one — see hotel.py docstring).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import get_workspace_root, load_node_config
from .validation import SourceReport, print_report
from .sources.camera import load_camera
from .sources.footfall_proxy import load_footfall_proxy
from .sources.google_reviews import load_google_reviews
from .sources.hotel import load_hotel
from .sources.info_desk import load_info_desk
from .sources.monthly_visitors import load_monthly_visitors
from .sources.rakuten import load_rakuten
from .sources.road_congestion import load_road_congestion
from .sources.rsi import load_rsi
from .sources.survey import daily_summary as survey_daily, load_survey
from .sources.traffic import load_traffic
from .sources.visitor_reservation import load_visitor_reservation
from .sources.weather import load_weather

SOURCE_LOADERS = {
    "camera": load_camera,
    "weather": load_weather,
    "rsi": load_rsi,
    "hotel": load_hotel,
    "traffic": load_traffic,
    "info_desk": load_info_desk,
    "monthly_visitors": load_monthly_visitors,
    "rakuten": load_rakuten,
    "road_congestion": load_road_congestion,  # TomTom, congestion ratio for every node
    "footfall_proxy": load_footfall_proxy,  # camera-less nodes only, see module docstring
    "visitor_reservation": load_visitor_reservation,  # attraction entry bookings, where a feed exists
    "google_reviews": load_google_reviews,  # new Google Maps reviews per day, where a place_id is set
    # survey is handled separately below — it's response-level, not date-unique
}

# Sources only some nodes declare (info_desk exists for Kanazawa only;
# monthly_visitors, rakuten, road_congestion, footfall_proxy,
# visitor_reservation and google_reviews are opt-in per node). Skipped entirely when absent from
# a node's config, so existing nodes' output and coverage reports don't
# change until their config opts in.
OPTIONAL_SOURCES = {"info_desk", "monthly_visitors", "rakuten",
                    "road_congestion", "footfall_proxy", "visitor_reservation", "google_reviews"}


def _run_loader(source_name: str, loader, node_cfg: dict):
    """Run one source loader; a missing data repo becomes that source's
    error report with a fix hint, instead of a traceback that stops the
    whole build."""
    try:
        return loader(node_cfg)
    except FileNotFoundError as e:
        missing = getattr(e, "filename", None) or str(e)
        return None, SourceReport(
            source=source_name, node_key=node_cfg["node_key"], status="error",
            notes=[f"data file not found: {missing}",
                   f"data repos are read from {get_workspace_root()}; run "
                   f"`python scripts/fetch_data.py` to clone them there, or set "
                   f"DHDE_WORKSPACE_ROOT to where they already are"])


def build_node_table(node_key: str) -> tuple[pd.DataFrame, pd.DataFrame | None, list[SourceReport]]:
    """Returns (master_daily_table, raw_survey_responses_or_None, reports)."""
    node_cfg = load_node_config(node_key)
    reports: list[SourceReport] = []
    master: pd.DataFrame | None = None

    print(f"\n=== Building node '{node_key}' ({node_cfg.get('label', node_key)}) ===")
    for source_name, loader in SOURCE_LOADERS.items():
        if source_name in OPTIONAL_SOURCES and source_name not in node_cfg["sources"]:
            continue
        df, report = _run_loader(source_name, loader, node_cfg)
        reports.append(report)
        print_report(report)
        if df is not None:
            master = df if master is None else pd.merge(master, df, on="date", how="outer")

    survey_df, survey_report = _run_loader("survey", load_survey, node_cfg)
    reports.append(survey_report)
    print_report(survey_report)
    if survey_df is not None:
        daily_counts = survey_daily(survey_df)
        master = daily_counts if master is None else pd.merge(master, daily_counts, on="date", how="outer")

    if master is None:
        raise RuntimeError(f"No sources produced any data for node '{node_key}' — every source is unavailable/errored.")

    master = master.sort_values("date").reset_index(drop=True)
    return master, survey_df, reports


def write_outputs(node_key: str, master: pd.DataFrame, survey_df: pd.DataFrame | None,
                   reports: list[SourceReport], output_dir: str = "output") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    master.to_parquet(out / f"{node_key}_master.parquet", index=False)
    master.to_csv(out / f"{node_key}_master.csv", index=False)

    if survey_df is not None:
        survey_df.to_parquet(out / f"{node_key}_survey_responses.parquet", index=False)

    coverage = {"node_key": node_key, "sources": [r.to_dict() for r in reports]}
    with open(out / f"{node_key}_coverage_report.json", "w", encoding="utf-8") as f:
        json.dump(coverage, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[OK] wrote {out / f'{node_key}_master.parquet'} "
          f"({len(master)} rows, {master.shape[1]} columns)")
    print(f"[OK] wrote {out / f'{node_key}_coverage_report.json'}")
