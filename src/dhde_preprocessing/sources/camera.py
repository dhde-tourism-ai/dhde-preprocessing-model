"""
Ground-truth camera people-flow ingestion.

Source: code4fukui/fukui-kanko-people-flow-data, full/{sensor}/*.csv
- Person.csv: one row per day, columns placement/object class/aggregate
  from/aggregate to/total count. This is the visitor count — use it as
  the ground truth, where it exists.
- LicensePlate.csv: same row shape, but "total count" is VEHICLES, not
  people (Rainbow Line's two gates are parking-lot entrances with no
  Person.csv at all — plate-recognition is the only sensor there). Never
  conflate this with a person count: gates configured with
  license_plate_csv get a `{gate}_vehicle_count` column, not
  `{gate}_count`, so it's never silently treated as comparable to the
  person-count nodes.
- Face.csv: same daily grain, but "total count" here is only faces
  detected clearly enough to classify (a small, biased sample of the
  Person/vehicle total), plus age/gender bucket columns. Never treat
  Face's count as a visitor count — it's a demographic breakdown, kept
  as auxiliary columns.

There is no directional (in/out) data in this source. Multi-gate nodes
(Rainbow Line) are NOT merged into one count here; each gate gets its own
column, decided node-by-node in config (see config/nodes/rainbow_line.yaml).

The exports repeat some days (e.g. 2025-09-24 → 09-30 appears twice in
every sensor's files, checked 2026-09-27), once with slightly revised
counts. Only the last row per day is kept; without this, the outer join
in join.py multiplies those days (4 rows per day at Rainbow Line).

A 0 from a Person.csv sensor means the sensor was down, not that nobody
came (every Fukui sensor read 0 on 2025-09-26 → 09-28, and Tojinbo reads
~7,000 on a normal day), so those days become missing. Vehicle gates keep
their zeros: Rainbow Line's gate 2 really sees no cars on many days.
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, blank_zero_days, unavailable_report, validate_daily
from .camera_toyama import load_toyama_camera

FACE_DROP_COLS = {"placement", "object class", "aggregate from", "aggregate to", "total count"}


def _load_count_csv(path: str, count_col_name: str) -> pd.DataFrame:
    """Shared reader for Person.csv and LicensePlate.csv — same row shape,
    different meaning of "total count" (people vs. vehicles), so the
    caller names the output column accordingly."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["aggregate from"]).dt.normalize()
    df = df.drop_duplicates("date", keep="last")
    return df[["date", "total count"]].rename(columns={"total count": count_col_name})


def _load_face_csv(path: str, prefix: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    keep_cols = [c for c in df.columns if c not in FACE_DROP_COLS]
    df["date"] = pd.to_datetime(df["aggregate from"]).dt.normalize()
    df = df.drop_duplicates("date", keep="last")
    renamed = {c: f"{prefix}face_{c.replace(' ', '_')}" for c in keep_cols}
    return df[["date", *keep_cols]].rename(columns=renamed)


def load_camera(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    cam_cfg = node_cfg["sources"].get("camera", {})
    if not cam_cfg.get("enabled"):
        return None, unavailable_report("camera", node_key, cam_cfg.get("reason", "camera disabled for this node"))

    # Toyama nodes read Toyama City's AI camera export — see camera_toyama.py.
    if cam_cfg.get("provider") == "toyama_city":
        return load_toyama_camera(node_cfg)

    gates = cam_cfg["gates"]
    multi_gate = len(gates) > 1
    merged: pd.DataFrame | None = None
    notes: list[str] = []

    for gate in gates:
        prefix = f"{gate['name']}_" if multi_gate else ""

        if gate.get("person_csv"):
            path = resolve_path(gate["person_csv"])
            gate_df = _load_count_csv(path, f"{prefix}count")
        elif gate.get("license_plate_csv"):
            path = resolve_path(gate["license_plate_csv"])
            gate_df = _load_count_csv(path, f"{prefix}vehicle_count")
        else:
            raise ValueError(f"gate {gate['name']!r} config has neither person_csv nor license_plate_csv")

        if gate.get("face_csv"):
            face_path = resolve_path(gate["face_csv"])
            face_df = _load_face_csv(face_path, prefix)
            gate_df = pd.merge(gate_df, face_df, on="date", how="left")

        if gate.get("person_csv"):
            gate_df, n_blanked = blank_zero_days(gate_df, f"{prefix}count", [c for c in gate_df.columns if c != "date"])
            if n_blanked:
                notes.append(f"{gate['name']}: {n_blanked} day(s) reading 0 set to missing (sensor down)")

        merged = gate_df if merged is None else pd.merge(merged, gate_df, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    report = validate_daily(merged, source="camera", node_key=node_key, notes=notes)
    return merged, report
