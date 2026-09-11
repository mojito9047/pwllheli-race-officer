# Build & documentation scripts

Version-controlled tooling for building the release ZIP, the PDF manuals, and the
screenshots the manuals embed — plus the harnesses that simulate a fleet and measure
what the app makes of it. All paths are relative to the repo, so these run from a
fresh clone. Run them from the repo root, e.g. `python scripts/build_release_zip.py`.

## Requirements

- **PDF builders:** `reportlab` and `Pillow` (global Python).
- **Screenshot capture:** `playwright` + Chromium (installed in `.venv`), and a
  **running app instance** to capture from.
- **PyMuPDF** (`fitz`) is handy for verifying the built PDFs.

## The scripts at a glance

### Build

| Script | Purpose |
|--------|---------|
| `build_release_zip.py` | Package the app into the distributable ZIP. |
| `build_reference_manual.py` | Build the full Reference Manual PDF. |
| `build_manual.py` | Build the Race Officer's Guide PDF. |
| `build_competitor_guide.py` | Build the Competitor's Guide PDF. |
| `build_relay_guide.py` | Build the Relay & Front Door Guide PDF. No screenshots — its architecture diagram is drawn with reportlab shapes in the script. Mirrors `deploy/live_stream/README.md`; keep the two in step. |

### Capture — see [Capturing screenshots](#capturing-screenshots)

| Script | Purpose |
|--------|---------|
| `capture_screens.py` | Dashboard, series, race-sheet tabs (incl. Shorten course), boats, publish → `screenshots/`. |
| `capture_reference_screens.py` | Settings sections, boats, marks, backup → `ref_screens/`. |
| `capture_track_screens.py` | GPS-tracking figures, incl. the Course & start tab → `screenshots/`. Also needs `RO_CAP_RACE_ID`. |
| `capture_v253_screens.py` | Dashboard, tracker-battery warning and activity log → `screenshots/`. Two sources: `RO_CAP_BASE` for the dashboard, `RO_CAP_DEMO_BASE` for the two that must not show real data. |
| `capture_video_splits.py` | The `s_video_1/2/3` crops of the video settings section → `ref_screens/`. |
| `capture_crops.py` | The `crop_*` figures and `marks_top.png`, cut from the element each one is a picture of. These used to be cropped by hand in Pillow, so re-cutting them meant pixel offsets nobody had written down — and once a page grew, the old offsets framed the wrong thing. Note `crop_classes` is the **discards** section and `crop_classes2` the rating bands, which is the other way round from how they read. |
| `capture_competitor_screens.py` | Public competitor pages → `competitor_screens/`. |
| `capture_pursuit_screens.py` | Pursuit race/console/public screenshots. |
| `capture_power_screens.py` | Hut-power dashboard card / history / settings. |
| `capture_entries_tab.py` | The entries tab screenshot. |
| `capture_onwater_screens.py` | The `/onwater` page → `ref_screens/`. Its own script because the figure needs a conversation in it to be worth looking at, and the page is behind a per-user permission no role grants: the script grants **On the water** to `admin`, captures at phone width, and takes it away again in a `finally`, so a screenshot cannot quietly leave an account able to start races from a phone. The commands are sent through the built-in grammar deliberately, so the figure does not depend on a provider account. |

### Simulate & verify

| Script | Purpose |
|--------|---------|
| `simulate_trackers.py` | Feed Traccar simulated boats sailing a real course, to exercise the GPS-tracking + auto-finish chain end to end. See [Simulating a fleet](#simulating-a-fleet). |
| `rerun_race.py` | Replay a race that has already been sailed, as if it were happening now. See [Re-running a race](#re-running-a-race). |
| `verify_replay.py` | Drive the race replay viewer in a real browser and check playback. See [Verifying the replay viewer](#verifying-the-replay-viewer). |
| `verify_leaderboard.py` | Measure how good the predicted leaderboard is. See [Measuring the predicted leaderboard](#measuring-the-predicted-leaderboard). |
| `compare_rounding_tests.py` | Compare ways of deciding a boat has rounded a mark, over recorded tracks. See [Re-examining how a rounding is decided](#re-examining-how-a-rounding-is-decided). |
| `tracker_latency.py` | How late each tracker's fixes reach Traccar, and whether a change to one of them helped. See [Measuring tracker delay](#measuring-tracker-delay). |

### 3D replay

| Script | Purpose |
|--------|---------|
| `replay3d/export_race.py` | Write one sailed race as a JSON scene file in local metres: marks as they stood, course as sailed, finish line, wind, resampled tracks, optional terrain grid, imagery and hut-camera clips. Reads the hut's own backup with `--backup`. |
| `replay3d/build_scene.py` | Runs inside Blender: builds sea, land, buoys, course, animated yachts with spinnakers, a cut shot list and the app-styled overlay from that JSON. |
| `replay3d/fetch_mapbox.py` | Fetch and cache Mapbox satellite tiles for the bay as a lat/lon GeoTIFF (1.44 m/px at zoom 15). |
| `replay3d/fetch_sentinel2.py` | The free alternative: a cloud-free Sentinel-2 window at 10 m. |
| `replay3d/render_parallel.py` | Render a film across several background Blenders and join the parts. See [replay3d/README.md](replay3d/README.md). |
| `replay3d/renderer.py` | The render machine's loop: claim a job from the bucket, fetch what it needs, render, upload, report. **This folder ships in the release**, unlike the rest of `scripts/`, because a render machine is built by unzipping one. |

## Release pipeline (in order)

1. Bump `VERSION`, update `docs/CHANGELOG.md` and `README.md`. Run the test suite
   (`python -m pytest`) — the release helper does not, and more than one of these
   releases was held back by something it caught.
2. (If screenshots changed) re-capture them — see below.
3. Rebuild the PDFs:
   ```
   python scripts/build_reference_manual.py
   python scripts/build_manual.py
   python scripts/build_competitor_guide.py
   python scripts/build_relay_guide.py
   ```
   Output goes to `docs/*.pdf`. The `Covers app version` line on each cover now
   reads `VERSION`, so a rebuild stamps it correctly with nothing to remember. It
   used to be a literal, and sat at 0.164 for 29 releases.

   That makes the cover a *claim*, not a check: rebuilding restamps the version
   but the prose is hand-written in the builders, so **write the new features in
   before you rebuild** or the cover asserts coverage the content hasn't got. The
   embedded screenshots are checked-in PNGs under `scripts/screenshots/`,
   `scripts/ref_screens/` and `scripts/competitor_screens/`, and are just
   as stale-able — re-capture the pages a release changed (see the capture script's
   `RO_CAP_BASE`, which needs the app running).
4. Build the release ZIP:
   ```
   python scripts/build_release_zip.py
   ```
   Produces `pwllheli_race_officer_v<version>.zip` in the repo root. It excludes `.git`, `.venv`,
   `runtime`, `__pycache__`, the three SQLite databases, saved video clips
   and previously built ZIPs — and, by relative path, the two folders of **vendor
   manuals**: `docs/hardware` (start-hut hardware, ~84 MB) and `docs/Trackers` (tracker
   protocol and command manuals, ~47 MB). Both are reference material for installing
   kit rather than anything needed to run the app, and together they would be six times
   the size of everything else over the hut's 4G link. `data/dem` goes the same way:
   hand-pulled GeoTIFF scratch from the 3D replay work, untracked, and 30 MB on the
   machine that fetched it. Check what comes out: **~17 MB is right** (most of it the four PDFs), ~150 MB means an
   exclusion has stopped matching.

   All of `scripts/` is excluded **except `scripts/replay3d`**, which is the 3D replay
   renderer: a render machine is built by unzipping a release like everything else at
   this club, not by cloning the repository. See
   [`deploy/render_machine/README.md`](../deploy/render_machine/README.md).
5. Commit and push, then publish with **`.\release.ps1`** — in the repo root, not one
   level up. It reads `VERSION`, extracts that version's section from
   `docs/CHANGELOG.md` as the release notes, creates and pushes the annotated `vX.Y`
   tag, and creates the GitHub release with the ZIP attached. Safe to re-run after a
   part-finished release: an existing tag is reused, and an existing release has its
   notes and ZIP updated in place rather than failing. `-Version` overrides the version
   and `-Draft` holds it back.

   To cut **several stacked versions** at once, tag each at the *last* commit carrying
   that `VERSION`, so a tag is a whole release rather than a half-finished one, and
   build each ZIP in a throwaway `git worktree` at its tag. That way the working tree
   and anything running against it are never disturbed, and each version's ZIP is built
   by the builder it actually shipped with.

## Capturing screenshots

The `screenshots/`, `ref_screens/`, and `competitor_screens/` folders hold the
committed PNGs the PDF builders embed. The `crop_*` figures and `marks_top.png`
were once cut by hand; `capture_crops.py` now takes each from the element it is a
picture of, so run it alongside the others.

**Take the club's real trackers out first.** The dev database holds four real
15-digit IMEIs, and these PDFs are published. Deleting the tracker rows app-side
is *not* enough on its own — it moves the same devices into *Trackers seen on the
network*, which is read live from Traccar, so the page ends up listing more IMEIs
than before. Clear `traccar_base_url` and `traccar_token` for the capture as well,
and put both back afterwards. The Trackers-page capture asserts no 15-digit id is
visible before it saves, which is the check that should have existed all along:
the version committed before v0.263 mapped six real IMEIs to named club boats.

The capture scripts drive Playwright against a running app. Start a **temporary
instance** (do not disturb a live one) with security cookies relaxed so the
headless browser can sign in over http:

```
RO_PORT=5058 RO_HOST=127.0.0.1 RO_COOKIE_SECURE=0 RO_POWER_SIM=1 python app.py
```

Then point the capture scripts at it. Credentials come from the environment (never
hard-code them):

```
RO_CAP_BASE=http://127.0.0.1:5058 RO_CAP_PASSWORD=<admin password> \
  .venv/Scripts/python.exe scripts/capture_screens.py
```

`RO_CAP_BASE` defaults to `http://localhost:5050`. `RO_CAP_PASSWORD` falls back to
`runtime/initial_admin_password.txt` if unset. The scripts capture from series 1 /
race 1 in the current database.

Run **every** capture script with `RO_CAP_BASE` set. Three of them
(`capture_power_screens`, `capture_entries_tab`, `capture_pursuit_screens`) used to
hard-code a port and silently ignored it — two therefore drove the *live* app on
5050, creating and deleting a race in the real database. Fixed in v0.194; they all
honour `RO_CAP_BASE` now.

`capture_track_screens.py` also needs **`RO_CAP_RACE_ID`** — the race whose Course &
start tab is captured — or it skips `race_track_map.png` and
`race_track_leaderboard.png` with a message. Pick a race whose entries include tracked
boats that are actually moving, otherwise the figures show an empty course; those two
images illustrate the feature best when captured during a race (or from a database
being fed by `simulate_trackers.py`).

### Figures that must not come from the race-office PC

`capture_v253_screens.py` reads **two** bases, and the split is deliberate. The
dashboard is taken from `RO_CAP_BASE` — the real machine, because the hardware
cards are only worth photographing with real hardware behind them. The
tracker-battery warning and the activity log come from `RO_CAP_DEMO_BASE`, a
throwaway instance, for two different reasons:

* The real activity log names the club's trackers by their **IMEI**, and these
  PDFs are published. The demo log names `SIM-1`.
* The battery warning only renders when a tracker assigned to a boat is flat.
  Waiting for that is not a plan.

A demo instance is a `git worktree` of the current commit (so it has its own
`data/` and `runtime/` — `DATA_DIR` is fixed relative to the repo root and cannot
be pointed elsewhere) with a handful of boats, five trackers and one race seeded
into it, `track_ingest_secret` set in `hardware_settings`, and a track written
straight in by `insert_backfilled_positions` so the fleet is mid-race the moment
the browser opens. Beware two traps that cost time the first time round:

* `search_boats` filters on `status = 'ACTIVE'`, **upper case**. Seed lower-case
  and every Boat dropdown silently reads "unassigned" while the map still labels
  the boats correctly.
* Back-fill from `race_first_start_dt`, not from `races.start_time`. The class
  offset means the gun can be minutes later, and fixes before it are ignored —
  which reads as a boat that has rounded nothing, only for the *fastest* boat,
  which looks exactly like a bug in the rounding rule.

Log lines for the activity-log figure are made by performing the actions, not by
writing them into the file. Delete `runtime/logs/activity.log` first (stop the app
— Windows holds it open) or the fresh instance's first settings save, which
diffs every key from blank, fills the figure.

## Simulating a fleet

`simulate_trackers.py` feeds **Traccar** simulated boats sailing a real course (OsmAnd
protocol) to test the GPS-tracking + auto-finish chain end to end. Run on the relay
(`--url http://localhost:5055`) or the LAN; `--course N`, `--devices`, `--speed`,
`--once`, `--loop`. Add the device unique ids in Traccar first. See its docstring.

`--battery PCT` reports a battery level with each fix, the fleet spread down from
`PCT` so five devices land in all three bands — it needs `--forward-to`, because
the OsmAnd endpoint has nowhere to carry attributes. Without it nothing in the repo
can exercise the Battery column or the low-battery warning end to end.

### Which line the boats start and finish across (`--finish-line`)

The club sails to more than one line, and the boats have to use the **same one as the
race** or no finish is ever detected. `--finish-line` takes a key from
`data/start_finish.json`; it defaults to the line marked default there (`psc`), and the
startup banner prints which one is in use.

| key | line |
|-----|------|
| `psc` (default) | ODM (mark **O**) to the CHPSC bridge window |
| `isora_plas_heli` | Pwllheli Fairway Buoy (mark **F**) to Plas Heli |

```bash
python scripts/simulate_trackers.py --course 4 --finish-line isora_plas_heli ...
```

The two lines' seaward ends are **1172 m apart**, so this is not a detail. Before the
option existed the simulator always sailed to the club line: on course 1 the simulated
path crossed the ISORA line *zero* times, so an ISORA race could never be finished
however long it ran — and 26 of the courses include mark F.

Adding it turned up two older faults in the same geometry, both now fixed:

- **The "perpendicular" leave-and-return was not perpendicular.** It was built by
  reflecting the line's direction rather than rotating it, which is only perpendicular
  for a line running at 45°. On the club line it came out at **38°**, and mark O — an
  *end* of that line — measured as 137 m off it.
- **The finishing pass was aimed by the centroid of the course marks.** Since v0.247 the
  app judges a finish as a crossing from the side the *last mark* is on, and on a course
  whose marks straddle the line those are opposite — 8 of the club's 12 wind directions.
  The boats therefore crossed the wrong way and were never finished. The start is aimed
  by the first mark and the finish by the last, and a mark sitting *on* the line (usually
  the ODM) is stepped over, exactly as `core.track.finish_direction_point` does.

Together those took the simulator from finishing **9 of 67** bundled courses on the club
line to **67 of 67**, and 67 of 67 on the ISORA line.

### Where the boats wait before the start (`--prestart`)

**275 m off the line by default**, on the side away from the course, which is about a
minute at 8 kn. Raise it with `--prestart METRES` to give yourself longer between
starting the simulator and the gun.

Correcting the perpendicular made this worth stating, because it changed what the old
number meant: `450` used to be measured along an axis 38° off the perpendicular, so it
only ever put the boats ~277 m from the club line. Once the axis was a true perpendicular
the same 450 started them half again as far out — reported from the hut as "the boats
start a long way from the start line" — hence the smaller default that reproduces the
distance people were used to.

One cosmetic limitation: the waiting position is a perpendicular offset on the side away
from the course marks, and the club line is short (347 m). On 17 of the 67 courses — the
ones whose marks are dominated by the Fairway Buoy, north-east of that line's extension —
"away from the course" points seaward, so the boats wait out to sea rather than in the
harbour. The start crossing itself is correct on all 67; only the look of the first minute
on the chart is wrong. Putting them in the channel every time would need a harbour
approach that is not in the data.

> **Set the race's gun *before* the boats start sailing.** Course progress is measured only
> from fixes after the start signal, and it is sequential — so if the boats round mark 1
> before the gun, that rounding never counts and every mark after it stalls too. Put the
> warning signal ~4 minutes in the past (gun ~1 minute ahead) and start the simulator
> immediately: the boats leave the harbour now and cross the line just after the gun.

> **Watch the effective fix spacing, not the `--interval`.** The stored track is sampled by
> the app's poll (default 5 s), not by the simulator's send rate, so the real spacing is
> `speed x poll interval`. At 30 kn that is ~77 m against a 50 m rounding radius. 20 kn is a
> good demo speed: ~51 m, a 15-minute lap, and roundings that land reliably.

> **Only ever run one `simulate_trackers.py` at a time per set of device ids.**
> Two instances feeding the same ids produce a track that jumps back and forth by
> hundreds of metres: Traccar keeps only the latest fix per device, so the app's poll
> picks up whichever instance wrote last, and the stored track alternates between two
> boats at different points on the course. It looks exactly like a broken replay.
> Beware that `ps` under Git Bash does **not** reliably list Windows processes, so an
> instance you think died may still be running — check with PowerShell:
> `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select CommandLine`.
> `scripts/verify_replay.py` now reports this as bad *data* rather than a viewer fault.

### Sailing the course properly (`--sail`)

Without it the boats motor the rhumb line at a fixed speed: they sail dead upwind,
never tack, and cover exactly the straight-line distance. That is fine for exercising
ingestion, the map and finish detection, but it cannot test the **predicted
leaderboard**, whose whole job is to reason about how long the remaining legs will take.

With `--sail` a boat obeys its polar. It cannot point closer than the polar's minimum
TWA, so a windward leg is beaten in tacks; it will not sail dead downwind either, so a
run is gybed. Speed comes from the polar at the angle actually being sailed, and every
tack or gybe costs a few seconds of it.

```bash
python scripts/simulate_trackers.py --sail --course 1 --twd 20 --tws 12   --devices SIM-1,SIM-2,SIM-3 --polar-pct 100,94,88 --forward-to http://HUT:5050/api/track/ingest --token SECRET
```

Three things decide whether the run tells you anything:

- **Set `--twd` to the app's hut wind.** The app builds its leg model from the weather
  station, so sailing the boats in a different wind asks the estimator a different
  question. The startup banner prints the wind it is using and warns about this.
- **Check the leg summary it prints.** A course that happens to be all reaching at the
  chosen wind never exercises the tacking at all — Pwllheli's course 1 at the usual 298°
  is exactly that. `--twd 20` gives it four beats and two runs.
- **`--polar-pct` is the point.** Boats at 100/94/88% of the same polar are what the
  estimator has to sort out, because the app models the whole fleet with one polar and
  cannot know one boat is having a bad day.

A race takes as long as the polar says — 45 to 60 minutes. That is deliberate: the
estimator withholds anything for the first ten minutes and averages VMC over twenty, so a
compressed race would not exercise either window honestly. To iterate on the estimator
itself use `verify_leaderboard.py`, which sails the same fleet offline in a second.

## Re-running a race

`rerun_race.py` runs a race that has already been sailed **again, as if it were
happening now** — a new race starting in a moment, fed its own recorded fixes on the
clock. For testing the clubhouse display (`/bar`), the start and finish camera windows,
and anything else that only comes alive during a race. `--list`, `--source ID`,
`--speed N` (scales reported speed too, so distance-over-speed still means what it
says), `--purge`.

## Verifying the replay viewer

`verify_replay.py` drives the **race replay viewer** in a real browser (Playwright) and
checks playback: the clock advances at the selected speed, and no boat teleports. Needed
because playback runs on `requestAnimationFrame`, which browsers do **not** fire while a
page is hidden — so a headless load proves nothing about playing. Exits non-zero on
failure.

```bash
RO_CAP_BASE=... python scripts/verify_replay.py --race N [--from 0.55] [--headed]
```

## Measuring the predicted leaderboard

`verify_leaderboard.py` measures how good the **predicted leaderboard** is. It sails the
fleet offline in a second, writes the track to the database as a race that has been run,
then asks the app's own estimator what it would have said at every moment against the
finish each boat actually achieved. Reports projected-time error by stage of the race and
how often the corrected *order* was right. `--keep` leaves the race to scrub through in
the browser.

Its device ids carry the run's own timestamp (`VER-1785948051-1`), the way the race name
already does. They used to be a plain `VER-1..n`, and the second run then died on
`UNIQUE constraint failed: trackers.unique_id`: a run left with `--keep`, or one that died
before its cleanup, still had those ids registered. Clearing them at the start would have
been worse than the bug — a kept race resolves its boats' trackers through exactly those
rows, so tidying up for a new run would quietly strip the tracks off the race somebody had
asked to keep.

## Re-examining how a rounding is decided

`compare_rounding_tests.py` compares ways of deciding a boat has **rounded a mark**, over
recorded tracks. Read-only: it walks stored fixes and prints tables. This is the harness
that chose the v0.247 rule on 8 boat-races (5 of them simulator tracks) — **re-run it once
there is a season of real racing behind it**. Six parts: agreement with race-officer finish
times; a boat pushed radially clear of a mark; every Nth fix kept; whether a mark is counted
at the same moment as before; each track walked against courses it never sailed; and a check
that the harness still matches the shipped walk. `--part B --part C`, `--sweep`, `--depart`,
`--neighbourhood`.

Until v0.247 a mark counted as rounded on proximity alone. That asks how *tidily* a
boat rounded, when what the app needs to know is whether it got round and set off on
the next leg — and a boat given a wide berth stayed outside any sane radius the whole
way past, stalling its progress for the rest of the race and blocking its GPS finish.

The rule that replaced it was picked by measurement, and this script is the
measurement. Keep two things in mind when reading it:

- **The evidence was thin.** Eight boat-races had both stored fixes and a
  race-officer-recorded finish time, five of them simulator tracks. The script says so
  when it runs. Parts B and C — controlled perturbations of tracks that work — carried
  more weight than part A, which is only a floor.
- **The perturbation flatters the radius.** Pushing fixes radially outward can leave a
  straight-line interpolation between two of them that cuts nearer the mark than the
  boat ever went, so the radius column at large offsets is optimistic.

Part F exists because `walk()` in the script is a deliberate *copy* of
`core.track.boat_course_progress` — so the mark test can be swapped without the script
being able to change the app. Copies drift; F runs the shipped function over the same
cases and fails loudly if they disagree. Run it before trusting anything else.

The `plane` detector is a **rejected** candidate, kept because the negative result is
the useful part: a turn-gate at each mark was the obvious first idea and measured worse
than what it replaced, finding none of the recorded finishes. A plane is infinite, so
its before/after sides are global rather than local to the leg being sailed — on a real
track the boat began on the far side of the gate and made its one outbound crossing
851 m from the mark.

---

## Measuring tracker delay

```bash
python scripts/tracker_latency.py                        # last 12h, every device
python scripts/tracker_latency.py --daily --days 10      # a row per device per day
python scripts/tracker_latency.py --compare <imei> --control <imei> --flashed 2026-08-18
```

Traccar stamps every position with `fixTime` and `serverTime`, and keeps about ten
days of them. The difference is the tracker's own uplink delay with the app taken out
of the question — which matters, because the app polls intermittently and its stored
`server_time` carries gaps that are the app's, not the tracker's.

A GL-series unit batches its fixes and lands them ~45 s later, every time, in normal
health; the RUTX50 pushes as it goes and arrives in under a second. Neither is a fault,
so the median is not the number to watch. The **tail** is: the share of fixes arriving
more than a minute late, because that is what delays a GPS finish being *detected*.
The finish time itself is interpolated from fix timestamps and stays right — it is the
proposal and the horn that wait.

`--compare` answers "did that change help?" and is built to resist the two ways of
getting that wrong. It prints the full baseline rather than a single before/after pair,
because the same tracker has run anywhere between 0% and 49% late across ten
consecutive nights with nothing altered on it — one night's comparison is inside the
noise and means nothing. And it skips the whole day of the change, because a unit that
has just rebooted spends its first half hour re-acquiring and catching up, and counting
those fixes attributes the reboot to the change.

Always keep a `--control`: a comparable device that did not change. When two independent
trackers degrade on the same night the cause is upstream of both, and without a control
in the table that reads as a fault in the one you happened to be looking at.
