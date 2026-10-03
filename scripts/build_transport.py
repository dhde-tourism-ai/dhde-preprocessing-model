#!/usr/bin/env python3
"""
Build the app's transport data (Transport page and the map's Public transport
layer), run daily by dhde-app's daily-data workflow after build_real_data.py.

Writes to --output-dir:

- transport.json, transport_map.json: public transport access per site from
  the open GTFS-JP bus timetables (downloaded fresh with --refresh)
- transport_modes.json: visitors by mode, the tourism survey's mode shares
  x the visitor estimates in the app's real_data.json (--real-data)
- transport_trends.json: Google Trends search interest (skipped, not failed,
  when Google refuses the request)

Each part runs on its own: a part that fails writes nothing, so the app keeps
yesterday's file for it. Exits 1 if the access part (the timetables) fails,
since that is the part the page can't do without.

Usage:
    python scripts/build_transport.py --output-dir output/transport \\
        --real-data ../dhde-app/public/data/real_data.json --refresh
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.transport import access, modes, trends  # noqa: E402


def main() -> int:
    ws = os.environ.get("DHDE_WORKSPACE_ROOT")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", default="output/transport")
    ap.add_argument("--real-data", required=True, help="the app's real_data.json (visitor estimates)")
    ap.add_argument("--survey-dir", default=str(Path(ws) / "fukui-kanko-survey") if ws else None,
                    help="checkout of code4fukui/fukui-kanko-survey (default: $DHDE_WORKSPACE_ROOT/fukui-kanko-survey)")
    ap.add_argument("--date", help="start of the timetable reference week (default: today, JST)")
    ap.add_argument("--refresh", action="store_true", help="re-download the timetables")
    ap.add_argument("--cache-dir", default=str(access.CACHE), help="where the timetable zips are kept")
    ap.add_argument("--skip-trends", action="store_true")
    args = ap.parse_args()
    out = Path(args.output_dir)

    ok = True
    print("== Public transport access (GTFS-JP)")
    try:
        access.build(out, start=date.fromisoformat(args.date) if args.date else None, refresh=args.refresh,
                     cache_dir=Path(args.cache_dir))
    except Exception:
        traceback.print_exc()
        print("[FAIL] transport.json / transport_map.json not built")
        ok = False

    print("== Visitors by mode (tourism survey)")
    try:
        modes.build(Path(args.survey_dir) if args.survey_dir else None, Path(args.real_data), out / "transport_modes.json")
    except Exception:
        traceback.print_exc()
        print("[FAIL] transport_modes.json not built")

    if not args.skip_trends:
        print("== Search interest (Google Trends)")
        trends.build(out / "transport_trends.json")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
