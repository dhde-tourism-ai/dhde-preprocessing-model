import pandas as pd
import pytest

from dhde_preprocessing.sources import survey, survey_milli

# Milli's facility list writes areas as "金沢エリア"; the code4fukui repo
# writes "金沢" — the join has to normalize both.
FACILITIES = pd.DataFrame({
    "エリア": ["金沢エリア", "金沢エリア", "加賀エリア", "能登エリア", "加賀エリア"],
    "施設": ["兼六園", "金沢駅", "山代温泉", "和倉温泉", "同名の宿"],
    "市町": ["金沢市", "金沢市", "加賀市", "七尾市", "小松市"],
})

RESPONSES = pd.DataFrame({
    "ID": [1, 2, 3, 4, 5, 6],
    "回答エリア": ["金沢", "金沢", "加賀", "能登", "加賀", "能登"],
    "施設": ["兼六園", " 金沢駅 ", "山代温泉", "和倉温泉", "リストにない宿", "兼六園"],
    "回答日時": ["2024-01-01T10:00:00+09:00", "2024-01-01T23:30:00+09:00", "2024-01-02T09:00:00+09:00",
             "2024-01-02T18:00:00+09:00", "2024-01-03T08:00:00+09:00", "2024-01-03T11:00:00+09:00"],
})


@pytest.fixture
def milli_repo(monkeypatch, tmp_path):
    repo = tmp_path / "ishikawa-kanko-survey"
    repo.mkdir()
    RESPONSES.to_csv(repo / "all.csv", index=False)
    monkeypatch.setattr(survey_milli, "resolve_path", lambda p: str(tmp_path / p))
    monkeypatch.setattr(survey_milli, "fetch_sheet", lambda sheet_id, cache_name: (FACILITIES, "stub"))


def _cfg(cities):
    return {"node_key": "kanazawa", "sources": {"survey": {
        "enabled": True, "provider": "milli", "repo": "ishikawa-kanko-survey",
        "facilities_sheet_id": "facilities", "cities": cities,
    }}}


def test_dispatches_from_survey_and_filters_by_city(milli_repo):
    df, report = survey.load_survey(_cfg(["金沢市"]))
    assert report.status == "ok"
    assert set(df["市町"]) == {"金沢市"}
    # 兼六園 answered under 能登 still resolves by its unique name, so
    # Kanazawa gets 3 rows. 23:30 JST stays on 01-01 (dates are JST, naive).
    assert len(df) == 3
    assert df["date"].dt.tz is None
    assert df.groupby("date").size().to_dict() == {
        pd.Timestamp("2024-01-01"): 2, pd.Timestamp("2024-01-03"): 1,
    }


def test_unlisted_facility_is_dropped_and_counted(milli_repo):
    df, report = survey.load_survey(_cfg(["加賀市", "小松市"]))
    assert list(df["施設"]) == ["山代温泉"]
    assert any("1 of 6" in n for n in report.notes)


def test_no_matching_city_is_an_error(milli_repo):
    df, report = survey.load_survey(_cfg(["輪島市"]))
    assert df is None
    assert report.status == "error"


def test_facility_list_fetch_failure_is_reported_not_raised(milli_repo, monkeypatch):
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
    responses = pd.DataFrame({"回答エリア": ["能登"], "施設": ["共通名"]})
    assert survey_milli.assign_city(responses, facilities).isna().all()


def test_double_submission_is_dropped_keeping_the_first():
    df = pd.DataFrame({
        "回答エリア": ["金沢", "金沢", "金沢"],
        "施設": ["兼六園", "兼六園", "兼六園"],
        "回答日時": ["2024-01-01T10:00:00+09:00", "2024-01-01T10:00:00+09:00", "2024-01-01T10:00:01+09:00"],
        "満足度": ["満足", "とても満足", "満足"],  # double submit that differs in one field
    })
    cleaned, n_dupes = survey_milli.clean_responses(df)
    assert n_dupes == 1
    assert len(cleaned) == 2
    assert cleaned.iloc[0]["満足度"] == "満足"
