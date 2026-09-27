import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "collect_live", Path(__file__).resolve().parent.parent / "scripts" / "collect_live.py")
collect_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_live)


def test_upsert_keeps_history_and_newer_pull_wins():
    # JARTIC only returns ~90 days, so old rows must survive a new pull,
    # and the partial latest day must be replaced by the next pull's value.
    existing = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "volume_total": [100, 40]})
    new = pd.DataFrame({"date": ["2026-01-02", "2026-01-03"], "volume_total": [90, 120]})
    merged = collect_live.upsert_daily(existing, new)
    assert list(merged["date"].dt.strftime("%Y-%m-%d")) == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert list(merged["volume_total"]) == [100, 90, 120]
