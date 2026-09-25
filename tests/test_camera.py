import pandas as pd
import pytest

from dhde_preprocessing.sources import camera


def _write_person_csv(path, rows):
    pd.DataFrame(rows, columns=["placement", "object class", "aggregate from", "aggregate to", "total count"]).to_csv(path, index=False)


def _write_face_csv(path, rows):
    pd.DataFrame(
        rows,
        columns=["placement", "object class", "aggregate from", "aggregate to", "total count",
                 "male range00to05", "female range00to05"],
    ).to_csv(path, index=False)


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(camera, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def test_single_gate_person_csv(patch_resolve_path):
    tmp_path = patch_resolve_path
    _write_person_csv(tmp_path / "person.csv", [
        ["tojinbo-shotaro", "Person", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 3778],
        ["tojinbo-shotaro", "Person", "2024-12-21 00:00:00", "2024-12-22 00:00:00", 7411],
    ])
    node_cfg = {
        "node_key": "tojinbo",
        "sources": {"camera": {"enabled": True, "gates": [
            {"name": "tojinbo", "person_csv": "person.csv"},
        ]}},
    }
    df, report = camera.load_camera(node_cfg)
    assert report.status == "ok"
    assert list(df["count"]) == [3778, 7411]
    assert "gate1_count" not in df.columns  # single gate: no prefix


def test_multi_gate_uses_prefix(patch_resolve_path):
    tmp_path = patch_resolve_path
    _write_person_csv(tmp_path / "g1.csv", [["g1", "Person", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 10]])
    _write_person_csv(tmp_path / "g2.csv", [["g2", "Person", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 20]])
    node_cfg = {
        "node_key": "rainbow_line",
        "sources": {"camera": {"enabled": True, "gates": [
            {"name": "gate1", "person_csv": "g1.csv"},
            {"name": "gate2", "person_csv": "g2.csv"},
        ]}},
    }
    df, report = camera.load_camera(node_cfg)
    assert report.status == "ok"
    assert df.loc[0, "gate1_count"] == 10
    assert df.loc[0, "gate2_count"] == 20


def test_license_plate_produces_vehicle_count_not_count(patch_resolve_path):
    """The Rainbow Line gap: no Person.csv, only LicensePlate.csv — must
    never be silently treated as a person count. Two gates (matching the
    real rainbow_line.yaml config) so the gate-name prefix applies."""
    tmp_path = patch_resolve_path
    _write_person_csv(tmp_path / "plates1.csv", [
        ["rainbow-line-parking-lot-1-gate", "LicensePlate", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 71],
    ])
    _write_person_csv(tmp_path / "plates2.csv", [
        ["rainbow-line-parking-lot-2-gate", "LicensePlate", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 30],
    ])
    node_cfg = {
        "node_key": "rainbow_line",
        "sources": {"camera": {"enabled": True, "gates": [
            {"name": "gate1", "license_plate_csv": "plates1.csv"},
            {"name": "gate2", "license_plate_csv": "plates2.csv"},
        ]}},
    }
    df, report = camera.load_camera(node_cfg)
    assert report.status == "ok"
    assert "gate1_vehicle_count" in df.columns
    assert "gate1_count" not in df.columns
    assert df.loc[0, "gate1_vehicle_count"] == 71
    assert df.loc[0, "gate2_vehicle_count"] == 30


def test_face_csv_columns_are_prefixed_and_date_not_clobbered(patch_resolve_path):
    """Regression test for the bug where the newly-added `date` column
    got swept into keep_cols and renamed away (face_date)."""
    tmp_path = patch_resolve_path
    _write_person_csv(tmp_path / "person.csv", [
        ["x", "Person", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 100],
    ])
    _write_face_csv(tmp_path / "face.csv", [
        ["x", "Face", "2024-12-20 00:00:00", "2024-12-21 00:00:00", 5, 1, 2],
    ])
    node_cfg = {
        "node_key": "tojinbo",
        "sources": {"camera": {"enabled": True, "gates": [
            {"name": "tojinbo", "person_csv": "person.csv", "face_csv": "face.csv"},
        ]}},
    }
    df, report = camera.load_camera(node_cfg)
    assert "date" in df.columns
    assert "face_date" not in df.columns
    assert df.loc[0, "face_male_range00to05"] == 1
    assert df.loc[0, "face_female_range00to05"] == 2


def test_disabled_camera_returns_unavailable():
    node_cfg = {"node_key": "katsuyama", "sources": {"camera": {"enabled": False, "reason": "no sensor"}}}
    df, report = camera.load_camera(node_cfg)
    assert df is None
    assert report.status == "unavailable"
