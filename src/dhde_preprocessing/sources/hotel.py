"""
Hotel reservation / demand ingestion — cleaned via glitch detection and
gap filling, not just a raw day-of snapshot read.

Source layout: code4fukui/{repo}/data/{yyyy-mm-dd}.csv, one row per
date_visit per snapshot, plus {repo}/latest_hotel.csv (per-hotel room
count + the date each hotel's data starts being included, used to
compute total capacity on any given date_visit).

Two repos currently feed this, both same format:
- code4fukui/fukui-station-kanko-reservation — Fukui-Station-specific,
  3 hotels, 585 rooms. Used for the fukui_station node.
- code4fukui/echizen-coast-kanko-reservation — regional (Echizen Coast),
  used as the general demand signal for the other 3 nodes (see README —
  the old dashboard pipeline isolated hotel LAG FEATURES to Fukui
  Station specifically, but that's a modeling-stage decision, out of
  scope here).

THE CLEANING METHODOLOGY BELOW WAS NOT DESIGNED HERE — it's ported
faithfully from a teammate's data-quality analysis of the Fukui Station
repo (Colab notebook + written methodology doc), which found the naive
"just read the day-of snapshot" approach (this module's first version)
misses real problems: failed data pulls, frozen/stale snapshots, values
exceeding hotel capacity, negative revenue (refunds), impossible room
prices, and one-day glitches that revert. See the PR this was added in
for the full methodology writeup. Default parameters below are hers,
validated by her own strict/default/loose sensitivity check (settings
barely move the results — average monthly difference 0.16 occupancy
points, 7 JPY ADR).

Key concept — "booking curve": each date_visit appears in ~90 snapshots
(the file dated at date_visit itself, and roughly the 89 before it,
since snapshots are forward-looking, see the module's `hotel.py` sibling
docstring history / PR discussion for how that forward-window works).
`lead_time = date_visit - snapshot_date` days. Reading a date_visit's
row across all its snapshots in lead_time order shows how its bookings
accumulated over time — most of the cleaning below works along these
curves, not on any single snapshot in isolation.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import jpholiday
import numpy as np
import pandas as pd

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

CORE = ["n_room", "n_people", "amount_fee"]      # cleaned together as one group
SECONDARY = ["n_stay", "n_reserve"]              # low-trust columns, cleaned separately
METRICS = CORE + SECONDARY

DEFAULT_PARAMS = dict(
    MAX_LEAD=89,        # keep 0..89 days ahead (every snapshot covers this)
    # ADR_MIN/ADR_MAX are NOT set here — they're derived per repo from that
    # repo's own price distribution (see _derive_adr_bounds). Her original
    # analysis hardcoded 3,000-30,000 JPY, validated against Fukui Station's
    # prices specifically; applied as-is to the regional Echizen Coast
    # repo, that fixed range excluded 87% of rows as "out of range" (its
    # median price is ~64,500 JPY — a different, pricier market, not bad
    # data) versus ~2% for a data-driven bound on either repo. Ported the
    # validated approach, not the specific numbers, since those numbers
    # were tied to which dataset she was looking at.
    ZERO_LEAD=30,       # all-zero rows further ahead than this = not loaded yet
    SNAP_WIN=5,         # snapshots on each side used to judge a whole snapshot
    SNAP_DEV=0.25,      # a value is "off" if > 25% from its neighbours' median
    SNAP_SHARE=0.50,    # a snapshot is bad if > 50% of its values are off
    BUMP_ABS=8,         # small reverting bump: at least this many rooms ...
    BUMP_REL=0.05,      # ... and at least 5% of the level
    SPIKE_WIN=5,        # rolling-median glitch check window (snapshots)
    SPIKE_REL=0.30,
    SPIKE_ABS={"n_room": 30, "n_people": 30, "amount_fee": 300_000,
               "n_stay": 30, "n_reserve": 30},
    MAX_GAP=7,          # only fill gaps of at most this many snapshots
    RESERVE_LEAD=60,    # n_reserve is trusted only this close to the stay
)


# ── Loading ───────────────────────────────────────────────────────────────

def _derive_adr_bounds(raw: pd.DataFrame, low_pct: float = 0.01, high_pct: float = 0.99) -> tuple[float, float]:
    """Data-driven replacement for a hardcoded ADR_MIN/ADR_MAX — see
    DEFAULT_PARAMS comment for why this can't be a fixed constant shared
    across repos with different price tiers."""
    adr = raw["amount_fee"] / raw["n_room"].replace(0, np.nan)
    adr = adr[adr.notna() & (adr >= 0)]
    if adr.empty:
        return 0.0, float("inf")
    return float(adr.quantile(low_pct)), float(adr.quantile(high_pct))


def _load_capacity(repo_root: str) -> pd.DataFrame:
    hotels = pd.read_csv(f"{repo_root}/latest_hotel.csv", encoding="utf-8-sig")
    hotels["create_file_begin_date"] = pd.to_datetime(hotels["create_file_begin_date"])
    return hotels


def _load_all_snapshots(data_dir: str) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(f"{data_dir}/*.csv")):
        name = os.path.basename(path)[:10]
        try:
            snap = pd.to_datetime(name, format="%Y-%m-%d")
        except ValueError:
            continue  # not a {yyyy-mm-dd}.csv file
        d = pd.read_csv(path, encoding="utf-8-sig")
        d.columns = d.columns.str.strip()
        d["snapshot_date"] = snap
        frames.append(d)

    if not frames:
        return pd.DataFrame(columns=["date_visit", *METRICS, "snapshot_date", "lead_time"])

    raw = pd.concat(frames, ignore_index=True)
    raw["date_visit"] = pd.to_datetime(raw["date_visit"], errors="coerce")
    raw["lead_time"] = (raw["date_visit"] - raw["snapshot_date"]).dt.days
    return raw


# ── Cleaning pipeline (ported — see module docstring) ──────────────────────

def _blank(df: pd.DataFrame, mask: pd.Series, columns: list[str], reason: str, log: list[dict]) -> None:
    for c in columns:
        hit = df.loc[mask & df[c].notna(), ["snapshot_date", "date_visit", c]]
        log.extend({"snapshot_date": a, "date_visit": b, "column": c,
                     "old_value": v, "reason": reason} for a, b, v in hit.itertuples(index=False))
    df.loc[mask, columns] = np.nan


def _step_basic(raw: pd.DataFrame, P: dict) -> pd.DataFrame:
    df = raw.copy()
    for c in METRICS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date_visit"])
    df = df.drop_duplicates(subset=["snapshot_date", "date_visit"], keep="last")
    df = df[df["lead_time"].between(0, P["MAX_LEAD"])].copy()
    return df


def _step_snapshots(df: pd.DataFrame, P: dict, log: list[dict]) -> tuple[pd.DataFrame, dict]:
    na_share = df.groupby("snapshot_date")["n_room"].apply(lambda s: s.isna().mean())
    empty = set(na_share[na_share > 0.5].index)

    wide = df.pivot_table(index="date_visit", columns="snapshot_date", values="n_room")
    snaps, vals, K = wide.columns, wide.values, P["SNAP_WIN"]
    off = set()
    for i, s in enumerate(snaps):
        nb = [j for j in range(i - K, i + K + 1) if j != i and 0 <= j < len(snaps)]
        ref, cur = np.nanmedian(vals[:, nb], axis=1), vals[:, i]
        m = ~np.isnan(cur) & ~np.isnan(ref) & (ref >= 20)
        if m.sum() >= 30 and (np.abs(cur[m] - ref[m]) / ref[m] > P["SNAP_DEV"]).mean() > P["SNAP_SHARE"]:
            off.add(s)

    fee = df.pivot_table(index="date_visit", columns="snapshot_date", values="amount_fee")
    stale = set()
    for i in range(1, len(snaps)):
        a, b = wide[snaps[i]], wide[snaps[i - 1]]
        m = a.notna() & b.notna()
        if m.sum() > 30 and (a[m] == b[m]).all() and (fee[snaps[i]][m] == fee[snaps[i - 1]][m]).all():
            stale.add(snaps[i])

    bad = empty | off
    df["from_bad_snapshot"] = df["snapshot_date"].isin(bad).astype(int)
    _blank(df, df["snapshot_date"].isin(bad), METRICS, "bad snapshot", log)
    df["is_stale"] = df["snapshot_date"].isin(stale)

    stats = {"failed_pulls": len(empty), "off_level_snapshots": len(off), "frozen_snapshots": len(stale)}
    return df, stats


def _step_rules(df: pd.DataFrame, P: dict, log: list[dict], hotels: pd.DataFrame) -> pd.DataFrame:
    cap = {d: hotels.loc[hotels["create_file_begin_date"] <= d, "nrooms"].sum()
           for d in df["date_visit"].drop_duplicates()}
    df["capacity"] = df["date_visit"].map(cap)

    df["amount_fee_raw"] = df["amount_fee"]
    df["neg_fee_adjustment"] = (df["amount_fee"] < 0).astype(int)
    adr = df["amount_fee"] / df["n_room"].replace(0, np.nan)
    rules = {
        "negative revenue": df["amount_fee"] < 0,
        "rooms exceed capacity": df["n_room"] > df["capacity"],
        "fewer guests than rooms": (df["n_room"] > 0) & (df["n_people"] < df["n_room"]),
        "ADR out of range": adr.notna() & (df["amount_fee"] >= 0)
            & ((adr < P["ADR_MIN"]) | (adr > P["ADR_MAX"])),
        "all zero far ahead": (df[CORE].fillna(0).sum(axis=1) == 0) & (df["lead_time"] > P["ZERO_LEAD"]),
    }
    for reason, mask in rules.items():
        if reason == "all zero far ahead":
            cols = CORE + SECONDARY
        elif reason == "negative revenue":
            cols = ["amount_fee"]
        else:
            cols = CORE
        _blank(df, mask, cols, reason, log)

    df["n_reserve_reliable"] = (df["lead_time"] <= P["RESERVE_LEAD"]).astype(int)
    _blank(df, (df["n_reserve"] == 0) & (df["n_room"] > 0), SECONDARY,
           "0 reservations but rooms booked", log)
    return df


def _neighbours(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """previous and next known value along a booking curve"""
    return s.ffill().shift(1), s.bfill().shift(-1)


def _step_glitches(df: pd.DataFrame, P: dict, log: list[dict]) -> pd.DataFrame:
    df = df.sort_values(["date_visit", "snapshot_date"]).reset_index(drop=True)
    g = df.groupby("date_visit")

    prev = g["n_room"].transform(lambda s: _neighbours(s)[0])
    nxt = g["n_room"].transform(lambda s: _neighbours(s)[1])
    d1, d2 = df["n_room"] - prev, df["n_room"] - nxt
    size = np.minimum(d1.abs(), d2.abs())
    level = (prev + nxt) / 2
    bump = (np.sign(d1) == np.sign(d2)) & (size >= P["BUMP_ABS"]) \
        & (size >= P["BUMP_REL"] * level) & ((prev - nxt).abs() < 0.5 * size)
    _blank(df, bump.fillna(False), CORE, "reverting bump in rooms", log)

    for c in CORE + SECONDARY:
        med = df.groupby("date_visit")[c].transform(
            lambda s: s.rolling(P["SPIKE_WIN"], center=True, min_periods=3).median())
        dev = (df[c] - med).abs()
        spike = (med.notna() & (dev > P["SPIKE_REL"] * med.abs()) & (dev > P["SPIKE_ABS"][c]))
        _blank(df, spike, CORE if c in CORE else [c], f"glitch in {c}", log)
    return df


def _fill_curve(s: pd.Series, max_gap: int) -> pd.Series:
    """time-weighted interpolation, but only for gaps of <= max_gap snapshots"""
    miss = s.isna()
    run_id = (miss != miss.shift()).cumsum()
    run_len = miss.groupby(run_id).transform("sum")
    filled = s.interpolate(method="time", limit_area="inside")
    filled[miss & (run_len > max_gap)] = np.nan
    return filled.ffill(limit=2)


def _step_fill(df: pd.DataFrame, P: dict) -> pd.DataFrame:
    df = df.sort_values(["date_visit", "snapshot_date"]).reset_index(drop=True)
    df["was_imputed"] = df[CORE].isna().any(axis=1)
    idx = df.set_index("snapshot_date")
    for c in METRICS:
        df[c] = idx.groupby("date_visit")[c].transform(lambda s: _fill_curve(s, P["MAX_GAP"])).values
    df = df.dropna(subset=["n_room", "n_people"]).copy()  # revenue may stay empty
    adr = df["amount_fee"] / df["n_room"].replace(0, np.nan)
    df.loc[adr.notna() & ((adr < P["ADR_MIN"]) | (adr > P["ADR_MAX"])), "amount_fee"] = np.nan
    df.loc[df["n_reserve_reliable"] == 0, "n_reserve"] = np.nan
    for c in METRICS:
        df[c] = df[c].round().astype("Int64")
    return df


def _step_features(df: pd.DataFrame) -> pd.DataFrame:
    df["occ"] = (df["n_room"] / df["capacity"]).astype(float).round(4)
    df["adr"] = (df["amount_fee"] / df["n_room"].replace(0, pd.NA)).astype(float).round(0)
    df["revpar"] = (df["amount_fee"] / df["capacity"]).astype(float).round(0)
    df["rev_per_guest"] = (df["amount_fee"] / df["n_people"].replace(0, pd.NA)).astype(float).round(0)
    df["is_holiday"] = df["date_visit"].map(lambda d: int(jpholiday.is_holiday(d)))
    return df.sort_values(["snapshot_date", "date_visit"]).reset_index(drop=True)


def _run_pipeline(raw: pd.DataFrame, hotels: pd.DataFrame, P: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    log: list[dict] = []
    df = _step_basic(raw, P)
    df, snap_stats = _step_snapshots(df, P, log)
    df = _step_rules(df, P, log, hotels)
    df = _step_glitches(df, P, log)
    df = _step_fill(df, P)
    df = _step_features(df)
    return df, pd.DataFrame(log), snap_stats


# ── Public interface ─────────────────────────────────────────────────────

MASTER_COLS = [
    "n_room", "n_people", "amount_fee", "n_stay", "n_reserve", "capacity",
    "occ", "adr", "revpar", "rev_per_guest",
    "is_stale", "from_bad_snapshot", "was_imputed", "neg_fee_adjustment", "n_reserve_reliable",
]


def load_hotel(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    hotel_cfg = node_cfg["sources"].get("hotel", {})
    if not hotel_cfg.get("enabled"):
        return None, unavailable_report("hotel", node_key, hotel_cfg.get("reason", "hotel disabled for this node"))

    repo_root = resolve_path(hotel_cfg["repo"])
    data_dir = f"{repo_root}/data"

    raw = _load_all_snapshots(data_dir)
    if raw.empty:
        return None, SourceReport(source="hotel", node_key=node_key, status="error",
                                   notes=[f"no snapshot files found under {data_dir}"])
    hotels = _load_capacity(repo_root)

    adr_min, adr_max = _derive_adr_bounds(raw)
    params = dict(DEFAULT_PARAMS, ADR_MIN=adr_min, ADR_MAX=adr_max)
    clean, cleaning_log, snap_stats = _run_pipeline(raw, hotels, params)

    # One row per date_visit: prefer the smallest lead_time with valid
    # data (closest to the actual stay date) — after cleaning/filling,
    # not the raw day-of snapshot, so a blanked-then-refilled lead_time=0
    # still comes through, and a genuinely unrecoverable one falls back
    # to the next-best lead_time this date_visit has.
    final_daily = (
        clean.sort_values("lead_time")
        .groupby("date_visit", as_index=False).first()
        .sort_values("date_visit").reset_index(drop=True)
        .rename(columns={"date_visit": "date"})
    )

    notes = [
        f"regional source: {hotel_cfg['repo']}" if hotel_cfg.get("scope") == "regional" else f"station-specific source: {hotel_cfg['repo']}",
        f"{len(clean):,}/{len(raw):,} rows kept after cleaning ({len(clean)/len(raw):.1%})",
        f"{len(cleaning_log):,} cell(s) changed (see cleaning methodology in module docstring)",
        f"snapshot-level issues: {snap_stats['failed_pulls']} failed pulls, "
        f"{snap_stats['off_level_snapshots']} off-level, {snap_stats['frozen_snapshots']} frozen/stale",
        f"ADR sanity bounds derived from this repo's own price distribution: {adr_min:,.0f}-{adr_max:,.0f} JPY/room "
        f"(1st-99th percentile) — not a fixed constant shared across repos, see DEFAULT_PARAMS comment",
    ]

    report = validate_daily(final_daily[["date", *MASTER_COLS]], source="hotel", node_key=node_key, notes=notes)
    return final_daily[["date", *MASTER_COLS]], report
