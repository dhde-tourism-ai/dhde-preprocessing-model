"""
Monthly visitor counts per city and prefecture — the one demand signal
measured the same way everywhere in Japan, so it's what makes Fukui,
Ishikawa and Toyama nodes directly comparable.

Source: code4fukui/japan-kanko-stat, data/all.csv (month YYYYMM, lgcode,
n), built daily from the Japan Tourism Agency's digital tourism
statistics open data. One row per month per prefecture (2-digit lgcode,
e.g. 17 = Ishikawa) or city (5-digit, e.g. 17201 = Kanazawa), from
2021-01.

The master table is daily, so each month's figure is repeated on every
day of that month in columns named `*_visitors_month` — the value is the
MONTH's total, not that day's. Dividing it into days (or not) is a
modeling-stage decision.

Optional source: only nodes that declare `monthly_visitors` in their
config get it (see join.OPTIONAL_SOURCES).
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily


def expand_to_days(monthly: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """(month YYYYMM, n) -> one row per calendar day carrying its month's n."""
    months = pd.to_datetime(monthly["month"].astype(int).astype(str), format="%Y%m")
    days = pd.date_range(months.min(), months.max() + pd.offsets.MonthEnd(0), freq="D")
    by_month = pd.Series(monthly["n"].to_numpy(), index=months.dt.to_period("M"))
    return pd.DataFrame({"date": days, value_col: days.to_period("M").map(by_month).to_numpy()})


def load_monthly_visitors(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    m_cfg = node_cfg["sources"].get("monthly_visitors", {})
    if not m_cfg.get("enabled"):
        return None, unavailable_report("monthly_visitors", node_key, m_cfg.get("reason", "monthly visitors disabled for this node"))

    stats = pd.read_csv(f"{resolve_path(m_cfg['repo'])}/data/all.csv")
    frames, notes = [], []
    for level in ("city", "pref"):
        code = m_cfg[f"{level}_lgcode"]
        rows = stats[stats["lgcode"] == code].sort_values("month")
        if rows.empty:
            notes.append(f"lgcode {code} ({level}) not found in {m_cfg['repo']}/data/all.csv")
            continue
        frames.append(expand_to_days(rows, f"{level}_visitors_month"))
        notes.append(f"{level} lgcode {code}: {rows['month'].min()}–{rows['month'].max()}")

    if not frames:
        return None, SourceReport(source="monthly_visitors", node_key=node_key, status="error", notes=notes)

    daily = frames[0]
    for f in frames[1:]:
        daily = pd.merge(daily, f, on="date", how="outer")
    notes.append("monthly totals repeated on every day of their month — not daily counts")
    report = validate_daily(daily, source="monthly_visitors", node_key=node_key, notes=notes)
    return daily, report
