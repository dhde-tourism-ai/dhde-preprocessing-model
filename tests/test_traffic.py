import requests

from dhde_preprocessing.sources import traffic


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
