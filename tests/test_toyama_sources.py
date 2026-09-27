"""Toyama sources: TOYTOS survey and Toyama City AI cameras."""
import pandas as pd

from dhde_preprocessing.sources import camera, camera_toyama, survey, survey_toytos


def _toytos_cfg(cities):
    return {"node_key": "toyama_station", "sources": {"survey": {
        "enabled": True, "provider": "toytos", "ckan_dataset": "kanko_data",
        "resource_name": "富山県観光ウェブアンケート", "fallback_url": "https://example.invalid/x.csv",
        "cities": cities,
    }}}


def _patch_toytos(monkeypatch, responses):
    monkeypatch.setattr(survey_toytos, "_resource_url", lambda dataset, name: "https://example.invalid/x.csv")
    monkeypatch.setattr(survey_toytos, "fetch_csv", lambda url, cache_name, **k: (responses, "stub"))


def test_toytos_filters_on_answer_place_and_drops_exact_duplicates(monkeypatch):
    responses = pd.DataFrame({
        "アンケート回答日": ["2025/5/1", "2025/5/1", "2025/5/1", "2025/5/2"],
        "回答場所": ["富山市", "富山市", "高岡市", "富山市"],
        "満足度（旅行全体）": ["満足", "満足", "満足", "やや満足"],
    })
    _patch_toytos(monkeypatch, responses)
    df, report = survey.load_survey(_toytos_cfg(["富山市"]))
    assert report.status == "ok"
    assert len(df) == 2  # the identical 05-01 row counted once; 高岡市 excluded
    assert any("1 exact duplicate" in n for n in report.notes)


def test_toytos_uses_fallback_url_when_ckan_is_down(monkeypatch):
    seen = {}

    def _ckan_down(*a):
        raise RuntimeError("ckan down")

    def _fetch(url, cache_name, **k):
        seen["url"] = url
        return pd.DataFrame({"アンケート回答日": ["2025/5/1"], "回答場所": ["富山市"]}), "stub"
    monkeypatch.setattr(survey_toytos, "_resource_url", _ckan_down)
    monkeypatch.setattr(survey_toytos, "fetch_csv", _fetch)
    df, report = survey.load_survey(_toytos_cfg(["富山市"]))
    assert report.status == "ok"
    assert seen["url"] == "https://example.invalid/x.csv"


def _camera_export(totals):
    days = [f"2024-01-0{i}" for i in range(1, len(totals) + 1)]
    return pd.DataFrame({"Day": days, "DayofWeek": ["月"] * len(totals), "Total(Male)": [t // 2 for t in totals],
                         "Total(Female)": [t - t // 2 for t in totals], "Total": totals})


def _cam_cfg(gates):
    return {"node_key": "toyama_station", "sources": {"camera": {
        "enabled": True, "provider": "toyama_city", "gates": gates,
    }}}


def test_camera_multi_gate_keeps_separate_columns_and_blanks_zero_days(monkeypatch):
    exports = {"toyama_camera_07": _camera_export([1000, 0, 1200]), "toyama_camera_04": _camera_export([500, 600, 700])}
    monkeypatch.setattr(camera_toyama, "fetch_csv", lambda url, cache_name, **k: (exports[cache_name], "stub"))
    df, report = camera.load_camera(_cam_cfg([{"name": "free_passage", "camera_id": "07"},
                                              {"name": "north_exit", "camera_id": "04"}]))
    assert report.status == "ok"
    assert "count" not in df.columns  # never summed across cameras
    assert df["north_exit_count"].tolist() == [500, 600, 700]
    assert df["free_passage_count"].isna().tolist() == [False, True, False]
    assert df["free_passage_male_count"].isna().tolist() == [False, True, False]
    assert any("1 day(s) with a total of 0" in n for n in report.notes)


def test_camera_single_gate_uses_plain_count_column(monkeypatch):
    monkeypatch.setattr(camera_toyama, "fetch_csv", lambda url, cache_name, **k: (_camera_export([10, 20]), "stub"))
    df, _ = camera.load_camera(_cam_cfg([{"name": "free_passage", "camera_id": "07"}]))
    assert list(df.columns) == ["date", "count", "male_count", "female_count"]
