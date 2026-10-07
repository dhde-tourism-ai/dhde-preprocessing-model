#!/usr/bin/env python3
"""
Record how many Instagram posts are tagged at every node that has an
`instagram` source with a `location_id`, in {out}/instagram/ (see
sources/instagram.py for why a weekly total, not the posts).

Three ways in:
- Default: run the Apify "Instagram Scraper" actor on each location page
  and save its media_count, one snapshot per run. Only the location's own
  fields are downloaded. Needs APIFY_TOKEN; without it this prints a
  warning and exits 0. Meant to run weekly (see
  .github/workflows/collect-instagram.yml), which commits --out to the
  `live-data` branch.
- --import FILE --location-id ID --node NODE: load an export with recent
  posts someone ran by hand on one location (the actor's JSON or CSV
  download) into the per-post log. No token needed.
- --find-places "NAME, NAME": look up Instagram place ids by name and
  print the candidates (id, name, coordinates, post count) to pick one for
  a node's `instagram.location_id`. Needs APIFY_TOKEN; stores nothing.

A weekly run is one small actor run per location.

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
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sentiment import try_load_scorer
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


def run_actor(run_input: dict, token: str) -> str:
    """Start the actor, wait for it, and return its dataset id."""
    run = _apify("POST", f"{APIFY}/acts/{ACTOR}/runs?waitForFinish=60", token, json=run_input)["data"]
    deadline = time.time() + RUN_TIMEOUT_S
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise RuntimeError(f"Apify run still {run['status']} after {RUN_TIMEOUT_S}s")
        run = _apify("GET", f"{APIFY}/actor-runs/{run['id']}?waitForFinish=60", token)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['status']}")
    return run["defaultDatasetId"]


def scrape_total(location_id: str, token: str) -> dict:
    """The location page's own fields: its id, name and media_count (posts ever
    tagged there). Only these are downloaded: the page's "top posts", with their
    usernames, are left on Apify."""
    run_input = {"directUrls": [location_url(location_id)], "resultsType": "posts", "resultsLimit": 1,
                 "addParentData": False}
    dataset = run_actor(run_input, token)
    fields = "location_id,name,media_count,error,errorDescription"
    items = _apify("GET", f"{APIFY}/datasets/{dataset}/items?clean=true&format=json&fields={fields}", token)
    return next((it for it in items if str(it.get("location_id")) == str(location_id)), items[0] if items else {})


def store_total(item: dict, node_key: str, location_id: str, run_date: str) -> None:
    """One snapshot of the location's post total. Anything but a number for this
    location records nothing: a missing total stays missing."""
    count = item.get("media_count")
    if str(item.get("location_id", location_id)) != str(location_id):
        print(f"::warning::{node_key}: the scraper answered for location {item.get('location_id')}, not "
              f"{location_id}; nothing recorded. Check instagram.location_id in config/nodes/{node_key}.yaml")
        return
    if not isinstance(count, (int, float)) or count < 0:
        why = item.get("error") or item.get("errorDescription") or "no media_count in the result"
        print(f"::warning::{node_key}: no post total for location {location_id} ({why}); nothing recorded "
              "(missing, not 0)")
        return
    n = ig.append_total(node_key, location_id, int(count), run_date)
    print(f"  {node_key}: {int(count):,} posts tagged at {item.get('name') or location_id}; {n} snapshot(s) in the log")


def find_places(names: list[str], token: str, per_name: int = 5) -> None:
    """Print Instagram place candidates for each name. Nothing is stored."""
    for name in names:
        run_input = {"search": name, "searchType": "place", "searchLimit": per_name, "resultsType": "details"}
        dataset = run_actor(run_input, token)
        items = _apify("GET", f"{APIFY}/datasets/{dataset}/items?clean=true&format=json", token)
        print(f"\n{name}: {len(items)} candidate(s)")
        for it in items:
            loc = it.get("location") or {}
            pid = it.get("location_id") or it.get("id") or it.get("locationId") or loc.get("pk") or loc.get("id")
            lat = it.get("lat") or it.get("latitude") or loc.get("lat")
            lng = it.get("lng") or it.get("longitude") or loc.get("lng")
            posts = it.get("postsCount") or it.get("mediaCount") or it.get("media_count")
            print(f"  id={pid}  name={it.get('name') or loc.get('name')}  lat={lat} lng={lng}  posts={posts}  "
                  f"{location_url(str(pid)) if pid else ''}")


def store(items: pd.DataFrame, node_key: str, location_id: str, limit: int | None, since: str | None,
          run_date: str, scorer=None) -> None:
    """Add one location's posts to its logs.

    Posts tagged at another location (the actor sometimes returns a
    related place) are dropped. An error row, e.g. a page that no longer
    exists, records nothing: marking those days 0 would be wrong.

    An empty result records nothing either. The scraper gives no sign it
    reached the page (on 2026-10-02 it returned nothing, without an error,
    for two busy sites), and weeks without a single post at a site people
    visit every day aren't believable. Those days stay missing."""
    if items.empty:
        print(f"::warning::{node_key}: the scraper returned nothing for location {location_id}, without an "
              "error; nothing recorded (missing, not 0). Check the run in the Apify console.")
        return
    if "error" in items and items["error"].notna().any() and ("id" not in items or items["id"].isna().all()):
        print(f"::warning::{node_key}: Apify returned an error for location {location_id}: "
              f"{items['error'].dropna().iloc[0]}; nothing recorded. Check instagram.location_id "
              f"in config/nodes/{node_key}.yaml")
        return
    returned = len(items)
    if "locationId" in items:
        other = items["locationId"].notna() & (items["locationId"].astype(str) != location_id)
        if other.any():
            print(f"  {node_key}: dropped {int(other.sum())} post(s) tagged at another location")
            items = items[~other]
    posts = ig.normalize(items, location_id, scorer)
    if since:
        posts = posts[posts["date"] >= since]
    run = ig.run_summary(posts, location_id, run_date, limit, since, returned)
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
    # Unused since live runs record weekly totals; kept so the workflow's first_days input still parses.
    parser.add_argument("--first-days", type=int, default=FIRST_DAYS, help=argparse.SUPPRESS)
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
        store(items, args.node, str(args.location_id), args.limit, None, today.isoformat(), try_load_scorer())
        return 0

    locs = configured_locations()
    print(f"{len(locs)} location(s) configured: {', '.join(sorted(locs))}")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("::warning::APIFY_TOKEN not set, no posts collected")
        return 0

    failures = 0
    for node_key, location_id in locs.items():
        try:
            store_total(scrape_total(location_id, token), node_key, location_id, today.isoformat())
        except Exception as e:  # noqa: BLE001 - one failed location shouldn't lose the others
            failures += 1
            print(f"  {node_key}: failed this run: {e}")
    print(f"\ndone, {failures} of {len(locs)} location(s) failed")
    # Red in Actions only when every location failed (bad token, no credit, Apify down).
    return 1 if locs and failures == len(locs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
