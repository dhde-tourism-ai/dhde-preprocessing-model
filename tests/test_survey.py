import pandas as pd
import pytest

from dhde_preprocessing.sources import survey


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(survey, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def _write_survey_repo(tmp_path):
    repo = tmp_path / "survey_repo"
    repo.mkdir()
    # area.csv: note the 300000-range number lives in 親番号, NOT id —
    # this is the bug that was actually hit while building this module.
    pd.DataFrame({
        "id": [6, 4],
        "市町名": ["坂井市", "福井市"],
        "親番号": [300006, 300004],
        "エリア名": ["東尋坊 エリア", "福井駅前 エリア"],
    }).to_csv(repo / "area.csv", index=False)

    pd.DataFrame({
        "回答日時": ["2024-01-01 10:00:00", "2024-01-02 11:00:00", "2024-01-03 09:00:00"],
        "回答エリア": ["東尋坊 エリア", "福井駅前 エリア", "越前海岸 北部 エリア"],
        "満足度の理由": ["景色が良かった、東尋坊がきれいだった", "", ""],  # mentions 東尋坊 in free text only
    }).to_csv(repo / "all.csv", index=False)
    return repo


def test_matches_on_parent_number_not_id(patch_resolve_path):
    patch_resolve_path  # noqa: B018 - fixture ensures resolve_path patched before repo written
    tmp_path = patch_resolve_path
    _write_survey_repo(tmp_path)

    node_cfg = {"node_key": "tojinbo", "sources": {"survey": {
        "enabled": True, "repo": "survey_repo", "area_ids": [300006],
    }}}
    df, report = survey.load_survey(node_cfg)
    assert report.status == "ok"
    assert len(df) == 1
    assert df.iloc[0]["回答エリア"] == "東尋坊 エリア"


def test_does_not_match_free_text_mentions(patch_resolve_path):
    """The row whose 回答エリア is 越前海岸 mentions 東尋坊 only in its
    free-text satisfaction-reason field — must not be pulled in by an
    exact 回答エリア match."""
    tmp_path = patch_resolve_path
    _write_survey_repo(tmp_path)

    node_cfg = {"node_key": "tojinbo", "sources": {"survey": {
        "enabled": True, "repo": "survey_repo", "area_ids": [300006],
    }}}
    df, _ = survey.load_survey(node_cfg)
    assert "越前海岸 北部 エリア" not in df["回答エリア"].values


def test_unknown_area_id_is_reported_as_error(patch_resolve_path):
    tmp_path = patch_resolve_path
    _write_survey_repo(tmp_path)
    node_cfg = {"node_key": "x", "sources": {"survey": {
        "enabled": True, "repo": "survey_repo", "area_ids": [999999],
    }}}
    df, report = survey.load_survey(node_cfg)
    assert df is None
    assert report.status == "error"


def test_daily_summary_scores_satisfaction_and_groups_home_prefecture():
    responses = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01"] * 4 + ["2024-01-02"]),
        "満足度": ["とても満足", "満足", "不満", None, "どちらでもない"],
        "都道府県": ["福井県", "大阪府", "北海道", "石川県", None],
        "NPS": [10, 9, 3, None, 7],
        "温泉や露天風呂": [1, 1, 0, 0, 1],
        "地元の美味しいものを食べる": [1, 0, 0, None, 0],
    })
    by = survey.daily_summary(responses).set_index("date")
    day = by.loc["2024-01-01"]
    assert day["survey_response_count"] == 4
    assert day["survey_satisfaction_n"] == 3 and day["survey_satisfaction_mean"] == pytest.approx(11 / 3, abs=1e-3)
    assert (day["survey_origin_fukui"], day["survey_origin_kansai"], day["survey_origin_hokuriku"],
            day["survey_origin_other"], day["survey_origin_kanto"]) == (1, 1, 1, 1, 0)
    assert (day["survey_nps_n"], day["survey_nps_promoters"], day["survey_nps_detractors"]) == (3, 2, 1)
    assert by.loc["2024-01-02", ["survey_nps_promoters", "survey_nps_detractors"]].sum() == 0  # 7 is passive
    assert day["survey_purpose_onsen"] == 2 and day["survey_purpose_local_food"] == 1
    assert "survey_purpose_shopping" not in by.columns  # not asked in this sample
    # No prefecture given: counted as a response, not as any region.
    assert by.loc["2024-01-02", survey.ORIGIN_COLS].sum() == 0


def test_daily_summary_without_the_fukui_columns_counts_only():
    responses = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-01"])})
    out = survey.daily_summary(responses)
    assert list(out.columns) == ["date", "survey_response_count"]
    assert out["survey_response_count"].item() == 2


def test_disabled_survey_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"survey": {"enabled": False, "reason": "nope"}}}
    df, report = survey.load_survey(node_cfg)
    assert df is None
    assert report.status == "unavailable"
