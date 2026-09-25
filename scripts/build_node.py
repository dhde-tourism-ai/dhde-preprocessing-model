#!/usr/bin/env python3
"""
Build one (or all) node master tables end to end.

Usage:
    python scripts/build_node.py --node tojinbo
    python scripts/build_node.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Japanese labels

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes
from dhde_preprocessing.join import build_node_table, write_outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", help="node key, e.g. tojinbo")
    parser.add_argument("--all", action="store_true", help="build every configured node")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    if not args.node and not args.all:
        parser.error("pass --node <key> or --all")

    node_keys = list_configured_nodes() if args.all else [args.node]
    for node_key in node_keys:
        master, survey_df, reports = build_node_table(node_key)
        write_outputs(node_key, master, survey_df, reports, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
