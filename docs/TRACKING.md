# Yacht tracking and automated finishes

The app can show the fleet's live GPS positions on the race course chart and detect
finish-line crossings, either **proposing** a finish for the race officer to confirm or
(when armed for it) **auto-confirming** an unmanned finish. Every GPS finish is tagged and
stays reviewable against the finish video and adjustable by hand — GPS is never trusted blind.

Competitors see it too: when GPS tracking is enabled, the **public race page** shows the tracked
boats on its course chart and each boat's progress in the Entries table. Tracker *management* stays
inside the race office.

## Public competitor page

With tracking enabled, `/public/race/<id>` shows each tracked boat on the course chart and adds
**Marks / Next / To go / SOG / Fix** columns to its Entries table, refreshing every few seconds.
A boat with no position shows dashes across the row and says why: *No tracker* when there is no
tracker on the boat, *Not reporting* when there is one and nothing has been heard from it in the
last hour. Nothing is shown when GPS tracking is turned off in Settings, so the feature can be kept
private simply by leaving it disabled.

## How it fits together

Boats carry Queclink **GL521MG** LTE trackers. They report to a **Traccar** server on the
relay; the hut app pulls positions from Traccar over the internet (outbound only — no open
port on the hut PC). See [`deploy/live_stream/README.md`](../deploy/live_stream/README.md)
section 7 for the one-time relay/Traccar setup and the single inbound port the trackers need.

```
GL521MG ──▶ Traccar (relay) ──push (forward)──▶ hut app ──▶ map + finish detection
                            └─pull (REST poll)─┘   safety net + back-fill
```

Positions reach the app two ways, and both are wanted:

- **Push (preferred).** Traccar's position forwarder POSTs each fix to
  `/api/track/ingest` the moment it decodes it, and **finish detection runs on receipt**.
  This is what keeps the automatic horn and the competitors' view prompt. It is off until a
  **Push ingest token** is set in Settings *and* the matching `forward.header` on the relay —
  see [`deploy/live_stream/traccar-forward.xml`](../deploy/live_stream/traccar-forward.xml).

  **Check it is actually working.** A token that does not match the relay's fails *silently*:
  the app carries on polling, every tracker reports, and nothing looks wrong except that the
  horn is a poll interval late. So the **Settings → GPS tracking** status box says which of
  three states you are in (v0.193): *not configured — positions arrive on the next poll*,
  *configured, but no fix has arrived this way yet* (go and check `forward.header`), or
  *working — N fixes received, last just now*. The same numbers are in `/api/track/status`
  as `ingest_configured`, `forward_count` and `last_forward_age`. The counts are since the
  app last started, so they reset on a restart.
- **Poll (safety net).** The app keeps polling Traccar. That covers a relay that is not
  forwarding, a stalled forwarder, and — since v0.189 — it **back-fills gaps**: the live poll
  only ever returns each device's *latest* fix, so an app outage used to leave a permanent
  hole in the track. The poller now asks Traccar for the missing range and fills it in.

With push working you can raise the poll interval to about 30 seconds; it is no longer
carrying the latency.

**Coverage & accuracy reality:** tracking is cellular (LTE Cat M1/NB2 with 2G fallback), so it
thins offshore; and the GL521MG is a battery asset tracker built for very long standby, so its
default reporting is far too coarse for finish order. Set a **frequent reporting interval on
race days** — and expect to **charge the units between race days**, because a rate fast enough
to time a line crossing costs a large multiple of the standby drain. Treat GPS finishes as
proposals to confirm: the finish video is the arbiter.

**What the delay is made of.** A recorded finish *time* does not depend on how quickly the
app hears about a fix — crossings are interpolated between fix timestamps — but the automatic
horn does, and so does what competitors see. Push removes the app's share of that delay
(measured at 0.02–0.32 s in testing, against 0–5 s of poll wait). What remains is the
tracker's own reporting interval and the cellular hop, which is why an automatic horn will
always be a beat behind a race officer pressing the button on sight.

## The chart: live, and a replay of the whole race

With tracking on, the public race page's **Chart** tab is one view that does both. It opens at
the **latest positions** — during a race that is simply the live chart — and winds back through
everything recorded so far. Drag the timeline under the map or press play (1x / 4x / 16x / 60x);
each boat trails the last ten minutes of its track, and clicking one highlights it. The order on
the water beside the map follows whatever moment is shown.

While you are at the live edge it keeps up on its own, asking every ten seconds for **only the
fixes it does not already have**. The moment you wind back it stops dragging you forward and lets
you look around; the window keeps growing behind you, and **Live** returns you to the front. Play
forward far enough and it rejoins live by itself.

There is no separate Replay tab: two map tabs showing the same fleet was one too many. Old
`#ptab-replay` links land on the chart. With GPS tracking switched off the tab is the plain course
chart it has always been.

### How it works

Every fix is kept (Settings → *Position history retention*), so a race can be replayed
afterwards rather than only watched live. Two endpoints serve it:

- **`/public/race/<id>/track`** (and `/api/race/<id>/track` for the race office) returns the
  whole race in one response: each entered boat with its recorded fixes as compact
  `[t, lat, lon, sog, cog]` rows. The window runs from the **warning signal** — the approach
  to the line is the interesting part — to a couple of minutes after the last boat finished,
  or to now for a race still going. A real 199-minute race with one tracker is about 88 KB.
- **`/public/race/<id>/positions?at=<epoch>`** returns the fleet **as it stood at that
  moment**: positions, marks rounded, next mark, distance to go and the order on the water.

The **order on the water** comes down with the track too, pre-computed by the server at every
few seconds of the race. The viewer then shows the order for whatever moment is on screen as a
local lookup — no request per frame, and nothing trailing the boats on the chart. Computing it
server-side matters: mark rounding, distance-to-go and finish detection stay the app's own
course walk rather than a second version in the browser that could quietly disagree with the
live view. The whole series is one forward pass of each boat's track, so it costs about the
same as a single leaderboard however many snapshots it holds (a 24-minute race: 291 snapshots,
33 KB, built in under a tenth of a second).

The second is the useful trick. Course progress was already computed by walking a boat's list
of fixes, so asking for the fixes *up to* a moment gives the fleet as it was then — the mark
rounding, distance-to-go and interpolated finish detection are the same code, not a second
implementation that could disagree with the live one. Two things do change with the clock: a
boat only reads FINISHED if it had actually finished by then, and "last fix age" is measured
from the replay clock rather than from now.

### What bounds the reply (v0.276)

Until v0.276 nothing did. The window is drawn from the gun to the last finish, so it is only
ever as sensible as the finish times it is drawn from — and one afternoon a finish was stamped
**352 days** after its race's gun, a mis-typed manual entry since corrected. The endpoint did
exactly what it was told and assembled a year-long replay. A single request took gigabytes, and
the hut has 8 GB.

The obvious diagnosis is "too many fixes", and it is wrong, which is worth recording because it
is what a bound would have been sized against. Measured: a fix costs about **43 bytes** a boat
once it is a Python row; a **board snapshot costs about 470**, and the order-on-the-water board
is sampled every five seconds across the whole window *whether or not any boat reported in it*.
That is roughly **1 MB per hour per three boats before a single fix is counted**. The board is
the expense, and the board is a function of the window alone.

So there are two bounds, doing different jobs:

- **`REPLAY_MAX_WINDOW_S` — twelve hours** (`core/track.py`, applied in `race_track_window`).
  This is the one that matters, because it bounds the board and the wind series as well as the
  fixes. Twelve hours is not a performance setting: it is the line between a long race and bad
  data. The longest race the club has ever sailed is **2 hours 40 minutes** (the median is 1h
  8m), so the clamp sits at four and a half times the worst real case and cannot touch a real
  replay. It is the **end** that is clamped, never the start — a race left with a boat stuck on
  RACING since March would, if you kept the most recent twelve hours instead, show an empty sea
  and cut the racing out entirely.
- **`REPLAY_MAX_FIXES` — 60,000 across the fleet, floor 1,000 a boat** (applied in
  `race_track_history`). The window bounds time; it cannot see **rate**. Every club tracker
  reports every five seconds today — the busiest real unit managed 1,768 fixes in its busiest
  hour — but that is a device setting, not a law, and one reconfigured to 1 Hz would put five
  times as much through the same bounded window. Fixes over budget are thinned **evenly**, each
  boat always keeping its first and last, so a track still starts and ends where the boat did.
  Evenly rather than truncated: a track that stops in the middle of the bay reads as a boat that
  retired.

At the rates the club actually runs, the budget never bites — a 2h40 race at five seconds is
about 1,900 fixes a boat, against a nine-boat share of 6,600. When it does bite, the replay
plays at six times life, so five seconds becoming six is not something an eye can see.

**Nothing here can change a race result.** This endpoint is read by the replay viewer and by
nothing else. Finish detection walks `positions_for_entry_since` directly, with no upper bound
at all, and is deliberately left that way — that is the same distinction that let the second
boat of the 2026-08-08 night race finish correctly while the chart was frozen.

**And it says so when it thins.** The reply carries `thinned`, `fixes_dropped` and a
`recorded_fixes` count on every boat. A cap nobody is told about reads as "this is the whole
track".

## Estimating a finish time mid-race

The public chart can rank the fleet on **corrected** time while the race is still on (see
[`PUBLIC_COMPETITOR_PAGE.md`](PUBLIC_COMPETITOR_PAGE.md)), which means projecting a finish time
for every boat still racing. Each snapshot in the pre-computed series therefore carries **three**
projections per boat, and the viewer picks whichever the competitor asked for. Whichever is
picked drives the **ranking and the times printed beside it**: a board whose order came from one
estimator and whose finish times came from another contradicts itself in front of the competitor
reading down it.

- **Average pace since the start** (`estimate_elapsed_by_vmc_start`) — *the default*. How much of
  the course the boat has covered, and how long that has taken: `elapsed x (course / covered)`.
  No wind, no polar, no window.
- **Recent pace** (`estimate_elapsed_by_vmc`). How much distance-to-finish the boat has actually
  closed over `VMC_WINDOW_S` (**twenty minutes**), converted to knots and carried over what is
  left. A window rather than instantaneous VMG so that a tacking duel, where VMC on any one tack
  is poor, does not read as the boat having stopped.
- **Polar pace factor** (`estimate_elapsed_by_pace`). `core.courses.course_leg_analysis` gives a
  target time per leg from the boat polar and sail chart. A boat's *pace* is its elapsed time
  divided by the target time for what it has sailed — including a pro-rata slice of the leg it is
  on — and the projected elapsed is the whole course's target time at that pace. The only method
  that knows the legs ahead are a different point of sail from the legs behind.

**Why the simplest one is the default**, having been dismissed here for years as "biased by
whatever the boat has just been doing". The bias is real and it does not matter. Measured over
the night race of 8 August 2026, average-pace-since-the-start had a *larger* median error than a
windowed VMC (58 minutes against 28) and called the finishing order right in 99% of snapshots
against 63-83%. Its errors are **common-mode**: at 30 minutes the three boats were out by +153,
+162 and +166 minutes, all having just sailed the same slow first leg. A board ranks
*differences*, so a bias every boat shares cancels, and a per-boat one does not. Accuracy is not
what a leaderboard needs.

**Why not the polar factor**, which is the better instrument in principle. It needs a wind, and
the club measures wind on the hut, which is not where the boats are. The pace factor is a ratio,
so a wind *speed* error largely cancels between its numerator and denominator — 14% spread over
8 to 26 knots on that course. A *direction* error does not, because it changes which legs are
beats: 44% spread over all directions. Direction is precisely what a headland bends. For a short
club course in sight of the hut it remains sound; for anything round Trwyn Cilan it is not.

Guards keep a bad number off the page. Nothing is estimated in the first `ESTIMATE_AFTER_S` (ten
minutes) of racing — the YB Tracking convention, and sound, because before the first mark a
boat's pace is mostly which end of the line it started. A boat unheard from for
`ESTIMATE_STALE_S` (three minutes) gets no estimate rather than one from a stale position. A boat
whose distance to the next mark has *grown* over the window gets no recent-pace estimate rather
than a finish time in the last century. An estimate that has the boat averaging less than
`MIN_PLAUSIBLE_SPEED_KN` for the rest of the course is dropped: the five-minute window this
replaced once produced a finish estimate of **32218 minutes — twenty-two days** — because a boat
momentarily not closing divides by nearly nothing, and that one implies about 0.01 kn.

That guard was first written as a ceiling of six times the elapsed time, which is the wrong unit
and cost the club a race. Early on, the true answer *is* many times elapsed — ten minutes into a
seven-hour race the ratio is 42 — so the board stayed blank until 77 minutes when it could have
answered from 10, and the estimate being suppressed at 10 minutes was 6.0 hours against an actual
6.9. Implied speed is what separates a sound early guess from nonsense; the ratio to elapsed only
measures how far through the race you are. By window, the worst case was 32218 minutes at five, 994 at ten and 425 at
twenty, which is why the window is twenty. A finished boat's "estimate" is simply its real
elapsed time.

### The wind gauge on the chart

`race_track_history` ships a `wind` series with the track: `[t, twd, tws, gust]` rows from
`weather_samples_between`, thinned to one every `WIND_SERIES_STEP_S` (30 s). Wind for a past race
is there to be had because `race_wind_retention_windows` exempts an hour either side of every
race from the 24-hour purge, so this works for a race sailed months ago as well as one happening
now.

The chart's gauge is the shared `partials/wind_gauge.html` dial, included with `gauge_manual=1`.
That marks it as driven by something else, and `wind_gauge.js` skips such gauges in its live poll
— otherwise a replay of last month's race would show this afternoon's wind a few seconds after
you scrubbed to it. `race_replay.js` looks up the reading at the playback clock and calls
`window.WindGauge.setInstrument`.

The reading is the last one **at or before** the moment shown, not interpolated: wind is a
measurement every half minute, not a track, and averaging two readings across a shift would
invent a direction nobody recorded. A reading more than ten minutes old is treated as no reading
and the gauge hides itself.

### Boats are compared at the same instant

Trackers do not report together. In race 65 the fleet ran at 2 s, 10 s and 61 s, and with
dropouts the worst gap between two boats' last-known positions averaged **136 seconds** and
reached 306. Ranking each boat's latest fix against the others is comparing boats at different
moments: at 6 kn, 136 s is 420 m of pure staleness, enough to invert an order. The chart looked
right because it interpolates between fixes; the line-honours board visibly swapped boats round
while nothing on the water changed.

Every boat is therefore carried forward from its last fix on its last known course and speed
(`dead_reckoned`) to the moment being shown — live, and to each snapshot's own time in the
replay, so the corrected boards get it too. Measured against post-hoc interpolation over race
65's first leg, the projection's own position error was 7 m median and 101 m at the 90th
percentile, against the 935 m the staleness was worth; the order was exactly right in 71% of
moments against 51%, and the board changed order 3 times against 16.

Rewinding every boat to the last moment they had *all* reported was tried and is worse: it is
exact for the moment it shows, and that moment is 136 s ago, so it got the order right 29% of the
time — worse than doing nothing. The projection is capped at `PROJECT_MAX_AGE_S`, kept equal to
`ESTIMATE_STALE_S`, past which a boat is missing rather than quiet and is left where it last
actually was. The long-term fix is trackers reporting often and equally; this makes the board
readable until there are some.

Ratings are turned into a **factor** server-side by `entry_rating_factors` — what one second of
elapsed corrects to, via the same `rating_from_entry_for_result` / `corrected_seconds_for_result`
pair the published results use. The browser only multiplies, so there is no second copy of the
correction formula to drift from the results table.

### How good is it, actually

`scripts/verify_leaderboard.py` answers that with numbers rather than opinion. It sails a fleet
offline under the polar — beating and gybing, each boat at a different percentage of it — writes
the track into the database as a race that has been run, and then asks the estimator what it would
have said at every five-second step against the finish each boat actually achieved.

Five boats spread over 100% to 82% of a J/122 polar, course 1 in 12 kn from 020°T (four beats,
two runs), median absolute error on the projected finish time:

| minutes into the race | polar pace | VMC |
|---|---|---|
| 10–15 | 17.4% | 7.2% |
| 15–30 | 7.2% | 11.3% |
| 30–45 | 1.5% | 4.4% |
| 45+ | 0.7% | 1.3% |

And the question that actually matters — was the *order* right? Pace had the final IRC order at
74% of snapshots and the YTC order at 82%, settling for good at 32 minutes; VMC managed 61% and
70%. Repeating it with the fleet spread across points of sail as well as speed (`--upwind-bias`,
so one boat is 8% quicker upwind and 8% slower down) barely moved the pace figures.

Two honest caveats. VMC is better than pace for the first fifteen minutes and worse after — which
is why both are offered rather than one being declared correct. And a fleet differing by a flat
percentage is the kindest case there is for a method that scales a single number; `--upwind-bias`
exists to make it harder, and a real fleet is harder still.

**Checking playback after a change.** Continuous playback is driven by the browser's animation
clock, which is not fired while a page is hidden — so the normal test suite cannot exercise it,
and a headless page load proves only that the viewer *loaded*. `scripts/verify_replay.py` drives
the real thing in Playwright's Chromium and fails if the clock stalls or a boat teleports.

**The replay is only as good as the reporting rate.** At a 10-second interval a boat at 6
knots moves about 31 m between fixes, so a tack is a cut corner however smoothly it is drawn.
This is the same trade-off as the automatic horn: a rate fast enough for a convincing replay
costs a large multiple of the tracker's standby battery.

## One-time connection setup (Settings → GPS tracking, admin)

Enter the **Traccar base URL** (e.g. `https://pro.pwllhelisailingclub.org/traccar`) and an
**API token** from Traccar, tick **Enable GPS tracking**, and save. Optionally set the poll
interval, history retention and the mark-rounding radius. No hardware yet? Tick **Simulate boats**
to preview the map and finishes with synthetic boats. (For the API token to add/remove devices,
it needs device-management rights in Traccar.)

**Live data always wins over the simulator.** If a working Traccar URL and token are configured,
positions come from Traccar even when *Simulate boats* is ticked — the simulator is only used when
there is no connection configured, or as a fallback when Traccar cannot be reached. To demo or test
with simulated boats on a system that already has a working connection, clear the base URL and
token first (or use `scripts/simulate_trackers.py` to feed the real Traccar with simulated boats).

**Traccar's own web UI is not at that base URL.** `https://…/traccar/` loads a page that spins for
ever: the base URL serves Traccar's **REST API**, which works because the app adds the `/traccar`
prefix to every call, but Traccar's UI is a single-page app that asks for `/assets/index-*.js`,
`/styles.css` and `/api/socket` at the **site root**, where the relay sends them to the hut app
instead. Reach the UI on the LAN or over SSH at `http://<relay-ip>:8082`, or give it its own
Cloudflare hostname — see `deploy/live_stream/README.md` §7 step 6. You rarely need it: devices,
assignment and removal are all done on the **Trackers** page.

## Managing trackers (Trackers page — race officers)

The **Trackers** item in the main menu (available to race officers, not just admins) is where you
manage the fleet's trackers:

- **Trackers seen on the network:** devices Traccar has heard from that are not set up here yet,
  most recently heard from first, each with a boat dropdown and **Add**. Switch a tracker on and —
  with automatic registration enabled on the relay (`database.registerUnknown`, see
  [`traccar-forward.xml`](../deploy/live_stream/traccar-forward.xml)) — it appears here by itself,
  so nobody has to type a 15-digit IMEI. Adopting one links it to the existing Traccar device
  rather than creating a second. The page notices new arrivals between refreshes and says so.

  A device Traccar registers by itself belongs to **no Traccar user**, which has two effects the
  app handles for you: Traccar's own web UI hides it unless you switch on *All Devices*, and its
  positions are not returned to the app's API token at all. So the list asks Traccar for unowned
  devices too, and adopting one **claims it for the token's user** — without that the poller and
  the back-fill would never see that boat, even though push would. If Traccar has several accounts
  the app cannot tell which one the token belongs to; it says so, and you link the device to your
  user in Traccar.
- **Assign / re-assign:** pick each tracker's boat and Save. A tracker's data always belongs to
  the boat it was on at the time, so **moving a tracker to another boat leaves the first boat's
  track intact** — the track follows the boat, not the device.
- **Remove a tracker:** deletes it from Traccar too; the boats keep their past tracks.

Adopting from that list is the *only* way to register a tracker. A form for typing an IMEI by
hand existed until v0.270 and was removed: a tracker that has never reached Traccar cannot be
tracked whatever the app records, and picking one off a list is both quicker and impossible to
mistype.

A boat's assigned tracker is used for every race it enters, and the **Trackers page is the only
place the pairing is set**. The race sheet used to carry a second, per-race "loaner" dropdown on
its entries tab; it was removed in v0.207, because two places to set the same thing is two places
to get it wrong on a race morning. To put a different tracker on a boat for one race, re-assign it
here and move it back afterwards — the track follows the boat, so nothing recorded is lost either
way.

### Battery (v0.253)

Each tracker's **battery** sits beside *Last reported* on the Trackers page, red / amber /
green on the same dots: **green above 25%**, **amber 25% and below** (charge it before
Saturday), **red 10% and below** (may not last a race). A dash means the tracker does not
report a level — which is not the same as a flat one, and is why it is not shown as 0%.

The level comes from the newest stored fix, exactly as *Last reported* does, so it is on
the position rather than the tracker and a race's drain is in the history too. Traccar
supplies it in the position's `attributes`, and **names it differently per protocol**:
`batteryLevel` is the percentage Queclink sends (the `<Battery Percentage>` field of the
@Track protocol), while Teltonika sends AVL IO 113, which Traccar surfaces as `io113`.
Both are read. A position's `battery` is *volts* and is deliberately not treated as a
percentage — guessing a cell chemistry to convert it would put a made-up number on the
page people use to decide what to charge. Both the poll and the push forwarder keep it.

Reading only `batteryLevel` is what made this worth fixing rather than a tidy-up: the
club's Teltonika ATC700 reporting every 10 s drains about **8.6%/h** against the
GL521MG's **0.9%/h** at 60 s, so the warning covered the trackers that survive a week in
a bag and said nothing about the one that will not last a long Saturday.

**The dashboard warns** when a tracker *assigned to a boat* is at or below 25%, flattest
first, naming the boat. It only appears when there is something to say, and only when GPS
tracking is enabled — a card that is always on screen is furniture rather than a warning.
An unassigned spare is not warned about: that is a job for another day, not for the
dashboard of a race being run.

This matters more than it might for a boat instrument. These are battery *asset* trackers
being run at a reporting rate far above their standby design, which is why the advice
above is to charge them between race days — the app just never said which ones needed it.

### What kind of tracker it is (v0.269)

A **Type** column names each tracker's model. It is derived rather than stored, from the first
eight digits of the IMEI — the *type allocation code*, which every unit of a model shares; the
club's two GL521MGs both read `86486407` — confirmed against the protocol Traccar decoded.

Both halves are needed. The code alone cannot be trusted: it belongs to whoever certified the
radio, and a tracker built around an off-the-shelf cellular module often ships with the module
vendor's range. The ATC700's own datasheet names its module as a Quectel EG915U, so another
maker's tracker on the same part could carry the same code. And the protocol alone is far too
coarse — the **ATC700 and the RUTX50 are both `teltonika`**, one a 1000 mAh asset tracker
draining 10%/h and the other a mains-powered router at zero delay. So a catalogue entry may name
the protocol it applies to, and a device that speaks something else is not matched: the app falls
back to "Teltonika device" rather than assert a model it has reason to doubt.

A tracker the app does not recognise shows *unknown* with a **name it** button, which opens the
**Tracker types** catalogue with the code already filled in. Naming a model is not admin-gated —
it records a fact about the kit rather than reaching the hardware.

### Sending a command to a tracker (v0.269, administrators)

**Commands** beside each tracker opens a console that sends a raw protocol command through Traccar
and shows what comes back. Administrators only: the rest of this page decides which boat carries
which tracker, and this reconfigures hardware at sea. Prebuilt commands are offered per protocol,
because the two dialects have nothing in common and offering Queclink syntax to a Jimi tracker
would look authoritative and do nothing.

Traccar queues a command while the device sleeps and delivers it on the next connection. Measured
round trips: about **6 s** to a GL521MG holding a long-lived TCP connection, **95 s to 5 minutes**
to an ATC700 that wakes on its own schedule.

**What comes back depends on the protocol, and the console says which.** Teltonika replies over
Codec 12 and the reply *is* the data, so `getparam 10100` returns the values and they appear in
the console. Queclink acknowledges with `+ACK:GTRTO` and sends its answer as a separate message
that Traccar's decoder discards — verified for a configuration read, device information and
signal strength, each of which produced an acknowledgement alone. Turning on Traccar's
`database.saveOriginal` does not recover them: it attaches raw text to a *position*, and those
replies never become one. So those queries are labelled *"answer not visible here"*, and the one
Queclink query that does answer is a position request, whose reply arrives as a fix.

**Factory resets are refused, not confirmed.** In Queclink's dialect `AT+GTRTO=...,4,...` is a
factory reset and `...,3,...` is a reboot — one digit apart — and a tracker that forgets its
server address stops being findable at all, recoverable only over USB with the unit off the boat.
A reboot asks for confirmation and quotes its measured cost: **5 min 45 s with no fixes**, then
several minutes at reduced accuracy, which is why it should never be done to a boat approaching
the finish. Every command is written to the activity log, refusals included.

**Setting a Teltonika interval writes both halves of the pair.** The club's SIM is multi-network
and registers as **roaming in the UK**, so of `10050`/`10150` only the roaming half is live — and
the device accepts a write to the dead half, reads it back correctly, and ignores it. A
multi-network SIM can also change registration mid-race, so a device with one half set would
silently change behaviour partway through. See [`TRACKERS.md`](TRACKERS.md) for the full pairing.

### What the GPS thought of itself (v0.269)

Every stored fix now carries **altitude, hdop, pdop, satellite count, signal strength** and
Traccar's own **validity flag**, alongside the battery level, on all three routes fixes arrive by
— the poll, the push forwarder and the outage back-fill. Fields a device does not send stay empty
rather than becoming zero: "not reported" and "reported as zero" are different, and a check that
confused them would condemn every fix from a device that simply does not send the field. Every row
recorded before this release has them empty, which readers must treat as *not known* rather than
*bad*.

Nothing interprets them yet. They are stored raw because any check on them will want tuning
against real history, and a value discarded at ingest cannot be looked at a second time.

**Altitude is the one worth having, because a boat is at sea level.** It is the only field whose
true value is known in advance, so a departure from it measures the error directly. Two GL521MG
failures during the races of 16 August are obvious in it and invisible everywhere else: one
receiver drifted to 1060 m below the sea with 627 m of horizontal error before recovering, and
another held a bias that put it 20–50 m south of the marks it rounded. `hdop` — the conventional
quality field, and the only one a GL sends — sat at 1–3 through both. It reports confidence in the
fix just made, not whether the solution has drifted.

That matters beyond tidy-looking tracks. A finish is interpolated between the two fixes bracketing
the line, so a positional offset becomes a timing error scaled by boat speed: at 3.5 kn, 50 m is
**28 seconds**.

### Asking racing boats where they are (v0.270)

A Queclink reports once a minute and holds each fix until the top of the minute, so its position
reaches the app about **45 seconds old** — 185 m behind the boat at 6 knots, which is the gap a
finish is interpolated across. Asked directly it answers in about a second.

So while a race is on, the app asks. From the **warning signal** until **a minute after each boat
stops racing**, every tracked boat carrying a **GL521MG** is sent a position request every 20 seconds.
The extra minute is not padding: a finish is interpolated between the fixes either side of the
line, so the one *after* the crossing is what pins it down.

Twenty seconds is a floor the tracker sets, not a choice — at 5 s it returns duplicates of a cached
fix with latency climbing to 57 s, and only at 20 s does every request bring back something new.
The result is fixes **62 m apart arriving 4 s old**, against 185 m arriving 45 s old.

It costs about **+1.1 %/h**, roughly doubling a Queclink's idle draw, so even a nine-hour race
polled from start to finish is about 19% of its battery. **Only the GL521MG is asked**, matched on its
IMEI type code rather than on its protocol: `gl200` is Queclink's whole @Track family, and the
command carries `gl521m` — the GL521M's own *password* — so another Queclink would reject it.
Teltonika is left out for a different reason: an ATC700 already reports every 10 s while moving
and answers in a second, so there is nothing to gain, and at ~10 %/h it is the one unit that
cannot spare the asking. Adding a model means knowing its command and its password, not guessing
from the family it belongs to.

Switch it off in **Settings → GPS tracking** if you need to. It is on by default, does nothing
unless Traccar is configured, and a race left open by mistake stops being polled after 30 hours so
a forgotten one cannot flatten a tracker.

### What the SIM is doing (v0.270)

A tracker that goes quiet gives you no clue why. A flat battery, a boat behind a headland and a
SIM the network has stopped carrying all look exactly the same from here — but only the last of
those will still be silent tomorrow, and it is the only one nothing else in the app can see.

With a **Hologram** API key in **Settings → GPS tracking → SIM connectivity**, the Trackers page
gains a **SIM** column and, when it matters, a warning above the table naming every tracker whose
SIM cannot carry data. A SIM **paused by system** has hit a usage limit or a low balance; it will
not come back on its own. Worth knowing before you go looking for the boat.

The popout names the state the way Hologram's own dashboard does — **Connected** when a data
session is open at that moment, **Ready** when the SIM is live but idle — so the two pages can be
compared without translating. Both are healthy. The club's Queclinks hold a long-lived connection
and usually read Connected; the ATC700s sleep between reports and flip between the two. It is a
snapshot, and the line above the table says how old it is.

Read the healthy case carefully: it says the *SIM* is fine, **not that the tracker is alive**. A
live SIM proves only that nothing at the network end is stopping it. The column is worth watching
for its bad answers, not reassured by its good ones.

The column also shows **which carrier each tracker is actually on**. That is not trivia: the club's
SIM is multi-network, and on a Teltonika half of every setting is live or dead depending on whether
the device counts as roaming — see [`TRACKERS.md`](TRACKERS.md). It has been an assumption until
now. A tracker that changes carrier partway through a race is also the leading suspect for the
spells where fixes arrive minutes late.

It is **off by default**: it needs an account the club may not have, and it is a second outbound
connection on a page opened on race morning. When it is on, **the page never waits for Hologram** —
it draws with whatever it already knows and fetches behind you, so a Hologram that is slow or down
costs the column and nothing else. The trade is that the first load after the app starts shows a
blank SIM column and the next one is filled in.

### What it costs

The popout also shows each SIM's data for the **last four weeks separately**, the total for the
current billing period, and a **month to date** figure that includes the plan's standing charge.
Weekly figures are data only.

Read the money for what it is: **Hologram's API returns no cost anywhere**. Every usage endpoint
gives bytes and nothing else; the only prices available are the plan's per-MB rate and its
recurring fee. So these figures are worked out in the app — usage × rate, plus the fee — and are
not read from an invoice. They will not match a Hologram bill that includes SMS, phone numbers or
anything else. A megabyte is a million bytes, which is how Hologram counts (checked against the
club's own dashboard, not assumed).

Currency is US dollars. The API returns rates as bare numbers with no currency field, so that is
the one part of the sum the app is told rather than shown.

**costs and balance**, beside *check again*, opens the same thing for the whole fleet: every
tracker's data and cost, the account balance, and when it will need topping up.

**Read the periods carefully — there is no single one.** Hologram bills each SIM on its own
30-day cycle counted from the day it was activated, so every "this period" figure covers a
different window. The **Days** column is how far into its own cycle each SIM is; the club's have
sat at one day and sixteen at the same moment, and the one-day-old SIMs had barely started
accruing. **Charged so far** is fees plus data across those uneven periods — real money already
committed, but *not* a calendar month and not a like-for-like total. The **last 28 days** row is
the same window for every SIM and is the figure to watch over time. (While a fleet is new the two
agree, because none of the SIMs is older than the window.) Both popouts link through to the Hologram dashboard — to the front page, because
Hologram publishes no per-SIM URL; its search box takes an ICCID.

### When to top up

The account is prepay, and when it empties **every tracker stops at once** — with nothing on the
water to see it coming, which is why this is worked out rather than left to be noticed.

It is not the balance divided by a monthly cost. Each SIM bills on its own 30-day cycle from the
day it was activated, so the fees arrive in **clusters**: the club's seven fall on two dates a
fortnight apart. The app walks forward a day at a time from today, taking the recent daily data
spend off each day and each SIM's fee on its own renewal date, and reports the first day the
balance would go under. It also says when the balance drops below the auto top-up mark, if one is
set.

The daily data figure is the last seven days averaged — long enough to include a race weekend,
short enough to notice a change in how the trackers are used. Beyond six months it stops offering
a date, because a seven-day average will not carry that far.

**The dashboard says so too**, once there is a month or less left, and more loudly inside a
fortnight. If auto top-up is configured the card says so, since the problem may settle itself
provided the card on file still works.

If Hologram will not give the app a rate, the data still appears and only the money is blank;
the same is true of the balance, which the popout reports as unavailable rather than guessing.
Neither happens with the role below, but a more restricted one may hit it. The balance needs Hologram's separate **billing** permission, and without it the costs
popout shows usage and says the balance is unavailable rather than guessing.

Answers are kept for **two minutes**, and the line above the table says how old the one you are
looking at is. If you have just changed something in the Hologram dashboard, press **check
again** rather than waiting — that asks immediately. The two minutes exist because the reading is
usually wanted at the moment it has just changed: a SIM paused in the dashboard once still read
**OK** here, which is the failure this is built to avoid.

Hologram API keys **cannot be made read-only** — there is one key per user, and its power comes
from that user's role. So limit the key by limiting its owner: make a separate Hologram user for
the app, give it the **Editor** role, and take that user's key from **Settings → My account →
API**. Editor is what the club runs and it is enough for everything on these pages — SIM state,
usage, plan pricing and the account balance — while stopping short of activating SIMs or changing
billing.

Do not paste in an owner or admin key. And because there is one key per user, regenerating it
rotates that user's access everywhere, which is the other reason to keep it off a personal
login.

### A phone as a tracker

A GL521MG is not the only thing that can appear on the chart. The free **Traccar Client** app
(iOS and Android) reports over the OsmAnd protocol, which the relay listens for on **5055**, so a
phone in a pocket is a working tracker:

1. In the app, set **Server URL** to `http://track.pwllhelisailingclub.org:5055` and pick a
   **device identifier** — anything memorable, e.g. `PHONE-RIB`.
2. Add that identifier on the **Trackers** page here and assign it to a boat, exactly as for a
   real tracker. Traccar ignores an identifier it has never been told about.
3. Set the app's reporting interval to a few seconds for a race, and start the service.

Useful for the rescue RIB or committee boat, for a volunteer's phone when there are more boats
than trackers, and for trying the whole chain out before the hardware arrives. Two caveats: it
drains a phone battery quickly at race-day reporting rates, and it needs a mobile signal in the
same places the trackers do. `scripts/simulate_trackers.py` speaks the same protocol, which is
how a simulated fleet can be sailed through the real relay.

## One boat, one tracker

A boat carries one tracker, and from **v1.002** the app holds you to it:
assigning a tracker to a boat that already has one takes the boat off the other
and tells you which, so you can put it back the other way round if that was not
what you meant. The displaced tracker is unassigned, never deleted, and the
fixes it already recorded keep the boat they were recorded against &mdash; that
track happened and belongs to that boat.

Before that the table let two devices point at one boat, and every fix from both
was stamped with it, so the boat's track became the two interleaved. It cost one
club race: a spare tracker aboard *Mojito* was also paired to *Crackajack*, and
the replay drew Crackajack flipping between the two boats several times a
minute. Repairing it is in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md#a-boats-track-jumps-to-another-boats-position).

What the app still cannot see is a tracker paired to the right boat and carried
onto the wrong one. It reports from a plausible place at a plausible speed, so
nothing looks wrong until somebody watches the replay. **When a tracker moves
boat, change it on the Trackers page at the same time.**

## When a mark moves

A mark that has dragged is a mark the app is looking for boats to round in the
wrong place, and the course walk is **sequential** — the walk will not look for
mark 5 until it has seen mark 4. When rounding was judged on the radius alone
that made a 60 m drag catastrophic rather than untidy: every boat read as never
having rounded it, every mark behind it stalled, and the symptom was a fleet
stuck at the same mark on the *Position on the water* list with no GPS finishes
at all.

All three of the tests below now absorb a drag of that order — the neighbourhood
test (v0.247) and, since v0.258, a **gate** reaching 750 m along a line through
the mark on the required hand. A drag has to be large to defeat all three.

Re-measuring is still the fix, for two reasons that outlast the geometry. A drag
of hundreds of metres is past the neighbourhood and can put boats the wrong side
of the recorded position, where the gate barely reaches. And everything else the
position feeds — the chart, the leg analysis, the course length, distance-to-go —
stays wrong until it is corrected, quietly, because nothing stalls to tell you.
When it is one boat that simply sailed wide of a mark, the **Sailing to** arrows
correct it in place — see [On a race](#on-a-race).

### The rounding radius

The radius in **Settings → GPS tracking** (50 m by default) applies to every mark
unless a mark overrides it. A mark can carry **its own** radius, set in
**Marks → Edit**: a buoy on a long scope swings a wide circle, and one boats are
told to give a berth needs more room still — while a radius large enough for
those, at a mark rounded twice in the same course, would start swallowing
roundings that never happened.

Leave a mark's radius blank and it follows the Settings value, including when
that value changes later. Only set it for a mark that genuinely needs its own.

Marks also need re-measuring after heavy weather, and there are two ways:

- **From a phone on the water** — **Marks → Set a position from the water**. Take
  the boat to the mark, wait for the fix to settle, pick the mark from the list
  and set it. The page shows the fix and the accuracy the phone claims, how far
  the mark is about to move, and refuses a fix worse than 25 m: with a 50 m
  rounding radius, a vague fix could move a mark most of the way to the edge of
  its own circle. A move of more than 2 km has to be confirmed, because at that
  distance the likely explanations are the wrong mark picked or a phone still
  reporting from the clubhouse.

  It needs the club's **https** address — browsers do not give a position to a
  page served over plain http — and permission to set marks: the **Mark layer**
  role, or the per-user **Set marks** tickbox on a race officer. Administrators
  always have it. See [SETTINGS_AND_ADMIN.md](SETTINGS_AND_ADMIN.md).

  If the page sits on "waiting for GPS…", the phone has not been asked or has
  refused. On an iPhone each browser is granted location **separately**, under
  its own name in Settings → Privacy & Security → Location Services — allowing it
  for Safari does nothing for Chrome. The page says which browser it is in and
  what to check.

- **By typing it in** — **Marks → Edit** on the marks page, for a position taken
  from a chart plotter or a survey. Needs the same **Set marks** permission as the
  phone page (v0.246; it was administrators only). Whoever has just re-laid a mark
  is the one who knows what changed, and the permission already lets them move it
  from the water — so requiring an administrator to type the same correction, or to
  adjust that mark's own rounding radius, was the wrong line to draw. **Adding and
  deleting** marks stay with administrators: those change the set of marks every
  course sequence is written against.

### A correction does not reach backwards

Every change records who set it, when, from what, and the accuracy claimed, and
keeps the position it replaced with the date it was set. That history is read:
**a race is drawn, replayed and analysed with the marks as they stood when it was
sailed**. The course chart, the replay, the leg analysis, the course length and
the finish line (O is a laid mark too) all wind back together.

Without it, correcting a mark by more than the rounding radius would put every
recorded rounding of that mark outside its own circle — and because the walk is
sequential, a race that scored perfectly on the day would afterwards read as a
fleet that never got round.

**A race is pinned to when it ended, not when it started.** This is what makes
the usual case work. A dragged mark is normally discovered *during* a race, by
the boats failing to register as rounding it; somebody then goes out and pings it
mid-race. Pinning to the start would file that correction after the race and
ignore it, leaving the boats already round unrecognised — and every boat behind
them, the walk being sequential. Pinned to the end, a correction made while the
race was still being sailed counts for that race, permanently, and the walk picks
up the earlier roundings as well because it recomputes from every stored fix.

While any boat is still racing the marks are simply the current ones, so a
correction takes effect the moment it is made. A race with no start time yet —
one still being built — uses the current marks too.

The one case this does not recover is a drag noticed **after** everyone has
finished: pinging it then is filed after the race and does not reach back into
it. Nothing about the recorded finish *times* is affected either way, those being
times rather than geometry — but the chart and the *Position on the water* walk
for that race will still show the fleet stuck at the mark. If that happens, edit
the mark by hand, note the old position first, and put it back afterwards.

## On the dashboard

The dashboard carries a map below the race and wind cards: every mark, and any
tracker that has reported in the **last hour** — the same window the Trackers
page counts as reporting. It is a glance, not a chart to work from: no course and
no leaderboard, which belong to a race's own chart.

The zoom follows whatever is happening. With boats out it fits them and the marks
around them; with nothing out it fits the racing area. A mark more than 40 km out
is left out of the zoom (though still drawn), because a passage-race mark across
the Irish Sea would otherwise shrink the bay to a smudge. A tracker is never left
out however far out it is — if a boat is really out there, the map follows it.

## On a race

- **Live map & "Position on the water":** on the race sheet's *Course & start* tab, tracked boats
  appear on the course chart as a hull turned to its COG with its sail number below, and in the **Position on the
  water** list, ordered by how far round the course each boat is — showing marks rounded (e.g.
  `6/10`), the next mark, and the distance still to sail, plus speed and last-fix age. Boats whose
  last fix is over a minute old are dimmed (*stale*). **Every boat entered is listed**; one with no
  position shows dashes and says why &mdash; *No tracker* if there is none on the boat, *Not
  reporting* if there is one and nothing has come from it in the last hour &mdash; and sorts after
  the tracked boats. The display position reaches up to an hour outside the race window, so a boat
  that reported shortly before the warning signal is still on the chart, but one that last spoke
  weeks ago is not placed at where it was then.
- **Until a course is chosen there is no progress to report.** A new race stores the first fixed
  course as a fallback so the chart and the leg analysis have geometry to work with, and marks
  rounded, next mark and distance to go were all being measured against it &mdash; on a race sheet
  whose own header said *Course: not set yet*. GPS finish detection was walking the same guess.
  Marks, next and to-go are blank until the race officer sets the course; position, speed and
  last-fix age are the tracker's own report and are unaffected.
  This is the order **on the water** — it takes no account of each boat's handicap, so it is not a
  corrected-time result.
- **How progress is worked out:** the app follows each boat through the course marks in order.
  A mark counts as rounded when **any** of three tests passes. The second, the **gate**, was
  added in v0.258 and is the only one that knows a mark has a *required side* at all — until then
  a boat rounding the wrong way counted exactly like one rounding correctly.
  1. The boat passes within the **mark-rounding radius** (Settings → GPS tracking, default 50 m) —
     measured against the **path between fixes**, not just the fixes themselves. That matters more than it sounds: a boat reporting every 10 seconds at 6 knots
  moves about 31 m between fixes, and a coarser race-day rate can step clean over a mark with no
  fix inside the radius. Before v0.201 that boat scored **nothing for the rest of the race** —
  progress is sequential, so one missed rounding stalls every mark after it, and the boat sat at
  0/7 and last place while visibly sailing mid-fleet. A leg is only trusted across a normal
  reporting gap (60 s), so a back-filled outage cannot sweep through marks the boat never
  approached. If roundings are still being missed, raise the radius to suit your reporting rate.
  This is also what lets a boat round the ODM mid-course (as many courses do) without being
  finished early.

  2. Or (v0.258) the boat crosses the mark's **gate**: a line drawn through the mark at right
     angles to the leg arriving at it, reaching **750 m** on the hand the boat is supposed to
     pass and only the **rounding radius** on the hand it is not. Cross it and the mark is
     rounded.

     The asymmetry does two jobs with one number. The long side is the side test — pass 400 m
     out on the correct hand and the gate is crossed, 400 m out on the wrong hand and it is not.
     The short side is the width in which *no* side can be determined: a boat closer to the mark
     than we know where the mark is has no determinable side, and a drifting buoy is exactly a
     reason not to discriminate, so inside the radius either hand is accepted and the question is
     never asked. A mark that records its own position accuracy widens that band.

     750 m comes from a race rather than from taste. Drawing level with mark AA on a 9.2 km leg
     in the night race of 8 August, the three boats were 369 m, 468 m and 512 m off, all on the
     correct hand — so a 500 m reach would have missed the widest of them by twelve metres, and
     that one miss stalled the boat for six hours. Configurable in Settings and per mark.

     Crossing is a plain fact about the straight line between two fixes, which is what makes the
     gate work at the reporting rate the club actually has. A test that accumulated a swept angle
     was tried first and abandoned: at 61 s between fixes a boat travels 250 m, a tight rounding
     happens entirely inside one step, and the line between the fixes either side of a *correct*
     20 m rounding ran 81 m the wrong side of the mark — 87 degrees of confident, wrong verdict.
     It was reading the sampling rate rather than the boat.

     **A suspected wrong side is never refused.** The walk is sequential, so refusing would stall
     that boat for the rest of the race and take its automatic finish with it — to enforce a rule
     this app does not adjudicate. The race officer does that, on a protest, with the video and
     the track. So the gate can only ever *add* a rounding the other tests missed; it cannot take
     one away, and therefore cannot do worse than before.

  3. Or (v0.247) the boat came within the mark's **neighbourhood** and has since opened up 50 m
     from its closest approach, with the *next* mark nearer than it was at that moment. This is
     the test that catches a **wide rounding**: a boat given a berth by the fleet can stay
     outside any sane radius the whole way past, and a radius asks how tidily it rounded rather
     than whether it got round — which is the fleet's business to judge, not the app's. The
     neighbourhood is 400 m, capped at half the shorter leg touching that mark so a short-legged
     course cannot have a neighbourhood wider than its own legs, and never tighter than the
     rounding radius. There is no new setting: it follows the course.

     The 50 m departure and the next-mark condition are what keep it honest. It is never
     *early* — measured against recorded tracks it fired 30–60 s **after** the radius on a tight
     rounding, so a boat cannot be credited with a mark it has not yet left, and a tack on the
     beat is not mistaken for a departure. It was also better than the radius alone at rejecting
     a track walked against a course it never sailed.

     **All three are load-bearing, and the third is needed for the opposite reason to the one
     you would guess.** Simulated over the club's fixed courses on a real polar in 20 kn,
     reporting every 61 s, removing the gate loses the *wide* roundings as expected — but
     removing this closest-approach test loses the *tight* ones. At 61 s a boat steps 250 m
     between fixes, so a 20 m rounding can put no fix at all beyond the mark: the line between
     fixes never crosses the gate and no reach would help, while no fix lands inside the radius
     either. With 20 m roundings, all three tests complete 67 of 67 courses and radius-plus-gate
     completes 2. The regression tests for this are `tests/test_simulated_races.py`.

     This was chosen by measurement rather than argument, with a harness kept in the
     repo as `scripts/compare_rounding_tests.py` so the choice can be re-examined once
     there is a season of real racing behind it. Over every race with a
     race-officer-recorded finish time it agreed with the radius to within a second; it held a
     rounding pushed 400 m wide where the radius lost it at 100 m; and at 30-second reporting
     intervals it still found finishes the radius missed entirely. A turn-gate plane at each
     mark — the obvious first idea — measured *worse* than the radius, finding none of the
     recorded finishes, because a plane's before/after sides are global rather than local to the
     leg being sailed.

  Beyond the neighbourhood the boat is not rounding that mark at all, and the **Sailing to**
  arrows are the remedy — as they are for a mark that has dragged out of reach, or a tracker
  that slept through the rounding.
- **Correcting the mark a boat is sailing to (v0.246):** a wide rounding can still stay outside
  the radius the whole way past, and the cost is not just a wrong number: the boat shows several
  marks behind where it is for the rest of the race, and because the finish is only looked for
  once every earlier mark is rounded, **its GPS finish never arrives either**. The
  **Sailing to** column at the end of each *Position on the water* row has a **&larr;** and a
  **&rarr;**: forward treats the mark the boat is heading for as rounded, back puts it on the
  previous one. The row then shows *set*, so a corrected boat is not mistaken for a detection.

  A correction applies **from the moment it is made**, and that is deliberate rather than
  incidental. Applied from the start of the track it would reach back over the whole race — and
  because O is both the finish line's mark and a mid-course rounding mark on most club courses, a
  boat nudged on to the line could then be "finished" by a crossing it made on an earlier lap, at
  the wrong time. Taking effect from now is also what makes the back arrow hold: the walk resumes
  from the corrected mark instead of immediately re-counting the rounding already behind the boat.

  It reaches the finish detection, not just the display, which is most of the point. The arrows
  stop at the ends — back from the first mark and forward past the line are both refused, since
  finishing a boat is the finish button's job. A finished boat shows no arrows; change its time by
  editing the entry. Every press is recorded in the activity log with the marks either side of it,
  and appears in the race log as a note.
- **Arm GPS auto-finish:** on the same tab, **Arm GPS auto-finish** is ticked on a new race
  and **Auto-confirm (unmanned)** is not (v0.276). So a finish is *caught* whenever it can
  be, and then waits below as a proposal for you to confirm or dismiss &mdash; which is what
  the section on the ODM below argues for, and the sort of mistake it describes is the reason.
  Tick auto-confirm for a race with nobody in the hut; the on-the-water page ticks it for you
  on a race started from a boat. Untick **Arm GPS auto-finish** and there is no detection at all.

## Finishes

- With auto-confirm **off**, a detected crossing appears as a **proposal**: boat, detected
  time, a **Finish video** link, and **Confirm** / **Dismiss**. Confirm records the finish
  (tagged `gps-confirmed`); Dismiss discards it. You can still edit the finish time afterwards
  in the normal way.
- With auto-confirm **on**, the finish is recorded immediately (tagged `gps-auto`) and a
  finish video clip is scheduled around the moment for later review. The horn only sounds on
  an auto-finish if you enable **Sound horn on auto-confirmed GPS finish** in Settings (off by
  default — GPS latency makes an auto-horn late and risky).

A boat is only finished once it has **rounded every mark in the course** and then crosses the
finish line — so a course that passes the ODM several times (e.g. O‑F‑O‑1‑O) finishes correctly
on the *last* line crossing, not the first. If you **shorten the course**, GPS progress and
finishes follow the shortened course automatically: boats need only round up to the shorten mark
and then cross the line, and the distance-to-go in *Position on the water* reflects the shortened course. Start and finish lines are the same at CHPSC, so the
app also distinguishes a finish from the start by the **direction** of the crossing (leaving the
course) and by ignoring crossings in the first couple of minutes after the start.

### Which way across the line counts (v0.248)

The direction is taken from **the last mark rounded** — a boat finishes crossing the line
from the direction of that mark. The racing rules define finishing as crossing the line
from the **course side**, and on the last leg the course side is exactly what that mark
tells you — which is why a direction exists in the test at all.

It used to be taken from the centroid of the course marks, and that was wrong in a way
worth recording. The side of a line is measured against the *infinite* line through its
two ends, so a course with marks on both sides of that extension has a centroid that can
sit on the far side from where the boats actually come in. The test is then exactly
backwards: the real finish is read as *entering* the course and discarded, and the time
recorded is the boat crossing the line again on its way back to the marina.

**8 of the club's 12 courses straddle the line that way**, course 1 among them, and every
tracked boat-race in the database had been finishing 25 to 46 seconds late. It went
unnoticed because those finishes were recorded by GPS auto-confirm, so the stored time
and the detected time were the same number — there was nothing for them to disagree
about. It was found by a race officer walking a simulated race through the replay and
noticing the boat had plainly crossed half a minute before its recorded finish. Which is
the argument for the finish video and for **confirming** GPS finishes rather than arming
auto-confirm unless the hut is unmanned.

**It cannot be silent twice.** The direction test now says when it throws a crossing
away. If a boat crossed the line with every mark rounded and that crossing was refused
by the direction test alone, the activity log gets:

```
finish direction skipped an earlier crossing | race #146 · Demo Boat A · crossed at
13:28:34 but that was judged as entering the course; finish taken at 13:29:09
(35s later). Check it against the video.
```

Either the boat really did cross the wrong way first — which happens, and the video
settles it — or the finishing direction is wrong again. Run against the races that were
already in the database, the old behaviour produces this line for **all eleven** of them
with the right times and gaps; the current behaviour produces none. Worth knowing what
the measurement is worth: on the club's own data the direction test changes no answer at
all, so anything it does reject deserves a look rather than a shrug.

### When the line's mark has moved (v0.256)

A finish line has two ends, and they are not the same kind of thing. The **shore** end is
a surveyed transit — a bridge window — and does not move. The **seaward** end is a laid
buoy on a mooring: it swings, it drags, and it gets re-laid.

A boat that passes *inside* the physical buoy but *outside* the position the app holds
for it falls off the end of the line segment, and its finish is never seen. The direction
test does not save you, because that measures against the *infinite* line and is
perfectly content; only the segment bound refuses the crossing. That is exactly what
happened on an ISORA night race: the third boat to finish got no automatic finish.

So each line projects its seaward end outward, by `seaward_extension_m` in
`data/start_finish.json`:

| line | extension | line length | used for detection |
|------|-----------|-------------|--------------------|
| Pwllheli SC — Bridge window to ODM | 150 m | 347 m | 497 m |
| ISORA — Fairway Buoy to Plas Heli | 250 m | 1510 m | 1760 m |

Only the seaward end. Extending the shore end would project the line inland over the
harbour wall and cover nothing that can move.

**Why this is safe.** The extension is *collinear*, so the infinite line is unchanged and
the finishing-direction test cannot be affected by it. And a finish still requires every
mark rounded first, which is what already stops a mid-course pass of the ODM counting.
The extended line is used **only** for the crossing test: the chart still draws the line
where it really is, and distance-to-go still measures to the real mark.

It is a tolerance, not an open end — a boat well beyond the extension still does not
finish. Set it to the widest the buoy plausibly swings, not to the widest number you can
think of.

### The mark was wrong before it drifted (v0.256)

Worth recording, because a tolerance can hide this rather than fix it. The stored position
for **F**, the Pwllheli Fairway Buoy, was **112 m inshore** of the position ISORA's own
sailing instruction gives — and that position had been sitting in `start_finish.json` all
along as `si_seaward_position`, unread. On a 1510 m line that is 7% of its length, before
the buoy had moved at all.

F is now at the SI position. The old one is kept in the mark's position history, so races
already scored still replay against the line they were sailed to.

**A drifting line mark is worth re-measuring, not just tolerating.** See *When a mark
moves* above: a race officer can set a mark's position from a phone alongside it. The
extension is for the drift between measurements, not a substitute for making one.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
