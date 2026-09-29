#!/usr/bin/env python3
"""
12-month forecast of monthly visitors per Fukui node's municipality and of
Fukui guest-nights. See src/dhde_preprocessing/monthly_forecast.py.

Downloads its two sources itself (no build_node.py run needed) and writes:
    output/monthly_forecast.csv           one row per series per future month
    output/monthly_forecast_backtest.csv  backtest MAPE per series and model
    output/monthly_actuals.csv            the actual months each forecast was fitted on

Usage:
    python scripts/forecast_monthly.py
    python scripts/forecast_monthly.py --output-dir somewhere
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Japanese labels

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.monthly_forecast import build_monthly_forecast


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    forecast, scores, notes, actual = build_monthly_forecast()
    for note in notes:
        print(f"  {note}")
    print()
    print(scores.to_string(index=False))

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    forecast.to_csv(out / "monthly_forecast.csv", index=False)
    scores.to_csv(out / "monthly_forecast_backtest.csv", index=False)
    actual.to_csv(out / "monthly_actuals.csv", index=False)
    print(f"\nwrote {len(forecast)} rows to {out / 'monthly_forecast.csv'}")


if __name__ == "__main__":
    main()
