# Race officer workflow

This guide describes the normal race-day flow.

**A `?` beside a heading opens the explanation.** The race page keeps the
controls at the top and the reasoning one click away, so what stays on screen is
what changes what you do next — "AP is up since 14:02", "this race has
started", "whole minutes only". The *why* and the worked examples are behind the
`?`: how the warning signal relates to the first gun, what each postponement
signal means, why a rating edited on the Entries tab cannot rescore an earlier
race. Everything in those popups is also in this guide.

## 1. Before racing

Open **Settings** and check:

- Horn serial port, default blast duration and ProLog-style manual horn input sensing if used.
- Central audio is enabled/tested if VHF/audio announcements will be generated from the race-office PC.
- Weather station is enabled and reporting live wind.
- Video recorder is enabled, camera source is correct, and buffer segments are being written.
- IRC and YTC listing URLs are correct if you will look up new boats.
- Polars required for course recommendation are available.
- Course chart background/overlay URLs are correct if online charts will be used.

Open **Boats** and check:

- Active boats are correct.
- IRC TCC values are present for boats that should appear in IRC results.
- YTC ratings are present for boats that should appear in YTC results.
- IRC NS is not used by the current race workflow and is not shown in the UI.

## 2. Create or select a series

Open **Series**, then **Add series** for a new one. The add page carries the whole
setup, so a series is configured in one pass rather than created blank and then
edited:

- name and description,
- the discard profile and the minimum races to constitute the series,
- up to three IRC and three YTC rating-band classes, each with a name, a
  Numeral 0–9 class flag and optional rating bounds,
- the default start plan, up to six starts.

**No rating bands are ticked to begin with.** Tick the ones the series uses; an
untouched form creates a series with no bands, which scores as one fleet per
rating rule.

Ticking a band adds it to the **Default start plan** below straight away, on
every start, and renaming it renames it there too &mdash; so the bands and the
starts that use them are set in one pass. Untick a band from a start to give it
a start of its own.

It is the same form as **Edit series details** on the series itself, so anything
here can be changed later. Changing the discard profile or the rating bands
re-scores every race in the series at once, so it is a larger change once a
season is under way than before it starts.

A race is assigned to a series when created, or later from the race page's
**Course & start** tab.

A series can also be **deleted**, from the Series list or from the bottom of *Edit
series details*, but only when no races are in it &mdash; a series still holding
racing shows *In use* and says how many races are in the way. Move them to another
series (or to none) on each race's **Course & start** tab, or delete them, and the
button appears.

## 3. Create a race

Open **New race**.

Race creation is deliberately minimal. Course and first warning-signal time are set later on the race sheet because the race officer may not know the final wind/course when the race sheet is first created.

If the race is created inside a series, existing competitors from that series are added automatically to the new race.

## 4. Course & start tab

Use **Course & start** to:

- Set or change race name.
- Assign or change series.
- Set the **first warning signal** time in whole minutes; seconds are forced to `00`.
- Review the default series start plan or untick **Use the series default start plan** and set a race-specific start plan.
- Select a fixed CHPSC course. Until somebody does, the race reads **Course not
  set** here and on the competitor page, and the spoken VHF course announcement
  stays silent — a new race stores a fixed course number as a fallback so the
  chart and the leg analysis have geometry to work with, and that is not the same
  thing as a race officer having chosen it. Saving this tab is choosing.
- Open **Recommend course** to choose a recommended fixed course using live hut wind and selected polar.
- Open **Build manual course** to create a made-up course from marks.
- Choose the polar used for course analysis.
- Choose the **finish line** the race is sailed to (see below).
- View course chart and live leg analysis.

Compound marks such as **Y** and **A** can be added as normal course marks. The course board still shows `Yp`/`Ys` or `Ap`/`As`, but the chart and leg analysis expand them into their two physical corner points in the correct rounding order.

When a made-up course is active, the fixed course selector is greyed out and the race displays **Made up course**. Clear the made-up course to return to fixed-course selection.

### Choosing the finish line

Almost every race finishes where it starts, on the club line between the CHPSC
Bridge window and the ODM (mark **O**), and the **Finish line** selector can be
left alone.

Some races are not sailed to it. An **ISORA** passage race finishes on the
transit between the Pwllheli Fairway Buoy (mark **F**) and the bridge at Plas
Heli, bearing 297 degrees magnetic — a line about 1.6 km long whose shore end is
nearly 800 m from the club one, and which the ISORA instructions point out is
*not* the line used for starts at Pwllheli. Pick it from the selector and
everything follows: the chart draws it, GPS finishes are detected on it, and the
competitor page and clubhouse display show it.

**The start line does not change.** Races start on the club line whatever they
finish on, so with a different finish selected the chart draws two lines, marked
*Start line* and *Finish line*.

Boats **cross** the finish line rather than rounding the mark at the end of it.
When a course's last mark is the finish line's own mark — O on the club line, F
on the ISORA one — no rounding is required there; the boats sail to the line and
cross it. A course that ends anywhere else, including a shortened one, still has
to reach the line.

The seaward end of each line is a mark, so **re-measuring it moves the line with
it**: ping F from the water and the ISORA finish follows. The shore ends are
fixed positions, being buildings rather than marks.

## 5. Add entries tab

Use **Add entries** to add boats:

- Add all active boats.
- Add one boat from the boat database.

Boats are added to a race from the boat database. When a boat is added, its current IRC and YTC ratings are copied onto the race entry. Results use these race-entry ratings, not a live lookup back to the boat database.

For a race in a series, adding an entry to one race also adds the same boat/entry to the other races in that series.

## 6. Start console & log tab

Use **Start console & log** to manage the start:

- Review the scheduled signal plan for all configured starts.
- Confirm the displayed flags; the graphical panel shows only flags currently up.
- Fire a manual horn if required.
- Log individual recall, general recall, postpone or abandon events.
- Review horn/race log.

The automatic horn/audio defaults are configured in **Settings → Horn settings & Test → Start automation and central audio**.

## 7. Entries & finish times tab

Use **Entries & finish times** to record finishes, status changes and race-specific ratings.

- The blue **Finish** button is at the front of each entry row.
- **Finish** fires the horn, records the finish time, logs the event and schedules a finish video clip if recording is enabled.
- Editable IRC and YTC fields on each entry are the ratings used for that race's results.
- Use status controls for DNF, DNS, RET or other non-racing outcomes.
- Open **Show live finish camera** if the RO wants to watch the finish camera in the same tab. With a live-stream URL configured it shows stills while the relay stream starts, then live video — which lags the water by a few seconds, so time finishes from the horn, not the picture (see [`VIDEO_RECORDING.md`](VIDEO_RECORDING.md)).
- The horn/race log is also shown at the bottom for assigning manual horn events as finishes.
- With GPS tracking enabled, a single **GPS finish detection** strip above the
  list carries **Arm GPS auto-finish**, **Auto-confirm (unmanned)**, **Apply**
  and the current state. Detections waiting for an answer replace that state
  with a review table offering **Confirm** and **Dismiss** on each.

When all entries are no longer `RACING`, the header timer changes to **Race finished**.

Before the race's first warning signal, entries are shown as **PRESTART** rather than `RACING`; they change to `RACING` once the warning-signal time is reached. This is a display label only — the stored status is `RACING` throughout, so elapsed times, results and GPS finish detection are unaffected.

## 8. Results tab

The Results tab shows IRC and YTC results, split by configured rating-band class when classes are available.

- IRC includes only boats with a race-entry IRC TCC.
- YTC includes only boats with a race-entry YTC rating.
- IRC TCC values are displayed to three decimal places.
- Start and finish video links appear when available.

## 9. Remove Race tab

Use **6. Remove Race** only when a race sheet was created in error or needs to be completely removed. The tab lists the race activity that would be lost before deletion, including whether a first warning time is set, whether a course is selected, whether the race appears to have started, entries, finishes, scoring/status records, race log events, video clips and series membership.

The delete form requires confirmation. Deleting a race removes the race sheet, its entries, finish times, race log events and race video clip records/files. Because the race row is deleted, it is also removed from any series it belonged to. This cannot be undone; back up `data/` first if there is any chance the race will be needed later.

## 10. Races page

Use **Races** in the side menu to find races grouped by series. Standalone races are shown in a separate roll-up section.

## 11. Competitor page

Use the pop-out public competitor page for a read-only display.

The page is organised into tabs under the race header. During the race those are **Entries**, **Chart** and **Course analysis**; a **Leader board** tab appears with the provisional results once every boat has stopped racing, and is then the tab shown first.

The **Chart** tab is the one competitors spend the race on. It shows the fleet on the course and winds back through everything recorded so far, with a translucent leaderboard rolling up over it: the order on the water, or an **estimated IRC/YTC corrected** order projected from each boat's pace. That estimate is labelled as such on the page and is not a result — the Results tab and the published scores remain authoritative.

The competitor landing page separately shows start-hut wind, wind history and the optional live camera.

For a television in the club bar there is a separate display at `/bar` — see [`BAR_DISPLAY.md`](BAR_DISPLAY.md). Full detail on the competitor pages is in [`PUBLIC_COMPETITOR_PAGE.md`](PUBLIC_COMPETITOR_PAGE.md).

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.


## Add entries tab layout

The race page **Add entries** tab includes a simple read-only list of entries already in the race, followed by the bulk-add and database-add controls. This helps the race officer avoid duplicate entries while adding boats.

## Start console horn display

The race page **Start console & log** tab shows horn output and manual horn-input configuration on one line. Manual horn-input polling reports short active/idle text rather than the longer ProLog wiring diagnostic; detailed wiring behaviour remains covered in the hardware documentation.

## Postponing a start (AP)

If the wind dies, the line is not ready or the fleet is not there, **postpone —
do not move the start time**. Moving it signals nothing to the fleet, and if the
sequence is already running it drops the rest of it: boats hear a warning, then
no preparatory signal and no gun. It is on the **Start console & log** tab,
which is the tab that is open before a start.

Choose the signal and press **Postpone**. The app:

- sounds **two horn blasts** and makes the announcement;
- flies **AP** in the flag panel on the race page, the competitor pages and the
  clubhouse display, and stops the countdown counting towards a gun that is not
  going to be fired;
- **holds the start sequence**: no warning, preparatory or starting signal
  sounds while the flag is up. The race keeps its scheduled time — nothing is
  rescheduled behind your back;
- records it in the race log, with the time the flag went up.

Three signals, which are the three RRS 27.3 gives a race committee before the
start:

| Signal | Meaning |
| --- | --- |
| **AP** | Races not started are postponed. The warning signal will be made one minute after AP is removed. |
| **AP over H** | Races not started are postponed. Further signals ashore. |
| **AP over A** | Races not started are postponed. No more racing today. |

When the wind is back, say **when AP comes down** — whole minutes only, as
with the first warning signal, and the next whole minute is filled in for you.
**The warning signal is then one minute after that, and the gun five minutes
after the warning**, so choosing 14:20 gives a warning at 14:21 and a first gun
at 14:26.

**It has to be at least a minute and a half away.** One minute before the flag
moves the app announces *“Answering pennant will be lowered in one minute”*, so
there has to be room for that; a time closer than that is refused, and the
message says the earliest that works. The flag stays up on every display until
the minute you chose, because that is when it comes down on the mast, and the
**single horn sounds then**, followed by *“Answering pennant down”* with the
warning and gun times. Change your mind before it comes down and you can simply
set a different time — the announcement follows the new one.

That horn sounds whether or not start automation is on: putting AP up, calling a
shortened course and taking AP down are all signals **you** asked for, and the
app makes them. Only the signals it makes on its own — the warning, preparatory
and starting guns — wait for automation to be switched on.

After **AP over H** you are asked for the new warning signal time, because
“further signals ashore” means the next signals are made later, ashore — there
is no one-minute warning to count from. After **AP over A** there is nothing to
lower: no more racing today is a decision, not a pause.

**The signal plan shows the postponement.** While AP is up, the signals due
before it comes down are struck through and marked *held* — they will not be
made — and two rows are added for what will: the announcement a minute
beforehand, and the flag coming down with its sound. A plan that leaves out the
next thing you are going to hear is worse than no plan, because it gets read
instead of thought about.

**The start video follows the start.** The start clip is booked as soon as a start
time is set, and its builder waits for that moment and cuts it out of the rolling
buffer. Postponing cancels it — the fleet will not be starting then, so all it
would catch is an empty line — and lowering AP books a new one for the gun that
actually happens. The clubhouse camera holds off too: it cuts to the start hut for
two minutes either side of the gun, and there is no gun while AP is up.

**Everything is in the Horn and race log**, on the race sheet and in the start
console: the flag going up with which flag it was, the time it is due to come
down, the announcement, and the flag coming down. The log updates itself, so a
postponement made from the water appears in the hut without anybody refreshing.
That log is what a protest or a question the following week is settled from.

**AP postpones races that have not started.** Once the first gun has gone the
button is not offered at all, and the page says why: stopping a race under way is
abandonment (flag **N**), which the app does not signal — if the wind has died
mid-race, shorten the course instead. A race that was postponed *before* its gun
can always still be lowered, whatever the clock says, so the flag can never get
stuck up.

## Replaying a race in the bar

On the race sheet's **Results** tab, **Replay this race in the bar** puts the race on
the clubhouse television for people coming in from sailing. Anybody signed in can do
it — it changes what a screen shows and touches no race data — and the television
switches itself over within a few seconds. Nobody needs to go near it.

It runs from the **warning signal to the last finish** at six times life, with the
chart, the leaderboard and the wind exactly as they were, dropping to **normal speed
whenever a video plays** — the start, the finishes, anything else recorded that day.
Beside the button it says roughly how long it will take, which for a club race is
usually fifteen to twenty-five minutes.

At the last finish it holds on the finishing order for a minute and a half and then
goes back to the current race by itself. **Stop the replay** on the same tab ends it
early. Only one race replays at a time; choosing another replaces it.

A race needs a start time and at least one boat finished, or there is nothing to
replay and the tab says so.

**Not every clip is shown.** Clips carry a minute either side of their moment, so a
fleet finishing close together produces clips that are mostly the same video — one
club race was six minutes long with twenty minutes of clip. The replay skips the
repeats and joins an overlapping clip where it has got to. Every clip is still on the
race sheet; this only affects what the bar watches.

**The clock follows the picture.** While a clip plays the replay runs at normal speed
locked to that video, so the fleet on the chart is where it was in the frame on
screen. Clips are cut from the recorder's rolling buffer in whole segments, so a clip
file starts a little earlier than the minute that was asked for — up to twenty
seconds on the hut's settings. Clips built from v0.268 record where their own footage
begins and the replay uses it; on races recorded before that it places them within a
few seconds, so the camera's burnt-in clock and the replay clock can differ slightly
on an older race.

## Shortening the course

If the wind drops (or time runs short), use the **Shorten course** tab (between *Start console & log* and *Entries & finish times*) to shorten the course at a mark. Pick a mark from the course sequence and press **Call shortened course**. The app:

- sounds **two horn blasts**, then (once the horns have finished, so it is not drowned out) makes a central-audio announcement — *"Shortened course called on mark &lt;mark&gt; — after this mark proceed to finish"* — repeated a few seconds later;
- displays International Code flag **S** (blue square on white) in the flag panel on both the race page and the public competitor page, and keeps it up until all boats are no longer racing;
- records the decision in the race log and shows a **Shortened course** banner on the race page and the public competitor page;
- truncates the displayed course (and the predicted-time / leg analysis) so it ends at that mark, with **→ Finish** shown after it, and draws the final leg on the course chart as a dashed line from that mark directly to the finish line.

Boats round the chosen mark and then sail directly to the finish line as normal; finish times are recorded on the *Entries & finish times* tab exactly as for a full course. If you call it in error, use **Clear shortened course** to undo. The announcement uses the hut central audio, so it is heard over the VHF/PA when central audio is enabled in Settings.

## Pursuit races

A **pursuit race** is a different kind of race, selected with the **Race type** option when you create a new race. It runs for a fixed period, the slower boats start first and the faster boats later, so on handicap they would all finish together. Choose **IRC** or **YTC** as the rating system that sets the start times.

Setting one up:

- On the **New race** page choose **Pursuit**, pick the rating system and the fixed period (in minutes), and optionally add all active boats.
- On the race sheet's **Course & start** tab, set the first warning-signal time (the first, slowest boat starts five minutes later) and confirm the period and rating system, then save. Each boat's start time is computed automatically and recomputed whenever you add or remove a boat. (Until the first warning time and period are set, boats show "set start time" rather than a time — this is not the same as a missing rating.)
- Choose a course the same way as a standard race: a fixed numbered course, a wind-based **Recommend course**, or a made-up course from **Build manual course**.
- The slowest-rated boat starts first; each faster boat is delayed by `period × (1 − slowest speed / boat speed)`. A boat that genuinely has no rating in the chosen system is entered but gets no start time and is flagged with a warning.

Running it:

- The **Start console** tab shows the start ladder — every boat and its start time, with the next boat to start highlighted and a live countdown. Horns fire automatically: the five-minute warning sequence before the first start, one start signal at each boat's start time, then the finish signal at the end of the period. **Fire horn now** is available for a manual signal.
- The central audio also **announces the next boat(s) to start** by name — 10 seconds after the previous boat started — so competitors get a spoken heads-up. Boats starting within 20 seconds of each other are named in one announcement. These appear in the signal plan too.
- There is a **start video** for each start time and no finish video.

Finishing:

- At the finish signal, judge the boats by their order on the water and record it on the **Finish & positions** tab (a position number and status for each boat). There is no time correction — the finishing order is the result.
- The **Results** tab shows the finishing order. A pursuit race in a series scores into the series standings by finishing position, like any other race's places.

There are no classes in a pursuit race, and there is a single finish signal rather than individual finish times.

## GPS tracking and automated finishes

If the club is running GPS trackers (Queclink units reporting to the Traccar server on the relay), you can watch the fleet live and let the app help with finishes. Competitors see the fleet too, on the public race page's Chart tab, whenever GPS tracking is enabled in Settings — leaving it disabled is what keeps it private. Tracker *management* stays in the race office.

Setting up (once): on the **Trackers** page (bottom of the side menu — available to race officers), add each tracker by its IMEI and assign it to a boat. A boat's track always stays with the boat, so if you swap a tracker between boats the history follows the boats correctly. Each tracker's **last-reported** time shows with a green/amber/red dot (green within 5 minutes, amber within the hour, red after), updating live.

On the water:

- The race sheet's **Course & start** tab shows tracked boats moving on the course chart, plus a **Position on the water** list ordered by how far round the course each boat is — marks rounded (e.g. `6/10`), the next mark and the distance still to sail. Every entered boat is listed; one without a tracker shows dashes and *No tracker*. This is the order on the water, **not** corrected for handicap.
- Competitors see the same positions: the public race page's **Chart** tab shows the tracked boats sailing the course, and can be wound back through the race so far. Under it they can pick a **corrected-time** order — IRC or YTC — projected from each boat's pace, which the page labels an estimate and not a result. Detail in [`PUBLIC_COMPETITOR_PAGE.md`](PUBLIC_COMPETITOR_PAGE.md).
- If the club bar has the **clubhouse display** up (`/bar`), it shows the same race on a television with no controls: the chart zoomed to the boats still racing, the leaderboards cycling beside it, and the start-hut camera at the start, at each rounding of the ODM and at each finish. It needs nothing from the race office. See [`BAR_DISPLAY.md`](BAR_DISPLAY.md).
- Automated finishes are on for a new race: **Arm GPS auto-finish** is ticked on the *Entries & finish times* tab. **Auto-confirm** is **not** (v0.276) &mdash; detection proposes and a person accepts, which for a manned race costs one click and catches a wrong finish before it is in the results. Either box can be changed there. The app follows each boat through the course marks in order and takes its finish once it has rounded them all and crossed the finish line. On a course that reuses the ODM mid-course, or a shortened course, the finish is only taken on the final line crossing.
- Tick **Auto-confirm** and each detected finish is recorded straight away (still reviewable and editable afterwards) &mdash; tick it for a race with nobody in the hut, which is what the on-the-water page does by itself. Left off, detections wait as **proposals** — boat and time, with a link to the finish video to check against — for you to confirm or dismiss. GPS gives the approximate time and order; the finish video remains the arbiter. Set a **frequent tracker reporting interval on race days** or finish order between close boats will be too coarse.

Trackers are paired with boats on the **Trackers** page, and nowhere else — the *Entries & finish times* tab has no tracker control. Full detail is in [`TRACKING.md`](TRACKING.md).

## The Virtual Race Officer

Everything above is done on the race sheet, in the hut. It can also be done from
a boat, by typing plain English at `/vro` — create the race, set its start
and course, enter the boats, shorten the course, ask how the fleet is getting
on. Each command is read back in full and nothing happens until you agree to it,
and every one of them goes through the same code the race sheet uses.

It is a per-user permission (**VRO**, in *Settings → Users*), granted
one account at a time and implied by no role. Arming the start sequence is
deliberately not offered there at all.

Full detail, including what it will not do and what happens when the hut cannot
reach a model, is in [`VIRTUAL_RACE_OFFICER.md`](VIRTUAL_RACE_OFFICER.md).
