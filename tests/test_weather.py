import pandas as pd
import pytest

from dhde_preprocessing.sources import weather


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(weather, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def test_no_input_csv_yet_returns_unavailable_with_todo_note():
    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "station_name": "三国 (Mikuni)", "station_id": "57001", "input_csv": None,
    }}}
    df, report = weather.load_weather(node_cfg)
    assert df is None
    assert report.status == "unavailable"
    assert any("obsdl" in n for n in report.notes)


def test_disabled_weather_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"weather": {"enabled": False, "reason": "nope"}}}
    df, report = weather.load_weather(node_cfg)
    assert df is None
    assert report.status == "unavailable"


def test_malformed_export_reports_error_not_crash(patch_resolve_path):
    """The obsdl parser is explicitly unverified against a real export —
    a bad/unexpected file must surface as an error report, not blow up
    the whole pipeline run."""
    tmp_path = patch_resolve_path
    bad_file = tmp_path / "export.csv"
    bad_file.write_text("not,a,real,export\njust,some,random,text", encoding="utf-8")

    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "station_name": "三国 (Mikuni)", "station_id": "57001",
        "input_csv": "export.csv",
    }}}
    df, report = weather.load_weather(node_cfg)
    assert report.status == "error"
    assert "obsdl" in " ".join(report.notes)


def test_parses_minimal_assumed_shape(patch_resolve_path):
    """Covers the CURRENT assumed obsdl shape (date in col 0, temp in
    col 1, 5 header rows) — update this test alongside _parse_obsdl_csv
    once verified against a real export."""
    tmp_path = patch_resolve_path
    lines = ["header"] * 5 + ["2024-01-01,5.2,extra,cols", "2024-01-02,6.1,extra,cols"]
    (tmp_path / "export.csv").write_text("\n".join(lines), encoding="utf-8")

    node_cfg = {"node_key": "tojinbo", "sources": {"weather": {
        "enabled": True, "station_name": "三国 (Mikuni)", "station_id": "57001",
        "input_csv": "export.csv",
    }}}
    df, report = weather.load_weather(node_cfg)
    assert report.status == "ok"
    assert list(df["temp_c"]) == [5.2, 6.1]
