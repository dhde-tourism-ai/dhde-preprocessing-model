import numpy as np
import pandas as pd

from dhde_preprocessing import pickup as pk


def _curves(final: pd.Series, pickup_after: callable) -> pd.DataFrame:
    """Occupancy by night x lead where the pickup after lead L is pickup_after(L)."""
    return pd.DataFrame({lead: final - pickup_after(lead) for lead in range(90)}, index=final.index)


def _flat(start="2024-01-01", end="2025-12-31", level=0.6) -> pd.Series:
    nights = pd.date_range(start, end)
    return pd.Series(level, index=nights)


def test_night_types():
    t = pk.night_type(pd.DatetimeIndex([
        "2025-10-04",  # Saturday
        "2025-10-12",  # Sunday before Sports Day (Mon 13th)
        "2025-10-13",  # Sports Day, a working day follows: like a Sunday
        "2025-10-15",  # plain Wednesday
        "2025-08-14",  # Obon
        "2025-05-03",  # Golden Week
        "2025-12-31",  # New Year
    ]))
    assert list(t) == ["before_off", "before_off", "Sun", "Wed", "peak", "peak", "peak"]


def test_steady_pickup_is_forecast_exactly():
    final = _flat()
    occ = _curves(final, lambda lead: 0.003 * lead)
    bt = pk.backtest(occ, final, leads=(7, 30))
    late = bt[bt["night"] >= "2025-06-01"]
    for m in pk.METHODS:
        assert np.allclose(late[m], late["final"]), m
    assert np.allclose(late["otb"], late["final"] - 0.003 * late["lead"])


def test_only_nights_ended_by_the_forecast_date_are_used():
    final = _flat()
    occ = _curves(final, lambda lead: 0.1)
    night = pd.Timestamp("2025-06-18")
    before = pk.forecast_night(night, 30, occ, final, pk.night_type(final.index))
    # Change what happened to every night after the forecast date: nothing moves.
    later = final.index > night - pd.Timedelta(days=30)
    final2 = final.copy()
    final2[later & (final.index != night)] = 0.95
    occ2 = _curves(final2, lambda lead: 0.1)
    occ2.loc[night] = occ.loc[night]
    after = pk.forecast_night(night, 30, occ2, final2, pk.night_type(final.index))
    assert before == after


def test_cancellations_keep_their_sign_and_the_forecast_is_capped():
    final = _flat(level=0.5)
    occ = _curves(final, lambda lead: -0.05)  # more booked at lead 30 than in the end
    f = pk.forecast_night(pd.Timestamp("2025-06-18"), 30, occ, final, pk.night_type(final.index))
    assert np.isclose(f["recent"], 0.5)

    full = _flat(level=0.98)
    occ = _curves(full, lambda lead: 0.1)
    occ.loc[pd.Timestamp("2025-06-18")] = 0.97  # this night is ahead of pace
    f = pk.forecast_night(pd.Timestamp("2025-06-18"), 30, occ, full, pk.night_type(full.index))
    assert f["recent"] == 1.0


def test_peak_nights_use_last_years_peak():
    final = _flat()
    md = final.index.month * 100 + final.index.day
    obon = (md >= 813) & (md <= 816)
    final[obon] = 0.9
    occ = _curves(final, lambda lead: 0.1)
    occ.loc[obon] = (final[obon] - 0.4).to_numpy()[:, None]  # Obon books late
    f = pk.forecast_night(pd.Timestamp("2025-08-14"), 30, occ, final, pk.night_type(final.index))
    assert np.isclose(f["recent"], 0.9) and np.isclose(f["blend"], 0.9)


def test_score_reports_skill_against_the_baselines():
    final = _flat()
    occ = _curves(final, lambda lead: 0.003 * lead)
    s = pk.score(pk.backtest(occ, final, leads=(30,)), pd.Timestamp("2025-10-01")).set_index("method")
    assert s.loc["blend", "mae_pp"] == 0 and s.loc["blend", "skill_vs_otb"] == 1
    assert np.isclose(s.loc["otb", "bias_pp"], -9.0)
