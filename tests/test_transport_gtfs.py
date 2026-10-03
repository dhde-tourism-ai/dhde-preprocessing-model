"""Planner and access metrics on a tiny synthetic network.

    H (hub) --bus 1--> M --bus 2--> N (node)      M and M2 are 100 m apart
                       M2 --bus 3--> N

Bus 1: H 08:00 -> M 08:20, H 09:00 -> M 09:20
Bus 2: M 08:30 -> N 09:00, M 17:00 -> N 17:30
Bus 3: M2 09:40 -> N 10:00
Back:  N 16:00 -> H 16:40 (bus 4), N 18:00 -> M 18:20 (bus 5, dead end)
"""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pytest

from dhde_preprocessing.transport import access as bt
from dhde_preprocessing.transport.gtfs import build_network, earliest_arrival, hhmm, latest_departure, load_feed, service_date, to_seconds

WALK = {"speed_m_per_min": 80, "detour_factor": 1.3, "transfer_radius_m": 300, "transfer_buffer_min": 3}
STOPS = {"H": (36.0, 136.0), "M": (36.1, 136.0), "M2": (36.1009, 136.0), "N": (36.2, 136.0)}
TRIPS = {
    "t1a": ("r1", [("H", "08:00:00"), ("M", "08:20:00")]),
    "t1b": ("r1", [("H", "09:00:00"), ("M", "09:20:00")]),
    "t2a": ("r2", [("M", "08:30:00"), ("N", "09:00:00")]),
    "t2b": ("r2", [("M", "17:00:00"), ("N", "17:30:00")]),
    "t3": ("r3", [("M2", "09:40:00"), ("N", "10:00:00")]),
    "t4": ("r4", [("N", "16:00:00"), ("H", "16:40:00")]),
    "t5": ("r5", [("N", "18:00:00"), ("M", "18:20:00")]),
}


@pytest.fixture
def feed_zip(tmp_path: Path) -> Path:
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("GTFS/agency.txt", "agency_id,agency_name,agency_url,agency_timezone\na,Test Bus,http://x,Asia/Tokyo\n")
        z.writestr("GTFS/stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n"
                   + "".join(f"{k},{k},{la},{lo}\n" for k, (la, lo) in STOPS.items()))
        z.writestr("GTFS/routes.txt", "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                   + "".join(f"r{i},a,{i},Route {i},3\n" for i in range(1, 6)))
        z.writestr("GTFS/calendar.csv", "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                   "wk,1,1,1,1,1,0,0,20260101,20261231\n")
        z.writestr("GTFS/calendar_dates.txt", "service_id,date,exception_type\nwk,20261014,2\n")
        z.writestr("GTFS/trips.txt", "route_id,service_id,trip_id\n" + "".join(f"{r},wk,{t}\n" for t, (r, _) in TRIPS.items()))
        rows = "".join(f"{t},{tm},{tm},{s},{i + 1}\n" for t, (_, calls) in TRIPS.items() for i, (s, tm) in enumerate(calls))
        z.writestr("GTFS/stop_times.txt", "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + rows)
    return path


def net_for(feed_zip: Path, d: date = date(2026, 10, 7)):
    return build_network([load_feed(feed_zip, "f", "bus")], d, WALK)


def idx(net, stop: str) -> int:
    return net.stop_index[f"f:{stop}"]


def test_times():
    assert to_seconds("25:10:00") == 90600
    assert hhmm(90600) == "25:10"


def test_earliest_arrival_with_transfer_and_footpath(feed_zip: Path):
    net = net_for(feed_zip)
    ea = earliest_arrival(net, {idx(net, "H"): to_seconds("08:00:00")})
    assert hhmm(ea[idx(net, "N")]) == "09:00"  # bus 1 then bus 2 at M
    ea = earliest_arrival(net, {idx(net, "H"): to_seconds("08:30:00")})
    # 09:00 bus to M (09:20), walk 100 m to M2 (+2 min walk, +3 min buffer), bus 3 at 09:40
    assert hhmm(ea[idx(net, "N")]) == "10:00"


def test_latest_departure_ignores_dead_end_trip(feed_zip: Path):
    net = net_for(feed_zip)
    ld = latest_departure(net, {idx(net, "H"): bt.DAY_END})
    assert hhmm(ld[idx(net, "N")]) == "16:00"  # 18:00 bus only reaches M, which has no onward trip


def test_calendar_dates_remove_service(feed_zip: Path):
    assert net_for(feed_zip, date(2026, 10, 14)).conns == []  # removed by calendar_dates
    assert net_for(feed_zip, date(2026, 10, 10)).conns == []  # Saturday: no service


def test_expired_feed_uses_same_week_a_year_earlier(feed_zip: Path):
    f = load_feed(feed_zip, "f", "bus")
    d = service_date(f, date(2027, 10, 6))
    assert d == date(2026, 10, 7) and d.weekday() == 2


def test_access_metrics(feed_zip: Path):
    net = net_for(feed_zip)
    hub = bt.node_stops(net, STOPS["H"], 50, WALK)
    node = bt.node_stops(net, STOPS["N"], 50, WALK)
    c = bt.service_counts(net, node)
    assert c["departures"] == 2 and c["arrivals"] == 3
    assert (c["first_departure"], c["last_departure"]) == ("16:00", "18:00")
    j = bt.journeys_from(net, hub, {"n": node})["n"]
    assert j["fastest_min"] == 60 and (j["fastest"]["depart"], j["fastest"]["arrive"]) == ("08:00", "09:00")
    nine = j["after_0900"]
    assert (nine["depart"], nine["arrive"], nine["minutes"]) == ("09:00", "10:00", 60)
    assert [leg["mode"] for leg in nine["legs"]] == ["bus", "walk", "bus"]  # change from M to M2 on foot
    back = bt.last_return(net, hub, node)
    assert (back["leave"], back["arrive_hub"], back["minutes"]) == ("16:00", "16:40", 40)
    assert back["legs"][0]["route"] == "Route 4"


def test_minutes_match_clock_times(feed_zip: Path):
    """A trip's minutes are always arrive minus depart as displayed (no rounding drift)."""
    net = net_for(feed_zip)
    hub = bt.node_stops(net, STOPS["H"], 50, WALK)
    node = bt.node_stops(net, STOPS["N"], 50, WALK)
    j = bt.journeys_from(net, hub, {"n": node})["n"]
    for trip in (j["fastest"], j["after_0900"]):
        assert trip["minutes"] == bt.clock(trip["arrive"]) - bt.clock(trip["depart"])


def test_long_distance_uses_best_gateway(feed_zip: Path):
    net = net_for(feed_zip)
    stops = {"h": bt.node_stops(net, STOPS["H"], 50, WALK), "n": bt.node_stops(net, STOPS["N"], 50, WALK)}
    cfg = {"long_distance": {"far": {"name": "Far", "name_ja": "遠", "change_min": 5, "basis": "test", "gateways": [
        {"name": "H", "name_ja": "H", "node": "h", "jr_min": 30},
        {"name": "M", "name_ja": "M", "lat": STOPS["M"][0], "lon": STOPS["M"][1], "radius_m": 50, "jr_min": 20},
    ]}}}
    far = bt.from_far(net, cfg, stops, WALK)
    assert far["h"]["far"]["minutes"] == 30  # at the gateway: JR leg only
    # via H: 30 + 5 + 60 = 95. Via M: 20 + 5 + 25, the quickest local trip being the
    # walk to M2 (2 min + 3 min to change, leaving 09:35) and bus 3 at 09:40 -> 10:00
    assert (far["n"]["far"]["minutes"], far["n"]["far"]["via"]) == (50, "M")


def test_reference_days_are_wed_sat_sun():
    days = bt.reference_days(date(2026, 10, 2))
    assert [d.weekday() for d in days.values()] == [2, 5, 6]
    assert all(d >= date(2026, 10, 2) for d in days.values())


def test_fare_override_only_for_its_stop_pair():
    over = [{"feed": "k", "route": "Liner", "yen": 1000, "from": "2026-10-01", "from_stop": "Station", "to_stop": "Temple"}]
    full = {"legs": [{"mode": "bus", "feed": "k", "route": "Liner", "from": "Station", "to": "Temple", "fare_yen": 750}]}
    part = {"legs": [{"mode": "bus", "feed": "k", "route": "Liner", "from": "Station", "to": "Midway", "fare_yen": 630},
                     {"mode": "walk", "from": "Midway", "to": "X", "minutes": 4},
                     {"mode": "bus", "feed": "k", "route": "Other", "from": "X", "to": "Y", "fare_yen": 300}]}
    bt.apply_fare_overrides(full, over, date(2026, 10, 7))
    bt.apply_fare_overrides(part, over, date(2026, 10, 7))
    assert full["fare_yen"] == 1000
    assert part["fare_yen"] == 930  # partial ride keeps its own fare
    before = {"legs": [{"mode": "bus", "feed": "k", "route": "Liner", "from": "Station", "to": "Temple", "fare_yen": 750}]}
    bt.apply_fare_overrides(before, over, date(2026, 9, 30))
    assert before["fare_yen"] == 750  # not yet in force


def test_change_penalty_prefers_fewer_buses(feed_zip: Path):
    """From H at 09:00 the only way to N is bus 1 + walk + bus 3; with no
    alternative, the penalty must not lose the journey."""
    net = net_for(feed_zip)
    from dhde_preprocessing.transport.gtfs import journey
    j = journey(net, {idx(net, "H"): 9 * 3600}, {idx(net, "N")}, change_penalty=600)
    assert j is not None and j["arrive"] == "10:00"
