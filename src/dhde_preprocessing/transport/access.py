"""
Build transport.json and transport_map.json: how
visitors can reach the priority nodes by public transport.

Per node and day type (weekday, Saturday, Sunday/holiday), from the open
GTFS-JP timetables listed in config/transport/config.json:

- the stops serving the node (within `radius_m` of its access point) and
  the routes calling there
- departures and arrivals per day, by mode, and the first and last of each
- from Fukui Station: the fastest journey and the first one leaving at or
  after 09:00, each with its itinerary
- back to Fukui Station: the last bus or train from the node that still gets
  there today, which is also the "car only after HH:MM" time
- Kanazawa and Kyoto: an estimated JR leg to a gateway station (Fukui or
  Awara-Onsen), then the fastest local journey on that day's timetable
- which feeds were used; a feed that ended before the reference week is left
  out entirely

The map file holds the route lines and stops serving the nodes, plus the
walking areas from config/transport/walk_areas.json (scripts/fetch_walk_areas.py).

Run by scripts/build_transport.py.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .gtfs import INF, Network, build_network, earliest_arrival, haversine_m, hhmm, journey, latest_departure, load_feed, walk_seconds

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config" / "transport" / "config.json"
WALK_AREAS = REPO / "config" / "transport" / "walk_areas.json"
CACHE = REPO / "output" / "transport_cache"
JST = timezone(timedelta(hours=9))
DAY_END = 24 * 3600 + 59 * 60  # trips after midnight still count for the service day
CHANGE_PENALTY = 10 * 60  # an extra bus has to save 10 minutes to be worth it
ORIGIN_WINDOW = (5 * 3600, 21 * 3600)  # departures considered for journeys from the hub or a gateway
DAY_TYPES = {"weekday": 2, "saturday": 5, "sunday": 6}  # Wednesday stands in for a weekday


def reference_days(start: date) -> dict[str, date]:
    try:
        import jpholiday  # optional: skip a weekday that is a public holiday
    except ImportError:
        jpholiday = None
    out = {}
    for name, wd in DAY_TYPES.items():
        d = start + timedelta(days=(wd - start.weekday()) % 7)
        while name == "weekday" and jpholiday and jpholiday.is_holiday(d):
            d += timedelta(days=7)
        out[name] = d
    return out


def fetch(feed: dict, refresh: bool, cache_dir: Path = CACHE) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{feed['id']}.zip"
    if refresh or not path.exists():
        r = requests.get(feed["url"], timeout=120, headers={"User-Agent": "dhde-transport/1.0"})
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def node_stops(net: Network, anchor: tuple[float, float], radius: float, walk: dict) -> list[dict]:
    out = []
    for i, r in enumerate(net.stops.itertuples()):
        d = haversine_m(anchor[0], anchor[1], r.lat, r.lon)
        if d <= radius:
            out.append({"idx": i, "id": r.stop_id, "name": r.stop_name, "feed": r.feed, "lat": round(r.lat, 6),
                        "lon": round(r.lon, 6), "distance_m": round(d), "walk_s": walk_seconds(d, walk)})
    return out


def service_counts(net: Network, stops: list[dict]) -> dict:
    ids = {s["id"] for s in stops}
    calls = net.calls
    first_seq = calls.groupby("trip_id")["seq"].transform("min")
    last_seq = calls.groupby("trip_id")["seq"].transform("max")
    here = calls[calls["stop_id"].isin(ids)]
    dep = here[here["seq"] < last_seq[here.index]].sort_values("dep").drop_duplicates("trip_id")
    arr = here[here["seq"] > first_seq[here.index]].sort_values("arr").drop_duplicates("trip_id", keep="last")
    by_mode = {m: int(n) for m, n in dep.groupby("mode").size().items()}
    routes = (here.drop_duplicates("trip_id").groupby(["route_id", "name", "mode", "feed"]).size()
              .reset_index(name="calls").sort_values("calls", ascending=False))
    return {
        "departures": int(len(dep)),
        "arrivals": int(len(arr)),
        "departures_by_mode": by_mode,
        "first_departure": hhmm(dep["dep"].min()) if len(dep) else None,
        "last_departure": hhmm(dep["dep"].max()) if len(dep) else None,
        "first_arrival": hhmm(arr["arr"].min()) if len(arr) else None,
        "last_arrival": hhmm(arr["arr"].max()) if len(arr) else None,
        "routes": [{"id": r.route_id, "name": r.name, "mode": r.mode, "feed": r.feed, "trips": int(r.calls)}
                   for r in routes.itertuples()],
    }


def journeys_from(net: Network, origin: list[dict], nodes: dict[str, list[dict]]) -> dict[str, dict | None]:
    """Journeys from the `origin` stops to each node's stops, stop to stop.

    Every departure time from the origin in ORIGIN_WINDOW is tried; only the
    journeys nobody would beat by leaving later are kept. For the fastest
    one and the first one leaving at or after 09:00 the itinerary is rebuilt,
    and every time shown comes from that itinerary, so they always add up.
    """
    src = {s["idx"] for s in origin}
    times = sorted({int(t) for t in net.calls[net.calls["stop_id"].isin({s["id"] for s in origin})]["dep"]
                    if ORIGIN_WINDOW[0] <= t <= ORIGIN_WINDOW[1]})
    found: dict[str, list[tuple[int, int]]] = {k: [] for k in nodes}
    for t in times:
        ea = earliest_arrival(net, {i: t for i in src})
        for k, stops in nodes.items():
            a = min((ea[s["idx"]] for s in stops), default=INF)
            if a < INF:
                found[k].append((t, a))
    out: dict[str, dict | None] = {}
    for k, pairs in found.items():
        best: list[tuple[int, int]] = []
        for t, a in sorted(pairs, key=lambda p: (-p[0], p[1])):
            if not best or a < best[-1][1]:
                best.append((t, a))
        best.sort()
        targets = {s["idx"] for s in nodes[k]}
        trips = [j for j in (journey(net, {i: t for i in src}, targets) for t, _ in best) if j and j["legs"]]
        if not trips:
            out[k] = None
            continue
        fastest = min(trips, key=lambda j: (j["minutes"], clock(j["depart"])))
        # What a visitor would pick: the same departures planned with a penalty per
        # extra bus, then the quickest of those (fewer buses, then cheaper, on ties).
        simple = [j for j in (journey(net, {i: t for i in src}, targets, CHANGE_PENALTY) for t, _ in best) if j and j["legs"]]
        rides = lambda j: sum(1 for leg in j["legs"] if leg["mode"] != "walk")
        recommended = min(simple or trips, key=lambda j: (j["minutes"], rides(j), j.get("fare_yen") or 10**6))
        nine = next((j for j in trips if clock(j["depart"]) >= 9 * 60), None)
        out[k] = {"journeys": len(trips), "fastest_min": fastest["minutes"], "fastest": fastest, "recommended": recommended,
                  "typical_min": round(statistics.median(j["minutes"] for j in trips)),
                  "first_arrival": min(trips, key=lambda j: clock(j["arrive"]))["arrive"], "after_0900": nine}
    return out


def apply_fare_overrides(j: dict | None, overrides: list[dict], d: date) -> None:
    """Fares changed after the feed was published (config fare_overrides), applied
    to a journey's legs in place, then the journey total is recomputed."""
    if not j:
        return
    for leg in j["legs"]:
        for o in overrides:
            same_trip = leg.get("from") == o.get("from_stop", leg.get("from")) and leg.get("to") == o.get("to_stop", leg.get("to"))
            back_trip = leg.get("from") == o.get("to_stop") and leg.get("to") == o.get("from_stop")
            if leg.get("feed") == o["feed"] and leg.get("route") == o["route"] and (same_trip or back_trip) and d.isoformat() >= o["from"]:
                leg["fare_yen"] = o["yen"]
    fares = [leg.get("fare_yen") for leg in j["legs"] if leg["mode"] != "walk"]
    j["fare_yen"] = sum(fares) if fares and all(f is not None for f in fares) else None


def clock(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def last_return(net: Network, hub: list[dict], stops: list[dict]) -> dict | None:
    """The last bus or train leaving one of the node's stops that still reaches the hub today."""
    ld = latest_departure(net, {s["idx"]: DAY_END for s in hub})
    cands = [(ld[s["idx"]], s) for s in stops if ld[s["idx"]] > -INF]
    if not cands:
        return None
    leave, stop = max(cands, key=lambda c: c[0])
    j = journey(net, {stop["idx"]: leave}, {s["idx"] for s in hub})
    if not j or not j["legs"]:
        return None
    return {"leave": j["depart"], "from_stop": stop["name"], "arrive_hub": j["arrive"], "minutes": j["minutes"],
            "legs": j["legs"]}


def gateway_stops(net: Network, gw: dict, stops: dict[str, list[dict]], walk: dict) -> list[dict]:
    if "node" in gw:
        return stops[gw["node"]]
    return node_stops(net, (gw["lat"], gw["lon"]), gw["radius_m"], walk)


def from_far(net: Network, cfg: dict, stops: dict[str, list[dict]], walk: dict) -> dict[str, dict[str, dict]]:
    """Kanazawa and Kyoto: an estimated JR leg to each gateway station (no open JR
    timetable), a change, then the fastest local journey from that gateway on
    this day's timetable. The best gateway wins. node -> city -> result."""
    out: dict[str, dict[str, dict]] = {k: {} for k in stops}
    for city, ld in cfg["long_distance"].items():
        best: dict[str, dict] = {}
        for gw in ld["gateways"]:
            origin = gateway_stops(net, gw, stops, walk)
            at_gateway = {k for k, st in stops.items() if {s["id"] for s in st} & {s["id"] for s in origin}}
            local = journeys_from(net, origin, {k: v for k, v in stops.items() if v and k not in at_gateway})
            for k in stops:
                if k in at_gateway:
                    total, lm = gw["jr_min"], 0
                elif local.get(k):
                    lm = local[k]["fastest_min"]
                    total = gw["jr_min"] + ld["change_min"] + lm
                else:
                    continue
                if k not in best or total < best[k]["minutes"]:
                    best[k] = {"minutes": total, "via": gw["name"], "via_ja": gw["name_ja"], "jr_min": gw["jr_min"],
                               "change_min": 0 if k in at_gateway else ld["change_min"], "local_min": lm}
        for k in stops:
            out[k][city] = best.get(k) or {"minutes": None}
            out[k][city] |= {"name": ld["name"], "name_ja": ld["name_ja"], "status": "estimated", "basis": ld["basis"]}
    return out


def simplify(path: list[tuple[float, float]], tol_m: float = 25) -> list[list[float]]:
    """Douglas-Peucker in metres, then 5-decimal rounding (about 1 m)."""
    if len(path) < 3:
        return [[round(a, 5), round(b, 5)] for a, b in path]

    def dist(p, a, b):
        if a == b:
            return haversine_m(p[0], p[1], a[0], a[1])
        # planar approximation is fine at these lengths
        k = 111000.0
        ax, ay, bx, by, px, py = a[1] * k * 0.81, a[0] * k, b[1] * k * 0.81, b[0] * k, p[1] * k * 0.81, p[0] * k
        dx, dy = bx - ax, by - ay
        u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return ((ax + u * dx - px) ** 2 + (ay + u * dy - py) ** 2) ** 0.5

    keep = [False] * len(path)
    keep[0] = keep[-1] = True
    stack = [(0, len(path) - 1)]
    while stack:
        i, j = stack.pop()
        far, idx = 0.0, None
        for m in range(i + 1, j):
            d = dist(path[m], path[i], path[j])
            if d > far:
                far, idx = d, m
        if idx is not None and far > tol_m:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [[round(p[0], 5), round(p[1], 5)] for p, k in zip(path, keep) if k]


def route_lines(feeds: list, net: Network, route_ids: set[str]) -> list[dict]:
    by_feed = {f.id: f for f in feeds}
    stops = net.stops.set_index("stop_id")
    lines = []
    for rid in sorted(route_ids):
        f = by_feed[rid.split(":", 1)[0]]
        trips = f.trips[f.trips["route_id"] == rid]
        if trips.empty:
            continue
        route = f.routes[f.routes["route_id"] == rid].iloc[0]
        st = f.stop_times[f.stop_times["trip_id"].isin(trips["trip_id"])]
        longest = st.groupby("trip_id").size().idxmax()
        path = None
        if f.shapes is not None and "shape_id" in trips:
            sid = trips.loc[trips["trip_id"] == longest, "shape_id"].iloc[0]
            pts = f.shapes[f.shapes["shape_id"] == sid].sort_values("seq")
            if len(pts) >= 2:
                path = list(zip(pts["lat"], pts["lon"]))
        if path is None:
            seq = st[st["trip_id"] == longest].sort_values("seq")["stop_id"]
            path = [(stops.at[s, "lat"], stops.at[s, "lon"]) for s in seq if s in stops.index]
        colour = route.get("route_color", "") or ""
        lines.append({"id": rid, "name": route["name"], "mode": route["mode"], "feed": f.id,
                      "colour": f"#{colour}" if len(colour) == 6 else None, "path": simplify(path)})
    return lines


def build(out_dir: Path, *, start: date | None = None, refresh: bool = False, cache_dir: Path = CACHE,
          config: Path = CONFIG, walk_areas: Path = WALK_AREAS) -> None:
    """Write transport.json and transport_map.json to out_dir."""
    cfg = json.loads(Path(config).read_text(encoding="utf-8"))
    walk = cfg["walk"]
    days = reference_days(start or datetime.now(JST).date())

    # A timetable that ended before the reference week is left out entirely: not
    # used, and not listed in the output.
    first_day = min(days.values())
    feeds, sources = [], []
    for fc in cfg["feeds"]:
        f = load_feed(fetch(fc, refresh, Path(cache_dir)), fc["id"], fc["mode"])
        expired = f.valid_to is not None and f.valid_to < first_day
        print(f"  {fc['id']}: {len(f.trips)} trips, valid {f.valid_from} .. {f.valid_to}{'  EXPIRED: left out' if expired else ''}")
        if expired:
            continue
        feeds.append(f)
        sources.append({k: fc.get(k) for k in ("id", "name", "name_ja", "mode", "url", "page", "licence", "licence_url",
                                                "caveat", "caveat_ja", "publish_status")}
                       | {"agency": f.agency, "valid_from": f.valid_from and f.valid_from.isoformat(),
                          "valid_to": f.valid_to and f.valid_to.isoformat()})
    if not feeds:
        raise RuntimeError("no current timetable feed; nothing to build")

    hub_id = cfg["hub"]
    anchors = {k: nc["anchor"] for k, nc in cfg["nodes"].items()}

    nodes_out: dict[str, dict] = {k: {"anchor": anchors[k], "radius_m": cfg["nodes"][k]["radius_m"],
                                      "note": cfg["nodes"][k].get("note"), "note_ja": cfg["nodes"][k].get("note_ja"),
                                      "days": {}} for k in cfg["nodes"]}
    used_dates: dict[str, dict[str, str]] = {}
    weekday_routes: set[str] = set()
    map_stops: dict[str, dict] = {}

    for day, d in days.items():
        net = build_network(feeds, d, walk)
        used_dates[day] = net.used_dates
        stops = {k: node_stops(net, (a["lat"], a["lon"]), cfg["nodes"][k]["radius_m"], walk) for k, a in anchors.items()}
        called = set(net.calls["stop_id"])
        stops = {k: [s for s in st if s["id"] in called] for k, st in stops.items()}  # stops with a service this day
        hub = stops[hub_id]
        others = {k: v for k, v in stops.items() if k != hub_id and v}
        from_hub = journeys_from(net, hub, others)
        far = from_far(net, cfg, stops, walk)
        for k, served in stops.items():
            entry = service_counts(net, served) if served else {"departures": 0, "arrivals": 0, "routes": []}
            legs = []
            if k != hub_id:
                entry["from_hub"] = from_hub.get(k)
                entry["to_hub"] = last_return(net, hub, served) if served else None
                entry["car_only_after"] = entry["to_hub"]["leave"] if entry["to_hub"] else "all day"
                fh = entry["from_hub"] or {}
                for j in (fh.get("fastest"), fh.get("recommended"), fh.get("after_0900"), entry["to_hub"]):
                    apply_fare_overrides(j, cfg.get("fare_overrides", []), d)
                legs = [leg for j in (fh.get("fastest"), fh.get("after_0900"), entry["to_hub"]) if j for leg in j["legs"]]
            entry["from_far"] = far[k]
            used = {r["feed"] for r in entry["routes"]} | {leg["feed"] for leg in legs if "feed" in leg}
            entry["feeds_used"] = sorted(used)
            nodes_out[k]["days"][day] = entry
            if day == "weekday":
                weekday_routes |= {r["id"] for r in entry["routes"]}
                for s in served:
                    m = map_stops.setdefault(s["id"], {"id": s["id"], "name": s["name"], "feed": s["feed"],
                                                       "lat": s["lat"], "lon": s["lon"], "nodes": []})
                    m["nodes"].append(k)
                nodes_out[k]["stops"] = [{key: s[key] for key in ("id", "name", "feed", "lat", "lon", "distance_m")}
                                         | {"walk_min": round(s["walk_s"] / 60)} for s in served]
        print(f"  {day} {d}: {len(net.conns)} connections")
        if day == "weekday":
            lines = route_lines(feeds, net, weekday_routes)

    for k, n in nodes_out.items():
        all_days = n["days"].values()
        n["modes"] = sorted({r["mode"] for d in all_days for r in d.get("routes", [])}) + ["car"]
        n["feeds"] = sorted({f for d in all_days for f in d["feeds_used"]})

    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "timezone": "Asia/Tokyo",
            "hub": hub_id, "reference_days": {k: v.isoformat() for k, v in days.items()}, "timetable_dates": used_dates,
            "walk": walk}
    transport = meta | {
        "note": "Public transport access per node from open GTFS-JP timetables (dhde-preprocessing-model docs/transport.md). Times are scheduled, "
                "not live, and run stop to stop: the walk from the stop to the site is shown separately. Kanazawa and "
                "Kyoto add an estimated JR leg, since JR timetables are not open data.",
        "sources": sources,
        "nodes": nodes_out,
    }
    walk_file = Path(walk_areas)
    areas = json.loads(walk_file.read_text(encoding="utf-8")) if walk_file.exists() else None
    tmap = meta | {"lines": lines, "stops": sorted(map_stops.values(), key=lambda s: s["id"]),
                   "walk_areas": areas}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "transport.json").write_text(json.dumps(transport, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "transport_map.json").write_text(json.dumps(tmap, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"[OK] wrote {out / 'transport.json'} and transport_map.json ({len(lines)} lines, {len(map_stops)} stops)")
    for k, n in nodes_out.items():
        wk = n["days"]["weekday"]
        fh, th = wk.get("from_hub"), wk.get("to_hub")
        far = {c: v["minutes"] for c, v in wk["from_far"].items()}
        print(f"  {k:14s} deps {wk['departures']:4d} {wk.get('departures_by_mode', {})} "
              f"first {wk.get('first_departure')} last {wk.get('last_departure')} | fastest "
              f"{fh and (fh['fastest']['depart'], fh['fastest']['arrive'], fh['fastest_min'])} | after 09:00 "
              f"{fh and fh['after_0900'] and (fh['after_0900']['depart'], fh['after_0900']['arrive'])} | "
              f"last return {th and (th['leave'], th['arrive_hub'], th['minutes'])} | far {far}")

