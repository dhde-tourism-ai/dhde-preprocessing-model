# Osaka and Kyoto data sources

Sources suggested for adding Osaka and Kyoto nodes, checked 2026-09-27:
what each link really contains, and whether it can feed a daily node
table the way the Fukui, Ishikawa and Toyama sources do. The Osaka and
Kyoto nodes are now configured with the existing sources below (see the
README's Kyoto and Osaka section); this file is the record of what else was
checked.

## Summary

None of the suggested links adds new **daily** tourism-demand data that
can be pulled on a schedule. Several aren't what their label says. But
four sources this repo already has cover Osaka and Kyoto as they are
(see [Already covered](#already-covered-by-existing-sources)).

| # | Suggested as | What it really is | Pull on a schedule? | Cost |
|---|---|---|---|---|
| 1 | Osaka weather | Osaka hourly photochemical oxidant (air quality) | Yes | Free |
| 2 | Osaka tourism survey | Annual visitor and lodging statistics (Excel/PDF) | No | Free |
| 3 | Osaka AI camera, traffic, RSI | Hanshin Expressway vehicle trajectories, five 1-hour samples | No | Application needed |
| 4 | Osaka / Kyoto traffic and AI camera | JARTIC open data page (no camera data) | Already used, via the API | Free |
| 5 | Osaka people flow | MLIT monthly staying population, 2019–2021, frozen | No | Free |
| 6 | Kyoto reservation | AirROI: Airbnb / short-term rental metrics | Only through a paid API | Paid |
| 7 | Kyoto reservation | Doorstep Analytics: Airbnb listings | No | Datasets paid |
| 8 | Kyoto weather | JMA obsdl download form | No (use ETRN instead) | Free |
| 9 | Kyoto RSI | Road damage along Kyoto City Bus Route 3 | No | Free |
| 10 | Kyoto people flow | MLIT monthly staying population, 2019–2021, frozen | No | Free |

## Each source

### 1. Osaka "weather": code4fukui/taiki_osaka
https://github.com/code4fukui/taiki_osaka
- **What it is:** air quality, not weather. Hourly photochemical oxidant
  (OX) readings from all of Osaka Prefecture's air-pollution monitoring
  stations (大気汚染常時監視システム).
- **Access:** CSV in the repo (`data2/OX.csv`), refreshed hourly by a
  GitHub Action. No registration. The repo is MIT; the prefecture's own
  terms for the data aren't stated.
- **Use:** could be an optional extra column (e.g. air-quality alerts).
  It doesn't replace weather.

### 2. Osaka tourism statistics
https://www.pref.osaka.lg.jp/o070070/kanko/toukei/index.html
- **What it is:** Osaka Prefecture's inbound visitor survey (2018, 2019,
  2023, 2024) and accommodation statistics by area (2020–2024). The survey
  paused from 2020 to March 2023.
- **Access:** annual Excel and PDF reports.
- **Use:** yearly context or a baseline only, not a daily signal.

### 3. Osaka "AI camera, traffic and RSI": Zen Traffic Data
https://zen-traffic-data.net/english/outline/dataset.html
- **What it is:** Hanshin Expressway vehicle trajectories (every 0.1 s,
  from image sensing), on three expressway sections (Route 11 Ikeda,
  Route 4 Wangan, Route 13 Higashiosaka). As of February 2023, five
  one-hour datasets: research samples, not a continuous series.
- **Access:** a data-usage application is required. Fees aren't stated.
- **Label check:** no people counts, no tourist sites, and no RSI
  (search intent) data.
- **Use:** none for daily demand.

### 4. Osaka and Kyoto traffic: JARTIC open data
https://www.jartic.or.jp/service/opendata/
- **What it is:** the same source as this repo's `traffic` module.
  Monthly CSV files; older data can't be downloaded once a month is
  replaced. Nothing on the page is specific to Osaka or Kyoto, and it has
  no AI camera data.
- **Use:** use the existing JARTIC API instead (`sources/traffic.py`),
  which works nationwide: an Osaka or Kyoto node only needs a nearby
  `point_code` in its config.

### 5 and 10. Osaka and Kyoto people flow: MLIT data on geospatial.jp
Osaka: https://www.geospatial.jp/ckan/dataset/mlit-1km-fromto/resource/b82f2e11-7301-467a-8fa6-f63f3af2d8e1
Kyoto: https://www.geospatial.jp/ckan/dataset/mlit-1km-fromto/resource/0be83158-af12-4487-97ef-dbee33957131
- **What it is:** `monthly_fromto_city_27` (Osaka) and
  `monthly_fromto_city_26` (Kyoto), the staying population per
  municipality by where visitors came from, from Agoop estimates. Each
  value is the average daily population over a month, split by weekday or
  holiday and by day or night. These are municipality files, not the 1km
  mesh files (those are separate resources in the same dataset).
- **Coverage:** 2019, 2020 and 2021 only. Last updated 2022-01-13, made
  for COVID-19 analysis, and no releases are planned after that.
- **Access:** free ZIP download, 政府標準利用規約 (Government Standard Terms
  of Use).
- **Use:** a one-time historical baseline at most.

### 6. Kyoto "reservation": AirROI
https://www.airroi.com/data-portal/markets/kyoto-japan
- **What it is:** Airbnb and short-term rentals only, not hotels:
  occupancy, ADR, RevPAR and listing counts (about 4,100 listings), plus
  forward calendar rates. Updated monthly.
- **Access:** downloads and an API, pay as you go. Prices aren't stated.
- **Use:** only if the project pays, and it measures short-term rentals,
  a different market from the hotel feeds used for Fukui.

### 7. Kyoto "reservation": Doorstep Analytics
https://doorstepanalytics.com/report?location=Kyoto&country=Japan
- **What it is:** Airbnb listing data (prices, calendar, reviews, hosts).
  Prices are sampled from the first five available nights within the next
  two months.
- **Access:** free web dashboard; the datasets are paid downloads. No API.
- **Use:** none for a scheduled pipeline.

### 8. Kyoto weather: JMA obsdl portal
https://ds.data.jma.go.jp/risk/obsdl/
- **What it is:** JMA station and AMeDAS data, hourly and daily.
- **Access:** a multi-step download form with a size limit per request;
  it can't be scripted reliably (see the README's weather note).
- **Use:** use the repo's existing weather module instead. It scrapes JMA's
  ETRN pages and works for any station: an Osaka or Kyoto node only needs
  the station's `prec_no` / `block_no` / `page` in its config.

### 9. Kyoto "RSI": Kyoto City open data 00618
https://data.city.kyoto.lg.jp/dataset/00618/
- **What it is:** not RSI (route search intent). It's road deterioration
  detected by video along Kyoto City Bus Route 3 (北白川仕伏町 to 松尾橋):
  damage type and location. Made at a 2022 data hackathon, CSV, CC0,
  created and last updated 2022-04-28.
- **Use:** none for tourism demand.

## Already covered by existing sources

These modules already work for Osaka and Kyoto with config only, no new
code:

| Source | Osaka / Kyoto coverage |
|---|---|
| `weather` (JMA ETRN) | Any JMA station |
| `traffic` (JARTIC API) | Any JARTIC point, national roads only |
| `monthly_visitors` (JTA digital tourism statistics) | Osaka (lgcode 27) and Kyoto (26) prefectures and their cities and wards, monthly, 2021-01 to 2026-08 (e.g. Osaka Kita-ku 27127, Kyoto Nakagyo-ku 26104) |
| `rakuten` (Rakuten Travel API) | Any coordinates; history only builds from the first daily run |

## Still missing for Osaka and Kyoto

Compared with Fukui, no source has been found yet for:
- a daily visitor count (camera or site entry bookings)
- route-search / map intent (RSI)
- hotel reservation snapshots
- a visitor survey
