#!/usr/bin/env python3
"""
Backtest the pickup model (final hotel occupancy from bookings so far) on
each reservation repo, against the two baselines. Prints a Markdown table
per repo and writes output/pickup_backtest.csv.
See src/dhde_preprocessing/pickup.py and docs/pickup.md.

Usage:
    python scripts/backtest_pickup.py
    python scripts/backtest_pickup.py --repos fukui-station-kanko-reservation --holdout-days 90
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from dhde_preprocessing import pickup

REPOS = [
    "fukui-station-kanko-reservation",
    "echizen-coast-kanko-reservation",
    "fukui-kanko-reservation",
    "mikatagoko-kanko-reservation",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", nargs="+", default=REPOS)
    parser.add_argument("--holdout-days", type=int, default=120,
                        help="the last N days of finished nights are held out and scored")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    tables = []
    for repo in args.repos:
        occ, final = pickup.curves(pickup.load_clean(repo))
        test_from = final.index.max() - pd.Timedelta(days=args.holdout_days - 1)
        table = pickup.score(pickup.backtest(occ, final), test_from)
        print(f"\n### {repo}: nights {final.index.min():%Y-%m-%d} to {final.index.max():%Y-%m-%d}, "
              f"held out from {test_from:%Y-%m-%d}\n")
        print("| " + " | ".join(table.columns) + " |")
        print("|" + "---|" * len(table.columns))
        for row in table.astype(object).where(table.notna(), "").itertuples(index=False):
            print("| " + " | ".join(str(v) for v in row) + " |")
        tables.append(table.assign(repo=repo, test_from=test_from.date()))

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(tables).to_csv(out / "pickup_backtest.csv", index=False)
    print(f"\nwrote {out / 'pickup_backtest.csv'}")


if __name__ == "__main__":
    main()
