import pandas as pd
import pytest

from dhde_preprocessing.sources import guest_nights


def _sheet(years: list, months: list, rows: list[list]) -> pd.DataFrame:
    """A 推移表 monthly sheet: title, blank, era-year row, month row, data."""
    width = 1 + len(months)
    grid = [["title"] + [None] * (width - 1), [None] * width, [None, *years], [None, *months], *rows]
    return pd.DataFrame(grid)


def test_parse_sheet_reads_eras_months_and_prefectures():
    sheet = _sheet(
        ["平成31年", None, "令和元年", None],
        ["3月", "4月", "5月", "6月"],
        [["全　国", 1000, 1100, 1200, None],
         ["18福井県", 10, 11, 12, None]],
    )
    df = guest_nights.parse_sheet(sheet)
    fukui = df[df["pref_code"] == 18].set_index("month")["n"]
    assert fukui.to_dict() == {201903: 10, 201904: 11, 201905: 12}  # June not yet published
    assert set(df["pref_code"]) == {0, 18}


def test_find_workbook_url_picks_the_time_series_link():
    html = ('<a href="/kankocho/content/1.xlsx">2026年7月分 集計結果［Excel:26KB］</a>'
            '<a href="/kankocho/content/2.xlsx"><img src="x.png">推移表［Excel:1.3MB］</a>')
    assert guest_nights.find_workbook_url(html) == "https://www.mlit.go.jp/kankocho/content/2.xlsx"


def test_find_workbook_url_fails_loudly_without_one():
    with pytest.raises(ValueError):
        guest_nights.find_workbook_url("<a href='x.xlsx'>other</a>")
