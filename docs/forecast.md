# Daily visitor forecast (v1)

`python scripts/build_forecast.py` (after `scripts/build_integrated.py`)
forecasts visitors 1 to 7 days ahead for each Fukui node, with a low/high
range, and says how wrong each model was on dates it had never seen.
Code: `src/dhde_preprocessing/forecast.py`.

Outputs, in `output/`:

- `forecast_fukui.parquet` / `.csv`: one row per node per forecast day
  (`date`, `node_key`, `target`, `model`, `predicted`, `low`, `high`,
  `backtest_wape`, `baseline_wape`, `range_coverage`, `issued_from` =
  last observed day).
- `forecast_fukui_backtest.csv`: backtest scores per node and model.
- `forecast_fukui_report.json`: the same scores, the model chosen per node,
  and the pending nodes with the reason.

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
  around holidays), because those read 0 while open days already have
  bookings.
- Awara Onsen: `hotel_n_people_lead7` (`hotel.py`). Taken from the raw
  snapshots, not the cleaned booking curve: the cleaning's glitch check
  and gap filling look at later snapshots. The two agree on 99.5% of
  days.

**Left out for now:** weather (only forecasts are known in advance; wiring
the EC2 box's saved JMA forecasts in is the next step), hotel at the other
nodes (the training table holds final bookings, and the week-ahead value
is the regional feed, not the site), RSI (5 days late, short town-level
history), traffic (~90 days).

## How it's tested

Rolling origin over the last 26 weeks: for each week, train on every day
up to that week's start and forecast the 7 days after it. So every score
is on dates later than anything the model trained on, and the test
window includes Golden Week and Obon 2026. `tests/test_forecast.py`
checks that no feature uses a value after the day the forecast is made.

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

## Results (backtest to 2026-09-26, WAPE)

| Node | Baseline | Regression | LightGBM | Used |
|---|---|---|---|---|
| Fukui Station | 20.4% | 16.1% | 16.1% | Regression (tie) |
| Tojinbo | 36.9% | 27.6% | 28.4% | Regression |
| Rainbow Line | 47.9% | 41.8% | 39.2% | LightGBM |
| Katsuyama | 64.4% | 16.1% | 25.9% | Regression |
| Awara Onsen | 26.4% | 2.8% | 6.2% | Regression |

Range coverage on unseen weeks, for the model used: Fukui Station 85%,
Tojinbo 75%, Rainbow Line 92%, Katsuyama 85%, Awara Onsen 81%.

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
20.4% and 36.9%.

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
- **Weather forecasts** are the next feature to add.
- **Eiheiji** stays pending until a real visitor count exists. The temple
  charges admission, so its own counts are the likely source; the request
  goes through the team lead.
