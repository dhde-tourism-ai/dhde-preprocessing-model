import numpy as np
import pandas as pd

from dhde_preprocessing import monthly_forecast as monthly


def _series(start: str, values) -> pd.Series:
    return pd.Series(list(values), index=pd.period_range(start, periods=len(values), freq="M"), dtype=float)


SEASON = [100, 90, 140, 160, 150, 120, 180, 220, 150, 160, 130, 110]


def test_pure_seasonality_is_forecast_exactly_by_the_baseline():
    y = _series("2023-01", SEASON * 3)
    res = monthly.forecast_series(y, [])
    assert res["model"] == monthly.BASELINE
    assert res["mape"][monthly.BASELINE] < 1e-9
    fc = res["forecast"]
    assert list(fc["month"]) == [str(p) for p in pd.period_range("2026-01", periods=12, freq="M")]
    assert np.allclose(fc["predicted"], SEASON)


def test_steady_growth_is_picked_up_by_own_growth():
    y = _series("2021-01", [v * 1.2 ** (i // 12) for i, v in enumerate(SEASON * 5)])
    res = monthly.forecast_series(y, [])
    assert res["model"] == "own_growth"
    # Half of the 20% growth is carried forward.
    assert np.allclose(res["forecast"]["predicted"].iloc[0], SEASON[0] * 1.2 ** 4 * np.exp(0.5 * np.log(1.2)))


def test_backtest_scores_every_model_given_a_neighbour():
    nb = _series("2021-01", [v * 1.2 ** (i // 12) for i, v in enumerate(SEASON * 6)])
    y = _series("2021-01", [v * 1.2 ** max(0, i // 12 - 1) for i, v in enumerate(SEASON * 6)])
    bt = monthly.backtest(np.log(y), [np.log(nb)])
    assert set(bt["model"]) == set(monthly.MODELS)


def test_comparable_from_drops_earlier_months():
    y = _series("2023-01", [v * 5 for v in SEASON] + SEASON * 2)  # a level break at 2024-01
    res = monthly.forecast_series(y, [], comparable_from="2024-01")
    assert res["mape"][monthly.BASELINE] < 1e-9
    assert np.allclose(res["forecast"]["predicted"], SEASON)


def test_range_always_contains_the_forecast():
    rng = np.random.default_rng(0)
    y = _series("2022-01", [v * rng.uniform(0.8, 1.3) for v in SEASON * 4])
    fc = monthly.forecast_series(y, [])["forecast"]
    assert ((fc["low"] <= fc["predicted"]) & (fc["predicted"] <= fc["high"])).all()


def test_one_year_of_history_gives_baseline_without_a_range():
    # Nothing can be backtested: every target needs its own last-year month.
    y = _series("2025-01", SEASON)
    res = monthly.forecast_series(y, [])
    assert res["model"] == monthly.BASELINE and res["backtest_n"] == 0
    assert np.allclose(res["forecast"]["predicted"], SEASON)
    assert res["forecast"]["low"].isna().all()


def test_backtest_n_counts_distinct_target_months():
    # 20 months from 2025-01: the baseline can score only 2026-01..08, from many origins.
    y = _series("2025-01", SEASON + SEASON[:8])
    res = monthly.forecast_series(y, [])
    assert res["backtest_n"] == 8
    assert res["range_rough"]


def test_failed_visitor_download_skips_visitor_series(monkeypatch):
    monkeypatch.setattr(monthly, "load_official_table", lambda: (None, ("visitors fetch failed: offline",)))
    monkeypatch.setattr(monthly, "load_guest_nights", lambda: (pd.DataFrame(), "test"))
    forecast, scores, notes = monthly.build_monthly_forecast([monthly.SERIES[0]])
    assert forecast.empty and scores.empty
    assert any("every visitor series skipped" in n for n in notes)


def test_visitors_series_sums_codes_and_needs_all_of_them():
    table = pd.DataFrame({
        "month": [202501, 202502, 202501, 202502, 202501],
        "lgcode": [18442, 18442, 18501, 18501, 99999],
        "n": [10, 20, 1, 2, 5],
    })
    s = monthly.visitors_series(table, (18442, 18501))
    assert list(s) == [11, 22]
    assert str(s.index[0]) == "2025-01"
    assert monthly.visitors_series(table, (18442, 11111)).empty
