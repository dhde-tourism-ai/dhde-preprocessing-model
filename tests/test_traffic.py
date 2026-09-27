import pandas as pd
import pytest
import requests

from dhde_preprocessing.sources import traffic


@pytest.fixture(autouse=True)
def no_saved_history(monkeypatch, tmp_path):
    # Keep tests hermetic: saved history is read from an empty temp dir
    # unless a test writes one there.
    monkeypatch.setattr(traffic, "resolve_live_path", lambda p: str(tmp_path / p))
    return tmp_path


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _feature(time_code, up_small=1, up_large=0, down_small=1, down_large=0):
    return {
        "properties": {
            "時間コード": time_code,
            "上り・小型交通量": up_small, "上り・大型交通量": up_large,
            "下り・小型交通量": down_small, "下り・大型交通量": down_large,
        }
    }


def test_disabled_traffic_returns_unavailable():
    node_cfg = {"node_key": "tojinbo", "sources": {"traffic": {"enabled": False, "reason": "too far"}}}
    df, report = traffic.load_traffic(node_cfg)
    assert df is None
    assert report.status == "unavailable"


def test_zero_features_is_reported_as_error(monkeypatch):
    monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: _FakeResponse({"features": []}))
    node_cfg = {"node_key": "x", "sources": {"traffic": {
        "enabled": True, "point_code": 123, "distance_km": 2.0, "window_days": 7,
    }}}
    df, report = traffic.load_traffic(node_cfg)
    assert df is None
    assert report.status == "error"


def test_api_failure_is_reported_as_error_not_raised(monkeypatch):
    def _raise(*a, **k):
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(traffic.requests, "get", _raise)
    node_cfg = {"node_key": "x", "sources": {"traffic": {
        "enabled": True, "point_code": 123, "distance_km": 2.0,
    }}}
    df, report = traffic.load_traffic(node_cfg)
    assert df is None
    assert report.status == "error"


def test_aggregates_hourly_features_to_daily_volume(monkeypatch):
    features = [
        _feature("202401010800", up_small=5, up_large=2, down_small=3, down_large=1),
        _feature("202401010900", up_small=4, up_large=1, down_small=2, down_large=0),
        _feature("202401020800", up_small=10, up_large=0, down_small=5, down_large=0),
    ]
    monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: _FakeResponse({"features": features}))
    node_cfg = {"node_key": "x", "sources": {"traffic": {
        "enabled": True, "point_code": 123, "distance_km": 2.0,
    }}}
    df, report = traffic.load_traffic(node_cfg)
    assert report.status == "ok"
    df = df.set_index("date")
    day1 = df.loc["2024-01-01"]
    assert day1["volume_total"] == (5 + 2 + 3 + 1) + (4 + 1 + 2 + 0)
    assert day1["hours_observed"] == 2
    day2 = df.loc["2024-01-02"]
    assert day2["volume_total"] == 15


def test_layer_is_configurable_and_defaults_to_cctv(monkeypatch):
    # Permanent counters live on a different JARTIC layer with the same
    # property names; the node config picks which one to query.
    seen = []

    def _get(url, params, timeout):
        seen.append(params["typeNames"])
        return _FakeResponse({"features": [_feature("202401010800")]})

    monkeypatch.setattr(traffic.requests, "get", _get)
    base = {"enabled": True, "point_code": 123, "distance_km": 2.0}
    traffic.load_traffic({"node_key": "x", "sources": {"traffic": dict(base)}})
    traffic.load_traffic({"node_key": "x", "sources": {"traffic": dict(base, layer=traffic.PERMANENT_LAYER)}})
    assert seen == [traffic.LAYER, traffic.PERMANENT_LAYER]


def _write_history(root, rows):
    d = root / traffic.HISTORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["date", "volume_total", "volume_upstream", "volume_downstream", "hours_observed"]) \
        .to_csv(d / "x_traffic_daily.csv", index=False)


def test_merge_history_keeps_old_rows_and_newer_pull_wins():
    # JARTIC only returns ~90 days, so old rows must survive a new pull,
    # and a partial latest day must be replaced by the next pull's value.
    existing = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "volume_total": [100, 40]})
    new = pd.DataFrame({"date": ["2026-01-02", "2026-01-03"], "volume_total": [90, 120]})
    merged = traffic.merge_history(existing, new)
    assert list(merged["date"].dt.strftime("%Y-%m-%d")) == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert list(merged["volume_total"]) == [100, 90, 120]


def test_build_reads_saved_history(monkeypatch, no_saved_history):
    # Regression: history saved by scripts/collect_live.py was written but
    # never read by the build.
    _write_history(no_saved_history, [["2023-06-01", 500, 250, 250, 24]])
    monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: _FakeResponse({"features": [_feature("202401010800")]}))
    node_cfg = {"node_key": "x", "sources": {"traffic": {"enabled": True, "point_code": 123, "distance_km": 2.0}}}
    df, report = traffic.load_traffic(node_cfg)
    assert report.status == "ok"
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2023-06-01", "2024-01-01"]


def test_api_failure_falls_back_to_saved_history(monkeypatch, no_saved_history):
    _write_history(no_saved_history, [["2023-06-01", 500, 250, 250, 24]])
    def _raise(*a, **k):
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(traffic.requests, "get", _raise)
    node_cfg = {"node_key": "x", "sources": {"traffic": {"enabled": True, "point_code": 123, "distance_km": 2.0}}}
    df, report = traffic.load_traffic(node_cfg)
    assert report.status == "ok" and len(df) == 1
    assert any("saved history only" in n for n in report.notes)


def test_merge_history_with_nothing_to_merge_returns_empty():
    assert traffic.merge_history(pd.DataFrame(columns=["date"]), None).empty


@pytest.mark.parametrize("content", ["", "date,volume_total\n"])
def test_empty_saved_history_file_is_ignored(monkeypatch, no_saved_history, content):
    d = no_saved_history / traffic.HISTORY_DIR
    d.mkdir(parents=True)
    (d / "x_traffic_daily.csv").write_text(content, encoding="utf-8")
    monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: _FakeResponse({"features": [_feature("202401010800")]}))
    node_cfg = {"node_key": "x", "sources": {"traffic": {"enabled": True, "point_code": 123, "distance_km": 2.0}}}
    df, report = traffic.load_traffic(node_cfg)
    assert report.status == "ok" and len(df) == 1


def test_history_path_is_not_mangled_for_s3(monkeypatch):
    # Path("s3://b/x") collapses to "s3:/b/x"; the loader must pass the string through.
    seen = []

    def _read(path, **kwargs):
        seen.append(path)
        return None

    monkeypatch.setattr(traffic, "resolve_live_path", lambda p: f"s3://bucket/live/{p}")
    monkeypatch.setattr(traffic, "read_csv_if_exists", _read)
    monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: _FakeResponse({"features": [_feature("202401010800")]}))
    traffic.load_traffic({"node_key": "x", "sources": {"traffic": {"enabled": True, "point_code": 1, "distance_km": 1}}})
    assert seen == ["s3://bucket/live/jartic_history/x_traffic_daily.csv"]
