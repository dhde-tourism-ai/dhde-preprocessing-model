"""
Visitor sentiment / Kansei survey ingestion.

Source: code4fukui/fukui-kanko-survey, all.csv (~100MB, one row per
response, prefecture-wide, updated daily) filtered by its 回答エリア
column against area.csv's exact エリア名 for the node's configured
area id(s) — matched against area.csv's 親番号 column, NOT its "id"
column (a small unrelated sequential row number); the 300000-range
numbers used in node configs and by the user are 親番号 values.

IMPORTANT: byid/{id}.csv files are NOT area-filtered data despite what
that repo's README implies — they're sharded by respondent member ID
(id // 100). Do not use them for area filtering; this module only reads
all.csv + area.csv.

Matching is an exact string match against 回答エリア (normalized —
stripped of surrounding whitespace), not a substring/contains search —
free-text fields elsewhere in the same row can incidentally mention a
landmark name without the response actually being registered to that
area, so a loose match would pull in false positives.

This module returns the raw, response-level filtered rows (one row per
survey response, not aggregated to one row per day) — every original
column is preserved for whatever the later modeling stage needs
(satisfaction, NPS, spending, free text, demographics). Aggregating this
down to a single daily figure (a response count, at minimum) happens in
join.py, since that's a per-node table-shape decision, not a per-source
cleaning decision.
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

CHUNK_SIZE = 50_000


def _resolve_area_names(area_csv_path: str, area_ids: list[int]) -> dict[int, str]:
    # area.csv's "id" column is a small sequential row number — the
    # 300000-range identifiers used throughout this project (and by the
    # user) are actually the "親番号" (parent number) column.
    area_df = pd.read_csv(area_csv_path)
    area_df = area_df[area_df["親番号"].isin(area_ids)]
    return dict(zip(area_df["親番号"], area_df["エリア名"].str.strip()))


def load_survey(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    survey_cfg = node_cfg["sources"].get("survey", {})
    if not survey_cfg.get("enabled"):
        return None, unavailable_report("survey", node_key, survey_cfg.get("reason", "survey disabled for this node"))

    repo_root = resolve_path(survey_cfg["repo"])
    area_ids = survey_cfg["area_ids"]
    area_name_by_id = _resolve_area_names(f"{repo_root}/area.csv", area_ids)
    missing_ids = [i for i in area_ids if i not in area_name_by_id]
    if missing_ids:
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=[f"area id(s) not found in area.csv: {missing_ids}"])

    target_names = set(area_name_by_id.values())
    notes = [f"matched on 回答エリア in {target_names} (area id(s) {area_ids})"]

    chunks = []
    all_csv_path = f"{repo_root}/all.csv"
    for chunk in pd.read_csv(all_csv_path, chunksize=CHUNK_SIZE, low_memory=False):
        matched = chunk[chunk["回答エリア"].astype(str).str.strip().isin(target_names)]
        if not matched.empty:
            chunks.append(matched)

    if not chunks:
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=notes + ["zero responses matched — check area name/id mapping"])

    df = pd.concat(chunks, ignore_index=True)
    df["date"] = pd.to_datetime(df["回答日時"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    report = validate_daily(df, source="survey", node_key=node_key, notes=notes)
    return df, report
