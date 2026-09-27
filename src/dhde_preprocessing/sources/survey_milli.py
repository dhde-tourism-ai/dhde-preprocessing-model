"""
Visitor survey ingestion for Ishikawa nodes — Milli QR survey.

Selected by `survey.provider: milli` in a node config; survey.py
dispatches here. Fukui nodes don't set a provider and keep reading
fukui-kanko-survey exactly as before.

Source: Milli's "QRアンケートデータ 表形式データ (全エリア)" Google Sheet —
one row per response, from QR-code posters at ~730 accommodations and
tourist facilities across Ishikawa, starting 2023-09. Each row carries
エリア (金沢/加賀/能登エリア) and 施設 (facility name), but no municipality.
The municipality comes from Milli's separate facility list sheet
(エリア, 施設, 住所, 市町), joined on (エリア, 施設); nodes are then matched
on 市町 via the config's `cities` list.

Facility names that aren't in the facility list (~1% of rows as of
2026-09) can't be placed in any municipality, so they're dropped and
counted in the report notes rather than guessed at. If a name appears in
the list under a different エリア, it's matched on name alone, but only
when that name is unique in the list.

Response counts here are NOT comparable with fukui-kanko-survey counts:
they depend on how many posters each prefecture put up, not only on
visitor numbers. Compare trends within a prefecture, not levels across.

Like survey.py, this returns raw response-level rows (every original
column kept, plus 市町 and date); join.py does the daily aggregation.
"""
from __future__ import annotations

import pandas as pd

from ..validation import SourceReport, validate_daily
from .gsheet import fetch_sheet


def _facility_city_lookup(facilities: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    f = facilities.dropna(subset=["施設", "市町"]).copy()
    for c in ("エリア", "施設", "市町"):
        f[c] = f[c].astype(str).str.strip()
    by_area_and_name = dict(zip(zip(f["エリア"], f["施設"]), f["市町"]))
    unique_names = f.drop_duplicates("施設", keep=False)
    by_name = dict(zip(unique_names["施設"], unique_names["市町"]))
    return by_area_and_name, by_name


def assign_city(responses: pd.DataFrame, facilities: pd.DataFrame) -> pd.Series:
    by_area_and_name, by_name = _facility_city_lookup(facilities)
    area = responses["エリア"].astype(str).str.strip()
    name = responses["施設"].astype(str).str.strip()
    city = pd.Series([by_area_and_name.get(k) for k in zip(area, name)], index=responses.index)
    return city.fillna(name.map(by_name))


def load_milli_survey(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    survey_cfg = node_cfg["sources"]["survey"]
    cities = survey_cfg["cities"]

    try:
        responses, responses_note = fetch_sheet(survey_cfg["sheet_id"], "survey_responses")
        facilities, facilities_note = fetch_sheet(survey_cfg["facilities_sheet_id"], "survey_facilities")
    except Exception as e:  # noqa: BLE001 - a fetch failure must not crash the whole run
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=[f"Milli sheet fetch failed and no cache: {e!r}"])

    responses = responses.copy()
    responses["市町"] = assign_city(responses, facilities)
    n_unplaced = int(responses["市町"].isna().sum())

    df = responses[responses["市町"].isin(cities)].copy()
    notes = [responses_note, facilities_note, f"matched on facility municipality (市町) in {cities}"]
    if n_unplaced:
        notes.append(f"{n_unplaced} of {len(responses)} prefecture-wide responses have a facility not in "
                     f"Milli's facility list — dropped, can't be placed in a municipality")
    if df.empty:
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=notes + ["zero responses matched — check the cities list against 市町 values"])

    df["date"] = pd.to_datetime(df["タイムスタンプ"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    report = validate_daily(df, source="survey", node_key=node_key, notes=notes)
    return df, report
