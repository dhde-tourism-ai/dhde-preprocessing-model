#!/usr/bin/env python3
"""
Backtest the forecast models and forecast the next 7 days per node.

Run scripts/build_integrated.py first; this reads its training table.
Writes output/forecast_fukui.parquet (plus .csv), forecast_fukui_backtest.csv
(scores per node and model) and forecast_fukui_report.json.

Usage:
    python scripts/build_forecast.py
    python scripts/build_forecast.py --weeks 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Japanese labels

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from dhde_preprocessing.forecast import BACKTEST_WEEKS, PENDING, forecast, write_forecast


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=BACKTEST_WEEKS, help="backtest weeks (rolling origins)")
    parser.add_argument("--input", default="output/integrated_fukui_train.parquet")
    parser.add_argument("--full", default="output/integrated_fukui.parquet",
                        help="full integrated table, for values known ahead (week-ahead museum bookings)")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    table = pd.read_parquet(args.input)
    full = pd.read_parquet(args.full) if Path(args.full).exists() else None
    fc, scores, report = forecast(table, weeks=args.weeks, full=full)

    print(f"\nBacktest, last {args.weeks} weeks (WAPE, lower is better):")
    wide = scores.pivot(index="node_key", columns="model", values="wape")
    print(wide.round(3).to_string())
    print("\nShare of later backtest days inside the low/high range fitted on earlier weeks:")
    print(scores.pivot(index="node_key", columns="model", values="coverage_holdout").round(2).to_string())
    print("\nModel used, and the factor converting its count into visitors:")
    for node_key, model in report["chosen_model"].items():
        c = report["calibration"][node_key]
        conv = f"x {c['factor']}" if c["factor"] else f"none ({c['status']})"
        print(f"  {node_key}: {model}, visitors = count {conv}")
    for node_key, reason in PENDING.items():
        print(f"  {node_key}: pending ({reason})")
    write_forecast(fc, scores, report, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
