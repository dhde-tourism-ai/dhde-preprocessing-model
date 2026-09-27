# Data gaps per node

What is missing for each node, what the pipeline uses instead today, and
suggested sources to close the gap. Kept here so gaps stay visible and
reviewable instead of living only in coverage reports. Last checked
2026-09-27; update this file whenever a config changes.

## Coverage summary

| Node | Camera | Weather | RSI | Hotel | Survey | Traffic | Road congestion |
|---|---|---|---|---|---|---|---|
| Tojinbo | Yes | Yes | Partial (Sakai City 2026 only) | Regional | Yes | No | TomTom |
| Fukui Station | Yes | Yes | Partial (prefecture total) | Own feed | Yes | CCTV, 2.7km | TomTom |
| Katsuyama | No (museum bookings + proxy) | Yes | Yes | Regional | Yes | Permanent, 5.3km | TomTom |
| Rainbow Line | Vehicles only | Yes | Yes | Own feed (from 2025-06) | Yes | CCTV, 5km | TomTom |
| Awara Onsen | No (proxy) | Yes | Yes | Own feed | Yes | No | TomTom |
| Eiheiji | No (proxy) | Partial (12km) | Yes | Regional | Yes | Permanent, 2.5km | TomTom |
| Mikuni Port | No (proxy) | Yes | Partial (Sakai City 2026 only) | Regional | Yes | No | TomTom |
| Ono Castle Town | No (proxy) | Yes | Yes | Regional | Yes | Permanent, 3.9km | TomTom |
| Maruoka Castle | No (proxy) | Partial (no humidity/sun) | Partial (Sakai City 2026 only) | Regional | Yes | CCTV, 1.3km | TomTom |

"Regional" = code4fukui/echizen-coast-kanko-reservation, used where no
area-specific reservation feed exists.

## Gaps and current handling

### Camera / people flow
- **Missing:** Katsuyama, Awara Onsen, Eiheiji, Mikuni Port, Ono Castle
  Town, Maruoka Castle. code4fukui/fukui-kanko-people-flow-data only has
  sensors at Tojinbo, Fukui Station and the two Rainbow Line parking gates.
- **Rainbow Line** gates count vehicles (LicensePlate.csv), not people.
- **Today:** camera stays `enabled: false` (reported as unavailable), and a
  `footfall_proxy` fills in from nearby signals in widening 5km / 15km
  circles, per tech lead review of PR #6. Proxy columns are labelled as
  proxies, never merged into camera counts:

  | Node | proxy_camera_count | proxy_survey_count |
  |---|---|---|
  | Mikuni Port | Tojinbo camera, 3.3km | 6 areas within 5km |
  | Awara Onsen | Tojinbo camera, 6.2km | 6 areas within 5km |
  | Maruoka Castle | Fukui Station camera, 11.1km | 1 area within 5km |
  | Eiheiji | Fukui Station camera, 12.2km | 2 areas within 5km |
  | Katsuyama | none within 15km | 6 areas within 5km |
  | Ono Castle Town | none within 15km | 1 area within 5km |

  Hotel occupancy (`occ`) is the third proxy signal and already comes from
  the hotel source. A 30km circle was tried but reached Fukui Station (a
  city hub) for Katsuyama and Ono, too different a place to stand in.
- **Katsuyama now has a near-direct visitor signal:** Dinosaur Museum
  advance entry bookings from code4fukui/dinosaur-opendata, daily since
  2023-09 (`visitor_reservation` source, `reserved_visitors` column).
  Reserved entries only: ~57% of reported FY2025 visitors (738k of 1.30M),
  so walk-ins and same-day tickets are missing.
- **Suggested:** any people-flow source for Ono would replace its
  survey-only proxy with a real count.

### Hotel
- **Area-specific feeds:** Fukui Station (fukui-station-kanko-reservation),
  Awara Onsen (fukui-kanko-reservation), Rainbow Line
  (mikatagoko-kanko-reservation).
- **Regional fallback:** Tojinbo, Katsuyama, Eiheiji, Mikuni Port, Ono,
  Maruoka.
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
- **Sakai City** (Tojinbo, Mikuni Port, Maruoka) only exists in the 2026
  folder; earlier dates are filled from the prefecture-wide total.
- **Fukui City** (Fukui Station) is not tracked; prefecture total only.
- **Open question:** whether the modelling stage should use filled dates
  or treat them as missing.

### Traffic (JARTIC, api.jartic-open-traffic.org)
- **Missing:** Tojinbo (nearest point ~14km), Awara Onsen (~8.1km), Mikuni
  Port (~11.2km) on both the CCTV and permanent-counter layers.
- **Flagged:** every enabled point is within 1.3 to 5.3km of its node but
  not confirmed to sit on the visitor access road.
- **No history:** JARTIC keeps hourly data for roughly 3 to 4 months only.
  A scheduled daily pull is needed to build up history.
- **Suggested:** MLIT road traffic census 2021 (free CSV, covers
  prefectural roads such as Route 305 near Tojinbo), usable as a static
  baseline, not a daily signal:
  https://www.mlit.go.jp/road/census/r3/ and
  https://www.pref.fukui.lg.jp/doc/douken/census/r3census.html
- **The dashboard needs congestion, not counts.** Now covered for all nine
  nodes by the `road_congestion` source (TomTom Orbis traffic-flow tiles,
  free tier, no card; key in `TOMTOM_API_KEY`). Each road segment within
  2km gives relative speed (current / free-flow); `road_congestion` =
  1 - mean relative speed per day.
  - TomTom's older Flow Segment Data API has no data anywhere in Japan
    (even central Tokyo); only the Orbis tiles do.
  - Tiles give no confidence value, so rural reliability is unverified.
    First live snapshot (2026-09-27): Eiheiji and Rainbow Line read exactly
    free-flow (1.00), which may be real or a fallback to typical speeds.
    Cross-check against JARTIC at the six nodes that have both.
  - Live only: history builds only if the pipeline runs on a schedule.
  - **Open:** TomTom's terms on caching/storing data have not been
    verified yet (page only renders in a browser); check before relying on
    stored history in production.
- **Not used:** Google Maps (Routes API). Even its free allowance needs a
  billing account with a card, and its terms restrict storing results.

## Site capacity
No site publishes a visitor capacity. Official visitor counts, parking and
other limits found so far, with sources and what's still missing, are in
[`site_capacity.md`](site_capacity.md).

## Dropped for now
- **Kanazawa Spillover:** Ishikawa Prefecture, so no code4fukui dataset
  covers it. Removed from configs per tech lead review of PR #6.
  Revisit if an Ishikawa source (hotel, survey, search trends, or
  Shinkansen ridership) becomes available.
