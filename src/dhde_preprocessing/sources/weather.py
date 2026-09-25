"""
JMA weather ingestion.

Station identity is confirmed via code4fukui/jma_station (station master
CSV only — no observations). Actual hourly/daily observations come from
JMA's download portal: https://www.data.jma.go.jp/risk/obsdl/index.php

TODO (explicitly deferred, per project scope): that portal is a
form/session-driven download, not a stable REST endpoint, so this module
does NOT automate fetching it. Instead it expects a CSV export dropped at
node_cfg["sources"]["weather"]["input_csv"] — a human runs the obsdl form
for the configured station/date range and drops the export there. When
that file isn't present yet, this reports the source as "unavailable"
with the station info needed to pull it, rather than failing the whole
pipeline.

The obsdl export format itself (multi-row header, per-column quality
flags, often Shift-JIS encoded) is parsed on a best-effort basis below —
it has NOT been verified against a real export, since none was available
while building this. If a real export doesn't parse cleanly, this will
surface as a status="error" SourceReport (see validation.py) rather than
crash the run — treat the exact parsing logic as a follow-up to verify
against a real file, not as settled.
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

OBSDL_PORTAL_URL = "https://www.data.jma.go.jp/risk/obsdl/index.php"


def load_weather(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    w_cfg = node_cfg["sources"].get("weather", {})
    if not w_cfg.get("enabled"):
        return None, unavailable_report("weather", node_key, w_cfg.get("reason", "weather disabled for this node"))

    input_csv = w_cfg.get("input_csv")
    station_name = w_cfg.get("station_name", "unknown")
    station_id = w_cfg.get("station_id", "unknown")

    if not input_csv:
        return None, unavailable_report(
            "weather", node_key,
            f"No obsdl export dropped in yet for station {station_name} (id {station_id}). "
            f"TODO: manually export from {OBSDL_PORTAL_URL} and set sources.weather.input_csv "
            f"in config/nodes/{node_key}.yaml — automating this download is a follow-up, see module docstring.",
        )

    path = resolve_path(input_csv)
    try:
        df = _parse_obsdl_csv(path)
    except Exception as e:  # noqa: BLE001 - a bad/unexpected export format must not crash the run
        return None, SourceReport(
            source="weather", node_key=node_key, status="error",
            notes=[f"failed to parse obsdl export at {path}: {e!r}",
                   "obsdl format parsing is unverified against a real export — see module docstring"],
        )

    report = validate_daily(df, source="weather", node_key=node_key,
                             notes=[f"station={station_name} (id {station_id})"])
    return df, report


def _parse_obsdl_csv(path: str) -> pd.DataFrame:
    """Best-effort obsdl export parser — UNVERIFIED, see module docstring.

    JMA's obsdl exports typically have: a few header rows (station name,
    item labels), a data-quality-flag column after each numeric column,
    and Shift-JIS encoding. This assumes that shape; adjust once a real
    export is available to test against.
    """
    try:
        raw = pd.read_csv(path, encoding="shift_jis", skiprows=5, header=None)
    except UnicodeDecodeError:
        raw = pd.read_csv(path, encoding="utf-8", skiprows=5, header=None)

    # Expected columns, in order: date, then (value, quality_flag, homogeneity_flag)
    # triples per weather element. Only the first element (assumed to be
    # temperature) plus date is extracted here as a minimal viable slice —
    # extend once the real column layout is confirmed.
    df = pd.DataFrame({
        "date": pd.to_datetime(raw[0], errors="coerce").dt.normalize(),
        "temp_c": pd.to_numeric(raw[1], errors="coerce"),
    })
    return df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
