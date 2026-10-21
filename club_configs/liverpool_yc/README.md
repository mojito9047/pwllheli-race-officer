# Liverpool Yacht Club configuration

These replace `data/marks.json`, `data/courses.json` and `data/start_finish.json` for an installation at Liverpool Yacht Club (River Mersey, Liverpool Marina). They were built on 2026-10-20 from the documents linked on [lyc.org.uk/race-information](https://www.lyc.org.uk/race-information):

| Document | Used for |
|---|---|
| LYC Sailing Instructions 2025 v2 (published 4 April 2025) | Start/finish line (SI 11, 15.1), courses (SI 8), marks (SI 9), VHF, time limit |
| Marks 290520.pdf, "Type and position of marks" (29 May 2020) | 16 mark positions, plus the bearing and distance tables used to cross-check them |
| "Mersey river race marks" chart (LYC 2018) | Vatican (the 17th mark) and where the line is drawn |
| Notice of Race 2025 v1 | Confirms courses are published on the day (NoR 11) |

## What is in each file

- **marks.json**: 17 marks under the club's own abbreviations (BRM, DNG, DKS, E02, E04, E06, G01, G02, G03, G05, G07, G09, G11, MID, NEL, PLK, VAT). Positions are given exactly as the club publishes them, to 0.01 of a minute.
- **courses.json**: no courses. LYC has no fixed courses. River courses are set on the day from the marks list (marks in order, rounding sides and laps), shown in the lock or given on VHF 37 (SI 8.1.1). Every LYC race is therefore a made-up course.
- **start_finish.json**: one line, `lyc`, used for both starting and finishing. It runs from the Brunswick lock house flagstaff out to Pluckington (PLK), 656 m. A 100 m seaward extension allows for PLK's position error. Pluckington bounds the line because boats must finish outside the shipping channel that it marks (SI 15.1.3).

## Checks made

- Every position matches the source PDF exactly (script comparison).
- The club's distance table matches its positions except for **G11** (out by 0.2–0.5 nm) and **Midway** (about 360 m, a club-laid buoy that is re-laid from time to time). OpenStreetMap shows a green lateral buoy 18 m from the published G11 position. The published G11 position is therefore kept, and the table is wrong.
- Compared with OpenStreetMap buoys, 11 marks are within 18–115 m (E06 21 m, G09 20 m, PLK 74 m, NEL 115 m). OSM has no buoy near Dukes, G01, Midway or Vatican.
- **Brombro and Dingle need checking on the water.** OSM shows an *east cardinal* 9 m from Dingle's position (the club lists Dingle as a yellow special) and no cardinal near Brombro.

## Before relying on it

1. **Survey the shore end.** No position is published for the flagstaff. The one used here is the small building beside the lock's river gate in OpenStreetMap (way 269224156), which is where the club chart draws the line. The transit bearing is set by the handrail triangle, which needs surveying too. The chart draws the line at about 246° true, but flagstaff-to-PLK is 237°.
2. **Re-measure the marks from tracks.** These are big ship buoys on long moorings in 5-knot tides, and the club calls its positions approximate.
3. **The app now takes the start line from `start_finish.json`** (`core/startfinish.py`, 2026-10-20). Courses are measured from PLK, made-up courses start there, and the course chart draws the line from PLK to the flagstaff, labelled "Lock house" (`short_label`). Before that change, course lengths showed "—" and the chart drew Pwllheli's line.

   VHF announcements say a mark's own name from `marks.json` ("Pluckington"); Pwllheli's spoken names for O, F, C, E, T, M, Y and A are used only for Pwllheli's marks (v1.019). Still Pwllheli's: those letters come first in the mark picker (`core/courses.py`).

## `sim_app.py`: development only

**Not for a production system, and not for a club's race office PC.** It starts a temporary Liverpool app on a developer's PC with one simulated race already sailed (race 9001: seven LYC boats, a made-up river course, made-up tracks and wind), so the club's files can be tried in the charts, the results and a 3D film.

```
python club_configs\liverpool_yc\sim_app.py
```

Then open http://localhost:5059. `--rebuild` starts again from nothing. Its data is kept in `runtime/lyc_sim/`, and the generated test login is in `runtime/lyc_sim_login.txt`. Neither is committed.

It runs Flask's development server on 127.0.0.1 over plain http, keeps a test password in a text file, writes invented data straight into its own database, and points the app at its sandbox by patching module attributes. None of that is how the app is installed. A real Liverpool installation is an ordinary release with the three JSON files above copied into its `data/` folder, as described in [CLUB_CUSTOMISATION.md](../../docs/CLUB_CUSTOMISATION.md).

## Not included

- **Committee-boat lines.** SI 11 and 15.1.5 allow them, and the IRC Championships use one. They have no fixed position.
- **Long races.** The Isle of Man Midnight Race (start at the LYC line, Dukes to port, Crosby and Queens channels, Q2 to starboard, Douglas No. 1 to port, finish off Victoria Pier), the OS series and the Lyver Trophy have their own SIs. Their marks are not on the club's list.
- **Three Rivers Challenge.** Pluckington, then G11, Gut and West Bar in any order, then back to Pluckington. Because the order is free, it is not a fixed course.
