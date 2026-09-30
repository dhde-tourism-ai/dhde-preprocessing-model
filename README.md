# dhde-preprocessing-model

The data and forecasting pipeline behind the DHDE tourism app
(https://dhde-tourism-ai.github.io/dhde-app/) for Fukui and Hokuriku, Japan.

It takes public tourism data (cameras, weather, hotel bookings, surveys,
search interest, traffic), cleans it, and builds one daily table per site.
It then forecasts visitors per site: daily for the next 7 days, and monthly
for the next 12.

**Main is live.** Every morning at 09:00 JST the app rebuilds its data from
this repo's `main` branch, so a merged change shows on the site the next
day. Work on a branch and open a pull request.

## Contents

- [How the pipeline works](#how-the-pipeline-works)
- [Quickstart](#quickstart)
- [What each step writes](#what-each-step-writes)
- [Sites (nodes)](#sites-nodes)
- [Docs](#docs)
- [Adding a site](#the-template-pattern)
- [Reference: how each source is handled](#non-obvious-things-found-while-building-this--read-before-extending)
- [Tests](#tests)

## How the pipeline works

```
raw public data
  -> build_node.py          one cleaned daily table per site ("master table")
  -> build_integrated.py    all Fukui sites in one table, same columns, ready for models
  -> build_forecast.py      7-day forecast per site, with a low/high range
  -> check_calibration.py   checks the conversion from measured counts to visitors

forecast_monthly.py         12-month forecast (downloads its own data, runs on its own)
```

Three rules hold throughout:

- **Missing stays missing.** A missing or broken value is left empty and
  flagged, never turned into 0 or made up.
- **No future data in forecasts.** Every forecast input is known before
  the day being forecast, and tests check this.
- **Every site uses only its own real data.** Where a source doesn't
  exist for a site, the coverage report says so with the reason.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/fetch_data.py                 # 1. download the data repos
python scripts/build_node.py --all           # 2. build every site's master table
python scripts/build_integrated.py           # 3. combine the six Fukui sites
python scripts/build_forecast.py             # 4. 7-day forecast
python scripts/check_calibration.py          # 5. (optional) check the visitor conversion
python scripts/forecast_monthly.py           # 12-month forecast, independent of 2 to 5
pytest                                       # run the tests
```

Build one site only with `python scripts/build_node.py --node tojinbo`.

**Where the data repos go:** next to this repo. If this repo is at
`~/work/dhde-preprocessing-model`, `fetch_data.py` puts the data in `~/work/`
(only the files needed, for the big repos) and updates it on later runs. To
use data that lives elsewhere, set `DHDE_WORKSPACE_ROOT` to that folder. A
missing repo shows up as that source's error in the coverage report
instead of stopping the build.

**Some sources need keys**, read from environment variables, never from
config (this repo is public): `TOMTOM_API_KEY` for road congestion,
`RAKUTEN_APP_ID` and `RAKUTEN_ACCESS_KEY` for Rakuten hotel availability.
Without them those sources are reported as unavailable and the build goes
on.

**Live traffic and hotel history** (JARTIC, TomTom, Rakuten) can't be
downloaded after the fact, so GitHub Actions saves it to the `live-data`
branch (see [Collecting live data](#collecting-live-data-history-cant-be-backfilled)).
To build with it, point `DHDE_LIVE_DATA_ROOT` at a checkout of that branch.

## What each step writes

Everything goes to `output/`, which is not committed.

| Step | File | What it is |
|---|---|---|
| `build_node.py` | `{node}_master.parquet` / `.csv` | One row per day for one site, every source joined on `date` |
| | `{node}_survey_responses.parquet` | Survey answers, one row per response (see [why](#why-survey-is-handled-differently-from-the-other-sources)) |
| | `{node}_coverage_report.json` | Per source: status, rows, date range, gaps, missing-value rates |
| `build_integrated.py` | `integrated_fukui_train.parquet` | Six Fukui sites, one row per site per day, up to yesterday: the training table |
| | `integrated_fukui.parquet` | The same, continued with future bookings, for the app |
| | `integrated_kyoto*.parquet`, `integrated_osaka*.parquet` | The same for Kyoto and Osaka (`--region kyoto` or `osaka`), same columns |
| `build_forecast.py` | `forecast_fukui.parquet` / `.csv` | Next 7 days per site: visitors and the site's own count, each with low/high, plus the backtest error |
| `check_calibration.py` | `calibration_check.csv` | Per site: the visitor factor and how well its count tracks official monthly visitors |
| `forecast_monthly.py` | `monthly_forecast.csv` | Next 12 months per town, the prefecture and guest-nights |

## Sites (nodes)

Each site is one config file in `config/nodes/`.

| Region | Sites | Notes |
|---|---|---|
| Fukui (priority six) | `tojinbo`, `fukui_station`, `rainbow_line`, `katsuyama`, `awara_onsen`, `eiheiji` | Integrated table and 7-day forecast. Eiheiji isn't forecast yet (no daily visitor count) |
| Ishikawa | `kanazawa`, `kaga_onsen`, `komatsu`, `nanao` | See [Ishikawa and Toyama nodes](#ishikawa-and-toyama-nodes) |
| Toyama | `toyama_station`, `takaoka`, `himi`, `tateyama` | Same |
| Kyoto | `kyoto_station`, `arashiyama`, `fushimi_inari`, `higashiyama` | Integrated table only. See [Kyoto and Osaka nodes](#kyoto-and-osaka-nodes) |
| Osaka | `osaka_station`, `namba`, `osaka_castle`, `usj` | Same |

What each Fukui site measures:

| Site | Visitor signal | Forecast converted to visitors with |
|---|---|---|
| Tojinbo | People camera | Official 2025 count (651k) |
| Fukui Station | People camera | No official site figure, so it stays in detections |
| Rainbow Line | Cars at two summit car parks | Official 2025 count (443k) |
| Katsuyama | Dinosaur Museum advance bookings | Museum entries, FY2025 (1,298,975) |
| Awara Onsen | Guests at the 10 hotels in its feed | Official 2025 count (658k) |
| Eiheiji | None yet | |

## Docs

| Doc | What's in it |
|---|---|
| [`docs/forecast.md`](docs/forecast.md) | The 7-day forecast: targets, features, models, how it's tested, current scores |
| [`docs/calibration.md`](docs/calibration.md) | Turning each site's count into visitors, and whether one factor per site is sound |
| [`docs/integrated_dataset.md`](docs/integrated_dataset.md) | The integrated table's columns and cleaning rules |
| [`docs/data_gaps.md`](docs/data_gaps.md) | What's missing per site, and what's used instead |
| [`docs/site_capacity.md`](docs/site_capacity.md) | Official visitor counts, parking and other limits per site |
| [`docs/osaka_kyoto_sources.md`](docs/osaka_kyoto_sources.md) | The suggested Osaka and Kyoto sources, and why most aren't usable |
| [Monthly forecast](#monthly-forecast) (below) | The 12-month forecast |

---

# Reference

The sections below are for working on the pipeline itself: how sites are
configured and how each data source is handled.

## The template pattern

Every node is **one YAML config + the shared source modules**, nothing
node-specific in Python. `config/nodes/{node}.yaml` declares which
sources apply and their node-specific parameters (sensor file paths,
weather station id, RSI/survey area name or id, JARTIC point code).
`src/dhde_preprocessing/join.py` reads that config, calls each source
module, and merges everything on `date`.

**Adding a site means writing a config, not code.** Copy an existing
node's YAML and adjust:

| Source | What a new node's config needs |
|---|---|
| `camera` | `gates`: list of `{name, person_csv or license_plate_csv, face_csv?}`. Set `enabled: false` with a `reason` if no sensor exists (don't fabricate one — see Katsuyama). |
| `weather` | `station_name`, `station_id` (look up in code4fukui/jma_station, confirms which physical station is nearest), plus `prec_no`/`block_no`/`page` (JMA's own addressing for the live scrape endpoint — see caveat below) and `start_date`. |
| `rsi` | `repo`, `area_name` (a municipality tracked in code4fukui/fukui-kanko-trend-data's per-year folders) or `null` to use the prefecture-wide total only. |
| `hotel` | `repo` (use a node-specific reservation repo if one exists, e.g. `fukui-station-kanko-reservation` — falls back to the regional `echizen-coast-kanko-reservation` otherwise) and `scope` (`station-specific` or `regional`, just for the report notes). Price-sanity bounds are derived automatically from whichever repo you point at — see caveat below. |
| `survey` | `repo`, `area_ids` — the 親番号 (parent number) value(s) from fukui-kanko-survey's `area.csv`, **not** its `id` column (see caveat below). |
| `traffic` | `enabled: false` with `reason` unless a JARTIC monitoring point actually exists nearby — query the live API on both layers and check the distance before assuming (see caveat below), not just because a node exists. Optional `layer` picks the permanent-counter layer instead of the default CCTV one. |
| `footfall_proxy` | Optional, only for nodes with **no camera**: `enabled: true` and `radii_km` (default `[5, 15]`). Adds `proxy_camera_count` (nearest other node's Person.csv camera inside the first circle that has one; a camera marked `proxy_eligible: false`, like Fukui Station's, is never used) and `proxy_survey_count` (responses pooled across all survey areas inside the first circle that has any). Labelled proxies, never merged into real camera counts; see `sources/footfall_proxy.py`. |
| `visitor_reservation` | Optional, only where an attraction publishes entry bookings: `enabled: true` and `repo` (currently `dinosaur-opendata` for Katsuyama). Adds `reserved_visitors` / `reserved_fee` from the visit-day snapshot, and `bookings_final` (False for future dates, which are bookings so far). Reserved entries only, not total visitors; see `sources/visitor_reservation.py`. |
| `road_congestion` | `enabled: true` and `radius_km` (default 2). Live TomTom Orbis traffic-flow tiles; needs the `TOMTOM_API_KEY` environment variable (free tier, no card). Adds `road_congestion` (1 - mean relative speed of roads within the radius) plus relative-speed columns. Snapshots are cached under `{workspace_root}/tomtom_cache/`, so history only builds up if the pipeline runs on a schedule; see `sources/road_congestion.py`. |

Every source function has the signature `load_x(node_cfg) -> (df | None, SourceReport)`. If you add a new source
type, follow that same signature and register it in
`join.py`'s `SOURCE_LOADERS` (or handle it separately like `survey`, if
it isn't naturally one-row-per-day).

## Collecting live data (history can't be backfilled)

JARTIC keeps hourly traffic for only ~3 months and TomTom only gives the
current state, so their history exists only if we save it.
`scripts/collect_live.py` pulls both for every node, and
`.github/workflows/collect-live-data.yml` runs it hourly (06:15 to 21:15 JST)
and commits the results to the `live-data` branch, keeping main free of data
commits. The build reads that history back: point `DHDE_LIVE_DATA_ROOT` at a
checkout of the `live-data` branch (defaults to the workspace root), and
`traffic` merges it with each live pull while `road_congestion` reads its
saved snapshots. It needs the `TOMTOM_API_KEY` repository secret (Settings → Secrets
and variables → Actions); without it only JARTIC is collected. Scheduled
workflows only run from the default branch, so collection starts once this is
merged to main.

**Google reviews** work the same way: `scripts/collect_google_reviews.py`
runs the Apify Google Maps Reviews Scraper weekly on Mondays
(`.github/workflows/collect-google-reviews.yml`, needs the `APIFY_TOKEN`
secret) and appends new reviews to `google_reviews/` on `live-data`. Only
the date, stars, language and yes/no flags are kept, never names or review
text, because the branch is public. To load an export someone ran by hand:
`python scripts/collect_google_reviews.py --out history --import FILE.csv --max-reviews 1000`.
Nodes opt in with a `google_reviews.place_id` in their config.
Reviews with text are also counted per language (`reviews_lang_ja`, `_en`,
`_zh_hant`, `_zh_hans`, `_ko`, `_other`, each with a `_stars_mean`).
Traditional Chinese points to Taiwan or Hong Kong and Simplified to
mainland China: a proxy for the visitor's market, not their nationality.

## Non-obvious things found while building this — read before extending

- **`config.resolve_path`** is the only way any source module touches the
  filesystem. It joins onto `DHDE_WORKSPACE_ROOT` (env var; defaults to
  the parent of this repo for local dev). Set that env var to an `s3://`
  prefix in the AWS deployment and every source module works unchanged —
  pandas reads `s3://` URIs the same as local paths. Never hardcode a
  path in a source module.

- **Survey area matching uses `area.csv`'s `親番号` column, not `id`.**
  `id` is an unrelated small sequential row number. The 300000-range
  numbers used throughout this project (and in `config/nodes/*.yaml`) are
  `親番号` values. This was a real bug hit while building Tojinbo's
  config — `tests/test_survey.py` has a regression test for it.

- **Survey matching is exact on `回答エリア`, not a substring search.** A
  loose "does this row mention 東尋坊 anywhere" grep returns ~3.75x more
  rows than the exact match (7,348 vs. 1,958 for Tojinbo) — the
  difference is free-text fields elsewhere in the same row incidentally
  mentioning a landmark without that response actually being registered
  to that area. If a future need calls for the looser signal, add it as
  a clearly-separate, clearly-labeled column, not by loosening this match.

- **Hotel reservation files are forward-looking snapshots, not daily
  totals, and are cleaned through a real glitch-detection/gap-filling
  pipeline, not a naive day-of read.** `{repo}/data/{date}.csv` — the
  file named `2025-06-01.csv` contains rows for `date_visit` 2025-06-01
  *through* 2025-08-30 (bookings in hand for the next ~90 days, as of
  that snapshot day). The same `date_visit` appears in ~90 different
  snapshots, one per day before it — its "booking curve." A teammate's
  data-quality analysis of this (Colab notebook + methodology doc, see
  PR discussion) found real problems the naive "just read lead_time=0"
  approach this module started with was blind to: failed data pulls,
  frozen/stale snapshots (the feed stopped updating but kept serving the
  same numbers), values exceeding actual hotel capacity, negative
  revenue (refunds), impossible room prices, and one-day glitches that
  revert. `sources/hotel.py` ports that full pipeline (blank bad values
  rather than drop rows, then fill blanks from the same booking curve's
  neighbours, gaps of ≤7 snapshots only), and adds `occ`/`adr`/`revpar`
  columns plus data-quality flag columns (`is_stale`, `was_imputed`,
  etc.) so nothing is hidden.

  **This repo covers several hotel datasets, same format, very
  different price tiers** — area-specific feeds where one exists
  (`fukui-station-kanko-reservation`: 3 hotels near Fukui Station, 585
  rooms; `fukui-kanko-reservation`: Awara Onsen;
  `mikatagoko-kanko-reservation`: Mikata Five Lakes, used for Rainbow
  Line) and `echizen-coast-kanko-reservation` (regional, used for every
  other node).
  The original analysis's price-sanity check used a fixed 3,000–30,000
  JPY/room range, validated against the Fukui Station repo specifically.
  Applied as-is to the regional repo (real median price ~64,500 JPY — a
  pricier market, not bad data), that fixed range wrongly excluded 87%
  of its rows. Fixed by deriving the price bounds from each repo's own
  distribution (1st/99th percentile) instead of hardcoding one repo's
  numbers everywhere — see `_derive_adr_bounds` and the regression test
  in `tests/test_hotel.py`. Ported the validated *approach*, not the
  specific *numbers*, since those were tied to which dataset she'd
  looked at.

- **Rainbow Line's two gates have no `Person.csv` at all** — only
  `LicensePlate.csv` (vehicle counts, since it's a parking-lot entrance)
  and `Face.csv`. Its camera columns are `gate1_vehicle_count` /
  `gate2_vehicle_count`, deliberately not `gate1_count`, so nothing
  downstream can silently treat a vehicle count as a person count.

- **JMA weather is fully automated — not the obsdl portal.** The obsdl
  portal (`https://www.data.jma.go.jp/risk/obsdl/index.php`) really is a
  form/session-driven download with no stable API, so this does NOT use
  it. Instead it scrapes JMA's public ETRN hourly-observation pages
  (`https://www.data.jma.go.jp/obd/stats/etrn/view/{page}.php`), the same
  approach already proven working in the sibling
  hokuriku-tourism-ai-governance-dashboard repo's
  `jma/fetch_jma_monthly.py`. Each node's config carries `prec_no`/
  `block_no`/`page` (a different addressing scheme than the obsdl
  `station_id` used to originally confirm the physical station).
  Fetched days are cached per node under
  `{workspace_root}/jma_cache/{node}_hourly.csv` — repeated runs only
  scrape the gap since last time instead of re-fetching full history.
  This repo's cache was seeded from the sibling dashboard repo's
  already-fetched 2024-12 → 2026-03 history rather than re-scraping it
  from scratch. A single bad day (a transient site hiccup, a genuinely
  missing observation) is caught per-day and doesn't throw away the rest
  of a run — see the regression test in `tests/test_weather.py` for the
  bug this was actually hit and fixed.

- **JARTIC (road traffic) coverage was checked empirically, not
  assumed**, by querying the live API with each node's real coordinates
  on **both** JARTIC layers — the CCTV AI counters (default) and the
  permanent counters (`layer: t_travospublic_measure_1h` in the node
  config; same property names, different point set). Tojinbo and Awara
  Onsen have no point within 8km on either layer and are
  `enabled: false`. The other four are `enabled: true` but flagged — a
  point exists nearby (2.5–5.3km), but this has
  **not** been confirmed to sit on the road visitors actually use to
  reach the site. `distance_km` and `point_code` are carried into every
  build's coverage report so this stays visible. Also: JARTIC only
  retains 5-minute data ~1 month and hourly data ~3 months, and its API
  requires the field name unquoted in a `>=`/`<=` range CQL filter (the
  spec PDF's own examples show it quoted, which 400s against the live
  endpoint) — see `sources/traffic.py`.

- **Windows console encoding**: `scripts/build_node.py` reconfigures
  stdout to UTF-8 on startup — without it, printing any node's Japanese
  label crashes with `UnicodeEncodeError` on a default Windows terminal
  (cp1252).

## Ishikawa and Toyama nodes

Ishikawa: `kanazawa`, `kaga_onsen`, `komatsu`, `nanao`. Toyama:
`toyama_station`, `takaoka`, `himi`, `tateyama`. Same pipeline, but
code4fukui's camera, RSI and hotel datasets are Fukui-only, so each node
gets only what really exists for its own prefecture — never another
prefecture's data as a stand-in:

| Source | Ishikawa | Toyama |
|---|---|---|
| weather | all four (JMA `prec_no: "56"`) | all four (JMA `prec_no: "55"`) |
| traffic | kanazawa ~1.2km, komatsu ~5.7km; kaga_onsen/nanao off (~6.6/~7.0km) | toyama_station ~2.3km, takaoka ~1.6km, himi ~1.0km; tateyama off (~19km) |
| survey | Milli QR survey (`provider: milli`), from 2023-09 | TOYTOS web survey (`provider: toytos`), from 2025-04 |
| camera | none | toyama_station only — Toyama City AI cameras (`provider: toyama_city`), from 2023-02 |
| info_desk | kanazawa only — Kanazawa tourist desk enquiries | none |
| monthly_visitors | all four | all four |
| rakuten | all four | all four |
| rsi, hotel | none | none |

**`monthly_visitors` is the cross-prefecture comparison signal.** It
comes from the JTTA (日本観光振興協会) digital tourism statistics, downloaded from the
publisher's page on every build (cached under `open_data_cache/`):
monthly visitor counts per city and prefecture, measured the same way
across Japan, from 2021-01. Each month's total is repeated on every day
of that month (`city_visitors_month`, `pref_visitors_month`). Fukui
nodes can opt in with the same 4 config lines (Fukui pref lgcode 18,
e.g. Fukui city 18201, Sakai 18210). It no longer reads
code4fukui/japan-kanko-stat: that mirror never re-downloads a file, so it
kept pre-revision figures up to 2026-02 after the publisher revised 2025
and Jan–Feb 2026 on 2026-04-14, which showed as a fake 2-6x jump for many
Fukui towns from 2026-03. Survey counts are **not** comparable
across prefectures — each prefecture runs its own questionnaire and
poster placement — so compare survey trends within a prefecture only.

**`rakuten` is the cross-prefecture hotel signal.** Rakuten Travel API:
hotels listed within 3km of each node, and the share still with a room
for 2 adults 1, 7 and 30 days before each stay date, plus the cheapest
charge. Availability on one site, not bookings — so it sits beside
Fukui's `hotel` source rather than replacing it. Credentials come from
the `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` environment variables, never
config (public repo). **No history:** each run appends a snapshot to
`rakuten_snapshots/{node}.csv`, so it must run daily; the series starts
2026-09-27. `.github/workflows/collect-rakuten.yml` runs
`scripts/collect_rakuten.py` at 09:30 JST every day and commits the
snapshots to the `live-data` branch; set `DHDE_LIVE_DATA_ROOT` to a
checkout of that branch to build with them. It needs the
`RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` repository secrets, and the
Rakuten app must not be locked to one IP address (Actions runners
change). Fukui nodes can opt in with the same config block.

- **Where the data comes from.** A sibling repo, like the Fukui sources:
  `ishikawa-kanko-survey` (code4fukui). Over HTTP, via
  `sources/remote_csv.py` (cached under
  `{workspace_root}/open_data_cache/`, falls back to the cache if a
  fetch fails): Milli's facility list and tourist desk Google Sheets,
  TOYTOS from Toyama's CKAN portal, Toyama City's camera CSV export, and
  the JTTA (日本観光振興協会) digital tourism statistics for `monthly_visitors`.
- **Milli survey rows carry a facility, not a municipality.** The
  municipality comes from Milli's facility list, joined on (area,
  facility); ~1% name a facility missing from the list and are dropped
  (counted in the report notes). TOYTOS rows already carry the
  municipality they were answered in (回答場所).
- **Cleaning:** Milli double submissions (same facility, same second)
  are dropped, keeping the first; TOYTOS has dates only, so only exact
  duplicate rows are dropped. A day with a total of 0 at a tourist desk
  or a station camera is treated as missing (closed / camera down), not
  0. A day only gets a desk total when every desk has a value. Toyama
  cameras are kept as separate columns per camera, never summed (one
  person can pass both). Spending answers stay as yen-range text.
- **Date range of the master table.** Sources are outer-joined on
  `date`, so a node's table spans every source: it starts 2021-01-01
  when `monthly_visitors` is on, and runs up to 30 days ahead for
  Rakuten stay dates (as the Fukui `hotel` source already runs ahead).
  Other columns are empty in those rows. Nothing is trimmed here —
  choosing the modeling window is a modeling-stage decision.
- **Optional sources.** `info_desk`, `monthly_visitors` and `rakuten` are skipped
  for nodes whose config doesn't declare them, so Fukui output is
  unchanged.
- **Real gaps, not pipeline bugs:** `nanao` has 576 survey-free days
  since 2023-09 (most likely Wakura Onsen closing after the January 2024
  Noto earthquake); both Toyama Station cameras have no data
  2023-11-22 → 2023-12-19; the Kanazawa desk's one zero day is
  2024-01-02, the day after the earthquake.

## Kyoto and Osaka nodes

Kyoto: `kyoto_station`, `arashiyama`, `fushimi_inari`, `higashiyama`.
Osaka: `osaka_station`, `namba`, `osaka_castle`, `usj`. Same config layout
as the Fukui nodes, and the same rule as Ishikawa and Toyama: only data
that really exists for the prefecture, never another prefecture's.

| Source | Kyoto and Osaka |
|---|---|
| weather | all eight: 京都 (`prec_no: "61"`, block 47759) and 大阪 (`"62"`, 47772), the nearest full-observation stations (1–8km) |
| traffic | six, 0.5–5.3km, each node on its own point (see below); namba and osaka_castle off (their nearest counters read 0 on 84–87 of the last 91 days) |
| monthly_visitors | all eight, by ward (the statistics have wards, not the whole cities) |
| rakuten | all eight, 1km radius |
| camera, rsi, hotel, survey | none: no open equivalent found, see [`docs/osaka_kyoto_sources.md`](docs/osaka_kyoto_sources.md) |

- **Rakuten radius is 1km, not 3km.** The sites are 2–4km apart, so 3km
  circles would overlap and count the same hotels at several nodes.
- **No two nodes share a traffic point.** The closest CCTV point to
  Fushimi Inari and Higashiyama is Kyoto Station's (6810060), so those two
  use their next-nearest point instead of copying Kyoto Station's signal.
- **Days a traffic counter reads 0 are missing, not 0** (counter down, as
  with the cameras): in the last 91 days, osaka_station 39, arashiyama 29,
  kyoto_station 6, and fukui_station 51.
- **Namba and Osaka Castle share a ward** (中央区, 27128), so their
  `city_visitors_month` values are the same: it's the ward's total, not
  each site's.
- **No daily visitor count yet.** These nodes have no camera or booking
  count to predict; Rakuten availability is the closest daily demand
  signal, and it only has history from its first daily run.
- **Integrated tables:** `python scripts/build_integrated.py --region kyoto`
  (or `osaka`) writes `integrated_kyoto*.parquet`, with the same columns as
  Fukui's.

## Monthly forecast

```bash
python scripts/forecast_monthly.py   # writes output/monthly_forecast.csv + _backtest.csv
```

12 months ahead, for visitors in each Fukui node's municipality (Rainbow
Line = Mihama + Wakasa), Fukui prefecture's visitors, and Fukui
guest-nights (total, Japanese, foreign). It downloads its own two
sources, so it doesn't need `build_node.py` first: the JTTA (日本観光振興協会) digital
tourism statistics (`sources/monthly_visitors.py`) and the JTA
accommodation survey's 推移表 workbook (`sources/guest_nights.py`,
prefecture-level only, about two months behind).

Each series gets whichever model backtests best (rolling-origin, scored
only on months after the forecast origin): the same month last year, or
that plus half of the recent year-on-year growth, either its own or
Ishikawa's and Toyama's. `low`/`high` are the 10th–90th percentile of the
chosen model's backtest errors. Details and thresholds are in
`src/dhde_preprocessing/monthly_forecast.py`.

- **Fukui's visitor counts are only comparable from 2025-01.** The
  publisher's April 2026 tourism-point revision didn't reach back, so
  2024 and earlier count fewer points (Fukui 1.65x in 2025 vs 1.09x
  nationally). That leaves too little history to test the growth
  models, so visitor series use last year's month for now; the choice is
  re-tested on every run.
- **Guest-nights** use 2023-01 onward (after COVID). JTA changed its
  sampling in 2026-01, so year-on-year changes across it are partly
  method.
- As of the 2026-08 data, backtest MAPE is about 9–17% for the node
  municipalities, 9% for the prefecture and 9–10% for guest-nights
  (17% for foreign guest-nights, a small and volatile series).

## Why survey is handled differently from the other sources

The other sources are naturally one-row-per-day. Survey responses
are one-row-per-response, many per day. So `sources/survey.py` returns
the raw, filtered, response-level table, and `survey.daily_summary`
(called by `join.py`) turns it into daily columns that add up across
days: `survey_response_count`, `survey_satisfaction_n` and `_mean`
(1 to 5; weight the mean by `_n`), `survey_nps_n`, `_promoters` (9-10)
and `_detractors` (0-6), so NPS = (promoters - detractors) / n * 100
over any window, and counts by home region (`survey_origin_*`) and
purpose of visit (`survey_purpose_*`). These are mapped for the Fukui
survey only; the Ishikawa and Toyama nodes give the count only for now.
Toyama's TOYTOS survey asks similar questions under other names
(満足度（旅行全体）, おすすめ度, 居住都道府県, 訪問目的), not mapped yet. The full
response-level table is also written separately
(`{node}_survey_responses.parquet`) for everything else (spending, free
text, demographics).

## Tests

```bash
pytest tests/ -v
```

Every source module is tested against small synthetic fixtures (not the
real, large external repos) so tests are fast and don't depend on those
repos being cloned. Each test file also documents the specific bug it
regression-tests, where one was found while building this (the `date`
column clobber in `camera.py`, the `親番号` vs. `id` mismatch in
`survey.py`).
