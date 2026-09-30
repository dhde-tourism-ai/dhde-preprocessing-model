"""
A record of every forecast run: which model each node used, and how wrong
it was in the backtest, so runs can be compared over time.

Each run gets a version, `<UTC time>-<git commit>` (`+dirty` when tracked
files had uncommitted changes, so the code can't be recovered from git;
`-2`, `-3`... when a run in the same second already took the version).
It appends one row per node (daily) or series (monthly) to
`output/model_registry.csv`:

    version, trained_at, commit, forecast, series, model, metric,
    error_pct, baseline_error_pct, data_through

`error_pct` is the backtest error of the model used (daily: WAPE, monthly:
MAPE), `baseline_error_pct` that of the baseline on the same days, so a
row says both how good the model is and how much it beats "same as last
week/year". The fitted daily models are saved with joblib in
`output/models/<version>/models.joblib` (node -> (model name, fitted));
the monthly models are fixed rules with nothing fitted, so the version's
commit is enough to rerun them.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd

REGISTRY = "model_registry.csv"
COLUMNS = ["version", "trained_at", "commit", "forecast", "series", "model", "metric",
           "error_pct", "baseline_error_pct", "data_through"]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def new_version(now: datetime | None = None) -> tuple[str, str, str]:
    """(version, trained_at, commit) for a run starting now."""
    now = now or datetime.now(timezone.utc)
    commit = _git("rev-parse", "--short", "HEAD") or "nogit"
    if commit != "nogit" and _git("status", "--porcelain", "--untracked-files=no"):
        commit += "+dirty"
    return f"{now:%Y%m%d-%H%M%S}-{commit}", now.isoformat(timespec="seconds"), commit


def daily_rows(fc: pd.DataFrame) -> pd.DataFrame:
    """One row per node from forecast.forecast()'s rows."""
    first = fc.groupby("node_key", sort=True).first().reset_index()
    return pd.DataFrame({
        "forecast": "daily", "series": first["node_key"], "model": first["model"], "metric": "WAPE",
        "error_pct": (first["backtest_wape"] * 100).round(1),
        "baseline_error_pct": (first["baseline_wape"] * 100).round(1),
        "data_through": pd.to_datetime(first["issued_from"]).dt.strftime("%Y-%m-%d"),
    })


def monthly_rows(fc: pd.DataFrame) -> pd.DataFrame:
    """One row per series from monthly_forecast.build_monthly_forecast()'s rows."""
    first = fc.groupby("series", sort=False).first().reset_index()
    return pd.DataFrame({
        "forecast": "monthly", "series": first["series"], "model": first["model"], "metric": "MAPE",
        "error_pct": first["backtest_mape_pct"], "baseline_error_pct": first["baseline_mape_pct"],
        "data_through": first["data_through"],
    })


def record(rows: pd.DataFrame, output_dir: str = "output", models: dict | None = None,
           now: datetime | None = None) -> str:
    """Append `rows` (from daily_rows / monthly_rows) under a new version, save
    `models` next to it, and return the version."""
    version, trained_at, commit = new_version(now)
    out = Path(output_dir)
    # Two runs in the same second on one commit: keep them apart, or compare()
    # sees duplicate series and the second models.joblib overwrites the first.
    taken = set(load(output_dir)["version"]) | {p.name for p in (out / "models").glob("*")}
    base, n = version, 1
    while version in taken:
        n += 1
        version = f"{base}-{n}"
    out.mkdir(parents=True, exist_ok=True)
    if models:
        (out / "models" / version).mkdir(parents=True, exist_ok=True)
        joblib.dump(models, out / "models" / version / "models.joblib")
    rows = rows.assign(version=version, trained_at=trained_at, commit=commit)[COLUMNS]
    path = out / REGISTRY
    rows.to_csv(path, mode="a", header=not path.exists(), index=False)
    return version


def load(output_dir: str = "output") -> pd.DataFrame:
    path = Path(output_dir) / REGISTRY
    return pd.read_csv(path, dtype={"version": str, "commit": str, "data_through": str}) if path.exists() else pd.DataFrame(columns=COLUMNS)


def compare(registry: pd.DataFrame, forecast: str = "daily") -> pd.DataFrame:
    """Latest run against the one before it, one row per series.

    A series missing from the previous run (new node) has empty `prev_*`.
    """
    runs = registry[registry["forecast"] == forecast]
    versions = sorted(runs["version"].unique())
    if not versions:
        return pd.DataFrame()
    now = runs[runs["version"] == versions[-1]].set_index("series")
    prev = (runs[runs["version"] == versions[-2]].set_index("series") if len(versions) > 1
            else pd.DataFrame(columns=runs.columns).set_index("series"))
    table = pd.DataFrame({
        "prev_model": prev["model"], "prev_error_pct": prev["error_pct"].astype(float),
        "model": now["model"], "error_pct": now["error_pct"],
        "baseline_error_pct": now["baseline_error_pct"],
    }).loc[now.index]
    table["change_pp"] = (table["error_pct"] - table["prev_error_pct"]).round(1)
    table.attrs = {"version": versions[-1], "prev_version": versions[-2] if len(versions) > 1 else None}
    return table.reset_index()
