from datetime import datetime, timezone

import pandas as pd
import pytest
import requests

from dhde_preprocessing.sources import weather_live as wl

NOW = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)  # 12:00 JST


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def _hourly(times, precip, temp=15.0):
    n = len(times)
    return {"hourly": {"time": times, "temperature_2m": [temp] * n, "precipitation": precip,
                       "wind_speed_10m": [3.0] * n, "relative_humidity_2m": [70] * n, "weather_code": [61] * n}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(wl, "resolve_live_path", lambda p: str(tmp_path / "live" / p))
    monkeypatch.setattr(wl, "resolve_path", lambda p: str(tmp_path / "ws" / p))
    monkeypatch.setattr(wl, "FETCH_PAUSE_S", 0)
    monkeypatch.setattr(wl, "RETRY_PAUSE_S", 0)
    return tmp_path


def _cfg(weather=True):
    w = {"enabled": True, "prec_no": "57", "block_no": "1071", "page": "hourly_a1"} if weather else {"enabled": False}
    return {"node_key": "n", "coordinates": {"lat": 36.2, "lon": 136.1}, "sources": {"weather": w}}


def _serve(monkeypatch, payload=None, status=200, jma=None):
    calls = []

    def _get(url, params, timeout):
        calls.append(params)
        if isinstance(payload, Exception):
            raise payload
        return _Resp(payload, status)
    monkeypatch.setattr(wl.requests, "get", _get)
    def _day(p, b, page, day):
        calls.append(str(day))
        return (jma or {}).get(str(day), [])
    monkeypatch.setattr(wl.weather, "_fetch_day", _day)
    return calls


def _saved(env):
    return pd.read_csv(env / "live" / wl.HISTORY_DIR / "n.csv")


def _obs(ts, precip):
    return {"timestamp": ts, "temp_c": "14.0", "precip_1h_mm": str(precip), "wind_speed_ms": "2.0", "humidity_pct": "80"}


def test_no_coordinates_is_unavailable():
    df, report = wl.collect({"node_key": "n", "sources": {}})
    assert df is None and report.status == "unavailable"


def test_forecast_asks_the_jma_model_in_ms_and_saves_it(env, monkeypatch):
    calls = _serve(monkeypatch, _hourly(["2026-10-01T12:00", "2026-10-01T13:00"], [0.0, 9.5]))
    df, report = wl.collect(_cfg(weather=False), now=NOW)
    assert report.status == "ok"
    assert calls[0]["models"] == "jma_seamless" and calls[0]["wind_speed_unit"] == "ms"
    saved = _saved(env)
    assert list(saved["timestamp"]) == ["2026-10-01 12:00:00", "2026-10-01 13:00:00"]
    assert list(saved["source"]) == ["forecast", "forecast"] and saved["precip_1h_mm"].iloc[1] == 9.5
    assert saved["issued_at"].iloc[0] == "2026-10-01T03:00+00:00"


def test_an_observation_replaces_the_forecast_for_its_hour(env, monkeypatch):
    _serve(monkeypatch, _hourly(["2026-09-30 11:00", "2026-09-30 12:00"], [5.0, 6.0]),
           jma={"2026-09-30": [_obs("2026-09-30 11:00:00", 1.5)]})
    df, _ = wl.collect(_cfg(), now=NOW)
    rows = df.set_index(df["timestamp"].dt.strftime("%H:%M"))
    assert rows.loc["11:00", "source"] == "observed" and rows.loc["11:00", "precip_1h_mm"] == 1.5
    assert rows.loc["12:00", "source"] == "forecast"


def test_a_later_forecast_never_replaces_an_observation(env, monkeypatch):
    _serve(monkeypatch, _hourly(["2026-09-30 11:00"], [5.0]), jma={"2026-09-30": [_obs("2026-09-30 11:00:00", 1.5)]})
    wl.collect(_cfg(), now=NOW)
    # Next hour: JMA's page fails, the new forecast still covers 11:00.
    _serve(monkeypatch, _hourly(["2026-09-30 11:00", "2026-09-30 12:00"], [7.0, 8.0]))
    wl.collect(_cfg(), now=NOW.replace(hour=4))
    saved = _saved(env).set_index("timestamp")
    assert saved.loc["2026-09-30 11:00:00", "source"] == "observed"
    assert saved.loc["2026-09-30 11:00:00", "precip_1h_mm"] == 1.5


def test_a_newer_forecast_replaces_the_older_one(env, monkeypatch):
    _serve(monkeypatch, _hourly(["2026-10-02 09:00"], [1.0]))
    wl.collect(_cfg(weather=False), now=NOW)
    _serve(monkeypatch, _hourly(["2026-10-02 09:00"], [12.0]))
    wl.collect(_cfg(weather=False), now=NOW.replace(hour=4))
    saved = _saved(env)
    assert len(saved) == 1 and saved["precip_1h_mm"].iloc[0] == 12.0
    assert saved["issued_at"].iloc[0] == "2026-10-01T04:00+00:00"


def test_a_blank_jma_hour_is_not_an_observation(env, monkeypatch):
    blank = {"timestamp": "2026-09-30 12:00:00", "temp_c": "", "precip_1h_mm": "", "wind_speed_ms": "", "humidity_pct": ""}
    _serve(monkeypatch, _hourly(["2026-09-30 12:00"], [4.0]), jma={"2026-09-30": [blank]})
    df, _ = wl.collect(_cfg(), now=NOW)
    assert list(df["source"]) == ["forecast"]


@pytest.mark.parametrize("failure", [requests.ConnectionError(), 503])
def test_a_failed_forecast_keeps_the_saved_one(env, monkeypatch, failure):
    _serve(monkeypatch, _hourly(["2026-10-02 09:00"], [1.0]))
    wl.collect(_cfg(weather=False), now=NOW)
    if isinstance(failure, Exception):
        _serve(monkeypatch, failure)
    else:
        _serve(monkeypatch, {}, status=failure)
    df, report = wl.collect(_cfg(weather=False), now=NOW.replace(hour=4))
    assert report.status == "ok" and any("forecast failed" in n for n in report.notes)
    assert len(_saved(env)) == 1 and df["precip_1h_mm"].iloc[0] == 1.0


def test_nothing_fetched_and_nothing_saved_is_an_error(env, monkeypatch):
    _serve(monkeypatch, requests.ConnectionError())
    df, report = wl.collect(_cfg(), now=NOW)
    assert df is None and report.status == "error"


def _full_day(day):
    """JMA's 24 rows for `day`: 01:00 to 24:00 (00:00 the next day)."""
    start = pd.Timestamp(day)
    return [_obs(str(start + pd.Timedelta(hours=h)), 0.0) for h in range(1, 25)]


def test_a_complete_day_is_not_fetched_again(env, monkeypatch):
    jma = {"2026-09-28": _full_day("2026-09-28"), "2026-09-29": _full_day("2026-09-29"), "2026-09-30": _full_day("2026-09-30")[:10]}
    calls = _serve(monkeypatch, _hourly(["2026-10-01 12:00"], [0.0]), jma=jma)
    wl.collect(_cfg(), now=NOW)
    assert [c for c in calls if isinstance(c, str)] == ["2026-09-28", "2026-09-29", "2026-09-30"]
    # Next hour: 28 and 29 Sep are complete, 30 Sep (10 of 24 hours) is asked again.
    calls = _serve(monkeypatch, _hourly(["2026-10-01 12:00"], [0.0]), jma=jma)
    _, report = wl.collect(_cfg(), now=NOW.replace(hour=4))
    assert [c for c in calls if isinstance(c, str)] == ["2026-09-30"]
    assert any("fetched 1 of the last 3 days" in n for n in report.notes)


def test_jma_no_precip_dash_reads_as_zero():
    table = pd.DataFrame([["1", "--", "15.0", "80", "1.0"], ["2", "///", "15.0", "80", "1.0"], ["3", "0.5", "15.0", "80", "1.0"]],
                         columns=pd.MultiIndex.from_tuples([("時", ""), ("降水量 (mm)", ""), ("気温 (℃)", ""), ("湿度 (％)", ""), ("風向・風速(m/s)", "風速")]))
    rows = wl.weather._extract_rows(table, "hourly_s1")
    assert [r["precip_1h_mm"] for r in rows] == ["0.0", "", "0.5"]


def test_history_joins_the_jma_cache_and_the_collector_file(env, monkeypatch):
    cache = env / "ws" / wl.weather.CACHE_DIR
    cache.mkdir(parents=True)
    pd.DataFrame([{"timestamp": "2026-09-30 23:00:00", "temp_c": 13, "precip_1h_mm": 0.5, "wind_speed_ms": 1.0,
                   "humidity_pct": 90, "snow_depth_cm": None, "snowfall_1h_cm": None, "sun_1h_h": None, "weather_type": ""},
                  {"timestamp": "2026-10-01 00:00:00", "temp_c": 13, "precip_1h_mm": 0.0, "wind_speed_ms": 1.0,
                   "humidity_pct": 90, "snow_depth_cm": None, "snowfall_1h_cm": None, "sun_1h_h": None, "weather_type": ""}],
                 ).to_csv(cache / "n_hourly.csv", index=False)
    _serve(monkeypatch, _hourly(["2026-10-01 00:00", "2026-10-01 13:00"], [3.0, 2.0]))
    wl.collect(_cfg(weather=False), now=NOW)
    hist = wl.load_hourly_history("n")
    assert list(hist["timestamp"].dt.strftime("%m-%d %H")) == ["09-30 23", "10-01 00", "10-01 13"]
    # The cache's observation for 00:00 beats the collector's forecast for it.
    assert list(hist["source"]) == ["observed", "observed", "forecast"] and hist["precip_1h_mm"].iloc[1] == 0.0


def test_one_request_serves_every_node(env, monkeypatch):
    a, b = _cfg(weather=False), {**_cfg(weather=False), "node_key": "m", "coordinates": {"lat": 35.6, "lon": 135.9}}
    calls = _serve(monkeypatch, [_hourly(["2026-10-01 12:00"], [1.0]), _hourly(["2026-10-01 12:00"], [9.0])])
    fc = wl.fetch_forecasts([a, b, {"node_key": "x", "sources": {}}], now=NOW)
    assert len(calls) == 1 and calls[0]["latitude"] == "36.2,35.6" and calls[0]["longitude"] == "136.1,135.9"
    assert fc["n"]["precip_1h_mm"].iloc[0] == 1.0 and fc["m"]["precip_1h_mm"].iloc[0] == 9.0 and "x" not in fc
    calls.clear()
    df, _ = wl.collect(b, now=NOW, forecasts=fc)
    assert calls == [] and df["precip_1h_mm"].iloc[0] == 9.0


def test_a_timed_out_forecast_is_retried(env, monkeypatch):
    answers = [requests.ReadTimeout(), requests.ReadTimeout(), _Resp(_hourly(["2026-10-01 12:00"], [2.0]))]
    _serve(monkeypatch, None)

    def _get(url, params, timeout):
        r = answers.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(wl.requests, "get", _get)
    df, report = wl.collect(_cfg(weather=False), now=NOW)
    assert df["precip_1h_mm"].iloc[0] == 2.0 and not any("failed" in n for n in report.notes)


def test_a_failed_batch_falls_back_to_one_request_per_node(env, monkeypatch):
    _serve(monkeypatch, [_hourly(["2026-10-01 12:00"], [1.0])])  # 1 point back for 2 asked
    two = [_cfg(weather=False), {**_cfg(weather=False), "node_key": "m"}]
    assert wl.fetch_forecasts(two, now=NOW) == {}
    _serve(monkeypatch, _hourly(["2026-10-01 12:00"], [3.0]))
    df, _ = wl.collect(two[1], now=NOW, forecasts={})
    assert df["precip_1h_mm"].iloc[0] == 3.0
