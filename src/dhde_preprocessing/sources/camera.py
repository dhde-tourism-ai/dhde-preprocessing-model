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
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

FACE_DROP_COLS = {"placement", "object class", "aggregate from", "aggregate to", "total count"}


def _load_count_csv(path: str, count_col_name: str) -> pd.DataFrame:
    """Shared reader for Person.csv and LicensePlate.csv — same row shape,
    different meaning of "total count" (people vs. vehicles), so the
    caller names the output column accordingly."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["aggregate from"]).dt.normalize()
    return df[["date", "total count"]].rename(columns={"total count": count_col_name})


def _load_face_csv(path: str, prefix: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    keep_cols = [c for c in df.columns if c not in FACE_DROP_COLS]
    df["date"] = pd.to_datetime(df["aggregate from"]).dt.normalize()
    renamed = {c: f"{prefix}face_{c.replace(' ', '_')}" for c in keep_cols}
    return df[["date", *keep_cols]].rename(columns=renamed)


def load_camera(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    cam_cfg = node_cfg["sources"].get("camera", {})
    if not cam_cfg.get("enabled"):
        return None, unavailable_report("camera", node_key, cam_cfg.get("reason", "camera disabled for this node"))

    gates = cam_cfg["gates"]
    multi_gate = len(gates) > 1
    merged: pd.DataFrame | None = None

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

        merged = gate_df if merged is None else pd.merge(merged, gate_df, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    report = validate_daily(merged, source="camera", node_key=node_key)
    return merged, report
