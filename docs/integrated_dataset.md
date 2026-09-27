# Integrated dataset

One table for the six Fukui nodes, one row per node per day, the same
columns for every node. Built from the node master tables by
`src/dhde_preprocessing/integrate.py`:

```bash
python scripts/build_node.py --node fukui_station   # ...and the other five, or --all
python scripts/build_integrated.py
```

Outputs, in `output/`:

- `integrated_fukui_train.parquet` / `.csv`: **for model training.**
  2024-12-01 (the weather start date in every Fukui config) to yesterday
  in JST. Change it with `--start` / `--end`.
- `integrated_fukui.parquet` / `.csv`: the same table, continued to the
  last date any source has (future hotel and museum bookings, for the
  dashboard). Don't train on rows after yesterday.
- `integrated_fukui_report.json`: both windows, what each cleaning rule
  changed per node, the camera outage days, and the non-null count of
  every training column per node

## Rules that avoid bias

| Rule | Why |
|---|---|
| Every day in the window has a row per node | A missing day is a row of missing values, not an absent row |
| Training table: nothing from today or later | Today is partial; later rows are bookings so far (hotel, museum), which would leak future information |
| Survey counts are 0 on days without responses, between each count's first and last day | Days with no responses had no row, so read as missing instead of 0 |
| RSI: prefecture-filled days are missing for nodes with a town file | Town and prefecture numbers differ by orders of magnitude; see `rsi_level` |
| Traffic: days under 24 observed hours or reading 0 all day are missing | Partial days under-count; Fukui Station's counter reads 0 from 2026-08-09 |
| Camera (in `camera.py`): repeated export days are kept once; a person sensor reading 0 is missing | The raw files repeat 2025-09-24 → 09-30, which duplicated rows; every sensor read 0 on 2025-09-26 → 28 (system down) |
| Camera: on days every people camera is down, other nodes' all-zero camera counts are missing too | Rainbow Line's vehicle gates read 0 on 2025-09-26 → 28 because of the outage. Its other zero days stay: the gates really see no cars on many days (mostly Jan–Feb 2026) |
| RSI (in `rsi.py`): zero days before a file starts tracking are missing | 永平寺町 reads 0 on everything until 2026-06-05 |
| Missing is always empty, never 0 | Use the `has_<source>` columns to tell "no data" from "zero" |

## Columns

The table always has the same 69 columns, in the same order
(`EXPECTED_COLUMNS` in `integrate.py`), even when a source fails on a run
or has no data yet. An expected column with no data at all is listed
under `warnings` in `integrated_fukui_report.json` (today: the `road_*`
columns). A column a source adds later is left out and named there too,
until it's added to `EXPECTED_COLUMNS`.

**Row keys and context**

| Column | Meaning |
|---|---|
| `date` | Day (JST) |
| `node_key` | `fukui_station`, `tojinbo`, `katsuyama`, `rainbow_line`, `awara_onsen`, `eiheiji` |
| `day_of_week` | 0 = Monday … 6 = Sunday |
| `is_holiday` | 1 on a Japanese public holiday |
| `hotel_scope` | Where the hotel columns come from: `station-specific`, `area-specific`, or `regional` (the same echizen-coast feed at Tojinbo, Katsuyama and Eiheiji: identical values, not three observations) |
| `weather_station` | JMA station used (Eiheiji uses Fukui, ~12km away) |
| `has_<source>` | 1 if the row has any value from that source (camera, weather, rsi, hotel, traffic, road_congestion, survey, footfall_proxy, visitor_reservation) |

**Values** (units and cleaning are described in each source module)

| Prefix | Source | Main columns | Nodes |
|---|---|---|---|
| `camera_` | People-flow cameras | `camera_count` (people); `camera_gate1_vehicle_count`, `camera_gate2_vehicle_count` (cars, Rainbow Line) | Fukui Station, Tojinbo, Rainbow Line |
| `weather_` | JMA hourly, daily means (`precip` is the daily sum) | `weather_precip`, `weather_temp`, `weather_wind`, `weather_sun`, `weather_humidity`, `weather_snow_depth` | All |
| `rsi_` | Google Maps / search intent | `rsi_map_views`, `rsi_search_views`, `rsi_directions`, … and `rsi_level` (`area` or `prefecture`) | All (Tojinbo 2026 only, Eiheiji from 2026-06) |
| `hotel_` | Reservation feeds, cleaned | `hotel_occ`, `hotel_adr`, `hotel_revpar`, `hotel_n_room`, `hotel_n_people_lead7` (guests booked as of 7+ days before), … and flags `hotel_was_imputed`, `hotel_is_stale`, … | All (Rainbow Line from 2025-06) |
| `traffic_` | JARTIC road counter | `traffic_volume_total`, `traffic_hours_observed` | Fukui Station, Katsuyama, Rainbow Line, Eiheiji (last ~90 days only) |
| `road_` | TomTom road congestion | `road_congestion` (1 - mean relative speed), `road_relative_speed_mean`, `road_relative_speed_min`, `road_snapshots` | All six, once the collector has history (empty today) |
| `survey_` | Visitor survey responses | `survey_response_count` | All |
| `proxy_` | Stand-ins for nodes without a camera | `proxy_camera_count` (Tojinbo's camera, for Awara Onsen), `proxy_survey_count` | Awara Onsen, Eiheiji, Katsuyama |
| `attraction_` | Dinosaur Museum advance bookings | `attraction_reserved_visitors` (~57% of all visitors), `attraction_reserved_visitors_lead7` (booked as of 7 days before; 0 on closing days) | Katsuyama |

## Not in this table

- Camera Face.csv demographic columns: a small, biased sample of each
  count (see `camera.py`); still in each `{node}_master.parquet`.
- Response-level survey answers (satisfaction, spending, free text): in
  `{node}_survey_responses.parquet`.
- Values for `road_*` (TomTom): the columns are there but empty until the
  hourly collector has run with `TOMTOM_API_KEY` set; they fill in
  automatically once it has.

## Known limits

- The traffic rule only runs in the integrated tables.
  `fukui_station_master.parquet` still has the broken counter's 0 days
  (every day from 2026-08-09), so don't read traffic from the master
  table directly. It stays out of `traffic.py` because
  `scripts/collect_live.py` saves that module's output as permanent
  history.
- Traffic and road congestion have only a few months of history.
- Survey counts depend on how many people answered the questionnaire, not
  only on how many visited.
