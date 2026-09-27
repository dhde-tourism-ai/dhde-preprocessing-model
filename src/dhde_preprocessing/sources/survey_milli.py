"""
Visitor survey ingestion for Ishikawa nodes — Milli QR survey.

Selected by `survey.provider: milli` in a node config; survey.py
dispatches here. Fukui nodes don't set a provider and keep reading
fukui-kanko-survey exactly as before.

Source: code4fukui/ishikawa-kanko-survey, all.csv — the same code4fukui
group that publishes fukui-kanko-survey. It pulls Milli's (Ishikawa
Prefecture's tourism open-data project) QR survey sheets daily, drops
non-consenting responses and shortens the column names. One row per
response, from QR-code posters at ~730 accommodations and tourist
facilities across Ishikawa, starting 2023-09.

Each row carries 回答エリア (金沢/加賀/能登) and 施設 (facility name), but
no municipality. The municipality comes from Milli's facility list
Google Sheet (エリア, 施設, 住所, 市町), joined on (area, facility); nodes
are then matched on 市町 via the config's `cities` list. The repo writes
the area as "金沢" and the facility list as "金沢エリア", so both are
normalized before joining.

Facility names that aren't in the facility list (~1% of rows as of
2026-09) can't be placed in any municipality, so they're dropped and
counted in the report notes rather than guessed at. If a name appears in
the list under a different area, it's matched on name alone, but only
when that name is unique in the list.

Response counts here are NOT comparable with fukui-kanko-survey counts:
they depend on how many posters each prefecture put up, not only on
visitor numbers. Compare trends within a prefecture, not levels across.

Like survey.py, this returns raw response-level rows (every original
column kept, plus 市町 and date); join.py does the daily aggregation.
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, validate_daily
from .remote_csv import fetch_sheet


def _area(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.removesuffix("エリア")


def _facility_city_lookup(facilities: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    f = facilities.dropna(subset=["施設", "市町"]).copy()
    f["エリア"] = _area(f["エリア"])
    for c in ("施設", "市町"):
        f[c] = f[c].astype(str).str.strip()
    by_area_and_name = dict(zip(zip(f["エリア"], f["施設"]), f["市町"]))
    unique_names = f.drop_duplicates("施設", keep=False)
    by_name = dict(zip(unique_names["施設"], unique_names["市町"]))
    return by_area_and_name, by_name


def assign_city(responses: pd.DataFrame, facilities: pd.DataFrame) -> pd.Series:
    by_area_and_name, by_name = _facility_city_lookup(facilities)
    area = _area(responses["回答エリア"])
    name = responses["施設"].astype(str).str.strip()
    city = pd.Series([by_area_and_name.get(k) for k in zip(area, name)], index=responses.index)
    return city.where(city.notna(), name.map(by_name))


def clean_responses(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Row-level cleaning; returns (cleaned rows, duplicates dropped).

    Answer fields are left as-is (spending stays as the questionnaire's
    yen-range text, e.g. "10,000円以上 20,000円未満") — turning them into
    numbers is a modeling-stage choice, as for Fukui.

    - 回答日時 is ISO with +09:00; converted to naive JST so `date` merges
      with every other source's naive dates.
    - Two submissions from the same facility in the same second are one
      person double-submitting (a few are byte-identical, the rest differ
      only in a field or two), not two visitors — keep the first.
    """
    df = df.copy()
    ts = pd.to_datetime(df["回答日時"], errors="coerce", utc=True).dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
    df["date"] = ts.dt.normalize()
    df = df.dropna(subset=["date"])
    n_before = len(df)
    df = df.drop_duplicates(subset=["回答エリア", "施設", "回答日時"], keep="first")
    return df.sort_values("date").reset_index(drop=True), n_before - len(df)


def load_milli_survey(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    survey_cfg = node_cfg["sources"]["survey"]
    cities = survey_cfg["cities"]

    responses = pd.read_csv(f"{resolve_path(survey_cfg['repo'])}/all.csv", low_memory=False)
    try:
        facilities, facilities_note = fetch_sheet(survey_cfg["facilities_sheet_id"], "milli_survey_facilities")
    except Exception as e:  # noqa: BLE001 - a fetch failure must not crash the whole run
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=[f"Milli facility list fetch failed and no cache: {e!r}"])

    responses["市町"] = assign_city(responses, facilities)
    n_unplaced = int(responses["市町"].isna().sum())

    df = responses[responses["市町"].isin(cities)]
    notes = [f"responses from {survey_cfg['repo']}/all.csv; facility list {facilities_note}",
             f"matched on facility municipality (市町) in {cities}"]
    if n_unplaced:
        notes.append(f"{n_unplaced} of {len(responses)} prefecture-wide responses have a facility not in "
                     f"Milli's facility list — dropped, can't be placed in a municipality")
    if df.empty:
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=notes + ["zero responses matched — check the cities list against 市町 values"])

    df, n_dupes = clean_responses(df)
    if n_dupes:
        notes.append(f"{n_dupes} duplicate submission(s) dropped (same facility, same second)")

    report = validate_daily(df, source="survey", node_key=node_key, notes=notes)
    return df, report
