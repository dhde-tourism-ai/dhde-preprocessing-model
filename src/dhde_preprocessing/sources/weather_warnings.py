"""
JMA weather warnings and advisories in force at each node, kept as a history.

Since the end of May 2026 JMA publishes warnings in its new level system
(レベル２注意報 … レベル５特別警報) at `bosai/warning/data/r8/{office}.json`;
the old `bosai/warning/data/warning/{office}.json` stopped updating on
28 May 2026. The r8 file is a list of reports, one per hazard family (rain,
landslide, wind, waves, fog…), each giving every municipality's current
state. A warning is in force when its status isn't 解除 (lifted).

Each node's config names its JMA office and municipality codes (class20,
from bosai/common/const/area.json):

    jma_warning:
      office: "180000"        # 福井県
      areas: ["1821000"]      # 坂井市

Like the other live sources, JMA keeps no history, so each run (with the
hourly collector) updates `{live_data_root}/weather_warnings/{node_key}.csv`:
one row per warning spell, with when it was first and last seen in force
and `active` while it still is. The collector runs 06:15 to 21:15 JST, so a
warning issued and lifted overnight is never seen, and one lifted overnight
shows as ending at the last daytime run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

from ..config import read_csv_if_exists, resolve_live_path, write_csv
from ..validation import SourceReport, unavailable_report

URL = "https://www.jma.go.jp/bosai/warning/data/r8/{office}.json"
HISTORY_DIR = "weather_warnings"
COLS = ["code", "hazard", "level", "name_ja", "name_en", "first_seen", "last_seen", "report_datetime", "active"]
NOT_IN_FORCE = {"解除", "発表警報・注意報はなし"}

# code → (hazard, level), JMA's r8 table for municipality (class20) warnings,
# from the bosai/warning page's own code map (river flood forecasts use 20-22
# differently; they aren't in these reports).
CODES = {
    "10": ("rain", 2), "03": ("rain", 3), "43": ("rain", 4), "33": ("rain", 5),
    "29": ("landslide", 2), "09": ("landslide", 3), "49": ("landslide", 4), "39": ("landslide", 5),
    "19": ("tide", 2), "08": ("tide", 3), "48": ("tide", 4), "38": ("tide", 5),
    "15": ("wind", 2), "05": ("wind", 3), "35": ("wind", 5),
    "13": ("wind_snow", 2), "02": ("wind_snow", 3), "32": ("wind_snow", 5),
    "12": ("snow", 2), "06": ("snow", 3), "36": ("snow", 5),
    "16": ("wave", 2), "07": ("wave", 3), "37": ("wave", 5),
    "14": ("thunder", 2), "17": ("snow_melting", 2), "20": ("fog", 2), "21": ("dry", 2),
    "22": ("avalanche", 2), "23": ("cold", 2), "24": ("frost", 2), "25": ("ice_accretion", 2),
    "26": ("snow_accretion", 2),
}
HAZARD_NAME = {  # hazard → (ja, en); wind and wind_snow change name above level 2
    "rain": ("大雨", "Heavy rain"), "landslide": ("土砂災害", "Landslide"), "tide": ("高潮", "Storm surge"),
    "wind": ("強風", "Strong wind"), "wind_snow": ("風雪", "Wind and snow"), "snow": ("大雪", "Heavy snow"),
    "wave": ("波浪", "High waves"), "thunder": ("雷", "Thunderstorm"), "snow_melting": ("融雪", "Snowmelt"),
    "fog": ("濃霧", "Dense fog"), "dry": ("乾燥", "Dry air"), "avalanche": ("なだれ", "Avalanche"),
    "cold": ("低温", "Low temperature"), "frost": ("霜", "Frost"), "ice_accretion": ("着氷", "Ice accretion"),
    "snow_accretion": ("着雪", "Snow accretion"),
}
STRONGER_NAME = {"wind": ("暴風", "Storm"), "wind_snow": ("暴風雪", "Snowstorm")}
LEVEL_NAME = {2: ("注意報", "advisory"), 3: ("警報", "warning"), 4: ("危険警報", "danger warning"),
              5: ("特別警報", "emergency warning")}
LEVELLED = {"rain", "landslide", "tide"}  # shown with their レベル number


def name_of(hazard: str, level: int) -> tuple[str, str]:
    ja, en = STRONGER_NAME[hazard] if level >= 3 and hazard in STRONGER_NAME else HAZARD_NAME[hazard]
    lja, len_ = LEVEL_NAME[level]
    prefix = f"レベル{'０１２３４５'[level]}" if hazard in LEVELLED else ""
    return f"{prefix}{ja}{lja}", f"{en} {len_}" + (f" (level {level})" if hazard in LEVELLED else "")


def in_force(reports: list, areas: list[str]) -> tuple[dict[str, str], set[str], set[str]]:
    """({code: report datetime} for the warnings in force in any of `areas`,
    the areas the file lists, codes in force that CODES doesn't know)."""
    if not isinstance(reports, list):
        raise ValueError(f"expected a list of reports, got {type(reports).__name__}")
    out: dict[str, str] = {}
    seen: set[str] = set()
    unknown: set[str] = set()
    for rep in reports:
        for item in (rep.get("warning") or {}).get("class20Items") or []:
            if item.get("areaCode") not in areas:
                continue
            seen.add(item["areaCode"])
            for k in item.get("kinds") or []:
                code = k.get("code")
                if not code or k.get("status") in NOT_IN_FORCE:
                    continue
                if code in CODES:
                    out[code] = max(out.get(code, ""), rep.get("reportDatetime") or "")
                else:
                    unknown.add(code)
    return out, seen, unknown


def update(saved: pd.DataFrame, now_force: dict[str, str], now: str) -> pd.DataFrame:
    """Extend the spells still in force, open new ones, close the ones that ended."""
    rows = saved.to_dict("records")
    open_ = {r["code"]: r for r in rows if r["active"]}
    for code, report in now_force.items():
        if code in open_:
            open_[code].update(last_seen=now, report_datetime=report)
        else:
            hazard, level = CODES[code]
            ja, en = name_of(hazard, level)
            rows.append({"code": code, "hazard": hazard, "level": level, "name_ja": ja, "name_en": en,
                         "first_seen": now, "last_seen": now, "report_datetime": report, "active": True})
    for code, r in open_.items():
        if code not in now_force:
            r["active"] = False
    return pd.DataFrame(rows, columns=COLS)


def collect(node_cfg: dict, now: datetime | None = None,
            fetched: dict[str, list] | None = None) -> tuple[pd.DataFrame | None, SourceReport]:
    """`fetched` caches each office's file within one collector run (nodes share offices)."""
    node_key = node_cfg["node_key"]
    cfg = node_cfg["sources"].get("jma_warning", {})
    if not cfg.get("office") or not cfg.get("areas"):
        return None, unavailable_report("weather_warnings", node_key, "no JMA office/areas configured for this node")

    now_s = (now or datetime.now(timezone.utc)).isoformat(timespec="minutes")
    path = resolve_live_path(f"{HISTORY_DIR}/{node_key}.csv")
    try:
        saved = read_csv_if_exists(path, dtype={"code": str})
    except pd.errors.EmptyDataError:
        saved = None
    if saved is None:
        saved = pd.DataFrame(columns=COLS)
    saved["active"] = saved["active"].astype(str).str.lower() == "true"

    fetched = {} if fetched is None else fetched
    office = cfg["office"]
    try:
        if office not in fetched:
            resp = requests.get(URL.format(office=office), timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(f"JMA returned HTTP {resp.status_code}")
            fetched[office] = resp.json()
        areas = [str(a) for a in cfg["areas"]]
        now_force, seen, unknown = in_force(fetched[office], areas)
        if not seen:
            # Not "none in force": the file doesn't cover this node, so closing its open spells would be wrong.
            raise RuntimeError(f"none of the node's areas ({', '.join(areas)}) are in JMA's file")
    except (requests.RequestException, RuntimeError, ValueError, AttributeError, TypeError) as e:
        msg = str(e) if isinstance(e, (RuntimeError, ValueError)) else type(e).__name__
        return (saved if not saved.empty else None), SourceReport(
            source="weather_warnings", node_key=node_key, status="error",
            notes=[f"JMA warnings unreadable ({msg}): history unchanged"])

    table = update(saved, now_force, now_s)
    write_csv(table, path)
    active = table[table["active"].astype(bool)]  # an empty object column would select columns
    notes = [f"in force now: {', '.join(active['name_en']) or 'none'}", f"{len(table)} warning spell(s) on record"]
    if unknown:
        notes.append(f"in force but not in the code table, so not saved: {', '.join(sorted(unknown))}")
    return table, SourceReport(source="weather_warnings", node_key=node_key, status="ok", row_count=len(table), notes=notes)
