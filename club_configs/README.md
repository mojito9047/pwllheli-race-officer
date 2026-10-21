# Other clubs' configurations

Worked examples of the three files that describe a club's water (`marks.json`, `courses.json` and `start_finish.json`), for clubs other than Pwllheli. [CLUB_CUSTOMISATION.md](../docs/CLUB_CUSTOMISATION.md) explains each file and everything else a new club sets.

| Folder | Club |
|---|---|
| [`liverpool_yc`](liverpool_yc/README.md) | Liverpool Yacht Club, River Mersey. No fixed courses; the line runs from the Brunswick lock house to Pluckington. |

This folder is in the repository only. **It is not in the release ZIP**, and a hut never reads it: an installation uses the files in its own `data/` folder. To set up a club, copy its three files into `data/` of an ordinary release.

Each club folder's `README.md` gives its sources and what still needs checking on the water. Any `sim_app.py` beside them is **development only**: a temporary app with a simulated race for trying the files on a developer's PC, never for a production system.
