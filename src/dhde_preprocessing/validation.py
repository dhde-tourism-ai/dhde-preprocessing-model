"""
Shared validation for every source's cleaned output.

Every ingestion function returns (df, SourceReport) instead of just df —
callers log/report the SourceReport rather than the ingestion function
failing silently on a bad or partial source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SourceReport:
    source: str
    node_key: str
    status: str  # "ok" | "unavailable" | "error"
    row_count: int = 0
    date_min: str | None = None
    date_max: str | None = None
    expected_days: int | None = None
    missing_days: int | None = None
    null_rates: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "node_key": self.node_key,
            "status": self.status,
            "row_count": self.row_count,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "expected_days": self.expected_days,
            "missing_days": self.missing_days,
            "null_rates": self.null_rates,
            "notes": self.notes,
        }


def unavailable_report(source: str, node_key: str, reason: str) -> SourceReport:
    """Build the report for a source that's a known, intentional gap for
    this node (e.g. Katsuyama has no camera) — used INSTEAD of silently
    omitting the source, so the gap is visible in the coverage report.
    """
    return SourceReport(source=source, node_key=node_key, status="unavailable", notes=[reason])


def validate_daily(
    df: pd.DataFrame,
    *,
    source: str,
    node_key: str,
    date_col: str = "date",
    notes: list[str] | None = None,
) -> SourceReport:
    """Basic checks every daily, date-keyed source gets: row count, date
    coverage (including calendar gaps within the observed range), and
    per-column null rates. Never raises — a bad source is reported, not
    a crashed pipeline.
    """
    notes = list(notes or [])
    if df.empty:
        return SourceReport(source=source, node_key=node_key, status="error",
                             notes=notes + ["cleaned output is empty"])

    dates = pd.to_datetime(df[date_col]).dt.normalize()
    date_min, date_max = dates.min(), dates.max()
    expected_days = (date_max - date_min).days + 1
    actual_days = dates.nunique()
    missing_days = expected_days - actual_days
    if missing_days > 0:
        notes.append(f"{missing_days} calendar day(s) missing between {date_min.date()} and {date_max.date()}")

    null_rates = {
        col: round(float(df[col].isna().mean()), 4)
        for col in df.columns
        if col != date_col
    }
    high_null_cols = [c for c, r in null_rates.items() if r > 0.2]
    if high_null_cols:
        notes.append(f"high null rate (>20%) in: {', '.join(high_null_cols)}")

    return SourceReport(
        source=source, node_key=node_key, status="ok",
        row_count=len(df), date_min=str(date_min.date()), date_max=str(date_max.date()),
        expected_days=expected_days, missing_days=missing_days,
        null_rates=null_rates, notes=notes,
    )


def print_report(report: SourceReport) -> None:
    d = report.to_dict()
    print(f"  [{d['status'].upper():11s}] {d['source']:10s} node={d['node_key']}")
    if d["status"] == "ok":
        print(f"               rows={d['row_count']}  range={d['date_min']} -> {d['date_max']}"
              f"  missing_days={d['missing_days']}")
    for note in d["notes"]:
        print(f"               note: {note}")
