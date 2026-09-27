#!/usr/bin/env python3
"""
Build the integrated dataset from the node master tables.

Run scripts/build_node.py for the same nodes first; this reads its output.
Writes output/integrated_fukui.parquet (everything, future bookings
included) and output/integrated_fukui_train.parquet (up to yesterday, JST).

Usage:
    python scripts/build_integrated.py                  # the six Fukui nodes, from 2024-12-01
    python scripts/build_integrated.py --start 2025-06-01 --end 2026-08-31
    python scripts/build_integrated.py --nodes tojinbo eiheiji
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Japanese labels

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.integrate import DEFAULT_START, FUKUI_NODES, build_integrated, write_integrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", nargs="+", default=FUKUI_NODES)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", help="last day of the training table (default: yesterday, JST)")
    parser.add_argument("--input-dir", default="output")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    table, train, summary = build_integrated(args.nodes, input_dir=args.input_dir, start=args.start, end=args.end)
    for node_key, info in summary["nodes"].items():
        for note in info["notes"]:
            print(f"  {node_key}: {note}")
    if summary["camera_outage_days"]:
        print(f"  camera system down (all people cameras) on: {', '.join(summary['camera_outage_days'])}")
    for warning in summary["warnings"]:
        print(f"  WARNING: {warning}")
    write_integrated(table, train, summary, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
