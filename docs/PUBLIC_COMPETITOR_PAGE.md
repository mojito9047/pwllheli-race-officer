# Public competitor page

The public competitor pages are read-only pages intended for competitors, spectators or a second screen. Race-office/admin pages live under `/admin` and remain behind login.

## Opening the page

The simplest public address is the site root:

```text
/
```

On Cloudflare this means competitors can use the bare hostname, for example:

```text
https://pro.pwllhelisailingclub.org
```

That redirects to the default competitor landing page:

```text
/public/current
```

The landing page shows the supplied Pwllheli Sailing Club banner, the current series, the list of races in that series, a direct **Go to current race** button, current hut wind, wind history and an optional live camera view.

The **Go to current race** button opens the race page that *follows the current race*:

```text
/public/race
```

A specific race can also be opened directly, pinned to it:

```text
/public/race/<race_id>
```

**The difference matters on the water (v0.277).** A competitor opens the current race from a
phone, sails it and finishes; the race officer finishes the fleet and starts the next race. On a
pinned page they stay on the race they have just sailed until they think to go back to the home
page and press the button again &mdash; which is not something to work out one-handed on a wet
phone. The unpinned page moves on by itself, says on its face that it will, and offers a link to
stay put. It is the same distinction `/bar` and `/bar/<id>` already draw for the clubhouse
television.

Nothing new drives it. The race page already polls a state signature every two seconds and
reloads when it changes; the unpinned page polls `/public/race/state`, whose signature covers the
race id, so the race officer moving on looks to the page exactly like a course change. The
positions and track endpoints stay pinned to the race being rendered &mdash; they are fetched
between reloads, and following the current race there would draw one race's boats over another's
course until the page noticed. A pinned page that is *not* the current race says so and offers
the way across; on the following page the explanation is behind the `?` beside the race name,
because three lines of it pushed the header off a phone.

**Where the reload lands.** The open tab is remembered in the URL as a `#fragment`, and a browser
honours a fragment before the page's own script runs &mdash; so every reload arrived at the
*tabs*, wherever the viewer actually was: scrolled up to the countdown when the course changed,
you were thrown down to the chart and never saw the course board or the clock change. The tab now
moves to a `?tab=` parameter on every state reload (dropped once read), so there is no anchor to
jump to, and the page lands deliberately: **a different race at the top**, because the header is
the news; **the same race exactly where the viewer was**, carried in `sessionStorage`, keyed by
race, cleared on a race change and read once. Without storage the page opens at the top.

The same page serves phones, tablets and PCs (see **Phones and tablets** below); the older `/public/mobile/...` addresses still work and redirect here.

## Competitor landing page

The default competitor page is designed as the page to share publicly. The top of the page — the race-information header — carries the current race name, its first start, a **Go to current race** button, and two instruments:

- The **wind dial**: the same analog TWD/TWS instrument the race officer sees on the dashboard (`templates/partials/wind_gauge.html` driven by `static/wind_gauge.js`, refreshing from `/api/weather/current` every 5 seconds). It is deliberately *not* wired to the weather-station status line, so raw station errors stay off the public page; with no wind available the dial simply reads `—°` / `— kt`. The Wind tab has the detail and the history.
- The **countdown** to the current race's first start, in the same words as the race page (*Counting down* / *Race started* / *Race finished* / *Start not set*). It is calculated in the browser from the server-rendered start time. With no current race, no countdown is shown.

  **It keeps up with the race office (v0.251).** Because that start time is rendered once by the server, this page used to sit on *Start not set* with a dead clock until somebody reloaded it — at the one moment everybody has it open. It now asks `/public/current/state` every five seconds for a single hash of what it was rendered from (the current race, its start, its course, the race list) and reloads when it changes. The endpoint answers nothing but `{"ok", "signature"}`.

### When the race officer changes something mid-race

Every public view is rendered once and then keeps itself up to date, so anything the server writes into the page needs telling. Three faults here were reported from the hut within a day of each other, all the same shape:

- **A shortened course** did not reach the competitor page or the clubhouse display. The race page polls a signature and reloads on a change, but shortening changed nothing in it: the sequence in that signature comes from the *unshortened* course, and the shorten columns were absent. Fixed by putting them in.
- **A start time set** did not reach the clubhouse display, whose state poll only watched the camera window and whether the current race had moved on.
- **Neither reached anybody on the Chart tab.** A reload throws away where a viewer had scrubbed the replay to, so the page holds one back while the chart is open — and it used to hold it for as long as the tab was open at all, on the reasoning that a replay of a finished race cannot go stale. The chart is exactly where somebody watches a race in progress. It now holds the reload only while the viewer has actually **wound back into the past**; at the live edge, or with no track loaded at all, it reloads.

Below the header the page is **three tabs**:

- **Races** — every race in the current calendar year, grouped into its series. Each series is a rolled-up section showing its name and race count; the **current series is open** and the **current race is highlighted**. Races that are not in a series are collected into a final group. Each row gives the first start, status, entry count and a link to the race page.
- **Wind** — current start-hut TWD, TWS and gust, plus a **tall wind-history plot** (the tab gives it the height that the old stacked layout could not). The plot uses seven labelled vertical scale positions for TWD and TWS, and shows TWS as horizontal bars starting from 0 kt. History length is selectable from 5 to 60 minutes.
- **Live camera** — the hut camera view. It **starts when the tab is opened** (there is no longer a *Show live camera* checkbox) and stops when the viewer leaves the tab or backgrounds the page, so an idle viewer costs the hut no camera traffic. The card deliberately avoids showing detailed recorder/source messages to competitors.

  When a **live-stream URL** is configured (Settings → Video recording → *Public live stream page*), the tab shows **branded snapshots first and then the live stream**. The relay's stream runs on demand, so the first viewer waits while MediaMTX spins the camera up; rather than sit on a black player, the panel keeps refreshing the snapshot and swaps to the stream once it reports that it is playing. The stream is the relay's own watch page embedded in the panel, so there is one player to maintain. Snapshot refreshing stops as soon as the video takes over, and leaving the tab removes the player so the relay can drop the stream. On a phone the video fills the width of the screen (the panel drops its surrounding chrome). There is no separate *Watch live* button on the home page — the panel itself is the live view; the race page keeps a *Watch live* link in its footer. Without a live-stream URL the tab behaves exactly as before: snapshots only.

If the current race is not assigned to a series, the page still shows the current race button, and the group holding the current race is the one left open.

## Specific race page

The page is organised into **tabs** below the race header, so it stays short — especially on a phone. The race name, warning/start times, course sequence, flag panel and countdown sit above the tabs and are always visible.

While the race is on there are three: **Entries** (or **Start times** for a pursuit race), **Chart** and **Course analysis**. Once every boat has stopped racing, a fourth **Leader board** tab appears holding the results, and it is the tab shown first. The other three stay available as a record of the race. (The live handicap-corrected order during a race lives under the **Chart** tab, next to the boats it is about; the *Leader board* tab holds the confirmed result.)

The selected tab is remembered in the page address (`#ptab-entries`, `#ptab-chart`, `#ptab-analysis`, `#ptab-results`), so a link can point at a particular tab.

The specific race page shows race-level information before and during racing:

- Race name and summary.
- First warning-signal time, actual start timing and race timer/status.
- Dynamic flag panel using the current signal plan.
- An **Entries** list showing each boat in the race: boat name, sail number, class, IRC rating, YTC rating and current status. This is shown above the course analysis while the race is in progress and is hidden once every competitor has finished (the same behaviour as the course analysis), so the results take its place. The ratings shown are the per-race entry rating snapshots.
- Course chart and course sequence.
- Compound marks such as `Y` and `A` remain compact in the public course sequence, but the course chart and leg analysis draw/analyse their physical corner marks.
- Course leg analysis when available. Hut wind is shown in the course-analysis controls rather than repeated in the race-information box.
- If the race officer **shortens the course**, a **Shortened course** banner appears naming the mark to finish at, Code flag **S** (blue square on white) shows in the flag panel until all boats stop racing, and the course chart is redrawn to end at that mark with a dashed line straight to the finish.
- **GPS boat positions** while the race is on and tracking is enabled: each tracked boat is drawn on the course chart as a hull in plan view, turned to its COG, in the same colour as its track, with its sail number below; and the Entries list gains **Marks / Next / To go / SOG / Fix** columns showing how far round the course each boat is, refreshing every few seconds. A boat with no tracker shows dashes and *No tracker*. This is the order **on the water** — not corrected for handicap. Nothing appears when GPS tracking is switched off in Settings. See [`TRACKING.md`](TRACKING.md).
- **A corrected-time leaderboard while the race is on**, under the chart. The **Leaderboard** picker offers *Line honours* — the order on the water, as above — or *IRC corrected* / *YTC corrected*, either overall or narrowed to one rating class. Only the systems the fleet is actually rated in are offered, and the class filter only appears when the race has classes.

  For a boat still racing the app has to **project a finish time**, and there is no single honest way to do it, so the competitor chooses the method:

  - **Average pace since the start** *(the default)* — how much of the course the boat has covered, and how long that has taken. Nothing else comes into it: no wind, no polar.
  - **Recent pace (last 20 minutes)** — how fast the boat has actually been closing on the finish lately, carried over the distance it has left. This is the one that notices a boat parking, and the jumpiest.
  - **Polar pace factor** — how the boat's time so far compares with the *target* time for the legs it has sailed, applied to the legs it has not. The only method that knows the legs ahead are a different point of sail from the legs behind. It needs a wind, and the only wind the club measures is on the hut.

  **Whichever method is picked drives the order *and* the times printed beside it.** A board whose ranking came from one estimator and whose finish times came from another would contradict itself as you read down it.

  The simplest method is the default, and the reason is not that it is the most accurate — it is not. Its errors are much the same for every boat at any given moment, because the whole fleet has just sailed the same slow beat or the same fast reach. A leaderboard ranks boats *against each other*, so an error they all share cancels out, while a per-boat one moves the order about. Over the night race of 8 August it had the finishing order right in 99% of the race against 63-83% for the alternatives, despite a larger error on each individual boat's finish time.

  The rating is then applied — `x TCC` for IRC, `x 1000 / YTC` for YTC — by the same code the published results use, so a live board and the final result cannot disagree. A boat that has already finished shows its **real** elapsed time, not a projection.

  Some things are deliberately withheld rather than guessed. **Nothing is estimated for the first ten minutes of racing** — the convention competitors know from YB Tracking, and sound, because before the first mark a boat's pace is mostly which end of the line it started. A boat whose tracker has been quiet for three minutes, or that is sailing *away* from its next mark, is listed without a position rather than projected from bad data. An estimate implying the boat will crawl the rest of the course is dropped rather than shown. Every corrected board is captioned as an estimate, not a result.

  Those ten minutes are counted **from the gun**, and so is the chart's own elapsed clock, so ten minutes on the clock is ten minutes of racing. The replay *window* opens at the warning signal five minutes earlier; the clock used to count from there, which made the estimates look five minutes late when they were not.

  **Boats are compared as at the same moment.** Trackers do not report together — one boat's may speak every two seconds and another's every minute — so ranking each boat's last known position against the others compares them at different times, and at 6 knots two minutes of that is 400 m. That is enough to invert an order, and it was why the board could be seen swapping boats round while nothing on the water was changing. Each boat is now carried forward from its last report on its last known course and speed, to the moment on screen. A boat unheard from for three minutes is left where it last actually was, and shown as stale.

  The picker works in the replay too, so the corrected order can be watched changing through a race already sailed.

- **A wind gauge on the chart, following the playback.** The same dial the race office and the landing page carry, floated transparently in the chart's top corner. Direction and speed are the dial's two readouts; the **gust** is a marker on the speed scale rather than a caption, because it is a reading of the same quantity as the speed needle and belongs on the same scale — and it appears only when it is actually gusting. It is driven by the replay clock rather than by the live poll: wind it back to the start and the gauge shows the wind at the start. The wind through a race comes down with the track (one reading every 30 seconds), so there is no request per frame — the same arrangement as the order on the water beside it.

  Two honest limits are on the face of it. The caption says **Wind at the hut**, because that is what the club measures and it is not the wind where the boats are once a course rounds a headland. And a reading more than ten minutes older than the moment on screen is not shown at all: the gauge disappears rather than presenting a stale reading as current, which is how the board already treats a boat whose tracker has gone quiet.

- **The chart owns the tab, and the leaderboard floats over it.** The chart used to be a capped square with the board stacked underneath, which on a phone meant a 300-pixel chart and most of the display spent on everything except the thing you opened. The panel now gives the chart everything left after the playback controls, and the board is a **translucent roll-up** over the bottom of it. It starts **rolled away** — opening the Chart tab is a request to see the chart — and one tap on the *Leaderboard* tab brings it up, one more puts it back. It never covers the playback controls, and it never takes more than about half the panel.

- **⛶ Full screen** is the same layout pinned to the whole display, for when the chart is all you want. A **✕** in the corner leaves, as does Escape; it has a control of its own because the roll-up board can cover the one in the control bar.

  It works by filling the browser window rather than by asking for true full screen, because iOS Safari refuses that on anything but a video, and a phone is exactly where this matters. Where the browser does support it, it is asked for as well, so the address bar goes too. Switching between any of the options is instant: the estimates and rating factors arrive with the track, so nothing is re-fetched.

The flag panel uses the same state as the race-office page. It only displays flags that are currently up: configured numeral class pennants, P during the preparatory period, and Code flag S while a course is shortened.

## What it shows after racing

When all entries are no longer racing, the **Leader board** tab appears and opens first:

- Provisional IRC/YTC results, split by configured rating class where available.
- Public video links when configured and allowed.
- For a pursuit race, the finishing-order table instead of corrected times.

The Chart and Course analysis tabs then become a **record of the conditions**: instead of the live hut wind they use the **average wind over the race**, measured from the first warning signal to the last boat finishing. TWD is vector-averaged (a plain mean would be wrong across north), TWS is a mean with its range shown alongside, and the gust figure is the highest seen in that window. The chart's wind arrow uses the same averaged direction, and nothing on those tabs refreshes any more, because the numbers no longer change. The live GPS tracking columns are dropped from the Entries tab at the same time.

Wind samples inside a race window are exempt from the normal 24-hour history purge, so an old race's averages are still available months later.

### The 3D replay film (v1.002)

Once a race has been rendered as a [3D replay film](RACE_REPLAY_3D.md), a link to it appears by itself: a **3D replay** button beside *Open* in the races list, and **Watch the 3D replay** in the race page header under the entry counts. It is also in each race's section of a published results document.

Nothing appears until the film is actually in the bucket, so an unrendered race looks exactly as it did before, and race day is unchanged — a film exists days after the racing, not on the water. The link goes straight to the storage bucket rather than through the hut, so it plays whether or not the clubhouse PC is switched on and a large download never crosses the hut's 4G connection.

## Phones and tablets

There is now **one responsive public page** for every device. On a phone the wide data tables (entries, results, leg analysis, pursuit start times) become **one card per boat/leg** with the column heading beside each value, so nothing is cut off and the page never scrolls sideways; the course chart, countdown and wind chips size themselves to the screen. On a tablet or PC the same page shows the familiar tables.

**Held sideways, a phone is a tablet.** It is wider than the point where the cards give way to ordinary tables, so a phone in landscape and a tablet get the same layout — and there the results table is nine columns. If it does not fit, **the table scrolls sideways inside its own panel**; the page itself never moves, so the rest of the screen stays put while you drag the columns across. Before v0.257 it was simply cut off at the right-hand edge, with nothing on the page to say the *Finish video* column was there at all.

The page also stops spending a third of a phone screen on borders. The results used to sit inside three nested frames — the tab panel, a card round each class's results, then a card per boat — each with its own border and padding. On a phone the middle frame keeps its heading and gives up its box, and the page gutter now scales with the screen instead of giving a sideways phone the margin meant for a desktop.

The old separate mobile addresses (`/public/mobile/current`, `/public/mobile/race/<id>`) still work — they now redirect to the responsive page — so links already shared with competitors keep working. There is no longer a desktop/mobile toggle to choose between.

## Security note

Public pages are deliberately read-only. The race-page **Competitor home** link returns to the landing page. Do not put private administrative notes in public-facing race fields, because those fields may be visible to competitors. Race-office controls, settings, user administration, boat management, finish-time entry and hardware controls remain behind `/admin` login.

The public live camera route only serves the latest branded preview JPEG and a sanitised status message. It does not expose video settings, FFmpeg command lines or logs. When R2 live-image mode is enabled, competitor browsers fetch the JPEG from the configured R2/custom-domain URL instead of this hut route.

## Results display

When a race is finished, the public specific race page uses the same classed IRC/YTC result groups as the race-office Results tab. Each configured class is shown as its own table, with start label, start time, status, elapsed time, corrected time and finish-video links. Entries missing ratings or outside the configured bands are shown in the same excluded/unclassed cards as the race-office view.

## Public read-only API

The app also exposes `/api/current_race_course` for external read-only displays such as the Mojito range-and-bearing app. It returns the current race metadata, selected course, parent/display marks, expanded compound-mark geometry, full mark coordinates and leg bearing/distance data in one JSON response.

`/public/race/<race_id>/positions` returns the live GPS positions and position-on-the-water order for a race (used by the public race page). It is read-only, returns nothing while GPS tracking is disabled, and only serves races the public may view.

This endpoint is deliberately read-only and contains the same kind of course/mark information that is already visible to competitors. Administrative race-office APIs remain behind `/admin` login.

## Public media and bandwidth

If Cloudflare R2 public-video publishing is enabled, public video links redirect to the uploaded R2 object after the race is finished. This keeps repeated crew viewing off the hut 4G connection. Until the R2 copy is ready, the public page shows the clip as processing/uploading instead of linking to the local evidence-quality file.

The public live-camera image can also be served via R2. With **Settings → Video Recording → Public live image → Upload latest JPEG to Cloudflare R2**, the hut uploads one branded JPEG to `<prefix>/live/latest_public.jpg` every few seconds and competitor browsers refresh the public R2/custom-domain URL. The public page still comes from the Race Officer app, but the high-frequency live image requests no longer hit the hut origin. Public branding keeps the club logo at top-left and rotates one sponsor logo at a time in the top-right; the live JPEG uses the sponsor selected for the current 5-second slot. From v0.122, the public competitor pages centre the club logo and public action/navigation buttons, and the wind-history TWS axis is anchored at 0 kt so wind-speed changes are shown against a consistent baseline. From v0.123, the shared wind-history plot uses seven labelled scale positions for both TWD and TWS, and the TWS trace is drawn as horizontal bars from 0 kt to the measured wind speed.

## Pursuit races

For a pursuit race the specific race page shows a **Start times** panel instead of the class entries list and course analysis. It lists every boat with its sail number, its rating in the race's chosen system (IRC or YTC) and its individual start time, sorted with the earliest (slowest boat) first. The **next boat due to start is highlighted** and each row shows a live countdown to its start, so a competitor can see exactly when to start. The single finish-signal time is shown below the list. Once the race officer has recorded the finishing order, the page switches to a **finishing-order** results table (position, boat, sail number, status) — there is no corrected-time table, because a pursuit is decided by the boats' order on the water.

## Remote access

For Cloudflare Tunnel setup, see `docs/REMOTE_ACCESS_CLOUDFLARE.md`. The tunnel should forward the whole app service to `http://localhost:5050`; do not configure the tunnel to forward only `/public`, because the public page also needs `/static/...` CSS, JavaScript and image files.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
