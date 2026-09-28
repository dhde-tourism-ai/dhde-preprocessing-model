import numpy as np
import pandas as pd

from dhde_preprocessing.calibration_check import compare, monthly_count

MONTHS = pd.period_range("2025-01", periods=12, freq="M")


def test_same_seasonal_shape_means_one_factor_fits():
    town = pd.Series(np.linspace(100, 300, 12), index=MONTHS)
    out = compare(town * 5, town)
    assert out["corr"] == 1.0 and out["ratio_spread"] == 0.0


def test_a_different_seasonal_shape_shows_up_as_spread():
    town = pd.Series(100.0, index=MONTHS)
    site = pd.Series([10.0] * 6 + [50.0] * 6, index=MONTHS)  # site peaks in summer, town is flat
    assert compare(site, town)["ratio_spread"] > 0.5


def test_outage_days_dont_shrink_a_month():
    days = pd.date_range("2025-01-01", "2025-01-31")
    y = pd.Series(10.0, index=days)
    y.iloc[:5] = np.nan  # 26 measured days
    assert monthly_count(y).iloc[0] == 310  # 10/day x 31 days, not 260
    y.iloc[:15] = np.nan  # 16 measured days: too few to trust
    assert monthly_count(y).empty
