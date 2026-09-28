#!/usr/bin/env python3
"""
Check whether one factor per node is enough to turn its count into visitors.

Run scripts/build_integrated.py first. Writes output/calibration_check.csv.
See src/dhde_preprocessing/calibration_check.py and docs/calibration.md.

Usage:
    python scripts/check_calibration.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Japanese labels

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from dhde_preprocessing.calibration_check import check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output/integrated_fukui_train.parquet")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    result = check(pd.read_parquet(args.input))
    cols = ["node_key", "factor", "count_per_visitor", "months", "corr", "ratio_spread", "high_month", "low_month"]
    print(result[[c for c in cols if c in result.columns]].to_string(index=False))
    out = Path(args.output_dir) / "calibration_check.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    print(f"[OK] wrote {out}")


if __name__ == "__main__":
    main()
