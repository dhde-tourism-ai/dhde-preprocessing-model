import pandas as pd
import pytest

from dhde_preprocessing.sources import rsi


def _write_metrics_csv(path, dates, directions):
    pd.DataFrame({
        "date": dates,
        "map_views": [0] * len(dates), "search_views": [0] * len(dates),
        "directions": directions, "call_clicks": [0] * len(dates), "website_clicks": [0] * len(dates),
        "average_rating": [0] * len(dates), "review_count_change": [0] * len(dates),
        "review_count_by_rating_1": [0] * len(dates), "review_count_by_rating_2": [0] * len(dates),
        "review_count_by_rating_3": [0] * len(dates), "review_count_by_rating_4": [0] * len(dates),
        "review_count_by_rating_5": [0] * len(dates),
    }).to_csv(path, index=False)


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(rsi, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def test_area_file_used_where_available_total_fills_gaps(patch_resolve_path):
    tmp_path = patch_resolve_path
    (tmp_path / "trend/2024").mkdir(parents=True)
    (tmp_path / "trend/2025").mkdir(parents=True)
    # 2024: only total (area not tracked yet)
    _write_metrics_csv(tmp_path / "trend/2024/total_daily_metrics.csv", ["2024-01-01", "2024-01-02"], [100, 200])
    # 2025: both total and area exist
    _write_metrics_csv(tmp_path / "trend/2025/total_daily_metrics.csv", ["2025-01-01"], [999])
    _write_metrics_csv(tmp_path / "trend/2025/area_坂井市_abcd_daily_metrics.csv", ["2025-01-01"], [50])

    node_cfg = {"node_key": "tojinbo", "sources": {"rsi": {
        "enabled": True, "repo": "trend", "area_name": "坂井市",
    }}}
    df, report = rsi.load_rsi(node_cfg)
    assert report.status == "ok"
    df = df.set_index("date")
    # 2024 dates have no area file -> falls back to total
    assert df.loc[pd.Timestamp("2024-01-01"), "directions"] == 100
    # 2025-01-01 has an area value -> area wins over total
    assert df.loc[pd.Timestamp("2025-01-01"), "directions"] == 50


def test_no_area_name_uses_total_only(patch_resolve_path):
    tmp_path = patch_resolve_path
    (tmp_path / "trend/2024").mkdir(parents=True)
    _write_metrics_csv(tmp_path / "trend/2024/total_daily_metrics.csv", ["2024-01-01"], [100])

    node_cfg = {"node_key": "fukui_station", "sources": {"rsi": {
        "enabled": True, "repo": "trend", "area_name": None,
    }}}
    df, report = rsi.load_rsi(node_cfg)
    assert report.status == "ok"
    assert df.loc[0, "directions"] == 100
    assert any("no matching municipality" in n for n in report.notes)


def test_disabled_rsi_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"rsi": {"enabled": False, "reason": "nope"}}}
    df, report = rsi.load_rsi(node_cfg)
    assert df is None
    assert report.status == "unavailable"


def test_rsi_level_says_where_each_value_comes_from(patch_resolve_path):
    tmp_path = patch_resolve_path
    (tmp_path / "trend/2025").mkdir(parents=True)
    _write_metrics_csv(tmp_path / "trend/2025/total_daily_metrics.csv", ["2025-01-01", "2025-01-02"], [999, 998])
    _write_metrics_csv(tmp_path / "trend/2025/area_坂井市_abcd_daily_metrics.csv", ["2025-01-02"], [50])
    df, _ = rsi.load_rsi({"node_key": "tojinbo", "sources": {"rsi": {
        "enabled": True, "repo": "trend", "area_name": "坂井市"}}})
    assert list(df["rsi_level"]) == ["prefecture", "area"]


def test_zero_days_before_tracking_starts_are_missing(patch_resolve_path):
    """永平寺町's file reads 0 on every metric until 2026-06-05: not tracked
    yet, not zero searches. Those days fall back to the total instead."""
    tmp_path = patch_resolve_path
    (tmp_path / "trend/2026").mkdir(parents=True)
    _write_metrics_csv(tmp_path / "trend/2026/total_daily_metrics.csv",
                       ["2026-06-03", "2026-06-04", "2026-06-05"], [0, 500, 600])
    _write_metrics_csv(tmp_path / "trend/2026/area_永平寺町_abcd_daily_metrics.csv",
                       ["2026-06-03", "2026-06-04", "2026-06-05"], [0, 0, 7])
    df, _ = rsi.load_rsi({"node_key": "eiheiji", "sources": {"rsi": {
        "enabled": True, "repo": "trend", "area_name": "永平寺町"}}})
    df = df.set_index("date")
    assert pd.isna(df.loc[pd.Timestamp("2026-06-03"), "directions"])  # neither file tracked yet
    assert df.loc[pd.Timestamp("2026-06-04"), "directions"] == 500
    assert df.loc[pd.Timestamp("2026-06-04"), "rsi_level"] == "prefecture"
    assert df.loc[pd.Timestamp("2026-06-05"), "rsi_level"] == "area"
