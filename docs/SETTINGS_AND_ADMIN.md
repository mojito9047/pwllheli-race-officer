# Settings and administration

Open **Settings** for system configuration. The page is organised into collapsible sections. A **Save settings** bar stays pinned to the top of the page, so you can save from anywhere without scrolling back up. Settings is in the bottom group of the side menu, with the other non-race items (Hut power, Documentation, Trackers, Backup / restore).

## User roles and access

Every login account has one of three roles:

- **Admin** — full access to everything, including Settings and restoring backups.
- **Race officer** — can run races and use everything needed on race day, but **Settings is read-only** and **restoring a backup is not allowed**. A race officer can still download a backup.
- **Mark layer** — reaches the phone page for re-measuring a mark, and nothing else. Signing in lands straight on it, and any other page sends them back to it. It is for whoever takes the RIB out after a storm: that job needs one button on a phone, not the start sequence, the finish times and the results as well. Mark layers may always set marks, so the **Set marks** tickbox is not shown on their rows.

When a race officer opens Settings, the page shows every value but the fields are greyed out and there is no **Save** button — a "Read-only · administrator access required" note appears instead. The restriction is also enforced on the server, so the settings-write actions are refused even if reached directly.

Sign-in is protected against brute-force guessing: after several failed attempts for the same user from the same address, further attempts are blocked for a few minutes ("Too many failed sign-in attempts"). This clears on a successful login or an app restart.

## Users

The main race-officer app requires login. Public competitor pages do not.

In **Settings → Users** (admin only) you can:

- Add users, choosing **Admin**, **Race officer** or **Mark layer** from the role dropdown.
- Change passwords, display names and roles.
- Tick **Set marks** for a race officer who should also be able to re-measure a mark's position from a phone on the water, and (from v0.246) **edit a mark** on the marks page — its name, position, buoy description and its own rounding radius. The permission already lets them move the mark from the water, so requiring an administrator to type the same correction was the wrong line to draw; whoever has just re-laid it is the one who knows. **Adding and deleting** marks remain admin-only, because those change the set of marks every course sequence is written against. It is a permission of its own so that somebody who already runs races does not need a second account to do it. Administrators and mark layers always may, so the box is not shown on their rows.
- Tick **VRO** for a user who may run racing from a boat with the **Virtual Race
  Officer** at `/vro` — create a race, set its start and course, enter boats,
  shorten the course. No role grants it, administrators included: the club has
  settled that a race may start with nobody watching the line, but who may drive
  the racing from a phone is a separate decision and is made one account at a
  time. It is checked by the endpoints as well as the page. See
  [`VIRTUAL_RACE_OFFICER.md`](VIRTUAL_RACE_OFFICER.md).
- Deactivate users.
- Delete users.

At least one active administrator must always remain: the app refuses to demote, deactivate or delete the last admin so you cannot lock everyone out of Settings.

### Changing your own password

Because Settings is read-only for race officers, every signed-in user has a **My account** page for changing their *own* password. Open it from the username shown in the top bar (or go to `/account`). It asks for the current password to confirm the change, enforces a minimum password length, and keeps you signed in. Administrators can still reset anyone's password from **Settings → Users**.

### First-run administrator

The first-run username is `admin` (role **Admin**). The password comes from `RO_INITIAL_ADMIN_PASSWORD` if set before first launch; otherwise the app generates one and writes it to `runtime/initial_admin_password.txt` on the race-office PC. Existing `admin` / `admin` credentials are replaced automatically unless `RO_ALLOW_DEFAULT_ADMIN=1` is explicitly set.

## Horn settings & Test

The horn settings area is split into three subsections so race-day checks are easier to follow.

### Horn output

Configure the serial output that fires the horn:

- Serial port, for example `COM3` or `/dev/ttyUSB0`. Leave it blank for simulation mode.
- Output line. Use `DTR` for the ProLog-style race-office interface; `RTS` is only for legacy/non-ProLog wiring.
- Default blast duration in milliseconds.
- Output polarity, shown as **Assert output line during horn blast**.

Use **Save and test horn output** before racing. The current output summary is shown in the same subsection so the port, line and duration are visible beside the test button.

### Manual horn input sensing

Enable this only when the ProLog-style DTR/RTS/DCD/CTS feedback interface is fitted and tested. The subsection shows the fixed active/idle input wiring and the poll interval. With ProLog-style sensing enabled, DTR fires the horn relay, RTS is held as the feedback common, DCD is idle and CTS is active. The live status is polled through the authenticated `/admin/api/hardware/input_status` JSON endpoint; if that endpoint is unavailable the page shows a short **Input status unavailable** message instead of a JavaScript parsing error.

### Start automation and central audio

Configure whether the central start scheduler fires automatic horn signals and whether the hut PC generates VHF/audio announcements. Speech-rate controls and the **Save and test central audio** button are kept with the audio/scheduler status messages.

If the VHF is keyed by VOX, enable **Play a VOX wake-up tone before announcements** and set the **VOX tone lead (seconds)**. A short tone is then played that many seconds before each spoken announcement, so the radio is already transmitting and the first words are not clipped.

Do not connect horn power directly to the PC or serial adapter. Use a proper relay, opto-isolated relay board or interface.


## Weather Station

Choose the wind source with the two option cards at the top of the section. Only the fields for the selected source are shown:

- **Weather station polling** — the app polls the configured start-hut weather station and uses its latest wind sample. Set the station URL or IP (the app reads `get_livedata_info` and stores wind samples in the local database), the polling interval, and a wind-direction offset/alignment correction.
- **Manual input** — no polling is attempted; the app uses the TWD and TWS you type in.

Wind displayed in race/course/public pages is labelled as **wind at the hut**.

## Video Recording

The **Video Recording** section is split into subsections so the high-use controls are easier to find.

### Camera input and recording mode

Configure:

- Enable/disable recorder.
- USB webcam or RTSP stream.
- USB camera dropdown or manual override.
- RTSP recording/main-stream URL.
- Recording mode: camera stream copy for RTSP/IP cameras, or re-encode with the app timestamp overlay.
- Stream-copy buffer format: Fragmented MP4 recommended, MPEG-TS advanced fallback.
- RTSP timestamp mode.
- FFmpeg path.

### Live preview and buffer

Configure:

- Optional RTSP preview/sub-stream URL for lightweight live camera preview. In stream-copy mode this preview runs separately from the evidence recorder.
- Preview still size, JPEG quality, frame rate and keyframe-only mode.
- Pre-event and post-event seconds.
- Segment length and rolling-buffer duration.

### Public video and live image publishing

Configure:

- Public video publishing: off/local evidence clips or Cloudflare R2 public copy.
- Public clip quality: 720p small or 1080p normal.
- **Draw the start line on public start/finish videos** (experimental, off by default): overlays the detected start line (pole base → ODM buoy) on the public copy only — red before the start and green after on start videos, green on finish videos. See VIDEO_RECORDING.md.
- Public live image source: served by the hut app or uploaded to R2 as one refreshed JPEG.
- R2 account/endpoint, bucket, access key, saved secret, public base URL and object prefix. **These also drive publishing a series' results** (v0.281): *Publish to website* on the series page uploads into `results/series-<id>/` in the same bucket, and the button is hidden until this section is filled in. See [`WEBSITE_PUBLISHING.md`](WEBSITE_PUBLISHING.md).

When Cloudflare R2 public-video publishing is enabled, the app keeps the full-quality evidence clip in `data/video_clips/`, creates a smaller H.264 web copy in `data/video_clips/public/`, and uploads that copy to R2. Use **Save and test R2 upload** after entering credentials. Use **Save and retry failed public video uploads** if a clip shows **Public video error** but the local public copy or evidence clip exists.

### Camera zoom / PTZ presets

When PTZ preset control is enabled, set the camera URL/IP, username, password, authentication mode, channel, idle preset, race start/finish preset and how many seconds before the first actual start the camera should switch. The password field is left blank on reload; leave it blank to keep the saved password. Use **Save and test camera login only** to verify credentials against the read-only PTZ capabilities endpoint without moving the camera, then use the idle/race preset buttons. A successful manual preset test holds the selected preset for 30 seconds before automatic switching resumes, so the scheduler cannot immediately return the camera to the idle view while you are checking the test. PTZ diagnostics include HTTP status, auth method/mode, saved-credential indicators, the ISAPI URL, the manual hold-until time and a password-masked equivalent curl command.

### Recorder status

The section shows recorder health, latest buffer segment, live-preview age and FFmpeg log tail. In stream-copy mode, use the camera's own OSD timestamp/NTP clock because the app does not re-encode the recorded evidence clips.

## GPS tracking

The **GPS tracking** section holds the *connection* to the Traccar server that ingests the boats' GPS trackers: enable tracking, the Traccar base URL and API token, the poll interval, position-history retention, the default mark-rounding radius (any mark can override it in Marks → Edit), the **rounding gate reach** — how far past a mark, on the hand a boat is supposed to pass it, still counts as rounding it (750 m; the only setting in the app that knows a mark has a required side, and what catches a boat that gives a mark a wide berth), a **Push ingest token**, a "sound the horn on an auto-confirmed GPS finish" toggle, and a "simulate boats" option for previewing without hardware. Under **SIM connectivity (Hologram)** it also holds an optional API key that adds each tracker's SIM state and current network to the Trackers page, and warns when a SIM has been paused — the one reason for a silent tracker that never fixes itself. Off by default; see [`TRACKING.md`](TRACKING.md) for what to read into it and which Hologram role the key should have. This section is admin-only like the rest of Settings.

The **Push ingest token** is what lets Traccar POST each fix to the app as it arrives instead of the app waiting for the next poll — set the same value here and in the relay's `forward.header`. The status box below reports which state you are in, because a mismatch is otherwise invisible: *Push: working — N fixes received, last just now*, *configured, but no fix has arrived this way yet*, or *not configured*.

Managing which tracker is on which boat — and adding or removing trackers — is done on the separate **Trackers** page (in the bottom group of the side menu), which is available to **race officers** as well as admins, since it is a race-day task. The app can create and delete the Traccar devices for you via the API, and a tracker that Traccar has heard from but that is not set up here yet can be picked off a list rather than typed in as a 15-digit IMEI. Full details, including the live map, *Position on the water* and automated finishes, are in [`TRACKING.md`](TRACKING.md). What each model of tracker actually does — battery, accuracy, how quickly its fixes arrive, and the ways each will mislead you — is in [`TRACKERS.md`](TRACKERS.md).

## Hut power (Victron VE.Direct)

The hut runs off-grid, and this section reads three Victron devices connected to the race-office PC by VE.Direct-to-USB cables. Monitoring is **read-only** — the app never sends anything to the Victron equipment.

Enter each device's serial port and leave any of them blank to skip that device:

- **SmartShunt (battery) port** — battery voltage, current, state of charge and time-to-go.
- **SmartSolar MPPT port** — solar panel voltage/power, yield and charger state.
- **Phoenix charger port** — charger/inverter voltage, current and state.

Also here:

- **Sample interval (seconds)** — how often a combined reading is recorded.
- **History retention (days)** — how long samples are kept. Power history is not race-tied, so this is a simple flat age cut.
- **Simulate power** — preview the dashboard card and history graph without any hardware attached.

**The dashboard warns when the battery stops getting back to full.** That, rather than a low battery, is how a solar hut fails: nothing looks wrong on any one day, the bank simply reaches a little less each afternoon, and by the time the voltage is visibly low there are days left rather than weeks. The card appears after **three days** without a full charge and hardens after seven, or immediately below 60%. A single dull day is ignored, because one happens all summer and a card that cries wolf in July is one nobody reads in November.

It matters here because the club's panels lie **flat on the hut roof**. At this latitude the December sun peaks 13.7° above the horizon, so a flat panel is nearly edge-on to it, and measured against the hut's own 24 W standing load a midwinter day — even a clear one — may not replace what the night took. Summer is not the test: across fifty days of July and August the bank reached 100% every single day.

Readings appear on the dashboard and on the **Hut power** page in the bottom group of the side menu. Samples are stored in their own database, `data/power_history.db`, which is a separate backup section from the race database — see [`DATA_AND_BACKUP.md`](DATA_AND_BACKUP.md).

**Reading the history chart.** The three panels — battery, solar and load, and current — share one time axis, ruled and labelled at round clock times so a moment can be followed down all three. Move the pointer across any panel and a cursor line follows it, marking where it crosses each trace and listing the time, state of charge, voltage, current, solar watts, load watts and charger state at that instant. Pick the period from the range selector; the 30-day view holds tens of thousands of samples and the cursor stays responsive across all of them.

## Rating lookup sources

The app can search both IRC and YTC sources when adding/editing boats.

### IRC listing

Default source:

```text
https://www.topyacht.com.au/rorc/data/ClubListing.csv
```

The app caches the downloaded IRC list for one hour to avoid slow repeated lookups.

### YTC listing

A Google Sheet sharing URL can be used. The app converts it into a CSV export URL automatically.

Example:

```text
https://docs.google.com/spreadsheets/d/<sheet-id>/export?format=csv&gid=0
```

The app caches the downloaded YTC list for one hour.

## Polars and sail charts

Use **Settings → Polars** to manage the polar files used by course recommendation, race leg analysis, manual course builder and public course analysis.

You can:

- upload a new polar;
- optionally upload a matching sail chart at the same time;
- add or replace a sail chart for an existing polar;
- delete only a sail chart;
- delete a polar and its matching sail chart.

Sail-chart filenames are enforced from the polar filename stem. For example, a polar named `J109.txt` uses `data/sailcharts/J109-SailChart.txt`, or `.csv` / `.tsv` if that was the uploaded chart format. If no matching chart is installed, course analysis uses the default sail chart at `data/DefaultSailChart.txt`.


## Course chart background

Configure:

- Base map tile URL.
- Nautical/seamark overlay tile URL.

The course route, marks, start/finish line and wind arrow still render without tile background; only the map/chart imagery requires network access.

## Virtual Race Officer

The settings for the **Virtual Race Officer** at `/vro`, the page that lets
somebody run racing from a boat by typing plain English. Who may use it is the
per-user **VRO** permission in *Users* above; this section is about what reads
what they type.

**A model is required.** With none configured the page says the feature is not
available and offers no box to type in, and both command endpoints refuse. The
app does have a small built-in grammar covering about six sentence shapes, and
it used to answer when no model was reachable; on a page that invites plain
English that read as an app understanding nothing, so it no longer does. The
cost is that the racing goes back to the race sheet when the hut's outbound
internet is down, which is the trade the club chose.

| setting | what it is |
|---------|-----------|
| API key | The provider key. Stored like every other secret and never shown back. Blank means the page is unavailable. |
| Model | The model id, e.g. `claude-sonnet-5`, or `anthropic/claude-sonnet-5` through Cloudflare's AI Gateway. |
| Base URL | The endpoint. Anthropic's own API by default; a gateway address gains logging, rate limiting and a model fallback without changing anything else. |

The card shows an **Interpreter** line saying whether one is configured and
answering, and if the last request failed it gives the provider's own reason — a
402 for an empty account, a 400 for a model id that does not exist. Without that
the symptom of every one of those is identical from the water: commands stop
being understood and nothing says why.

Two notes on gateways. The address decides the wire format — a path ending
`/chat/completions` is read as the OpenAI shape, one ending `/messages` as
Anthropic's — and the key is sent as a bearer token to everything except
Anthropic's own API. And **caching wants care**: what "start a race at 11" means
depends on the day it is said, so the request carries the resolved date in the
message itself; a cache keyed on the whole request is safe, one that ignores it
is not.

## Web server (v0.256)

How much the app will take on at once. It serves the race office, the clubhouse display and every competitor's phone from one PC, and these three settings size that. **They take effect when the app restarts** — Waitress reads them once at startup — so the card shows what the running process actually started with and turns amber with *restart to apply* when the saved values differ.

| setting | range | default | what it is |
|---------|-------|---------|-----------|
| Worker threads | 4–64 | 8 | Requests served at once. More costs little: these are mostly waits on disk and network, not CPU. |
| Connection limit | 50–512 | 100 | Open connections allowed **at all**. |
| Idle timeout (seconds) | 20–600 | 120 | How long an idle connection is held before it is closed. |

**Raise the connection limit first** if phones on the water get "cannot connect" while the race office is fine. It counts open sockets rather than people, and a browser holds up to six per origin — so the Waitress default of 100 is about sixteen viewers, reached whether or not anything is slow. That is what filled it during a race: every request fast, and no sockets left.

The environment variables `RO_THREADS`, `RO_CONNECTION_LIMIT` and `RO_CHANNEL_TIMEOUT` still work and are the defaults these settings override. All three are clamped on save and on read, so a mistyped value cannot make the app unreachable.

### Public address (v1.001)

The address competitors type in, for example `https://pro.pwllhelisailingclub.org`. Scheme and host only, no trailing path. **Unlike the three above, it takes effect immediately.**

Leave it empty on the club LAN, where the address a request arrives on is already the right one. It matters when the app is reached from outside: behind the relay, the app only ever sees the tunnel's own internal hostname, so every address it builds for the outside world names a machine nobody can reach. Two things consume those addresses today — the logo addresses the **live stream** fetches, and the branding manifest a **3D replay render machine** reads — and both quietly get nothing useful without this. Neither fails loudly, which is exactly why it is worth setting once and forgetting.

## Slow-request log (v0.256)

Any request taking longer than `RO_SLOW_REQUEST_MS` (default 250 ms) is written to `runtime/logs/slow.log` with its method, path, status and duration:

```
2026-08-09 21:14:07 |     812 ms | GET    /api/race/146/track | 200
```

One file per day, kept a fortnight. Set the threshold to `0` to turn it off entirely — below the threshold it costs one subtraction and one comparison.

It exists because when the server started refusing connections there were four plausible causes in the code and **nothing recorded how long a request took**. Guessing there means fixing three things that were fine and leaving the one that was not. If it happens again, this file names the endpoint.

## Recent hardware events

Shows recent horn and hardware input events. Use this to check unexpected manual horn input triggers.

## Public branding

The Settings page includes a **Public branding** section. Use it to enable/disable competitor-facing logo branding, upload an optional replacement Pwllheli Sailing Club logo, and add/delete sponsor logos for mark-sponsor exposure. The images are stored under `data/branding/`. The public live-camera page serves a branded JPEG with the logos burned into the image, and the smaller public R2 video copy burns the same logos into the video. Public camera/video branding keeps the club logo at the top-left and shows one sponsor logo at a time in the top-right, rotating every 5 seconds. Logos are capped at roughly 15% of the image/video height so they do not obscure the finish line. Local evidence clips are left unbranded. Standalone published results are static HTML, so they still show the full club/sponsor strip in the grey page banner.


## Activity log

The app keeps a plain-text **activity log** of who did what. From v0.249 it can be read in the browser: **Settings → Recent hardware events** carries a link to it, newest entry first with a button per day. Administrators only, since it names who did what, and read-only &mdash; there is no route that can edit or clear it. It is written to `runtime/logs/activity.log` on the race-office PC, with one file per day (the previous day rolls over to `activity.log.YYYY-MM-DD`). The log lives under `runtime/`, so it is not included in backups or release packages.

As of v0.245 it records everything that changes state, which is to say:

- **Access** — logins, failed logins, logouts and password changes.
- **Races and results** — races created, updated and deleted; boats added to and removed from races; finishes and pursuit positions recorded; a horn event assigned as a finish; a GPS finish confirmed or dismissed; **an entry edited by hand**, with the old and new finish time and status. That last one is the only action that can quietly change a published result, so it is worth knowing it is there.
- **The course** — course number changed, a manual course saved or cleared, a course shortened, and marks added, edited, moved or deleted.
- **On the water** — the mark a boat is sailing to, when a race officer corrects it with the arrows on the *Position on the water* list, recording the marks either side of the change. It also appears in that race's own event log.
- **Boats and ratings** — boat-database changes, boat re-links, and IRC/YTC rating imports.
- **Series** — created and updated, recording the discard profile and class count, because both re-score every race in the series at once.
- **Settings** — every save, listing which keys changed **and what they changed from**. `horn_active: 1 -> 0` is the kind of line that explains a horn fault a fortnight later. Passwords, secrets, tokens and passphrases are reported as changed or set **without their values**: this is a plain-text file, so it must never be somewhere to read a credential out of. A save that altered nothing is still recorded, so "who was in Settings" stays answerable.
- **Data movement** — a backup downloaded or restored (a restore replaces the data the club runs on), each nightly off-site backup and any failure, public video uploads retried or given up on, branding and polar/sail-chart uploads and deletions, and tracker changes.
- **Warnings worth reading** — when a GPS finish was recorded but an *earlier* line crossing had been refused by the finishing-direction test, with both times and the gap. Check that boat against its finish video.
- **User accounts** — added, updated and deleted.

Race-console horn presses and race log events are deliberately absent: those already go to the per-race event log shown on the race page, which is a better place to read a race back. GPS position ingest is not logged either — it arrives every few seconds and would bury everything else.

## Off-site backup

A **Off-site backup** section (v0.238) pushes an AES-256 encrypted copy of the usual backup ZIP to Cloudflare R2 each night, so the club's records survive the hut rather than only the hard disk. Full setup and restore instructions are in [`DATA_AND_BACKUP.md`](DATA_AND_BACKUP.md); the parts that belong here:

- The **bucket must be a separate private one**, not the bucket that serves public race videos. The app refuses to run otherwise — that bucket is publicly served, and a backup holds every user account and password hash in the club. The account ID and keys are shared with the video settings above.
- The **passphrase** field behaves like the camera and R2 secrets: leaving it blank keeps the saved value. Record it in the club password manager — nothing can open an off-site archive without it. There is a separate tickbox to clear it deliberately, which stops off-site backups.
- **Save and back up off-site now** runs one backup immediately and reports the real outcome, including a wrong bucket name or a refused key. Use it once after any change: an unattended 3 am job that has never succeeded is not a backup.
- The section lists **what is actually in the bucket**. The dashboard carries the age of the last success and flags it when there has not been one.
- Backups are held back while boats are racing, in the three hours before a start, and while race videos are still uploading. The hut is on 4G and a backup must never cost the club a start video.

## Backup / restore

The **Backup / restore** page sits at the bottom of the side menu, just above the human-supervised prototype warning. It is separate from Settings because it can download or replace large parts of the `data/` folder.

Backup and restore are selectable by section: database, GPS tracks, hut power history, marks/courses/start-finish files, polars and sail charts, branding images and videos. The **Backup passphrase** field on the restore panel is only for an encrypted off-site archive; leave it blank for a ZIP downloaded from this page. The app keeps race data in three separate SQLite files and all three are backed up — the GPS track history and the power history were added to the page in v0.192. Videos are not selected by default and show a warning because saved start/finish clips can make a large ZIP. Restore only replaces the sections ticked on the form and present in the uploaded Race Officer backup ZIP.

**Restoring a backup is admin only**, because it overwrites the current data. Downloading a backup is available to race officers too; for them the Restore panel is replaced by an "administrator access required" note.

The database option uses SQLite's online backup API so it can copy the live race database safely while the app is running. Temporary database connections are closed explicitly before the temporary backup folder is removed, which avoids Windows file-lock errors during backup creation.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

