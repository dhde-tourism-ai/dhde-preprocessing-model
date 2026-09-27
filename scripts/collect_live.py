#!/usr/bin/env python3
"""
Collect the live-only sources so their history isn't lost.

JARTIC keeps hourly traffic for only ~3 months and TomTom gives only the
current state, so neither can be backfilled later. This script pulls both
for every configured node and saves them under --out (default: history/):

    {out}/tomtom_cache/{node}_road_congestion.csv   one row per snapshot
    {out}/jartic_history/{node}_traffic_daily.csv   one row per day, merged

It is meant to run on a schedule (see .github/workflows/collect-live-data.yml),
which commits --out to the `live-data` branch. The full build can then point
DHDE_WORKSPACE_ROOT's tomtom_cache at that history.

Usage:
    python scripts/collect_live.py --out history
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sources import road_congestion
from dhde_preprocessing.sources.traffic import load_traffic
from dhde_preprocessing.validation import print_report


def upsert_daily(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge daily rows by date; the newer pull wins for dates in both
    (the most recent day is partial until the next run completes it)."""
    both = pd.concat([existing, new], ignore_index=True)
    both["date"] = pd.to_datetime(both["date"])
    return both.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="history")
    args = parser.parse_args()
    out = Path(args.out).resolve()

    # road_congestion keeps its snapshot cache under the workspace root;
    # nothing else collected here reads from it.
    os.environ["DHDE_WORKSPACE_ROOT"] = str(out)
    jartic_dir = out / "jartic_history"
    jartic_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for node_key in list_configured_nodes():
        cfg = load_node_config(node_key)
        print(f"\n=== {node_key} ===")

        df, report = road_congestion.load_road_congestion(cfg)
        print_report(report)
        failures += report.status == "error"

        df, report = load_traffic(cfg)
        print_report(report)
        if df is not None:
            path = jartic_dir / f"{node_key}_traffic_daily.csv"
            existing = pd.read_csv(path) if path.exists() else df.iloc[0:0]
            upsert_daily(existing, df).to_csv(path, index=False)
        elif report.status == "error":
            failures += 1

    if not os.environ.get(road_congestion.KEY_ENV):
        print(f"\n[WARN] {road_congestion.KEY_ENV} not set: no TomTom snapshots were taken")
    print(f"\ndone, {failures} source error(s)")
    return 0  # errors are reported, not fatal: one bad node shouldn't stop the others


if __name__ == "__main__":
    raise SystemExit(main())
