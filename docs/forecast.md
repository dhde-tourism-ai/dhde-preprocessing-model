# Daily visitor forecast (v1)

`python scripts/build_forecast.py` (after `scripts/build_integrated.py`)
forecasts visitors 1 to 7 days ahead for each Fukui node, with a low/high
range, and says how wrong each model was on dates it had never seen.
Code: `src/dhde_preprocessing/forecast.py`.

Outputs, in `output/`:

- `forecast_fukui.parquet` / `.csv`: one row per node per forecast day:
  `visitors_est`, `visitors_low`, `visitors_high` (the forecast in
  visitors, see below), `predicted`, `low`, `high` (the same in the node's
  own measured count), `calibration_factor`, `model`, `target`,
  `backtest_wape`, `baseline_wape`, `range_coverage`, `issued_from` (last
  observed day).
- `forecast_fukui_backtest.csv`: backtest scores per node and model.
- `forecast_fukui_report.json`: the same scores, the model chosen per node,
  and the pending nodes with the reason.
- `model_registry.csv`: one row per node per run, appended: the run's
  `version` (UTC time + git commit), the model used, its backtest error
  (`error_pct`, WAPE) and the baseline's. `models/<version>/models.joblib`
  holds that run's fitted models. `python scripts/compare_models.py`
  prints the latest run next to the previous one as a Markdown table
  (`--forecast monthly` for the monthly forecast, which records its runs
  there too). See `src/dhde_preprocessing/model_registry.py`.

## What is forecast

| Node | Target | Note |
|---|---|---|
| Tojinbo | `camera_count` | People camera |
| Fukui Station | `camera_count` | People camera (station hub, not a visitor count as such) |
| Rainbow Line | `camera_gate1_vehicle_count` + `camera_gate2_vehicle_count` | Vehicles, not people. Summed for now (the two gates are separate car parks, so one car is unlikely to be counted twice); to be confirmed with the team |
| Katsuyama | `attraction_reserved_visitors` | Dinosaur Museum advance bookings, ~57% of visitors |
| Awara Onsen | `hotel_n_people` | Overnight guests from Awara's own hotel feed, not day visitors. Its footfall proxy (Tojinbo's camera) is not used: it would copy Tojinbo |
| Eiheiji | pending | Only pooled survey answers and the regional hotel feed shared with two other nodes |

A node without a real count isn't forecast: there would be nothing to
check the forecast against.

## From measured count to visitors

The model forecasts what each node actually measures, since that's what
it can be checked against. Every forecast is then also given in one common
unit, visitors:

    visitors = measured count x factor
    factor   = official 2025 visitors / (mean daily count in 2025 x 365)

The official counts are Fukui Prefecture's 観光客入込数 2025 (gross visits),
stored per node as `official_visitors` in `config/nodes/*.yaml` with their
source (see also `site_capacity.md`). The factor uses the daily *mean*, not
the sum, so days a sensor was down don't inflate it, and it needs 300+
measured days.

| Node | Official 2025 | Factor | Reads as |
|---|---|---|---|
| Tojinbo | 651k | 0.198 | ~5 camera detections per visitor |
| Rainbow Line | 443k | 6.98 | ~7 visitors per car counted |
| Katsuyama | 1,299k (museum, FY2025) | 1.76 | bookings are 57% of museum entries |
| Awara Onsen | 658k | 1.86 | ~1.9 visitors per hotel guest |
| Fukui Station | none | none | No official site figure, so the forecast stays in camera detections |

What this does and doesn't do: it makes each node's yearly total match the
official figure, so nodes can be compared in one unit. The day-to-day
pattern is still the node's own count (overnight guests at Awara, cars at
Rainbow Line), and an official "visit" is a gross count (one person at two
sites counts twice). Error percentages are the same in either unit.
Katsuyama uses the museum's own entries over its fiscal year (April to
March), not the prefecture's area figure: the area's monthly pattern
doesn't follow the museum's. Whether one factor per node is sound, node by
node, is in [`calibration.md`](calibration.md). The app's `build_real_data.py` scales its history with the sum
instead of the mean, which gives slightly higher factors where sensors were
down (Tojinbo 0.208, Rainbow Line 7.12).

## Models

| Model | What it is |
|---|---|
| `baseline` | Same weekday last week (the briefing's number to beat) |
| `regression` | Ridge regression per node on log visitors: weekday, month, days off, holiday blocks, last weeks' level. Each coefficient reads as a % effect |
| `lightgbm` | Gradient-boosted trees pooled across the five nodes (node is a feature) |

Each node uses whichever model had the lowest backtest WAPE.

**Features** (all known 7 days before the forecast day): day of week,
month, week of the month, day off (weekend, public holiday, 29 Dec to
3 Jan), length of the day-off block (3+ = long weekend), day before/after
a day off, Golden Week, Obon, and the target 7, 14, 21 and 28 days
earlier plus its 28-day average ending 7 days earlier. `lag7_day_off`
tells the model when last week's value was a holiday, so it doesn't copy
the spike (the baseline's main failure).

Katsuyama and Awara Onsen also get `booked_lead7`: bookings for the day
as of a snapshot 7+ days before it, read from the raw snapshots so no
later information can reach it.

- Katsuyama: `attraction_reserved_visitors_lead7` (`visitor_reservation.py`).
  It also shows the museum's closing days (2nd and 4th Wednesday, moved
  around holidays): it reads 0 on exactly its 47 closing days. The
  regression gets an explicit "nothing booked a week ahead" flag, since a
  linear model on log bookings can't reach 0 on its own (Katsuyama 16.1% to
  13.6%).
- Awara Onsen: `hotel_n_people_lead7` (`hotel.py`). Taken from the raw
  snapshots, not the cleaned booking curve: the cleaning's glitch check
  and gap filling look at later snapshots. The two agree on 99.5% of
  days.

**Weather forecast** (Tojinbo and Rainbow Line only): daytime rain
(09:00 to 18:00, mm) and the day's highest temperature from JMA's model
(`sources/weather_ahead.py`). Only the forecast is known in advance, so a
day forecast h days ahead gets the forecast issued h days before it, from
Open-Meteo's archive of past model runs (back to March 2024, so the whole
training period has it). Each of the 7 horizons is fitted and backtested on
its own lead, so a model never learns from a fresher forecast than it will
have live. The other nodes keep exactly the model they had: their forecasts
and scores are identical with or without weather.

What it changed (26-week backtest, best model):

| Node | Without | With | |
|---|---|---|---|
| Rainbow Line | 38.9% | 35.3% | LightGBM both times |
| Tojinbo | 28.0% | 26.7% | regression, now LightGBM |

Tried and not used:
- The weather that actually happened (cheating, an upper bound): Rainbow
  Line 31.2%, Tojinbo 23.4%. So weather matters; a forecast gets part of it.
- The forecast from 7 days before for every horizon: worse everywhere
  (Rainbow Line 39.4%). A week out, its daily rain correlates 0.2 with the
  rain that fell; 1 day out, 0.5.
- Weather at all five nodes: Fukui Station, Katsuyama (indoor museum) and
  Awara Onsen (hotel guests booked ahead) moved by 0.3 points or less, and
  Katsuyama got slightly worse, so they are left without it. Chosen by what
  the sites are, which the numbers agree with.

**Left out for now:** hotel at the other
nodes (the training table holds final bookings, and the week-ahead value
is the regional feed, not the site), RSI (5 days late, short town-level
history), traffic (~90 days).

## How it's tested

Rolling origin over the last 26 weeks: for each week, train on every day
up to that week's start and forecast the 7 days after it. So every score
is on dates later than anything the model trained on, and the test
window includes Golden Week and Obon 2026. `tests/test_forecast.py`
checks that no feature uses a value after the day the forecast is made.

**Bookings that aren't final yet** are left out. Katsuyama's museum
bookings and Awara's hotel guests are only final once the visit day's own
snapshot is in (`attraction_from_earlier_snapshot`, `attraction_bookings_final`,
`hotel_lead_used`). If a feed runs late, the latest days hold bookings so
far; they're set to missing instead of being used as actuals, and the run
warns.

All models are scored on the same days. WAPE = sum of |error| / sum of
actual visitors.

**The low/high range** is the 5th to 95th percentile of the backtest's
errors for that node and model. To check it honestly, the percentiles
are fitted on the earlier 13 backtest weeks and counted on the later 13:
`range_coverage` is the share of those unseen days that fell inside. A
10th to 90th percentile band only caught 58 to 77% (errors are larger in
the busy summer weeks), so the wider band is used and read as a roughly
80% range, not a guaranteed 90%. Choosing the band after seeing that
check is itself a small choice made on test data; the next months'
coverage is the real test.

## Results (backtest to 2026-09-28, WAPE)

| Node | Baseline | Regression | LightGBM | Used |
|---|---|---|---|---|
| Fukui Station | 21.0% | 15.9% | 15.3% | LightGBM |
| Tojinbo | 38.3% | 27.1% | 26.7% | LightGBM (with weather) |
| Rainbow Line | 48.6% | 41.7% | 35.3% | LightGBM (with weather) |
| Katsuyama | 69.7% | 13.4% | 24.7% | Regression |
| Awara Onsen | 26.8% | 2.8% | 6.4% | Regression |

Range coverage on unseen weeks, for the model used: Fukui Station 75%,
Tojinbo 68%, Rainbow Line 93%, Katsuyama 87%, Awara Onsen 82%. Tojinbo's
range is the one to watch: LightGBM's caught 68% of later days, against 75%
for the regression it used before weather.

**Why Awara Onsen scores so well:** about 96% of its final guests are
already booked 7 days ahead (median), so the week-ahead bookings alone
would score 6.3%. The model mostly reads the order book; the low error
says the hotels book early, not that visitor demand is easy to predict.

Regression and LightGBM are within a point of each other at Fukui Station
and Tojinbo, but can differ by 20 to 30% on a single day, so a day's
number is less certain than the average error suggests; the range shows
that.

The briefing's baseline figures (22% at Fukui Station, 44% at Tojinbo)
were measured on a different window; on this one the baseline is
21.0% and 38.3%. Numbers shift slightly day to day as the 26-week window moves.

## Known limits and next steps

- **Katsuyama is forecasting bookings, not visitors.** Bookings were
  ~57% of FY2025 visitors; walk-ins aren't in the target.
- **Awara Onsen is forecasting overnight guests at the hotels in its feed**,
  not day visitors: 10 hotels, 576 rooms (fukui-kanko-reservation's
  `latest_hotel.csv`, data from the Fukui Prefecture Tourism Federation).
  What share of all Awara Onsen rooms that is hasn't been checked, so read
  it as those hotels' guests, not the town's total.
- **Rainbow Line** is small and lumpy (tens to hundreds of cars, many
  zero days in winter), so percentage errors stay large.
- **More weather:** wind and snow for Rainbow Line (a mountain toll road)
  and wind for Tojinbo (a cliff coast) are the next to try, in the same
  archive.
- **School holidays** helped a little in a test (Fukui Station 16.1% to
  15.3%) but only with approximate national dates, so they're left out
  until Fukui Prefecture's official school calendar is in.
- **Rainbow Line zero days:** 42 of its 47 zero days are in January and
  February, likely winter closures, but nothing tells the model in
  advance. A published closure calendar would help. Gate 2 reads 0 on 401
  days, so summing the gates barely changes the count; the ~7 visitors per
  car factor still needs checking (buses, or visitors the gates don't see).
- **Eiheiji** stays pending until a real visitor count exists. The temple
  charges admission, so its own counts are the likely source; the request
  goes through the team lead.
