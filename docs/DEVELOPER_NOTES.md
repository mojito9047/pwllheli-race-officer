# Developer notes

These notes summarise the structure and maintenance expectations for the Flask/Waitress app.

## Application shape

The project remains a single-process Flask application launched with `python app.py`, organised into three layers. `app.py` is the app core — Flask setup, the security config, auth/CSRF/session handling, the `before_request`/`after_request` hooks, the settings form, `url_for`-coupled and auth helpers, background-task startup and the Waitress `if __name__ == "__main__"` block. The **HTTP route handlers live in a `routes/` package** (all ~210 routes, split out of `app.py`). Everything else — the domain and hardware logic — lives in importable, unit-testable `core/` modules. Build/documentation tooling lives in `scripts/`.

```text
app.py        Flask app, security config, auth/CSRF/sessions, request hooks, settings form, background-task startup, Waitress entry point
routes/       HTTP route handlers, grouped by area (see below)
core/         Extracted logic modules (see mapping below)
templates/    Jinja templates
static/       CSS, JavaScript and images
data/         Backup-worthy race data: database, courses, marks, polars, sail charts and clips
runtime/      Temporary caches, FFmpeg buffer/logs and live preview files
deploy/       Windows deployment scripts + live-stream relay
docs/         User and developer documentation (incl. docs/hardware/ manuals)
scripts/      Build pipeline: release ZIP, PDF manuals, screenshot capture
tests/        pytest suite
```

`app.py` re-imports the names it uses from `core/` (so existing references and Jinja helpers resolve unchanged), and re-exposes several core modules as attributes (`app.appstate`, `app.db`, `app.video`, `app.horn`) that the test suite monkeypatches.

### The `routes/` package

Each module in `routes/` registers a group of views on the shared Flask `app` and keeps the **original endpoint names** and the dual `/x` + `/admin/x` decorators, so templates, `url_for(...)` and the public-endpoint allow-list are unaffected. A module obtains the app object and the helpers it needs from the *running* app module via `routes.app_module()` (which returns `sys.modules["app"]` when imported, or `__main__` when started with `python app.py`) rather than `from app import ...` — a plain `from app import app` would re-import `app.py` a second time under `python app.py` and register routes on a throwaway Flask instance. `app.py` imports the route modules at the very end of its body, after all helpers and hooks exist. Flask **blueprints were deliberately not used** because they namespace endpoints (`bp.name`), which would break every bare `url_for('name')` in the templates.

Modules: `auth` (login/logout/account), `pages` (dashboard, races list, recommend, split, documentation, power history), `competitor` (public race pages), `api` (weather/power/courses/marks/hardware/leg-analysis JSON), `series`, `boats`, `marks`, `backup`, `settings` (settings/hardware/users), `settings_actions` (hardware/video/weather test buttons + branding/polar uploads), `race`, `race_course`, `race_entries`, `race_console`, `assistant` (the Virtual Race Officer page at `/vro` — `/onwater` still answers — and the natural-language command endpoints: interpret / read back / confirm, executing only through `core.raceadmin`; it also assembles the facts the interpreter is allowed to know, matches a boat, series or race **name** against the database — the interpreter has words, only the database has boats — and keeps the conversation in `assistant_commands`, which is what lets a refresh on a boat come back with the thread and a live Yes button rather than a blank page), and `media` (branding/video/live-image serving).

A route that calls an `app.py`-defined helper that a test monkeypatches on the app module (e.g. `upload_bytes_to_r2`) must reference it live as `_app.<name>()` rather than binding a static copy, so the patch takes effect. Where a helper has moved into `core/`, patch it where it now lives — the shortened-course signal is `core.raceadmin.signal_shortened_course`, not `app._shorten_course_signal`.

SQLite is used for local storage at `data/race_officer.db`. The schema is created and upgraded by `init_db()` (in `core/db.py`) using `CREATE TABLE IF NOT EXISTS` and `ensure_column()` migrations. Because the schema builder (`_init_db_uncached`) is tightly coupled to schema/admin logic in `app.py`, `core.db.init_db()` invokes it through a registered hook, `core.db.SCHEMA_INITIALIZER`, which `app.py` sets at import.

## The `core/` package

Foundation and data access:

- `core/appstate.py` — single source of filesystem paths, URLs, `APP_VERSION`, the surveyed start line, and the reloadable course/mark/start-finish data (`reload_course_mark_data` refreshes it after a restore). Test fixtures monkeypatch paths and `DB_PATH` here.
- `core/db.py` — SQLite access (`get_db`, `init_db`, `table_columns`, `ensure_column`), the legacy-layout migration, and the `row_get`/`safe_json_loads` row helpers.
- `core/settings.py` — the `app_settings` store with its short-lived cache, the numeric/boolean validators, and the typed config views (`weather_config`, `listing_config`, `course_chart_config`, `race_console_config`).

Pure logic:

- `core/timeutils.py` — date/number/string formatting and geometry primitives (`parse_dt`, `haversine_nm`, `bearing_deg`, …).
- `core/polars.py` — polar/VMG interpolation and sail selection.
- `core/ratings.py` — IRC/YTC rating-band parsing and classification.
- `core/scoring.py` — corrected-time, RRS A7 low-point ranks, A2.1 discards, A8 tie-breaks.
- `core/weather.py` — station URL validation, live-data parsing, wind-retention windows.
- `core/courses.py` — course geometry, mark expansion, leg building, recommendation, spoken announcements.

Domain and hardware:

- `core/racesignals.py` — **the race signals themselves: what AP means and what it is called.** A leaf module importing nothing, which is the whole point: `core.assistant` needs the vocabulary to read a postponement back, and it may not import the service layer (a test holds it to that). `POSTPONEMENT_KINDS` carries AP, AP over H (further signals ashore) and AP over A (no more racing today) with the Race Signals wording, because that wording is what a race officer is agreeing to in a read-back and paraphrasing a rule there is how you agree to something else. `one_minute_rule` marks the one kind that carries it: the warning signal is made one minute after **AP** is removed, and ashore there is no such warning to count from. `normalise_postponement_kind` reads “AP over H”, “ap_h” and “AP/H” as one stored kind. **Lowering AP is scheduled, not immediate**: `resume_race` stores the whole minute the flag comes down (`postponement_ends_at`) and sets the warning one minute later, and `core/startsequence.end_due_postponement` makes the single sound at that moment — the same loop that makes every other timed signal. Lowering it on the click put the warning at 14:16:41 and the gun at 14:21:41, which no fleet can count down to. `race_is_postponed` therefore takes a `now`: the flag is up until that minute, because the displays must show what is on the mast rather than what has been decided about it. AP is also in `public_render_signature` — without it the clubhouse television goes on counting down to the old gun, which is the exact fault that signature was added for.
- `core/races.py` — race/series/boat DB accessors and start-time derivation. Also **whether a race is postponed** (`race_is_postponed`, `postponement_flag`): it lives here rather than beside the postpone/resume commands because the start-sequence scheduler has to ask it before sounding anything, and `core/startsequence.py` cannot import `core/raceadmin.py` without a cycle.
- `core/classconfig.py` — rating-band class config and start-plan model, start schedule, signal-panel helpers.
- `core/series.py` — race result tables and series standings/publish builders.
- `core/entrysync.py` — entry creation and series entry synchronisation.
- `core/raceadmin.py` — **the write side of a race sheet**: `create_race` and `update_race_settings`, taking a `RaceSpec` / `RaceSettings` dataclass rather than a form and raising `RaceValidationError` with the message to show. Both lived inside the route handlers, reading `request.form` and answering with `flash`/`redirect`, so only a browser POST could create a race — and any second caller would have had to re-implement the rules. It also covers `add_entries` (all-active / fleet / one boat, adding across the series where the race has one) and `shorten_course_at` / `clear_shortening`. `routes/race.py`, `routes/race_entries.py` and `routes/race_console.py` now parse the form and call in; the start-plan grid is the one part that stays in a route, because it genuinely is form-shaped. It owns the side effects that must not be forgettable: the post-save work (`reset_start_sequence_state_for_race`, `ensure_start_video_scheduled`) so a caller cannot save a new warning signal and leave the horn scheduled for the old one, the pursuit start-ladder recompute when a boat is added, and the two horn blasts plus spoken announcement of a shortened course (`signal_shortened_course`, moved here from `app.py` and fired on its own thread; pass `signal=False` for a dry run). `update_pursuit_settings` is the same for a pursuit, which has its own start ladder to recompute. `RaceSettings.choosing_course` says whether a save is somebody **choosing** the course or merely needing a course number to write: setting a start time needs one, and taking that as a choice let a race created from the water read as “course chosen” while the same command replied “course not yet set”. It only ever counts upwards. It takes the actor for the activity log rather than reading a Flask session. **Deleting a series** lives here too (v0.276): `series_delete_block(db, series_id)` returns the reason it cannot go or `None`, in the shape `core.marks.mark_delete_block` already used, and `delete_series` refuses on it. A series carries the rating bands, start plan and discard profile its races were *scored under*, so neither orphaning those races nor cascading the delete over them is offered: they are moved out or deleted first. The guard is asked twice — by the page to decide whether to render the button, and again by the route, so a stale page cannot delete a season.
- `core/assistant.py` — **interprets a typed sentence; decides nothing.** Turns "start a race at 11, hour long" into a named `Intent` from the bounded `TOOLS` list, then `resolve()` produces a read-back for a person to confirm, or a question when the sentence did not carry enough. It executes nothing and deliberately does not import `core.raceadmin`, which is what stops interpreting and acting quietly merging; a test asserts the import stays absent. `parse_command(text, context, parser)` is the provider seam, and a model-backed parser is a function of that shape. `grammar_parse` is the deterministic no-network reading of the same sentences: it is what lets the golden-utterance tests run in CI with no provider account, and **since v0.264 it no longer answers commands in production** — with no model the page says the feature is unavailable, because on a page inviting plain English six sentence shapes read as an app that understands nothing. Two club ambiguities are settled here rather than left to a model: a bare "start at 11" is the **gun**, so the warning signal is 10:55 and the read-back always shows both; and a length with no race type is a *question*, because it is a pursuit's actual period but only a course-recommendation target for a standard race. `CommandContext` carries the hut's clock (never the client's), the current race's own gun time, course and whether it has **finished** — a finished race stays the app's current race, so "let's try 29" read back as an ordinary course change to the race that had just ended, and `_which_race` now names it "(which has finished)" whatever the interpreter thought. It also carries the last few exchanges with the same person, the **proposal waiting to be agreed to** (while one is there it is what the conversation is about, to be re-issued in full with any change), and `facts`: plain sentences of the app's own working — the wind and how it has shifted over the hour, the course's legs and how long it should take, the courses it would recommend, who is entered, the club's series and recent races, whether the trackers are reporting. That list is the whole of what an answer in words may be built from; there is no other source, which is the point. `answer_to_question` puts a one-word reply back where it belongs, so "standard" re-issues the whole command with the time and length it already had — without it the app asked a question it could not hear the answer to. `reads_as_yes` / `reads_as_no` recognise plain agreement by allowing a short list of words rather than forbidding one, so "yes, course 4" stays an instruction.
- `core/assistant_llm.py` — the model-backed parser for `core.assistant`, and the only place in the app that talks to a model. Builds the provider request from `core.assistant.TOOLS` (so a tool the app does not implement cannot be offered by drifting), asks for a tool call but does not force one (`tool_choice: auto`) — forcing it contradicts the instruction to decline, and a model made to choose will choose: asked what it thought of the cricket, it proposed a status report. Prose comes back instead as the `ANSWER` pseudo-tool, which is not in `TOOLS`, has no branch in `_execute` and cannot become an action however it is worded — a race officer asks questions as well as giving orders. It puts the local date in the *message* so a gateway cache cannot serve yesterday's answer to "start a race at 11", and answers `None` on anything unusable — an unknown tool, a timeout, a reply with nothing in it — and the caller then says the interpreter did not answer rather than quietly answering with something else. `make_model_parser(..., transport=)` takes the HTTP call as an argument, which is what lets the tests drive the whole path with no network and no account. Configured by `assistant_api_key` / `assistant_model` / `assistant_base_url` in `core.horn.hardware_config`; no key means no parser, which is a working state. `dialect_for` picks the wire format from the address (`/chat/completions` is OpenAI's shape, `/messages` Anthropic's) and `auth_headers` sends a bearer token to everything except Anthropic's own API — Cloudflare's gateway speaks Anthropic's format while wanting a Cloudflare token, and answers a good key in `x-api-key` with a bare “Authentication error”. `LAST_ERROR` keeps why the last call failed: a 402, a wrong model id and a timeout are indistinguishable from the page, and all three look like a stupid app unless the reason is shown. `MAX_TOKENS` is 4096 rather than 512 because every reply from this endpoint opens with a thinking block spent from the same budget — a reply that runs out mid-thought carries no tool call and no words. 512 truncated a plain question (421 used); 2048 truncated “make me a course with lots of reaching, about 20 nm” on the club's own test server. Verified against Cloudflare's `anthropic/claude-sonnet-5` with `scripts/verify_assistant_model.py`, which is not in CI because it costs money and needs an account.
- `races.course_set` — whether anybody has actually **chosen** the course. A new race stores the first fixed course as a fallback so the chart, the leg analysis and the shortening options have geometry to work with, and that fallback used to be indistinguishable from a decision: the race sheet header, the competitor page and the spoken VHF announcement all said "Course 1" before a race officer had looked. `course_announcement_text` returns nothing until it is set (the one that could send a fleet round the wrong course). **As of v0.276 the whole of both pages asks it, not just the header** (`{% set course_shown = course_set or is_custom_course %}` in `race.html` and `competitor_race.html`): no course board, no leg analysis, no predicted time, `data-course-marks='[]'` on the chart -- a state the dashboard and Trackers maps already pass -- and the picker itself renders a selected `Course not set` option. `routes/race.py` reads an empty `course_no` as "saved without choosing": it keeps the stored fallback number and passes `choosing_course=False`, so a first warning signal can be set before the course is known. Refusing the save instead would block that, and defaulting to the fallback is the confusion this fixes. Set by `update_race_settings`, the course picker and the manual builder; back-filled to 1 for every race that already had a start time or a made-up course.
- `core/boats.py` — boat DB read side plus IRC/YTC rating-list import.
- `core/polar_io.py` — polar/sail-chart file IO.
- `core/backup.py` — backup section metadata and archive/restore IO.
- `core/eventlog.py` — the race event log.
- `core/weather_store.py` — wind sample storage/purge and the background poller.
- `core/horn.py` — ProLog serial horn IO and manual-input sensing.
- `core/audio.py` — the central TTS announcement queue/worker.
- `core/video.py` — FFmpeg recorder, clips, PTZ, Cloudflare R2 publishing, branding.
 **The recorder is supervised** (`video_watchdog_tick` / `start_video_watchdog`, every `VIDEO_WATCHDOG_SECONDS`): it restarts FFmpeg when the process has died *or* when it is alive and has written no segment for several segment-lengths, which is what an RTSP camera that vanishes without closing the connection looks like. It calls the ordinary `start_video_background_recorder`, so there is no second way to start a recorder and a broken camera fails and logs the ordinary way; restarts are counted into `video_runtime_status` for the dashboard and written to the activity log at most once every ten minutes, because an unplugged camera fails every time the watchdog looks. Before this, the recorder was started only by an app restart, a settings save or a scheduled clip — one that stopped overnight stayed stopped until somebody saved a setting, while the live preview's own watchdog kept the dashboard looking healthy. A stalled recorder leaves a characteristic single stale segment: the trim goes on deleting closed segments, but Windows will not delete the one FFmpeg still holds open. An empty buffer folder means it exited; one lone file means it stalled.
- `core/startsequence.py` — the central start-sequence automation scheduler.
- `core/power.py` — Victron VE.Direct hut power monitoring: text-protocol parser, a *separate* `data/power_history.db` time-series store, the background poller, a simulator, and live status. Monitor-only. Reads its config (device COM ports, simulator, sample interval, retention) through `core.horn.hardware_config`, where the keys are stored alongside the horn settings. `recharge_warning()` feeds the dashboard's winter card: it asks whether the bank is still *reaching* full rather than whether it is low, because a flat-mounted array at 52.9°N cannot replace a 24 W standing load on a December day and the failure shows in the trend weeks before the voltage. Today is excluded from the streak (it may just be mid-morning), a single dull day is ignored, and it swallows everything — it runs on the page the hut leaves open all day.
- `core/track.py` — GPS yacht tracking + automated finishes. Pulls boat positions from a **Traccar** server (Queclink GL521MG trackers) over outbound HTTPS, stores them in a *separate* `data/track_positions.db`, and runs the background poller/status (mirrors `core.power`). Includes the crossing geometry (local planar projection + segment intersection + course-side direction check), the finish-line endpoints (mark `O` ↔ `appstate.BRIDGE_WINDOW_*`), a simulator, tracker→boat resolution (the `trackers` table; the per-entry `entries.tracker_unique_id` override is still read but no longer written — the race page's dropdown for it went in v0.207, leaving the Trackers page as the only place a pairing is set), and finish detection that writes to the `finish_proposals` table (RO-confirmed) or records a finish directly (auto-confirm). Config lives in `core.horn.hardware_config` (`track_*`/`traccar_*` keys); the finish write reuses `core.horn.VIDEO_CLIP_SCHEDULER` for the review clip. Tables `trackers`/`finish_proposals` and the `entries.tracker_unique_id` / `races.gps_finish_enabled` / `races.gps_auto_confirm` columns are created in `app.py`'s `init_db`. RO-facing guide: `docs/TRACKING.md`; relay/Traccar setup: `deploy/live_stream/README.md` §7. The fleet is no longer one model: `tracker_model()` resolves a device from its IMEI's type allocation code **qualified by the decoded protocol** (a code identifies whoever certified the radio, so a tracker built on a bought-in module may carry the module vendor's — and protocol alone calls an ATC700 and a RUTX50 the same thing), against the built-in map overlaid with the editable `tracker_types` table. `quality_from_position()` keeps altitude, hdop, pdop, satellites, rssi, Traccar's `valid` flag and the protocol with every fix, on all three ingest paths — `fetch_positions` (poll), `parse_forwarded_positions` (push) and `backfill_positions` (outage recovery) — stored raw and interpreted nowhere. `send_tracker_command()` / `tracker_command_results()` drive the admin-only console through Traccar's `custom` command, with `command_refusal()` blocking factory resets. What the club has measured about each model, and the traps (a roaming SIM leaving half of every Teltonika parameter dead; `hdop` not detecting a bad fix) is in `docs/TRACKERS.md`, with the vendor protocol manuals in `docs/Trackers/`. **The replay reply is bounded, and by the window rather than by the fix count** (v0.276): `REPLAY_MAX_WINDOW_S` (12 h, clamped in `race_track_window`) and `REPLAY_MAX_FIXES` (60,000 across the fleet, thinned evenly in `race_track_history`, both ends of each track kept). The window is the one that matters — a board snapshot costs about ten times a fix and is emitted every five seconds across the window whether any boat reported or not — and the fix budget exists only because the window cannot see the tracker reporting *rate*. Neither can move a result: this path feeds the replay viewer alone, and finish detection's own walk over `positions_for_entry_since` stays unbounded. Reasoning and measurements in [`TRACKING.md`](TRACKING.md).
- `core/replay3d.py` — **one sailed race as a scene file for the 3D replay, and the render job for it.** The half that has to run on the hut, because it is the half that needs the database: marks as they stood that day, the course actually sailed after any shortening, the start line and the finish line (which are *not* the same for an ISORA race — Pwllheli starts everything on the club line and an ISORA passage race finishes on a line 760 m away), the wind, the scorer's results, and every tracked boat's fixes resampled onto a regular clock. Written in local metres with the same equirectangular projection `core.track` uses for crossing geometry, so a boat this file says crossed the line is one the app says crossed. It imports no Blender, no PIL, no GeoTIFF reader and nothing from `scripts/`, which is what lets the render half live on another machine entirely — the hut PC cannot render a film in a useful time and should not try. A scene carries **no terrain**: the height grid and the imagery are the same for every race at one club, so they are built once by `scripts/replay3d` and shared, and a scene names them. `build_job` wraps a scene in what to do with it and `blank_status`/`status_is_stale` are the other side of that contract; the keys live under `replay3d/` in the bucket the race videos already use, so the hut only ever pushes and the renderer only ever polls and needs no route into the hut. `status_is_stale` exists because a render is hours long: a renderer that dies mid-job leaves a healthy-looking status behind for ever, and the heartbeat at `replay3d/status/renderer.json` is what tells "nothing happened" from "there is no renderer". Renderer side and the pipeline: `scripts/replay3d/README.md`.
- `core/barreplay.py` — **replaying a race on the clubhouse television.** Holds only which race was chosen and when, as a runtime file rather than a setting: it is transient display state and has no business in the settings diff. `replay_plan` gives the display what it needs — the warning signal, the last finish, and the clips — but **the clock lives in the browser**, because only the browser knows how a video is really playing. The rule it keeps is six times life except while a clip runs, when it drops to real time *locked to that video*: driving the clock from the playback rather than running them side by side is what stops them drifting, so a two-minute clip advances the replay exactly two minutes. `_without_repeated_footage` is the non-obvious part, and came from real data: clips carry a minute either side, so a fleet finishing within a few minutes of each other produces clips that are mostly the same video — one club race was six minutes long with twenty minutes of clip. Clips that add less than `MIN_NEW_FOOTAGE_S` beyond the one before are dropped from the replay only; every one is still on the race sheet. `footage_start_ts` is the other thing real data taught: a clip does **not** begin at `event_time - pre_seconds`. Clips are cut by concatenating whole buffer segments, so the file opens at the segment boundary at or before the window — the hut's start clip for "R9 Summer" claimed 11:24:00 and its first frame reads 11:23:44. The replay locks its clock to the video, so it drew the fleet sixteen seconds ahead of the picture. Clips now record `footage_started_at` when they are built; for older ones the boundary is gone with the buffer and cannot be recovered — the file is always `pre + post + one segment` long however the overhang falls, so not even its duration says anything — and the middle of the segment is used instead.
- `core/bardisplay.py` — when the clubhouse display (`/bar`) should cut from the chart to the start-hut camera: the start window, each boat's finish predicted from distance-to-go over speed, and each boat rounding the ODM taken as a radius around the mark. Pure functions over leaderboard rows, on the server so it can be tested and so it uses the same course-progress figures the leaderboard does. RO-facing guide: `docs/BAR_DISPLAY.md`.
- `core/pursuit.py` — pursuit-race start-time maths (per-boat offsets from the rating system), finishing positions and the pursuit start scheduler.
- `core/marks.py` — add/delete simple marks in `data/marks.json` from the admin UI, with in-use protection; writes `ensure_ascii=True` + LF to match the existing file style.
- `core/startline.py` + `core/odm_detector.py` — colour-based detection of the orange ODM buoy and rendering of the actual start line as an overlay on the *public* copy of start/finish videos (never the evidence clip); the same detector library is also deployed to the live-stream relay.
- `core/rounding.py` — the **rounding gate**: a line through a mark at right angles to the leg arriving at it, reaching `reach_m` (750 m default) on the hand the course requires and only the rounding radius on the hand it does not. One of the three tests in `track.rounded_mark()`, and the only one that knows a mark has a required side. A suspected wrong side is reported, never refused: the walk is sequential, so refusing would stall the boat and take its automatic finish with it, to enforce a rule the app does not adjudicate. `tests/test_simulated_races.py` is the regression harness; `scripts/compare_rounding_tests.py` re-examines the choice against real tracks.
- `core/sailwave.py` — Sailwave-importable CSV for race and series results: one flat table, one row per competitor per race, races told apart by `RaceNo`. One file per rating system, because a Sailwave series is scored under one. `Place` is deliberately not exported.
- `core/hologram.py` — what the SIM inside each tracker is doing, read from Hologram's REST API (`GET /devices`, `GET /usage/data/`; HTTP Basic with the literal username `apikey`). Joins on IMEI, which is already a tracker's `unique_id`, so no new identifier is stored. Exists for one thing Traccar cannot tell you: a SIM **paused by system** has hit a usage limit or a low balance, will never recover on its own, and from the app looks exactly like a flat battery. It also reports the carrier and country of the most recent data session, which turns the roaming question in [`TRACKERS.md`](TRACKERS.md) — half of every Teltonika parameter is live or dead depending on the answer — from an inference into a reading. Fails soft throughout, because it sits in the render path of a page opened on race morning: `sim_status()` returns whatever is cached **immediately** and refreshes on a daemon thread when that is over two minutes old (`trackers_sim_refresh` forces it now, blocking, for somebody who has just changed a SIM and is waiting; the page always renders the cache age, because a stale answer that will not admit its age is what made a paused SIM read "OK"), so two calls at a six-second timeout can never become twelve seconds of page load (`blocking=True` is for tests and non-rendering callers). The first load after start-up is therefore empty and the second is populated. A failed refresh replaces the cache rather than leaving the old answer in place — showing an hour-old carrier as current defeats the point. The cache key is a hash of the credentials, so changing them in Settings takes effect at once, and a key change drops the previous account's data rather than serving it. A healthy state means only that nothing at the network end is stopping the tracker, never that the tracker is alive, and the UI is worded to match. Also carries four weeks of per-SIM data usage (`GET /usage/data/daily`, bucketed into seven-day windows counted back from today) and costs it from `GET /plans/pricing` — Hologram returns **no cost on any usage endpoint**, so every money figure is `bytes / 1_000_000 * overage`, plus the recurring `amount` for the month-to-date total. `BYTES_PER_MB` is decimal because that is what Hologram's own dashboard shows (7,965,927 bytes displayed as "7.96 MB"); the binary divisor would overstate every figure by 5%. USD is hardcoded since the API has no currency field. The pricing call is deliberately separate from the usage call and fails independently, so losing the money cannot lose the bytes; the club's **Editor**-role key reads pricing and balance fine, but a more restricted role need not. The carrier comes from the link's own `last_network_used` in preference to scanning sessions, and `_apply_open_sessions()` (`GET /devices/opensessions`) turns `LIVE` into the dashboard's own **Ready**/**Connected** — both are `LIVE` on the device record, so the distinction exists nowhere else; a failed lookup leaves the raw state rather than guessing. `forecast_balance()` projects the prepay balance day by day rather than dividing by a monthly figure — each SIM bills on its own 30-day cycle (`cur_period_end` from `GET /usage/data/billing`), so fees arrive in clusters an average would hide; it is pure and takes `now`, so the club's real fleet is a test case. `GET /organizations/{orgid}/balance/` needs the `billing_visible` permission and is fetched separately for that reason. `balance_warning()` and `sim_warnings()` feed the dashboard cards and both swallow everything — they run on the page the hut leaves open all day. Config lives in `core.horn.hardware_config` (`hologram_*` keys).
- `core/offsite.py` — the nightly encrypted off-site backup to R2. Reuses the Backup/restore ZIP builder, so an off-site copy restores with no new code. Held back while boats are racing, within three hours of a start, and while videos are uploading; every one of those guards is time-bounded so a stale `RACING` row cannot defer it for ever.
- `core/resultspublish.py` — **putting a series' standings on the club website** (v0.281). Renders nothing itself: the route hands it the same document *Preview HTML* shows, and it uploads. Reuses the **public** R2 bucket `core/video.py` already publishes clips to (`video_public_r2_*`, via `upload_bytes_to_r2`, so the curl fallback and the SigV4 path are the tested ones) rather than introducing a second bucket, second credentials and a second public base URL. Each series gets `results/series-<id>/`, and every publish writes **two** objects: a timestamped one, immutable and cached for a year, which is the permanent record of that publish; and `latest.html`, overwritten each time with a 60-second cache, which is the link that goes on the club website. The short cache on the second is not a tuning choice — a long one would have Cloudflare serving Saturday's standings on Wednesday, which is the fault the feature exists to prevent. The dated key runs to the **second**, so two publishes a minute apart are two objects rather than one with two rows pointing at it. The `published_results` table is the app's own record, and it exists so the competitor landing page can show the latest link without a signed round trip to Cloudflare on a public page; `latest_published_by_series()` is one query for every roll-up on it. Nothing is recorded unless the upload succeeded. RO-facing guide: `docs/WEBSITE_PUBLISHING.md`.
- `core/r2.py` — the app's single AWS Signature V4 implementation, plus the PUT/HEAD/LIST/DELETE verbs the off-site backup needs; `core/video.py`'s signed uploader calls in here so the canonical-request rules live in one place. The upload-allowance constants live here too, being a fact about the hut's 4G rather than about video.
- `core/slowlog.py` — one line per request over `RO_SLOW_REQUEST_MS` to `runtime/logs/slow-YYYY-MM-DD.log`, daily, kept a fortnight. Written after the server started refusing connections mid-race with nothing recording how long a request took.
- `core/docsview.py` — renders the bundled `docs/*.md` inside the app. The list discovers itself by globbing and takes each title from the file's own first heading, so a new document appears without registration; a slug is looked up in that list and never becomes a path.
- `core/logfiles.py` — **day-stamped log files, and the reason they are not rotated.** `TimedRotatingFileHandler` rotates by *renaming* the live file at midnight, and on Windows that rename fails outright while any other process holds it open — a second app instance during a restart, a backup, an editor. It does not heal: the handler advances its next-rollover time only after a **successful** rename, so every later line retries the same doomed rename and is discarded, silently, because `logging.raiseExceptions` is off in anything shipped. Measured, not assumed — with one reader holding the file, three lines after midnight left no rotated file and no lines. `DatedFileHandler` writes `stem-YYYY-MM-DD.log` and never renames anything; changing day opens a different file, which needs no lock on the old one. `files_by_day` reads **both** namings so the upgrade loses no history: the dated files, the old handler's `activity.log.YYYY-MM-DD`, and the bare `activity.log` it was writing when the app was last stopped (filed under the day it was last written to). `prune_days` replaces `backupCount`, and a file something still holds open is skipped rather than allowed to stop the prune.
- `core/activitylog.py` — best-effort day-stamped activity/audit log at `runtime/logs/activity-YYYY-MM-DD.log` (see `core/logfiles.py`); `app.audit()` wraps it. Readable in the browser from Settings → Recent hardware events (admin, read-only).
- `core/loginguard.py` — in-memory login brute-force guard (see Security below).

Where a `core/` module needs a callback into code still in `app.py`, it uses a registered hook rather than importing `app` (which would be circular): `core.db.SCHEMA_INITIALIZER` (schema builder) and `core.horn.VIDEO_CLIP_SCHEDULER` (manual-horn evidence clips, registered by `core/video.py`).

### The competitor race page, pinned and following

`/public/race/<id>` renders one race; `/public/race` renders whichever is current and keeps up
(v0.277) &mdash; the same split `/bar` and `/bar/<id>` already had. `competitor_race_context(race,
pinned=)` carries it, and the only thing it changes is which state endpoint `public_urls.state`
points at: `competitor_race_state` for a pinned page, `competitor_current_race_state` for a
following one. That is enough because `competitor_race_state_signature` hashes a dict containing
`race_id`, so the current race changing is indistinguishable, to the page's existing two-second
reload, from a course being changed. **`positions` and `track` stay pinned** to the rendered race
&mdash; they are fetched between reloads, and following there would draw one race's boats over
another's course. Both new endpoints had to be added to `public_endpoint()`, which is an
allow-list on purpose.

## Polar and sail-chart files

Polars live in `data/polars/`. Optional per-polar sail charts live in `data/sailcharts/` and must use the polar stem plus `-SailChart`, for example `J109.txt` → `J109-SailChart.txt`. Upload handlers enforce this naming so course analysis can deterministically find the correct chart. Keep the legacy same-name lookup as a fallback unless a future migration removes it deliberately.

## Video evidence and public publishing

Full-quality start/finish/manual-horn evidence clips live under `data/video_clips/`. When Cloudflare R2 public publishing is enabled, `publish_public_video_clip()` creates a smaller H.264 copy under `data/video_clips/public/` and `upload_public_video_file_with_retries()` uploads it to R2. Keep R2 uploads serialised with `PUBLIC_VIDEO_UPLOAD_LOCK`; concurrent finish uploads can overload the hut 4G connection and previously left the first finisher in `public video error`.

The Settings retry route calls `retry_public_video_uploads_once()`. It should reuse an existing public copy where possible and only regenerate from the evidence clip when the public copy is missing.

## Compound mark geometry

`data/marks.json` can define parent marks with `compound: true`, `components` and `rounding_order`. The canonical course sequence remains parent-level so course boards and audio announcements stay compact. Geometry expands through `expand_course_points()` before `course_legs()` calculates distances, bearings and TWA analysis.

Example: `Yp` expands to `Ya -> Yb`; `Ys` expands to `Yb -> Ya`. A corner is an ordinary coordinate-bearing mark and can be put on a course in its own right; `mark_sort_key()` files it under its parent in the picker. `hidden_from_picker: true` still exists as a mechanism for a mark that should not be selectable, but no mark carries it — the corners did until v0.255, which is why the walk and the chart could disagree about them.

## Waypoints

A mark with `"waypoint": true` used in a course with `"rounding": "via"`. It is in the geometry and nowhere else: no course board, no announcement, no shortening option, no mark count, nothing drawn on the chart. See [`WAYPOINTS.md`](WAYPOINTS.md).

`courses.expand_course_points()` is the single course path — chart payload, `course_legs()`, TWA analysis and `track.course_rounding_sequence()` all come from it. A waypoint is passed by `track.passed_waypoint()`, a gate perpendicular to the incoming leg, not by a radius: boats beating past a headland pass miles offshore, and a radius wide enough to catch them fires while they are still short of the corner.

Two things print or draw a course independently of that function, and both had to be taught: `course["board_marks"]` for the six templates that render course chips, and `routePoints()` in `static/course_map.js`, which expands the course again in the browser. Tests fail if either forgets.

## Timing model

The database column `races.start_time` now stores the **first warning-signal time**, retained under the old column name for database compatibility.

Use these helper functions rather than manually interpreting the column:

- `race_first_warning_dt()` — first warning signal as a `datetime`.
- `race_first_start_dt()` — first actual start, warning + 5 minutes.
- `race_start_schedule()` — all configured starts with absolute warning and actual start times.
- `start_sequence_key()` — hash used to deduplicate scheduled horn/audio events for the current timing/course/start-plan version.

Comments and UI text should say **first warning signal** when referring to the stored race setup field. Use **actual start** when referring to the time used for elapsed-time/result calculations.

## Class and start-plan model

Series setup supports up to three IRC classes and three YTC classes. Each class has a rating band and a Numeral 0–9 class flag. Start plans support up to six starts. Races normally use the series default start plan but can override it.

Older text parsers remain in place for backwards compatibility with existing saved data. New UI work should prefer the graphical class/start fields.

## Race-entry rating snapshots

Do not calculate results from the live boat database rating. When a boat is added to a race, the app copies the current IRC/YTC values into the entry's `manual_irc_rating` and `manual_ytc_rating` fields. These are the race-entry rating snapshots and are editable from **Entries & finish times**.

The legacy `entries.rating` and `entries.rating_source` columns are kept populated for compatibility, but IRC/YTC-specific code should use the explicit per-race rating fields.

## Series entry synchronisation

For series races:

- adding a boat to one race adds it to all races in that series,
- creating a new series race imports existing series competitors,
- saving a race into a series synchronises that race's entries to the series.

Use the existing helper functions for this behaviour instead of duplicating insertion logic:

- `add_boat_database_entry_to_race()`
- `add_boat_database_entry_to_series_races()`
- `sync_series_entries_into_race()`
- `sync_race_entries_to_series()`

Boats are added to a race only from the boat database (single or bulk-add); the one-off/manual entry route was removed. `core.entrysync` still keeps `add_manual_entry_to_race()` / `add_manual_entry_to_series_races()` so the series-sync path can copy any pre-existing manual entries, but these are not used to create new entries from the UI.

## Flag display model

The live flag panels are data-driven from the current signal plan. They use static images for Numeral 0–9 class pennants, the preparatory **P** flag, and Code flag **S** (shown while a course is shortened, until all boats stop racing — on both the race sheet and the public competitor page). Flags that are not currently up should not be rendered.

The current model covers normal start-sequence class/P flags plus the shortened-course S flag. Recall, postpone and abandon controls are logged but do not yet render a full flag-board state for X, AP, First Substitute, N and related flags.

## Explaining things without spending the screen on it

Long explanations belong behind a **`?`** beside the heading, not in a paragraph
under it. A race officer reads the paragraph once and then works around it for
the rest of the season, so the prose ends up as distance between them and the
control they came for.

The pattern is markup only — the delegated handler lives in `base.html` and
already covers every page:

```html
<h3>Postpone (AP)
  <button type="button" class="help-link" data-help="apHelp" aria-label="About postponement">?</button>
</h3>
<dialog id="apHelp" class="help-dialog">
  <form method="dialog" class="dialog-close-row"><button class="small secondary">Close</button></form>
  <h4>Postponing a race</h4>
  <p>…</p>
</dialog>
```

What stays on the page is **state and consequence** — "AP is up since 14:02",
"this race has started", "whole minutes only" — because that changes what the
officer does next. What moves into the dialog is the **why** and the worked
example. Applied so far on the race page to the course/start intro, the AP
rules, the GPS sailing-to arrows and the Entries tab's ratings and GPS-finish
explanation, and to the pursuit page's course block.

## The race-document theme

The app's look since v0.275, merged from `experimental/race-document-theme`
after a page-by-page audit. Before it, the app was styled as a
management console — Inter, a 14px radius on everything, soft-shadowed cards, a
blue primary button, rounded-full badges and a gradient wash — which is the
house style of every dashboard built in the last two years and says nothing
about sailing. This branch replaces the visual language without touching a
single template's structure.

The premise is that the app is two things and they should not look alike:

- **Documents** — entries, results, boats, marks, series, settings. Paper
  ground, hairline rules instead of cards, condensed uppercase labels, and
  monospaced tabular figures for every time and rating.
- **Instruments** — the countdown, the flag board, the clubhouse display, the
  on-water page. Near-black ground, oversized tabular numerals, and colour used
  only to mean something. The app already half did this: the countdown box has
  always been dark in a light page.

How it is put together:

- `static/theme_race_document.css` loads **after** `style.css` and overrides its
  surface. `static/theme_fonts.css` declares the bundled faces.
- Three typefaces, all SIL OFL 1.1, served from `static/fonts/` rather than a
  CDN so a race sheet never waits on a third party: **Archivo** (text),
  **Archivo Narrow** (labels) and **IBM Plex Mono** (figures). Archivo and
  Archivo Narrow are one superfamily. `latin-ext` is bundled as well as `latin`
  because Welsh place names need the circumflexed w and y. There is no serif on
  purpose — the first cut used one and read as a word processor, and charts and
  tide tables are set in condensed grotesques anyway.
- There is no theme switch. While it was an experiment the topbar carried a
  **Classic view** toggle backed by an `ro_ui_theme` cookie and an `RO_UI_THEME`
  default; both went in v0.276 once the look was adopted, along with the
  `/ui-theme/<name>` route. `style.css` remains the base layer the theme
  overrides, so the two stylesheets are still all there is to remove.
- The public pages, the clubhouse display, login, the mark-layer phone page, the
  on-water page and the split view build their own `<head>` instead of extending
  `base.html`, so they include `templates/partials/theme_links.html`. Miss those
  and a change stops at the admin pages while every surface the public actually
  sees keeps the old one.

**A real bug found by this work, not caused by it.** Below 1020px the shell
collapsed to `grid-template-columns: 1fr`, and `1fr` means `minmax(auto, 1fr)`
— that `auto` floor is the content's *min-content* width, so one wide table
stretched the grid track to 1043px on a 375px phone and the whole page scrolled
sideways: 585px of it on the race sheet. `minmax(0, 1fr)` fixes it. That fix,
the narrow-screen table scrolling and the long-URL wrapping live in
**`style.css`**, not in the theme layer, so they survive a switch to Classic
view — they are bugs, not styling.

To drop the theme entirely: delete the two stylesheets, `static/fonts/`,
`templates/partials/theme_links.html` and its seven includes, the two links in
`base.html`, and the `UI_THEMES` block in `app.py`. Nothing else depends on it,
which is the point of keeping it a layer.

**Re-shooting the guides.** Every screenshot in `scripts/screenshots/` and
`scripts/ref_screens/` is taken in whichever theme the capture sandbox defaults
to, so a change to the look means re-running the four capture scripts and
rebuilding all four PDFs. The sanitiser used for that renumbers tracker
`unique_id` but **not** the label column, and several of the club's trackers are
labelled with their own IMEI — check `trackers_page.png` before publishing.

## Hardware model

The ProLog-style horn interface uses:

- DTR to fire the horn relay,
- RTS held asserted as the feedback common,
- DCD as the idle feedback contact,
- CTS as the active/manual-horn feedback contact.

When manual input sensing is enabled, do not treat RTS as a horn output.


## Security

The app is designed to sit behind a Cloudflare Tunnel on the public internet. A pre-launch review found no critical/high code flaws (SQL is parameterised, path traversal / zip-slip / SSRF / open-redirect are guarded, subprocess calls are list-form, Jinja autoescape is on). The hardening config lives in `app.py` immediately after `app.secret_key`:

- **Session cookies:** `SESSION_COOKIE_HTTPONLY`, `SAMESITE="Lax"`, and `SECURE` (default on). Secure cookies only travel over HTTPS, so **local http testing must set `RO_COOKIE_SECURE=0`** or the browser won't store the login cookie. In production the browser↔Cloudflare leg is HTTPS, so Secure works even though the app itself speaks http to the tunnel. The test suite's `client` fixture forces `SESSION_COOKIE_SECURE=False`.
- **Response headers** (`apply_security_headers`, an `after_request`): `X-Content-Type-Options=nosniff`, `X-Frame-Options=SAMEORIGIN` (not `DENY` — `split.html` frames same-origin race pages), `Referrer-Policy`, and HSTS when Secure is on.
- **Request-body cap:** `MAX_CONTENT_LENGTH` = `RO_MAX_UPLOAD_MB` (default 64 MB). A very large backup-restore ZIP may need this raised.
- **Login brute force:** `core/loginguard.py` locks a `(client-ip, username)` pair after 8 failures in 15 min. It reads `CF-Connecting-IP` (the real client behind Cloudflare), falling back to `remote_addr`. Cleared on a successful sign-in; the conftest fixture resets it between tests.

A Content-Security-Policy is *not* set — the templates use inline `<script>`/`<style>`, so a real CSP needs nonces and browser testing; it's a documented follow-up. Content that could leak infrastructure (real R2 endpoints/keys) must stay out of committed screenshots and PDFs.

## Deployment scripts

The hut-PC deployment scripts live under `deploy/windows/`. They are intentionally small wrappers around the normal `python app.py` Waitress startup path. Keep them aligned with any changes to environment variables, port defaults or data/runtime paths.

The Scheduled Task runs in the signed-in race-office user session so audio, USB serial horn hardware and cameras remain accessible. The Scheduled Task action uses `powershell.exe -WindowStyle Hidden` directly rather than `cmd.exe`; otherwise a command window remains open on the race-office desktop for as long as the app runs. Do not replace it with a service model without re-testing audio, serial and video hardware.

## Documentation and comments

- Keep `README.md`, `docs/CHANGELOG.md`, and relevant workflow docs updated for user-visible behaviour changes.
- Keep function docstrings short and purpose-based.
- Update comments when behaviour changes; stale comments are worse than missing comments.
- For backup/restore changes, update `docs/DATA_AND_BACKUP.md`, `docs/SETTINGS_AND_ADMIN.md`, backup route tests and any comments explaining temporary files or SQLite connection lifetimes.
- Avoid referring to `races.start_time` as the actual start where the stored value is the first warning signal.
- Avoid reintroducing IRC NS into the UI unless the product requirement changes.

## Manual checks before packaging

Recommended checks for each release:

```bash
python -m compileall -q .
flask --app app routes
RO_COOKIE_SECURE=0 python app.py  # quick manual startup check over local http, then stop with Ctrl+C
# On Windows, also syntax-check deployment scripts and test deploy/windows/status_startup_task.cmd where possible.
pytest -q
```

The package now includes a pytest suite under `tests/`. Install the development dependencies with `pip install -r requirements-dev.txt` before running `pytest -q`.

Before zipping on Windows, check for case-only duplicate paths because Windows treats those as the same file.

## Building a release

The whole build pipeline is version-controlled under `scripts/` (see `scripts/README.md`):

- `python scripts/build_reference_manual.py`, `build_manual.py`, `build_competitor_guide.py`, `build_relay_guide.py` — rebuild the **four** PDF guides into `docs/`. Each reads `VERSION` itself, so the cover stamp cannot be forgotten; `tests/test_pdf_versions.py` checks it was generated rather than typed, and `tests/test_pdf_prose.py` reads the built PDFs back and fails when their prose no longer matches the code. Rebuild all four every release — a guide left out is a guide that starts drifting.
- `python scripts/build_release_zip.py` — package the distributable ZIP (excludes `.git`, `.venv`, `runtime`, `scripts`, DBs, video clips).
- `scripts/capture_*.py` — Playwright screenshot capture; run against a temp app started with `RO_COOKIE_SECURE=0` and point them at it with `RO_CAP_BASE` (`RO_CAP_PASSWORD` supplies the admin login, never hard-coded).

Then `release.ps1` (repo root) tags `vX.Y`, pushes it, and creates the GitHub release with `gh`, pulling notes from `docs/CHANGELOG.md` and attaching the ZIP. Release checklist: bump `VERSION`, prepend `docs/CHANGELOG.md`, add the new entry to the README's recent-releases window and drop the oldest (`tests/test_readme_current.py` holds it to five, and to this version at the top), rebuild all four PDFs + ZIP, `pytest -q`, commit, push, `.\release.ps1`.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

## PTZ manual test hold

Settings-page PTZ preset tests call the same ISAPI preset helper as automatic switching, but a successful test also sets a short in-memory `manual_hold_until` timestamp. The start scheduler checks this before queuing idle/recording preset changes. This prevents **Save and test race preset** being immediately overwritten by the normal "no race in start/finish window" idle preset request. The hold is deliberately short and is cleared on settings save/reset.
