from datetime import datetime, timezone

import joblib
import pandas as pd

from dhde_preprocessing import model_registry
from dhde_preprocessing.forecast import FIT_PREDICT, HORIZON, feature_table, forecast
from test_forecast import _table


def test_each_run_is_recorded_with_its_score_and_fitted_models(tmp_path):
    table = _table()
    fc, _, _, models = forecast(table, weeks=4)
    t0, t1 = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 8, tzinfo=timezone.utc)
    v0 = model_registry.record(model_registry.daily_rows(fc), tmp_path, models=models, now=t0)
    v1 = model_registry.record(model_registry.daily_rows(fc), tmp_path, models=models, now=t1)

    reg = model_registry.load(tmp_path)
    assert list(reg.columns) == model_registry.COLUMNS
    assert sorted(reg["version"].unique()) == [v0, v1]
    assert set(reg["series"]) == {"tojinbo", "katsuyama"}
    tojinbo = fc[fc["node_key"] == "tojinbo"].iloc[0]
    row = reg[(reg["version"] == v1) & (reg["series"] == "tojinbo")].iloc[0]
    assert row["model"] == tojinbo["model"] and row["error_pct"] == round(tojinbo["backtest_wape"] * 100, 1)

    # the saved models reproduce the forecast
    saved = joblib.load(tmp_path / "models" / v1 / "models.joblib")
    assert set(saved) == {"tojinbo", "katsuyama"}
    feats = feature_table(table, extra_days=HORIZON)
    for node_key, (name, fitted) in saved.items():
        future = feats[(feats["node_key"] == node_key) & feats["y"].isna()]
        fitted = {node_key: fitted} if name == "regression" else fitted
        pred = FIT_PREDICT[name][1](fitted, future).round()
        assert list(pred) == list(fc.loc[fc["node_key"] == node_key, "predicted"])


def test_compare_puts_the_latest_run_next_to_the_previous_one(tmp_path):
    def rows(err, series=("tojinbo",)):
        return pd.DataFrame({"forecast": "daily", "series": list(series), "model": "regression", "metric": "WAPE",
                             "error_pct": err, "baseline_error_pct": 30.0, "data_through": "2026-09-01"})

    model_registry.record(rows(20.0), tmp_path, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    model_registry.record(rows(18.5, ("tojinbo", "katsuyama")), tmp_path, now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    table = model_registry.compare(model_registry.load(tmp_path)).set_index("series")
    assert table.loc["tojinbo", "change_pp"] == -1.5
    assert pd.isna(table.loc["katsuyama", "prev_error_pct"])  # new node, nothing to compare with


def test_no_runs_yet_gives_an_empty_comparison(tmp_path):
    assert model_registry.compare(model_registry.load(tmp_path)).empty
