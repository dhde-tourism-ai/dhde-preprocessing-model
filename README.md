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
| `weather` | `station_name`, `station_id` (look up in code4fukui/jma_station), `input_csv` (null until a human drops in an obsdl export — see caveat below). |
| `rsi` | `repo`, `area_name` (a municipality tracked in code4fukui/fukui-kanko-trend-data's per-year folders) or `null` to use the prefecture-wide total only. |
| `hotel` | `repo` — this is regional (Echizen Coast), not node-specific; every node gets the same signal. |
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
  totals.** `echizen-coast-kanko-reservation/data/{date}.csv` — the file
  named `2025-06-01.csv` contains rows for `date_visit` 2025-06-01
  *through* 2025-08-30 (bookings in hand for the next ~90 days, as of
  that snapshot day). The most complete count for any `date_visit` is
  its own day-of snapshot (bookings settle as the date approaches), so
  `sources/hotel.py` reads only each file's first row rather than
  reconciling overlapping snapshots. If a file's first row's `date_visit`
  doesn't match its own filename date (a gap in the forward window),
  that file is skipped rather than misattributed.

- **Rainbow Line's two gates have no `Person.csv` at all** — only
  `LicensePlate.csv` (vehicle counts, since it's a parking-lot entrance)
  and `Face.csv`. Its camera columns are `gate1_vehicle_count` /
  `gate2_vehicle_count`, deliberately not `gate1_count`, so nothing
  downstream can silently treat a vehicle count as a person count.

- **JMA weather automation is an explicit, unsolved TODO.** The
  obsdl portal (`https://www.data.jma.go.jp/risk/obsdl/index.php`) is a
  form/session-driven download, not a stable API — this module expects a
  human to export a CSV and set `sources.weather.input_csv` in the node's
  config. The parser in `sources/weather.py` for that export format is
  **best-effort and unverified against a real export** (none was
  available while building this) — treat it as a starting point to fix
  once a real file is in hand, not as settled. Until then every node
  correctly reports weather as `unavailable` with the TODO in its notes.

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
