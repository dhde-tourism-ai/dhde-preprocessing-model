"""
Is one factor per node enough to turn its measured count into visitors?

forecast.calibration() converts each node's count (camera detections,
cars, museum bookings, hotel guests) into visitors with one factor, so the
official yearly total matches. That is only sound if the count rises and
falls through the year the way visitors do. This checks it against the
one independent monthly visitor figure there is: the digital tourism
statistics for the municipality each node sits in (monthly_visitors,
monthly_forecast.SERIES), from 2025-01, the first month comparable for
Fukui.

Per node:
- `months`: months compared (a month needs 20+ measured days; its total is
  the daily mean x days in the month, so outage days don't shrink it).
- `corr`: correlation of the monthly totals. Near 1 = same seasonal shape.
- `ratio_spread`: how much count / town visitors moves month to month
  (standard deviation / mean). Small = one factor fits every month.
- `high_month`, `low_month`: the months furthest above and below the
  average ratio, as a share of it.

The town is bigger than the site (Sakai city is more than Tojinbo), so a
perfect match isn't expected; a big seasonal mismatch means the factor
spreads the year's visitors over the wrong months.
"""
from __future__ import annotations

import pandas as pd

from .config import load_node_config
from .forecast import TARGETS, calibration, node_target
from .monthly_forecast import SERIES, visitors_series
from .sources.monthly_visitors import load_official_table

MIN_DAYS_PER_MONTH = 20
FIRST_MONTH = pd.Period("2025-01", freq="M")


def monthly_count(y: pd.Series) -> pd.Series:
    """Monthly total of a daily count: daily mean x days, months with 20+ measured days only."""
    m = y.groupby(y.index.to_period("M")).agg(["mean", "count"])
    m = m[m["count"] >= MIN_DAYS_PER_MONTH]
    return m["mean"] * m.index.days_in_month


def compare(site: pd.Series, town: pd.Series) -> dict:
    j = pd.DataFrame({"site": site, "town": town}).dropna()
    j = j[j.index >= FIRST_MONTH]
    if len(j) < 6:
        return {"months": len(j)}
    ratio = j["site"] / j["town"]
    rel = ratio / ratio.mean()
    return {
        "months": len(j),
        "corr": round(j["site"].corr(j["town"]), 2),
        "ratio_spread": round(ratio.std() / ratio.mean(), 2),
        "high_month": f"{rel.idxmax()} ({rel.max():.2f}x)",
        "low_month": f"{rel.idxmin()} ({rel.min():.2f}x)",
    }


def check(table: pd.DataFrame) -> pd.DataFrame:
    """One row per forecast node: its visitor factor and how well its count tracks its town."""
    official, _ = load_official_table()
    towns = {s.name: s for s in SERIES if s.kind == "visitors"}
    rows = []
    for node_key in TARGETS:
        if node_key not in set(table["node_key"]):
            continue
        y = node_target(table, node_key)
        cal = calibration(y, load_node_config(node_key))
        row = {"node_key": node_key, "measured": TARGETS[node_key][1], "factor": cal["factor"],
               "count_per_visitor": round(1 / cal["factor"], 2) if cal["factor"] else None,
               "official_visitors": cal["official_visitors"], "official_period": cal["official_period"]}
        spec = towns.get(node_key)
        if spec is not None and official is not None:
            row["town"] = spec.label
            row.update(compare(monthly_count(y), visitors_series(official, spec.codes)))
        rows.append(row)
    return pd.DataFrame(rows)
