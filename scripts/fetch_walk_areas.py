#!/usr/bin/env python3
"""
Fetch the 15- and 30-minute walking areas around each node's access point
and save them to config/transport/walk_areas.json (committed;
scripts/build_transport.py puts them in transport_map.json).

Walking areas are isochrones from the public Valhalla server of the
OpenStreetMap community (valhalla1.openstreetmap.de, pedestrian costing on
OpenStreetMap data, ODbL). Six requests, one per node, a second apart.
They change only when the paths do, so this runs by hand, not daily.

Usage:
    python scripts/fetch_walk_areas.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent.parent / "config" / "transport"
URL = "https://valhalla1.openstreetmap.de/isochrone"
MINUTES = [15, 30]


def isochrone(lat: float, lon: float) -> dict[str, list[list[float]]]:
    body = {"locations": [{"lat": lat, "lon": lon}], "costing": "pedestrian",
            "contours": [{"time": m} for m in MINUTES], "polygons": True, "denoise": 0.5, "generalize": 25}
    r = requests.post(URL, json=body, timeout=60,
                      headers={"User-Agent": "dhde-transport/1.0 (+https://github.com/dhde-tourism-ai/dhde-app)"})
    r.raise_for_status()
    fc = r.json()
    out = {}
    for f in fc["features"]:
        ring = f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"][0][0]
        out[str(int(f["properties"]["contour"]))] = [[round(y, 5), round(x, 5)] for x, y in ring]  # [lat, lon]
    return out


def main() -> None:
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    areas = {}
    for k, nc in cfg["nodes"].items():
        a = nc["anchor"]
        areas[k] = isochrone(a["lat"], a["lon"])
        print(f"  {k}: " + ", ".join(f"{m} min {len(r)} pts" for m, r in areas[k].items()))
        time.sleep(1)
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "Valhalla pedestrian isochrones (valhalla1.openstreetmap.de), OpenStreetMap data (ODbL)",
           "licence": "ODbL 1.0, © OpenStreetMap contributors", "minutes": MINUTES, "nodes": areas}
    (HERE / "walk_areas.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"[OK] wrote {HERE / 'walk_areas.json'}")


if __name__ == "__main__":
    main()
