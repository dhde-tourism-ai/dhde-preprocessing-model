# dhde-preprocessing-model

Per-node data preprocessing for the DHDE (Distributed Human Data Engine)
tourism dashboard covering Fukui/Hokuriku, Japan. This package's job stops
at: **raw source → cleaned, validated, date-keyed table → one joined
master table per node.** It does not build 0–100 normalized scores or any
forecasting/predicted values — that's a separate modeling/scoring stage
that consumes this pipeline's output.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/build_node.py --node tojinbo
python scripts/build_node.py --all
```

Each run prints a per-source coverage report (status, row count, date
range, calendar gaps, null rates) and writes to `output/`:

- `{node}_master.parquet` / `.csv` — the joined daily table
- `{node}_survey_responses.parquet` — raw response-level survey rows (see "Why survey is different" below)
- `{node}_coverage_report.json` — the same coverage info as machine-readable JSON

## The template pattern

Every node is: **one YAML config + the same six source modules**, nothing
node-specific in Python. `config/nodes/{node}.yaml` declares which
sources apply and their node-specific parameters (sensor file paths,
weather station id, RSI/survey area name or id, JARTIC point code).
`src/dhde_preprocessing/join.py` reads that config, calls each source
module, and merges everything on `date`.

**Onboarding a 5th node is: write a config, not write code.** Copy an
existing node's YAML, adjust:

| Source | What a new node's config needs |
|---|---|
| `camera` | `gates`: list of `{name, person_csv or license_plate_csv, face_csv?}`. Set `enabled: false` with a `reason` if no sensor exists (don't fabricate one — see Katsuyama). |
| `weather` | `station_name`, `station_id` (look up in code4fukui/jma_station, confirms which physical station is nearest), plus `prec_no`/`block_no`/`page` (JMA's own addressing for the live scrape endpoint — see caveat below) and `start_date`. |
| `rsi` | `repo`, `area_name` (a municipality tracked in code4fukui/fukui-kanko-trend-data's per-year folders) or `null` to use the prefecture-wide total only. |
| `hotel` | `repo` (use a node-specific reservation repo if one exists, e.g. `fukui-station-kanko-reservation` — falls back to the regional `echizen-coast-kanko-reservation` otherwise) and `scope` (`station-specific` or `regional`, just for the report notes). Price-sanity bounds are derived automatically from whichever repo you point at — see caveat below. |
| `survey` | `repo`, `area_ids` — the 親番号 (parent number) value(s) from fukui-kanko-survey's `area.csv`, **not** its `id` column (see caveat below). |
| `traffic` | `enabled: false` with `reason` unless a JARTIC monitoring point actually exists nearby — query the live API and check the distance before assuming (see caveat below), not just because a node exists. |

Every source function has the signature `load_x(node_cfg) -> (df | None, SourceReport)`. If you add a 7th source
type later, follow that same signature and register it in
`join.py`'s `SOURCE_LOADERS` (or handle it separately like `survey`, if
it isn't naturally one-row-per-day).

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

  **This repo covers two different hotel datasets, same format, very
  different price tiers** — `fukui-station-kanko-reservation` (3 hotels
  near Fukui Station, 585 rooms, used for that node specifically) and
  `echizen-coast-kanko-reservation` (regional, used for the other 3).
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
  assumed**, by querying the live API with each node's real coordinates:
  Tojinbo (~14km to nearest point) and Katsuyama (~22.5km) are
  `enabled: false`. Fukui Station (~2.7km) and Rainbow Line (~5km) are
  `enabled: true` but flagged — a point exists nearby, but this has
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

## Why survey is handled differently from the other five sources

The other five sources are naturally one-row-per-day. Survey responses
are one-row-per-response — many per day. Aggregating that down to a
score or summary is a modeling-stage decision (which fields to average,
how to weight them), out of scope here. So `sources/survey.py` returns
the raw, filtered, response-level table, and `join.py` derives exactly
one mechanical column from it for the master table —
`survey_response_count` (just a count, not a score) — while also writing
the full response-level table separately (`{node}_survey_responses.parquet`)
so the modeling stage has every original column (satisfaction, NPS,
spending, free text, demographics) to work with directly.

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
