import pandas as pd
import pytest

from dhde_preprocessing.sources import monthly_visitors


@pytest.fixture
def stat_repo(monkeypatch, tmp_path):
    (tmp_path / "japan-kanko-stat" / "data").mkdir(parents=True)
    pd.DataFrame({
        "month": [202401, 202402, 202401, 202402, 202401],
        "lgcode": [17201, 17201, 17, 17, 18201],
        "n": [310, 290, 900, 870, 55],
    }).to_csv(tmp_path / "japan-kanko-stat" / "data" / "all.csv", index=False)
    monkeypatch.setattr(monthly_visitors, "resolve_path", lambda p: str(tmp_path / p))


def _cfg(city=17201, pref=17):
    return {"node_key": "kanazawa", "sources": {"monthly_visitors": {
        "enabled": True, "repo": "japan-kanko-stat", "city_lgcode": city, "pref_lgcode": pref,
    }}}


def test_month_total_is_repeated_on_every_day_of_its_month(stat_repo):
    df, report = monthly_visitors.load_monthly_visitors(_cfg())
    assert report.status == "ok"
    assert len(df) == 31 + 29  # Jan + Feb 2024 (leap year)
    assert df["date"].min() == pd.Timestamp("2024-01-01")
    assert df["date"].max() == pd.Timestamp("2024-02-29")
    jan, feb = df[df["date"].dt.month == 1], df[df["date"].dt.month == 2]
    assert set(jan["city_visitors_month"]) == {310} and set(feb["city_visitors_month"]) == {290}
    assert set(jan["pref_visitors_month"]) == {900}


def test_unknown_code_is_noted_and_other_level_kept(stat_repo):
    df, report = monthly_visitors.load_monthly_visitors(_cfg(city=99999))
    assert report.status == "ok"
    assert "city_visitors_month" not in df.columns
    assert any("99999" in n for n in report.notes)


def test_disabled_is_unavailable():
    df, report = monthly_visitors.load_monthly_visitors({"node_key": "x", "sources": {}})
    assert df is None
    assert report.status == "unavailable"
