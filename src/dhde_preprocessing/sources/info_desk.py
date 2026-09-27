"""
Tourist information desk enquiries — daily counts, Kanazawa only.

Source: Milli's 観光案内所 窓口対応データ Google Sheets, one per desk
(金沢駅観光案内所, 金沢中央観光案内所). Long format: 日付, 属性, 数値 — one row
per day per enquiry category (金沢市内観光, 加賀, 能登, 外国人, ... and a
合計 total row), daily from 2023-04 with no gaps as of 2026-09.

Only the 合計 (total) and 外国人 (foreign visitors) rows are kept, summed
across the node's configured desks. This is a demand signal (people
walking up to ask for help), which Ishikawa otherwise lacks — there is
no camera, RSI or daily hotel data there. The sheets lag by a month or
two, so the most recent days will be missing from this source.
"""
from __future__ import annotations

import pandas as pd

from ..validation import SourceReport, unavailable_report, validate_daily
from .gsheet import fetch_sheet

CATEGORY_COLUMNS = {"合計": "info_desk_total", "外国人": "info_desk_foreign"}


def load_info_desk(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    d_cfg = node_cfg["sources"].get("info_desk", {})
    if not d_cfg.get("enabled"):
        return None, unavailable_report("info_desk", node_key, d_cfg.get("reason", "no tourist information desk data for this node"))

    frames, notes = [], []
    for i, desk in enumerate(d_cfg["desks"]):
        try:
            raw, note = fetch_sheet(desk["sheet_id"], f"info_desk_{node_key}_{i}")
        except Exception as e:  # noqa: BLE001 - one desk failing shouldn't drop the others
            notes.append(f"{desk['name']}: fetch failed and no cache: {e!r}")
            continue
        notes.append(f"{desk['name']}: {note}")
        raw = raw[raw["属性"].astype(str).str.strip().isin(CATEGORY_COLUMNS)]
        frames.append(pd.DataFrame({
            "date": pd.to_datetime(raw["日付"], errors="coerce").dt.normalize(),
            "category": raw["属性"].astype(str).str.strip().map(CATEGORY_COLUMNS),
            "count": pd.to_numeric(raw["数値"], errors="coerce"),
        }))

    if not frames:
        return None, SourceReport(source="info_desk", node_key=node_key, status="error", notes=notes)

    long = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    daily = (long.pivot_table(index="date", columns="category", values="count", aggfunc="sum")
             .reindex(columns=list(CATEGORY_COLUMNS.values())).reset_index())
    daily.columns.name = None

    report = validate_daily(daily, source="info_desk", node_key=node_key, notes=notes)
    return daily, report
