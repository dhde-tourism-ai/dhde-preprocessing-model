import pandas as pd
import pytest

from dhde_preprocessing.sources import footfall_proxy as fp


def _node(key, lat, lon, camera=None, proxy=None):
    sources = {
        "camera": camera or {"enabled": False},
        "survey": {"enabled": True, "repo": "survey"},
    }
    if proxy is not None:
        sources["footfall_proxy"] = proxy
    return {"node_key": key, "coordinates": {"lat": lat, "lon": lon}, "sources": sources}


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "resolve_path", lambda p: str(tmp_path / p))
    (tmp_path / "survey").mkdir()
    pd.DataFrame(
        [["near_area", 36.001, 136.001], ["other_near", 36.01, 136.0], ["far_area", 37.0, 137.0]],
        columns=["エリア名", "緯度", "経度"],
    ).to_csv(tmp_path / "survey" / "area.csv", index=False)
    pd.DataFrame(
        [["2025-01-01 10:00:00", "near_area"], ["2025-01-01 11:00:00", "other_near"],
         ["2025-01-02 09:00:00", "near_area"], ["2025-01-01 09:00:00", "far_area"]],
        columns=["回答日時", "回答エリア"],
    ).to_csv(tmp_path / "survey" / "all.csv", index=False)
    pd.DataFrame(
        [["s", "Person", "2025-01-01 00:00:00", "2025-01-02 00:00:00", 100],
         ["s", "Person", "2025-01-02 00:00:00", "2025-01-03 00:00:00", 200]],
        columns=["placement", "object class", "aggregate from", "aggregate to", "total count"],
    ).to_csv(tmp_path / "person.csv", index=False)
    return tmp_path


def test_not_configured_is_unavailable():
    df, report = fp.load_footfall_proxy(_node("x", 36, 136), all_node_cfgs=[])
    assert df is None
    assert report.status == "unavailable"


def test_uses_nearest_person_camera_and_pools_nearby_survey_areas(workspace):
    here = _node("here", 36.0, 136.0, proxy={"enabled": True, "radii_km": [5, 15]})
    cam_node = _node("cam", 36.05, 136.05, camera={"enabled": True, "gates": [{"name": "g", "person_csv": "person.csv"}]})
    df, report = fp.load_footfall_proxy(here, all_node_cfgs=[here, cam_node])
    assert report.status == "ok"
    df = df.set_index("date")
    assert df.loc["2025-01-01", "proxy_camera_count"] == 100
    # Both areas inside 5km are pooled; the far one is not.
    assert df.loc["2025-01-01", "proxy_survey_count"] == 2
    assert df.loc["2025-01-02", "proxy_survey_count"] == 1
    assert any("cam camera" in n and "5km circle" in n for n in report.notes)


def test_vehicle_only_camera_is_never_a_people_proxy(workspace):
    # Regression guard: Rainbow Line's gates are LicensePlate.csv (vehicles).
    here = _node("here", 36.0, 136.0, proxy={"enabled": True, "radii_km": [5]})
    vehicles = _node("rl", 36.01, 136.01, camera={"enabled": True, "gates": [{"name": "g", "license_plate_csv": "lp.csv"}]})
    df, report = fp.load_footfall_proxy(here, all_node_cfgs=[here, vehicles])
    assert "proxy_camera_count" not in df.columns
    assert any("no Person.csv camera" in n for n in report.notes)


def test_widens_circle_until_a_camera_is_found(workspace):
    here = _node("here", 36.0, 136.0, proxy={"enabled": True, "radii_km": [5, 15, 30]})
    cam_node = _node("cam", 36.1, 136.0, camera={"enabled": True, "gates": [{"name": "g", "person_csv": "person.csv"}]})  # ~11km
    df, report = fp.load_footfall_proxy(here, all_node_cfgs=[here, cam_node])
    assert "proxy_camera_count" in df.columns
    assert any("15km circle" in n for n in report.notes)
