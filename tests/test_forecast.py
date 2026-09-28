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


def test_visitor_factor_uses_the_daily_mean_so_sensor_gaps_dont_inflate_it():
    from dhde_preprocessing.forecast import calibration
    dates = pd.date_range("2025-01-01", "2025-12-31")
    y = pd.Series(100.0, index=dates)
    y.iloc[:40] = np.nan  # a 40-day outage
    info = calibration(y, {"official_visitors": {"year": 2025, "count": 73_000}})
    assert info["status"] == "ok" and np.isclose(info["factor"], 73_000 / (100 * 365))  # 2.0, not 73k / (325 * 100)


def test_no_official_figure_or_too_few_days_means_no_visitor_conversion():
    from dhde_preprocessing.forecast import calibration
    y = pd.Series(100.0, index=pd.date_range("2025-01-01", periods=200))
    assert calibration(y, {"official_visitors": {"year": 2025, "count": None}})["factor"] is None
    short = calibration(y, {"official_visitors": {"year": 2025, "count": 50_000}})
    assert short["factor"] is None and "too few" in short["status"]


def test_forecast_reports_visitors_next_to_the_measured_count():
    fc, _, report = forecast(_table(), weeks=4)
    row = fc[fc["node_key"] == "tojinbo"].iloc[0]
    factor = report["calibration"]["tojinbo"]["factor"]
    assert factor and np.isclose(row["visitors_est"], round(row["predicted"] * factor), atol=1)
    assert row["visitors_low"] <= row["visitors_est"] <= row["visitors_high"]


def _with_bookings(missing_forecast_days):
    """Synthetic table where Katsuyama has week-ahead bookings, plus a full
    table running 7 days on, with some of those days' bookings missing."""
    table = _table()
    k = table["node_key"] == "katsuyama"
    table.loc[k, "attraction_reserved_visitors_lead7"] = table.loc[k, "attraction_reserved_visitors"] * 0.8
    last = table["date"].max()
    future = pd.DataFrame({"date": pd.date_range(last + pd.Timedelta(days=1), periods=HORIZON), "node_key": "katsuyama"})
    future["attraction_reserved_visitors_lead7"] = 900.0
    future.loc[future.index[:missing_forecast_days], "attraction_reserved_visitors_lead7"] = np.nan
    return table, pd.concat([table, future], ignore_index=True)


def test_a_late_booking_feed_falls_back_instead_of_crashing():
    """Regression test (PR #13 review): with bookings missing on every
    forecast day, the regression raised 'feature names should match' and
    the whole run failed; with some missing, those days came out NaN."""
    for missing in (HORIZON, 3):
        table, full = _with_bookings(missing)
        fc, _, report = forecast(table, weeks=4, full=full)
        k = fc[fc["node_key"] == "katsuyama"]
        assert k["predicted"].notna().all()
        assert k["week_ahead_missing"].sum() == missing
        assert any("no week-ahead bookings" in w for w in report["warnings"])


def test_warns_when_week_ahead_columns_are_missing_from_the_input():
    """Node tables built without the week-ahead columns (e.g. from main before
    this PR) must say so, not silently score worse."""
    _, _, report = forecast(_table(), weeks=4)
    assert any("katsuyama: no week-ahead bookings in the input" in w for w in report["warnings"])


def test_regression_itself_covers_days_without_week_ahead_bookings():
    """The end-to-end test above can pick LightGBM, which tolerates gaps, so
    check the regression directly: it raised with every forecast day missing,
    and returned NaN for the missing ones otherwise."""
    from dhde_preprocessing.forecast import _trainable, fit_predict_regression
    for missing in (HORIZON, 3):
        table, full = _with_bookings(missing)
        feats = feature_table(table, extra_days=HORIZON, full=full)
        future = feats[(feats["node_key"] == "katsuyama") & (feats["date"] > table["date"].max())]
        pred = fit_predict_regression(_trainable(feats), future)
        assert not np.isnan(pred).any()


def test_zero_bookings_a_week_ahead_are_marked_as_closed_for_the_regression():
    from dhde_preprocessing.forecast import _regression_matrix
    f = build_features(node_target(_table(), "katsuyama"))
    f["booked_lead7"] = [0.0, np.nan] + [5.0] * (len(f) - 2)
    x = _regression_matrix(f, ["booked_lead7"])
    assert x["booked_lead7_is_zero"].iloc[0] == 1 and pd.isna(x["booked_lead7_is_zero"].iloc[1])
    assert x["booked_lead7_is_zero"].iloc[2] == 0


def test_last_day_of_a_range_still_sees_the_day_after_it():
    """Regression test (PR #13 review): day 7 of a live forecast is the last
    day calendar() sees. Sat 2026-10-03 got off_run_len 1 and next_day_off 0."""
    cal = calendar(pd.date_range("2026-09-27", "2026-10-03"))
    assert cal.loc["2026-10-03", "off_run_len"] == 2
    assert cal.loc["2026-10-03", "next_day_off"] == 1
    assert cal.loc["2026-09-27", "prev_day_off"] == 1  # Sun 27th: Sat 26th was off too


def test_bookings_so_far_are_not_used_as_final_counts():
    """Regression test (PR #13 review): if the museum feed runs late, the last
    days hold bookings so far. They must become missing, with a warning."""
    table = _table()
    k = table["node_key"] == "katsuyama"
    table.loc[k, "attraction_from_earlier_snapshot"] = 0
    table.loc[k, "attraction_bookings_final"] = 1
    last_two = k & (table["date"] >= table["date"].max() - pd.Timedelta(days=1))
    table.loc[last_two, "attraction_from_earlier_snapshot"] = 1
    y = node_target(table, "katsuyama")
    assert y.iloc[-2:].isna().all() and y.iloc[:-2].notna().all()
    _, _, report = forecast(table, weeks=4)
    assert any("only had bookings so far" in w for w in report["warnings"])


def test_a_backtest_too_short_to_check_the_range_does_not_crash():
    """Regression test (PR #13 review): np.quantile on an empty first half
    raised IndexError with --weeks 1 or a node whose data starts late."""
    fc, scores, _ = forecast(_table(), weeks=1)
    assert scores["coverage_holdout"].isna().all()
    assert fc["predicted"].notna().all()


def test_visitor_factor_can_use_a_fiscal_year():
    """Katsuyama's official figure is the museum's FY (April to March), so the
    count must be averaged over the same days, not the calendar year."""
    from dhde_preprocessing.forecast import calibration
    dates = pd.date_range("2025-01-01", "2026-06-30")
    y = pd.Series(np.where(dates < pd.Timestamp("2025-04-01"), 10.0, 100.0), index=dates)
    info = calibration(y, {"official_visitors": {"period": ["2025-04-01", "2026-03-31"], "count": 73_000}})
    assert np.isclose(info["factor"], 73_000 / (100 * 365))  # January-March 2025 (10/day) is outside the FY
