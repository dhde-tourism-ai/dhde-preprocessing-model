import numpy as np
import pandas as pd

from dhde_preprocessing.forecast import (
    CALENDAR_FEATURES, HORIZON, LAG_FEATURES, backtest, build_features, calendar, feature_table, forecast, node_target,
)


def _table(days=400, nodes=("tojinbo", "katsuyama")):
    """Synthetic integrated table: weekly pattern, busier days off, some noise."""
    dates = pd.date_range("2025-01-01", periods=days, name="date")
    cal = calendar(dates)
    rng = np.random.default_rng(0)
    frames = []
    for i, node_key in enumerate(nodes):
        level = 1000 * (i + 1)
        y = level * (1 + 0.6 * cal["is_day_off"].to_numpy()) * rng.normal(1, 0.05, days)
        frames.append(pd.DataFrame({"date": dates, "node_key": node_key,
                                    "camera_count": y if node_key == "tojinbo" else np.nan,
                                    "attraction_reserved_visitors": y if node_key == "katsuyama" else np.nan}))
    return pd.concat(frames, ignore_index=True)


def test_features_never_use_the_target_after_the_forecast_day():
    """Regression test for leakage: changing every value after an origin
    must not change any feature of the 7 days forecast from it."""
    y = node_target(_table(), "tojinbo")
    origin = y.index[200]
    changed = y.copy()
    changed[changed.index > origin] = 0
    window = slice(origin + pd.Timedelta(days=1), origin + pd.Timedelta(days=HORIZON))
    cols = CALENDAR_FEATURES + LAG_FEATURES
    a = build_features(y).loc[window, cols]
    b = build_features(changed).loc[window, cols]
    pd.testing.assert_frame_equal(a, b)


def test_calendar_marks_long_weekends_and_holiday_periods():
    cal = calendar(pd.date_range("2025-04-26", "2025-05-07"))
    assert cal.loc["2025-05-03", "off_run_len"] == 4  # Sat 3 May to Tue 6 May (Constitution Day, Greenery Day, Children's Day)
    assert cal.loc["2025-04-28", "is_day_off"] == 0 and cal.loc["2025-04-29", "is_day_off"] == 1  # Showa Day
    assert cal.loc["2025-05-01", "is_golden_week"] == 1 and cal.loc["2025-05-07", "is_golden_week"] == 0
    new_year = calendar(pd.date_range("2025-12-29", "2026-01-03"))
    assert new_year["is_day_off"].all()


def test_summed_target_is_missing_when_any_part_is_missing():
    t = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"]), "node_key": "rainbow_line",
                      "camera_gate1_vehicle_count": [10.0, 5.0], "camera_gate2_vehicle_count": [0.0, np.nan]})
    y = node_target(t, "rainbow_line")
    assert y.iloc[0] == 10 and pd.isna(y.iloc[1])


def test_backtest_only_tests_the_seven_days_after_each_origin():
    bt = backtest(feature_table(_table()), weeks=4)
    assert bt["horizon"].between(1, HORIZON).all()
    assert bt["origin"].nunique() == 4
    base = bt[bt["model"] == "baseline"].set_index(["node_key", "date"])["predicted"]
    y = node_target(_table(), "tojinbo")
    day = base.loc["tojinbo"].index[0]
    assert np.isclose(base.loc[("tojinbo", day)], y[day - pd.Timedelta(days=7)])  # same weekday last week


def test_forecast_covers_the_next_seven_days_with_a_range():
    table = _table()
    fc, scores, report = forecast(table, weeks=6)
    last = table["date"].max()
    for node_key, rows in fc.groupby("node_key"):
        assert list(rows["date"]) == list(pd.date_range(last + pd.Timedelta(days=1), periods=HORIZON))
    assert (fc["low"] <= fc["predicted"]).all() and (fc["predicted"] <= fc["high"]).all()
    assert set(fc["node_key"]) == {"tojinbo", "katsuyama"}
    assert "eiheiji" in report["pending"] and "eiheiji" not in set(fc["node_key"])
    # the synthetic data has a clear day-off effect, which last week's value copies badly
    best = scores.loc[scores.groupby("node_key")["wape"].idxmin(), "model"]
    assert (best != "baseline").all()
