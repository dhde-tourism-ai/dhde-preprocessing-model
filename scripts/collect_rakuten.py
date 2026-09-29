#!/usr/bin/env python3
"""
Take today's Rakuten hotel-availability snapshot for every node that has
a `rakuten` source, so its history isn't lost.

Rakuten only answers "what's free right now", so each missed day is gone
for good. This appends to {out}/rakuten_snapshots/{node}.csv (one row per
lead time per day; a lead already taken today is skipped, so re-running
is safe). It's meant to run daily (see .github/workflows/collect-rakuten.yml),
which commits --out to the `live-data` branch. The build reads those
snapshots when DHDE_LIVE_DATA_ROOT points at a checkout of that branch.

Needs RAKUTEN_APP_ID and RAKUTEN_ACCESS_KEY in the environment.

Usage:
    python scripts/collect_rakuten.py --out history
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sources.rakuten import load_rakuten
from dhde_preprocessing.validation import print_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="history")
    args = parser.parse_args()
    os.environ["DHDE_LIVE_DATA_ROOT"] = str(Path(args.out).resolve())

    nodes = failures = 0
    for node_key in list_configured_nodes():
        cfg = load_node_config(node_key)
        if "rakuten" not in cfg["sources"]:
            continue
        nodes += 1
        _, report = load_rakuten(cfg)
        print_report(report)
        # "none taken this run": no keys, so reading old snapshots isn't a successful collection.
        failures += report.status == "error" or any("failed this run" in n or "none taken this run" in n
                                                    for n in report.notes)

    print(f"\ndone, {failures} of {nodes} node(s) with errors")
    # Fail the job only when every node failed (bad keys, blocked IP, API
    # down), so a real outage shows up red in Actions; one bad node doesn't.
    return 1 if nodes and failures == nodes else 0


if __name__ == "__main__":
    raise SystemExit(main())
