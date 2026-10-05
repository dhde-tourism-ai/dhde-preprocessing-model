from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import requests

from dhde_preprocessing.sources import weather_ahead as wa


def _body(start, days, rain=0.5, temp=20.0, gaps=()):
    """Open-Meteo Previous Runs answer: every lead the same, except `gaps` = (lead, hour index) set to None."""
    times = pd.date_range(start, periods=24 * days, freq="h")
    hourly = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
    for n in wa.LEADS:
        hourly[f"precipitation_previous_day{n}"] = [rain] * len(times)
        hourly[f"temperature_2m_previous_day{n}"] = [temp + t.hour / 100 for t in times]
    for lead, i in gaps:
        hourly[f"precipitation_previous_day{lead}"][i] = None
    return {"hourly": hourly}


def test_daily_sums_daytime_rain_and_leaves_a_day_with_a_missing_hour_blank():
    body = _body("2026-09-01", 2, gaps=[(3, 12)])  # lead 3, 12:00 on the first day
    body["hourly"]["precipitation_previous_day1"][13] = -0.1  # archived noise: not negative rain
    d = wa.daily(body).set_index("date")
    assert list(d.index) == list(pd.to_datetime(["2026-09-01", "2026-09-02"]))
    assert d.loc["2026-09-02", "rain_mm_lead2"] == 0.5 * len(wa.DAY_HOURS)  # night hours left out
    assert d.loc["2026-09-01", "rain_mm_lead1"] == 0.5 * (len(wa.DAY_HOURS) - 1)
    assert np.isnan(d.loc["2026-09-01", "rain_mm_lead3"]) and d.loc["2026-09-02", "rain_mm_lead3"] > 0
    assert d.loc["2026-09-01", "temp_max_lead5"] == 20.18  # the 18:00 hour


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(wa, "resolve_path", lambda p: str(tmp_path / p))
    monkeypatch.setattr(wa, "FIRST_DAY", date(2026, 8, 1))
    monkeypatch.setattr(wa, "RETRY_PAUSE_S", 0)

    class Today(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 9, 0, tzinfo=tz)

    monkeypatch.setattr(wa, "datetime", Today)
    calls = []

    def get(lat, lon, start, end):
        calls.append((start, end))
        return _body(start.isoformat(), (end - start).days + 1)

    monkeypatch.setattr(wa, "_get", get)
    return calls


CFG = {"node_key": "n", "coordinates": {"lat": 36.2, "lon": 136.1}}


def test_load_fetches_once_then_only_the_days_that_can_still_change(env):
    first = wa.load(CFG)
    assert env == [(date(2026, 8, 1), date(2026, 10, 12))]  # to 7 days after today
    assert len(first) == 73 and first[wa.COLUMNS].notna().all().all()
    second = wa.load(CFG)
    assert env[1] == (date(2026, 9, 22), date(2026, 10, 12))  # the last RECHECK_DAYS days and the week ahead
    pd.testing.assert_frame_equal(first, second)


def test_a_failed_fetch_keeps_the_cache(env, monkeypatch):
    first = wa.load(CFG)

    def down(*a):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(wa, "_get", down)
    pd.testing.assert_frame_equal(wa.load(CFG), first)
    assert wa.load({"node_key": "n"}).equals(first)  # no coordinates: the cache, no request
