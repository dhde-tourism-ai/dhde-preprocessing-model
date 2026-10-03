# Pickup model: final hotel occupancy from bookings so far

Prototype for the decision "forecast final occupancy, or only monitor
bookings?". Code: `src/dhde_preprocessing/pickup.py`. To rerun:
`python scripts/backtest_pickup.py` (needs the reservation repos next to this
one), which writes `output/pickup_backtest.csv`.

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
at 100%. The ~80% range is the 10th-90th percentile of past errors.

## Backtest

Every night is forecast 7, 14, 30 and 60 days ahead with only what was known
then. Scored on the last 120 days held out (June to September 2026), mean
absolute error in occupancy points (lower is better):

| feed | lead | **blend** | bookings so far (otb) | same night last year | blend bias | range covers |
|---|---|---|---|---|---|---|
| Fukui Station (585 rooms, from 2023-12) | 7 | **4.2** | 7.2 | 9.1 | +0.9 | 77% |
| | 14 | **5.3** | 11.8 | 9.1 | +0.5 | 78% |
| | 30 | **8.1** | 19.5 | 9.1 | -1.1 | 84% |
| | 60 | **9.2** | 28.8 | 9.2 | -1.0 | 78% |
| Echizen Coast (74 rooms, from 2024-12) | 7 | **3.0** | 3.6 | 10.2 | +0.4 | 81% |
| | 14 | **4.5** | 5.8 | 10.2 | +0.5 | 74% |
| | 30 | **6.8** | 13.1 | 10.2 | -1.6 | 73% |
| | 60 | **9.6** | 26.5 | 10.9 | -5.1 | 65% |
| Awara Onsen (fukui-kanko, from 2023-09) | 7 | **2.0** | 4.0 | 8.7 | -0.2 | 82% |
| | 14 | **3.0** | 7.3 | 8.7 | -0.2 | 83% |
| | 30 | **5.3** | 16.8 | 8.7 | -1.9 | 80% |
| | 60 | 9.1 | 33.4 | **8.8** | -6.5 | 70% |
| Rainbow Line (mikatagoko, from 2025-06) | 7 | 5.7 | **5.3** | 16.8 | +1.6 | - |
| | 14 | **8.3** | 8.8 | 16.7 | +0.8 | - |
| | 30 | **15.1** | 16.4 | 17.0 | +3.8 | - |
| | 60 | 19.5 | 25.8 | **15.8** | +0.6 | - |

Per method (`recent`, `last_year`, `blend`) with skill scores:
`output/pickup_backtest.csv`. `last_year` beats `recent` at 30-60 days at
Fukui Station and Echizen Coast (the season shift), `recent` wins at Awara at
60; `blend` is never far from the better one.

## What it says

- **7 to 30 days out the model clearly helps**: it halves the error of
  "bookings so far" and beats "same night as last year" on the three feeds
  with more than a year of history (Fukui Station 30 days: 8.1 vs 19.5 and 9.1).
- **60 days out it is no better than last year's final** (about 9 points
  either way), and slightly low (bias -1 to -6.5): the range is the honest
  part there.
- **Rainbow Line isn't ready**: one year of history, so no last-year pickup and
  no range yet; it gains little over bookings so far.
- **Ranges**: the 10th-90th range covers 73-84% of held-out nights at 7-30
  days, close to the 80% it aims for; 65-78% at 60 days (too narrow).
- Echizen Coast's hotel list (74 rooms) hasn't changed since the feed began,
  and its monthly occupancy shows no level jump. Awara's capacity grew (392 to
  576 rooms): working in occupancy % keeps that from reading as demand.

A quick regression on top (bookings so far, last 7 days' booking pace, both
pickups, night type, month) cut Fukui Station's error at 30 and 60 days to
about 5.6 points, but was worse than `blend` at 60 days on Echizen Coast and
Awara, on one 4-month test window. Worth a rolling backtest before trusting it.

## Suggestion

Forecast final occupancy with `blend` at 7-30 days for Fukui Station, Echizen
Coast and Awara Onsen, with the range; further out, show last year's final
beside it. Keep Rainbow Line on bookings only until it has two summers.
