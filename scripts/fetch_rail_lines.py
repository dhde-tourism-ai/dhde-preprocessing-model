#!/usr/bin/env python3
"""
Fetch the railway lines and stations in and around Fukui and save them to
config/transport/rail_lines.json (committed; scripts/build_transport.py puts
them in transport_map.json for the map's Public transport layer).

Source: MLIT National Land Numerical Information, railway data (国土数値情報
鉄道データ N02), CC BY 4.0. It gives each line's track and stations, not
timetables (no rail timetable in Fukui is open data). Lines are kept from
the list below and clipped to a box around Fukui, so onward links visitors
use (Maibara, Kaga-Onsen, Higashi-Maizuru) stay but the rest of Japan doesn't.
The track changes rarely, so this runs by hand, not daily.

Usage:
    python scripts/fetch_rail_lines.py
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "config" / "transport" / "rail_lines.json"
VERSION = "N02-24"
URL = f"https://nlftp.mlit.go.jp/ksj/gml/data/N02/{VERSION}/{VERSION}_GML.zip"
PAGE = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2024.html"
BOX = (35.3, 135.35, 36.33, 136.9)  # lat_min, lon_min, lat_max, lon_max

# (operator, line) in the data -> how the app names it. kind: shinkansen, rail, tram.
# The last two values are ILLUSTRATIVE, for the map's moving trains only: a typical
# daytime interval (minutes) and average speed including stops (km/h). Rail timetables
# are not open data, so these are round figures, not a timetable.
LINES = {
    ("西日本旅客鉄道", "北陸新幹線"): ("hokuriku_shinkansen", "Hokuriku Shinkansen", "北陸新幹線", "shinkansen", 30, 180),
    ("ハピラインふくい", "ハピラインふくい線"): ("hapi_line", "Hapi-line Fukui", "ハピラインふくい線", "rail", 30, 55),
    ("えちぜん鉄道", "三国芦原線"): ("echizen_mikuni_awara", "Echizen Railway Mikuni-Awara Line", "えちぜん鉄道 三国芦原線", "rail", 30, 35),
    ("えちぜん鉄道", "勝山永平寺線"): ("echizen_katsuyama_eiheiji", "Echizen Railway Katsuyama-Eiheiji Line", "えちぜん鉄道 勝山永平寺線", "rail", 30, 35),
    ("福井鉄道", "福武線"): ("fukui_railway_fukubu", "Fukui Railway Fukubu Line", "福井鉄道 福武線", "tram", 20, 22),
    ("西日本旅客鉄道", "越美北線"): ("jr_etsumi_hoku", "JR Etsumi-Hoku Line (Kuzuryu Line)", "JR越美北線（九頭竜線）", "rail", 120, 40),
    ("西日本旅客鉄道", "小浜線"): ("jr_obama", "JR Obama Line", "JR小浜線", "rail", 60, 45),
    ("西日本旅客鉄道", "北陸線"): ("jr_hokuriku", "JR Hokuriku Line", "JR北陸線", "rail", 60, 55),
}


def line_key(p: dict) -> tuple[str, str] | None:
    k = (p["N02_004"], p["N02_003"])
    if k in LINES:
        return k
    if k[::-1] in LINES:  # one Echizen Railway section has operator and line swapped
        return k[::-1]
    return None


def inside(lon: float, lat: float) -> bool:
    return BOX[0] <= lat <= BOX[2] and BOX[1] <= lon <= BOX[3]


def clip(coords: list[list[float]]) -> list[list[list[float]]]:
    """Split a section into the runs of points inside BOX, as [lat, lon]."""
    runs, run = [], []
    for lon, lat in coords:
        if inside(lon, lat):
            run.append([round(lat, 5), round(lon, 5)])
        else:
            if len(run) > 1:
                runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    return runs


def stitch(paths: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join a line's track sections end to end into as few continuous paths as possible,
    so a train can travel along them. Sections meeting at a junction start a new path."""
    left = [p for p in paths if len(p) > 1]
    chains = []
    while left:
        chain = left.pop(0)
        grown = True
        while grown:
            grown = False
            for i, p in enumerate(left):
                if p[0] == chain[-1]:
                    chain = chain + p[1:]
                elif p[-1] == chain[-1]:
                    chain = chain + p[::-1][1:]
                elif p[-1] == chain[0]:
                    chain = p + chain[1:]
                elif p[0] == chain[0]:
                    chain = p[::-1] + chain[1:]
                else:
                    continue
                left.pop(i)
                grown = True
                break
        chains.append(chain)
    return sorted(chains, key=len, reverse=True)


def main() -> None:
    r = requests.get(URL, timeout=300)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    read = lambda name: json.loads(z.read(next(n for n in z.namelist() if n.endswith(f"UTF-8/{VERSION}_{name}.geojson"))))  # noqa: E731

    lines: dict[str, dict] = {}
    for f in read("RailroadSection")["features"]:
        k = line_key(f["properties"])
        if not k:
            continue
        lid, en, ja, kind, every, kmh = LINES[k]
        line = lines.setdefault(lid, {"id": lid, "name": en, "name_ja": ja, "operator_ja": k[0], "kind": kind, "paths": [],
                                      "service": {"basis": "illustrative", "interval_min": every, "speed_kmh": kmh,
                                                  "first": "06:00", "last": "23:00"}})
        line["paths"] += clip(f["geometry"]["coordinates"])

    for line in lines.values():
        line["paths"] = stitch(line["paths"])

    # A station served by several lines appears once per line; N02_005g groups them.
    stations: dict[str, dict] = {}
    for f in read("Station")["features"]:
        k = line_key(f["properties"])
        if not k:
            continue
        cs = f["geometry"]["coordinates"]
        lon, lat = cs[len(cs) // 2]
        if not inside(lon, lat):
            continue
        p = f["properties"]
        s = stations.setdefault(p["N02_005g"], {"id": p["N02_005g"], "name_ja": p["N02_005"],
                                                "lat": round(lat, 5), "lon": round(lon, 5), "lines": []})
        if LINES[k][0] not in s["lines"]:
            s["lines"].append(LINES[k][0])

    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": f"MLIT National Land Numerical Information, railway data ({VERSION})", "url": PAGE,
           "licence": "CC BY 4.0", "credit": "国土数値情報（鉄道データ）国土交通省",
           "lines": [lines[v[0]] for v in LINES.values() if v[0] in lines],
           "stations": sorted(stations.values(), key=lambda s: s["id"])}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    for line in out["lines"]:
        print(f"  {line['name']:42s} {len(line['paths']):4d} paths {sum(len(p) for p in line['paths']):6d} points")
    print(f"[OK] wrote {OUT} ({len(out['lines'])} lines, {len(out['stations'])} stations, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
