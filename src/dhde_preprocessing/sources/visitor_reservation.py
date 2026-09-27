"""
Visitor reservation ingestion — advance entry bookings for a single
attraction, as a near-direct visitor count where no camera exists.

Source: code4fukui/dinosaur-opendata (Fukui Prefectural Dinosaur Museum,
used for the katsuyama node). Layout: {repo}/data/{yyyy-mm-dd}.csv, one
snapshot per day taken ~03:31 JST, each holding date_visit, n_people,
amount_fee for the next ~60 days. Like the hotel feeds, a date_visit
appears in many snapshots (its booking curve); here we take the
snapshot from the visit day itself (lead 0), which is the most complete
count available — e.g. 2025-08-13 reads 6,034 at lead 7, 8,738 at lead 1
and 15,507 at lead 0.

IMPORTANT: this is RESERVED entries, not total visitors. In FY2025 the
lead-0 total (738k) was ~57% of the museum's reported 1.30M visitors —
walk-ins/same-day tickets and anyone not needing a booking aren't in
it. Treat it as a strong daily demand signal, not the full headcount.
A zero on the visit day is kept as zero (closure days); where the lead-0
snapshot is missing, the latest earlier snapshot is used and flagged in
`from_earlier_snapshot` so nothing is silently patched.
"""
from __future__ import annotations

import glob
import os

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily


def load_visitor_reservation(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    cfg = node_cfg["sources"].get("visitor_reservation", {})
    if not cfg.get("enabled"):
        return None, unavailable_report(
            "visitor_reservation", node_key, cfg.get("reason", "no attraction reservation feed for this node"))

    repo = cfg["repo"]
    frames, empty_files = [], 0
    for path in sorted(glob.glob(f"{resolve_path(repo)}/data/*.csv")):
        snap = os.path.basename(path)[:10]
        try:
            snap_date = pd.Timestamp(snap)
        except ValueError:
            continue  # not a {yyyy-mm-dd}.csv file
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except pd.errors.EmptyDataError:
            empty_files += 1
            continue
        df["snapshot"] = snap_date
        frames.append(df)

    if not frames:
        return None, SourceReport(source="visitor_reservation", node_key=node_key, status="error",
                                   notes=[f"no readable snapshot files under {repo}/data/"])

    snaps = pd.concat(frames, ignore_index=True)
    snaps["date"] = pd.to_datetime(snaps["date_visit"]).dt.normalize()
    snaps["lead"] = (snaps["date"] - snaps["snapshot"]).dt.days
    snaps = snaps[snaps["lead"] >= 0]  # a snapshot's own past dates read 0, not real

    # Per visit date, the snapshot closest to (and not after) the visit day.
    best = snaps.sort_values("lead").drop_duplicates("date", keep="first")
    out = best[["date", "n_people", "amount_fee"]].rename(
        columns={"n_people": "reserved_visitors", "amount_fee": "reserved_fee"})
    out["from_earlier_snapshot"] = best["lead"].to_numpy() > 0
    out = out.sort_values("date").reset_index(drop=True)

    latest = snaps["snapshot"].max()
    future = out["date"] > latest
    notes = [
        f"source: {repo} — reserved entries, not total visitors (~57% of reported FY2025 visitors)",
        f"{int((out['from_earlier_snapshot'] & ~future).sum())} past day(s) taken from an earlier snapshot (lead-0 missing)",
        f"{int(future.sum())} future day(s) after {latest.date()} are bookings so far, not final",
    ]
    if empty_files:
        notes.append(f"{empty_files} empty snapshot file(s) skipped")
    return out, validate_daily(out, source="visitor_reservation", node_key=node_key, notes=notes)
