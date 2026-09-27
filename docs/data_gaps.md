# Data gaps per node

What is missing for each node, what the pipeline uses instead today, and
suggested sources to close the gap. Kept here so gaps stay visible and
reviewable instead of living only in coverage reports. Last checked
2026-09-27; update this file whenever a config changes.

Scope is the six priority nodes from the tech lead review of PR #6.
Nodes dropped for now are listed at the end with what was found for them.

## Coverage summary

| Node | Camera | Weather | RSI | Hotel | Survey | Traffic | Road congestion |
|---|---|---|---|---|---|---|---|
| Tojinbo | Yes | Yes | Partial (Sakai City 2026 only) | Regional | Yes | No | TomTom |
| Fukui Station | Yes | Yes | Partial (prefecture total) | Own feed | Yes | CCTV, 2.7km | TomTom |
| Katsuyama | No (museum bookings + proxy) | Yes | Yes | Regional | Yes | Permanent, 5.3km | TomTom |
| Rainbow Line | Vehicles only | Yes | Yes | Own feed (from 2025-06) | Yes | CCTV, 5km | TomTom |
| Awara Onsen | No (proxy) | Yes | Yes | Own feed | Yes | No | TomTom |
| Eiheiji | No (proxy) | Partial (12km) | Yes | Regional | Yes | Permanent, 2.5km | TomTom |

"Regional" = code4fukui/echizen-coast-kanko-reservation, used where no
area-specific reservation feed exists.

## Gaps and current handling

### Camera / people flow
- **Missing:** Katsuyama, Awara Onsen, Eiheiji.
  code4fukui/fukui-kanko-people-flow-data only has sensors at Tojinbo,
  Fukui Station and the two Rainbow Line parking gates.
- **Rainbow Line** gates count vehicles (LicensePlate.csv), not people.
- **Today:** camera stays `enabled: false` (reported as unavailable), and a
  `footfall_proxy` fills in from nearby signals in widening 5km / 15km
  circles, per tech lead review of PR #6. Proxy columns are labelled as
  proxies, never merged into camera counts:

  | Node | proxy_camera_count | proxy_survey_count |
  |---|---|---|
  | Awara Onsen | Tojinbo camera, 6.2km | 6 areas within 5km |
  | Eiheiji | none (Fukui Station excluded) | 2 areas within 5km |
  | Katsuyama | none within 15km | 6 areas within 5km |

  Hotel occupancy (`occ`) is the third proxy signal and already comes from
  the hotel source. Fukui Station's camera is marked `proxy_eligible:
  false`: it's a city transport hub, too unlike a temple or museum to stand
  in (this is why Eiheiji, 12.2km away, gets no camera proxy).
- **Katsuyama has a near-direct visitor signal instead:** Dinosaur Museum
  advance entry bookings from code4fukui/dinosaur-opendata, daily since
  2023-09 (`visitor_reservation` source, `reserved_visitors` column).
  Reserved entries only: ~57% of reported FY2025 visitors (738k of 1.30M),
  so walk-ins and same-day tickets are missing. Future visit dates are
  bookings so far (`bookings_final` = False).

### Hotel
- **Area-specific feeds:** Fukui Station (fukui-station-kanko-reservation),
  Awara Onsen (fukui-kanko-reservation), Rainbow Line
  (mikatagoko-kanko-reservation).
- **Regional fallback:** Tojinbo, Katsuyama, Eiheiji.
- **Trade-off:** the Mikata Five Lakes feed for Rainbow Line starts
  2025-06-05 (the regional feed starts 2024-12), so Rainbow Line hotel
  history is ~6 months shorter.
- **Checked, no better hotel source:** every code4fukui reservation repo
  (only the three area feeds above plus the regional one exist).
  dinosaur-opendata is museum entry bookings, not hotels (used as visitor
  data above). The Rakuten file in dhde-tourism-ai/fukui-hotel-data-combined
  is a one-off price snapshot of 250 hotels for 5 check-in dates (Oct 2026),
  not a daily series, so it can't give occupancy.

### RSI (fukui-kanko-trend-data)
- **Sakai City** (Tojinbo) only exists in the 2026 folder, so only 29 of
  Tojinbo's 994 RSI days are town-level; the rest are filled from the
  prefecture-wide total. This predates PR #6.
- **Fukui City** (Fukui Station) is not tracked; prefecture total only.
- **Each row now says its level** (`rsi_level`: `area` or `prefecture`).
  The integrated training table treats prefecture-filled days as missing
  for nodes that have a town file (see [`integrated_dataset.md`](integrated_dataset.md)).
- **Zero days before tracking starts are missing**, not 0: 永平寺町
  (Eiheiji) reads 0 on every metric until 2026-06-05, so Eiheiji has
  ~110 days of town-level RSI.

### Traffic (JARTIC, api.jartic-open-traffic.org)
- **Missing:** Tojinbo (nearest point ~14km) and Awara Onsen (~8.1km) on
  both the CCTV and permanent-counter layers.
- **Flagged:** every enabled point is within 2.5 to 5.3km of its node but
  not confirmed to sit on the visitor access road.
- **No history from the API:** JARTIC keeps hourly data for roughly 3 to 4
  months only. `scripts/collect_live.py` (hourly GitHub Action) saves it to
  the `live-data` branch, and the build merges that saved history with each
  live pull (point `DHDE_LIVE_DATA_ROOT` at a checkout of the branch).
- **Suggested:** MLIT road traffic census 2021 (free CSV, covers
  prefectural roads such as Route 305 near Tojinbo), usable as a static
  baseline, not a daily signal:
  https://www.mlit.go.jp/road/census/r3/ and
  https://www.pref.fukui.lg.jp/doc/douken/census/r3census.html
- **The dashboard needs congestion, not counts.** Covered for all six
  nodes by the `road_congestion` source (TomTom Orbis traffic-flow tiles,
  free tier, no card; key in `TOMTOM_API_KEY`). Each road segment within
  2km gives relative speed (current / free-flow); `road_congestion` =
  1 - mean relative speed per day.
  - TomTom's older Flow Segment Data API has no data anywhere in Japan
    (even central Tokyo); only the Orbis tiles do.
  - Tiles give no confidence value, so rural reliability is unverified.
    First live snapshot (2026-09-27): Eiheiji and Rainbow Line read exactly
    free-flow (1.00), which may be real or a fallback to typical speeds.
    Cross-check against JARTIC at the four nodes that have both.
  - Live only: history comes from the same hourly collector and
    `live-data` branch. Without a key the source is reported unavailable.
  - **Open:** TomTom's terms on caching/storing data have not been
    verified yet (page only renders in a browser); check before relying on
    stored history in production.
- **Not used:** Google Maps (Routes API). Even its free allowance needs a
  billing account with a card, and its terms restrict storing results.

### Weather (JMA ETRN)
- **Eiheiji** uses Fukui station (~12km). The closer Miyama station
  records precipitation only.
- **Tojinbo** Mikuni station moved in 2009, now ~4km away.

## Site capacity
No site publishes a visitor capacity. Official visitor counts, parking and
other limits found so far, with sources and what's still missing, are in
[`site_capacity.md`](site_capacity.md).

## Dropped for now
Removed from configs per tech lead review of PR #6 (scope is the first six
nodes). The configs are in this PR's git history if they come back.
- **Kanazawa Spillover:** dropped as a Fukui node (no code4fukui dataset
  covers Ishikawa). Kanazawa is now covered as an Ishikawa node with its
  own prefecture's sources (`kanazawa`, see the README's Ishikawa and
  Toyama section).
- **Mikuni Port:** survey area 300076, RSI Sakai City (2026 only), weather
  Mikuni, regional hotel. No JARTIC point within 11km. Nearest camera is
  Tojinbo (3.3km).
- **Ono Castle Town:** survey area 300015, RSI 大野市, weather Ono (block
  0573), regional hotel, JARTIC permanent counter 6110840 (3.9km). Parking
  297 cars + 14 buses; castle closed Dec-Mar.
- **Maruoka Castle:** survey area 300008, RSI Sakai City (2026 only),
  weather Harue (no humidity/sunshine), regional hotel, JARTIC CCTV
  6810140 (1.3km). Parking 180 cars + 4 buses; keep closed for renovation.
