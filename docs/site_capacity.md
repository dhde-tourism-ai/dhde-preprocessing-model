# Site capacity reference

Open data found (2026-09-27) for turning visitor counts into a congestion
score. No site publishes a hard visitor capacity (a maximum number of
people), so this records the next best things: official annual visitor
counts and physical limits such as parking. Turning these into a 0 to 100
score is a modelling-stage decision; nothing here is used by the pipeline.

Annual visitors are Fukui Prefecture's 観光客入込数 (延べ人数: a visitor
to several sites counts at each), 2025, PDF only, no monthly split by site:
https://www.pref.fukui.lg.jp/doc/kankou/fukuiken-kankoukyakusu_d/fil/024.pdf
(index and earlier years:
https://www.pref.fukui.lg.jp/doc/kankou/fukuiken-kankoukyakusu.html)

| Node | Annual visitors 2025 | Official parking | Other capacity data | Caveats |
|---|---|---|---|---|
| Tojinbo | 651k (778k in 2024) | None: city lot closed since 2023-07 for redevelopment | Sightseeing boats: 4 x 69 passengers, every 15-20 min, Apr-Oct | New lot planned at 18,000 m², no car count or opening date |
| Mikuni Port | 396k town walk, plus 160k Yuaport hot spring | None for the town | Nearby (MLIT): Sunset Beach 323, Kuzuryu River Boat Park 186, station 28 + 3 large | |
| Awara Onsen | 658k | 165 (Yunomachi Station south lot) | Monthly ryokan guests in the city white paper; peak 76,624 in Aug 2025 | Total ryokan rooms not published |
| Katsuyama | 1,562k area; museum 1.30M (FY2025) | ~1,400 cars (museum site; fuku-e says 1,500) | Timed-entry tickets, daily cap not published | Entry bookings are in the pipeline (visitor_reservation) |
| Eiheiji | 518k | 3 town lots, counts not published | | Unofficial ~50 / 60 / 70 cars; lots 2-3 weekends only |
| Ono Castle Town | Not broken out (town centre 598k) | 297 cars + 14 buses (4 official lots) | | Castle closed Dec-Mar |
| Maruoka Castle | 340k (203k in 2024) | 180 cars + 4 buses | | Keep closed for renovation, no end date |
| Fukui Station | Not broken out (Fukui City 4,014k) | 200 (west exit underground lot) | 12 downtown lots, 2,065 spaces in total | |
| Rainbow Line | 443k | 200 across lots 1 and 2 (tourism sites; no official split) | Lift ~450/hour each way; cable car 30 per car | Cameras count vehicles at these same lots |

## Sources
- Tojinbo: https://www.city.fukui-sakai.lg.jp/kankou/kanko-bunka/kanko/shizen/tojinbo.html ,
  https://www.toujinbou-yuransen.jp/about/ ,
  http://www.city.fukui-sakai.lg.jp/kankou/toshisaisei/documents/toshisaisei-tojinbo.pdf
- Mikuni: https://www.pa.hrr.mlit.go.jp/minato-oasis/point/1239/
- Awara: https://www.city.awara.lg.jp/mokuteki/industry/kanko/konkojouhou/kankohakusho_d/fil/r7hakusyo.pdf ,
  https://www.city.awara.lg.jp/mokuteki/life/life0701/p011819.html
- Katsuyama: https://www.dinosaur.pref.fukui.jp/other/help.html ,
  https://www.fuku-e.com/spot/detail_1193.html
- Eiheiji: https://www.town.eiheiji.lg.jp/600/601/p002062.html
- Ono: https://www.city.ono.fukui.jp/kanko/kanko-joho/guide/joukamachi-higashi.html ,
  https://www.city.ono.fukui.jp/kanko/kanko-joho/guide/yuistation.html ,
  https://www.city.ono.fukui.jp/kanko/jigyosha/jigyosha-joho/kankouirikomi6.html
- Maruoka: https://maruoka-castle.jp/map-access/ , https://maruoka-castle.jp/information/
- Fukui Station: https://www.pref.fukui.lg.jp/doc/hozen/chikachu.html ,
  https://fukuimachip.ftmo.co.jp/
- Rainbow Line: https://mikatagoko.com/park-information/ ,
  https://mikatagoko.com/cable-car/ , https://www.fuku-e.com/spot/detail_1330.html

## Suggested use (modelling stage)
- Scale each node against its own history (e.g. 95th-percentile day = 100)
  so every node gets a score today.
- Use annual visitors to compare nodes (Katsuyama gets ~2.4x Tojinbo).
- Use parking as a hard physical limit where it matches the sensor, best
  at Rainbow Line (vehicle cameras at the 200-space lots).
- Flag Maruoka's renovation so a drop there isn't read as low demand.

## Still missing (would need a call to the site or tourism office)
- Tojinbo new car park capacity and opening date (Sakai City 観光交流課).
- Eiheiji official parking counts (Eiheiji Town).
- Awara Onsen total ryokan rooms (Awara Onsen ryokan association).
- Rainbow Line split between lot 1 and lot 2 (operator, 0770-47-1170).
