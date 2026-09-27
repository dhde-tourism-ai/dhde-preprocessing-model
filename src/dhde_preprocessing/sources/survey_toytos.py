"""
Visitor survey ingestion for Toyama nodes — TOYTOS web survey.

Selected by `survey.provider: toytos` in a node config; survey.py
dispatches here.

Source: 富山県観光ウェブアンケートデータ on Toyama Prefecture's CKAN
open-data portal (https://ckan.tdcp.pref.toyama.jp/dataset/kanko_data),
CC BY. TOYTOS is Toyama's counterpart to Fukui's FTAS and Ishikawa's
Milli: QR-code posters at tourist sites and accommodations. One row per
response, starting 2025-04-28 — a much shorter history than Fukui or
Ishikawa.

The CSV's download URL is looked up through the CKAN API on every run
(by resource name) rather than hardcoded, since the portal can replace
the file behind a dataset. Unlike Milli, each row already says where it
was answered — 回答場所 is a municipality (富山市, 高岡市, ...) — so nodes
match on it directly via the config's `cities` list.

Responses carry a date only (アンケート回答日, no time), so double
submissions can't be told apart from two real visitors except when the
whole row is identical; only those exact duplicates are dropped.

Response counts are NOT comparable with Fukui's or Ishikawa's survey
counts (different questionnaire, different poster placement). Compare
trends within a prefecture, not levels across.
"""
from __future__ import annotations

import pandas as pd
import requests

from ..validation import SourceReport, validate_daily
from .remote_csv import fetch_csv

CKAN_PACKAGE_SHOW = "https://ckan.tdcp.pref.toyama.jp/api/3/action/package_show"


def _resource_url(dataset: str, resource_name: str) -> str:
    resp = requests.get(CKAN_PACKAGE_SHOW, params={"id": dataset}, timeout=60)
    resp.raise_for_status()
    for r in resp.json()["result"]["resources"]:
        if r.get("name") == resource_name:
            return r["url"]
    raise ValueError(f"no resource named {resource_name!r} in CKAN dataset {dataset!r}")


def load_toytos_survey(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    survey_cfg = node_cfg["sources"]["survey"]
    cities = survey_cfg["cities"]

    try:
        try:
            url = _resource_url(survey_cfg["ckan_dataset"], survey_cfg["resource_name"])
        except Exception:  # noqa: BLE001 - CKAN down: fetch_csv below still falls back to cache
            url = survey_cfg["fallback_url"]
        responses, note = fetch_csv(url, "toytos_survey", encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001 - a fetch failure must not crash the whole run
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=[f"TOYTOS fetch failed and no cache: {e!r}"])

    df = responses[responses["回答場所"].astype(str).str.strip().isin(cities)].copy()
    notes = [note, f"matched on 回答場所 (where the survey was answered) in {cities}"]
    if df.empty:
        return None, SourceReport(source="survey", node_key=node_key, status="error",
                                   notes=notes + ["zero responses matched — check the cities list against 回答場所 values"])

    df["date"] = pd.to_datetime(df["アンケート回答日"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"])
    n_before = len(df)
    df = df.drop_duplicates().sort_values("date").reset_index(drop=True)
    if n_before - len(df):
        notes.append(f"{n_before - len(df)} exact duplicate row(s) dropped")

    report = validate_daily(df, source="survey", node_key=node_key, notes=notes)
    return df, report
