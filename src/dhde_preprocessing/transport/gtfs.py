"""
GTFS-JP loading and a small journey planner for the transport track.

Feeds come as zip files whose tables may sit in a sub-folder and use .txt or
.csv (both happen in the Fukui feeds). Every id is prefixed with the feed id,
so several feeds can be merged into one network.

The planner is a Connection Scan (Dibbelt et al. 2013) over one day's
connections (one trip moving from one stop to the next), with walking
transfers between stops closer than `transfer_radius_m`. It answers the two
questions the access cards need:

- earliest arrival anywhere, leaving a set of stops at time t
- latest departure from anywhere that still reaches a set of stops by time t
"""
from __future__ import annotations

import bisect
import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

INF = 10**9
ROUTE_MODES = {0: "rail", 1: "rail", 2: "rail", 3: "bus", 4: "ferry", 7: "rail", 11: "bus", 12: "rail"}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p = math.radians
    a = math.sin(p(lat2 - lat1) / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def to_seconds(hms: str) -> int | None:
    """'25:10:00' -> 90600. GTFS times run past 24:00 for trips after midnight."""
    if not isinstance(hms, str) or not hms.strip():
        return None
    h, m, s = (int(x) for x in hms.strip().split(":"))
    return h * 3600 + m * 60 + s


def hhmm(seconds: int | float | None) -> str | None:
    if seconds is None or seconds >= INF or seconds <= -INF:
        return None
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _table(z: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    for n in z.namelist():
        base = n.rsplit("/", 1)[-1]
        if base.rsplit(".", 1)[0] == name and base.rsplit(".", 1)[-1] in ("txt", "csv"):
            return pd.read_csv(io.BytesIO(z.read(n)), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    return None


@dataclass
class Feed:
    id: str
    mode: str
    stops: pd.DataFrame
    routes: pd.DataFrame
    trips: pd.DataFrame
    stop_times: pd.DataFrame
    calendar: pd.DataFrame
    calendar_dates: pd.DataFrame
    shapes: pd.DataFrame | None
    agency: str
    valid_from: date | None
    valid_to: date | None
    # fare tables: (route_id, origin zone, destination zone) -> yen; stop_id -> zone_id
    fares: dict[tuple[str, str, str], int] = field(default_factory=dict)
    zones: dict[str, str] = field(default_factory=dict)


def _ymd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def load_feed(path: Path, feed_id: str, mode: str) -> Feed:
    z = zipfile.ZipFile(path)
    p = f"{feed_id}:"
    stops = _table(z, "stops")
    stops = stops[stops["stop_lat"].str.strip() != ""].copy()
    stops["stop_id"] = p + stops["stop_id"]
    stops["lat"] = stops["stop_lat"].astype(float)
    stops["lon"] = stops["stop_lon"].astype(float)
    if "location_type" in stops:  # keep boarding points, drop station parents
        stops = stops[stops["location_type"].isin(["", "0"])]
    routes = _table(z, "routes")
    routes["route_id"] = p + routes["route_id"]
    routes["mode"] = [ROUTE_MODES.get(int(t) if str(t).isdigit() else 3, mode) for t in routes["route_type"]]
    routes["name"] = [
        (ln or sn or rid.split(":", 1)[1]).strip()
        for ln, sn, rid in zip(routes.get("route_long_name", ""), routes.get("route_short_name", ""), routes["route_id"])
    ]
    trips = _table(z, "trips")
    trips["trip_id"] = p + trips["trip_id"]
    trips["route_id"] = p + trips["route_id"]
    trips["service_id"] = p + trips["service_id"]
    if "shape_id" in trips:
        trips["shape_id"] = [p + s if s else "" for s in trips["shape_id"]]
    st = _table(z, "stop_times")
    st["trip_id"] = p + st["trip_id"]
    st["stop_id"] = p + st["stop_id"]
    st["seq"] = st["stop_sequence"].astype(int)
    st["arr"] = [to_seconds(a) if a else to_seconds(d) for a, d in zip(st["arrival_time"], st["departure_time"])]
    st["dep"] = [to_seconds(d) if d else to_seconds(a) for a, d in zip(st["arrival_time"], st["departure_time"])]
    st = st.dropna(subset=["arr", "dep"]).sort_values(["trip_id", "seq"])
    cal = _table(z, "calendar")
    cal = cal.copy() if cal is not None else pd.DataFrame(columns=["service_id"])
    if len(cal):
        cal["service_id"] = p + cal["service_id"]
    cd = _table(z, "calendar_dates")
    cd = cd.copy() if cd is not None else pd.DataFrame(columns=["service_id", "date", "exception_type"])
    if len(cd):
        cd["service_id"] = p + cd["service_id"]
    shapes = _table(z, "shapes")
    if shapes is not None and len(shapes):
        shapes["shape_id"] = p + shapes["shape_id"]
        shapes["seq"] = shapes["shape_pt_sequence"].astype(int)
        shapes["lat"] = shapes["shape_pt_lat"].astype(float)
        shapes["lon"] = shapes["shape_pt_lon"].astype(float)
    ag = _table(z, "agency")
    info = _table(z, "feed_info")
    vf = vt = None
    if info is not None and len(info) and info.iloc[0].get("feed_start_date"):
        vf, vt = _ymd(info.iloc[0]["feed_start_date"]), _ymd(info.iloc[0]["feed_end_date"])
    elif len(cal):
        vf, vt = min(_ymd(s) for s in cal["start_date"]), max(_ymd(s) for s in cal["end_date"])
    fares: dict[tuple[str, str, str], int] = {}
    fa, fr = _table(z, "fare_attributes"), _table(z, "fare_rules")
    if fa is not None and fr is not None and len(fr):
        price = {r.fare_id: int(float(r.price)) for r in fa.itertuples() if str(r.price).strip()}
        for r in fr.itertuples():
            if r.fare_id in price:
                key = (p + r.route_id if getattr(r, "route_id", "") else "", getattr(r, "origin_id", "") or "", getattr(r, "destination_id", "") or "")
                fares[key] = min(price[r.fare_id], fares.get(key, 10**9))
    zones = dict(zip(stops["stop_id"], stops["zone_id"])) if "zone_id" in stops else {}
    return Feed(feed_id, mode, stops, routes, trips, st, cal, cd, shapes,
                ag.iloc[0]["agency_name"] if ag is not None and len(ag) else feed_id, vf, vt, fares, zones)


WEEKDAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def active_services(feed: Feed, d: date) -> set[str]:
    ymd = d.strftime("%Y%m%d")
    on: set[str] = set()
    col = WEEKDAY_COLS[d.weekday()]
    for _, r in feed.calendar.iterrows():
        if r.get(col) == "1" and r["start_date"] <= ymd <= r["end_date"]:
            on.add(r["service_id"])
    for _, r in feed.calendar_dates[feed.calendar_dates["date"] == ymd].iterrows():
        (on.add if r["exception_type"] == "1" else on.discard)(r["service_id"])
    return on


def service_date(feed: Feed, wanted: date) -> date:
    """`wanted` if the feed covers it. Otherwise the same weekday 52 weeks
    earlier (or later), repeated until inside the feed's validity, so an
    expired feed still describes the same season (seasonal buses such as the
    Rainbow Line summit bus only run July to November). Failing that, the
    same weekday as late as possible in the feed."""
    if feed.valid_from is None or (feed.valid_from <= wanted <= feed.valid_to):
        return wanted
    step = timedelta(weeks=52) if wanted > feed.valid_to else -timedelta(weeks=52)
    d = wanted
    for _ in range(5):
        d -= step
        if feed.valid_from <= d <= feed.valid_to:
            return d
    d = feed.valid_to if wanted > feed.valid_to else feed.valid_from + timedelta(days=6)
    while d.weekday() != wanted.weekday():
        d -= timedelta(days=1)
    for _ in range(8):  # step back over a week that runs a holiday timetable (no trips)
        if active_services(feed, d):
            return d
        d -= timedelta(days=7)
    return d


@dataclass
class Network:
    """One day's merged network: stops, the calls each active trip makes, and connections."""
    stops: pd.DataFrame
    calls: pd.DataFrame  # one row per (trip, stop) for active trips, with route mode/name
    conns: list[tuple[int, int, int, int, str]] = field(default_factory=list)  # dep, arr, from_idx, to_idx, trip
    conn_deps: list[int] = field(default_factory=list)  # conns' departure times, for bisect
    stop_index: dict[str, int] = field(default_factory=dict)
    footpaths: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # idx -> [(idx, seconds)]
    trip_info: dict[str, tuple[str, str, str, str]] = field(default_factory=dict)  # trip -> (mode, route name, feed, route_id)
    used_dates: dict[str, str] = field(default_factory=dict)
    feeds: dict[str, "Feed"] = field(default_factory=dict)


def build_network(feeds: list[Feed], wanted: date, walk: dict) -> Network:
    calls_parts, used = [], {}
    for f in feeds:
        d = service_date(f, wanted)
        used[f.id] = d.isoformat()
        svc = active_services(f, d)
        trips = f.trips[f.trips["service_id"].isin(svc)]
        st = f.stop_times[f.stop_times["trip_id"].isin(trips["trip_id"])]
        st = st.merge(trips[["trip_id", "route_id"]], on="trip_id").merge(
            f.routes[["route_id", "mode", "name"]], on="route_id")
        st["feed"] = f.id
        calls_parts.append(st[["trip_id", "stop_id", "seq", "arr", "dep", "route_id", "mode", "name", "feed"]])
    calls = pd.concat(calls_parts, ignore_index=True).sort_values(["trip_id", "seq"]).reset_index(drop=True)
    stops = pd.concat([f.stops[["stop_id", "stop_name", "lat", "lon"]].assign(feed=f.id) for f in feeds],
                      ignore_index=True).drop_duplicates("stop_id").reset_index(drop=True)
    net = Network(stops=stops, calls=calls, used_dates=used, feeds={f.id: f for f in feeds})
    net.stop_index = {s: i for i, s in enumerate(stops["stop_id"])}
    first_calls = calls.drop_duplicates("trip_id")
    net.trip_info = dict(zip(first_calls["trip_id"], zip(first_calls["mode"], first_calls["name"], first_calls["feed"], first_calls["route_id"])))
    idx = calls["stop_id"].map(net.stop_index)
    calls = calls[idx.notna()]
    idx = idx[idx.notna()].astype(int).to_numpy()
    t, dep, arr = calls["trip_id"].to_numpy(), calls["dep"].to_numpy(), calls["arr"].to_numpy()
    conns = [(int(dep[i]), int(arr[i + 1]), int(idx[i]), int(idx[i + 1]), t[i])
             for i in range(len(t) - 1) if t[i] == t[i + 1]]
    conns.sort(key=lambda c: (c[0], c[1]))
    net.conns = conns
    net.conn_deps = [c[0] for c in conns]
    net.footpaths = _footpaths(stops, walk)
    return net


def walk_seconds(dist_m: float, walk: dict) -> int:
    return int(round(dist_m * walk["detour_factor"] / walk["speed_m_per_min"] * 60))


def _footpaths(stops: pd.DataFrame, walk: dict) -> dict[int, list[tuple[int, int]]]:
    radius = walk["transfer_radius_m"]
    buffer = walk["transfer_buffer_min"] * 60
    lat, lon = stops["lat"].to_numpy(), stops["lon"].to_numpy()
    cell = radius / 111000.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(zip(lat, lon)):
        grid.setdefault((int(a / cell), int(b / cell)), []).append(i)
    out: dict[int, list[tuple[int, int]]] = {}
    for i, (a, b) in enumerate(zip(lat, lon)):
        gi, gj = int(a / cell), int(b / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j in grid.get((gi + di, gj + dj), []):
                    if j == i:
                        continue
                    d = haversine_m(a, b, lat[j], lon[j])
                    if d <= radius:
                        out.setdefault(i, []).append((j, walk_seconds(d, walk) + buffer))
    return out


def earliest_arrival(net: Network, sources: dict[int, int]) -> list[int]:
    """Earliest arrival at every stop. `sources`: stop idx -> time you can be there."""
    ea = [INF] * len(net.stop_index)
    for s, t0 in sources.items():
        ea[s] = min(ea[s], t0)
        for j, w in net.footpaths.get(s, []):
            ea[j] = min(ea[j], t0 + w)
    on_trip: set[str] = set()
    first = bisect.bisect_left(net.conn_deps, min(sources.values())) if sources else len(net.conns)
    for dep, arr, a, b, trip in net.conns[first:]:
        if trip in on_trip or ea[a] <= dep:
            on_trip.add(trip)
            if arr < ea[b]:
                ea[b] = arr
                for j, w in net.footpaths.get(b, []):
                    if arr + w < ea[j]:
                        ea[j] = arr + w
    return ea


def latest_departure(net: Network, targets: dict[int, int]) -> list[int]:
    """Latest time you can leave every stop and still reach a target in time.
    `targets`: stop idx -> latest time you must be there. Mirror of
    earliest_arrival, scanning connections backwards."""
    ld = [-INF] * len(net.stop_index)
    for s, t0 in targets.items():
        ld[s] = max(ld[s], t0)
        for j, w in net.footpaths.get(s, []):
            ld[j] = max(ld[j], t0 - w)
    # Connections are scanned latest first, so a trip's later connections come
    # first: once one of them reaches a target, every earlier one on it does too.
    trip_ok: set[str] = set()
    for dep, arr, a, b, trip in reversed(net.conns):
        if trip in trip_ok or arr <= ld[b]:
            trip_ok.add(trip)
            if dep > ld[a]:
                ld[a] = dep
                for j, w in net.footpaths.get(a, []):
                    if dep - w > ld[j]:
                        ld[j] = dep - w
    return ld


def journey(net: Network, sources: dict[int, int], targets: set[int], change_penalty: int = 0) -> dict | None:
    """Earliest-arrival journey from `sources` (stop idx -> time) to the
    earliest-reached stop in `targets`, with its legs. Same scan as
    earliest_arrival, but each stop remembers how it was reached.

    `change_penalty` (seconds) is added before boarding any bus after the
    first, so a trip with fewer changes wins unless an extra bus saves more
    than that. The legs keep their real times; the penalty only steers."""
    ea = [INF] * len(net.stop_index)
    parent: list[tuple | None] = [None] * len(ea)  # ('src',) | ('walk', from, start) | ('ride', board_ci, ci)
    for s, t0 in sources.items():
        if t0 < ea[s]:
            ea[s], parent[s] = t0, ("src",)
    for s, t0 in sources.items():
        for j, w in net.footpaths.get(s, []):
            if t0 + w < ea[j]:
                ea[j], parent[j] = t0 + w, ("walk", s, t0)
    board: dict[str, int] = {}
    first = bisect.bisect_left(net.conn_deps, min(sources.values())) if sources else len(net.conns)
    for ci in range(first, len(net.conns)):
        dep, arr, a, b, trip = net.conns[ci]
        ready = ea[a] + (change_penalty if parent[a] is not None and parent[a][0] != "src" else 0)
        if trip in board or ready <= dep:
            board.setdefault(trip, ci)
            if arr < ea[b]:
                ea[b], parent[b] = arr, ("ride", board[trip], ci)
                for j, w in net.footpaths.get(b, []):
                    if arr + w < ea[j]:
                        ea[j], parent[j] = arr + w, ("walk", b, arr)
    best = min(targets, key=lambda s: ea[s], default=None)
    if best is None or ea[best] >= INF:
        return None
    names = net.stops["stop_name"]
    stop_ids = net.stops["stop_id"]
    legs, s = [], best
    while parent[s][0] != "src":
        p = parent[s]
        if p[0] == "ride":
            bc, c = net.conns[p[1]], net.conns[p[2]]
            mode, route, feed, route_id = net.trip_info[c[4]]
            legs.append({"mode": mode, "route": route, "feed": feed, "from": names[bc[2]], "depart": hhmm(bc[0]),
                         "to": names[s], "arrive": hhmm(c[1]), "_dep": bc[0],
                         "_route_id": route_id, "_from_id": stop_ids[bc[2]], "_to_id": stop_ids[s]})
            s = bc[2]
        else:
            legs.append({"mode": "walk", "from": names[p[1]], "to": names[s],
                         "minutes": max(1, round((ea[s] - p[2]) / 60)), "_dep": p[2]})
            s = p[1]
    legs.reverse()
    rides = [leg for leg in legs if leg["mode"] != "walk"]
    depart = rides[0]["_dep"] if rides else sources[s]
    if rides and legs[0]["mode"] == "walk":  # a walk to the first stop counts as part of the trip
        depart = rides[0]["_dep"] - legs[0]["minutes"] * 60
    for leg in legs:
        leg.pop("_dep")
        if leg["mode"] != "walk":
            leg["fare_yen"] = leg_fare(net, leg)
        for k in ("_route_id", "_from_id", "_to_id"):
            leg.pop(k, None)
    fares = [leg.get("fare_yen") for leg in legs if leg["mode"] != "walk"]
    return {"depart": hhmm(depart), "arrive": hhmm(ea[best]), "stop": int(best),
            # adult cash fare, sum of the legs (each bus boarded is paid separately); None if any leg's fare is unknown
            "fare_yen": sum(fares) if fares and all(f is not None for f in fares) else None,
            # whole minutes between the displayed clock times, so the numbers always add up
            "minutes": ea[best] // 60 - depart // 60, "legs": legs}


def leg_fare(net: Network, leg: dict) -> int | None:
    """Adult fare for one ride from the operator's fare table: by route and
    origin/destination zone, then any route, then a flat fare."""
    f = net.feeds.get(leg["feed"])
    if f is None or not f.fares:
        return None
    o, d = f.zones.get(leg["_from_id"], ""), f.zones.get(leg["_to_id"], "")
    for key in ((leg["_route_id"], o, d), ("", o, d), (leg["_route_id"], "", ""), ("", "", "")):
        if key in f.fares:
            return f.fares[key]
    return None
