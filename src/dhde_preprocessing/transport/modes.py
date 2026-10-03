"""
Estimate how many visitors to each site travel by train, bus, their own car
or a rental car (transport_modes.json).

    visitors by mode = the site's visitor estimate x the site's mode share

- **Mode share** comes from the Fukui Prefecture tourism survey
  (code4fukui/fukui-kanko-survey, all.csv): every response answers
  福井県内での交通手段 ("how did you get around in Fukui"), several answers
  allowed, and records the area it was given in (回答エリア). Responses are
  matched to a site with the same survey area ids as dhde-preprocessing-model's
  node configs (config/transport/config.json `mode_share.nodes`), over the last 12 months of
  responses. A respondent who ticked several modes counts as an equal share of
  each; walking only counts when it is the only answer (every trip ends on
  foot). The range is a 95% interval on each share from the sample size.
- **Visitors** come from the app's real_data.json (`visitors_est`, the
  site's own signal scaled to its official 2025 count) for the last 30 days,
  and from that official 2025 count for a year. Fukui Station has no visitor
  estimate, so it gets shares but no counts.

The result is Modelled: survey respondents choose to answer, and visitor
estimates carry their own confidence (shown per site).

Run by scripts/build_transport.py.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

CONFIG = Path(__file__).resolve().parents[3] / "config" / "transport" / "config.json"
MODES_COL = "福井県内での交通手段ALL"
# survey answer -> mode; walking is handled apart (only counts when it's the only answer)
MODE_OF = {
    "自家用車": "own_car",
    "レンタカー": "rental_car",
    "在来線": "train",
    "新幹線": "train",
    "路線バス": "bus",
    "旅行会社ツアーバス": "bus",
    "タクシー": "other",
    "レンタサイクル": "other",
    "その他": "other",
}
WALK = "徒歩"
MODES = ["train", "bus", "own_car", "rental_car", "other"]
BUS_KIND = {"路線バス": "route", "旅行会社ツアーバス": "tour"}
# 交通費 (transport spend for the trip, per person) answer bands -> yen, the band's midpoint
# (open top band: 120,000). "使わない" = spent nothing.
SPEND_MID = {
    "使わない": 0, "1,000円未満": 500, "1,000円以上 3,000円未満": 2000, "3,000円以上 5,000円未満": 4000,
    "5,000円以上 10,000円未満": 7500, "10,000円以上 20,000円未満": 15000, "20,000円以上 30,000円未満": 25000,
    "30,000円以上 40,000円未満": 35000, "40,000円以上 50,000円未満": 45000, "50,000円以上 100,000円未満": 75000,
    "100,000円以上": 120000,
}
Z = 1.96


def split(answer: str) -> dict[str, float]:
    """One response's 福井県内での交通手段 answer -> its share of each mode (sums to 1)."""
    opts = [o.strip() for o in str(answer).split("\n") if o.strip()]
    modes = sorted({MODE_OF[o] for o in opts if o in MODE_OF})
    if not modes:
        modes = ["other"] if WALK in opts else []
    return {m: 1 / len(modes) for m in modes}


def bus_kind(answer: str) -> dict[str, float]:
    """Within the bus share: route bus vs tour bus, same equal-split rule."""
    opts = [o.strip() for o in str(answer).split("\n") if o.strip()]
    modes = {MODE_OF[o] for o in opts if o in MODE_OF}
    kinds = [BUS_KIND[o] for o in opts if o in BUS_KIND]
    if not kinds:
        return {}
    w = 1 / len(modes) / len(kinds)
    out: dict[str, float] = {}
    for k in kinds:
        out[k] = out.get(k, 0) + w
    return out


def shares(answers: pd.Series) -> dict:
    """Mode shares with a 95% interval, from one site's responses."""
    rows = [split(a) for a in answers]
    rows = [r for r in rows if r]
    n = len(rows)
    out = {"n": n, "modes": {}, "bus_kind": {}}
    if n == 0:
        return out
    for m in MODES:
        p = sum(r.get(m, 0) for r in rows) / n
        half = Z * math.sqrt(max(p * (1 - p), 0) / n)
        out["modes"][m] = {"share": round(p, 6), "lo": round(max(0.0, p - half), 6), "hi": round(min(1.0, p + half), 6)}
    for k in ("route", "tour"):
        out["bus_kind"][k] = round(sum(bus_kind(a).get(k, 0) for a in answers if split(a)) / n, 4)
    return out


def area_names(area: pd.DataFrame, ids: list[str]) -> set[str]:
    """Exact area names for the configured 親番号 ids, current and former (as survey.py matches them)."""
    names: set[str] = set()
    for _, r in area[area["親番号"].isin(ids)].iterrows():
        names.add(str(r["エリア名"]).strip())
        if isinstance(r.get("旧エリア名"), str) and r["旧エリア名"].strip():
            names.add(r["旧エリア名"].strip())
    return names


def counts(visitors: float | None, s: dict) -> dict | None:
    if visitors is None or not s["modes"]:
        return None
    return {m: {"visitors": round(visitors * v["share"]), "lo": round(visitors * v["lo"]), "hi": round(visitors * v["hi"])}
            for m, v in s["modes"].items()}



def spend_by_mode(survey: pd.DataFrame) -> dict:
    """Average transport spend per visitor for the whole trip (交通費, band midpoints),
    by how they got around in Fukui, for visitors from outside and inside Fukui.
    Several modes split the respondent equally, as for the shares."""
    df = survey.dropna(subset=["交通費"]).copy()
    df["yen"] = df["交通費"].str.strip().map(SPEND_MID)
    df = df.dropna(subset=["yen"])
    out = {"note": "Survey 交通費: transport spend for the trip per person, band midpoints; includes getting to Fukui.",
           "responses": int(len(df))}
    for group, rows in (("visitors_from_outside", df[df["都道府県"] != "福井県"]), ("fukui_residents", df[df["都道府県"] == "福井県"])):
        acc: dict[str, list[float]] = {m: [0.0, 0.0] for m in MODES}
        for answer, yen in zip(rows[MODES_COL], rows["yen"]):
            for m, w in split(answer).items():
                acc[m][0] += w * yen
                acc[m][1] += w
        out[group] = {m: {"yen": round(v[0] / v[1], -2) if v[1] else None, "n": round(v[1])} for m, v in acc.items()}
    return out


def build(survey_dir: Path | None, real_data: Path, out: Path, *, config: Path = CONFIG) -> bool:
    """Write transport_modes.json to out. False (nothing written) without the survey."""
    if not survey_dir or not (Path(survey_dir) / "all.csv").exists():
        print(f"[SKIP] survey not found at {survey_dir}; transport_modes.json not built")
        return False

    cfg = json.loads(Path(config).read_text(encoding="utf-8"))["mode_share"]
    sd = Path(survey_dir)
    survey = pd.read_csv(sd / "all.csv", dtype=str, usecols=["回答日時", "回答エリア", MODES_COL, "交通費", "都道府県"])
    area = pd.read_csv(sd / "area.csv", dtype=str)
    survey["area"] = survey["回答エリア"].str.strip()
    survey = survey.dropna(subset=[MODES_COL])
    last = pd.to_datetime(survey["回答日時"]).max().date()
    first = last - timedelta(days=365) + timedelta(days=1)
    survey = survey[survey["回答日時"] >= first.isoformat()]

    real = json.loads(Path(real_data).read_text(encoding="utf-8")) if Path(real_data).exists() else {"nodes": {}}
    nodes_out, tot30, totyr = {}, {m: [0, 0, 0] for m in MODES}, {m: [0, 0, 0] for m in MODES}
    v30_total = vyr_total = 0
    period30 = None
    for node, nc in cfg["nodes"].items():
        names = area_names(area, nc["area_ids"])
        s = shares(survey.loc[survey["area"].isin(names), MODES_COL])
        rn = real["nodes"].get(node, {})
        daily = [d for d in rn.get("daily", []) if d.get("visitors_est") is not None]
        # last 30 days of the real file, ending at its shared date (complete days only)
        end = real.get("shared_date") or (daily[-1]["date"] if daily else None)
        last30 = [d for d in daily if end and d["date"] <= end][-30:]
        v30 = round(sum(d["visitors_est"] for d in last30)) if len(last30) >= 25 else None
        if v30 is not None:
            period30 = period30 or [last30[0]["date"], last30[-1]["date"]]
        annual = (rn.get("calibration") or {}).get("official_annual_2025")
        c30, cyr = counts(v30, s), counts(annual, s)
        for tot, c in ((tot30, c30), (totyr, cyr)):
            for m, v in (c or {}).items():
                tot[m][0] += v["visitors"]
                tot[m][1] += v["lo"]
                tot[m][2] += v["hi"]
        if c30:
            v30_total += v30
        if cyr:
            vyr_total += annual
        nodes_out[node] = {
            "survey_areas": sorted(names),
            "responses": s["n"],
            "shares": s["modes"],
            "bus_kind": s["bus_kind"],
            "visitors_30d": v30,
            "by_mode_30d": c30,
            "official_annual_2025": annual,
            "by_mode_year": cyr,
            "visitor_measure": rn.get("measure"),
            "visitor_confidence": (rn.get("calibration") or {}).get("confidence"),
            "note": nc.get("note"),
            "note_ja": nc.get("note_ja"),
        }
        print(f"  {node:14s} n={s['n']:5d} " + " ".join(f"{m} {v['share']:.0%}" for m, v in s["modes"].items())
              + f" | 30d {v30} -> {c30 and {m: v['visitors'] for m, v in c30.items()}}")

    def pack(tot: dict, total: float) -> dict:
        return {"visitors": round(total), "by_mode": {m: {"visitors": v[0], "lo": v[1], "hi": v[2],
                                                          "share": round(v[0] / total, 4) if total else None}
                                                      for m, v in tot.items()}}

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spend_per_visitor": spend_by_mode(survey),
        "status": "modelled",
        "method": "Visitors by mode = each site's visitor estimate x its mode share from the Fukui Prefecture tourism survey "
                  "(how respondents got around in Fukui, last 12 months of responses at that site). Several answers split "
                  "equally; walking counts only when it is the only answer. Ranges: 95% interval on each share from the "
                  "number of responses. Survey respondents choose to answer, so shares can lean towards app users.",
        "survey": {"source": "code4fukui/fukui-kanko-survey (Fukui Prefecture tourism survey), 福井県内での交通手段",
                   "url": "https://github.com/code4fukui/fukui-kanko-survey", "licence": "CC BY 4.0",
                   "from": first.isoformat(), "to": last.isoformat()},
        "modes": MODES,
        "totals": {
            "last_30_days": {"period": period30} | pack(tot30, v30_total),
            "year_2025": {"period": ["2025-01-01", "2025-12-31"]} | pack(totyr, vyr_total),
        },
        "nodes": nodes_out,
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    t = result["totals"]["last_30_days"]
    print(f"[OK] wrote {out}: last 30 days {t['visitors']:,} visitors -> "
          + ", ".join(f"{m} {v['visitors']:,}" for m, v in t["by_mode"].items()))
    return True

