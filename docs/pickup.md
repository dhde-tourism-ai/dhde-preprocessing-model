# Pickup model: final hotel occupancy from bookings so far

Prototype for the decision "forecast final occupancy, or only monitor
bookings?". Code: `src/dhde_preprocessing/pickup.py`. To rerun:
`python scripts/backtest_pickup.py` (needs the reservation repos next to this
one), which writes `output/pickup_backtest.csv` and
`output/pickup_backtest_seasons.csv`.

## The data

The FTAS reservation feeds take a snapshot every day, so each night is seen
about 90 times as bookings come in (cleaned by `sources/hotel.py`). Share of
the final rooms already booked, median over nights:

| days before | Fukui Station | Echizen Coast |
|---|---|---|
| 1 | 99% | 100% |
| 7 | 91% | 93% |
| 14 | 83% | 87% |
| 30 | 70% | 67% |
| 60 | 51% | 33% |

So a week out most of the answer is already booked; the open question is 14
to 60 days out, where nights differ a lot (30 days out, the 10th-90th
percentile is 42-98% of the final at Fukui Station).

## The model

final = rooms booked at lead L + the mean pickup after lead L on similar
nights that had already ended, from the last 8 weeks (`recent`), the same
weeks last year (`last_year`), or the mean of the two (`blend`). Similar =
same night type: Saturdays and nights before a holiday together, the other
weekdays apart, Golden Week / Obon / New Year kept out and forecast from last
year's peak. All in occupancy %, pickup keeps its sign (cancellations), capped
at 100%. The ~80% range is the 10th-90th percentile of past errors (5th-95th
at 60 days, where the season moves between the past errors and the night).
Stale snapshots (a frozen feed repeating an earlier day) are dropped at every
lead, not only for the final: 14.5% of Fukui Station's bookings-so-far values
at the forecast leads were stale.

## Backtest

Every night is forecast 7, 14, 30 and 60 days ahead with only what was known
then. Scored on the last 120 days held out (June to September 2026), mean
absolute error in occupancy points (lower is better):

| feed | lead | **blend** | bookings so far (otb) | same night last year | blend bias | range covers |
|---|---|---|---|---|---|---|
| Fukui Station (585 rooms, from 2023-12) | 7 | **4.2** | 7.4 | 8.6 | +0.8 | 77% |
| | 14 | **5.4** | 11.9 | 8.8 | +0.6 | 79% |
| | 30 | **8.2** | 19.6 | 9.3 | -1.1 | 84% |
| | 60 | 9.2 | 28.1 | 9.2 | -1.1 | 89% |
| Echizen Coast (74 rooms, from 2024-12) | 7 | **2.9** | 3.6 | 10.4 | +0.4 | 80% |
| | 14 | **4.4** | 5.8 | 9.9 | +0.4 | 74% |
| | 30 | **6.8** | 13.1 | 10.4 | -1.6 | 75% |
| | 60 | **9.8** | 26.5 | 10.7 | -5.3 | 78% |
| Awara Onsen (fukui-kanko, from 2023-09) | 7 | **2.0** | 4.0 | 8.7 | -0.2 | 82% |
| | 14 | **3.0** | 7.3 | 8.7 | -0.2 | 83% |
| | 30 | **5.2** | 16.8 | 8.7 | -1.9 | 79% |
| | 60 | 9.1 | 33.5 | **8.8** | -6.7 | 83% |
| Rainbow Line (mikatagoko, from 2025-06) | 7 | 5.8 | **5.4** | 16.2 | +2.0 | - |
| | 14 | **8.7** | 8.9 | 17.1 | +1.2 | - |
| | 30 | **15.0** | 15.8 | 17.1 | +4.0 | - |
| | 60 | 19.8 | 26.6 | **14.9** | -1.3 | - |

Per method (`recent`, `last_year`, `blend`) with skill scores:
`output/pickup_backtest.csv`. `last_year` beats `recent` at 30-60 days at
Fukui Station and Echizen Coast (the season shift), `recent` wins at Awara at
60; `blend` is never far from the better one.

### Every season, not only one summer

The table above is one summer. To check the decision doesn't rest on it, each
season of the last year is held out in turn, every forecast and range still
learning only from the nights before (`score_by_season`). `blend` / bookings
so far / same night last year:

| feed | season | 7 days | 30 days | 60 days |
|---|---|---|---|---|
| Fukui Station | autumn 2025 | **3.3** / 6.4 / 8.8 | **7.3** / 17.6 / 8.5 | 9.1 / 25.9 / **8.7** |
| | winter | **2.4** / 5.5 / 8.7 | **6.9** / 15.3 / 8.6 | 9.8 / 21.3 / **9.1** |
| | spring 2026 | **3.9** / 7.7 / 10.3 | **7.6** / 17.1 / 10.4 | **8.2** / 27.8 / 10.0 |
| | summer 2026 | **4.2** / 7.4 / 8.6 | **8.2** / 19.6 / 9.3 | 9.2 / 28.1 / 9.2 |
| Echizen Coast | winter | 3.5 / **3.2** / 8.7 | **6.1** / 7.8 / 9.0 | **6.6** / 17.7 / 8.4 |
| | spring 2026 | **2.8** / 3.9 / 9.0 | **5.2** / 13.0 / 9.3 | **7.0** / 20.7 / 9.5 |
| | summer 2026 | **2.9** / 3.6 / 10.4 | **6.8** / 13.1 / 10.4 | **9.8** / 26.5 / 10.7 |
| Awara Onsen | autumn 2025 | **1.9** / 3.0 / 5.3 | **4.2** / 10.9 / 5.3 | 5.6 / 21.8 / **5.3** |
| | winter | **1.8** / 2.3 / 8.1 | **5.4** / 8.4 / 8.1 | **7.9** / 17.0 / 8.2 |
| | spring 2026 | **2.0** / 4.2 / 9.4 | **5.8** / 17.2 / 9.3 | 10.1 / 31.1 / **8.9** |
| | summer 2026 | **2.0** / 4.0 / 8.7 | **5.2** / 16.8 / 8.7 | 9.1 / 33.5 / **8.8** |

(Summer runs June to September 2026. Echizen Coast has no autumn 2025: its
feed began in December 2024, so no last year to compare with yet.)

- **30 days: `blend` is best in all 11 feed-seasons**, and at 7 and 14 days in
  all but Echizen Coast's winter, where bookings so far is a little better
  (3.2 vs 3.5 at 7 days): its quiet winter nights barely pick up.
- **60 days: a draw.** `blend` wins 5, last year's final 5, one tie. The
  summer result holds all year.
- **Ranges**: 74-98% coverage in every season except Awara's spring 2026
  (64% at 30 days, 56% at 60, bias -4.4 and -9.5: nights ended well above
  their pickup). `range_ok` in the CSV is false when a range covers under
  70% of held-out nights, so the app can hide or mark it.

## What it says

- **7 to 30 days out the model clearly helps**, in every season: it halves the
  error of "bookings so far" and beats "same night as last year" on the three
  feeds with more than a year of history (Fukui Station 30 days in summer: 8.2
  vs 19.6 and 9.3).
- **60 days out it is no better than last year's final** (about 9 points
  either way), and slightly low in summer (bias -1 to -6.7).
- **Rainbow Line isn't ready**: one year of history, so no last-year pickup and
  no range yet; it gains little over bookings so far.
- **Ranges**: the 10th-90th range covers 74-84% of held-out summer nights at
  7-30 days, close to the 80% it aims for. At 60 days the 10th-90th covered
  only 65% at Echizen Coast and 70% at Awara; the 5th-95th now used there
  covers 78-89%, but it is 27-37 points wide.
- Echizen Coast's hotel list (74 rooms) hasn't changed since the feed began,
  and its monthly occupancy shows no level jump. Awara's capacity grew (392 to
  576 rooms): working in occupancy % keeps that from reading as demand.

A quick regression on top (bookings so far, last 7 days' booking pace, both
pickups, night type, month) cut Fukui Station's error at 30 and 60 days to
about 5.6 points, but was worse than `blend` at 60 days on Echizen Coast and
Awara, on one 4-month test window. Worth a rolling backtest before trusting it.

## Suggestion

Forecast final occupancy with `blend` at 7-30 days for Fukui Station, Echizen
Coast and Awara Onsen, with the range (hidden where `range_ok` is false). At
60 days, show bookings so far and last year's final instead: as good and
simpler. Keep Rainbow Line on bookings only until it has two summers.
