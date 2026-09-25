"""
Digital intent / RSI (route-search-intent-style) ingestion.

Source: code4fukui/fukui-kanko-trend-data, laid out as
{year}/area_{municipality}_{uuid}_daily_metrics.csv plus a
{year}/total_daily_metrics.csv (prefecture-wide). Both share the same
schema: date, map_views, search_views, directions, call_clicks,
website_clicks, average_rating, review_count_change,
review_count_by_rating_1..5.

Coverage varies by municipality and by year — some areas (e.g. 坂井市,
covering Tojinbo) only start appearing in the 2026 folder. Where a node's
area file exists for some years but not others, this fills the gap with
the prefecture-wide total for the missing years rather than dropping
those dates — the result column carries every date the total file
covers, with area-level values used wherever available.

If a node has no matching municipality at all (e.g. Fukui Station — Fukui
City isn't a tracked municipality in this dataset), the prefecture-wide
total is used outright for that node; see area_name: null in its config.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

RSI_COLS = [
    "map_views", "search_views", "directions", "call_clicks", "website_clicks",
    "average_rating", "review_count_change",
    "review_count_by_rating_1", "review_count_by_rating_2", "review_count_by_rating_3",
    "review_count_by_rating_4", "review_count_by_rating_5",
]


def _year_dirs(repo_root: str) -> list[Path]:
    root = Path(repo_root)
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit())


def _load_total(repo_root: str) -> pd.DataFrame:
    frames = []
    for year_dir in _year_dirs(repo_root):
        f = year_dir / "total_daily_metrics.csv"
        if f.exists():
            frames.append(pd.read_csv(f))
    if not frames:
        raise FileNotFoundError(f"No total_daily_metrics.csv found under any year folder in {repo_root}")
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    return df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def _load_area(repo_root: str, area_name: str) -> pd.DataFrame:
    frames = []
    for year_dir in _year_dirs(repo_root):
        matches = list(year_dir.glob(f"area_{area_name}_*.csv"))
        if matches:
            frames.append(pd.read_csv(matches[0]))
    if not frames:
        return pd.DataFrame(columns=["date", *RSI_COLS])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    return df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def load_rsi(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    rsi_cfg = node_cfg["sources"].get("rsi", {})
    if not rsi_cfg.get("enabled"):
        return None, unavailable_report("rsi", node_key, rsi_cfg.get("reason", "rsi disabled for this node"))

    repo_root = resolve_path(rsi_cfg["repo"])
    total = _load_total(repo_root)[["date", *RSI_COLS]]
    area_name = rsi_cfg.get("area_name")

    notes: list[str] = []
    if not area_name:
        notes.append("no matching municipality file for this node — using prefecture-wide total only")
        df = total
    else:
        area_df = _load_area(repo_root, area_name)
        if area_df.empty:
            notes.append(f"area file for '{area_name}' not found — falling back to prefecture-wide total only")
            df = total
        else:
            area_cols = {c: f"{c}__area" for c in RSI_COLS}
            merged = pd.merge(total, area_df[["date", *RSI_COLS]].rename(columns=area_cols), on="date", how="left")
            n_area_rows = merged["directions__area"].notna().sum()
            notes.append(
                f"area '{area_name}': {n_area_rows}/{len(merged)} days have area-level data, "
                f"rest filled from prefecture-wide total"
            )
            for col, area_col in area_cols.items():
                merged[col] = merged[area_col].fillna(merged[col])
                merged = merged.drop(columns=[area_col])
            df = merged

    report = validate_daily(df, source="rsi", node_key=node_key, notes=notes)
    return df, report
