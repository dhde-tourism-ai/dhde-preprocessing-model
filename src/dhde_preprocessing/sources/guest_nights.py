"""
Monthly guest-nights per prefecture — JTA's accommodation survey
(宿泊旅行統計調査), the control total for the monthly forecast.

Source: the survey's 推移表 (time-series) workbook, linked from
PAGE_URL and replaced on every release. One workbook holds every month
since 2011, split into:

    旧1-2 / 旧2-2 / 旧3-2   total / Japanese / foreign, 2011-01 to 2025-12
    1-1   / 2-1   / 3-1     total / Japanese / foreign, 2026-01 onward

The split is a method change, not just a layout one: from 2026-01 JTA
stratifies its sample by rooms instead of by employees, and says
year-on-year changes across the boundary may partly reflect that.

Release lag: first preliminary about one month after the month, second
about two; the 推移表 carries second preliminaries, so it runs about two
months behind. Prefecture level only — JTA publishes no monthly
municipality figures.
"""
from __future__ import annotations

import re
from io import BytesIO

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_path, write_csv
from .remote_csv import CACHE_DIR

PAGE_URL = "https://www.mlit.go.jp/kankocho/tokei_hakusyo/shukuhakutokei.html"
CACHE_PATH = f"{CACHE_DIR}/guest_nights.csv"
# The site answers bare clients with an error page.
HEADERS = {"User-Agent": "Mozilla/5.0 (dhde-preprocessing)"}

# column -> (sheet from 2026-01, sheet up to 2025-12)
SHEETS = {
    "guest_nights": ("1-1", "旧1-2"),
    "guest_nights_japanese": ("2-1", "旧2-2"),
    "guest_nights_foreign": ("3-1", "旧3-2"),
}
COLUMNS = ["month", "pref_code", *SHEETS]

_LINK_RE = re.compile(r'<a[^>]+href="([^"]+\.xlsx)"[^>]*>(.*?)</a>', re.S)
_ERA_RE = re.compile(r"(平成|令和)(\d+|元)年")
_ERA_BASE = {"平成": 1988, "令和": 2018}


def find_workbook_url(html: str) -> str:
    """The 推移表 workbook's absolute URL, from the survey page's HTML."""
    for href, text in _LINK_RE.findall(html):
        if re.sub(r"<[^>]+>|\s", "", text).startswith("推移表"):
            return requests.compat.urljoin(PAGE_URL, href)
    raise ValueError("no 推移表 workbook link on the survey page")


def _year(label) -> int | None:
    m = _ERA_RE.search(str(label))
    if not m:
        return None
    return _ERA_BASE[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))


def parse_sheet(sheet: pd.DataFrame) -> pd.DataFrame:
    """One monthly sheet -> (month YYYYMM, pref_code, n).

    Layout: row 2 holds the era year over its first month (blank after),
    row 3 the month ("1月"), then one row per prefecture ("18福井県"; the
    national row "全　国" gets pref_code 0). Blank cells are months not yet
    published.
    """
    years = sheet.iloc[2].map(_year).ffill()
    months = sheet.iloc[3]
    out = []
    for r in range(4, len(sheet)):
        name = sheet.iat[r, 0]
        if not isinstance(name, str):
            continue
        code = re.match(r"\d\d", name)
        pref = int(code.group()) if code else 0
        for c in range(1, sheet.shape[1]):
            month, value = months.iat[c], sheet.iat[r, c]
            if not (isinstance(month, str) and month.endswith("月")) or pd.isna(years.iat[c]) or pd.isna(value):
                continue
            out.append((int(years.iat[c]) * 100 + int(month[:-1]), pref, float(value)))
    return pd.DataFrame(out, columns=["month", "pref_code", "n"])


def parse_workbook(content: bytes) -> pd.DataFrame:
    book = pd.ExcelFile(BytesIO(content))
    table = None
    for col, (new_sheet, old_sheet) in SHEETS.items():
        parts = [parse_sheet(book.parse(s, header=None)) for s in (old_sheet, new_sheet)]
        # Old first, so a month in both sheets keeps the newer method's figure.
        one = (pd.concat(parts).drop_duplicates(["month", "pref_code"], keep="last")
               .rename(columns={"n": col}))
        table = one if table is None else table.merge(one, on=["month", "pref_code"], how="outer")
    return table[COLUMNS].sort_values(["pref_code", "month"]).reset_index(drop=True)


def load_guest_nights() -> tuple[pd.DataFrame, str]:
    """Every prefecture's monthly guest-nights, and a note on where they came from.

    Falls back to the last parsed copy if the page or workbook can't be
    fetched or read; raises only when there is no cached copy either.
    """
    cache_path = resolve_path(CACHE_PATH)
    try:
        page = requests.get(PAGE_URL, headers=HEADERS, timeout=60)
        page.raise_for_status()
        url = find_workbook_url(page.content.decode("utf-8", errors="replace"))
        book = requests.get(url, headers=HEADERS, timeout=120)
        book.raise_for_status()
        table = parse_workbook(book.content)
    except Exception as e:  # noqa: BLE001 - fall back to cache on any fetch/parse failure
        cached = read_csv_if_exists(cache_path)
        if cached is not None:
            return cached, f"live fetch failed ({e!r}); used cached copy at {cache_path}"
        raise
    write_csv(table, cache_path)
    return table, f"fetched live from {url}"
