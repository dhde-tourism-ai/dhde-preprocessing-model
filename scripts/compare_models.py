#!/usr/bin/env python3
"""
Compare the latest forecast run with the one before it, per node, from
output/model_registry.csv (written by build_forecast.py and
forecast_monthly.py). Prints a Markdown table, ready to paste.

Usage:
    python scripts/compare_models.py
    python scripts/compare_models.py --forecast monthly
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing import model_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecast", choices=["daily", "monthly"], default="daily")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    table = model_registry.compare(model_registry.load(args.output_dir), args.forecast)
    if table.empty:
        print(f"no {args.forecast} runs recorded yet in {Path(args.output_dir) / model_registry.REGISTRY}")
        return
    print(f"{args.forecast} forecast, run {table.attrs['version']} vs {table.attrs['prev_version'] or 'none'}"
          f" (backtest error %, lower is better)\n")
    print(table.fillna("").to_markdown(index=False))


if __name__ == "__main__":
    main()
