import mapbox_vector_tile
import pytest
import requests

from dhde_preprocessing.sources import road_congestion as rc

LAT, LON, ZOOM = 36.0, 136.0, 14


class _Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status


def _tile_with_segment_near_node(rel_near, rel_far):
    """A tile whose centre segment sits at the node and a corner segment ~1.5km off."""
    fx, fy = rc._tile_frac(LAT, LON, ZOOM)
    x, y = int(fx), int(fy)
    # Node position in y-up tile coordinates (extent 4096).
    nx, ny = (fx - x) * 4096, (1 - (fy - y)) * 4096
    near = f"LINESTRING({nx - 5} {ny}, {nx} {ny}, {nx + 5} {ny})"
    far_x = 0 if nx > 2048 else 4090
    far = f"LINESTRING({far_x} {ny}, {far_x + 3} {ny}, {far_x + 6} {ny})"
    data = mapbox_vector_tile.encode([{"name": "Traffic flow", "features": [
        {"geometry": near, "properties": {"relative_speed": rel_near}},
        {"geometry": far, "properties": {"relative_speed": rel_far}},
    ]}])
    return (x, y), data


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "resolve_path", lambda p: str(tmp_path / p))
    monkeypatch.setenv(rc.KEY_ENV, "test-key")
    return tmp_path


def _cfg(radius_km=0.3):
    return {"node_key": "n", "coordinates": {"lat": LAT, "lon": LON},
            "sources": {"road_congestion": {"enabled": True, "radius_km": radius_km, "zoom": ZOOM}}}


def _serve(monkeypatch, tiles):
    def _get(url, params, timeout):
        z, x, y = url.rsplit("/", 3)[-3:]
        return _Resp(tiles.get((int(x), int(y[:-4])), mapbox_vector_tile.encode([])))
    monkeypatch.setattr(rc.requests, "get", _get)


def test_disabled_is_unavailable():
    df, report = rc.load_road_congestion({"node_key": "n", "sources": {}})
    assert df is None and report.status == "unavailable"


def test_averages_only_segments_inside_radius(env, monkeypatch):
    xy, data = _tile_with_segment_near_node(rel_near=0.6, rel_far=0.1)
    _serve(monkeypatch, {xy: data})
    df, report = rc.load_road_congestion(_cfg())
    assert report.status == "ok"
    row = df.iloc[0]
    assert row["road_relative_speed_mean"] == pytest.approx(0.6)   # far segment excluded
    assert row["road_congestion"] == pytest.approx(0.4)


def test_snapshots_accumulate_in_cache(env, monkeypatch):
    xy, data = _tile_with_segment_near_node(rel_near=0.8, rel_far=0.1)
    _serve(monkeypatch, {xy: data})
    rc.load_road_congestion(_cfg())
    df, _ = rc.load_road_congestion(_cfg())
    assert df.iloc[0]["road_snapshots"] == 2


def test_missing_key_makes_no_request_and_never_raises(env, monkeypatch):
    monkeypatch.delenv(rc.KEY_ENV)
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: pytest.fail("must not call the API without a key"))
    df, report = rc.load_road_congestion(_cfg())
    assert df is None and report.status == "error"
    assert any("not set" in n for n in report.notes)


def test_request_error_does_not_leak_the_key(env, monkeypatch):
    def _boom(*a, **k):
        raise requests.ConnectionError("https://api.tomtom.com/...?key=test-key")
    monkeypatch.setattr(rc.requests, "get", _boom)
    _, report = rc.load_road_congestion(_cfg())
    assert not any("test-key" in n for n in report.notes)
