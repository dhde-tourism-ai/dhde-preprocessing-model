# From measured counts to visitors: calibration check

No node counts visitors. Each measures something else (camera detections,
cars, museum bookings, hotel guests), so the forecast converts it with one
factor per node (`forecast.calibration()`):

    visitors = count x factor
    factor   = official visitors in the period / (mean daily count in the period x days)

The official figures live in each node's config (`official_visitors`),
with their source. This page checks whether one factor per node is sound.
To rerun: `python scripts/check_calibration.py` (after
`build_integrated.py`), which writes `output/calibration_check.csv`.

## The test

A single factor is only right if the count rises and falls through the
year the way visitors do. The one independent monthly visitor figure is the
digital tourism statistics for the municipality each node is in (see
`monthly_visitors.py`; comparable for Fukui from 2025-01). The check
compares 20 months (2025-01 to 2026-08) of each node's count with its town's
visitors:

- **corr**: do they rise and fall together? (1 = same shape)
- **spread**: how much count / town visitors moves month to month. Small
  means one factor fits every month.

The town is bigger than the site, so a perfect match isn't expected.

| Node | Measured | Factor | Count per visitor | vs town | corr | spread | Verdict |
|---|---|---|---|---|---|---|---|
| Tojinbo | Camera detections | 0.198 | 5.05 | Sakai city | 0.94 | 14% | One factor is sound |
| Fukui Station | Camera detections | none | | Fukui city | 0.80 | 13% | Shape is sound; no official site figure |
| Rainbow Line | Cars, both gates | 6.98 | 0.14 (7 visitors per car) | Mihama + Wakasa | 0.89 | 32% | Level unconfirmed (see below) |
| Katsuyama | Museum bookings | 1.76 | 0.57 | Katsuyama city | 0.90 | 61% | Use the museum's own figure (done) |
| Awara Onsen | Hotel guests | 1.86 | 0.54 | Awara city | 0.55 | 30% | Reasonable for an onsen site; see below |

## Findings

**Tojinbo: the camera counts each visitor about 5 times, not 9.** Over
2025 the camera averaged 9,002 detections a day against 1,784 official
visitors a day (651k / 365). The briefing's "about 15,700 a day on camera"
is the summer level (August 2025 averaged 15,348, August 2026 17,818), and
dividing a summer day by the yearly average visitor day gives the 9x. The
camera follows Sakai city's monthly pattern closely (0.94), so one yearly
factor is the right method here. Why 5 detections per visitor isn't in the
camera documentation (code4fukui/fukui-kanko-people-flow-data, "general
person detection"); repeat passes in front of one camera are the likely
reason, but that is unconfirmed.

**Katsuyama: the area figure was the wrong basis.** Museum bookings peak
in summer far more than Katsuyama city's visitors do (August 2.4x the
average ratio, January 0.2x), so scaling bookings to the prefecture's
Katsuyama area figure (1,562k, calendar 2025) spread the area's other
visitors over the museum's summer peak. The node now uses the museum's own
entries: 1,298,975 in FY2025, April 2025 to March 2026 (a record; FY2024
was 1,264,541; [Chunichi](https://biz.chunichi.co.jp/news/article/10/124006/),
[Nikkei](https://www.nikkei.com/article/DGXZQOCC130HQ0T10C26A4000000/)),
over the same months. The factor, 1.76, says bookings are 57% of
entries, which matches the share already noted in `visitor_reservation.py`.

**Rainbow Line: 7 visitors per car is probably right for the official
figure, but can't be confirmed from our data.** The two summit car-park
gates counted 62,199 vehicles in 2025 (gate 1: 55,997, gate 2: 6,202),
against 443k official visitors. Buses alone don't explain it: the plate
data has no bus type (2025: 45,278 passenger cars, 6,566 rental cars,
5,681 commercial, 4,674 other). More likely the official figure counts
everyone who uses the Rainbow Line, while the gates only see cars that
park at the summit. The camera documentation also notes "operational
issues" with gate 1's plate camera (dates not published). The winter
months read near 0 (January 2026 at 0.07x the average ratio), so the car
parks look closed in winter while the towns still have visitors. Asking the
publisher what the 443k counts would settle it. Summing the gates is fine:
gate 2 is 10% of the cars and reads 0 on most days.

**Awara Onsen: hotel guests follow the onsen season, not the town's day
trips.** Guests are high in winter (February 2025 1.6x the average ratio)
when Awara city's visitors are lower, so the pattern differs (0.55). For
the Awara Onsen site (658k, a hot-spring town mostly visited for stays)
the hotel pattern is the more fitting one, so the factor stays. Only the 10
hotels in the feed are counted (576 rooms). Awara city's tourism white
paper publishes monthly ryokan guests for the whole town, which would be
the next check.

**Fukui Station: keep camera detections, don't invent a visitor number.**
There is no official figure for the station; the only one is Fukui city
(4,014k), and there is no basis for the station's share of it. The camera
follows the city's monthly pattern (0.80), so it is a sound signal of busy
and quiet periods. Recommended display: detections, or a share of a
typical day (e.g. 120% of normal), labelled as such, not visitors.

## Method notes

- The factor uses the daily mean over the official period, not the sum,
  so days a sensor was down don't inflate it. It needs 300+ measured days.
- The official counts are gross visits (one person at two sites counts at
  each).
- The factor fixes the yearly level; the day-to-day and month-to-month
  pattern is still the node's own count. The check above is what says
  whether that pattern can be trusted.
- The app's `build_real_data.py` scales its history with the prefecture's
  area figure for Katsuyama and with the sum rather than the mean; it
  should follow these factors so history and forecast use the same unit.
