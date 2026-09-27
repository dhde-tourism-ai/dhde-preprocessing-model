"""
Hotel availability from the Rakuten Travel API — the one hotel signal
collected the same way for Fukui, Ishikawa and Toyama.

Optional source: only nodes that declare `rakuten` in their config get
it (see join.OPTIONAL_SOURCES). Credentials come from the RAKUTEN_APP_ID
and RAKUTEN_ACCESS_KEY environment variables — never from config, since
this repo is public.

What's measured: for each node, hotels listed on Rakuten within
`search_radius_km` (max 3) of the node's coordinates, and how many of
them still have a room for 2 adults (configurable) on a given stay date,
checked `lead_days` before it. The share with rooms left falls as a date
fills up, so it's a demand signal, and being a ratio it's comparable
between nodes with very different hotel counts. The cheapest available
room charge for that night is kept too.

This is AVAILABILITY on one booking site, not bookings. It's not the
same measure as Fukui's reservation-repo `hotel` source, which is why
it's a separate source rather than a replacement.

Rakuten has no history: each run snapshots today + each lead time and
appends to `{workspace_root}/rakuten_snapshots/{node_key}.csv`, so the
series only grows from the first run on — this needs to run daily (e.g.
the same schedule as traffic) to be useful. A lead/day already
snapshotted today is not fetched again.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from ..config import resolve_path
from ..validation import SourceReport, unavailable_report, validate_daily

API_BASE = "https://openapi.rakuten.co.jp/engine/api/Travel"
SNAPSHOT_DIR = "rakuten_snapshots"
SNAPSHOT_COLS = ["snapshot_date", "stay_date", "lead_days", "hotels_listed", "hotels_vacant", "min_charge"]
REQUEST_GAP_S = 1.2  # Rakuten throttles repeated requests in short periods


def _auth() -> dict:
    return {"applicationId": os.environ["RAKUTEN_APP_ID"], "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
            "format": "json", "formatVersion": 2}


def _get(endpoint: str, params: dict) -> dict | None:
    """None when Rakuten answers 404 not_found (zero matching hotels)."""
    resp = requests.get(f"{API_BASE}/{endpoint}", params={**_auth(), **params}, timeout=60)
    if resp.status_code == 404 and "not_found" in resp.text:
        return None
    resp.raise_for_status()
    return resp.json()


def _cheapest_charge(hotel: list[dict]) -> float | None:
    """Lowest per-night total among the rooms returned for the cheapest hotel."""
    charges = [
        room["dailyCharge"]["total"]
        for part in hotel for room in part.get("roomInfo", [])
        if isinstance(room, dict) and room.get("dailyCharge", {}).get("total") is not None
    ]
    return float(min(charges)) if charges else None


def snapshot(geo: dict, stay: date, adult_num: int) -> tuple[int, int, float | None]:
    """(hotels listed in radius, hotels with a room for `stay`, cheapest charge)."""
    listed = _get("SimpleHotelSearch/20170426", {**geo, "hits": 1})
    time.sleep(REQUEST_GAP_S)
    vacant = _get("VacantHotelSearch/20170426", {
        **geo, "checkinDate": stay.isoformat(), "checkoutDate": (stay + timedelta(days=1)).isoformat(),
        "adultNum": adult_num, "hits": 1, "sort": "+roomCharge",
    })
    time.sleep(REQUEST_GAP_S)
    n_listed = listed["pagingInfo"]["recordCount"] if listed else 0
    if not vacant:
        return n_listed, 0, None
    return n_listed, vacant["pagingInfo"]["recordCount"], _cheapest_charge(vacant["hotels"][0])


def to_daily(snaps: pd.DataFrame, lead_days: list[int]) -> pd.DataFrame:
    """Snapshot log -> one row per stay date, columns per lead time."""
    snaps = snaps.copy()
    snaps["stay_date"] = pd.to_datetime(snaps["stay_date"])
    snaps["vacant_share"] = snaps["hotels_vacant"] / snaps["hotels_listed"].where(snaps["hotels_listed"] > 0)
    snaps = snaps.sort_values("snapshot_date").drop_duplicates(["stay_date", "lead_days"], keep="last")
    wide = snaps.pivot(index="stay_date", columns="lead_days", values=["vacant_share", "min_charge"])
    wide.columns = [f"rakuten_{metric}_d{lead}" for metric, lead in wide.columns]
    ordered = [f"rakuten_{m}_d{lead}" for lead in lead_days for m in ("vacant_share", "min_charge")]
    return wide.reindex(columns=ordered).rename_axis("date").reset_index()


def load_rakuten(node_cfg: dict) -> tuple[pd.DataFrame | None, SourceReport]:
    node_key = node_cfg["node_key"]
    r_cfg = node_cfg["sources"].get("rakuten", {})
    if not r_cfg.get("enabled"):
        return None, unavailable_report("rakuten", node_key, r_cfg.get("reason", "rakuten disabled for this node"))
    if not (os.environ.get("RAKUTEN_APP_ID") and os.environ.get("RAKUTEN_ACCESS_KEY")):
        return None, SourceReport(source="rakuten", node_key=node_key, status="error",
                                   notes=["RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY environment variables not set"])

    lead_days = r_cfg.get("lead_days", [1, 7, 30])
    radius = r_cfg.get("search_radius_km", 3)
    geo = {"latitude": node_cfg["coordinates"]["lat"], "longitude": node_cfg["coordinates"]["lon"],
           "searchRadius": radius, "datumType": 1}

    path = Path(resolve_path(f"{SNAPSHOT_DIR}/{node_key}.csv"))
    snaps = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=SNAPSHOT_COLS)
    today = date.today()
    done_today = set(snaps.loc[snaps["snapshot_date"] == today.isoformat(), "lead_days"].astype(int))

    notes, new_rows = [], []
    for lead in lead_days:
        if lead in done_today:
            continue
        stay = today + timedelta(days=lead)
        try:
            listed, vacant, charge = snapshot(geo, stay, r_cfg.get("adult_num", 2))
        except Exception as e:  # noqa: BLE001 - one failed lead shouldn't lose the others
            notes.append(f"lead {lead}d snapshot failed this run: {e!r}")
            continue
        new_rows.append({"snapshot_date": today.isoformat(), "stay_date": stay.isoformat(), "lead_days": lead,
                         "hotels_listed": listed, "hotels_vacant": vacant, "min_charge": charge})

    if new_rows:
        snaps = pd.concat([snaps, pd.DataFrame(new_rows)], ignore_index=True) if len(snaps) else pd.DataFrame(new_rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        snaps.to_csv(path, index=False)

    if snaps.empty:
        return None, SourceReport(source="rakuten", node_key=node_key, status="error",
                                   notes=notes + ["no snapshots yet"])

    daily = to_daily(snaps, lead_days)
    latest = snaps.iloc[-1]
    notes += [f"hotels on Rakuten within {radius}km: {int(latest['hotels_listed'])} (latest snapshot)",
              f"{len(snaps)} snapshot(s) since {snaps['snapshot_date'].min()} — no history before the first run",
              "availability on Rakuten, not bookings"]
    report = validate_daily(daily, source="rakuten", node_key=node_key, notes=notes)
    return daily, report
