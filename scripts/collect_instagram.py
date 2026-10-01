#!/usr/bin/env python3
"""
Collect new Instagram posts tagged at every node that has an `instagram`
source with a `location_id`, and append them to {out}/instagram/
(see sources/instagram.py for what's kept).

Two ways in:
- Default: run the Apify "Instagram Scraper" actor on each location page
  for posts since the last run. Needs APIFY_TOKEN in the environment;
  without it this prints a warning and exits 0. Meant to run weekly (see
  .github/workflows/collect-instagram.yml), which commits --out to the
  `live-data` branch.
- --import FILE --location-id ID --node NODE: load an export someone
  already ran by hand on one location (the actor's JSON or CSV download).
  No token needed.

- --find-places "NAME, NAME": look up Instagram place ids by name and
  print the candidates (id, name, coordinates, post count) to pick one for
  a node's `instagram.location_id`. Needs APIFY_TOKEN; stores nothing.

Cost on Apify's free plan: about $0.0027 a post. The first run looks back
--first-days days; later runs only fetch what's new.

Usage:
    python scripts/collect_instagram.py --out history
    python scripts/collect_instagram.py --find-places "レインボーライン山頂公園, Rainbow Line"
    python scripts/collect_instagram.py --out history --import dataset.json --node tojinbo --location-id 123
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sources import instagram as ig

APIFY = "https://api.apify.com/v2"
ACTOR = "apify~instagram-scraper"
DEFAULT_LIMIT = 200  # posts per location per run; well above a quiet site's week
FIRST_DAYS = 28
RUN_TIMEOUT_S = 1800


def configured_locations() -> dict[str, str]:
    """{node_key: location_id} for nodes with instagram enabled."""
    locs = {}
    for node_key in list_configured_nodes():
        i = load_node_config(node_key)["sources"].get("instagram", {})
        if i.get("enabled") and i.get("location_id"):
            locs[node_key] = str(i["location_id"])
    return locs


def location_url(location_id: str) -> str:
    return f"https://www.instagram.com/explore/locations/{location_id}/"


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


def scrape(location_id: str, since: str, limit: int, token: str) -> pd.DataFrame:
    run_input = {
        "directUrls": [location_url(location_id)],
        "resultsType": "posts",
        "resultsLimit": limit,
        "onlyPostsNewerThan": since,
        "addParentData": False,
    }
    run = _apify("POST", f"{APIFY}/acts/{ACTOR}/runs?waitForFinish=60", token, json=run_input)["data"]
    deadline = time.time() + RUN_TIMEOUT_S
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise RuntimeError(f"Apify run still {run['status']} after {RUN_TIMEOUT_S}s")
        run = _apify("GET", f"{APIFY}/actor-runs/{run['id']}?waitForFinish=60", token)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['status']}")
    # Fields we don't keep are dropped at the source, so captions never leave
    # memory and usernames are never downloaded.
    fields = "id,type,timestamp,likesCount,commentsCount,caption,locationId,error"
    items = _apify("GET", f"{APIFY}/datasets/{run['defaultDatasetId']}/items?clean=true&format=json&fields={fields}",
                   token)
    return pd.DataFrame(items)


def find_places(names: list[str], token: str, per_name: int = 5) -> None:
    """Print Instagram place candidates for each name. Nothing is stored."""
    for name in names:
        run_input = {"search": name, "searchType": "place", "searchLimit": per_name, "resultsType": "details"}
        run = _apify("POST", f"{APIFY}/acts/{ACTOR}/runs?waitForFinish=60", token, json=run_input)["data"]
        deadline = time.time() + RUN_TIMEOUT_S
        while run["status"] in ("READY", "RUNNING"):
            if time.time() > deadline:
                raise RuntimeError(f"Apify run still {run['status']} after {RUN_TIMEOUT_S}s")
            run = _apify("GET", f"{APIFY}/actor-runs/{run['id']}?waitForFinish=60", token)["data"]
        items = _apify("GET", f"{APIFY}/datasets/{run['defaultDatasetId']}/items?clean=true&format=json", token)
        print(f"\n{name}: {len(items)} candidate(s)")
        for it in items:
            loc = it.get("location") or {}
            pid = it.get("id") or it.get("locationId") or loc.get("pk") or loc.get("id")
            lat = it.get("lat") or it.get("latitude") or loc.get("lat")
            lng = it.get("lng") or it.get("longitude") or loc.get("lng")
            posts = it.get("postsCount") or it.get("mediaCount") or it.get("media_count")
            print(f"  id={pid}  name={it.get('name') or loc.get('name')}  lat={lat} lng={lng}  posts={posts}  "
                  f"{location_url(str(pid)) if pid else ''}")


def store(items: pd.DataFrame, node_key: str, location_id: str, limit: int | None, since: str | None,
          run_date: str) -> None:
    """Add one location's posts to its logs.

    Posts tagged at another location (the actor sometimes returns a
    related place) are dropped. An error row, e.g. a page that no longer
    exists, records nothing: marking those days 0 would be wrong."""
    if "error" in items and items["error"].notna().any() and ("id" not in items or items["id"].isna().all()):
        print(f"::warning::{node_key}: Apify returned an error for location {location_id}: "
              f"{items['error'].dropna().iloc[0]}; nothing recorded. Check instagram.location_id "
              f"in config/nodes/{node_key}.yaml")
        return
    if "locationId" in items:
        other = items["locationId"].notna() & (items["locationId"].astype(str) != location_id)
        if other.any():
            print(f"  {node_key}: dropped {int(other.sum())} post(s) tagged at another location")
            items = items[~other]
    posts = ig.normalize(items, location_id)
    if since:
        posts = posts[posts["date"] >= since]
    run = ig.run_summary(posts, location_id, run_date, limit, since)
    added, total = ig.append(node_key, posts, run)
    print(f"  {node_key}: {len(posts)} fetched, {added} new, {total} in the log; "
          f"covers {run['covered_from']} to {run['covered_to']}{' (capped)' if run['capped'] else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="history")
    parser.add_argument("--import", dest="imports", nargs="+", help="scraper export(s) to load instead of calling Apify")
    parser.add_argument("--node", help="with --import: the node the export is for")
    parser.add_argument("--location-id", help="with --import: the location the export was run on")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="posts per location per run (for --import, the cap it was run with)")
    parser.add_argument("--find-places", nargs="+", metavar="NAME", help="look up Instagram place ids by name")
    parser.add_argument("--first-days", type=int, default=FIRST_DAYS, help="how far a node's first run looks back")
    args = parser.parse_args()
    os.environ["DHDE_LIVE_DATA_ROOT"] = str(Path(args.out).resolve())
    today = datetime.now(ig.JST).date()

    if args.find_places:
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            parser.error("--find-places needs APIFY_TOKEN")
        names = [n.strip() for arg in args.find_places for n in arg.split(",") if n.strip()]
        find_places(names, token)
        return 0

    if args.imports:
        if not (args.node and args.location_id):
            parser.error("--import needs --node and --location-id")
        items = pd.concat([read_export(p) for p in args.imports], ignore_index=True)
        store(items, args.node, str(args.location_id), args.limit, None, today.isoformat())
        return 0

    locs = configured_locations()
    print(f"{len(locs)} location(s) configured: {', '.join(sorted(locs))}")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("::warning::APIFY_TOKEN not set, no posts collected")
        return 0

    failures = 0
    for node_key, location_id in locs.items():
        since = ig.since_date(node_key) or (today - timedelta(days=args.first_days)).isoformat()
        try:
            store(scrape(location_id, since, args.limit, token), node_key, location_id, args.limit, since,
                  today.isoformat())
        except Exception as e:  # noqa: BLE001 - one failed location shouldn't lose the others
            failures += 1
            print(f"  {node_key}: failed this run: {e}")
    print(f"\ndone, {failures} of {len(locs)} location(s) failed")
    # Red in Actions only when every location failed (bad token, no credit, Apify down).
    return 1 if locs and failures == len(locs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
