#!/usr/bin/env python3
"""
Collect new Google Maps reviews for every node that has a
`google_reviews` source with a `place_id`, and append them to
{out}/google_reviews/ (see sources/google_reviews.py for what's kept).

Two ways in:
- Default: run the Apify "Google Maps Reviews Scraper" actor for the
  newest reviews since the last run. Needs APIFY_TOKEN in the
  environment; without it this prints a warning and exits 0. Meant to
  run weekly (see .github/workflows/collect-google-reviews.yml), which
  commits --out to the `live-data` branch.
- --import FILE: load an export someone already ran by hand (the actor's
  CSV or JSON download). No token needed.

Usage:
    python scripts/collect_google_reviews.py --out history
    python scripts/collect_google_reviews.py --out history --import dataset.csv --max-reviews 1000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sources import google_reviews as gr

APIFY = "https://api.apify.com/v2"
ACTOR = "compass~Google-Maps-Reviews-Scraper"
DEFAULT_MAX_REVIEWS = 300  # per place per run; the busiest node gets about 60 a week
RUN_TIMEOUT_S = 1800


def configured_places() -> dict[str, str]:
    """{place_id: node_key} for nodes with google_reviews enabled."""
    places = {}
    for node_key in list_configured_nodes():
        g = load_node_config(node_key)["sources"].get("google_reviews", {})
        if g.get("enabled") and g.get("place_id"):
            places[g["place_id"]] = node_key
    return places


def read_export(path: str) -> pd.DataFrame:
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))
    return pd.read_csv(path, low_memory=False)


def _apify(method: str, url: str, token: str, **kw) -> dict | list:
    """Token goes in a header, never the URL, so it can't end up in a log."""
    try:
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {token}"}, timeout=120, **kw)
    except requests.RequestException as e:
        raise RuntimeError(f"Apify: {type(e).__name__}") from None
    if not resp.ok:
        raise RuntimeError(f"Apify: HTTP {resp.status_code}")
    return resp.json()


def scrape(place_id: str, since: str | None, max_reviews: int, token: str) -> pd.DataFrame:
    run_input = {
        "placeIds": [place_id],
        "maxReviews": max_reviews,
        "reviewsSort": "newest",
        "language": "en",
        "personalData": False,  # no reviewer names, ids or photos
    }
    if since:
        run_input["reviewsStartDate"] = since
    run = _apify("POST", f"{APIFY}/acts/{ACTOR}/runs?waitForFinish=60", token, json=run_input)["data"]
    deadline = time.time() + RUN_TIMEOUT_S
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise RuntimeError(f"Apify run still {run['status']} after {RUN_TIMEOUT_S}s")
        run = _apify("GET", f"{APIFY}/actor-runs/{run['id']}?waitForFinish=60", token)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['status']}")
    items = _apify("GET", f"{APIFY}/datasets/{run['defaultDatasetId']}/items?clean=true&format=json", token)
    return pd.DataFrame(items)


def store(items: pd.DataFrame, place_id: str, node_key: str, max_reviews: int | None, since: str | None,
          live: bool = False) -> None:
    """Add one place's reviews to its logs. `live` is a scraper run made just now:
    with no reviews it still records the days it covered (0 reviews, not missing).
    An import without the place says nothing about it, so nothing is recorded.

    A live run returning reviews under another place id (Google merged or
    moved the listing) records nothing either: marking those days 0 would
    hide real reviews, and they'd never be fetched again."""
    with_review = items[items["reviewId"].notna()] if "reviewId" in items else items.iloc[0:0]
    p_items = with_review[with_review["placeId"] == place_id] if "placeId" in with_review else with_review.iloc[0:0]
    if p_items.empty:
        if not live:
            print(f"  {node_key}: not in the import")
            return
        if not with_review.empty:
            other = sorted(set(with_review.get("placeId", pd.Series(dtype=object)).dropna().astype(str)))
            print(f"::warning::{node_key}: {len(with_review)} review(s) came back under place id(s) "
                  f"{', '.join(other) or '(none)'}, not {place_id}; nothing recorded. The listing may have "
                  f"moved: check it and update google_reviews.place_id in config/nodes/{node_key}.yaml")
            return
        run = gr.empty_run(items, place_id, datetime.now(gr.JST).date().isoformat(), since)
        _, total = gr.append(node_key, pd.DataFrame(columns=gr.REVIEW_COLS), run)
        print(f"  {node_key}: no new reviews, {total} in the log; covers {run['covered_from']} to {run['covered_to']}")
        return
    items = p_items
    reviews = gr.normalize(items)
    run = gr.run_summary(items, reviews, place_id, max_reviews, since)
    added, total = gr.append(node_key, reviews, run)
    print(f"  {node_key}: {len(reviews)} fetched, {added} new, {total} in the log; "
          f"covers {run['covered_from']} to {run['covered_to']}{' (capped)' if run['capped'] else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="history")
    parser.add_argument("--import", dest="imports", nargs="+", help="scraper export(s) to load instead of calling Apify")
    parser.add_argument("--max-reviews", type=int, default=DEFAULT_MAX_REVIEWS,
                        help="the per-place cap the run used (for --import, the cap it was run with)")
    args = parser.parse_args()
    os.environ["DHDE_LIVE_DATA_ROOT"] = str(Path(args.out).resolve())

    places = configured_places()
    print(f"{len(places)} place(s) configured: {', '.join(sorted(places.values()))}")

    if args.imports:
        items = pd.concat([read_export(p) for p in args.imports], ignore_index=True)
        unknown = set(items["placeId"].dropna()) - set(places)
        if unknown:
            print(f"  skipped {len(unknown)} place(s) not in any node config: {', '.join(sorted(unknown))}")
        for place_id, node_key in places.items():
            store(items, place_id, node_key, args.max_reviews, None)
        return 0

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("::warning::APIFY_TOKEN not set, no reviews collected")
        return 0

    failures = 0
    for place_id, node_key in places.items():
        since = gr.since_date(node_key)
        try:
            store(scrape(place_id, since, args.max_reviews, token), place_id, node_key, args.max_reviews, since,
                  live=True)
        except Exception as e:  # noqa: BLE001 - one failed place shouldn't lose the others
            failures += 1
            print(f"  {node_key}: failed this run: {e}")
    print(f"\ndone, {failures} of {len(places)} place(s) failed")
    # Red in Actions only when every place failed (bad token, no credit, Apify down).
    return 1 if places and failures == len(places) else 0


if __name__ == "__main__":
    raise SystemExit(main())
