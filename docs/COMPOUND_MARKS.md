# Compound marks

Some CHPSC course marks represent a feature rather than a single buoy. The race officer should be able to put the short parent mark on the course board, while the app uses the real corner points for charting and leg analysis.

Not to be confused with a **waypoint** ([`WAYPOINTS.md`](WAYPOINTS.md)), which is the other way round: a compound mark is one name for two things boats really do round, while a waypoint is a turning point boats do not round at all and which never reaches the course board.

## Current compound marks

### Y — Gwylan Islands

- `Yp`: round `Ya` to port, then `Yb` to port.
- `Ys`: round `Yb` to starboard, then `Ya` to starboard.

### A — Carreg Y Trai / St Tudwal's Islands

- `Ap`: round `Aa` to port, then `Ab` to port.
- `As`: round `Ab` to starboard, then `Aa` to starboard.

## Behaviour in the app

Course boards, public course sequences and audio announcements show whatever the race officer put on the course: the parent where the pair is being rounded (`Yp`, `As`), or the corner where only one is (`Yap`).

Course length, leg analysis, TWA/sail calculations and course charts use the expanded physical points. The course chart draws and labels the component points (`Ya`, `Yb`, `Aa`, `Ab`) so the route shape is clear.

The manual-course builder offers the parent marks `Y` and `A` **and** their corners `Ya`, `Yb`, `Aa`, `Ab`, with each corner listed directly under its parent. Picking the parent sends the fleet round both corners in the order the rounding requires; picking a corner sends them round that one only. On the board a corner reads by its display label (`Yap`, not `YAp`), and the audio names the feature before the corner — "Gwylan Islands corner Ya" — because "mark Y A" read aloud is "mark why-ay".

## Adding and removing marks

Administrators can add a **simple** mark (code, name, position in decimal degrees, buoy, top-mark) and remove an unused mark directly from the **Marks** page in the app — the position text is generated automatically and the change is written to `data/marks.json`. A mark cannot be removed while it is used in a fixed course or the start/finish line, or if it is a compound mark or a component of one. **Compound** marks (below) are still created and edited by hand in `data/marks.json`.

## Marks file format

A compound parent mark in `data/marks.json` uses:

```json
{
  "name": "Gwylan Islands",
  "lat": null,
  "lon": null,
  "compound": true,
  "components": ["YA", "YB"],
  "rounding_order": {
    "port": ["YA", "YB"],
    "starboard": ["YB", "YA"]
  }
}
```

Each component mark is a normal coordinate-bearing mark, with `component_of` and `hidden_from_picker` added:

```json
{
  "name": "Gwylan Islands corner Ya",
  "display": "Ya",
  "lat": 52.789985,
  "lon": -4.6933183333,
  "component_of": "Y",
  "hidden_from_picker": true
}
```

`display` is optional, but it lets the app show `Ya` rather than the internal JSON key `YA`.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
