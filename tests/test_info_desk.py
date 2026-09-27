import pandas as pd
import pytest

from dhde_preprocessing.sources import gsheet, info_desk


def _desk(totals, foreign):
    rows = []
    for day, (t, f) in enumerate(zip(totals, foreign), start=1):
        date = f"2024/01/0{day}"
        rows += [(date, "合計", t), (date, "外国人", f), (date, "金沢市内観光", 999)]
    return pd.DataFrame(rows, columns=["日付", "属性", "数値"])


def _cfg():
    return {"node_key": "kanazawa", "sources": {"info_desk": {"enabled": True, "desks": [
        {"name": "駅", "sheet_id": "station"}, {"name": "中央", "sheet_id": "central"},
    ]}}}


def test_sums_total_and_foreign_across_desks(monkeypatch):
    sheets = {"station": _desk([100, 120], [30, 40]), "central": _desk([50, 60], [5, 6])}
    monkeypatch.setattr(info_desk, "fetch_sheet", lambda sheet_id, cache_name: (sheets[sheet_id], "stub"))
    df, report = info_desk.load_info_desk(_cfg())
    assert report.status == "ok"
    assert list(df.columns) == ["date", "info_desk_total", "info_desk_foreign"]
    assert df["info_desk_total"].tolist() == [150, 180]
    assert df["info_desk_foreign"].tolist() == [35, 46]


def test_one_desk_failing_keeps_the_other(monkeypatch):
    def _fetch(sheet_id, cache_name):
        if sheet_id == "central":
            raise RuntimeError("boom")
        return _desk([100], [30]), "stub"
    monkeypatch.setattr(info_desk, "fetch_sheet", _fetch)
    df, report = info_desk.load_info_desk(_cfg())
    assert report.status == "ok"
    assert df["info_desk_total"].tolist() == [100]
    assert any("中央" in n and "failed" in n for n in report.notes)


def test_missing_config_is_unavailable():
    df, report = info_desk.load_info_desk({"node_key": "tojinbo", "sources": {}})
    assert df is None
    assert report.status == "unavailable"


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(gsheet, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def test_gsheet_falls_back_to_cache_when_fetch_fails(monkeypatch, patch_resolve_path):
    cache = patch_resolve_path / gsheet.CACHE_DIR / "x.csv"
    cache.parent.mkdir(parents=True)
    pd.DataFrame({"a": [1, 2]}).to_csv(cache, index=False)

    def _raise(*a, **k):
        raise gsheet.requests.ConnectionError("offline")
    monkeypatch.setattr(gsheet.requests, "get", _raise)
    df, note = gsheet.fetch_sheet("sheet", "x")
    assert df["a"].tolist() == [1, 2]
    assert "cached copy" in note


def test_gsheet_rejects_non_csv_response(monkeypatch, patch_resolve_path):
    class _Resp:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        content = b"<html>sign in</html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(gsheet.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(ValueError):
        gsheet.fetch_sheet("sheet", "y")
