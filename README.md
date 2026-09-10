# Pwllheli Race Officer v1.001

A human-supervised race-management app for Pwllheli Sailing Club racing. It helps the race officer prepare races, recommend or build courses, manage entries, run configurable RRS 26-style start sequences, log horn events, record finish times, calculate IRC/YTC race results, score race series, publish competitor information pages and keep video evidence of starts and finishes.

Race-office support software. It does not decide anything: official race decisions remain with the race officer and race committee, and results are provisional until they confirm them.


## Recent releases

### v1.001 update

- **The app can be reached around the relay no longer.** `hut-origin`, the hostname the relay
  proxies to, was public and served the whole app including the login page, so it bypassed the
  relay and every rule scoped to the club's address. Caddy now presents a Cloudflare Access
  service token, ready for a policy on that hostname to refuse everyone else.
- **Settings &rarr; Web server &rarr; Public address.** Behind the tunnel the app only saw the
  tunnel's own hostname, so the links it handed out &mdash; the competitor share links and the
  logo addresses the live stream reads &mdash; pointed at an internal name over plain http.
  Set the club's address here and every external link follows it. Empty keeps today's behaviour,
  which is what the hut network wants.

### v1.000 update

- **The MVP label comes off.** A season of the club's racing has run through it. What has not changed is that the race officer decides and the app records: results stay provisional until confirmed, and the caution in the sidebar stays.
- **A release unpacks into `pwllheli_race_officer_v1_001`.** An existing install keeps working where it is. This also fixes a latent bug &mdash; the ZIP builder hardcoded `v0_`, so 1.000 would have produced a folder named `pwllheli_ro_mvp_v0_000`.
- **Every screenshot in the guides re-shot** against 1.000.

### v0.285 update

- **A finish taken on the horn switch is a finish video.** The competitor page showed **View** for three boats and the published document said **No Video** for the same three: finishing on the physical horn leaves a `manual_horn` clip, and the published builder only looked for `finish` ones. On the club's database that was 10 finishes of 26 silently dropped. Both pages now use one rule &mdash; a clip attached to a boat is that boat's video &mdash; with a test holding them together.
- **The published document only carries links that work away from the hut.** A clip not yet published to R2 used to get an address on the hut PC (43 of them in one measured document), dead for anyone reading the results on the club website. It now says *No Video* instead, and **Preview HTML** shows the same, so the preview tells the truth about what will be published.

### v0.284 update

- **The boat's Fleet / class field retires** &mdash; the last of three fields answering the same question and disagreeing. Nothing but that form ever wrote it, so *Add all &lt;fleet&gt; boats* and the Virtual Race Officer's *"enter the IRC 1 fleet"* go with it; *"add the fleet"* now means every active boat. Entry classes already recorded are untouched.
- **The IRC and YTC listings download while you type.** Both start when the Add/Edit boat page opens, and the search joins that download instead of starting a second one: 0.13 s instead of 3.24 s once you have typed a boat name.
- **A finished race stops walking fixes recorded after it finished.** One sailed in July was reading 98,362 of them, six weeks of later tracking, and gaining a day's worth a day &mdash; 518 ms to draw, now 15 ms. A race still being sailed is still followed to now, with room for a passage race.
- **A camera that is not answering no longer costs every request 286 ms**, FFmpeg is killed when the app closes, and the recorder log is bounded at 8 MB instead of the 65 MB it had reached.

### v0.283 update

- **A boat with no tracker on it is not out on the water.** In a race with no trackers assigned, boats were listed at 96.73 nm to go with a last fix twenty-seven days old &mdash; the display position reached outside the race window with no bound, so any boat that ever carried a tracker sat at wherever it last was, for ever. That reach is now bounded by an hour, the app's own definition of a tracker that is not reporting.
- **A boat with nothing to report says which kind of nothing**: **Not reporting** when a tracker is on the boat and nothing has been heard from it, **No tracker** when there is none on it today &mdash; instead of a row showing *0/7 marks, next 1* beside an empty Fix.
- **A course nobody set is not a course to sail round.** A race reading *Course: not set yet* listed a boat at *0/7 marks, next 1, 5.05 nm to go* &mdash; measured against course 1, the fallback the chart uses when no course has been chosen. The fleet list, the replay and GPS finish detection all walked that guess; none of them do now.
- **The published results page fits a phone.** It overflowed sideways by 438px and gave the banner 30% of the screen; the results table now scrolls inside its own box and the banner shrinks. Zero horizontal overflow at 1440, 820 and 390.
- **The footer no longer tells you to upload the file to the club website**, which the app has done itself since v0.281.

## Main features

**Courses and starts**

- Stores the 2026 CHPSC fixed courses, racing marks and start/finish definitions.
- Recommends a course from live start-hut wind, the selected boat polar, sail chart and a target race duration; or build a made-up course by hand from marks. Compound marks such as `Y` and `A` stay compact on the course board but expand into their physical corners for charting and leg analysis.
- Draws the course chart with the CHPSC Bridge-to-ODM start/finish line and a live wind overlay.
- Stores the **first warning signal**; the first start is warning + 5 minutes. Runs RRS 26-style warning, preparatory, one-minute and start signals for up to six planned starts, with rating-band classes assigned graphically to each.
- Displays the numeral class pennants and P flag that are currently up, in both the race-office and public flag panels.
- **Shortens the course** at a mark mid-race: Code flag S shows, the chart redraws to end there, and GPS progress and finishes follow the shortened course.
- Runs **pursuit races**, with per-boat start times derived from the rating system and a finishing-order result.

**Boats, results and series**

- Boat database with IRC and YTC ratings, looked up from the configured RORC/TopYacht and YTC sources.
- Ratings are **snapshotted onto race entries** when a boat is added, and are editable per race. Boat-database changes never rewrite an existing entry.
- IRC and YTC results split by rating-band class, series scoring with a per-series discard profile and Appendix A tie-breaks, and entries kept synchronised across the races in a series.
- Exports a standalone Sailwave-style HTML file for the club website.

**On the water**

- **GPS tracking** of the fleet from Queclink trackers via a Traccar relay — the boats on the course chart, a *Position on the water* list, and finish-line crossings detected to propose or auto-confirm finishes. A phone running Traccar Client works as a tracker too. See [`docs/TRACKING.md`](docs/TRACKING.md).
- Records **start and finish video** from a USB camera or RTSP stream with FFmpeg, publishes branded web copies to Cloudflare R2, and can draw the real start line on them.
- **Re-measures a mark that has drifted** — marks can be edited, or set from a phone taken out in a RIB. It matters because everything measured from a mark is measured from the position on file: distance to go, the leg bearings, the chart, and in the end whether a boat is seen to round it. Each race keeps the mark positions it was sailed with, so a correction today does not redraw races already run.
- Reads the start-hut **weather station** for live wind and a wind history plot.
- The dashboard carries a **map of the marks and any tracker reporting in the last hour**, so "is anything on the water" is one glance rather than a page change.
- Monitors **hut power** (Victron VE.Direct) as a separate time-series store.

**What everyone else sees**

- Public **competitor pages** — one responsive page for phones, tablets and PCs. Its **Chart** tab shows the fleet live, winds back through the race so far and plays it at up to 60x, with a translucent leaderboard rolling up over it.
- That leaderboard offers the order on the water or an **estimated IRC/YTC corrected order**, projected from its average pace since the start (the default), its pace over the last twenty minutes, or its pace against its polar. Whichever is chosen drives the order *and* the times beside it. It is labelled an estimate, not a result.
- A **clubhouse display** at `/bar` for a television in the club bar: the chart zoomed to the boats still racing, the leaderboards cycling beside it, and the start-hut camera at the start, at each rounding of the ODM and at each finish. No controls — open it once and leave it. See [`docs/BAR_DISPLAY.md`](docs/BAR_DISPLAY.md).
- An optional **live camera** view, served on demand through the relay.

**Running it**

- **Backup and restore** of the database, settings, courses and marks, polars, branding, GPS tracks, hut power history and optionally the video evidence.
- A nightly **off-site backup**: the same ZIP, AES-256 encrypted and pushed to a private Cloudflare R2 bucket, held back while racing or while videos are uploading, verified after upload, with its age on the dashboard. See [`docs/DATA_AND_BACKUP.md`](docs/DATA_AND_BACKUP.md).
- The bundled markdown documentation **readable in the app**, on the Documentation page, alongside the PDF guides.
- A daily-rotating **activity log** of who did what, and a race event log.
- Users and roles (admin / race officer / mark layer), with self-service password change.

## What runs where: the hut PC, the relay and Cloudflare

The app itself is one Windows PC in the start hut, and on a club LAN that is all you
need. But four of the things above reach beyond that PC — competitors on their phones,
the live camera, GPS tracking and published video — and those go through a second
machine the club owns: **the relay**, a small Debian VPS.

```text
  competitors on phones, the bar TV      the boats' trackers
                 |                                |
                 |  https, through Cloudflare     |  raw TCP, one
                 |  (TLS, and it caches video)    |  port per protocol
                 v                                v
  +-----------------------------------------------------------+
  | THE RELAY   a Debian VPS - the club's single front door   |
  |                                                           |
  |   Caddy      pro.pwllhelisailingclub.org  ->  the hut app |
  |   MediaMTX   /live - one camera stream, served on demand  |
  |   Traccar    5004 Queclink | 5027 Teltonika | 5055 phone  |
  +-----------------------------------------------------------+
                 |  outbound only, in both directions
                 v
     THE HUT PC (Windows) - the app, behind a cloudflared tunnel
                 |
                 +-- the hut camera, on the hut LAN

     Cloudflare R2 - published race videos and the public live JPEG
```

What the relay is for:

- **The public front door.** `pro.pwllhelisailingclub.org` reaches the hut app through
  the relay and a Cloudflare Tunnel, and shows a holding page when the hut is offline.
- **The live camera.** The hut sends **one** stream, only while somebody is watching, and
  Cloudflare fans it out to every viewer. Club branding is burned in on the relay from the
  app's own branding manifest, so changing a sponsor logo needs no relay edit.
- **GPS tracking.** The boats' trackers report to **Traccar** on the relay. The hut app
  both pulls positions from it and is pushed each fix as it arrives.
- Published race video goes to **Cloudflare R2** rather than out of the hut on every view.

**The hut PC opens nothing inbound.** Every connection it makes is outbound, which is what
lets it sit behind an ordinary 4G router. The only inbound access anywhere in the system is
the tracker ports on the relay.

**Without a relay** the app still runs: races, results, series, video recording, horn and
weather all work on the hut PC and its LAN. What you lose is the public site, the live
camera and GPS tracking — the three things that need to be reachable from outside.

One trade-off worth knowing: because the relay is the single front door, if the *relay* is
down then `pro.` is unreachable altogether. The holding page only covers the *hut* being
down.

Setting one up is [`docs/Pwllheli_Relay_Guide.pdf`](docs/Pwllheli_Relay_Guide.pdf), with the
working copy of the procedures and config beside the files themselves in
[`deploy/live_stream/README.md`](deploy/live_stream/README.md).

## Quick start

```bash
cd pwllheli_race_officer
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

`python app.py` starts the app with Waitress on port 5050.

Open:

```text
http://localhost:5050/admin
```

The root page `http://localhost:5050` is the public read-only current-race page.

First-run login:

```text
Username: admin
Password: see runtime/initial_admin_password.txt on the race-office PC
```

You can set the first password yourself before the database is first created:

```powershell
$env:RO_INITIAL_ADMIN_PASSWORD="your-password"
python app.py
```

If neither `RO_INITIAL_ADMIN_PASSWORD` nor an existing database user is present, the app generates a random first-run password locally and writes it to `runtime/initial_admin_password.txt`.

## Recommended race-day workflow

1. Open **Settings** and check users, horn, weather, video, rating sources, polars and chart settings.
2. Open **Boats** and make sure active boats and IRC/YTC ratings are correct.
3. Create or select a **Series** if the race belongs to one, then configure rating-band classes and the default start plan.
4. Create a new **Race**. Course and first warning-signal time are set after creation.
5. On the race page, use **Course & start** to set the first warning-signal time and either recommend, select or build a course.
6. Check the race start plan. Series races use the series default unless the race override is enabled.
7. Use **Add entries** to add all active boats, a boat from the database, or a whole fleet/class. In a series, entries are synchronised across all races in that series.
8. Use **Start console & log** to run or supervise the start sequence, horns, recalls and race log.
9. Use **Entries & finish times** to record finishes and edit race-specific IRC/YTC ratings if a race needs to use a different value from the boat database.
10. Use **Results** for provisional IRC/YTC corrected results and video-evidence links.
11. If the wind drops or time runs short, use **Shorten course** to finish the race at a mark.
12. Open the public competitor page as a pop-out display when required, and put `/bar` on the clubhouse television if the club has one.
13. From the series page, use **Publish HTML** / **Download HTML** when you need a static results file for the website.

A printable race-day checklist is in [`docs/OPERATIONAL_CHECKLIST.md`](docs/OPERATIONAL_CHECKLIST.md).

## Documentation

Detailed documentation is in the `docs/` folder:

- [`docs/GETTING_STARTED_WINDOWS.md`](docs/GETTING_STARTED_WINDOWS.md) — step-by-step Windows setup for novice users.
- [`docs/DEPLOYMENT_WINDOWS.md`](docs/DEPLOYMENT_WINDOWS.md) — race-hut PC deployment with Windows Scheduled Task auto-start.
- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — installation, first run and upgrade notes.
- [`docs/OPERATIONAL_CHECKLIST.md`](docs/OPERATIONAL_CHECKLIST.md) — race-day checks before, during and after racing.
- [`docs/RACE_OFFICER_WORKFLOW.md`](docs/RACE_OFFICER_WORKFLOW.md) — normal race-day workflow.
- [`docs/SETTINGS_AND_ADMIN.md`](docs/SETTINGS_AND_ADMIN.md) — settings sections and administration.
- [`docs/CLASSES_AND_STARTS.md`](docs/CLASSES_AND_STARTS.md) — IRC/YTC rating-band classes, start plans and series entry synchronisation.
- [`docs/HARDWARE_HORN_AUDIO.md`](docs/HARDWARE_HORN_AUDIO.md) — serial horn output, manual horn input and VHF audio.
- [`docs/FLAGS_AND_START_SEQUENCE.md`](docs/FLAGS_AND_START_SEQUENCE.md) — dynamic flag model and scheduled signal plan.
- [`docs/VIDEO_RECORDING.md`](docs/VIDEO_RECORDING.md) — FFmpeg, camera setup, live preview and video troubleshooting.
- [`docs/TRACKING.md`](docs/TRACKING.md) — GPS yacht tracking, *Position on the water* and automated finishes.
- [`docs/Pwllheli_Relay_Guide.pdf`](docs/Pwllheli_Relay_Guide.pdf) — **the relay**: the single machine the club is reached through (front door, live camera, Traccar, visitor logging). Working copy of the procedures: [`deploy/live_stream/README.md`](deploy/live_stream/README.md).
- [`docs/WEATHER_STATION.md`](docs/WEATHER_STATION.md) — start-hut weather station setup.
- [`docs/PUBLIC_COMPETITOR_PAGE.md`](docs/PUBLIC_COMPETITOR_PAGE.md) — the public competitor home and race pages.
- [`docs/BAR_DISPLAY.md`](docs/BAR_DISPLAY.md) — the clubhouse TV at `/bar`.
- [`docs/REMOTE_ACCESS_CLOUDFLARE.md`](docs/REMOTE_ACCESS_CLOUDFLARE.md) — publishing the hut app through a Cloudflare Tunnel.
- [`docs/COMPOUND_MARKS.md`](docs/COMPOUND_MARKS.md) — parent marks that expand into physical corner marks for charting and analysis.
- [`docs/WAYPOINTS.md`](docs/WAYPOINTS.md) — turning points that bend a leg round a headland without being marks boats round.
- [`docs/WEBSITE_PUBLISHING.md`](docs/WEBSITE_PUBLISHING.md) — standalone HTML export for club website publishing.
- [`docs/SERIES_SCORING.md`](docs/SERIES_SCORING.md) — race series scoring and discards.
- [`docs/DATA_AND_BACKUP.md`](docs/DATA_AND_BACKUP.md) — database, uploaded files, videos and backup.
- [`docs/DEVELOPER_NOTES.md`](docs/DEVELOPER_NOTES.md) — code structure and extension notes.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — common issues and fixes.
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — version history.

Four printable guides are built from `scripts/build_*.py` and kept in `docs/`:

- [`Pwllheli_Race_Officer_Series_Guide.pdf`](docs/Pwllheli_Race_Officer_Series_Guide.pdf) — the race-day guide, start to finish.
- [`Pwllheli_Race_Officer_Reference_Manual.pdf`](docs/Pwllheli_Race_Officer_Reference_Manual.pdf) — every page and setting.
- [`Pwllheli_Competitor_Guide.pdf`](docs/Pwllheli_Competitor_Guide.pdf) — what competitors see on the public pages.
- [`Pwllheli_Relay_Guide.pdf`](docs/Pwllheli_Relay_Guide.pdf) — the relay: front door, live camera, Traccar.

## Important files and folders

```text
app.py                    Flask app core: setup, auth/CSRF/session, settings form, background tasks
routes/                   The HTTP route handlers, split out of app.py
core/                     Domain and hardware logic: db, scoring, courses, weather, video, tracking, ...
data/                     Database, courses, marks, sail charts, polars, branding and saved video clips
runtime/                 Temporary caches, video buffer, live preview, FFmpeg and activity logs
deploy/                   Race-hut PC deployment scripts, and the relay (Traccar, live stream)
scripts/                  Documentation tooling: PDF builders, screenshot capture, simulators, verifiers
static/                   CSS, JavaScript, images and icons
templates/                Flask/Jinja web templates
tests/                    pytest suite
docs/                     User, operations and technical documentation, and the PDF guides
```

## Main assumptions

- IRC corrected time is `elapsed × IRC TCC`.
- YTC corrected time is `elapsed × 1000 / YTC`.
- The race-entry IRC/YTC snapshot is the authority for results. Boat-database changes do not rewrite existing race entries.
- Server/race-office PC time is used for start/finish timing and video timestamps. The PC should be GPS/NTP-disciplined before racing.
- Live wind is the **wind at the hut**, not necessarily the wind across the whole course.
- Camera/video evidence is advisory and should be reviewed by the race officer.
- The visual flag panel shows the app’s current signal-plan state; recall/postponement/abandonment flag states are still manually managed by the race officer.

## Safety notes

Do not connect horns, horn batteries or horn switches directly to a PC serial port or USB serial adapter. Use a relay, opto-isolated input/output or a properly designed interface.

The public competitor pages are intentionally unauthenticated. The race-officer app is protected by username/password login.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
