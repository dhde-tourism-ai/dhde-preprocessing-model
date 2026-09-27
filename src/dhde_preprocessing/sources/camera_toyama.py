"""
Camera people-flow ingestion for Toyama nodes — Toyama City AI cameras.

Selected by `camera.provider: toyama_city` in a node config; camera.py
dispatches here.

Source: 富山市AIカメラシステム (https://toyama-ai.com/toyama_aicamera/),
27 observation points around Toyama Station and the city-centre
shopping streets, updated daily at 01:00 for the previous day. Free to
use with credit to Toyama City. Its CSV export
(`db/?search=1&mode=day&camera_id=NN&date_from=..&date_to=..`) returns
one camera per request: two title rows, then one row per day with
hourly pedestrian counts (5:00–23:00) and Total(Male), Total(Female),
Total. Daily data starts 2023-02-28.

Column naming follows camera.py: a single gate gets `count`, several
gates get `{gate}_count` each — never summed, since one person walking
past two cameras would be counted twice. Male/female totals are kept as
`{gate}_male_count` / `{gate}_female_count`.

A daily Total of 0 at a station camera means the camera was down, not
that nobody walked past — those days become missing, not 0.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..validation import SourceReport, blank_zero_days, validate_daily
from .remote_csv import fetch_csv

EXPORT_URL = "https://toyama-ai.com/toyama_aicamera/db/"
COLUMNS = {"Total": "count", "Total(Male)": "male_count", "Total(Female)": "female_count"}


def clean_camera(raw: pd.DataFrame, prefix: str) -> tuple[pd.DataFrame, int]:
    """One camera's export -> (date + prefixed count columns, zero days blanked)."""
    df = pd.DataFrame({"date": pd.to_datetime(raw["Day"], errors="coerce").dt.normalize()})
    for src, dst in COLUMNS.items():
        df[f"{prefix}{dst}"] = pd.to_numeric(raw[src], errors="coerce")
    df = df.dropna(subset=["date"])
    return blank_zero_days(df, f"{prefix}count", [f"{prefix}{c}" for c in COLUMNS.values()])


def load_toyama_camera(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    cam_cfg = node_cfg["sources"]["camera"]
    gates = cam_cfg["gates"]
    multi_gate = len(gates) > 1
    merged: pd.DataFrame | None = None
    notes = []

    for gate in gates:
        prefix = f"{gate['name']}_" if multi_gate else ""
        params = {"search": 1, "mode": "day", "camera_id": gate["camera_id"],
                  "date_from": cam_cfg.get("start_date", "2023-02-28"), "date_to": date.today().isoformat()}
        try:
            raw, note = fetch_csv(EXPORT_URL, f"toyama_camera_{gate['camera_id']}", params=params,
                                  encoding="utf-8-sig", skiprows=2)
        except Exception as e:  # noqa: BLE001 - one camera failing shouldn't drop the others
            notes.append(f"camera {gate['camera_id']} ({gate['name']}): fetch failed and no cache: {e!r}")
            continue
        gate_df, n_zero = clean_camera(raw, prefix)
        notes.append(f"camera {gate['camera_id']} ({gate['name']}): {note}")
        if n_zero:
            notes.append(f"camera {gate['camera_id']}: {n_zero} day(s) with a total of 0 treated as missing (camera down)")
        merged = gate_df if merged is None else pd.merge(merged, gate_df, on="date", how="outer")

    if merged is None:
        return None, SourceReport(source="camera", node_key=node_key, status="error", notes=notes)

    merged = merged.sort_values("date").reset_index(drop=True)
    report = validate_daily(merged, source="camera", node_key=node_key, notes=notes)
    return merged, report
