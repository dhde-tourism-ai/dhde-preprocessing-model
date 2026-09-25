"""
Hotel reservation / demand ingestion.

Source: code4fukui/echizen-coast-kanko-reservation, data/{yyyy-mm-dd}.csv.
This is Echizen Coast regional reservation data, not specific to any one
node — it's included as a general regional-demand signal for every node,
not just Fukui Station (the old dashboard pipeline isolated hotel LAG
FEATURES to Fukui Station specifically, but that's a modeling-stage
decision about feature engineering, out of scope for this preprocessing
pass — see README).

IMPORTANT — each daily file is a forward-looking booking-pipeline
snapshot, not a single day's total: data/2025-06-01.csv contains rows for
date_visit 2025-06-01 THROUGH 2025-08-30 (bookings in hand, as of that
snapshot day, for the next ~90 days). So a given date_visit appears in
many different snapshot files, each showing an earlier, less-complete
booking count as the visit date approaches.

The most complete, "final" count for any date_visit is therefore its own
day-of snapshot (data/{that same date}.csv) — the last observation taken
before that date passes out of the forward window. This module reads
only each file's first row (where date_visit == the file's own date),
across all files, rather than reconciling overlapping snapshots.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

HOTEL_COLS = ["n_stay", "n_people", "n_room", "amount_fee", "n_reserve"]


def load_hotel(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    hotel_cfg = node_cfg["sources"].get("hotel", {})
    if not hotel_cfg.get("enabled"):
        return None, unavailable_report("hotel", node_key, hotel_cfg.get("reason", "hotel disabled for this node"))

    data_dir = Path(resolve_path(hotel_cfg["repo"])) / "data"
    rows = []
    skipped = 0
    for f in sorted(data_dir.glob("*.csv")):
        try:
            file_date = pd.to_datetime(f.stem).normalize()
        except ValueError:
            continue  # not a {yyyy-mm-dd}.csv file (e.g. a .keep placeholder)
        day_df = pd.read_csv(f, nrows=1)
        day_df["date_visit"] = pd.to_datetime(day_df["date_visit"]).dt.normalize()
        if day_df.loc[0, "date_visit"] != file_date:
            # The snapshot for this date doesn't start with its own day —
            # e.g. a gap in the forward window. Skip rather than silently
            # attribute a different date's numbers to this file's date.
            skipped += 1
            continue
        rows.append(day_df.iloc[0])

    if not rows:
        return None, SourceReport(source="hotel", node_key=node_key, status="error",
                                   notes=[f"no usable day-of snapshots found under {data_dir}"])

    df = pd.DataFrame(rows).rename(columns={"date_visit": "date"})[["date", *HOTEL_COLS]]
    df = df.sort_values("date").reset_index(drop=True)

    notes = [f"source is regional (Echizen Coast), not {node_key}-specific"]
    if skipped:
        notes.append(f"{skipped} snapshot file(s) skipped — day-of row didn't match filename date")

    report = validate_daily(df, source="hotel", node_key=node_key, notes=notes)
    return df, report
