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
(satisfaction, NPS, spending, free text, demographics). `daily_summary`
turns it into one row per day (response count, satisfaction, NPS counts,
home region, purpose of visit); join.py calls it when building the node
table.
"""
from __future__ import annotations

import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily
from .survey_milli import load_milli_survey
from .survey_toytos import load_toytos_survey

CHUNK_SIZE = 50_000

# 満足度 answers on the Fukui survey, as a 1-5 score.
SATISFACTION = {"とても満足": 5, "満足": 4, "どちらでもない": 3, "不満": 2, "とても不満": 1}
# 都道府県 (home prefecture) -> region. The Fukui survey is answered by
# members registered in Japan: it has no overseas respondents, so origin
# here is domestic only. Prefectures not listed are "other".
ORIGIN_REGIONS = {
    "fukui": ["福井県"],
    "hokuriku": ["石川県", "富山県"],
    "kansai": ["大阪府", "京都府", "兵庫県", "奈良県", "和歌山県", "滋賀県"],
    "chubu": ["愛知県", "岐阜県", "三重県", "静岡県", "長野県", "山梨県", "新潟県"],
    "kanto": ["東京都", "神奈川県", "埼玉県", "千葉県", "茨城県", "栃木県", "群馬県"],
}
REGION_OF = {pref: region for region, prefs in ORIGIN_REGIONS.items() for pref in prefs}
ORIGIN_COLS = [f"survey_origin_{r}" for r in [*ORIGIN_REGIONS, "other"]]
# 訪問目的 (purpose of visit): one 0/1 column per answer, several allowed.
# The free-text その他 (other) column is left out.
PURPOSES = {
    "宿でのんびり過ごす": "relax_at_inn",
    "温泉や露天風呂": "onsen",
    "地元の美味しいものを食べる": "local_food",
    "花見や紅葉などの自然鑑賞": "nature",
    "名所、旧跡の観光": "sightseeing",
    "テーマパーク（遊園地、動物園、博物館など）": "theme_park_museum",
    "買い物、アウトレット": "shopping",
    "お祭りやイベントへの参加・見物": "events",
    "スポーツ観戦や芸能鑑賞（コンサート等）": "shows",
    "アウトドア（海水浴、釣り、登山など）": "outdoor",
    "まちあるき、都市散策": "town_walk",
    "各種体験（手作り、果物狩りなど）": "experiences",
    "スキー・スノボ、マリンスポーツ": "ski_marine",
    "その他スポーツ（ゴルフ、テニスなど）": "other_sports",
    "ドライブ・ツーリング": "drive",
    "友人・親戚を尋ねる": "visit_friends",
    "出張など仕事関係": "business",
}
PURPOSE_COLS = [f"survey_purpose_{k}" for k in PURPOSES.values()]
# NPS ("would you recommend", 0-10) as counts, so any window adds up:
# NPS = (promoters - detractors) / n * 100.
NPS_COLS = ["survey_nps_n", "survey_nps_promoters", "survey_nps_detractors"]
DAILY_COLS = ["survey_response_count", "survey_satisfaction_n", "survey_satisfaction_mean", *NPS_COLS,
              *ORIGIN_COLS, *PURPOSE_COLS]


def daily_summary(responses: pd.DataFrame) -> pd.DataFrame:
    """Response-level rows -> one row per day: responses, satisfaction, NPS
    counts, home region and purpose of visit.

    survey_satisfaction_mean is over the survey_satisfaction_n responses
    that answered 満足度 (weight by it when adding days up). NPS:
    promoters answered 9-10, detractors 0-6, out of survey_nps_n answers.
    A survey without the 満足度, NPS or 都道府県 column (the Ishikawa and
    Toyama providers) gets only the response count.
    """
    g = responses.groupby("date")
    out = g.size().rename("survey_response_count").to_frame()
    if "満足度" in responses.columns:
        score = responses["満足度"].astype(str).str.strip().map(SATISFACTION)
        out["survey_satisfaction_n"] = score.groupby(responses["date"]).count()
        out["survey_satisfaction_mean"] = score.groupby(responses["date"]).mean().round(3)
    if "NPS" in responses.columns:
        nps = pd.to_numeric(responses["NPS"], errors="coerce").where(lambda v: v.between(0, 10))
        out["survey_nps_n"] = nps.groupby(responses["date"]).count()
        out["survey_nps_promoters"] = nps.ge(9).groupby(responses["date"]).sum()
        out["survey_nps_detractors"] = nps.le(6).groupby(responses["date"]).sum()
    if "都道府県" in responses.columns:
        pref = responses["都道府県"].astype(str).str.strip()
        region = pref.map(REGION_OF).where(responses["都道府県"].isna() | pref.isin(REGION_OF), "other")
        counts = pd.crosstab(responses["date"], region)
        for r in [*ORIGIN_REGIONS, "other"]:
            out[f"survey_origin_{r}"] = counts[r] if r in counts else 0
    for col, key in PURPOSES.items():
        if col in responses.columns:
            ticked = pd.to_numeric(responses[col], errors="coerce").fillna(0).eq(1)
            out[f"survey_purpose_{key}"] = ticked.groupby(responses["date"]).sum()
    return out.reset_index()


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

    # Ishikawa and Toyama nodes read their own prefecture's survey instead.
    if survey_cfg.get("provider") == "milli":
        return load_milli_survey(node_cfg)
    if survey_cfg.get("provider") == "toytos":
        return load_toytos_survey(node_cfg)

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
