# Data gaps per node

What is missing for each node, what the pipeline uses instead today, and
suggested sources to close the gap. Kept here so gaps stay visible and
reviewable instead of living only in coverage reports. Last checked
2026-09-27; update this file whenever a config changes.

## Coverage summary

| Node | Camera | Weather | RSI | Hotel | Survey | Traffic |
|---|---|---|---|---|---|---|
| Tojinbo | Yes | Yes | Partial (Sakai City 2026 only) | Regional | Yes | No |
| Fukui Station | Yes | Yes | Partial (prefecture total) | Own feed | Yes | CCTV, 2.7km |
| Katsuyama | No (proxy) | Yes | Yes | Regional | Yes | Permanent, 5.3km |
| Rainbow Line | Vehicles only | Yes | Yes | Own feed (from 2025-06) | Yes | CCTV, 5km |
| Awara Onsen | No (proxy) | Yes | Yes | Own feed | Yes | No |
| Eiheiji | No (proxy) | Partial (12km) | Yes | Regional | Yes | Permanent, 2.5km |
| Mikuni Port | No (proxy) | Yes | Partial (Sakai City 2026 only) | Regional | Yes | No |
| Ono Castle Town | No (proxy) | Yes | Yes | Regional | Yes | Permanent, 3.9km |
| Maruoka Castle | No (proxy) | Partial (no humidity/sun) | Partial (Sakai City 2026 only) | Regional | Yes | CCTV, 1.3km |

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
- **Suggested:** any people-flow source for Katsuyama or Ono (e.g. Dinosaur
  Museum visitor counts) would replace a weak proxy with a real count.

### Hotel
- **Area-specific feeds:** Fukui Station (fukui-station-kanko-reservation),
  Awara Onsen (fukui-kanko-reservation), Rainbow Line
  (mikatagoko-kanko-reservation).
- **Regional fallback:** Tojinbo, Katsuyama, Eiheiji, Mikuni Port, Ono,
  Maruoka.
- **Trade-off:** the Mikata Five Lakes feed for Rainbow Line starts
  2025-06-05 (the regional feed starts 2024-12), so Rainbow Line hotel
  history is ~6 months shorter.
- **Suggested:** code4fukui/dinosaur-opendata for Katsuyama (not yet
  checked for reservation data); Rakuten data in
  dhde-tourism-ai/fukui-hotel-data-combined for node-level prices.

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
- **Considered, not used:** Google Maps (Routes API). It gives travel time
  in traffic (congestion), not vehicle counts, has no history, and its
  terms restrict storing results, which a daily model needs. It is also
  paid beyond a free monthly allowance. TomTom / HERE flow APIs have free
  tiers but similar limits (speed, not counts; storage terms to check).

### Weather (JMA ETRN)
- **Eiheiji** uses Fukui station (~12km). The closer Miyama station
  records precipitation only.
- **Maruoka** uses Harue (~4.7km), which has no humidity or sunshine.
- **Tojinbo** Mikuni station moved in 2009, now ~4km away.

## Dropped for now
- **Kanazawa Spillover:** Ishikawa Prefecture, so no code4fukui dataset
  covers it. Removed from configs per tech lead review of PR #6.
  Revisit if an Ishikawa source (hotel, survey, search trends, or
  Shinkansen ridership) becomes available.
