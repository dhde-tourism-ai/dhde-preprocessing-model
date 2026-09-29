"""
Integrated training dataset: every node's master table stacked into one
long table (one row per node per day) with the same columns for every
node, ready for the modelling stage to read as-is.

Input is what scripts/build_node.py writes to output/: each node's
`{node}_master.parquet` plus `{node}_coverage_report.json`. The report
says which source produced which column, which is how columns get their
source prefix here (`precip` -> `weather_precip`, `occ` -> `hotel_occ`).

What this step changes, and why (each one is a way a model would
otherwise learn something that isn't true):

- **One calendar per node.** Every day in the window gets a row, so a
  missing day is an explicit row with missing values, not an absent row.
- **Two tables.** The full one runs to the last date any source has,
  future hotel and museum bookings included (the dashboard needs them).
  The training one stops yesterday (JST): today is still partial, and
  later rows are bookings so far, values not known on that date that
  would leak into training.
- **Response counts are 0, not missing, on days without responses.**
  `survey_response_count` and `proxy_survey_count` only exist on days
  someone answered; between a count's first and last observed day, a day
  with no row is a real 0.
- **RSI keeps one level per node.** rsi.py fills a town's untracked days
  with the prefecture total (`rsi_level`). Those two differ by orders of
  magnitude, so for nodes that have a town file, prefecture-filled days
  become missing here. Fukui Station only has the total, so it keeps it.
- **Traffic: partial or dead-counter days are missing.** A day with
  fewer than 24 observed hours under-counts, and a whole day of 0 on a
  national road means the counter was down (Fukui Station's CCTV counter
  reads 0 every hour from 2026-08-09).
- **Camera-system outages are missing at every camera.** On a day when
  every people camera is down at once (2025-09-26 -> 28), Rainbow Line's
  vehicle gates read 0 too, but that 0 is the outage, not an empty car
  park. camera.py can't tell on its own: those gates really see no cars
  on many days (mostly Jan-Feb 2026, likely snow closures).
- **Flags are 0/1 integers** (hotel `is_stale`, `was_imputed`, ...), and
  every source gets a `has_<source>` column saying whether that row has
  any value from it, so "no data" is never read as "zero".
- **Node context columns** say how local each signal is: `hotel_scope`
  (`regional` rows are the same echizen-coast feed at Tojinbo, Katsuyama
  and Eiheiji, not three independent observations) and `weather_station`.

Left out on purpose: the camera Face.csv demographic columns (a small,
biased sample of each count, see camera.py; still in the master tables)
and the response-level survey table (its own parquet per node).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jpholiday
import pandas as pd

from .config import load_node_config
from .sources.survey import DAILY_COLS as SURVEY_COLS, ORIGIN_COLS, PURPOSE_COLS

REGIONS = {
    "fukui": ["fukui_station", "tojinbo", "katsuyama", "rainbow_line", "awara_onsen", "eiheiji"],
    "kyoto": ["kyoto_station", "arashiyama", "fushimi_inari", "higashiyama"],
    "osaka": ["osaka_station", "namba", "osaka_castle", "usj"],
}
FUKUI_NODES = REGIONS["fukui"]
DEFAULT_START = "2024-12-01"  # weather start_date in every Fukui config; hotel feeds start within days

# Column prefix per source. Columns that already carry a source's own
# prefix (road_*, proxy_*, rsi_level) keep their name.
SOURCE_PREFIX = {
    "camera": "camera_",
    "weather": "weather_",
    "rsi": "rsi_",
    "hotel": "hotel_",
    "traffic": "traffic_",
    "info_desk": "desk_",
    "monthly_visitors": "",
    "rakuten": "rakuten_",
    "road_congestion": "road_",
    "footfall_proxy": "proxy_",
    "visitor_reservation": "attraction_",
    "google_reviews": "reviews_",
}
FRONT_COLUMNS = ["date", "node_key", "day_of_week", "is_holiday", "hotel_scope", "weather_station"]

# The table always has exactly these columns, in this order, for every
# region, so its shape doesn't change when a source fails on one run or
# starts producing data (road_* stays empty until the TomTom collector has
# history), and Fukui, Kyoto and Osaka tables line up. An expected column
# with no data at all is a warning in the report, not a missing column.
EXPECTED_COLUMNS = FRONT_COLUMNS + [
    "has_camera", "has_footfall_proxy", "has_google_reviews", "has_hotel", "has_monthly_visitors", "has_rakuten",
    "has_road_congestion", "has_rsi", "has_survey", "has_traffic", "has_visitor_reservation", "has_weather",
    "camera_count", "camera_gate1_vehicle_count", "camera_gate2_vehicle_count",
    "weather_precip", "weather_temp", "weather_wind", "weather_sun", "weather_humidity", "weather_snow_depth",
    "rsi_level", "rsi_map_views", "rsi_search_views", "rsi_directions", "rsi_call_clicks", "rsi_website_clicks",
    "rsi_average_rating", "rsi_review_count_change",
    "rsi_review_count_by_rating_1", "rsi_review_count_by_rating_2", "rsi_review_count_by_rating_3",
    "rsi_review_count_by_rating_4", "rsi_review_count_by_rating_5",
    "hotel_n_room", "hotel_n_people", "hotel_amount_fee", "hotel_n_stay", "hotel_n_reserve", "hotel_capacity",
    "hotel_occ", "hotel_adr", "hotel_revpar", "hotel_rev_per_guest", "hotel_n_people_lead7", "hotel_lead_used",
    "hotel_is_stale", "hotel_from_bad_snapshot", "hotel_was_imputed", "hotel_neg_fee_adjustment",
    "hotel_n_reserve_reliable",
    "traffic_volume_total", "traffic_volume_upstream", "traffic_volume_downstream", "traffic_hours_observed",
    "road_congestion", "road_relative_speed_mean", "road_relative_speed_min", "road_snapshots",
    *SURVEY_COLS,
    "proxy_camera_count", "proxy_survey_count",
    "attraction_reserved_visitors", "attraction_reserved_fee", "attraction_reserved_visitors_lead7",
    "attraction_from_earlier_snapshot", "attraction_bookings_final",
    "city_visitors_month", "pref_visitors_month",
    "rakuten_vacant_share_d1", "rakuten_min_charge_d1", "rakuten_vacant_share_d7", "rakuten_min_charge_d7",
    "rakuten_vacant_share_d30", "rakuten_min_charge_d30",
    "reviews_new", "reviews_stars_mean", "reviews_stars_1", "reviews_stars_2", "reviews_stars_3",
    "reviews_stars_4", "reviews_stars_5", "reviews_with_text", "reviews_foreign",
    *[f"reviews_lang_{g}{s}" for g in ("ja", "en", "zh_hant", "zh_hans", "ko", "other")
      for s in ("", "_stars_mean")],
    "reviews_rating_total", "reviews_count_total",
]

SURVEY_COUNT = "survey_response_count"
RESPONSE_COUNTS = [SURVEY_COUNT, "survey_satisfaction_n", *ORIGIN_COLS, *PURPOSE_COLS, "proxy_survey_count"]
# Describe a row rather than measure anything, so they don't count towards has_<source>.
NOT_A_VALUE = {"traffic_hours_observed", "rsi_level"}
MIN_TRAFFIC_HOURS = 24


def jst_yesterday() -> pd.Timestamp:
    return pd.Timestamp((datetime.now(timezone(timedelta(hours=9))) - timedelta(days=1)).date())


def column_sources(report: dict) -> dict[str, str]:
    """{master column: source name}, read from a node's coverage report."""
    owner = {}
    for src in report["sources"]:
        if src["source"] in SOURCE_PREFIX:
            for col in src.get("null_rates", {}):
                owner[col] = src["source"]
    return owner


def _prefixed(col: str, source: str) -> str:
    prefix = SOURCE_PREFIX[source]
    return col if col.startswith(prefix) else f"{prefix}{col}"


def _as_flag(s: pd.Series) -> pd.Series:
    return s.map(lambda v: pd.NA if pd.isna(v) else int(bool(v))).astype("Int8")


def integrate_node(master: pd.DataFrame, report: dict, node_cfg: dict,
                   start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    """One node's master table -> its rows of the integrated table, plus notes."""
    node_key = node_cfg["node_key"]
    if not master["date"].is_unique:
        raise ValueError(f"{node_key}: master table has repeated dates; rebuild it with scripts/build_node.py")
    notes: list[str] = []

    owner = column_sources(report)
    keep = {c: _prefixed(c, owner[c]) for c in master.columns
            if c in owner and not (owner[c] == "camera" and "face_" in c)}
    for col in SURVEY_COLS:
        if col in master.columns:
            keep[col] = col
    df = master.set_index("date")[list(keep)].rename(columns=keep)
    src_of = {new: owner.get(old, "survey") for old, new in keep.items()}

    df = df.reindex(pd.date_range(start, end, name="date"))

    for col in RESPONSE_COUNTS:
        if col in master.columns and master[col].notna().any():
            seen = master.loc[master[col].notna(), "date"]
            inside = (df.index >= seen.min()) & (df.index <= seen.max())
            df.loc[inside, col] = df.loc[inside, col].fillna(0)

    rsi_cfg = node_cfg["sources"].get("rsi", {})
    if rsi_cfg.get("area_name") and "rsi_level" in df.columns:
        filled = df["rsi_level"] == "prefecture"
        rsi_cols = [c for c, s in src_of.items() if s == "rsi" and c != "rsi_level"]
        df.loc[filled, rsi_cols] = float("nan")
        df.loc[filled, "rsi_level"] = None
        notes.append(f"rsi: {int(filled.sum())} prefecture-filled day(s) set to missing (node has a town file)")

    if "traffic_hours_observed" in df.columns:
        vol_cols = [c for c, s in src_of.items() if s == "traffic" and c != "traffic_hours_observed"]
        bad = (df["traffic_hours_observed"] < MIN_TRAFFIC_HOURS) | (df["traffic_volume_total"] == 0)
        df.loc[bad, vol_cols] = float("nan")
        notes.append(f"traffic: {int(bad.sum())} partial or all-zero day(s) set to missing")

    for col in df.columns:
        values = set(df[col].dropna().unique()) if df[col].dtype in (object, bool, "boolean") else None
        if values is not None and values <= {True, False}:
            df[col] = _as_flag(df[col])
    for col in ("hotel_from_bad_snapshot", "hotel_neg_fee_adjustment", "hotel_n_reserve_reliable"):
        if col in df.columns:
            df[col] = _as_flag(df[col])

    flags = {}
    for source in dict.fromkeys(src_of.values()):
        cols = [c for c, s in src_of.items() if s == source and c not in NOT_A_VALUE]
        flags[f"has_{source}"] = df[cols].notna().any(axis=1).astype("Int8")

    hotel_cfg = node_cfg["sources"].get("hotel", {})
    weather_cfg = node_cfg["sources"].get("weather", {})
    context = pd.DataFrame({
        "node_key": node_key,
        "day_of_week": df.index.dayofweek,
        "is_holiday": [int(jpholiday.is_holiday(d)) for d in df.index],
        "hotel_scope": hotel_cfg.get("scope") if hotel_cfg.get("enabled") else None,
        "weather_station": weather_cfg.get("station_name") if weather_cfg.get("enabled") else None,
    }, index=df.index)
    out = pd.concat([context, pd.DataFrame(flags), df], axis=1).reset_index()
    out["is_holiday"] = out["is_holiday"].astype("Int8")
    return out, notes


def blank_camera_outages(table: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Blank the other cameras' zeros on days when all people cameras are down.

    Needs at least two people cameras, and only looks at days inside all
    of their date ranges. A node is only blanked when every count it has
    that day is 0: any non-zero reading shows its sensors were up (e.g.
    2025-12-27, when Fukui Station was down for New Year and Tojinbo's
    file skips the day, but Rainbow Line gate 1 counted 75 cars).
    """
    if "camera_count" not in table.columns:
        return table, []
    people = table.pivot(index="date", columns="node_key", values="camera_count").dropna(axis=1, how="all")
    if people.shape[1] < 2:
        return table, []
    first = max(people[c].first_valid_index() for c in people)
    last = min(people[c].last_valid_index() for c in people)
    span = people.loc[first:last]
    down = span.index[span.isna().all(axis=1)]
    cam_cols = [c for c in table.columns if c.startswith("camera_")]
    count_cols = [c for c in cam_cols if c.endswith("count")]
    all_zero = table[count_cols].fillna(0).eq(0).all(axis=1) & table[count_cols].notna().any(axis=1)
    blank = table["date"].isin(down) & all_zero
    table.loc[blank, cam_cols] = float("nan")
    table["has_camera"] = table[cam_cols].notna().any(axis=1).astype("Int8")
    return table, sorted({str(d.date()) for d in table.loc[blank, "date"]})


def build_integrated(nodes: list[str], input_dir: str = "output", start: str = DEFAULT_START,
                     end: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (full table, training table, summary). `end` is the training
    table's last day (default: yesterday, JST); the full table runs to the
    last date any node's master table has."""
    base = Path(input_dir)
    inputs = {}
    for node_key in nodes:
        master_path = base / f"{node_key}_master.parquet"
        if not master_path.exists():
            raise FileNotFoundError(f"{master_path} not found; run scripts/build_node.py --node {node_key} first")
        report = json.loads((base / f"{node_key}_coverage_report.json").read_text(encoding="utf-8"))
        inputs[node_key] = (pd.read_parquet(master_path), report)

    start_ts = pd.Timestamp(start)
    train_end = pd.Timestamp(end) if end else jst_yesterday()
    full_end = max(max(m["date"].max() for m, _ in inputs.values()), train_end)
    summary = {"start": str(start_ts.date()), "train_end": str(train_end.date()),
               "full_end": str(full_end.date()), "nodes": {}, "warnings": []}
    frames = []
    for node_key, (master, report) in inputs.items():
        rows, notes = integrate_node(master, report, load_node_config(node_key), start_ts, full_end)
        frames.append(rows)
        summary["nodes"][node_key] = {"notes": notes}

    table = pd.concat(frames, ignore_index=True)
    unexpected = [c for c in table.columns if c not in EXPECTED_COLUMNS]
    if unexpected:
        summary["warnings"].append(f"left out, not in EXPECTED_COLUMNS: {', '.join(unexpected)}")
    table = table.reindex(columns=EXPECTED_COLUMNS)
    has = [c for c in EXPECTED_COLUMNS if c.startswith("has_")]
    table[has] = table[has].fillna(0).astype("Int8")  # a node without the source at all
    table, outage_days = blank_camera_outages(table)
    summary["camera_outage_days"] = outage_days

    train = table[table["date"] <= train_end].reset_index(drop=True)
    empty = [c for c in EXPECTED_COLUMNS if c not in FRONT_COLUMNS and not c.startswith("has_")
             and train[c].isna().all()]
    if empty:
        summary["warnings"].append(f"no data in the training table for: {', '.join(empty)}")
    for node_key, rows in train.groupby("node_key"):
        summary["nodes"][node_key]["train_non_null"] = {
            c: int(rows[c].notna().sum()) for c in train.columns if c not in FRONT_COLUMNS}
    summary["full_rows"], summary["train_rows"] = len(table), len(train)
    return table, train, summary


def write_integrated(table: pd.DataFrame, train: pd.DataFrame, summary: dict, output_dir: str = "output",
                     name: str = "integrated_fukui") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for df, stem, last in ((table, name, summary["full_end"]), (train, f"{name}_train", summary["train_end"])):
        df.to_parquet(out / f"{stem}.parquet", index=False)
        df.to_csv(out / f"{stem}.csv", index=False)
        print(f"[OK] wrote {out / f'{stem}.parquet'} ({len(df)} rows, {df.shape[1]} columns, "
              f"{summary['start']} -> {last})")
    with open(out / f"{name}_report.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
