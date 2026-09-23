# Public read-only API

The Race Officer app answers a few read-only JSON requests without a login, for displays that are
not the app's own pages: the Mojito range-and-bearing app, a chart plotter drawing the current race,
anything else that wants the course and the marks. They carry nothing a competitor cannot already
see on the public race page.

For the competitor pages themselves, see [`PUBLIC_COMPETITOR_PAGE.md`](PUBLIC_COMPETITOR_PAGE.md).

## `/api/current_race_course`

One JSON document with everything an external display needs to draw the current race: the race,
what the race officer has decided about it, the course as set, the course as it is being sailed,
and every mark's position. Written for the Mojito range-and-bearing app and chart-plotter
displays; it carries the same course and mark information competitors can already see.

`GET`, no login, no parameters. It always answers for the **current** race: the newest race with a
boat still marked racing, or failing that the newest race by warning time (creation time when no
warning time is set). That is the race `/public/current` shows.

When there is no race at all it answers `404`:

```json
{"ok": false, "app": "Pwllheli Race Officer", "version": "1.011", "message": "No current race is available."}
```

**Conventions**

* **Times** are ISO 8601 **local time with no zone** — the hut's clock, so UK time and BST in
  summer: `"2026-08-13T10:49:00"`. An unset time is `""`, never `null`.
* **Positions** are decimal degrees, `lat` and `lon`. `lat_text` and `lon_text` are the same
  position the way the sailing instructions write it, `52° 52.700'N`.
* **Every position is the race's own.** A mark re-laid or corrected since is not moved under a
  race already sailed; the payload for last month's race has last month's buoys.
* **Distances** are nautical miles, great-circle. **Bearings** are degrees true, the initial
  bearing from one point to the next.
* A mark has a **`code`**, the key used everywhere in the data (`YA`), and a **`display`**, what
  goes on the board and in the sailing instructions (`Ya`). Match on `code`; show `display`.

### Top level

| Field | Meaning |
|---|---|
| `ok` | `true`. |
| `app` | `"Pwllheli Race Officer"`. |
| `version` | The Race Officer version answering, e.g. `"1.011"`. |
| `race` | The race and what has been decided about it. |
| `course` | The course as set, and `course.sailed`, the course as it is being sailed. |
| `marks` | Every mark in the club's list, keyed by `code` — not only the ones on the course. |

### `race`

| Field | Meaning |
|---|---|
| `id` | The race's id. `/public/race/<id>` is its page. |
| `name` | e.g. `"Club Race"`. |
| `class_name` | The class the race is for, or `""` when it is open. |
| `series_id`, `series_name` | The series it belongs to, or `null`. |
| `rating_rule` | `IRC_TCC`, `YTC` or `DUAL` (both). |
| `course_no` | The fixed course number stored on the race. **Meaningless when `custom_course` is true** — read `course` instead. |
| `custom_course` | `true` when the race officer made the course up rather than choosing a fixed one. |
| `course_set` | `false` until the race officer chooses a course. A new race is created with a course number, so `course` always has marks in it — **do not import a course while this is false.** |
| `first_warning_time` | The first warning signal, or `""`. |
| `start_time` | The same value. It is the historic name of the column and is kept for existing importers. |
| `first_start_time` | The first start, five minutes after the warning, or `""`. Count down to this. |
| `postponed` | `true` while AP, AP over H or AP over A is flying. Hold the countdown. |
| `postponement_flag` | `"AP"`, `"AP over H"`, `"AP over A"`, or `""`. |
| `postponement_ends_at` | When the race officer has said AP comes down, if they have; `""` otherwise, and always `""` when not postponed. |
| `status` | One of the values below. |
| `entry_count`, `racing_count`, `finished_count` | Boats entered, still racing, and finished. A boat counts as racing from the moment it is entered, before any gun. |
| `race_finished` | `true` once there are entries and none is still racing. |
| `public_urls` | Links to the race's public pages and feeds, relative to the host: `current`, `desktop`, `mobile`, `state`, `positions`, `track`. `state` is the one pinned to this race. |

`status` is worked out when asked, in this order:

| `status` | When |
|---|---|
| `Finished` | There are entries and none is still racing. |
| `Start sequence pending` | The first start is still ahead. **Still said while AP is flying** — check `postponed`. |
| `Racing` | At least one boat is marked racing and the first start is not in the future — **including when no warning time has been set.** Boats are stored as racing from the moment they are entered, so a race with entries and no start time says `Racing`. |
| `Started` | The first start has gone and nobody is entered. |
| `Awaiting start time` | No warning time has been set and nobody is entered. |

### `course` — the course as set

| Field | Meaning |
|---|---|
| `source` | `"fixed"` for one of the club's numbered courses, `"manual"` for a made-up one. |
| `course_no` | The course number, e.g. `3`; the string `"Made up course"` for a made-up course. |
| `name` | The wind label of a fixed course (`"N"`, `"SW"`), or `"Made up course"`. |
| `wind_label`, `wind_bearing_deg`, `wind_range_deg` | The wind a fixed course is set for: `"N"`, `0`, `{"from": 349, "to": 11}`. The range can pass through north. All `null` for a made-up course. |
| `sequence_text` | The course board: `"1p 4p 7p"`, with a lapped course written once and followed by `×2`. |
| `length_nm` | For a **fixed** course, the published length from the course card. For a made-up course, measured from the legs. The two ways differ: course 3's card says 10.3, its legs add up to 10.65. |
| `laps` | Times round. `marks` is already the expansion — a course sailed twice has every mark twice. |
| `marks` | The course board, one entry per mark rounded, in order. See *Course marks*. |
| `expanded_marks` | The physical points boats sail past, in order, from the start. See *Expanded marks*. **Draw the route from these.** |
| `legs` | One per pair of consecutive `expanded_marks`. See *Legs*. |
| `shortened` | `true` once the course has been shortened. |
| `shortened_at` | `null`, or where it was shortened — see below. |
| `sailed` | The course as the fleet is sailing it now — see below. |

`course.marks`, `course.expanded_marks`, `course.legs` and `course.length_nm` stay the course **as
set**, even after a shortening. That is what was set; `course.sailed` is what is being sailed.

**Course marks** (`course.marks[]`) are the parent marks as the board shows them: a compound mark
is one entry, `Y`, not its two corners.

| Field | Meaning |
|---|---|
| `code`, `display`, `name` | e.g. `"1"`, `"1"`, `"Partington Marine"`. |
| `lat`, `lon`, `lat_text`, `lon_text` | Position. **A compound mark (`Y`, `A`) has none** — `lat` and `lon` are `null` and the text forms `""` — because it is a group of islands with no single position; its corners in `expanded_components` carry the coordinates. |
| `buoy`, `top_mark` | What it looks like: `"Large Barrel"`, `"Yellow Spherical"`. On a compound mark `top_mark` holds the rounding instruction from the sailing instructions instead. |
| `rounding` | `"port"` or `"starboard"` — the side it is left on — or `"via"` for a waypoint, which is not rounded. |
| `token` | The board token, `"1p"`; a waypoint's is just its code, `"TC"`. |
| `waypoint` | Only on a waypoint (`TC`, `TP`): `true`. It bends a leg and is not a mark; see *Expanded marks*. |
| `expanded_components` | The physical points this entry becomes, in the order they are rounded, each a mark with `parent_mark` and `parent_display` added. A plain mark is its own single component. |
| `compound`, `components`, `rounding_order` | Only on a compound mark: `true`, its corners, and the order they are rounded each way. |

**Expanded marks** (`course.expanded_marks[]`) are what the geometry is made of. The first is
always the start mark `O`, with `"rounding": "start"`. A compound mark becomes its corners in the
order they are rounded — `Y` to port is `YA` then `YB`, to starboard `YB` then `YA`. Each entry is a
mark as above, plus:

| Field | Meaning |
|---|---|
| `rounding` | `"start"`, `"port"`, `"starboard"`, or `"via"` for a waypoint. |
| `parent_mark`, `parent_display` | The board mark this point belongs to: `"Y"` for `YA`. |
| `sequence_mark`, `sequence_display` | The same. |
| `is_compound_component` | `true` for a corner of a compound mark. |
| `waypoint` | `true` for a turning point that bends a leg — the long courses west past the Llŷn have them so a leg does not cross land. **Bend the route there; do not draw it as a mark or count it as a rounding.** |
| `token` | The board token of the mark it belongs to. |

**Legs** (`course.legs[]`), one per consecutive pair of expanded marks, so there is always one
fewer leg than expanded marks. The first leg starts at `O`.

| Field | Meaning |
|---|---|
| `from`, `to` | Display codes of the two ends: `"O"`, `"1"`, `"Ya"`. |
| `from_mark`, `to_mark` | Their codes: `"YA"`. |
| `from_parent_mark`, `to_parent_mark` | The board marks they belong to: `"Y"`. |
| `to_rounding` | How the end of the leg is left: `"port"`, `"starboard"`, `"via"`. |
| `distance_nm`, `bearing_deg` | Length and bearing, e.g. `1.201`, `178.6`. `null` if either end has no position. |
| `finish` | Only on the leg to the finish of a shortened course, in `course.sailed.legs`: `true`. |

**`shortened_at`** is `null` until the course is shortened, then:

| Field | Meaning |
|---|---|
| `index` | Index into `course.marks` of the last mark rounded. |
| `mark`, `display` | That mark. |
| `label` | What the race officer picked, e.g. `"4 (rounding 1)"` or `"7 (lap 2, rounding 1)"`. |
| `time` | When the shortening was signalled. |

An index the course no longer reaches — the course was changed after it was shortened — is not a
shortening: `shortened` is `false`.

**`sailed`** is the route as the fleet is sailing it now:

| Field | Meaning |
|---|---|
| `sequence_text` | The board for what is being sailed: `"1p 4p"` once shortened at the second mark. |
| `marks`, `expanded_marks` | As above, cut at the shorten mark once shortened. |
| `legs` | As above, cut at the shorten mark, and then **one more leg, to the middle of the finish line**, with `"to": "Finish"`, `"to_mark": null` and `"finish": true`. That is the leg the competitor chart draws dashed. |
| `finish` | `null`, or once shortened the point that last leg goes to: `{"lat", "lon", "description": "Middle of the finish line"}`. It is the race's own finish line, the one GPS finish detection uses. |
| `length_nm` | Always **measured**, from `sailed.legs` — so once shortened it includes the run to the finish. |

Until a shortening, `sailed.marks`, `sailed.expanded_marks` and `sailed.legs` are the same as the
course's own. `sailed.length_nm` is not always the same as `course.length_nm`: a fixed course's is
the published figure and this one is measured, so course 3 shows 10.3 above and 10.65 here.

### `marks`

Every mark in the club's list, keyed by `code`, at the race's positions — whether it is on the
course or not, so a display can draw the whole area. Each is a mark as above without the course
fields (`rounding`, `token`). A compound mark carries `compound`, `components` and
`rounding_order`; each of its corners carries `component_of`.

### Example

Race 619, a fixed course shortened at mark 4 after the second rounding. Arrays are cut short.

```json
{
  "ok": true,
  "app": "Pwllheli Race Officer",
  "version": "1.011",
  "race": {
    "id": 619,
    "name": "Club Race",
    "class_name": "",
    "series_id": null,
    "series_name": null,
    "course_no": 3,
    "custom_course": false,
    "rating_rule": "DUAL",
    "start_time": "2026-08-13T10:49:00",
    "first_warning_time": "2026-08-13T10:49:00",
    "first_start_time": "2026-08-13T10:54:00",
    "status": "Finished",
    "course_set": true,
    "postponed": false,
    "postponement_flag": "",
    "postponement_ends_at": "",
    "entry_count": 2,
    "finished_count": 2,
    "racing_count": 0,
    "race_finished": true,
    "public_urls": {
      "current": "/public/current",
      "desktop": "/public/race/619",
      "mobile": "/public/mobile/race/619",
      "state": "/public/race/619/state",
      "positions": "/public/race/619/positions",
      "track": "/public/race/619/track"
    }
  },
  "course": {
    "source": "fixed",
    "course_no": 3,
    "name": "N",
    "wind_label": "N",
    "wind_bearing_deg": 0,
    "wind_range_deg": {"from": 349, "to": 11},
    "sequence_text": "1p 4p 1p 4p 5p 6p 5p 6p Op 8p 4p 7p Op",
    "length_nm": 10.3,
    "marks": [
      {
        "code": "1", "display": "1", "name": "Partington Marine",
        "lat": 52.8783333333, "lon": -4.405,
        "lat_text": "52° 52.700'N", "lon_text": "04° 24.300'W",
        "buoy": "Large Barrel", "top_mark": "Yellow Spherical",
        "rounding": "port", "token": "1p",
        "expanded_components": [{"code": "1", "...": "...", "parent_mark": "1", "parent_display": "1"}]
      }
    ],
    "expanded_marks": [
      {
        "code": "O", "display": "O", "name": "Outer Distance Mark (Welsh Water)",
        "lat": 52.8791166667, "lon": -4.3993333333,
        "lat_text": "52° 52.747'N", "lon_text": "04° 23.960'W",
        "buoy": "Large Yellow Barrel", "top_mark": "",
        "rounding": "start", "token": "O",
        "parent_mark": "O", "parent_display": "O",
        "sequence_mark": "O", "sequence_display": "O",
        "is_compound_component": false, "waypoint": false
      }
    ],
    "legs": [
      {
        "from": "O", "to": "1", "from_mark": "O", "to_mark": "1",
        "from_parent_mark": "O", "to_parent_mark": "1", "to_rounding": "port",
        "distance_nm": 0.211, "bearing_deg": 257.1
      }
    ],
    "laps": 1,
    "shortened": true,
    "shortened_at": {"index": 1, "mark": "4", "display": "4", "label": "4 (rounding 1)", "time": "2026-08-13T12:10:51"},
    "sailed": {
      "sequence_text": "1p 4p",
      "length_nm": 2.76,
      "marks": [],
      "expanded_marks": [],
      "legs": [
        {
          "from": "4", "to": "Finish", "from_mark": "4", "to_mark": null,
          "from_parent_mark": "4", "to_parent_mark": null, "to_rounding": "",
          "distance_nm": 1.344, "bearing_deg": 6.2, "finish": true
        }
      ],
      "finish": {"lat": 52.88059, "lon": -4.4001742, "description": "Middle of the finish line"}
    }
  },
  "marks": {
    "4": {
      "code": "4", "display": "4", "name": "Mark 4",
      "lat": 52.8583333333, "lon": -4.4041666667,
      "lat_text": "52° 51.500'N", "lon_text": "04° 24.250'W",
      "buoy": "Large Barrel", "top_mark": "Yellow Spherical"
    }
  }
}
```

In full, `sailed.legs` is `O→1`, `1→4`, then `4→Finish`: 0.211 + 1.201 + 1.344 = 2.76 nm.

### Using it from a display

1. **Poll `/public/race/state`**, which is cheap, and fetch this endpoint only when its
   `signature` changes. The signature moves when the course is set, changed or shortened, when AP
   goes up or comes down, when the start time changes, and when the current race moves on
   (`race_id` changes). It also moves on every finish, which does no harm.
2. **Import nothing while `race.course_set` is false**; show the countdown to
   `race.first_start_time` instead, or "no start time" while it is `""`.
3. **Hold the countdown while `race.postponed` is true**, and show `postponement_flag`.
4. **Draw `course.sailed.expanded_marks` joined by `course.sailed.legs`.** Bend at a point with
   `waypoint: true` without marking it; colour a rounding by its `rounding`; draw the leg with
   `finish: true` as the run to the finish.
5. Use `race.race_finished` to know when to stop. Do not read `status` alone for whether the
   race has started: it says `Racing` for boats entered to a race with no start time — compare
   `first_start_time` with the clock.

`/public/race/state` answers `404` with `ok: false` when there is no race, like this endpoint. Its
state fields include `course_set`, `postponed_flag`, `postponement_ends_at` and
`shortened_at_index`, `shortened_at_mark`, `shortened_at_time` beside the race's entries.

## Other public feeds and access

`/public/race/<race_id>/positions` returns the live GPS positions and position-on-the-water order for a race (used by the public race page). It is read-only, returns nothing while GPS tracking is disabled, and only serves races the public may view.

Everything in this document is read-only and carries the same kind of race, course and mark information competitors can already see. The race office's own APIs stay behind the `/admin` login.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
