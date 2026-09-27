import pandas as pd
import pytest

from dhde_preprocessing.sources import survey, survey_milli

FACILITIES = pd.DataFrame({
    "エリア": ["金沢エリア", "金沢エリア", "加賀エリア", "能登エリア", "加賀エリア"],
    "施設": ["兼六園", "金沢駅", "山代温泉", "和倉温泉", "同名の宿"],
    "市町": ["金沢市", "金沢市", "加賀市", "七尾市", "小松市"],
})

RESPONSES = pd.DataFrame({
    "エリア": ["金沢エリア", "金沢エリア", "加賀エリア", "能登エリア", "加賀エリア", "能登エリア"],
    "施設": ["兼六園", " 金沢駅 ", "山代温泉", "和倉温泉", "リストにない宿", "兼六園"],
    "タイムスタンプ": ["2024/01/01 10:00:00", "2024/01/01 12:30:00", "2024/01/02 09:00:00",
                   "2024/01/02 18:00:00", "2024/01/03 08:00:00", "2024/01/03 11:00:00"],
})


@pytest.fixture
def patch_fetch(monkeypatch):
    sheets = {"responses": RESPONSES, "facilities": FACILITIES}
    monkeypatch.setattr(survey_milli, "fetch_sheet", lambda sheet_id, cache_name: (sheets[sheet_id], "stub"))


def _cfg(cities):
    return {"node_key": "kanazawa", "sources": {"survey": {
        "enabled": True, "provider": "milli",
        "sheet_id": "responses", "facilities_sheet_id": "facilities", "cities": cities,
    }}}


def test_dispatches_from_survey_and_filters_by_city(patch_fetch):
    df, report = survey.load_survey(_cfg(["金沢市"]))
    assert report.status == "ok"
    assert set(df["市町"]) == {"金沢市"}
    # 兼六園 listed under 能登エリア in the response still resolves by its
    # unique name, so Kanazawa gets 3 rows: two on 01-01, one on 01-03.
    assert len(df) == 3
    assert df.groupby("date").size().to_dict() == {
        pd.Timestamp("2024-01-01"): 2, pd.Timestamp("2024-01-03"): 1,
    }


def test_unlisted_facility_is_dropped_and_counted(patch_fetch):
    df, report = survey.load_survey(_cfg(["加賀市", "小松市"]))
    assert list(df["施設"]) == ["山代温泉"]
    assert any("1 of 6" in n for n in report.notes)


def test_no_matching_city_is_an_error(patch_fetch):
    df, report = survey.load_survey(_cfg(["輪島市"]))
    assert df is None
    assert report.status == "error"


def test_fetch_failure_without_cache_is_reported_not_raised(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("network boom")
    monkeypatch.setattr(survey_milli, "fetch_sheet", _raise)
    df, report = survey.load_survey(_cfg(["金沢市"]))
    assert df is None
    assert report.status == "error"


def test_ambiguous_name_is_not_matched_on_name_alone():
    facilities = pd.DataFrame({
        "エリア": ["金沢エリア", "加賀エリア"], "施設": ["共通名", "共通名"], "市町": ["金沢市", "加賀市"],
    })
    responses = pd.DataFrame({"エリア": ["能登エリア"], "施設": ["共通名"]})
    assert survey_milli.assign_city(responses, facilities).isna().all()


def test_double_submission_is_dropped_and_consent_column_removed():
    df = pd.DataFrame({
        "エリア": ["金沢エリア", "金沢エリア", "金沢エリア"],
        "施設": ["兼六園", "兼六園", "兼六園"],
        "タイムスタンプ": ["2024/01/01 10:00:00", "2024/01/01 10:00:00", "2024/01/01 10:00:01"],
        "個人情報保護の方針について": ["同意する"] * 3,
        "満足度": ["満足", "とても満足", "満足"],  # double submit that differs in one field
    })
    cleaned, n_dupes = survey_milli.clean_responses(df)
    assert n_dupes == 1
    assert len(cleaned) == 2
    assert cleaned.iloc[0]["満足度"] == "満足"  # keeps the first
    assert "個人情報保護の方針について" not in cleaned.columns
