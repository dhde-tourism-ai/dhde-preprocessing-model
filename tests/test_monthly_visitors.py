import pandas as pd
import pytest

from dhde_preprocessing.sources import monthly_visitors


def _official_csv(rows: list[tuple]) -> pd.DataFrame:
    """A publisher city/pref file as fetch_csv returns it (Japanese headers)."""
    return pd.DataFrame(rows, columns=["年", "月", "地域区分", "データ区分", "地域コード", "地域名称", "人数"])


@pytest.fixture
def official(monkeypatch):
    """Three publisher files: a monthly city file for Feb 2024, then the
    yearly city and pref files for 2024, published once the year closed,
    which revise Feb. Listed as strings sort (city2024 first)."""
    files = {
        "city2024": _official_csv([
            (2024, 1, "市区町村", "観光来訪者数", 17201, "金沢市", 310),
            (2024, 2, "市区町村", "観光来訪者数", 17201, "金沢市", 295),
            (2024, 1, "市区町村", "観光来訪者数", 18201, "福井市", None),
        ]),
        "pref2024": _official_csv([
            (2024, 1, "都道府県", "観光来訪者数", 17, "石川県", 900),
            (2024, 2, "都道府県", "観光来訪者数", 17, "石川県", 870),
        ]),
        "city202402": _official_csv([
            (2024, 2, "市区町村", "観光来訪者数", 17201, "金沢市", 290),
        ]),
    }
    monkeypatch.setattr(monthly_visitors, "list_official_csvs",
                        lambda: ([(f"https://x/{s}.csv", s) for s in files], "file list: test"))
    monkeypatch.setattr(monthly_visitors, "fetch_csv",
                        lambda url, name, **kw: (files[name.removeprefix("kanko_stat_")], f"fetched live from {url}"))
    monthly_visitors.load_official_table.cache_clear()
    yield
    monthly_visitors.load_official_table.cache_clear()


def _cfg(city=17201, pref=17):
    return {"node_key": "kanazawa", "sources": {"monthly_visitors": {
        "enabled": True, "city_lgcode": city, "pref_lgcode": pref,
    }}}


def test_month_total_is_repeated_on_every_day_of_its_month(official):
    df, report = monthly_visitors.load_monthly_visitors(_cfg())
    assert report.status == "ok"
    assert len(df) == 31 + 29  # Jan + Feb 2024 (leap year)
    assert df["date"].min() == pd.Timestamp("2024-01-01")
    assert df["date"].max() == pd.Timestamp("2024-02-29")
    jan = df[df["date"].dt.month == 1]
    assert set(jan["city_visitors_month"]) == {310}
    assert set(jan["pref_visitors_month"]) == {900}


def test_later_published_file_wins_for_a_reissued_month(official):
    df, _ = monthly_visitors.load_monthly_visitors(_cfg())
    feb = df[df["date"].dt.month == 2]
    assert set(feb["city_visitors_month"]) == {295}


def test_blank_count_is_dropped_not_zero(official):
    table, _ = monthly_visitors.load_official_table()
    assert 18201 not in set(table["lgcode"])


def test_unknown_code_is_noted_and_other_level_kept(official):
    df, report = monthly_visitors.load_monthly_visitors(_cfg(city=99999))
    assert report.status == "ok"
    assert "city_visitors_month" not in df.columns
    assert any("99999" in n for n in report.notes)


def test_fetch_failure_is_an_error_report_not_a_crash(monkeypatch):
    def boom():
        raise ConnectionError("offline")
    monkeypatch.setattr(monthly_visitors, "list_official_csvs", boom)
    monthly_visitors.load_official_table.cache_clear()
    df, report = monthly_visitors.load_monthly_visitors(_cfg())
    monthly_visitors.load_official_table.cache_clear()
    assert df is None
    assert report.status == "error"


def test_fetch_failure_is_cached_for_the_run(monkeypatch):
    calls = []
    def boom():
        calls.append(1)
        raise ConnectionError("offline")
    monkeypatch.setattr(monthly_visitors, "list_official_csvs", boom)
    monthly_visitors.load_official_table.cache_clear()
    monthly_visitors.load_monthly_visitors(_cfg())
    monthly_visitors.load_monthly_visitors(_cfg(city=18201, pref=18))
    monthly_visitors.load_official_table.cache_clear()
    assert len(calls) == 1


def test_page_links_are_parsed():
    html = ('<a href="https://d2eveo6c5xeu3l.cloudfront.net/city/city2025.csv">x</a>'
            '<a href="https://d2eveo6c5xeu3l.cloudfront.net/pref/pref202608.csv">y</a>'
            '<a href="https://example.com/other.csv">z</a>')
    assert monthly_visitors.CSV_LINK_RE.findall(html) == [
        ("https://d2eveo6c5xeu3l.cloudfront.net/city/city2025.csv", "city2025"),
        ("https://d2eveo6c5xeu3l.cloudfront.net/pref/pref202608.csv", "pref202608"),
    ]


def test_disabled_is_unavailable():
    df, report = monthly_visitors.load_monthly_visitors({"node_key": "x", "sources": {}})
    assert df is None
    assert report.status == "unavailable"


def test_revised_prefecture_gets_a_comparability_note(official):
    _, fukui = monthly_visitors.load_monthly_visitors(_cfg(city=17201, pref=18))
    _, ishikawa = monthly_visitors.load_monthly_visitors(_cfg())
    assert any("202501 onward is comparable" in n for n in fukui.notes)
    assert not any("comparable" in n for n in ishikawa.notes)
