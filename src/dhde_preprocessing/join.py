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

from .config import load_node_config
from .validation import SourceReport, print_report
from .sources.camera import load_camera
from .sources.hotel import load_hotel
from .sources.rsi import load_rsi
from .sources.survey import load_survey
from .sources.traffic import load_traffic
from .sources.weather import load_weather

SOURCE_LOADERS = {
    "camera": load_camera,
    "weather": load_weather,
    "rsi": load_rsi,
    "hotel": load_hotel,
    "traffic": load_traffic,
    # survey is handled separately below — it's response-level, not date-unique
}


def build_node_table(node_key: str) -> tuple[pd.DataFrame, pd.DataFrame | None, list[SourceReport]]:
    """Returns (master_daily_table, raw_survey_responses_or_None, reports)."""
    node_cfg = load_node_config(node_key)
    reports: list[SourceReport] = []
    master: pd.DataFrame | None = None

    print(f"\n=== Building node '{node_key}' ({node_cfg.get('label', node_key)}) ===")
    for source_name, loader in SOURCE_LOADERS.items():
        df, report = loader(node_cfg)
        reports.append(report)
        print_report(report)
        if df is not None:
            master = df if master is None else pd.merge(master, df, on="date", how="outer")

    survey_df, survey_report = load_survey(node_cfg)
    reports.append(survey_report)
    print_report(survey_report)
    if survey_df is not None:
        daily_counts = (
            survey_df.groupby("date").size().rename("survey_response_count").reset_index()
        )
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
