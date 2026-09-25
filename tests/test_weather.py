import pandas as pd
import pytest

from dhde_preprocessing.sources import weather

# A minimal but real-shaped hourly_a1 (AMeDAS) HTML table, two hours, one
# column set matching what JMA actually serves — enough for _extract_rows
# to find precip/temp/humidity/wind/sun/snow columns by keyword.
AMEDAS_HTML = """
<table>
<tr><th></th><th>降水量(mm)</th><th>気温(℃)</th><th colspan="2">風向・風速(m/s)</th><th>日照時間(h)</th><th colspan="2">積雪(cm)</th><th>湿度(％)</th></tr>
<tr><th>時</th><th></th><th></th><th>風速</th><th>風向</th><th></th><th>降雪</th><th>積雪</th><th></th></tr>
<tr><td>1</td><td>0.0</td><td>23.1</td><td>1.3</td><td>北西</td><td>--</td><td>--</td><td>--</td><td>96</td></tr>
<tr><td>2</td><td>0.5</td><td>22.9</td><td>1.8</td><td>北</td><td>--</td><td>--</td><td>--</td><td>95</td></tr>
</table>
""" + "<table>" + "<tr><td>x</td>" * 25 + "</tr>" * 20 + "</table>"  # padding table >=20 rows to mimic real page noise


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(weather, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def test_disabled_weather_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"weather": {"enabled": False, "reason": "nope"}}}
    df, report = weather.load_weather(node_cfg)
    assert df is None
    assert report.status == "unavailable"


def test_every_day_failing_reports_error_not_crash(monkeypatch, patch_resolve_path):
    """Every single day failing (e.g. a real site outage) must still
    surface as a clean error report, not an unhandled exception."""
    def _raise(*a, **k):
        raise RuntimeError("network boom")
    monkeypatch.setattr(weather, "_fetch_day", _raise)
    monkeypatch.setattr(weather.time, "sleep", lambda *a: None)  # keep the test fast

    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "prec_no": "57", "block_no": "1071", "page": "hourly_a1",
        "start_date": "2024-01-01",
    }}}
    df, report = weather.load_weather(node_cfg)
    assert df is None
    assert report.status == "error"


def test_one_bad_day_does_not_lose_the_rest_of_the_run(monkeypatch, patch_resolve_path):
    """Regression test: a single failing day (a transient site hiccup, a
    genuinely missing observation) must not throw away every other day
    already fetched successfully in the same run."""
    import datetime

    def _flaky_fetch_day(prec_no, block_no, page, day):
        if day == datetime.date(2024, 1, 2):
            raise RuntimeError("simulated one-off failure")
        return [{"hour": 1, "precip_1h_mm": "0.0", "temp_c": "6.0", "humidity_pct": "70",
                  "wind_speed_ms": "1.0", "sun_1h_h": "", "snowfall_1h_cm": "", "snow_depth_cm": "",
                  "weather_type": "", "timestamp": f"{day.isoformat()} 01:00:00"}]

    monkeypatch.setattr(weather, "_fetch_day", _flaky_fetch_day)
    monkeypatch.setattr(weather.time, "sleep", lambda *a: None)

    class _FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2024, 1, 3)

    monkeypatch.setattr(weather, "date", _FixedDate)

    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "prec_no": "57", "block_no": "1071", "page": "hourly_a1",
        "start_date": "2024-01-01",
    }}}
    df, report = weather.load_weather(node_cfg)
    assert report.status == "ok"
    # 2024-01-01 and 2024-01-03 fetched fine; only 2024-01-02 failed.
    assert set(df["date"].dt.date) == {datetime.date(2024, 1, 1), datetime.date(2024, 1, 3)}


def test_uses_cache_and_only_fetches_missing_days(monkeypatch, patch_resolve_path):
    """Regression test for the caching design: a day already in the cache
    file must not trigger a re-fetch."""
    tmp_path = patch_resolve_path
    cache_dir = tmp_path / "jma_cache"
    cache_dir.mkdir()
    pd.DataFrame({
        "timestamp": ["2024-01-01 01:00:00", "2024-01-01 02:00:00"],
        "snow_depth_cm": ["", ""], "snowfall_1h_cm": ["", ""],
        "temp_c": [5.0, 5.5], "precip_1h_mm": [0.0, 0.0],
        "sun_1h_h": ["", ""], "wind_speed_ms": [1.0, 1.2],
        "weather_type": ["", ""], "humidity_pct": [80, 81],
    }).to_csv(cache_dir / "tojinbo_hourly.csv", index=False)

    fetch_calls = []

    def _fake_fetch_day(prec_no, block_no, page, day):
        fetch_calls.append(day)
        return [{"hour": 1, "precip_1h_mm": "0.0", "temp_c": "6.0", "humidity_pct": "70",
                  "wind_speed_ms": "1.0", "sun_1h_h": "", "snowfall_1h_cm": "", "snow_depth_cm": "",
                  "weather_type": "", "timestamp": f"{day.isoformat()} 01:00:00"}]

    monkeypatch.setattr(weather, "_fetch_day", _fake_fetch_day)

    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "prec_no": "57", "block_no": "1071", "page": "hourly_a1",
        "start_date": "2024-01-01",
    }}}

    # Freeze "today" so the missing-day range is small and deterministic.
    import datetime

    class _FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2024, 1, 2)

    monkeypatch.setattr(weather, "date", _FixedDate)

    df, report = weather.load_weather(node_cfg)
    assert report.status == "ok"
    # 2024-01-01 was already cached (2 rows) — only 2024-01-02 should have been fetched.
    assert fetch_calls == [datetime.date(2024, 1, 2)]
    df = df.set_index("date")
    assert df.loc[pd.Timestamp("2024-01-01"), "temp"] == 5.25  # mean of cached 5.0, 5.5
    assert df.loc[pd.Timestamp("2024-01-02"), "temp"] == 6.0


def test_extract_rows_parses_real_shaped_amedas_table():
    tables = pd.read_html(pd.io.common.StringIO(AMEDAS_HTML))
    data_table = tables[0]
    rows = weather._extract_rows(data_table, "hourly_a1")
    assert len(rows) == 2
    assert rows[0]["hour"] == 1
    assert rows[0]["temp_c"] == "23.1"
    assert rows[0]["humidity_pct"] == "96"
    assert rows[1]["precip_1h_mm"] == "0.5"


def test_clean_cell_treats_jma_missing_markers_as_empty():
    vals = ["--", "///", "×", "5.2"]
    assert weather._clean_cell(0, vals) == ""
    assert weather._clean_cell(1, vals) == ""
    assert weather._clean_cell(2, vals) == ""
    assert weather._clean_cell(3, vals) == "5.2"
