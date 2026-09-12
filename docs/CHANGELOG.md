# Change log

## v1.003

**Nothing in this release changes the race-office app.** It is all the 3D replay: the
renderer, what the film looks like and how long it takes to make. The hut PC gains nothing
by installing it; a render machine gains all of it.

**The films had no titles.** The card at the front naming the race and the card at the end
with the corrected times are drawn from the scene, and the renderer never asked for them — so
every film made on a render machine has opened on the water and stopped dead at the last
finish. They were built by the exporter, which is the developer's path, and nothing carried
that across when rendering moved to another machine. The third thing to go that way, after
the typefaces and the club branding, and like those it failed silently.

**The results card lost a whole rating above five boats.** It dropped tables that would not
fit, so a club scoring IRC and YTC together lost the YTC winner from the film with nothing on
the card to say so; above about sixteen boats it then began to overlap itself. Both came from
type shrinking more slowly than the bands holding it, so below a point it outgrew them. Each
font is now measured against its own band, the rating headings have a floor of their own
because a heading is per-table overhead rather than per-boat, and a fleet too big for the card
is trimmed with *and 12 more* rather than a whole rating being dropped.

**Boat names sat on the boats.** The name plate was placed a fixed distance above the hull,
but a mast is fifteen metres and how tall that is on screen depends entirely on where the
camera is: 2,507 of 32,029 boat-frames in one race had a rig taller than that fixed offset.
The overlay now knows where each masthead is and the plate clears it, moving sideways rather
than stacking into a column when boats overlap, and keeping off the club burgee.

**Each boat's speed is on its name plate**, from the tracker's own reading — the same figure
the race office sees, and the same samples the boat under it is animated from.

**The coastline is four times sharper.** Satellite imagery was fetched at 1.44 m per pixel,
mosaicked, stored — and then resampled down to 7.24 m per pixel in the last step before
Blender saw it, so four fifths of it was thrown away. The cap is now 4096 pixels rather than
2048, which costs nothing in downloads because the detail was already on disk.

**Compositing is several times faster.** Over half of that stage was converting a picture
into the same picture: PyAV's `to_image` takes a slow path from YUV to RGB and PIL then
copies the whole frame again to add an alpha channel. Asking the scaler for RGBA in one pass
is 0.61 ms against 11.25, and byte-identical. The stage is also split across cores now and
the parts joined without a second encode, so the one part of a render that used a single core
no longer does.

**A re-render reused the old boat positions.** The overlay track — every boat's place on
screen, frame by frame — was written only if the file was missing, so re-rendering a race
after correcting its GPS tracks produced corrected boats with the name plates still following
where the old fixes had been. It now carries a fingerprint of the scene it was made from and
is rewritten when that no longer matches.

**A boat carries one tracker, and the app now holds you to it.** Two trackers could be
pointed at the same boat, and every fix from both was stamped with it, so the boat's track
became the two devices interleaved. In one club race a spare aboard *Mojito* was also paired
to *Crackajack*, and the replay drew Crackajack flipping between the two boats several times
a minute. Assigning a tracker now takes the boat off any other and says which, in a warning
that is finally amber — `.message.warning` had no style at all, so every warning in the app
had been rendering in the green of a success. Data already recorded that way is repaired with
`scripts/fix_track_misattribution.py`; the symptom and the cure are in `TROUBLESHOOTING.md`.

## v1.002

**A race, as a film.** The app can now turn a sailed race into a 3D replay: the fleet on the water
in three dimensions, sailing the course they actually sailed, over the real coastline, with the
start-hut camera cut in at the start and at each finish. It is built from what the race office
already recorded — GPS tracks, the course as sailed, the wind log, the results and the published
videos — so there is nothing extra to do on the water to get one. On a race's **Results** tab,
beside *Replay this race in the bar*, there is **Render a 3D film**.

**The hut PC does not make it, and does not try.** A replay is around eleven thousand frames and
the race-office PC is a fanless box that is also running the race. Pressing the button writes a job
into the same R2 bucket the club's race videos already use; a render machine somewhere else — a
desktop with a graphics card, switched on when there is something to make — picks it up, makes the
film and puts it back beside the clips it is made from. Neither machine connects to the other: the
hut only pushes and the renderer only polls, so nothing has to be opened up at either end. A
dashboard card says whether there is a render machine at all, because a job queued against one that
is switched off otherwise looks exactly like one being worked on.

**Competitors get the film without being sent it.** Once one exists, a link appears by itself on the
public races list, on that race's own page and in each race's section of a published results
document. Every one of them goes straight to the bucket rather than through the hut, so a film plays
whether or not the clubhouse PC is on and a large download never crosses the hut's 4G connection.
Nothing shows until the film is really there.

**The fleet is trimmed to the wind of the moment, not the race's average.** The scene carries the
whole wind log. For one club race the mean was 162° while the wind actually went from 122° to 196°
and built from two knots to eight, so an average would have shown a fleet trimmed to a wind that was
only briefly true — and the shift that decided the race would not have been on screen at all. Heel
follows the breeze and a spinnaker goes up when the angle calls for one. A true wind readout sits
between the race clock and the course board.

**A render machine is built from a release ZIP**, like everything else at this club. The renderer
ships inside it, so there is no separate download and no source checkout: unzip, make a virtual
environment, install Blender. It needs a graphics card — EEVEE will not start without one — and it
holds no club race data: the course, the marks and both lines are baked into the job it is handed.
Full procedure in `deploy/render_machine/README.md`, which is also on the Documentation page.

**Map tiles are cached on the render machine rather than in R2.** The first film over a stretch of
water fetches what it needs and every film after it is free. A club renders a handful of courses and
the whole of Pwllheli bay is a few hundred tiles, well inside Mapbox's free tier, so a second copy
in the bucket bought nothing and cost a cache to keep in step.

**The start line, finish line and radio/limits cards are off the dashboard.** They never changed.
That is Sailing Instructions, not a dashboard, and three cards of standing text were taking room on
the page the race office leaves open all day, above the things that do change.

**A release is 17 MB, not 44.** `data/dem` — 30 MB of map scratch, untracked, and present only on a
machine that had once run the fetch scripts — was being swept into the ZIP. Nothing installed reads
it. A release built on the wrong machine went out at 44 MB over the hut's 4G link.

**Documentation.** A new guide, `docs/RACE_REPLAY_3D.md`, and a chapter in the Reference Manual, a
section in the race-day guide and one in the Competitor Guide. Two older errors went with them: the
manuals described a dashboard that no longer exists, and both the manual and the installation guide
claimed the app builds a QR code and competitor share links. It does not, and never did.

## v1.001

**Security, from an external check of the live site.** A read-only probe on 10 September 2026
found no critical or high issues: everything gated in the code is gated in production, the
login lockout works, cookies and security headers are as intended, and no route can be
enumerated. It found two medium items, and they turned out to be one job.

**The relay is the only way to the app.** The club's address is served by the relay, which
proxies through to the hut over a Cloudflare tunnel. Caddy now authenticates that hop with a
Cloudflare Access service token, so the tunnel hostname answers the relay and nothing else,
and the rate-limit and firewall rules scoped to the club's address cover everything rather
than most things. The token is presented from the configuration here and the matching policy
lives in Cloudflare, which is deliberate: the relay can be made ready before the policy is
applied, so there is no moment in between where it cannot reach the hut.

**Links pointed at an internal hostname.** Reached through the tunnel, the app sees the
tunnel's own `Host` header and a plain `http` scheme, so every address it built for the
outside world came out as `http://hut-origin/...`. The one with a consumer today is the
branding manifest the live-stream relay reads, which is what makes the ordering below matter.
**Settings &rarr; Web server &rarr; Public address** fixes that and every future external
address with it; leave it empty and nothing changes, which is right on the hut network.

The order matters, and it is the reason these shipped together. The relay downloads the logo
addresses out of that manifest exactly as given, and it swallows a failed download, so closing
`hut-origin` before correcting the addresses would have quietly taken the club's branding off
the live stream with nothing to show why. Set the public address first.

Also here: the relay's installer now gives Caddy the environment it needs and restarts rather
than reloads it, because a reload re-reads the Caddyfile but not the unit's environment &mdash;
the kind of difference that shows up as "I set the token and it still does not work". The
relay README, the env template and the printed relay guide carry the new token and the
ordering.

## v1.000

**The MVP label comes off.** It has run the club's racing for a season &mdash; starts, finishes,
IRC and YTC results, series scoring, GPS tracking, evidence video, the competitor pages and
published results on the club website. It stopped being a prototype some time ago and the name had
not caught up.

What has *not* changed is who is answerable for a race. The app recommends, records and calculates;
the race officer decides. The sidebar still says so, results are still provisional until confirmed,
and the wording throughout is unchanged on that point &mdash; only the word *prototype* has gone,
because it described the software's maturity rather than the rule.

**A release now unpacks into `pwllheli_race_officer_v1_000`**, not `pwllheli_ro_mvp_v0_<n>`. The
Windows guides name the new folder. An existing hut install keeps working where it is; move it when
convenient rather than on release day.

Fixed on the way: the ZIP builder took the version's last dot-component and pasted it after a
hardcoded `v0_`, so 1.000 would have shipped a folder called `pwllheli_ro_mvp_v0_000`. It now
derives the whole version, and `release.ps1` looks for the same name the builder writes &mdash;
those two disagreeing is a release that stops at "ZIP not found".

**Every screenshot in the four guides has been re-shot** against 1.000, so the manuals show the
current app rather than one with an MVP wordmark and a Fleet column that retired in v0.284.

## v0.285

**A finish taken on the horn switch is a finish video.** Reported from the club website: the
competitor page showed **View** against three boats' finish videos and the published document, for
the same race and the same three boats, said **No Video**.

The race officer can finish a boat two ways. The *Finish* button schedules a clip typed `finish`.
The **physical horn switch** logs a horn event and schedules a `manual_horn` clip; assigning that
horn time to a boat afterwards writes the finish onto the entry and stamps the entry's id onto the
clip, but leaves the clip's type alone &mdash; reasonably, since it is a recording of a horn.

So the rule that matters is that **a clip carrying an entry is that boat's video**, whatever its
type. The competitor page has always used exactly that. The published builder asked for
`clip_type == 'finish'` and so dropped every finish taken on the horn: on the club's own database,
10 of 26. Measured against that database, the published document goes from 0 video links and 16
*No Video* to 10 and 6, the remaining six being boats with no clip at all. A test now pins the two
pages to the same answer so they cannot drift apart again.

**And the document only carries links that work away from the hut.** When a clip had not been
published to Cloudflare R2, the fallback built an address out of whatever host the race officer
happened to publish from: one measured series document held 43 links reading
`http://raceofficer.local:5050/public/video/clip/124`, every one of them dead for the people it was
published for. A row that says there is no video is honest; a link that cannot resolve is not. The
competitor pages keep that fallback and should &mdash; they are served *by* the hut, so a hut
address is right there.

One visible consequence: **Preview HTML now shows *No Video* wherever the published file will**,
rather than a link that only resolves because you are looking at it from the hut. That is the
preview's job. Publishing videos publicly is what makes one linkable, under `video_public_r2_*` in
Settings.

## v0.284

**The boat's fleet/class field retires**, the last of three fields that answered "what class is this
boat in" and disagreed with each other. The race's own label went in v0.271 and the per-entry
override in v0.276, both because a class comes from the series' rating bands. The boat's survived
that round on the grounds that it told two boats apart in the picker. Nothing but the form ever
wrote it &mdash; neither the IRC nor the YTC import sets it &mdash; so it was only ever as good as
what somebody typed: of 21 boats, 11 had none and three held the literal word *None*.

Removing an input is not the whole job. The save still read `class_name` from the form, and a save
that reads a field the form no longer sends writes an empty string over every boat &mdash; the trap
v0.271 found with the race's label. There is now a test that saves a boat and checks its stored
class survives. The **fleet scope** goes too: *Add all &lt;fleet&gt; boats* and the Virtual Race
Officer's *"enter the IRC 1 fleet"* both matched active boats against that text, so neither end of
the comparison is written any more. *"Add the fleet"* now means every active boat rather than
silently none. `entries.class_name` is untouched: it is the record of seasons already scored.

**The rating listings download while you are still typing.** Looking up a boat waits on the RORC
club listing (379 KB) and the YTC sheet (265 KB) &mdash; 3.2 s cold on a wired line, and rather
more through the hut's tunnel. Both now start when the Add/Edit boat page opens, in parallel, and
the search **joins** that download rather than starting a second one. The head start comes off the
wait second for second: with a four-second gap to type a boat name, the search waits 0.13 s instead
of 3.24 s.

### Found while chasing a slowdown

Reported as the app feeling slower, and after measuring it the cause was never established: the
released v0.283 and this build serve pages in 3&ndash;5 ms side by side on the same machine. These
are real faults found on the way, worth having on their own merits; none of them is offered as the
explanation.

**A finished race stopped walking its own GPS fixes at some point after it finished.** The progress
walk ran from the gun to *now*, so opening a race sailed on 21 July read six weeks of tracking that
had nothing to do with it: 98,362 fixes across nine boats, 81,849 of them for one boat, and another
day's worth every day it sat in the database. A race whose boats have all finished now stops at its
own last finish, plus an hour so a corrected finish time is still inside the window. **A race with
boats still racing is walked to now**, bounded only by a three-day backstop for one left open and
forgotten &mdash; deliberately not the replay's twelve-hour cap, which would freeze an ISORA
passage race mid-Irish-Sea and quietly stop detecting its finishes. Race sheet for that race: 518
ms to 15 ms.

**A camera that is not answering no longer costs every request 286 ms.** The recorder relaunch runs
from a before-request hook, and a launch that failed immediately cleared its config hash, so the
next request tried again &mdash; each attempt spawning FFmpeg and blocking 250 ms. Failed launches
now wait one watchdog interval; a settings save still retries at once.

**FFmpeg is killed when the app closes.** It never was: it is a child process, not a thread, so
every run of the app left its recorder and preview running. Confirmed both ways.

**And the recorder log is bounded.** It had reached 65 MB of failure messages, never rotated &mdash;
and the diagnostics tail read the whole file to show its last 1600 characters. It now seeks (23 ms
to 0.25 ms) and the log rotates at 8 MB, keeping two generations.

## v0.283

**A boat with no tracker on it is not out on the water.** Reported from a race with no trackers
assigned to any boat in it: the race sheet and the competitor page both listed a boat at 96.73 nm
to go, first on the water, last fix *39832m ago* &mdash; twenty-seven days. Two deliberate
behaviours had met. An entry's tracker falls back to the last device the boat was ever seen on, so
a race sailed months ago still draws its track after its trackers were deleted; and the
leaderboard's display position reaches outside the race window on purpose, so a tracker that
reported three minutes before the warning signal still puts its boat on the chart. Together they
said: any boat that ever carried a tracker is at wherever it last was, for ever. That reach is now
bounded by an hour &mdash; the app's own definition of a tracker that is not reporting, the red dot
on the Trackers page. Past it the display falls back to the last fix inside the race, which is what
an old race wants and is correctly empty for a race that has not started. The distance-to-go went
with it, being worked out from that fix.

**A boat with nothing to report says which kind of nothing.** With the stale position correctly
discarded, what was left was a fleet row with no fix but a progress count &mdash; *0/7 marks, next
1* &mdash; sitting beside boats that plainly said *No tracker*, and nothing to say why two boats
with no position looked different. Such a row is now dashed across and gives one of two reasons:
**Not reporting** &mdash; there is a tracker on the boat and nothing has been heard from it in the
last hour &mdash; or **No tracker**, meaning there is no tracker on the boat today. That turns on what the boat carries **now**, not on whether a track can
be found for it: a boat whose tracker was taken off it has not gone quiet, and the historical
fallback that keeps an old race's track drawable must not be allowed to call it quiet.

**A course nobody set is not a course to sail round.** A race whose own header read *Course: not
set yet* listed a boat at **0/7 marks, next 1, 5.05 nm to go**. Every one of those numbers was real
arithmetic &mdash; against course 1, which nobody picked: `course_no` defaults to 1 and the course
lookup hands that back as a fallback so the chart and the leg analysis have geometry to work with.
v0.276 drew the line for the race sheet &mdash; no board, no chart line, no predicted time and
nothing selected in the picker until somebody has chosen a course &mdash; and the spoken
announcement refuses on the same ground, because reading out a guessed course would send the fleet
the wrong way. The **rounding sequence** was the piece nobody asked, and three things read it: the
fleet list, the replay, and **GPS finish detection**, which was walking boats towards marks of its
own invention and watching for them to cross the line. The guard now sits in the sequence itself
rather than in each of the three. A boat's position, speed and fix age are its tracker's own report
and still show; only what is derived from a course goes.

**The published results page fits a phone.** Measured at 1440, 820 and 390: the phone overflowed
sideways by 438px and gave the banner 30% of the screen before anything worth reading, with the
first table below the fold. The results table is 804px wide and none of its columns can go, so it
now scrolls inside its own box &mdash; with a shadow on whichever edge has more table beyond it,
because a table clipped mid-column otherwise reads as a broken page. A table that scrolls is
normal; a page that scrolls is broken. The 62px the tablet overflowed was a different fault
wearing the same clothes: the sponsor strip only wrapped below 700px, so between 700 and about
1100 the last logo hung over the edge. Zero horizontal overflow at all three widths now, and a
phone gets a smaller banner.

**And the footer no longer tells you to upload the file to the club website**, which since v0.281
the app does itself &mdash; and which is a puzzle to read *on* the club website.

## v0.282

**The published results file is a tenth of the size.** It embeds every logo so it stays one file
somebody can upload anywhere &mdash; and it was embedding the **originals**: 1.48 MB of PNG across
six logos, which base64 inflates by a third. A results page went out at **2 MB**, and since v0.281
that goes over the hut's 4G on every publish. One sponsor logo alone was 742 KB, displayed at
160px wide. They are now scaled to twice their displayed size, and a real series document measures
**271 KB** where it measured 2 MB. A logo already below that size keeps its original, because
re-encoding a small palette PNG as RGBA makes it bigger &mdash; measured, one grew from 12 KB to
15 KB. Without Pillow the full-size embed is used, as before: a larger file is a better failure
than no logos.

**A race that has not started no longer says its fleet is racing.** An entry is stored `RACING`
from the moment the boat is entered, because every calculation keys off it &mdash; elapsed time,
results, GPS finish detection, the "still racing" count. Published raw, a race hours from its
warning signal listed a whole fleet as *RACING*, which was true of none of them. The document now
uses the same `entry_display_status` the race sheet and the competitor page already use:
**PRESTART** until the warning signal, and anything else &mdash; FINISHED, DNF, OCS &mdash;
unchanged. The Status column stays: it is the only place a DNF, RET or OCS appears in a race
table.

**And the guides describe the app again.** v0.281 shipped the Publish button with nothing about it
in any of the four PDFs, which were rebuilt for the version stamp alone &mdash; so they carried a
v0.281 cover while both manuals stated *"the app does not publish directly to a website"*. The
Series Guide and the Reference Manual now cover publishing, the stable link and the two objects;
the Competitor Guide covers the **Published results** link; and `SETTINGS_AND_ADMIN.md` notes that
the video R2 settings drive results publishing too.

**Every screenshot has been re-shot.** All 58 dated from before v0.276, so the guides showed the
Class column that no longer exists, the fleet field that retired, the old dashboard, and a series
page without Delete or Publish. Two capture scripts had been failing silently for longer than
that: they found a race by matching `innerText` on the Races page, which is empty for rows inside
a **collapsed roll-up**, so they ran against `/admin/race/None`. `textContent` does not care
whether a thing is rendered.

## v0.281

**Results go on the club website from the race office.** *Download HTML* hands you a file, and
then somebody with an FTP client has to put it somewhere &mdash; a step that happens on Monday if
it happens at all. **Publish to website** on the series page renders the standings as they stand
and uploads them, so they can go out at the end of each day's racing.

It uses **the same public Cloudflare R2 bucket the race videos already go to**, configured under
Settings → Video recording. Results are a second kind of public artefact from the same race
office; a second bucket would mean second credentials, a second base URL and a second thing to get
wrong, for no benefit anyone could name. The button appears only once that bucket is set up.

Each series publishes into `results/series-<id>/`, and **every publish writes two objects** &mdash;
the pair is the design:

- a **timestamped** one, immutable and cached for a year: the record of what went out that
  evening, which never changes afterwards;
- **`latest.html`**, overwritten each time and told to go stale after a minute: the link for the
  club website, pasted once and current all season.

Getting those cache headers the wrong way round would leave Cloudflare serving Saturday's
standings on Wednesday &mdash; the exact fault this feature exists to prevent, arriving by another
route. The timestamp runs to the second, because two publishes in the same minute (a mis-click, or
a finish remembered late) must not be one object with two entries pointing at it.

**Published&hellip;** lists what has gone out, with the club-website link in a box to copy and
every dated publish openable, so *what did we put out on Saturday evening* has an answer. The
copy button selects the field as well as copying it: the hut PC is not always on https, where the
clipboard API silently does nothing, and it says *Press Ctrl+C* rather than claiming a copy that
did not happen.

**And the competitor landing page carries the link.** Each series roll-up shows **Published
results** pointing at the same `latest.html`, so a competitor on the water and the club website
are reading the same document.

Nothing is recorded unless the upload succeeded, so the list can never offer a link that was never
written; a failure says what went wrong rather than "try again", the bucket being over the hut's
4G link and the credentials somebody else's to fix.

## v0.280

**Six races, one order.** The series page, the Races page and the competitor page each sorted a
series' races by `COALESCE(start_time, created_at)` &mdash; two ascending, one descending &mdash;
so the same six races appeared in three different sequences. Worse than inconsistent: that
expression sorts one race by when it **starts** and the next by when it was **typed in**, which
are different clocks. Give Race 1 of a September series a start time and its key jumps from
"created on the 2nd" to "starts on the 4th", moving it from the middle of a list to the end of it.

One rule now, in `core.races.races_in_order`: a start time when the race has one, otherwise the
name read **naturally**, so a series set up in advance lists Race 1 to Race 6 rather than in
whatever order somebody happened to create them. Naturally, because plain alphabetical puts
*Race 10* before *Race 2*.

A fully scheduled series is unaffected, and that is not a nicety: a race's **number in its
series** is its position in that list, and it decides which Sailwave column a race lands in. Every
race having a start time means this is start-time order, exactly as before. A test holds it.

**And a race nobody has scheduled says so.** A new race's entries are added `RACING` &mdash; which
is right, it is how a boat that has not finished is recorded, and it is not the same as the race
being under way. A series set up in advance showed six races all reading **Racing** before a
course or a start time had been set for any of them; then the first got a start time and changed
to *Start sequence pending* while the other five went on claiming to race. "No start time" is
asked before "any boat still out" now, and the label is **No start time set**.

The competitor page and the dashboard had that ladder of branches duplicated line for line, with
the same fault in each. It is one function, so the hut and the competitors cannot be told two
different things about the same race.

Left alone deliberately: `get_current_competitor_race` and the assistant's recent-races list still
order by most-recent. They ask which race is *current*, not what order a set of races goes in, and
changing them would change which race the app calls current.

## v0.279

**Opening a race no longer scrolls past the race.** The race name, the course board, the flags
and the countdown were above the top of the screen the moment a race sheet opened &mdash; measured
at scrollY 254, about 200ms after load. `history.replaceState` does not scroll, which is why this
looked impossible; but the fragment it writes is still in the document's URL when the browser
runs its **scroll-to-fragment** step, and the browser retries that step as the page settles,
finds `#tab-course` and jumps to it. The fragment is no longer written on the first load. One
already in the URL still works, because a redirect asking for `#tab-admin` after a save means it.

The competitor page hit this exact thing and guarded it, with a comment in the code saying so.
The race sheet never got the same fix.

**"Publish HTML" is "Preview HTML".** It opens the results page in a tab to look at. Publishing
is what the website export does, and a button claiming it on a page carrying a season's results
is the wrong word to hesitate over.

**And the downloads are named after the series.** `series_1_results_publish.html` and
`series_1_irc_sailwave.csv` say neither which series nor which run, in a folder where they land
more than once as a season is scored. They are now
`Autumn-Series-2026-results-2026-09-01-1642.html` and
`Autumn-Series-2026-irc-sailwave-2026-09-01-1642.csv`. A Welsh series name keeps its circumflexes:
the `Content-Disposition` carries an ASCII form in `filename=` and the real one in `filename*`,
which is what every current browser prefers &mdash; without the second, *Gŵyl* arrives as mojibake
or is dropped for a default.

**The race sheet's two Sailwave exports are named the same way.** `race_9_irc_sailwave.csv`
names a row id, not a race. They are now `R9-Summer-irc-sailwave-2026-09-01-1642.csv`, with the
series prefixed only when it adds something: half the club's races are called *R1*, which on its
own names nothing, and the other half are called *Welsh IRCs Cruisers - Race 2* inside a series
called *Welsh IRCs YTC Cruisers*, where prefixing puts the club's name in the filename twice.
Sharing a real word is the test &mdash; an exact-substring check said those two names differ,
which is true and beside the point &mdash; and it counts letters only, three or more, so a shared
*2026* is not two names having said the same thing.

Writing that turned up a defect in the header helper itself. The ASCII and UTF-8 forms of the
filename were slugged by different rules, so for a plain ASCII name they disagreed: the header
carried a `filename*` contradicting its `filename`, and the browser preferred the worse of the
two &mdash; `Cruisers---Race-2`, from a `" - "` the other branch had already collapsed. One
slugging rule now, differing only in whether the accents survive, so `filename*` is emitted only
when it genuinely differs.

**Trackers sits above Hut power** in the side menu, which is the right way round for how often
each is opened on a race day.

## v0.278

**The dashboard stops saying things twice.** The wind card drew an analog gauge with the
direction and the speed in the middle of it &mdash; the whole point of an instrument you read at
a glance &mdash; and then repeated both underneath as chips, beside a *Source* the status line
directly above already tells you. The chips go, and the styling with them: a CSS rule with no
markup left is the sort of thing that survives for years because nobody can tell whether it is
still holding something up.

The **Current race** box loses two lines nobody acts on: *"The public site root redirects to this
race for competitors"* and *"Public address: /"*. The redirect still happens; it is simply not
something a race officer does anything with on race morning.

**The two dashboard cards now have the same head.** *Current race* was an `h2` in a
`.section-head`; *Wind* was an `h3` in a head of its own. So one was set in the reading face with
its rule drawn by the row, and the other in the condensed uppercase the theme gives section
headings, with a rule that stopped at the end of the word and sat higher up the card. Side by
side, the two rules did not line up across the page. Both are the same head now.

**And the rules sit higher, because of a margin nobody could see.** `style.css` sets
`h2 { margin-top: 0 }` and stops there, leaving the browser's 0.83em **bottom** margin in place
&mdash; 16.6px of it, measured, inside a flex row, pushing that row's rule down the card for no
reason on the screen. The theme already reset this for `h3`; `h2` was missed. The rules moved up
18px. Swept across the dashboard, Races, Series, Boats, Marks, Trackers, Settings and a race
sheet to check that nothing which puts a paragraph under such a heading lost its gap: each one
still clears the heading by 15px, from the paragraph's own top margin.

**The Current race card carries the course board**, between the countdown and the way in, which
is what the empty half of that card was for. Empty until a course is chosen &mdash; the server
sends the marks rather than a course number, because a new race always has a number and that is
not the same as a decision &mdash; and shortened when the course has been.

**Fixed: a race could lose "course not set" overnight.** `course_set` arrived to tell a race's
fallback course number apart from one somebody chose, and a back-fill marked the races that
already existed as chosen &mdash; on the reasonable evidence that *a race with a start time has a
course*. That back-fill lived inside `init_db`, which runs on every request, and nothing told it
to stop. So it kept applying that rule to races created long afterwards, for which it is false:
set the first warning signal before choosing the course, restart the app, and the course board,
the chart and the competitor page all went back to showing the fallback. The whole point of the
column, undone by its own migration.

It survived because `init_db` is cached per process, so the damage arrived at the next restart
rather than the next request &mdash; the race was right all afternoon and wrong the next morning,
which is the worse of the two. `ensure_column` now reports whether it was the call that added the
column, and the back-fill runs only then.

**And the two SIM dialogs have room to breathe.** They were laid out as tightly as the tracker
table they grew out of, which is a page wide where these are a dialog. `padding: 4px 0` in
particular gave the columns **no horizontal gap at all**, so in the narrower per-SIM dialog a row
label ran straight into its number &mdash; *This billing period6.60$0.20*. Measured against the
real stylesheets: a row was 29px and is now 35px, with 14px between columns and the first and
last cells still flush to the edge so the rules run the full width of the dialog. The balance
list underneath gets the same treatment.

## v0.277

**A competitor on the water is no longer left on the race they have just sailed.** The journey,
as reported: open the competitor home on a phone, press **Go to current race**, sail the race,
finish. The race officer finishes the fleet and moves on to the next race &mdash; and the
competitor sits on the race that is over, because that button handed them `/public/race/<id>` and
pinned them to it. The only way forward was to know to go back to the home page and press the
same button again, which is not something to work out one-handed on a wet phone.

**Go to current race** now opens `/public/race`, which *follows* whichever race is current: the
same distinction `/bar` and `/bar/<id>` have always drawn for the clubhouse television. It says
on its face that it will move on, and offers a link to stay on one race. `/public/race/<id>` still
pins, which is what a link shared to somebody needs and what looking back at last week's race
needs &mdash; and a pinned page that is not the current race now says so and offers the way
across, so a bookmark no longer leaves somebody watching a finished race with nothing to explain
where the racing went.

Almost no new client code drives it. The page already polls a state signature every two seconds
and reloads when it changes, and that signature is computed over a dict containing the race id
&mdash; so pointing an unpinned page at the current-race state endpoint makes the race officer
moving on look exactly like a course change, something the page has always known how to pick up.
The positions and track endpoints stay pinned to the race being rendered: they are fetched
between reloads, and following the current race there would draw one race's boats over another's
course for as long as it took the page to notice.

**Where that reload lands took one more change, and it was not only about a new race.** The URL
carries the open tab as a `#fragment`, and a browser honours a fragment before any of the page's
own script runs &mdash; so *every* reload landed at the tabs, wherever the viewer actually was.
Look at the chart, scroll back up to watch the countdown, and the moment the race officer changes
the course you are thrown back down to the chart, never seeing the course board or the clock
change. The reload was carrying you away from the very thing it was delivering.

So the tab moves to a query parameter on every state reload and there is no anchor left to jump
to, and the page then lands somewhere deliberate: **a different race opens at the top**, because
the news is the header &mdash; name, course board, flags, clock; **the same race comes back
exactly where the viewer was**, which is not what "keeps its place" used to mean here. The
position is carried in `sessionStorage`, keyed by race so it cannot be applied to another one,
cleared on a race change and read once. No storage &mdash; a private window &mdash; and the page
simply opens at the top.

**And the explanation is behind a `?`.** Three lines saying the page follows the racing pushed
the header down a 390px phone, and it is read once and then in the way, which is what the `?` is
for everywhere else in the app. The competitor page builds its own `<head>`, so it needed its own
copy of the one-line handler `base.html` gives every race-office page.

## v0.276

**A series is set up in one pass.** Creating one used to be a name-and-description box sitting
permanently open above the list, so the first thing the Series page showed was a blank form for
something done a few times a season — and everything that actually decides how a series scores,
the discard profile, the rating bands and the default start plan, could only be reached
afterwards under *Edit series details*. The honest flow was: create a blank series, land on it,
immediately open the editor. Creation now has its own page, reached the way Add a boat is, and
it carries the whole setup.

It is **literally the same form** in both places: `templates/partials/series_form.html` is
included by the add page and the editor, and both routes read it through `_series_form_values`.
Six rating bands, a six-start plan and a script that relabels the start checkboxes from the band
names is not a thing to keep two copies of — the first band added to one would have been
silently ignored by the other. **Nothing is ticked on a new series**: the grid builder hands its
six rows back already enabled, which is right for the editor and would have meant every series
created with just a name arrived carrying six placeholder bands with no rating limits.

**A series can be deleted, once nothing is in it.** There was no way to remove one at all, so a
series created by mistake stayed on the list for ever and went on being offered in every race's
series picker. It goes from the Series list or from the bottom of *Edit series details* — and
only when no races are attached: one still holding racing shows *In use* and says how many races
are in the way and where to move them.

The restriction is the design, not a shortcut. A series is not just a label on a group of races:
it carries the rating bands, the start plan and the discard profile those races were **scored
under**. Deleting the row with races still pointing at it would drop them out of the standings
while they went on claiming to belong to it, with no record of what they had been scored against;
cascading instead would delete a season's racing from a button on a list page. So neither — the
races are moved out (each race's own Course & start tab) or deleted first. The guard is asked
twice, once by the page to decide whether to offer the button and again by the route, so a page
left open while somebody else adds a race cannot delete a season.

**And the start plan follows the bands as you tick them.** The first thing the new add page was
used for found the hole in it: the class checkboxes in *Default start plan* were rendered from
the bands the page was **loaded** with, so on a page that starts with none they were not rendered
at all and it said *No classes configured yet* however many bands you then filled in — leaving
the same two-step flow the page was built to remove. All six band slots are rendered now and
hidden until their band is ticked; ticking one reveals it and puts it on every start, which
matters because without that last part the fault only gets quieter — the band would appear,
sit unticked on every row, and the save would refuse the start for having no classes.

**The free-text fleet label retires** from the New race form and from Course & start. A race's
class comes from its series' rating bands, and the label was a second, disagreeing answer to the
same question. Removing the input was not the whole job: the save read it as
`request.form.get("class_name", "")`, so the moment the form stopped sending the field, every
save of that tab would have written empty over whatever the race had — on a tab a race officer
saves repeatedly on race morning, for a value the Races list, the series race table and the
published results all still show for the seasons run with it.

**Classic view is gone** — the switch, the route, the cookie, `RO_UI_THEME` and the dead CSS.
The theme links are unconditional now.

**Fewer words in front of the controls.** The Recommend page loses two paragraphs, a line about
background polling and two buttons the page itself had made redundant; the polar status line is
now silent when everything loads and a warning naming the consequence when it does not. The
Boats intro, the Marks storm note, the series scoring paragraph and the finish-line description
all move behind a `?`.

**A course nobody chose is no longer shown as this race's course.** A new race stores a fixed
course number as a fallback, so that the chart, the leg analysis and the shortening options have
some geometry before anybody has chosen anything. `course_set` has told that fallback apart from
a decision since v0.246 &mdash; but only the two page *headers* asked it. So a fresh race sheet
read **Course: 1** in its header, **Course not set** as the heading of the board directly below,
and drew the fallback's seven marks on that board and on the chart; and the competitor page said
**Course: not set yet** immediately above the same seven marks.

One flag now, asked by the whole of both pages. Until a course is chosen there is no course
board, no leg analysis, no predicted time, and the chart draws the marks and the start/finish
line but no course &mdash; `data-course-marks='[]'`, a state the dashboard and Trackers maps
already pass. **The course picker reads *Course not set* with nothing selected**, which is where
the contradiction was loudest: a number sitting in the box with no way to tell it had never been
picked. The Races list and a series' race table say *not set* in their Course column.

Saving the tab with the picker left on *Course not set* saves everything else and leaves the
course unchosen &mdash; setting a first warning signal before the course is known is an ordinary
thing to do on a race morning. Refusing would block that, and quietly writing the fallback is
exactly the confusion being fixed. Choosing one, recommending one or building one marks it, and
it only ever goes one way.

**And the fleet label stops appearing where it cannot be changed.** The free-text Class/fleet
field retired above, so the Races page's **Class** column, the same column on a series' race
table, the race sheet header's **Fleet**, and the competitor page's **Class** were a dash on
every race made since and unchangeable on the older ones. All four go. The value is still stored
and still exported; nothing that can no longer be set is still presented as if it could be.

**The Class column on the race sheet agreed with itself.** *Add entries* showed the series
rating band a boat is scored in &mdash; IRC1, YTC2. *Entries & finish times*, two tabs away,
showed the boat record's own free-text class &mdash; "Class 1", "YTC" &mdash; a different
question with a different answer, typed by hand at some point and never revisited. Two columns
with the same heading in the same race, saying different things. Both now render the same
expression, so neither can drift from the other, and the free text is still the fallback for a
race with no series to take bands from.

**And the free text is no longer typed in.** *Add from boat database* carried a **Class
override** box, pre-filled with the race's fleet label. It is the per-entry version of the
fleet field that retired above, for the same reason.

**Auto-confirm is off for a new race.** Detection stays armed &mdash; a finish caught is worth
more than one you were going to time anyway, and a missed one cannot be recovered. Recording it
without anybody approving it is a different question, and the honest default is the other way
round: for a manned race, accepting a detection costs one click, where a wrong finish written
straight into the results of a race still being sailed has to be noticed first. The club has
already been bitten by exactly that &mdash; every tracked race in the database finished 25 to
46 seconds late for months because 8 of the 12 courses straddle the finish line, and it went
unnoticed *because* the finishes were auto-confirmed, so the stored time and the detected time
were the same number with nothing to disagree about. The unmanned case opts in: the tick box, or
the on-the-water page, which turns it on by itself because there is nobody in the hut.

**The Races page stops wasting a screen, and opens the right roll-up.** It opened *Standalone
races* whenever any standalone race existed &mdash; which at this club is most of the year, so
the group least likely to hold the race you came for was the one always open and every real
season sat shut. It now opens the roll-up holding the **current race**, the same race the
sidebar's *Current race* link goes to, and marks that race in the table so an open roll-up
explains itself.

The whitespace was a separate fault with a measurable cause. `.race-rollup` sets `padding: 0`
because its summary carries its own; the theme layer loads afterwards and re-declares a bare
`.card { padding: 16px 18px }`, equal specificity and later file, so it won. Every collapsed
roll-up was inset twice and 99px tall for one line of text, with 44px of dead space under it
(18px card margin + 14px grid gap + 12px). Now 67px and 14px, and the page is 1039px where it
was 1625. The reset is two classes deep so the load order cannot take it back.

**The replay reply is bounded.** `/api/race/<id>/track` had no upper limit on anything. It draws
its window from the gun to the last finish, and one afternoon a finish was stamped **352 days**
after its race's gun — a mis-typed manual entry, since corrected — so the endpoint did exactly
what it was told and assembled a year-long replay. A single request took gigabytes on a machine
with eight.

The obvious diagnosis is "too many fixes" and it is wrong, which is worth recording because it
is what a bound would have been sized against. Measured: a fix costs about **43 bytes** a boat;
a **board snapshot costs about 470**, and the order-on-the-water board is sampled every five
seconds across the whole window whether or not a boat reported in it — roughly **1 MB per hour
per three boats before a single fix is counted**. So the window is the bound that matters, and
it is now capped at **twelve hours** from the warning signal. That is four and a half times the
longest race the club has ever sailed (2h40; the median is 1h08), so it cannot touch a real
replay; it is the line between a long race and bad data. The **end** is clamped and never the
start — keeping the most recent twelve hours of a race left open since March would show an empty
sea and cut the racing out.

A second bound covers what the first cannot see. The window bounds time, not **rate**: every
club tracker reports every five seconds today — the busiest real unit managed 1,768 fixes in its
busiest hour — but that is a device setting, and one reconfigured to 1 Hz would put five times as
much through the same window. Fixes over **60,000 across the fleet** are thinned evenly, each
boat keeping its first and last so a track still starts and ends where the boat did, and the
reply says plainly that it thinned. Neither bound can move a race result: this path feeds the
replay viewer alone, and finish detection's own walk stays deliberately unbounded.

**Also fixed:** mono stopped leaking into prose — the theme sets `td.big` in IBM Plex Mono for a
results RANK column, and `.big` is also the Marks page's code cell, so the note under a mark code
inherited a monospace and broke *waypoin / t* across two lines. Removing the fleet field broke
the New race page, which nothing caught: the input lived inside a `<div>` the page's script still
looked up on load, so it threw and choosing Pursuit showed no fields at all — there are no tests
over that script and the browser found it. The Boats page loses a Settings button that was a
second door to a room the sidebar already opens.

**Documentation:** the Series Guide, Reference Manual, workflow guide, Operational Checklist and
Competitor Guide all described the two-step series flow and the retired fleet field; all four
PDFs were rebuilt. The replay bounds are written up in
[`TRACKING.md`](TRACKING.md) with the measurements, and in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) as the two things a race officer might actually meet.

## v0.275

**The app stops looking like every other app.** The styling was a management console —
Inter, a 14px radius on everything, soft-shadowed cards, a blue primary button, rounded-full
badges and a gradient wash behind the lot. That is the house style of every dashboard built in
the last two years, `style.css` described itself as "UniFi-inspired", and none of it said
sailing.

**The premise is that this app is two things and they should not look alike.** The
*documents* — entries, results, boats, marks, series, settings — are the descendants of paper:
sailing instructions, entry lists and results sheets pinned to the clubhouse board. They get a
paper ground, hairline rules instead of cards, condensed uppercase labels, and monospaced
tabular figures for every time and rating, so a column of finish times compares down the page.
The *instruments* — the countdown, the flag board, the start sequence — are read at a glance
across a hut in daylight, and get near-black with oversized tabular numerals and colour used
only where it carries meaning. The app already half did this: the countdown box has always
been dark in a light page. Now it is deliberate.

**Three typefaces, bundled, no serif.** Archivo, Archivo Narrow and IBM Plex Mono, all SIL Open
Font Licence 1.1, served from the app rather than a font CDN — the hut reaches the world
through a tunnel and race morning is the wrong time to discover a third-party dependency. 164K
for the lot, and `latin-ext` travels with `latin` so Welsh place names get a real circumflexed
w and y instead of a fallback glyph. The first attempt set body text in a serif and read as
Times New Roman in a Word document; it was not even authentic, since charts, tide tables and
sailing instructions are all set in condensed grotesques. The generic UI blue is replaced by
the club's own signal-flag colours.

**It is a layer, not a rewrite.** No template's structure changed. `theme_race_document.css`
loads after `style.css` and overrides its surface, so the whole look comes off by deleting two
links — and **Classic view** in the topbar switches back in one click, which on a race morning
beats waiting for a deploy.

**A real bug, found by the work rather than caused by it.** Below 1020px the shell collapsed to
`grid-template-columns: 1fr`, and `1fr` means `minmax(auto, 1fr)` — that `auto` floor is the
content's *min-content* width, so a single wide table stretched the grid track to 1043px on a
375px phone and dragged the whole page sideways, header, tabs and all. The race sheet scrolled
585px sideways before this release. That fix, along with narrow-screen table scrolling and
long-URL wrapping, is in `style.css` rather than the theme, so it survives a switch to Classic
view: these are bugs, not styling.

**The race-sheet tabs stay put.** Opening a race writes `#tab-course` into the URL, so the
browser jumped to the pane — which begins below the tab row, leaving the navigation off the top
of the screen before the officer had done anything, and costing a scroll up to change page. The
tab bar is now sticky under the topbar.

**Found by auditing all 29 pages at both widths, rather than by looking at a few.** The
clubhouse television had its race name painted near-white on a white bar, unreadable from the
only place that screen is ever read from. Every *small* button in the app was still rounded,
because `.button.small` is two classes and outranks a single-class rule — big buttons went
square and small ones did not, on twenty-three pages. An IRC TCC lost its last digit to a box
sized in `ch`; so did latitude and longitude on the marks page; and widening the ratings pushed
the status column back to reading "FINISHE". A heading inside a `<summary>` drew a 50px rule
across an 1118px section. The per-row "Copied from boat data when entered" is gone: it said the
same sentence under every rating box on every row, when the Boat column beside it already reads
"Database boat".

## v0.274

**The Results tab gets to the results.** It opened with a full card for the bar replay — a
heading, a three-line description and a button — then a second heading with a paragraph
restating a formula that every result table already prints under its own title a few pixels
lower. The replay is now one strip carrying its title, its button and roughly how long it will
run, the way GPS finish detection does on the Entries tab; the explanation of what a replay
actually does is behind the `?`. All three states fit that one row: available, running, and
nothing to replay yet.

**The two Sailwave exports were being pushed apart by the layout.** They were bare children of a
`space-between` flex row, so one was pinned to the top right and the other wrapped onto a line of
its own beneath it — reading as two unrelated controls rather than a choice between rating
systems. They are one item now and stay side by side down to an 820px window. Why there are two
of them is behind the `?` rather than only in a code comment: a Sailwave series is scored under
one rating system and a competitor carries one Rating, so a single mixed file would silently
rescore a fleet.

Between the two, the first result table now starts about 170px into the tab instead of roughly
480.

## v0.273

**The race page puts the controls first and the explanations behind a `?`.** The Course & start
tab spent its top on a paragraph and put its two course buttons at the far right of the heading
row, which on a 1100px form is where nobody looks. Both are now full-size, on the left, directly
under the heading, and **Recommend / change course** is simply **Recommend course**. Course number
and finish line share a row instead of taking one each, and the form ends around 600px rather than
900 — everything down to **Save** without scrolling.

**The Entries & finish times tab reached the list of boats after three stacked blocks of
preamble.** GPS finish detection is now a single strip carrying its title, both tick boxes,
**Apply** and the current state on one line, and the table starts roughly where that card's heading
used to be.

**A `?` beside a heading opens the explanation.** This is the part that is a pattern rather than
four edits: the handler is delegated in `base.html`, so a new explanation is a button and a dialog
and no JavaScript. What stays on the page is state and consequence — "AP is up since 14:02", "this
race has started", "whole minutes only" — because that changes what the race officer does next.
What moves into the popup is the *why* and the worked example: the 14:20 → 14:21 → 14:26 sequence,
abandonment being flag **N**, why a rating edited on the Entries tab cannot rescore a race sailed in
June. Those paragraphs are read once, by somebody new to the app, and then stand between every
other user and the control they came for. Everything in them is also in
[`RACE_OFFICER_WORKFLOW.md`](RACE_OFFICER_WORKFLOW.md).

**The dashboard says when the battery has stopped getting back to full.** That, not a low battery,
is how a solar hut fails: nothing looks wrong on any one day, the bank simply reaches a little less
each afternoon, and by the time the voltage is visibly low there are days left rather than weeks.
Three days short of full is a watch, a week or a genuinely low bank is urgent, and one dull day is
ignored — a card that cries wolf in July is one nobody reads in November. It matters because the
club's panels lie **flat** on the hut roof: at 52.9°N the December sun peaks 13.7° up, so a flat
panel is nearly edge-on to it, and against the hut's own 24 W standing load a midwinter day, even a
clear one, may not replace what the night took. Across fifty days of the club's real summer data the
bank reached 100% every single day, and the warning stays silent through all of them.

**The power history chart can be read rather than estimated.** Labelled gridlines on round numbers
in every panel, real voltage ticks on the right, and the shared time axis ruled down all three at
round clock times, so a moment can be followed from the battery to the solar to the current. A
cursor follows the pointer with a dot on each trace and a box giving the time and every value at
that instant. The chart is cached offscreen and blitted, because a pointer move on the 30-day range
was re-plotting 74,000 points per panel at 590 ms a redraw; it is now 0.2 ms.

**Three traps in the screenshot tooling, found while re-shooting the guides.** `capture_crops.py`
anchored the Course & start figure to the first `form` in the tab, which is now the help dialog's
close button — a 30px sliver of nothing. The race page lands part-scrolled and the topbar is
sticky, so it painted itself straight over the heading that crop starts at. And
`capture_power_screens.py` was the one capture script that ignored `RO_CAP_PASSWORD` and read the
repo's own password file, so when it was pointed at a sandbox the login quietly failed and it
photographed the login page; the shot itself succeeded, so nothing said so. It now checks it is
past the login page and stops if it is not.

## v0.272

**The live stream carries the wind.** TWD, TWS and gust across the top of the picture, from
the hut's own weather station, so anybody watching from the bar or the club website reads the
same numbers the race officer is looking at without a second screen. It rides on the branding
encode the relay already runs, so it costs no extra transcode; ffmpeg re-reads the text while
it runs, so the numbers change without restarting the stream.

**Most of the work is in refusing to say anything.** A file-backed readout keeps showing
whatever it was last given, so a dropped link would leave a twenty-minute-old wind burned into
live-looking footage with nothing on screen to admit it — and people sail on those numbers. A
sample older than two minutes therefore renders as **nothing at all**, as does one with no
direction, no speed, or a timestamp in the future (the clocks disagree, so its age cannot be
reasoned about either way). Gust is dropped on its own when the station does not report one.

Off unless asked for: `WIND_OVERLAY=1` on the relay. Size and backing are tunable there too
— the text is outlined rather than boxed, because white alone disappears against the pale
overcast sky this camera mostly looks at, while a panel puts a caption bar across the view.

**It says which state it is in, every time the stream starts.** The first deployment produced
no readout and an empty log, and "switched off", "started and died" and "working but nothing
to draw" all looked identical. One line at startup makes silence mean the script never ran,
and prints the first value so it can be compared against the picture. Three traps found doing
that are written down in the relay guide: `journalctl` shows nothing without `sudo`, the
source only logs once a viewer connects, and Cloudflare's bot rules **403 the default
python-urllib User-Agent** — so `curl` succeeds from the relay while the script gets nothing,
and the failure looks exactly like a station with no wind. That last one has now caught three
separate fetches in this project.

## v0.271

**The tracking status said nothing was being tracked while positions were arriving.** The
settings box read **No trackers reporting yet** directly above **Push: working — 12 fixes
received, last just now**. Both lines describe the same feature at the same moment and they
cannot both be true.

The cause was a misplaced `else`. An hourly housekeeping step had been inserted between
`if track_active(cfg):` and its `else:`, which quietly re-bound the `else` to the housekeeping's
`try`. That step returns normally on every cycle — it has an hourly guard that *returns* rather
than raising — so the status just built from a real poll was thrown away and replaced with an
empty one, every time. Shipped since v0.256 and present in every release since.

It reads as a cosmetic fault and is not: that message is how a race officer checks the fleet is
being tracked before a start. Six tests now drive a real iteration of the background loop, which
nothing had done before — the test suite replaces that loop with a no-op so a stray thread cannot
write to a developer's database, and the bug lived in the gap.

**The SIM popout names the state the way Hologram does.** **Connected** when a data session is
open at that moment, **Ready** when the SIM is live but idle — both healthy, and both `LIVE` on
the device record, so the distinction needed asking for separately. It is not decoration: the
club's Queclinks hold a long-lived connection and read *Connected*, while the ATC700s sleep
between reports and flip between the two. Anyone comparing this page against the Hologram
dashboard no longer has to translate. If that lookup fails the raw state is shown rather than a
guess, because calling a healthy tracker *Ready* without having checked would misdescribe it.

**A SIM being switched off says so before it goes quiet.** Hologram shows a **Pausing** step
before a SIM settles to *Paused by user*, and which field carries it is not obvious — the state
asked for, the state reached, and any pending request can each disagree while a change is in
flight. Any of them disagreeing is now treated as mid-change, so whichever one moves, the app
notices. A SIM on its way to paused is reported as paused: it is about to stop carrying data, and
a page that says *OK* about a SIM being switched off is worse than one that is a minute early.
Once the change lands the wording returns to *Paused by user* or *Paused by system*, which says
whether somebody chose it or a usage limit did.

## v0.270

**Racing boats are asked where they are, rather than waited on.** A Queclink reports once a minute and holds each fix until the top of the minute, so its position reaches the app about 45 seconds old — 185 m behind the boat at 6 knots, and that is the gap a finish gets interpolated across. From the warning signal until a minute after each boat stops racing, every tracked GL521MG is now asked directly every 20 seconds, and answers in about a second. The extra minute is not padding: a finish is interpolated between the fixes either side of the line, so the one *after* the crossing is what pins it down.

Twenty seconds is the tracker's floor, not a preference — at 5 s it returns duplicates of a cached fix with latency climbing to 57 s. The result is fixes 62 m apart arriving 4 s old, against 185 m arriving 45 s old, for about +1.1 %/h of battery. **Only the GL521MG is asked**, matched on its IMEI type code and not its protocol: `gl200` is Queclink's whole family and the command carries the GL521M's own password, so another model would reject it. An ATC700 is left alone for the opposite reason — it already reports every 10 s while moving, and at ~10 %/h it is the one unit that cannot spare the asking.

**GPS finish detection is armed on a new race.** Both **Arm GPS auto-finish** and **Auto-confirm** now default on. A finish the detector catches is worth more than one the race officer was going to time anyway, and a missed detection cannot be recovered after the race; either box can still be unticked on the Entries tab. Existing races keep whatever they were saved with — the alternative would have silently armed every historic race in the database.

**A tracker that goes quiet now has somewhere to look.** With a Hologram API key in **Settings → GPS tracking**, the Trackers page shows what each tracker's SIM is doing: a green, amber or red dot per row, and a popout with the carrier it is actually on, four weeks of data, and what that has cost. A SIM **paused by system** has hit a usage limit or a low balance, will never recover on its own, and from every other panel in the app looks exactly like a flat battery. That one distinction is the reason this exists.

Read a healthy answer carefully: it says the *SIM* is fine, not that the tracker is alive. Only the bad answers mean anything, and the wording says so.

**Which carrier each tracker is on stopped being an assumption.** The club's SIM is multi-network, and on a Teltonika half of every setting is live or dead depending on whether the device counts as roaming — a trap that cost real time to find and was invisible while the answer was inferred. The fleet turns out to sit across Vodafone, Telefónica, Hutchison 3G and EE at the same moment.

**Costs, and when the account needs topping up.** A fleet popout adds every tracker's data and cost, the balance, and the date it runs out. That date is not the balance divided by a monthly figure: Hologram bills each SIM on its own 30-day cycle from the day it was activated, so the fees arrive in clusters — the club's seven fall on two dates a fortnight apart — and an average would hide both. The app walks forward a day at a time instead. The dashboard says so too, once a month or less is left, beside the low-battery warning and by the same rule: only trackers assigned to a boat.

The money is worked out here, not read from an invoice: Hologram returns **no cost on any usage endpoint**, only a per-MB rate and a recurring fee. And there is no single time period on that page — each SIM's "this period" is a different window, one day and sixteen at the same moment while this was written, so the table says how far into its own cycle each one is and carries one figure on a window they share.

**Dates read `19 Sep 2026`.** The app showed ISO everywhere, which is unambiguous but nobody's handwriting. The month is a word because `05/09` is read two ways, and the month names are written out rather than left to the machine's locale — a Welsh-locale host would otherwise render the same race in two languages. ISO stays where a machine is the reader: log filenames, which sort by name, and the Sailwave export.

**Trackers are adopted, not typed in.** The **Add a tracker** form is gone: a device Traccar has already seen can be picked off a list, which is the workflow that does not involve transcribing a 15-digit IMEI. A tracker type that was named by mistake can now be removed again.

**[docs/TRACKERS.md](TRACKERS.md) gained the five-unit night.** Five identically configured ATC700s sat three metres apart and their accuracy still varied by nearly a factor of two — CEP50 3.9 m to 7.2 m. Twenty-one parameters read back off each one are identical, GNSS source included, and time-to-first-fix is 2–4 s on all five, so it was neither configuration nor a stale almanac. It was satellite count, and the best unit was simply the one on charge, sitting wherever the charger is. Given the same sky the next morning they were indistinguishable. Two rules follow: check the satellite count before blaming a tracker, and a bench comparison of two trackers in different corners of one room measures the room.

## v0.269

**Every fix now carries what the GPS thought of itself.** Altitude, hdop, pdop, satellite count, signal strength and Traccar's own validity flag are stored beside each position, on all three routes fixes arrive by — the poll, the push forwarder and the outage back-fill. The back-fill was quietly dropping the battery level too, so the stretch most worth examining after an outage held the least data. Nothing interprets the numbers yet: they are stored raw because a check on them will want tuning against real history, and a value discarded at ingest cannot be looked at a second time.

**The reason is two GPS failures during the races of 16 August, neither of which the app could see.** One receiver's solution drifted for 28 minutes — its altitude walking from −22 m to −1060 m with horizontal error growing in lockstep to 627 m — and then snapped back the instant it re-acquired; on the chart the boat sailed 600 m away and came home. The other held a steady vertical bias for a whole race, which measured against the surveyed marks put it 20–50 m to the south of the buoys it rounded, while the healthy boat passed the same marks within seven metres. Both were diagnosed days later and only because Traccar keeps ten days of history. The app had stored latitude, longitude, speed and course, and thrown the rest away.

**Altitude is the field that did the work, because a boat is at sea level.** It is the only value whose truth is known in advance, so a departure from it measures the error directly. `hdop` — the conventional quality field, and the only one a GL521MG sends — sat at 1–3 through both failures and flagged neither. It reports how confident a receiver is in the fix it just made, not whether its solution has drifted, which is why it rises after a healthy cold start and stayed silent through a receiver placing itself a kilometre below the sea.

**The Trackers page says what each tracker is.** A Type column, derived from the first eight digits of the IMEI — the type allocation code, which every unit of a model shares — and confirmed against the protocol Traccar decoded. Both halves are needed: the code alone cannot be trusted, because it belongs to whoever certified the radio and a tracker built on an off-the-shelf cellular module often ships with the module vendor's range (the ATC700's own datasheet names a Quectel part). And the protocol alone is far too coarse: the ATC700 and the RUTX50 are both "teltonika", one a 1000 mAh asset tracker draining 10%/h and the other a mains-powered router. A model the app does not recognise can be named on the page rather than waiting for a release.

**Administrators can talk to a tracker from the app.** Trackers → **Commands** sends a raw protocol command through Traccar and shows what comes back, with prebuilt commands chosen by protocol — the two dialects have nothing in common, and offering Queclink syntax to a Jimi tracker would look authoritative and do nothing. The rest of the page stays open to race officers, because which boat carries which tracker is a race-day job; this reconfigures hardware at sea.

**Factory resets are refused rather than confirmed.** In Queclink's dialect a factory reset and a reboot are one digit apart, and a tracker that forgets its server address stops being findable at all — recovering it means getting the unit off the boat and onto a USB cable. A reboot asks for confirmation and quotes what it costs: 5 min 45 s with no fixes, measured, then several minutes at reduced accuracy. Every command is logged, refusals included, so an attempt to factory-reset a tracker from the race office leaves a trace.

**The console is honest about what it can show you, which is not the same for both protocols.** Teltonika replies over Codec 12 and the reply *is* the data, so it appears in the console. Queclink acknowledges a query and sends its answer as a separate message that Traccar's decoder discards — verified for a configuration read, device information and signal strength, every one of which produced an acknowledgement alone. Enabling Traccar's `database.saveOriginal` does not help: it attaches raw text to a position, and those replies never become one. So the Queclink queries are labelled "answer not visible here", and the explanation shown beside a tracker is the one that applies to its own protocol.

**[docs/TRACKERS.md](TRACKERS.md) collects what the club has measured** about all four models — battery, accuracy, latency, reporting floors, and the traps. The one worth reading first: the club's SIM roams in the UK, so only the roaming half of each Teltonika parameter pair is live, and the device accepts a write to the dead half, reads it back correctly and ignores it. The vendor protocol manuals the document cites are now tracked in `docs/Trackers/` beside it; the 74 MB of configurator installers and firmware that live in the same folder are not, and the release ZIP excludes both.

## v0.268

**Watch the race back in the bar.** Members coming in from sailing wanted to see the race they had just sailed. The clubhouse television already knew how to draw a race at any moment — that is how the live view works — so a replay is a different clock and a list of video, not a second display. On the race sheet's **Results** tab, **Replay this race in the bar** puts it on the screen: anybody signed in can do it, the television switches itself over within a few seconds, and nobody has to go near it. It runs from the warning signal to the last finish at six times life, dropping to normal speed whenever a video plays, and at the end holds the finishing order for a minute and a half before handing the screen back to the live view. A club race takes fifteen to twenty-five minutes, and the button says roughly how long before you start one.

**The clock follows the picture rather than running beside it.** While a clip plays, the replay's time *is* that clip's own footage time. A television that buffers, decodes slowly or throttles a tab therefore cannot let the boats on the chart drift away from the boats in the video: when a two-minute clip ends, the replay has advanced exactly two minutes and the fleet is where the video left it.

**Not every clip is played.** Clips carry a minute either side of their moment, so a fleet finishing close together produces clips that are mostly the same water — one club race was six minutes long with twenty minutes of clip. A clip adding less than twenty-five seconds beyond the one before is skipped, and an overlapping one is joined where the replay has got to rather than restarted. Every clip is still on the race sheet; this only decides what the bar watches.

**A clip does not begin where it used to say it did.** Clips are cut by concatenating whole twenty-second segments of the rolling buffer, so a file's first frame is not the minute before the event — it is the segment boundary at or before it, up to a whole segment earlier. Because the replay locks its clock to the picture, believing the claim drew the fleet ahead of it: sixteen seconds, measured against the hut's own start clip for the race of 16 August, which claims 11:24:00 and opens at 11:23:44. Clips built from this release record where their footage really starts. On races already recorded the boundary is gone with the buffer and cannot be recovered — the file is always pre + post + one segment long however the overhang falls, so not even its length says anything — so those are placed in the middle of the segment instead: a few seconds out rather than up to twenty.

**Everything on the screen has to answer for the moment being replayed, and four things did not.** The order on the water came from the newest board the server had sent, which in a replay is the finishing order, so every boat read *Finished* a minute before its own start — and because the chart deliberately leaves finished boats out of the view it frames, that also left nothing to frame, and the fleet sailed off the bottom of the screen. The wind gauge polls the weather station, so a race sailed on Sunday was shown under Tuesday afternoon's wind, sitting there not changing. The flag panel asked whether the race had finished — a replayed race always has — and showed nothing at all from the warning signal to the last finish, which is the part most worth watching back. All four now read the replay's own clock, and the wind and the flags come from what was recorded at the time.

**And a refused request to play threw the clip away.** Asking a video to play in the same breath as giving it a new source is refused outright — the request superseded by the load still in flight — and the element then sits ready and paused, waiting to be asked again. Taken as *this television will not play video*, that cut clips off seconds in and skipped others entirely; and because abandoning a clip moved the clock to the end of it, abandoning the last one carried the replay past the last finish and ended it there. It now asks again, judges a clip by whether its picture is actually moving rather than by what a promise said, and gives up only after ten seconds of a still picture on a screen somebody is looking at.

**The club's own website can show the start hut.** `deploy/embed/club_website_camera.html` is a page to drop into an iframe on pwllhelisailingclub.co.uk. It plays the live stream from the relay, so the first viewer starts it and Cloudflare fans the same copy out to everybody after them, and the hut's uplink serves one stream rather than one per visitor. A still from the hut fills the gap while the stream starts. Embedded on a site the app does not recognise it takes a single still and stops, rather than a page nobody is watching pulling pictures off the hut all day.

**A development machine can be run against a copy of the hut's data.** `scripts/use_hut_data.py --from <folder>` swaps the databases in and immediately blanks the club's credentials and switches off everything that reaches outside itself — recording, horns, audio, uploads, ingest — so a dev box with real races on it cannot upload to the club's bucket or move the camera on the hut roof. `--restore` puts the development databases back. Without it, testing a replay meant either no real race to test on or a second machine quietly acting as the hut.

## v0.267
**The guides showed a button that no longer exists.** The Start console & log screenshot in both PDFs still had **Postpone - AP** in the manual controls — the control that logged a note, did none of the postponing, and was deleted in v0.266 — printed directly above the new section explaining what replaced it. A picture that contradicts the prose beside it is worse than no picture, because the reader believes the picture.

Re-taken, and a second one added of **AP actually flying**: the pennant in the flag panel, the clock reading *Postponed*, the minute the flag comes down with the warning and gun that follow from it, and the signal plan with the held signals struck through above the two AP rows. That is the state the section is about and no screenshot showed it. `scripts/capture_postpone_screens.py` takes both, and puts the demo database back exactly as it found it.

**And three paragraphs of the v0.266 notes described the release as it was partway through.** It said there was “no box asking when to restart” and quoted a gun at 14:21:41 — written partway through the release, and contradicted later in the same notes by the change that made the race officer name the minute AP comes down. Another still said the countdown was fixed on “all four displays”, written before the clubhouse television and the club’s landing page were found to have their own. A third counted eight clocks in the app and claimed a check against a ninth appearing — and a ninth had already appeared, in a file that check already covered, which is the limit of what it can promise. All three corrected here and on the release page. The released v0.266 package is untouched: it was the description that was wrong, not the software.

The common thread is worth naming: notes written while the work was still moving, and not re-read against the finished thing before the release went out. Reading the section end to end is now part of cutting one.

## v0.266
**Postponing a race is a signal, not an edit of the start time.** In the first race one Sunday the wind died before the start, and the club delayed it the only way the app allowed: by changing the start time. That signals nothing to the fleet — and because the horn scheduler is keyed on that time, editing it during a running sequence drops the rest of the sequence and plans a fresh one. Boats hear a warning, then no preparatory signal and no gun. The app taught the habit: there was no AP anywhere in it, and the Virtual Race Officer's prompt said in as many words that there was no postpone tool and it should offer to move the start instead.

**AP, AP over H and AP over A** — the three signals RRS 27.3 gives a race committee before the start. Flying one sounds two horn blasts, holds the start sequence (no warning, preparatory or starting signal sounds while it is up), shows the flag on the race sheet, the competitor pages and the clubhouse display, and records it with its time. The race keeps its scheduled time: nothing is rescheduled behind anybody's back.

**The race officer does not choose the new start time**, which is the whole point of the flag. They choose when the wind is back, and **the warning signal follows one minute after AP comes down**, by rule, with the gun five minutes after that — so nobody has to predict the weather. After AP over H the warning time is asked for, because “further signals ashore” means the next signals are made later, ashore, and there is no one-minute warning to count from. After AP over A there is nothing to lower: no more racing today is a decision, not a pause.

**Lowering AP is a time, not a click.** Pressed at 14:15:41 it put the warning signal at 14:16:41 and the first gun at 14:21:41, and no fleet can count down to that. The race officer now names the minute AP comes down — whole minutes, as with the first warning signal, and the next whole minute is filled in ready — and the warning follows one minute later with the gun five minutes after that. The flag stays up on every display until that minute, because that is when it comes down on the mast, and the single horn sounds then from the same scheduler that makes every other timed signal. You still do not have to predict the weather; you say when you are ready, and every derived time is a round number.

**The final countdown was spoken at the wrong speech rate.** The club slowed the **Normal speech rate** down so the course announcements could be followed, and that dragged the “Ten. Nine. … One.” with it until “One” landed after the gun. It was the one announcement not using the setting called **Countdown speech rate** — which the documentation had said it used for three releases. It does now.

How far ahead of the gun the count begins follows from that rate as well, rather than being fixed at eleven seconds: the ten numbers are one utterance, so a slower voice must start earlier and a faster one later. That is what makes it keep time at any setting. **If you have slowed the normal rate, set the countdown rate to what the normal rate used to be** — an existing club keeps whatever it had stored, because a default is not a migration; only a fresh install picks up the new 185.

**The Horn and race log shows the whole race.** It showed the most recent twenty events, which a single start sequence fills on its own — so the log of the race someone was looking at began part-way through the race they were looking at. All of them now, on the race sheet and the start console. The dashboard's list is still a handful: that one is every race at once, and is a glance rather than a record.

**Setting the time AP comes down is announced.** It told the race officer, in a banner, and nobody afloat — which is the wrong way round for a boat sitting in a dying breeze wanting to know whether to put the kettle on. It is spoken when the time is set and again whenever it is changed, naming the flag, the warning signal and the first gun. In whole minutes: they are whole minutes by construction, and the seconds were one more thing to listen past.

**The start video would have been recorded at the wrong time.** A start clip is booked the moment a start time is set, and its builder thread then sleeps until that moment and cuts the clip out of the rolling buffer. Postponing does not move the stored time — the flag is what changes — so the club would have got a minute of empty start line filed as the start video, and afterwards two clips to choose between, one of them of nothing. Postponing now cancels it and lowering AP books one for the gun that actually happens; the sleeping builder checks again when it wakes, since it was already asleep before anybody flew the flag. This is the one that mattered: the clip is the evidence a protest is settled from.

**And the clubhouse camera held a countdown to it.** The bar screen cuts to the start-hut camera for two minutes either side of the gun, captioned “Start in 1:35” — which it went on doing under a banner reading POSTPONED. There is no gun while AP is up, so the window does not open. A boat rounding the ODM still shows, because that happens whatever the flag says.

**The signal plan shows the postponement.** It was wrong in both directions at once: it listed the course announcements due before the flag comes down as though they would be made — they are not, because nothing sounds while AP flies — and it left out the two signals that will actually happen. Held signals are now struck through and marked, and the announcement and the flag coming down are rows of their own. A plan that leaves out the next thing you are going to hear is worse than no plan, because it gets read instead of thought about.

**Postponing is no longer offered after the first gun.** The rule was always enforced — AP postpones races that have not started, and stopping one under way is abandonment, flag N, which this app does not signal — but the page went on showing the button and answering with an error, which is a worse way of saying no. It now says why, and points at shortening the course instead. A race postponed *before* its gun can still be lowered whatever the clock says, so the flag cannot get stuck up.

**Every clock in the app knows about AP now.** There are nine, and the first pass reached four: the race office dashboard, the standalone start console, the club’s landing page and the Virtual Race Officer strip all went on counting down to a gun that AP had stopped — and the clubhouse display’s camera caption turned out to be a ninth, found later still, from a photograph. The tests list them and check that no new *file* has grown a clock without being told. That is a net rather than a proof, and the caption is why it is worth saying so: it lived in a file the list already covered.

**And the flag comes down without a reload.** The page is rendered once and AP comes down by itself, so the race sheet showed the flag and a stopped clock past the chosen minute until somebody refreshed. No polling was needed to fix it — both times are already in the page — only asking the question every second instead of once. The **Lower AP** button retires itself at the same moment, which is where “That race is not postponed.” came from; asked anyway, it now says AP has already come down and when the warning signal is.

**The fleet is told before the flag moves.** One minute beforehand: *“Answering pennant will be lowered in one minute”*, and at the moment itself the horn followed by *“Answering pennant down”* with the warning and gun times. A single horn is easy to miss on the water and a flag coming down is easier to miss still. That is also why the chosen minute must be at least ninety seconds away — there has to be room for the first announcement, and a flag lowered forty seconds from now is one nobody was told about.

**And that horn now sounds at all.** It was gated on start automation while the two sounds putting AP *up* were not, so on a club that sounds its own horns AP went up noisily and came down in silence. Putting AP up, shortening a course and taking AP down are all signals the race officer asked for; only the ones the app makes on its own behalf wait for automation.

**Postponing is on the Start console & log tab**, which is the tab that is open before a start, rather than beside shortening — which is where somebody goes half an hour *into* a race. The old **Postpone - AP** button in the manual controls is gone: it sounded two horns and wrote a note while doing none of the postponing, so the sequence carried on underneath it and fired the warning gun anyway.

**And pressing the button no longer throws you back to Course & start.** The race sheet remembers its tab in the URL fragment and a redirect has none. Shortening had the same fault and got the same fix.

**AP is drawn as a pennant**, tapering along its whole length to a blunt fly, five stripes red-white-red-white-red. It was a striped rectangle, which is not a rough likeness of AP but a different flag — and the race sheet is the one place that must not teach the wrong one.

**The countdown stops counting.** Found by looking at the page rather than by a test — the flag was flying and the clock underneath it was still ticking down to the old start. Every display now reads *Postponed* instead, from one shared wording.

**The Virtual Race Officer can do it**, with the usual read-back, which is the strongest case there is for that page: a race officer on a boat watching the breeze die is exactly who needs this, and the alternative was the wrong procedure. It never asks what time to restart at. The club's earlier decision that it flies no flags is reversed for AP alone; a recall it still cannot signal, and a race already under way it refuses — that is abandonment, flag N, which this app does not signal and says so rather than flying the wrong flag.

**`start_sequence_key` returned `None`, and had since it was extracted.** The refactor that moved the scheduler out of `app.py` kept the function's first line and dropped the rest, so a function annotated `-> str` fell off the end for every race and every version of every race. Its job is to tell "the warning signal I already sounded" from "the warning signal for the time that has just moved" — which is the case its own docstring describes, and the one postponement depends on. It failed quietly for two hundred releases because the in-memory keys are cleared on every edit and the event-log guard falls back to matching the scheduled time within five seconds; what was gone was the margin. Restored, with nine tests that fail against the shipped code.

**The audit log stopped at midnight when anything else held the file.** `TimedRotatingFileHandler` rotates by *renaming* the live file, and on Windows that fails outright while another process has it open — a restart where the old process has not quite exited will do it. It does not heal: the handler advances its next-rollover time only after a **successful** rename, so every later line retries the same doomed rename and is discarded, silently. Each day now goes to a file named for that day and nothing is ever renamed. Both old namings are still read back, so the hut's history survives the upgrade. A gap in a log written before this is not evidence that nothing happened.

## v0.265
**A race recorded no video, and nothing had been watching the recorder.** FFmpeg was started at app start, when video settings were saved, and when a clip was scheduled — so a recorder that stopped in the small hours stayed stopped until somebody happened to save a setting. The hut's own log shows one that ended on a Thursday and did not run again until the Saturday evening; the dashboard looked healthy throughout, because the *live preview* has had a watchdog for a while and was dutifully restarted overnight. The thing being supervised was the thing that did not matter. A watchdog now checks the recorder every 30 seconds and restarts it — through the ordinary start, so there is no second way to start a recorder and a broken camera still fails and logs the ordinary way.

**The race itself was lost to the other half of that.** The recorder did not die: it stayed alive with its camera gone, holding the segment it was writing open and adding nothing. FFmpeg waits for a silent camera indefinitely — on the hut's own build, a black-holed address was still being waited on after 25 seconds — so it is now told to give up after ten, and the watchdog restarts a recorder that is alive and writing nothing as readily as one that has exited. The club's camera reboots to a schedule at three in the morning, which is a two-minute outage that used to cost the whole day's video.

**One stale file in the buffer folder is now a diagnosis, not a mystery.** The trim was working perfectly the whole time — it deleted every closed segment as it aged out, and the one file that survived did so because FFmpeg still had it open and Windows will not delete a file another process holds. So an empty folder means the recorder exited; exactly one file means it stalled, and that file's timestamp is the minute the camera went away. TROUBLESHOOTING says so, having briefly said the opposite.

**The dashboard says when the recorder is down.** It read “Video recorder is not running” in the same grey as everything else, all one Saturday morning. It now carries a *check* flag and the number of times the recorder has been restarted since the app started — a count climbing steadily means the camera, not the app. Video switched off on purpose stays quiet, because that is a decision rather than a fault.

**Saving a new user failed with the hut-offline screen.** A v0.264 regression, and mine: a column was added to the users table and the INSERT was left one value short, so the save raised, and only the duplicate-username case was being caught. The relay then dressed a 500 up as “the hut is not available”, which sent whoever was looking to the wrong end of the problem entirely — the hut was up and answering. A 500 is now the app's own error, shown as such.

**And the Virtual Race Officer stopped answering with 504 on the live hut.** Not the app: the relay gave the hut five seconds to reply, and thinking about a race takes longer than that. Interpreting now gets 45.

## v0.264
**No interpreter, no feature.** The app has a small built-in grammar — about six sentence shapes — and it answered whenever no model was configured or one could not be reached. On a page whose whole premise is *type what you want to do*, that reads as an app that understands nothing, and somebody on the water cannot tell a sentence it will not understand from one it has **mis**understood. So the club's decision: this feature does not exist until an interpreter is configured and answering. With none, the page says so and offers no box to type in, and both endpoints refuse. The cost is real and deliberate — the hut's outbound internet can fail while the page itself is perfectly reachable, and the racing then goes back to the race sheet.

**Shortening at a mark the course passes more than once.** Course 3 rounds mark 4 three times, so “shorten at 4” names three different finishes — and the app took the first, which is the one answer that is certainly wrong: the fleet sailed past it half an hour ago. It now uses the leader's progress to pick the rounding they are coming to, says which one in the read-back (“rounding 2”), and asks when nothing is reporting a position. Underneath it was worse: a mark *name* was being matched against the *index* as well, so “shorten at mark 1” also matched the mark at index 1 — mark 8 on course 1 — and only came out right because mark 1 happened to be first in the list.

**The board follows a shortening.** It was still showing all thirteen marks of course 3 beside the words “shortened at 4”. The strip, the chart and the time round are now of the course being sailed.

**And the reply limit again.** 2048 was not enough for “make me a course with lots of reaching, about 20 nm” — designing a course from twenty-four marks is a long think, and the reply was truncated to nothing. Raised to 4096, the failure now says *a shorter sentence will work* rather than *raise max_tokens*, which is an instruction for whoever maintains the app and was appearing on a phone in the middle of a race — and the distances and bearings between the racing marks are now in the look-up, so the trigonometry is done once by the app that already knows how, rather than from latitudes by whoever is reading them.

**It can look up what a boat is rated**, by name or by sail number. Three places hold a rating and they are not the same thing: the boat database is what a race actually scores on, because entering a boat snapshots its rating at that moment, and the RORC IRC listing and the YTC sheet are where that figure came from — either may have moved since. All three are reported, because somebody asking is usually asking because something does not look right. A boat the club does not have is looked up in the same two listings the race office reads when adding one, and the app offers to add it. **An existing record is never overwritten**: the import screens do update one, correctly, with the listing row and the record side by side in front of you; from a boat they are not, so there the record wins and the reply says what it already holds.

**The page is now the Virtual Race Officer**, at `/vro`, which is what the club calls it. The per-user permission is **VRO**, the Settings section and the side-menu link say *Virtual Race Officer*, and `/onwater` still answers — it is on phones already, and a bookmark that 404s at sea helps nobody.

**It can make up a course.** “Make me a windward-leeward twice round, O to 4” reads back *Op 4p Op 4p Op* before anything is set, and “change the second mark to 2, starboard” reads the whole course back again. The board and the chart follow each change. Building a course meant dragging marks around a page 1100px wide; the save is now `core.raceadmin.set_custom_course`, which the course builder calls too — one implementation, as with everything else here.

**It can ask the app rather than be told everything.** The club has 24 marks, 67 courses, a boat database and a polar: sending all of it with every sentence typed on 4G is not the answer. A read-only **look-up** fetches what was asked for — where a mark is, a course's legs and their distances, how many courses there are, the boats, the series with how many races each has, recent races, where the fleet has got to, the polar and its sail chart for the wind blowing now. The answer goes into the thread, so the next question can be answered from it.

**Three things it could not do, from one reported conversation.** “Same boats” created the race and quietly entered nobody; “add Sgrech Bach and Mojito” entered the first and dropped the second without a word; and asked to put a race into ISORA it answered that a series can only be set when a race is created. All three now work, and `EntryScope` gained a `same_as` kind so “the same boats as last week” is one call through the ordinary service.

**And one thing it had backwards.** Asked whether setting a start time arms the horn, it said only the race office can make a horn sound. It is the opposite: with start automation on, the scheduler fires the warning, preparatory and start signals off the race's stored warning signal, so **moving a start time from the water moves the horn**. There is no separate arming step to withhold. The facts now say which it is, and the guides are corrected.


**Running racing from the water.** A new page, `/onwater`, for a race officer on a boat with a phone in one hand and a tiller in the other: type a sentence, read what it would do, press **Yes**. It creates races, sets starts and courses, enters boats, shortens courses, reports status and past results — all through the same `core.raceadmin` functions the race sheet calls, so there is no second way to run a race. Full detail in [`VIRTUAL_RACE_OFFICER.md`](VIRTUAL_RACE_OFFICER.md).

**Who may use it is a permission of its own.** *On the water*, per account in **Settings &rarr; Users**, granted by no role including administrator. The club has settled that a race may start with nobody watching the line — a boat later judged over by video takes an alternative penalty — but who may drive the racing from a phone is a separate decision, made one account at a time. Arming the start sequence is not offered on that page at all, because that decision has not been made.

**Nothing happens without a read-back.** The command endpoint interprets and changes nothing; a second endpoint is the only thing that acts. It is the only check that catches the class of mistake where the app understood perfectly and it was not what was meant. A proposal lapses after five minutes, only one is live at a time, and sending the same command twice does it once — a phone that retries after a dropped reply is the same command, not a second race.

**It holds a conversation.** The last half hour with the same person is threaded, so *"add the fleet to that one"* has an antecedent. A question the app asks can be answered in a word: told *standard*, it re-issues the whole command with the time, name and length it already had — before, it asked a question it could not hear the answer to. While a proposal waits it is what the conversation is about, so asking for a longer course changes **that** proposal. Agreement in words works as well as the button, and *"yes, but make it half past"* is read as the instruction it is. **A refresh no longer loses any of it**: on a boat that is a dropped signal or a locked phone, not a decision to start again, so the page reloads the thread and a waiting proposal comes back with its Yes button live.

**It answers from the app's own working.** Asked how long a race will be, or whether a course could have more reaching, it used to say the app did not tell it — from an app that computes exactly that for all 67 courses in 22 milliseconds. The interpreter is now given the wind and how it has shifted over the hour, the course's legs mark by mark with the point of sail, how long it should take round and when the first boats would finish, the courses it would recommend, who is entered by name and sail number, the club's active boats, its series and its recent races, and whether the trackers are reporting. That list is the whole of what an answer may be built from; with no wind reading it says none of it, because inventing a breeze to answer with would be worse than silence.

**The club's words are in the interpreter too.** *AP* now gets "the app can't fly the AP flag or make a postponement signal — it can only move the start time; how many minutes?" rather than "I'm not sure what that means". Nothing was added to the tool list: it says more, it does not do more. And a question about a job is no longer read as an instruction to do it — *"what series will a new race be in?"* proposed creating a race, twice.

**The gun, the course and the board at the top of the page.** Two lines and a rolled-up chart in 121px of an 812px phone: the race, the countdown to the first gun (red inside five minutes), the course with its length and how long it should take, the wind, and the **course board** — the same marks and hands the race sheet and the clubhouse display show, and the thing that gets read out over the VHF. The chart draws itself from the marks already in the page — no tiles, no Leaflet, nothing fetched over the same 4G the hut is using — and all of it follows the race: a start put back twenty minutes moves the countdown that was the reason for moving it, and a course change redraws the board and the chart. Neither used to: the strip said "course 17" above a picture of course 16, and a page loaded before a course was chosen had no chart element at all, so setting one drew nothing until a reload.

**A command changes what it names and nothing else.** `RaceSettings` is the whole Course &amp; start form and `update_race_settings` writes every field of it — which is right for a form, where every field was on screen. Built from only what a sentence named, it cleared the rest: **"use course 29" wiped the race's start time, its series (so it scored in no standings), its class and its finish line**, while the read-back said "course 29". There was nothing in that sentence anybody could have checked. Every save from this page is now built from the race as it stands with only the named fields changed — including the create-with-a-time path, which was writing the series and then immediately clearing it.

**A race created from the water records its own finishes.** **Arm GPS auto-finish** and **Auto-confirm (unmanned)** are both on from the start, and the read-back and the result say so, because it is a real difference in how the race will be run that the sentence did not ask for. Nobody is in the hut to press **Finish**: that is the premise of the page. Both remain ordinary tick boxes on the Entries tab.

**A course nobody chose is not a course.** A new race stores the first fixed course as a fallback so the chart and the leg analysis have geometry to work with. That was indistinguishable from a decision and reached three places — the race sheet header, the competitor page and **the spoken VHF announcement**. A fleet sent round a course the race officer never picked is a general recall at best. `races.course_set` now records the difference: both pages say *Course not set*, the announcement stays silent, and existing races back-fill as chosen.

**A race in no series is scored in no standings.** Creating a race from the water had no way to name a series at all, and nothing said so. The series are now offered, matched against the club's own list — an unknown name is a question listing the real ones, never a race quietly created outside the points — and the read-back says which series, or *in no series*, and *no start time yet* where there would be none.

**A finished race is named as finished.** It stays the app's current race after the last boat is in, so *"let's try 29"*, said about the next race, read back as an ordinary course change to the one that had just ended. Not refused — correcting a finished race is a real thing to want — but the read-back says so and offers a new race instead.

**Asked for the results of a past race, the page said "the hut did not answer (500)".** A result row carries the entry and the boat, not a name, so reporting real results raised where a race with nothing placed had been fine — which is exactly what the first test of it exercised. It reads the name from the row now, and the test finishes two boats first.

**A deterministic question no longer beats a better one.** Told "shorten course", the grammar recognised the shape and could only ask which mark; the model had already asked *and named the mark the fleet was sailing to*, and that sentence was being thrown away. A deterministic **action** still wins — "shorten at mark 4" must shorten the course however chatty the alternative reply would be.

**Two failures that only looked like a stupid model.** The provider request allowed 512 tokens; every reply from this endpoint opens with a thinking block spent from the same budget, and one plain question came back having used 421. A reply that runs out mid-thought carries no tool call and no words, and reached the page as *"I did not understand that"*. Raised to 2048, and a reply with nothing usable in it now records why. Separately, a model configured but failing — an empty account, a wrong model id, a timeout — was indistinguishable from no model at all: the grammar answered, everything appeared to work, and only the short commands were understood. The page and Settings now say which interpreter is answering and, when it failed, the provider's own words.

## v0.263

A documentation release. No behaviour changes beyond two lines of stale help text in the app itself &mdash; but the guides had drifted in ways that would have cost somebody a Saturday, and the figures were leaking data they should never have carried.

**The published figures named the club's trackers.** The Trackers-page screenshot embedded in the Reference Manual mapped **six real 15-digit IMEIs to named club boats** &mdash; `862129082306832` to Andromed A, `864864070498856` to CRACKAJACK, and four more. That PDF is published. The figure is re-taken with the trackers hidden *and* the Traccar connection cleared, because hiding the rows alone is not enough: it moves the same devices into *Trackers seen on the network*, which is read live from Traccar, so the first re-capture listed **more** IMEIs than the original. The capture now refuses to save if a 15-digit id is visible on the page, and `scripts/README.md` says why.

**Every screenshot was older than the app.** All 56 were captured before v0.249, so each showed a sidebar with no **Current race** link, and some showed controls since renamed &mdash; the series toolbar figure still showed a `Download CSV` button replaced by `Sailwave CSV (IRC)/(YTC)` in v0.243. Re-captured against this version.

**The hand-cropped figures now cut themselves.** `crop_*` and `marks_top.png` were bands cut by hand in Pillow at offsets nobody had written down, so when a page grew they framed the wrong thing and could not be re-cut &mdash; matching the old crops against the current pages failed for six of seven. `scripts/capture_crops.py` takes each from the element it is a picture of. It turned up that `crop_classes` is the *discards* section and `crop_classes2` the rating bands, the reverse of how they read, and that `crop_course_bottom` had been framing the GPS position list rather than the leg analysis its caption promised.

**One rule, described eight different ways.** Mark rounding has been decided by three tests since v0.258, the widest being a gate reaching 750 m on the hand the course requires. The pre-v0.258 rule &mdash; *"rounding is judged within a radius"* &mdash; was still stated as fact in the Reference Manual's marks chapter, `TRACKING.md`, `TROUBLESHOOTING.md` twice, the repository README, the race sheet's own help text and the GPS settings help. The troubleshooting one was the headline diagnosis of *"boats are not being seen to round a mark"*, which sends a volunteer out in a RIB to re-measure a mark that is exactly where it should be.

**The Race Officer's Guide was a release behind in four places.** Rounding described as the closest-approach test alone, so the person who fields *"why did that count?"* was never told the app knows a mark has a required side. The leaderboard's estimate offered as "the polar or the speed it has been closing" &mdash; the pre-v0.258 pair, missing the method the board opens on, while the Competitor's Guide listed all three correctly. Every race-sheet tab number one out, because **Shorten course** was inserted at 4 and the chapters after it were never renumbered. And the CSV exports still called spreadsheets, three releases after they became Sailwave import files.

**Two settings existed in no document at all.** `RO_TRACK_GATE_REACH_M`, shipped with the gate itself, and `RO_DB_TIMEOUT_S`. Six `core/` modules &mdash; `rounding`, `sailwave`, `offsite`, `r2`, `slowlog`, `docsview` &mdash; were missing from the developer map, and `build_relay_guide.py` was missing from the release pipeline, which is how a guide stops being rebuilt.

**Smaller corrections.** The weather chapter said every wind sample is pruned after 24 hours; a race's own wind is kept indefinitely, which is what the replay gauge reads back. The install chapter unpacked into `pwllheli_ro_mvp_v0_131` and the Windows guides into `v0_193`. `scripts/README.md` said the VMC window averages five minutes; it is twenty. The Competitor's Guide said each boat trails a quarter of an hour, which is the clubhouse display's figure &mdash; the competitor chart draws ten. Two guides named a **Settings &rarr; Video & camera** section that does not exist. `TRACKING.md` gave instructions for a per-race loaner tracker dropdown four paragraphs after saying it was removed in v0.207, and `RACE_OFFICER_WORKFLOW.md` said GPS tracking is not shown to competitors seven lines above a bullet describing what competitors see.

**What stops it happening again.** `tests/test_pdf_prose.py` reads the four built PDFs back and checks their prose against the code, so a guide not rebuilt after a change fails the suite. `tests/test_doc_prose.py` does the same for the markdown. Between them they now hold 26 facts, and the ones that earn their place are the coverage checks &mdash; every `core/` module in the developer map, every environment variable documented somewhere, every guide builder in the release checklist &mdash; because a new module or setting shipping with no documentation is invisible until somebody goes looking. Of the checks written, 17 fail against the tree they were written for and pass against this one; the rest are regression guards on facts that were already right. One was written badly first: a check that the competitor guide quotes the ten-minute trail passed against the wrong sentence, because the guide says "ten minutes" elsewhere about something else.

## v0.262

**The chart's leaderboard was empty on v0.261, and this fixes it.** Found while building the wind gauge below, not by looking for it.

The clock fix released in v0.261 declared `raceElapsedText` at module scope, where it reads `data` &mdash; a closure variable of `init()`. It therefore threw a ReferenceError on every frame, and it is called two lines into `drawFrame`: the clock above it updated, and the slider, the corrected leaderboard and everything drawn after it did not. The board rendered **zero rows** and the elapsed readout sat at `0:00` while the chart itself animated normally, which is why nothing looked broken. Anyone who opened the Chart tab on v0.261 had an empty board under a working map.

There is now a test that no helper declared at module scope in that file refers to `data`, which is the general form of the mistake rather than the instance of it.

**A wind gauge on the chart, following the playback.** The same dial the race office and the landing page carry, floated transparently over the chart's top corner and driven by the replay clock rather than the live poll &mdash; wind the timeline back to the start and the gauge shows the wind at the start. Direction and speed are the dial's two readouts. **Gust is a marker on the speed scale**, not a caption: it is a reading of the same quantity as the speed needle so it belongs on the same scale, and a line of text under the readouts broke the symmetry the dial is built around.

The wind for a whole race ships with the track as `[t, twd, tws, gust]` rows thinned to one every 30 seconds, so there is no request per frame &mdash; the same arrangement as the order on the water beside it. It works for a race sailed months ago because `race_wind_retention_windows` already exempts an hour either side of every race from the 24-hour purge; nothing new had to be stored.

Two honest limits are on the face of it. The caption says **Wind at the hut**, because that is what the club measures and it is not the wind where the boats are once a course rounds a headland &mdash; the same fact that makes the polar estimator unreliable offshore. And a reading more than ten minutes older than the moment on screen is not drawn at all: the gauge hides rather than presenting a stale reading as current, which is how the board already treats a boat whose tracker has gone quiet.

## v0.261

**The chart's elapsed clock counts from the gun.** Reported straight after v0.260 as "so now it's 15 mins not 10". The estimates were in fact arriving at exactly ten minutes &mdash; measured against the live payload, all three boats first ranked at 19:15:00 UTC against a first gun of 19:05:00 &mdash; but the clock beside the chart was reading 15:00 at that moment.

The replay window opens at the **warning signal**, five minutes before the first start, because the approach to the line is worth watching. The elapsed readout counted from there rather than from the gun, so it ran five minutes ahead of the race. That is wrong on its own terms &mdash; elapsed time in a race is time since your start &mdash; and it landed on the one number a competitor checks it against, since the board's own caption says estimates begin after ten minutes of racing. It looked broken while being correct.

The track payload now carries `first_start`, and the clock counts from it, counting **down** before the gun. Nothing about when the estimates appear has changed.

## v0.260

**The corrected leaderboard answers from ten minutes.** Reported after the club updated to v0.259: on race 61 the board showed dashes for a long time, when competitors want it as soon as the ten-minute hold is up. It was blank until **77 minutes** for the leader, 88 and 101 for the other two.

Mine, and introduced in v0.258. The guard that keeps a nonsense estimate off the page was written as a ceiling of six times the elapsed time. That is the wrong unit. Early in a long race the projected total legitimately *is* many multiples of elapsed &mdash; ten minutes into a seven-hour race the ratio is 42 &mdash; so the ceiling suppressed every estimate until `elapsed x 6` had grown past the real answer, which is most of the way through the race. Measured on that race: at 10 minutes the boat had covered 0.82 of 29.5 nm and the estimate was **6.0 hours against an actual 6.9**. A useful board, thrown away for 67 minutes.

It is now judged on the **speed the estimate implies for the rest of the course** (`MIN_PLAUSIBLE_SPEED_KN`). That is the quantity that actually separates a sound early guess from nonsense: the 32218-minute estimate the guard was written for implies about 0.01 kn and is still refused, while 6.0 hours with 28.7 nm to go implies 4.9 kn and is shown. The ratio to elapsed only measures how far through the race you are.

With it fixed, all three boats are ranked from **10 minutes** and the order is right in 99% of the snapshots it can rank.

## v0.259

**Settings stopped stranding inputs at the bottom of their boxes.** Reported against v0.258's **GPS tracking** section, which looked simply messy: four labels in a row along the top, the gate-reach box tucked under its own label, and the retention and rounding-radius boxes adrift near the bottom of the panel.

A `label` is a grid, and as a grid *item* of `.two-col` / `.three-col` it is stretched to the tallest cell in its row. Its own rows are auto-sized, so the leftover height was being shared out between them and the input slid down the cell. One field carrying a paragraph of help text beside two without it is all it takes &mdash; and that is a normal thing to write, which is why this was waiting to happen rather than unlucky.

`align-content: start` on the label packs its rows to the top and keeps every label with its own input whatever its neighbours are doing. Measured as the offset from a label's top to its own control, over the label's height: v0.258 put `track_retention_days` and `track_rounding_radius_m` at **0.47**, the worst on the page, against 0.39 for a row that is even. The single line brings them back to 0.39, and it corrects **every** settings section rather than the one somebody happened to notice &mdash; 114 labels checked across all of them.

**The GPS tracking fields are also regrouped.** The three numbers now sit in one tidy row, and the explanation of how the rounding radius and the gate reach work together is set as a paragraph *below* the pair it describes rather than stuffed into one grid cell. The push ingest token gets its own row with room for its note. That is readability rather than alignment: measured, the CSS line fixes the layout on its own.

**A note on measuring it.** The first attempt to prove which of the two changes mattered swapped the template file between runs and found no difference. Templates are cached with debug off, so both runs measured the *new* markup while only the stylesheet was actually changing &mdash; the comparison was of one thing against itself. Restarting the app between runs gave the numbers above.

## v0.258

**Mark rounding, and the leaderboards that depend on it.** All of this came out of one reported symptom and the races behind it.

**A mark is rounded by which side you passed it, not by how close you got.** Nothing in the walk knew a mark had a required side &mdash; a boat rounding the wrong way counted exactly like one rounding correctly &mdash; and both existing tests asked how *near* the boat came. In the night race of 8 August, CRACKAJACK sailed round AA 420 m off, plainly round it and on the correct hand, and the 400 m neighbourhood missed it **by twenty metres**. The walk is sequential, so that one miss stalled the boat at AA for six hours: distance-to-go rose instead of falling, the projected finish reached 165 hours, and because the finish line is only watched once every earlier mark is rounded, **its automatic finish could never arrive**. Worth noting against v0.256: the extended finish line was a real fix, but it was not what stopped that boat finishing.

**The gate.** A line through the mark at right angles to the leg arriving at it, reaching **750 m** on the hand a boat must pass and only the **rounding radius** on the hand it must not. Cross it and the mark is rounded. The asymmetry does two jobs with one number: the long side is the side test, and the short side is the width in which *no* side can be determined &mdash; a boat closer to the mark than we know where the mark is has no determinable side, and a drifting buoy is exactly a reason not to discriminate. 750 m is from the race, not from taste: drawing level with AA the three boats were 369 m, 468 m and 512 m off, so 500 m would have missed the widest by twelve metres. Settings &rarr; GPS tracking, and per mark.

**A suspected wrong side is reported, never refused.** Refusing stalls the boat for the rest of the race and takes its automatic finish with it, to enforce a rule this app does not adjudicate. So the gate can only ever *add* a rounding; it cannot take one away, and therefore cannot do worse than what it replaced.

**What was tried first and thrown away.** A signed swept angle about the mark &mdash; the string test of RRS 28 made computable. It is elegant and it reads the sampling rate rather than the boat: at 61 s between fixes a boat travels 250 m, a tight rounding happens entirely inside one step, and the straight line between the fixes either side of a *correct* 20 m rounding ran 81 m the wrong side of the mark, for 87 degrees of confident, wrong verdict. Three attempts to compensate with tuning constants were the signal that the shape was wrong. Crossing a gate is a plain fact about that same straight line, and has no such failure.

**Whole simulated races, in the suite.** Boats sailing a Beneteau 40.7 polar in 20 kn round the club's fixed courses, reporting every 61 s, steered round an arc on the required hand of every mark so the ground truth is known by construction. Two earlier versions of the fixture quietly sailed the *wrong* side and "proved" the detector broken; it now checks its own geometry &mdash; polar speeds, every rounding's actual side, the sampling coarseness &mdash; before any assertion runs. Complete races by how wide the boats rounded, before and after: 20&ndash;150 m 67/67 both; 250 m 63&rarr;67; 350 m 65&rarr;67; 450 m 0&rarr;67; 600 m 0&rarr;67.

**And each detector is proved load-bearing by switching it off**, which turned up the counter-intuitive one. Removing the gate loses the *wide* roundings, as expected. Removing closest-approach loses the *tight* ones: at 61 s a 20 m rounding can put no fix at all beyond the mark, so the line between fixes never crosses the gate and no reach would help, while no fix lands inside the radius either. With 20 m roundings, all three complete 67 of 67 courses and radius-plus-gate completes 2.

**The corrected board is ranked by average pace since the start.** Three methods now &mdash; average pace since the start (default), recent pace over twenty minutes, and the polar pace factor &mdash; and whichever is selected drives the **order and the times printed beside it**, because a board whose ranking came from one estimator and whose times came from another contradicts itself as you read down it. The clubhouse display had a hidden version of exactly that: it read `r[7] || r[8]`, a *per-boat* fallback, so a boat with no polar estimate that snapshot was ranked by VMC against boats ranked by the polar.

**Why the simplest method, having been dismissed in the code for years as "the naive alternative".** Its bias is real and it does not matter. Measured over the night race it had a *larger* median error than a windowed VMC (58 minutes against 28) and called the finishing order right in 99% of snapshots against 63&ndash;83%. Its errors are common-mode: at 30 minutes the three boats were out by +153, +162 and +166 minutes, all having just sailed the same slow first leg. A board ranks *differences*, so a shared bias cancels and a per-boat one does not. Accuracy is not what a leaderboard needs.

**Why not the polar factor, which is the better instrument in principle.** It needs a wind, and the club measures wind on the hut, which is not where the boats are. The factor is a ratio, so a wind *speed* error largely cancels between numerator and denominator &mdash; 14% spread over 8 to 26 knots. A *direction* error does not, because it changes which legs are beats: 44% spread over all directions. Direction is precisely what a headland bends. It stays on offer, and remains right for a short course in sight of the hut.

**The recent window is twenty minutes, not five.** Five is short enough to divide by nearly nothing: it once produced a finish estimate of **32218 minutes &mdash; twenty-two days**. Worst case by window: 5 min 32218, 10 min 994, 20 min 425. Nothing wilder than six times the elapsed time is now shown at all.

**Boats are compared as at the same instant.** Reported from race 65: the chart held CRACKAJACK, ANDROMEDA, MOJITO on the first leg while the line-honours board swapped the three round repeatedly. The trackers ran at 2 s, 10 s and 61 s, and with dropouts the worst gap between two boats' last-known positions averaged **136 seconds** and reached 306 &mdash; at 6 kn, 420 m of pure staleness, easily enough to invert an order. The chart looked right because it interpolates; the board did not because it did not. Every boat is now carried forward from its last fix on its last known course and speed to the moment being shown, live and in the replay, so the corrected boards get it too. Over that leg: the order exactly right 71% of moments against 51%, and 3 visible order changes against 16. Rewinding every boat to the last moment they had all reported was tried and is worse &mdash; exact for a moment 136 s ago, and right 29% of the time. Capped at three minutes, past which a boat is missing rather than quiet and is left where it last actually was.

The long-term fix for that last one is trackers reporting often and equally. This makes the board readable until there are some.

## v0.257

**The competitor pages on a phone, reported from an iPhone after a night race.** Display only; no application code changed.

**Held sideways, the results lost their right-hand columns.** The wide public tables become one card per boat on a phone, but that layout stops at 700px &mdash; and a phone in landscape is 844px, a tablet 768 or 1024. All of them therefore got the ordinary nine-column results table, and where it did not fit it was **cut off at the right edge**, taking the *Finish video* link with it and leaving nothing on the page to say a column was missing. Walking the ancestor chain found the clipping two levels above the table: `.result-card` is a grid item, a grid item will not shrink below its content, so a wide table pushed the card past its track and `.race-tabs` &mdash; `overflow: hidden`, for its rounded corners &mdash; quietly swallowed 114px of it. Letting the card shrink hands the overflow to the table, which now scrolls inside its own box. The page itself still never pans sideways.

**Three nested frames were spending a third of the screen.** The tab panel, a card around each class's results, then a card per boat &mdash; each with its own border and padding, together **120px of a 390px phone** before a single result was drawn. Once every row is a card the middle frame is only decoration, so it keeps its heading and gives up its box; and the blanket `.card` padding for phones was catching the tab panel, which already pads its own pane &mdash; the base stylesheet sets `.race-tabs` to 0 for exactly that reason and the phone rule was undoing it. Chrome is now 50px and the table 340px instead of 270px, which also lets a Pro Max fit two columns per card where it managed one.

**A sideways phone stopped paying the desktop page gutter**, worth another 40px at 844px. Only the padding comes down here, not the frames: the public results grid is one column at *every* width, so each card is what separates one class's results from the next. Done with fluid `clamp()` rather than a second breakpoint &mdash; a hard edge at 1024px was worth more, but it made the table **43px narrower one pixel wider**, which reads as a bug when you resize a window. There is now no step anywhere: 918px at 1024, 919px at 1025, and the desktop gutter is left where it was.

**A note on the measuring.** The first fix was verified against tables that all fitted &mdash; every width reported "nothing clipped" while no table was under any pressure, which proves nothing. The contradiction that exposed it was a table box measuring 724px inside a 710px viewport while the page did not pan; that only happens if something above is already absorbing the overflow. The numbers quoted here come from the ancestor chain, not from the absence of a symptom.

## v0.256

**Three faults from one night race, and the plumbing underneath them.**

**The chart stopped updating when the first boat finished.** Reported after a night race: the chart froze not long after the first finisher, the second boat still finished but never appeared on it, and by the next morning every track was there in full. All three are the same fault. ``race_track_window`` took the last finish as *the latest among boats that had finished* &mdash; which with one boat home is that boat &mdash; and closed the window a couple of minutes later. Everything the chart fetches is bounded by that window. Finish detection is not, which is why the second boat still finished; and by morning the whole fleet was in, so the window covered the race. It now closes only when nobody is still RACING. There was a second effect on the night: once a viewer's `since` passed the frozen end, every poll re-sent the **entire race**, to every phone watching, from a hut PC that was already refusing connections.

**A finish was missed because the line's seaward mark had moved.** The Fairway Buoy had drifted offshore, so a boat sailed inside the physical mark but outside the position the app held for it &mdash; off the end of the line segment. The finishing-direction test was content (it measures against the *infinite* line); only the segment bound refused it. The seaward end of each line is now projected outward by a per-line ``seaward_extension_m`` in `data/start_finish.json` &mdash; 150 m for the club line, 250 m for the ISORA one. Only the seaward end: the shore end of both lines is a surveyed transit that cannot move, and extending it would project the line inland. The extension is collinear, so the infinite line and therefore the direction test are unchanged, and a finish still requires every mark rounded first.

**And the mark was wrong before it ever drifted.** Comparing the stored position for **F** against the position ISORA's own sailing instruction gives &mdash; recorded in `start_finish.json` all along as ``si_seaward_position`` &mdash; they were **112 m apart**, 7% of a 1510 m line. F is corrected to the SI position. The old position is kept in the mark's history, so races already scored still replay against the line they were sailed to.

**The push path no longer holds the write lock.** The relay pushes every fix to `/api/track/ingest`, and that request purged old rows on every call. The purge filters on ``fix_time``, which no index covered, so each pushed fix scanned the whole table inside a write transaction &mdash; and this database is in rollback journal mode, where a writer excludes every reader, so each push locked out the clubhouse display, the competitor pages and the race sheet. Retention is now hourly housekeeping with an index behind it: 5.78 ms of exclusive lock per push becomes 0.01 ms per hour. Finish detection stays inline, so the horn still fires on the fix that crossed the line, but only for the trackers that just reported rather than re-walking every boat in every armed race.

**The web server can be sized from Settings.** Worker threads, connection limit and idle timeout, defaulting to the environment and clamped so nobody can lock themselves out. The connection limit had been left at the Waitress default of 100 &mdash; and it counts open sockets rather than people, so with a browser holding several each, a race with sixteen viewers could exhaust it while every request was fast. Waitress reads these at startup, so the card shows what the running process actually started with and says when a restart is needed.

**New: `runtime/logs/slow.log`.** One line per request over ``RO_SLOW_REQUEST_MS`` (default 250 ms) with method, path, status and duration. Written because the connection exhaustion had four plausible causes in the code and nothing recorded how long a request took &mdash; which means fixing three things that were fine and leaving the one that was not.

**A note on how two of these shipped.** ``test_a_finished_race_ends_just_after_the_last_finish`` finished one boat of two and asserted the window closed anyway: a green test, named for a case it did not cover, demanding the behaviour that froze the chart. And the F discrepancy sat in the same file as the SI position it disagreed with. Where a single function could not be shared, the tests are now contract tests &mdash; the chart and the rounding sequence must **not** use the extended line, and all three detection paths must.

## v0.255

**Courses can go round a headland.** A long course west does not sail in a straight line, because the Ll&#375;n peninsula is in the way &mdash; but the app drew one, straight across Mynytho, Llangian and Trwyn Cilan. Reported as a chart that looked wrong; the picture was the least of it.

- That leg measured **10.90 nm** against the **13.06 nm** actually sailed. Twenty per cent short, for the whole leg, in distance-to-go and in the projected leaderboard. And one bearing of 243&deg;T stood in for 211&deg;T then 278&deg;T &mdash; with the wind at 145&deg;T, a 66&deg; TWA reach and a 133&deg; TWA broad reach modelled as a single leg at 98&deg;, so the polar projection was wrong for both halves of it.
- A **waypoint** is a turning point that is *not* a mark. It is in the geometry and nowhere else: absent from the course board, the announcement, the shortening options, a boat's marks-rounded count, and the chart, which simply bends the leg there and draws nothing. Made on the Marks page with a tick, added in the course builder with a single **Add** button &mdash; boats pass a turning point, they do not leave it on a hand.
- **Passed by a gate, not a radius**, and the reasoning is the interesting part. Boats beating past a headland pass a long way offshore, so a radius would have to be a mile or more &mdash; and the radius test fires on proximity alone, with no departure test to hold it back, so a 2 km radius on a waypoint 12.6 km down the leg advances the walk while the boat is still 84% short of the corner. Widening the closest-approach neighbourhood fails differently: its departure threshold is a fixed 50 m, so one tack at 1900 m counts as leaving a 2 km neighbourhood. A boat is past a waypoint once it draws level with it, which cannot fire early and does not care how far off it happens. Verified to 5 km either side.
- The club's two are **TC** (Trwyn Cilan) and **TP** (Porth Ceiriad). New guide: [`docs/WAYPOINTS.md`](WAYPOINTS.md).

**A compound mark is now actually rounded, not just drawn.** Y and A name a *pair* of corner buoys and carry no position of their own. The chart, the leg lengths and the TWA analysis expanded Y into Ya and Yb; the GPS walk had its own loop, looked up "Y", found no position and dropped it silently. A course reading `1 Y O` was charted as 1 &rarr; Ya &rarr; Yb &rarr; O and **walked as 1 &rarr; O**: nothing was required at the Gwylans, and since the finish is only looked for once every earlier mark is rounded, a boat could be recorded as finished having never sailed west of Abersoch. Latent only because no *fixed* course uses one &mdash; but the picker offered nothing else for the Gwylans, so every hand-built course to them hit it. Progress is recomputed from the stored track, so a race in flight corrects itself; **a finish already recorded does not, and wants checking against the video**.

**A course can be sent round one corner of a compound mark.** `Ya`, `Yb`, `Aa` and `Ab` are now offered in the builder, filed under their parent rather than adrift at the end of the list alphabetically. Named properly too: the board reads `Yap` and not the internal key `YAp`, and the audio says "Gwylan Islands corner Ya" rather than "mark Y A", which read aloud is "mark why-ay".

**Tracker batteries the app was throwing away.** v0.253 read `batteryLevel`, which is what Queclink sends; Teltonika sends AVL IO 113 instead, so 1280 fixes from the ATC700 stored no battery at all &mdash; a dash on the Trackers page and a dashboard warning that could never fire, for the one tracker that needs it. Measured over 3&frac12; hours on the club's own units: the ATC700 reporting every 10 s drains **8.6%/h** against the GL521MG's **0.9%/h** at 60 s, so the warning covered the trackers that survive a week in a bag and said nothing about the one that will not last a long Saturday.

**And a battery reading now says when it is older than the tracker.** A device last heard from a day ago sat showing 65%, which is what it had when it last spoke, with nothing to say so. Past the point where a tracker still counts as reporting, the level is marked *last seen* with the age in the tooltip &mdash; kept rather than hidden, because a stale *low* reading is the most useful line on the page and it is what the dashboard warning fires on.

**Under all of it, one recurring fault.** Three of these were the same shape: one rule with several implementations. The GPS walk against the chart's course expansion; six templates each printing the course chips their own way; and `static/course_map.js`, which expands the course a third time in the browser and knew nothing about waypoints. Where a single function could not be shared, the tests are now contract tests &mdash; no template may loop `course.marks` for chips, the JavaScript route builder and its background-mark layer must both consult the waypoint flag, and the trackers page and its polling endpoint must agree on which fields exist.

## v0.254

**The manuals catch up with the app.** Rebuilding the PDFs restamps their covers from `VERSION`, and that is all it does &mdash; the prose is hand-written in the builders. So four covers went out saying *Covers app version 0.253* over content that stopped at v0.242. Measured before writing anything: of twelve features from v0.243&ndash;v0.253, **none** was covered. Every apparent match was incidental &mdash; the hut *power* battery, "flags hoisted" in an unrelated sentence, SOG in a tracking table that predates the clubhouse display having one.

- **Race Officer's Guide:** tracker batteries (the thresholds, why an unknown level is a dash and not 0%, and that the dashboard warning covers assigned trackers only); the mark-correction arrows, including why a correction applies from now rather than retroactively and that it reaches the automatic finish detection; and the clubhouse display section rewritten to match what is on the television &mdash; hoisted flags beside the countdown, SOG on the cycling leaderboards, the red/green course colours, and following the current race through a create or a delete.
- **Reference Manual:** correcting the mark a boat is sailing to; tracker batteries; finding today's race; the Sailwave CSV exports and why finishing places are deliberately not among them; the in-app markdown documentation viewer; and an Activity log section that no longer says *"There is no in-app screen for it"*, which has been untrue since v0.249 &mdash; now with the settings diff, the redaction rule, and what the `finish direction skipped an earlier crossing` line means when you see it.

**Figures that showed the app as it was eleven releases ago.** Three illustrated text describing something not in the picture: the Trackers page without its Battery column, *Position on the water* without the correcting arrows, and a dashboard whose side menu had no *Current race* link.

- Re-captured from a **throwaway worktree instance**, not the race-office PC. The old `trackers_page.png` published the club's tracker IMEIs into a document that goes to the club, and the low-battery warning only renders when a tracker assigned to a boat is actually flat &mdash; which is not something to sit and wait for. The dashboard still comes from the real machine, because its hardware cards are only worth photographing with real hardware behind them.
- New figures for the dashboard battery warning and the activity log page. The log lines in that figure were produced by **performing the actions** &mdash; two mark corrections and a settings save &mdash; rather than written into the file.
- `simulate_trackers.py` gains `--battery`, because nothing in the repo could produce a battery reading, so neither the Battery column nor the warning could be exercised end to end. It requires `--forward-to` and refuses without it: the OsmAnd endpoint has nowhere to carry attributes and would drop the level silently. The fleet is spread down a ladder rather than in even steps, which would skip clean over the amber band.

**`scripts/README.md`** now says the cover version is read from `VERSION` (it used to claim a literal), and that this makes the cover a *claim* the prose has to earn before a rebuild. It also records the two traps that cost the most time here, both of which read as app bugs and were neither: `search_boats` filters on `status = 'ACTIVE'` in **upper case**, so a lower-case seed leaves every Boat dropdown silently reading *unassigned*; and back-filling a track from `races.start_time` instead of `race_first_start_dt` loses the class offset, which drops the *fastest* boat's first rounding and looks exactly like a fault in the rounding rule.

No application code changed in this release.

## v0.253

**The Trackers page shows each tracker's battery, and the dashboard says when one is low.** The boats carry battery *asset* trackers run at a reporting rate far above their standby design, which is why the documentation tells you to charge them between race days &mdash; it just never said which ones needed it.

- The level was already arriving and being thrown away. Traccar puts it in a position's `attributes` and the GL521MG reports it (@Track protocol V3.05, the `<Battery Percentage>` field), but **both** ingest paths &mdash; the poll and the push forwarder &mdash; built a fixed dict of lat/lon/speed/course/time and dropped the attributes. Confirmed against the club's live Traccar while building this: the key is `batteryLevel`, and one unit was sitting at 25%.
- It is stored on the **fix**, not the tracker, so the page reads it exactly as it already reads *Last reported* &mdash; the newest stored fix per device &mdash; and a race's drain is in the history for nothing extra.
- Red/amber/green on the same dots as *Last reported*: green above 25%, amber at or below (charge before Saturday), red at or below 10% (may not last a race). A tracker that reports no level shows a **dash, not 0%**: not knowing is not the same as flat, and 0% would send somebody off to charge a unit that was never going to say.
- **The dashboard card only appears when there is something to say**, listing the flattest first and naming the boat. Assigned trackers only &mdash; a spare in the drawer being flat is not the business of the dashboard of a race being run &mdash; and nothing at all when GPS tracking is off.
- `battery` (volts) is deliberately *not* read as a percentage: converting it would need a cell chemistry, and guessing one would put a made-up number on the page people use to decide what to charge.

## v0.252

**Deleting a race no longer strands the clubhouse display.** Its state poll was asking about a race that had ceased to exist, getting a 404, and treating that like a dropped connection &mdash; so it asked again every five seconds and went on showing a deleted race indefinitely. It now reloads once on that 404: plain `/bar` picks up whatever race is current now, and a pinned `/bar/<id>` lands on *No race running* and stops there rather than looping. A 500 or a genuinely dropped connection is still shrugged off without a reload, which is what you want on a screen left running all afternoon.

**Creating a race sheet still does not take the screen away from a race being sailed**, and that is deliberate &mdash; but it was not written down anywhere, which is how it came to look like a fault. The current race is the latest one that still has boats *racing*, failing that the latest race at all. So if an earlier race has a boat nobody finished or retired, that race stays current and the display stays on it; finishing or retiring the last boat is what moves things on. Now explained in [`docs/BAR_DISPLAY.md`](BAR_DISPLAY.md), including where to look when a display seems stuck on the wrong race.

## v0.251

**What the race officer does now reaches the screens that are already open.** Three faults reported from the hut within a day of each other, all the same shape: every public view is rendered once by the server and then keeps itself up to date, so anything the server writes into the page has to be told when it changes.

- **A shortened course reached nobody.** The competitor race page already polled a signature and reloaded when it changed &mdash; but shortening changed nothing in it. The course sequence in that signature is built from `course_for_race`, which returns the **unshortened** course (the truncation is applied separately for display), and the `shortened_at_*` columns were not in it at all. Proved before fixing: the signature's course inputs were byte-identical before and after calling a shortened course.
- **A start time set reached the clubhouse display last of all** &mdash; which is to say never. Its state poll watched the camera window and whether the current race had moved on, and nothing else, so the television sat on *Waiting* with a dead clock and no flags. It now polls a hash of everything it is rendered from: the course, the sequence, the start time its clock counts from and the schedule its flags come from. Deliberately **excluding** entries and finishes, because reloading a television in the middle of a finish sequence would restart the map, the camera and the board cycle at the worst possible moment.
- **Neither reached anybody on the Chart tab**, which is where you watch a race. A reload throws away where a viewer had scrubbed the replay to, so the page holds one back while the chart is open &mdash; and it held it for as long as the tab was open at all, on the reasoning that a replay of a race already sailed cannot go stale. It now holds the reload only while the viewer has actually **wound back into the past**; at the live edge a reload costs nothing, and with no track loaded there is no scrub position to protect. That last case is the reported one: a race with no start time has no track, so the old code held the reload for ever and the page stayed on *Start not set*.
- **The competitor landing page had no state poll at all.** It ticked its countdown and refreshed the wind, and never asked whether anything else had changed &mdash; so setting the first warning signal left every open copy reading *Not set*, at the one moment everybody is looking at it. It now asks `/public/current/state`, which answers nothing but `{"ok", "signature"}`.

**The countdown phase labels are shared too.** *Warning signal window*, *Preparatory period*, *One minute* were computed from 300/240/60 written out again in both admin race sheets. They come from the same RRS 26 timings as the flags, so they now come from the same place: `static/signal_flags.js`. A change to the sequence cannot move one and leave the other.

## v0.250

**The clubhouse display shows the hoisted flags.** It is the one screen the room is looking at during a start sequence, and it showed the countdown without saying what was up the mast.

- The flags sit **beside the countdown**, laid out right to left: the first flag hoisted is nearest the clock and each later one is added to its left, the way they go up. With nothing up, the row takes no space at all rather than leaving a hole in the header.
- Sized for a television rather than reused from the competitor page, and only redrawn when the flags actually change &mdash; this is a screen left on all afternoon, and rewriting identical markup every second restarts the image decode on some sets.
- Verified live in a browser through a real start sequence: three flags up at four minutes, reading **P / Class 2 / Class 1** left to right.

**Which flags are up is now one rule, not four.** It lived inline in the competitor page's template, and a copy of it in the race sheet, and another in the pursuit race sheet &mdash; a second copy is exactly how this codebase has gone wrong twice this month, in the live course walk against the replay's and in the comparison harness against the app. It is now `static/signal_flags.js`, called by all four pages, with the RRS 26 windows (class flag from the warning signal to its start, P from the preparatory signal to one minute) named once. A test fails if any page reimplements it.

**The pursuit race sheet had quietly lost Code flag S.** Found while checking whether the admin pages really were duplicates: the standard race sheet and the competitor page both show S while a shortened course is being sailed, and the pursuit sheet had no code for it at all &mdash; its panel was never even given the `course_shortened` flag. A pursuit race can be shortened like any other, so the race officer running one saw nothing while the competitors' phones showed S. It also gets back the `aria-label`s and the `data-numeral` fallback that keeps a numeral readable if its image fails to load, which is the fault v0.163 fixed for Code flag S. Verified on a shortened race across all four pages.

## v0.249

Four things noticed in testing, all small and all about looking at the app rather than running it.

- **The clubhouse display shows each boat's speed.** The order says who is ahead; the speed says whether that is about to change. Taken from the same interpolated position the chart draws the hull at, so the number and the boat agree. A finished boat shows a dash &mdash; its last fix is not news.
- **The course sequence on that display is properly red and green again.** It was drawn in pale tints (`#dcfce7`, `#fee2e2`) which read as plain grey from the other side of a room, which is the only place that page is ever looked at. It now uses the same solid port/starboard as every other page, with white text.
- **A "Current race" link in the side menu**, between Dashboard and New race. It was the most-visited page in the app and the only way to it was Races, then finding today's race in the list.
- **The activity log can be read in the browser** &mdash; a discreet link under Settings &rarr; Recent hardware events. It records every change made in the app, including what settings changed *from*, and until now it could only be read as a file on the race-office PC, which is no use to somebody asking "who changed that?" from the other side of the club. Newest entry first, one button per day. Administrators only, since it names who did what; **read-only by route**, with nothing that can edit or clear it &mdash; a test asserts no method but GET is accepted. The day comes from the query string, so the filename is built from a validated date rather than joined onto a path (nine traversal attempts are pinned in tests).

## v0.248

**Every GPS finish was being recorded on the wrong crossing.** A race officer walked a simulated race through the replay and found the boat had plainly crossed the line at 13:28:34 while its recorded finish read 13:29:09. The time being stored was the boat crossing the line *again* on its way back to the marina.

- A finish is a crossing that **leaves the course**, so something has to say which way that is, and it was the centroid of the course marks. That is the wrong point: the side of a line is measured against the **infinite** line through its two ends, so a course with marks on both sides of that extension has a centroid that can sit on the far side from where the boats actually come in. The direction test is then exactly backwards &mdash; the real finish reads as *entering* the course and is thrown away.
- It is now taken from **the last mark rounded**, which is what defines the finishing direction: the racing rules have a boat finish by crossing from the **course side**, and on the last leg that is the side the last mark is on.
- **This was not one odd course.** 8 of the club's 12 courses have marks that straddle the line that way, course 1 among them, and every tracked boat-race in the database had been finishing **25 to 46 seconds late**.
- It survived because those finishes were recorded by **GPS auto-confirm**: the stored time *was* the detector's own output, so there was nothing for them to disagree about. Which is the argument for confirming GPS finishes against the finish video rather than arming auto-confirm unless the hut is unmanned.
- The replay walk takes the same direction, so a replay and the live view still agree.

**The direction test can no longer fail silently.** A crossing that happened with every mark rounded and was refused by the direction test alone is the exact signature of this bug, so it now goes to the activity log with both times and the gap between them &mdash; either the boat did cross the wrong way first and the video settles it, or the direction is wrong again. Replayed over the races already in the database, the old behaviour produces that line for all eleven of them and the new behaviour for none. Worth remembering that on the club's own data the direction test changes no answer at all: it is the racing rules' *course side* requirement, and anything it rejects is therefore worth a second look.

**Two matching holes in the harness that had missed it.** `scripts/compare_rounding_tests.py` compares candidate detectors against recorded finish times &mdash; and all 11 of its cases were `gps-auto`, so it had **no independent ground truth at all**. It now says so, loudly, and counts how many finishes were timed by a person. Its self-check (part F) also compared only *whether* the app found a finish, not when: it waved this 34-second change straight through. It compares the time now, and that was verified by deliberately breaking the script and watching F catch it.

## v0.247

**A boat that rounds a mark wide is no longer invisible.** Rounding was decided by proximity alone &mdash; inside the mark's radius, 50 m by default &mdash; which asks how *tidily* a boat rounded when the question the app needs answered is whether it got round and set off on the next leg. Judging the rounding itself is the fleet's business, not the app's.

The cost was never just a wrong number. The course walk is sequential, so one mark the app could not see rounded left the boat reading several marks behind where it was for the rest of the race, and because the finish line is only watched once every earlier mark is rounded, **its GPS finish never arrived either**, however plainly it crossed.

- A second test now runs alongside the radius: the boat came within the mark's **neighbourhood** and has since opened up 50 m from its closest approach, with the *next* mark nearer than it was at that moment. The radius is still tried first, so a tight rounding registers instantly as before and gains no latency.
- **No new setting.** The neighbourhood is 400 m, capped at half the shorter leg touching that mark, so a short-legged course cannot have a neighbourhood wider than its own legs &mdash; and never tighter than the rounding radius. It follows the course rather than needing to be tuned.
- **It is never early.** Measured against recorded tracks it fired 30&ndash;60 s *after* the radius on a tight rounding, so a boat cannot be credited with a mark it has not yet left, and a tack on the beat is not mistaken for a departure. The next-mark condition is what separates a rounding from a boat merely passing.
- **Chosen by measurement, not argument.** Over every race carrying a race-officer-recorded finish time the new test agreed with the radius to within a second; it held a rounding pushed 400 m wide where the radius lost it at 100 m; at 30-second reporting intervals it still found finishes the radius missed entirely; and it was *better* at rejecting a track walked against a course it never sailed. A turn-gate plane at each mark &mdash; the obvious first idea, and the one this set out to build &mdash; measured **worse** than the radius, finding none of the recorded finishes: a plane's before/after sides are global rather than local to the leg being sailed, and on a real track the boat began on the far side of it and made its one outbound crossing 851 m away.
- **The harness that chose this lives in the repo** as `scripts/compare_rounding_tests.py`, because the evidence was thin: eight boat-races had both stored fixes and a race-officer finish time, and five of those were simulator tracks. It is read-only, prints six comparison tables, says so when the sample is small, keeps the rejected turn-gate for the record, and checks itself against the walk the app actually ships. Re-run it once there is a season of real racing behind it &mdash; `python scripts/compare_rounding_tests.py`.
- Beyond the neighbourhood a boat is not rounding that mark at all, and v0.246's **Sailing to** arrows remain the remedy &mdash; as they do for a mark that has dragged out of reach, or a tracker that slept through the rounding. The arrow tests had to be rewritten around a 618 m berth, because the 300 m one they were built on is now handled without anybody pressing anything.

## v0.246

**The race officer can correct the mark a boat is sailing to.** A mark counts as rounded only when a boat passes inside its rounding radius, and the walk through the course is sequential &mdash; so one wide rounding is not a cosmetic problem. The boat shows several marks behind where it is for the rest of the race, and because the finish line is only watched once every earlier mark has been rounded, **its GPS finish never arrives either**.

- The **Sailing to** column at the end of each *Position on the water* row carries a **&larr;** and a **&rarr;**. Forward treats the mark the boat is heading for as rounded; back puts it on the previous one. The row then shows *set*, so a hand-corrected boat is not mistaken for a detection and nobody goes looking for a fault that is not there.
- **A correction applies from the moment it is made, and that is the whole design.** Applied from the start of the track it would reach back over the entire race &mdash; and because O is both the finish line's mark and a mid-course rounding mark on most club courses, a boat nudged on to the line could then be "finished" by a crossing it made on an earlier lap, at the wrong time. Taking effect from now is also what makes the back arrow hold: the walk resumes from the corrected mark instead of immediately re-counting the rounding already behind the boat.
- **It reaches the finish detection, not just the display.** That is most of the point: a boat stalled on a mark the app never saw it round can never satisfy "every earlier mark rounded", so its finish would go on being missed however plainly it crossed the line.
- The arrows stop at the ends &mdash; back from the first mark and forward past the line are both refused, because finishing a boat is the finish button's job and not a side effect of an arrow. A finished boat shows no arrows. Every press is in the activity log with the marks either side of it, and in the race log as a note. A replay of the race shows the correction too, from the moment it was made.

**A user with the Set marks permission can edit a mark, not only re-measure one.** The permission already lets somebody stand next to a mark and set it to where they are, so requiring an administrator to type the same correction &mdash; or to give that mark its own rounding radius &mdash; was the wrong line to draw. Whoever has just re-laid it is the one who knows. **Adding and deleting** marks stay with administrators: those change the set of marks that every course sequence is written against. Mark layers are unaffected, still confined to the phone page.

## v0.245

**The activity log now records everything that changes state, and a settings save says what it changed.** The log answers "who changed that, and what was it before?" &mdash; and it could not answer the second half. A save recorded only that somebody pressed Save, and 35 of the 65 things you can post from the app recorded nothing at all.

- **A settings save lists the keys that changed, old value to new.** `horn_active: 1 -> 0` is the line that would have explained the v0.242 horn fault a fortnight later. Only the differences are listed, not the whole form, and a save that altered nothing is still recorded so "who was in Settings" stays answerable.
- **Credentials are never written.** Anything whose name contains password, secret, passphrase, token or access key is reported as `changed` or `set` with no value, in either direction. Matched as substrings so a setting added later is redacted by default rather than needing to be remembered &mdash; this is a plain-text file on the race-office PC and it must not become somewhere to read a credential out of.
- **An entry edited by hand is now logged**, with the old and new finish time and status. It is the one action that can quietly change a published result, and it was silent. An edit that changes nothing is not logged.
- Also newly recorded: a **backup downloaded or restored** (a restore replaces the data the club runs on), a **series edit** with its discard profile and class count because that re-scores every race at once, a **manual course saved**, **IRC/YTC rating imports**, branding and polar/sail-chart changes, and public video uploads retried or given up on.
- Horn presses and race log events are deliberately still absent &mdash; they go to the per-race event log on the race page, which is a better place to read a race back &mdash; and GPS position ingest would bury everything else. Everything else that writes to the database now leaves a line.

Full list of what is recorded in [`docs/SETTINGS_AND_ADMIN.md`](SETTINGS_AND_ADMIN.md); that page's summary had also drifted behind three releases and is now correct.

## v0.244

**The startup log was half unreadable.** A v0.243 install log read fine for the timestamped lines and came out as `R e q u i r e m e n t   a l r e a d y   s a t i s f i e d` for everything pip printed.

- Two cmdlets were writing the same file and they do not agree on a default encoding: `Add-Content` for the timestamped lines, `Tee-Object` for the piped output of pip and the app. On the hut's build of Windows PowerShell 5.1 `Tee-Object` writes **UTF-16LE**, so half the file was two-byte characters being read as one. Windows 11's newer 5.1 build writes UTF-8 from `Tee-Object`, which is why development never showed it.
- Everything now goes through one writer at a stated encoding &mdash; UTF-8 without a BOM, appended directly &mdash; so the file is the same on every machine and can be pasted anywhere. Verified on a real launcher run: one encoding throughout, non-ASCII round-tripping, and the exit-code behaviour from v0.242 unchanged (stderr proceeds, a non-zero exit still throws with its real code).
- This surfaced only because the v0.242 fix let pip run to completion for the first time. Before that the step aborted on its first line of stderr, so almost no native output ever reached the log to be mangled.

Nothing else changed: v0.243's install itself was clean, and the launcher fix is now proven on the hut &mdash; pages of pip output, progress bars and all, without aborting.

## v0.243

**The race and series CSV exports are Sailwave import files.** The club scores here and keeps its published history in Sailwave, so these downloads exist to be imported rather than read &mdash; and until now they were stacked human-readable tables that Sailwave could not parse at all.

- One header row of field names Sailwave recognises, then **one row per competitor per race**, races told apart by `RaceNo`: the shape its importer reads. Every column is a documented Sailwave field name, so an import needs no column mapping. Confirmed working against a real import.
- **One file per rating system.** A Sailwave series is scored under one system and a competitor carries one `Rating`, but this app produces IRC *and* YTC from the same finish times, so each page offers both and formats the ratings the way each system is written (IRC TCC to three decimals, YTC whole). Mixing them in one file would silently rescore a fleet.
- **`Place` is deliberately not exported.** Sailwave scores from elapsed time and rating; sending a finishing order as well would import this app's arithmetic and then ask Sailwave to redo it, with nothing to say which wins if they disagreed. `Elapsed` *is* sent alongside `Start` and `Finish`, because a race here can have per-class start times.
- Races are numbered by **position in their series**, so importing week by week lands each race in its own Sailwave race rather than overwriting race 1 every time. The series export is every race in one file; the standings are not exported because Sailwave works out its own totals and discards, which is the reason for importing.
- The readable version of all of this is still the HTML publish. Full column reference and import steps in `docs/WEBSITE_PUBLISHING.md`.

**The bundled markdown documentation can be read in the app.** Twenty-five `docs/*.md` files describe how everything works, and the only way to read one was to find the file on the PC &mdash; no use when TROUBLESHOOTING is the one you want *while* something is going wrong.

- The Documentation page's **Common links** section is now split, with **Reference documentation** beside it listing every document. Each opens rendered, with the others alongside it.
- **The list discovers itself**: files are found by globbing and titled from their own first heading, so a new document appears without being registered anywhere.
- **Links between documents work.** These docs cross-reference each other constantly, and a rendered page whose internal links all 404 would be worse than no rendering. `.md` links are rewritten to the viewer (anchors kept) and PDF links to the PDF route.
- Three files outside `docs/` are included by name &mdash; the README as *Overview and quick start*, the relay setup and the Windows deployment scripts. `scripts/README.md` is deliberately **not** offered: `scripts/` is excluded from the release ZIP, so it would be a link that 404s on every install. Named explicitly rather than by widening the glob, so a slug still cannot reach a parent directory.
- Needs the new `Markdown` package. Without it the raw text is shown rather than the page failing, the same way encrypted backups degrade without `pyzipper`.

**The wind dial drops the word "Wind" and enlarges both readings.** It appeared in the panel caption, the card heading and the tab already, while taking the roomiest part of the dial. The direction moved up into that space and the speed sits below the hub, both at 20px rather than 19 and 14 &mdash; neither reading is the secondary one. The clubhouse display gains most: at that size the speed went from about ten actual pixels to fourteen. Every clearance measured on all three pages that carry the gauge.

## v0.242

**Clearing "Assert output line during horn blast" reported the manual horn button as permanently pressed.** From the hut, while testing a race: a continuous stream of `Manual horn switch detected` with nobody near the button. Every one scheduled an evidence clip, and the 84 that piled up then held up the nightly off-site backup. Ticking the box again stopped them.

- On the ProLog wiring these are not independent settings. DTR drives the horn relay, RTS is held asserted as the relay-feedback common, and the relay's auxiliary contact ties RTS to DCD when idle and to CTS when active. Clearing the box means active-low, so the app holds DTR **asserted** at idle &mdash; which energises the horn relay continuously, and its feedback contact then ties RTS to CTS, exactly the signal the app reads as a pressed button.
- **The phantom events were the visible half; the relay being held in is the serious half.** That is the horn. So the combination is no longer offered: the app already forced the horn line, the input line and the input polarity when sensing is enabled, and now forces the output polarity too &mdash; stored as well as applied when read, so the tickbox never shows one thing while the app does another. The Settings page locks the box and says why rather than silently overriding what somebody just chose. Active-low remains available for other relay wiring with input sensing switched off.
- Tests cover both halves: that the setting is forced and persisted, and what `set_serial_output_inactive` actually puts on the wire. `TROUBLESHOOTING.md` gains the symptom, since a stream of phantom horn events is what somebody would search for.

**Stuck public-video uploads can be given up on.** There was a button to *retry* them and nothing to stop trying, and a backlog also holds up the nightly off-site backup, which stands aside while race videos are still uploading.

- Settings &rarr; Video &amp; camera &rarr; **Give up on stuck public video uploads**. Nothing is deleted: no clip, no evidence file, no public copy. Only the publishing state changes, and the retry button still selects abandoned clips &mdash; so fixing the bucket later and pressing Retry queues them all again. Giving up on publishing a race video must never be the same as losing the record of the race.
- The deferral message now names the way out rather than only the problem.

**A running off-site backup no longer reports itself as deferred.** The same report showed `Running now: Deferred — 84 race video upload(s)`, which is two incompatible things at once: `run_offsite_backup_once` checked "is one already running?" *after* the busy check, so a scheduler tick landing during a "back up now" run fell through, deferred, and saved its deferral over the live progress line while the real run carried on underneath. The already-running check is now the first thing it does.

## v0.241

**The clubhouse display shows the wind.** It had no wind reading at all, which is the first thing anyone watching a race asks. The dial from the competitor page now floats over the top-left of the chart, semi-transparent, updating itself every five seconds from the same public endpoint the other pages use — no new route, no second copy of the instrument.

- It stays on screen **while the camera is up**, because a start and a finish are exactly when the room asks what the wind is doing. That means it has to read over pale map tiles, over bright sky (the likely top-left of a start-hut view) and over dark water, so its border and drop shadow are load-bearing rather than decorative &mdash; a translucent white card disappears against white sky. Checked against blown-out white and dark water, not just the black placeholder panel.
- `pointer-events: none`, so a transparent box over the chart can never swallow a drag on the one occasion somebody does touch that screen.

**And the wind dial itself was wrong &mdash; on every page that shows it.** Reported from the clubhouse display and true of the race-office dashboard and competitor home as well.

- **The speed needle never reached its own scale.** Its tip sat at radius 88 while the knots ticks start at 95, so it always fell short of the band it was pointing at. It now reaches 96, past the white face, which is where a needle pointing at an outer scale belongs.
- **The title was drawn across the needles.** "Wind" sat at radius 8 &mdash; on the hub itself &mdash; and *before* the needles in the SVG, so both were painted straight over the word. It is now a small label above the centre, and all the text is drawn last so it sits over the needles rather than under them.
- **The readouts were unreadable, and it was the direction ring's fault.** The twelve three-digit bearing labels sit at radius 56; the readouts sat at 28 and 48. So `108°` and `2.5 kt` were rendered through `210`, `180` and `150` &mdash; the reported `2102.5 kt150`. The ring is now **N/E/S/W**, which frees the whole centre and reads far better from across a room; the exact bearing was never on that ring anyway, it is the large readout underneath. The readouts grew to 19px and 14px and carry a white halo, so a needle passing beneath a digit cannot break it up.
- **The knots numbers were 8px**, the smallest text on a display meant to be read across a bar, and they sit outside the bezel on the page background rather than on the white face. Now 11.5px, bolder and darker.
- Direction ticks shortened slightly to buy the vertical room this needed. Every clearance &mdash; title to hub, hub to readouts, readouts to `S` &mdash; is measured rather than eyeballed, on all three pages, and all of the text is confirmed inside the dial face.
- The three guide screenshots that show the dial were re-captured.
- **The release ZIP was shipping SQLite sidecars.** Caught by checking this version's package before publishing it: the exclusion list matched `.db`, and `race_officer.db-wal` does not end in `.db`, so a developer whose database had been in WAL mode packaged `data/race_officer.db-wal` and `-shm` while the database itself was correctly excluded. Worse than untidy &mdash; a stale WAL beside a database is replayed over it on the next open, so an install could come up with somebody else's pages laid over a fresh database. Fixed, and `tests/test_release_package.py` now pins the whole exclusion list: the three databases, their sidecars, `runtime/` (the session key and the first-run admin password), saved video clips and nested release ZIPs.

## v0.240

**A race officer could not record a finish.** From the hut, mid-race: `Finish now` returned `sqlite3.OperationalError: database is locked`, as a 500 page, with no finish saved. A manual-horn clip had wedged FFmpeg for its full 180-second timeout and the disk was saturated behind it.

- FFmpeg was the trigger; the defect was that **`sqlite3.connect()` was called with no `timeout`**, so every connection in the app used the library default of **five seconds** and then gave up. Reproduced on a copy of the hut database: a write that has to wait eight seconds fails at five, and is recorded if it is simply allowed to wait. The timeout is now **30 seconds** (`RO_DB_TIMEOUT_S` to raise it further), on the race, GPS-track and power databases alike.
- Waiting is always better than failing for this write. Nothing the race office does is so urgent that erroring out beats arriving late, and a finish that was never recorded is the one thing this app cannot afford to lose. Both halves are pinned by tests &mdash; that the old five-second default loses the finish, and that the new one records it.
- `synchronous` stays at FULL. Relaxing it is the usual companion to a concurrency fix, and it risks losing the last commits on power loss &mdash; this PC is off-grid on a battery, so that is a normal event here.
- **WAL was tried and deliberately rejected**, with the reasoning recorded in `core/db.py` so it is not switched on by someone reading the SQLite documentation. It genuinely helps (a six-second read blocks a finish in rollback mode and does not in WAL), but a restore overwrites `race_officer.db` in place and in WAL a live `-wal` beside it is replayed over the file just written &mdash; measured: the restore reports success and hands back the *old* rows. Deleting the sidecar does not rescue it on Windows, where it cannot be removed while any connection holds the database open, and this app leaks connections because `with get_db() as db` is a transaction context manager rather than a closer. WAL needs connection lifecycle and a restore that goes through SQLite first.
- A stale `-journal`/`-wal`/`-shm` left by a crash is now cleared when a database is restored, since it belongs to the file being replaced.

**"Save and back up off-site now" returned a Cloudflare 504 while the backup succeeded behind it.** The archive was in R2 and the page reported failure.

- The backup ran **inside the HTTP request**. It snapshots three databases, encrypts tens of megabytes and pushes them over 4G &mdash; minutes &mdash; and the hut is reached through a cloudflared tunnel that abandons a request at around 100 seconds. Reporting the outcome directly was the right goal and the wrong mechanism.
- The button now starts the work in the background and returns at once. The status box on the Settings page carries the progress line (“Uploading 39.3 MB…”) and the result, and refreshes itself while a run is under way. A second press does not start a parallel backup.

## v0.239

**An off-site backup that could not be written reported itself as a broken network.** From the hut: `Network error: [WinError 10053] An established connection was aborted by the software in your host machine`. That text names the wrong culprit and sends you to the router.

- **What actually happens.** R2 answers a request it does not like — most often an API token whose bucket scope does not include the backup bucket — and closes the connection. With a 40 MB archive still going out, the socket is torn down while the client is still *writing*, so the HTTP response, which is where R2 says `AccessDenied`, is never read. Reproduced against a local server that returns 403 and closes mid-body: a 24 MB body loses the status and yields exactly that WinError 10053; a small body returns a readable `HTTP 403`.
- **A preflight now writes a few dozen bytes to the bucket before the archive is built.** Small enough to be sent in one go, so the real status and R2's own error code come back. It also fails *cheaply* — building the archive snapshots three databases and encrypts tens of megabytes, and there was no reason to pay for that only to find out at the end that the credentials cannot write there. The failure now names the bucket and quotes R2's reason.
- **Aborted-connection errors say where to look**: that a connection dropping partway through an upload usually means the far end rejected it rather than that the link broke, and to check the token's bucket scope — while noting a genuinely dropped 4G link looks identical from the hut, so it is worth one re-run.
- **The archive is streamed through curl, with the built-in signer as the fallback.** This is the lesson the public-video uploader already learned and this path had not: give up on a dead link rather than a slow one (`--speed-limit`/`--speed-time`), and stream from the file rather than holding the whole archive in memory to sign it. curl is tried *first* here, the opposite of the video path, because that ordering is right for a small live JPEG and wrong for a tens-of-megabytes archive.

**The PDF guides cover off-site backup.** The markdown documentation had it from v0.238; the guides people actually open did not, because the builders carry their own text.

- The reference manual's **Backup and restore** chapter gains an *Off-site backup* section: setting it up as a field table, why the bucket must be a separate private one and is refused otherwise, the API-token bucket-scope trap that produced the error above, AES-256 and recovering an archive with 7-Zip alone, restoring one (including that a wrong passphrase is refused before anything is deleted, and the `RO_MAX_UPLOAD_MB` limit), when it stands aside for racing, and how to tell it is still happening. With a screenshot of the panel.
- The race-officer guide's good-practice list gains the **dashboard card**: what it shows, and why a backup that quietly stopped is worse than none.
- All four PDFs rebuilt at this version.

## v0.238

**The club's data now leaves the hut.** Everything the race office cannot recreate &mdash; races, results, series, boats, users, the GPS tracks, the mark position histories &mdash; lived on one PC in one hut. The Backup/restore page has always made a ZIP, but only when somebody remembered, and the download landed on a laptop in the same building. A fire, a theft or a dead disk took the lot.

- **Nightly encrypted push to Cloudflare R2.** The same backup ZIP the Backup/restore page produces, AES-256 encrypted, uploaded on a schedule (03:15 by default). The hut opens nothing inbound behind its cloudflared tunnel, so it has to push rather than be collected from; the R2 credentials and the size-derived upload timeouts already proven on race video are reused. Because it is the same ZIP format, an off-site copy restores on the existing page with a passphrase and no new code path.
- **Encrypted in a format that outlives this app.** WinZip AES-256, not a container of our own devising: 7-Zip or WinRAR extracts the `data/` folder given the passphrase, with nothing else installed. A private format would have made the off-site copy depend on the very thing it exists to survive. The passphrase belongs in the club password manager next to the admin login &mdash; without it the archive is not recoverable by anyone, including us.
- **A separate private bucket, and it is enforced.** The account and keys are shared with public video, but the bucket must be a different one and the app refuses to run if it is not. The video bucket is served publicly; a backup in it would be one guessed object key away from being anybody's download, and it holds every user account and password hash in the club.
- **It stands aside for racing.** No backup while boats are racing, in the three hours before a start, or while race videos are still uploading. The hut is on 4G shared with a caravan park and has already lost start videos to Saturday-evening contention; a backup must never be the reason one is lost. A held-back run tries again twenty minutes later.
- **Every guard can become false again.** "Any boat still marked RACING" is never false once a race sheet is abandoned, and "any clip still uploading" is never false once an upload has been given up on &mdash; so each check is bounded by a time window. A nightly job that one stale row could switch off for ever is not a nightly job, and that failure would have arrived wearing the disguise of a deferral.
- **Verified, not assumed.** The upload is checked with a HEAD against the expected byte count, because an upload that returns 200 without the bytes landing is the failure nobody notices until a restore. An unencrypted sidecar manifest goes up beside each archive with the sections, file counts, size and SHA-256, so the club can see what is off-site and check a downloaded copy without decrypting anything first.
- **On the dashboard, including when it has not run.** A card carries the age of the last success and says so plainly when the answer is "never" or "40 days" &mdash; a backup job that quietly stopped months ago is worse than none, because the club believes it has one. Settings has a **Save and back up off-site now** button that reports the real outcome, and lists what is actually in the bucket rather than what the app believes it put there.
- **Retention** keeps the newest 30 copies by default and removes older archives with their manifests after each success.
- Under the hood, Signature V4 signing and the upload allowances moved to a new `core/r2.py` shared with video publishing &mdash; the backup needs HEAD, LIST and DELETE as well as PUT, and two copies of the canonical-request rules would have been two places to get them wrong. The signing is byte-for-byte what it was.
- **Not included, deliberately.** *Video*: the public web copies are already on R2, so backing them up pays twice for the same bytes &mdash; with the stated consequence that those branded, re-encoded copies are what is off-site and the unbranded evidence clips are not. *The relay copy* from the agreed design: it needs a file transport to the LXC that does not exist yet, and R2 is the off-site half.

**A pip warning could stop the Windows launcher starting the app.** Found while installing the new `pyzipper` requirement, but it was never about that package: adding *any* line to `requirements.txt` changes its hash, which makes `start_race_officer.ps1` run its pip step, and the pip step's first action is to upgrade pip itself. When pip cannot delete its own old folder it leaves a `~ip` remnant and prints `WARNING: Ignoring invalid distribution ~ip` to stderr on every later run.

- `Invoke-LoggedCmdLine` routed native commands through `cmd.exe` specifically so that harmless stderr would not look like a failure &mdash; but the `2>&1` was applied by PowerShell to *cmd.exe's* stderr, and cmd passes the child's stderr straight through to it. PowerShell therefore still saw a native command writing to stderr and, with `$ErrorActionPreference = 'Stop'`, still raised `NativeCommandError`. One warning line aborted the script before the app was launched.
- The exit code is the only thing that should decide whether a step failed, which is what that function already checks. So the preference is relaxed around the native call alone and restored afterwards; `Stop` semantics are unchanged for the rest of the script. Verified both ways: a command that writes to stderr and exits 0 now proceeds, and one that exits non-zero still throws with its real exit code.
- Stderr lines are also flattened to their message text before reaching `runtime/logs/race_officer.log`. They were arriving wrapped in PowerShell's `ErrorRecord` formatting, so one line of pip output became six lines of `At line:30 char:9` scaffolding in the log the hut is troubleshot from.
- v0.77 attempted this fix and did not succeed, and `TROUBLESHOOTING.md` said it had. That entry is corrected, and now also covers clearing the `~ip` folder itself.

## v0.237

**The clubhouse display did not know a race was over once a boat retired.**

- It asked whether *every* boat had finished. A boat can leave a race without finishing it &mdash; retired, did not start, did not compete, disqualified &mdash; and those boats never get a finish time, so the test was never satisfied. The clock counted up all afternoon and the bar screen went on showing a race that had ended at lunchtime, with the camera still waiting for finishes that were never coming.
- A race is over when **no boat is still racing**, which is not the same thing. A retirement is a settled result, not a boat on the water. A missing status still counts as racing: leaving the display running is the safer error.

**Known, not fixed:** the display has no notion of a **pursuit race** at all &mdash; no mention of one anywhere in the page, its script or `core/bardisplay.py`. A pursuit starts each boat at its own time, so the single race clock, the counting-down state and the start-camera window are all built on an assumption it breaks. That needs its own pass rather than a patch.

## v0.236

**The PDF reference manual now covers the features it had fallen behind on.** The markdown had them; the guide people actually open did not, because the builders carry their own hand-written text rather than reading the markdown.

- **The three roles.** The users chapter still described an Admin/Race officer dropdown. It now has a section on all three, including why **Mark layer** exists at all: the person who takes a RIB out after a storm is often neither an administrator nor the duty race officer, and that job needs one button on a phone rather than the start sequence, the finish times and the results. It also explains the **Set marks** tickbox as the alternative for a race officer who should be able to do it without a second account.
- **Marks move.** A new section on why a mark's position is a measurement rather than a constant &mdash; rounding is judged within a radius and the walk is sequential, so one mark 60 m adrift stalls every mark behind it &mdash; and the two ways to correct it: typing a position in, or the phone page taken out in the RIB. Covers the 25 m accuracy floor, the 2 km confirmation, that it needs the club's https address, and that on an iPhone each browser is granted location separately. Ends on what the position history is for: a race is drawn and replayed with the marks as they stood when it was sailed, pinned to when the race ended so a mid-race correction counts for that race.
- **The per-mark rounding radius**, and why blank &mdash; following the Settings value &mdash; is the right answer for almost every mark.
- **Choosing the finish line**, in the chapter on setting a race's course: what the ISORA line is, that the start line does not change with it, that the last mark is crossed rather than rounded, and that getting it wrong does not fail loudly &mdash; the crossing test simply never fires.
- Two new screenshots: the mark edit panel with its rounding-radius field, and the phone page as it looks with a fix and a mark picked.

58 pages, up from 55.

## v0.235

**A full pass over the markdown documentation**, checking every file against the app rather than only the parts that had just changed.

Mechanically checked first — every repo path, every URL path and every version string the docs name. That turned up no broken references, which is worth recording: the `data/startline_config.json` and `/public` mentions that looked wrong are both correct (an optional file, and a path prefix in prose).

What did need fixing:

- `DEVELOPER_NOTES.md` said the app had **~174 routes**. It has 213.
- `DATA_AND_BACKUP.md` described `marks.json` as mark positions. It now also holds each mark's **position history** — who set it, when, from what, and every position it replaced — and an optional per-mark **rounding radius**. That history is what lets a past race be drawn as it was sailed, so restoring an older `marks.json` rolls back corrections along with positions, which is worth knowing before a restore.
- `OPERATIONAL_CHECKLIST.md` gained the **finish-line check** before the start. A race sailed to a line it is not set to finishes nobody.
- `TROUBLESHOOTING.md` was missing three failure modes, all of them seen for real: **a race showing no tracks** after its trackers were deleted (nothing is lost, and v0.230 recovers it by itself), **a boat appearing twice in a series result** (two records for one boat, and how to tell which to keep), and **no GPS finishes with the fleet stuck at the last mark**, which is either a dragged mark or the wrong finish line and is worth separating.

**Still outstanding:** the PDF reference manual has no section on the **Mark layer** role or the **mark-ping page**, both added since it was last written. The markdown covers them; the PDF does not.

## v0.234

**Documentation brought up to date.**

- All four PDF guides rebuilt, and **every screenshot re-captured** against the current app. They had drifted a long way: the marks page, the users list, the boats list and the competitor pages have all changed shape since they were last taken.
- `RACE_OFFICER_WORKFLOW.md` now covers **choosing a finish line** on the Course & start tab — why an ISORA race needs a different one, that the start line does not change with it, that the last mark is crossed rather than rounded, and that re-measuring the mark at the seaward end moves the line with it.
- The screenshot capture script for the competitor pages had its address hard-coded, so it could only ever run against a server on port 5050. It honours `RO_CAP_BASE` like the others now.

**The Documentation page.**

- The **Open PDF buttons stepped up and down** across the row, because the cards are laid out on a grid and the guide descriptions are different lengths. Each card is now a column with the button pushed to the bottom of it, so the buttons line up whatever the text above them does.
- Added **Common links**: split screen, clubhouse display, competitor page, the mark-ping page, trackers and hut power history. These are the screens that live on another monitor, another device or with somebody else — which is exactly why nobody has a link to them when they need one. The mark-ping entry notes that it needs the club's https address rather than a LAN one.

## v0.233

Three corrections to the per-race finish line added in v0.232.

- **The start line is always the club line.** Selecting the ISORA finish relabelled the chart's single line as *Start / finish*, which was simply wrong — races start on the PSC line whatever they finish on, and the sailing instruction says so itself. The chart now draws the start line and the finish line as separate things, labelled *Start line* and *Finish line*, with the finish in its own colour so the two are never mistaken for each other on the water or on the clubhouse TV. When a race finishes where it started, it stays one line labelled *Start / finish line* as before.
- **The ISORA line's seaward end is mark F.** The Fairway buoy *is* mark F, so pinning the line to the position printed in the instruction would have frozen it there; using the mark means re-measuring F from the water moves the line with it, like any other laid mark. The shore end stays an explicit position, because the bridge at Plas Heli is a building rather than a mark.
- **The last mark of a course is crossed, not rounded**, when it is the finish line's own mark. Boats sail to the line and cross it; they do not go round the buoy first. The sequence walk never requires its last element to be rounded, so a course already ending at that mark now leaves it as the finish rather than adding another element behind it — true for O on the club line and for F on the ISORA one. A course ending elsewhere, including a shortened one, still has the line appended so the boats have to reach it.

## v0.232

**A race chooses which finish line it is sailed to**, on the Course & start tab.

- Pwllheli's usual line is the ODM (mark O) to the surveyed bridge window: 347 m long, and start and finish are geographically the same. An **ISORA** passage race is not sailed to it. Its sailing instruction 15 gives the finish as the transit between the Pwllheli Fairway Buoy and the bridge at Plas Heli, bearing 297 degrees magnetic — a line **1.6 km** long whose shore end is **787 m** from the club one, and which the instructions go out of their way to say is not the usual line.
- Finishing boats on the wrong line does not fail loudly. The crossing test simply never fires, and every boat sits unfinished at the end of a race that has plainly finished. So the choice reaches everything that uses the line: the course chart, GPS auto-finish, the leaderboard and progress walk, the replay, the public competitor page and the clubhouse display.
- **Auto-finish resolves the line per race**, not once for the whole scan. It used to work one line out before looping over every armed race, which would have finished an ISORA race on the club line.
- The two ends can be given as a **mark** or as an **explicit position**. The club line's seaward end stays mark O, so re-measuring the ODM still moves the line with it. The ISORA line's ends are stated outright in the instructions, so those are the positions the race is sailed to whatever the mark list holds — and neither end of it is a mark on the course, which the chart now copes with.
- Crossing *direction* needed no change: it comes from the course centroid, so it adapts to any line by itself.
- An unknown or missing key falls back to the club line rather than leaving a race with no finish line, which could never finish a boat at all.

**Worth knowing about the positions.** The two in the sailing instruction agree with each other — they bear 292.7 degrees true apart, and 297 magnetic is about 294.5 true with local variation. But the instruction's Fairway Buoy position is about **112 m** from where the app holds mark `F`, and its Plas Heli Bridge is **787 m** north of the surveyed CHPSC bridge window, so the two are genuinely different points and not a typo for the club line. Mark `F` may be worth re-measuring from the water.

## v0.231

**One boat, one record.** Four records for the same boat turned up in the database, one of them with a space in the sail number.

- They are not merely untidy. A series groups a competitor by **boat id** (`series_competitor_key`), so a second record for one boat is a second competitor: its results sit on their own line, scored and discarded separately. A tracker attaches to one boat id, so only one of the records is tracked. And every stored GPS fix carries a boat id, so the boat's track splits with it.
- Adding a boat whose sail number is **already on file** now reuses that record rather than creating a rival, matching on a sail number normalised for case and spacing — `GBR 1210` and `GBR1210` are the same boat however they were typed. The rating importers had always matched this way and reused what they found; the Boats page had no duplicate check at all, which is where the four came from.
- **Deleting and re-adding a boat keeps its history.** Deleting a boat that has raced only deactivates it — the entries have to keep something to point at — so the record being re-added is usually the inactive one, and it is revived rather than shadowed. The page says what it did, naming how many race entries the record it reused already carries, because quietly doing something other than what the button said would be worse than the duplicate.
- **Editing a sail number onto one already taken is refused** rather than merged. Merging two boats that have both raced is not something to do silently behind a Save button.
- Blank sail numbers are not an identity: several boats may have none, and they are never treated as the same boat.
- The Boats page **marks records that share a sail number**, so the ones that predate this can be found and tidied by hand. Which record should survive depends on which carries the race entries, so nothing is merged automatically.

## v0.230

**A race keeps its tracks after its trackers are removed.**

- Deleting the simulated trackers from the Trackers page took every race they had sailed blank: no boats on the chart, no progress on the *Position on the water* list, nothing to replay. The expectation was that a boat's track stayed with the boat, and that was right — `remove_tracker` says so in as many words, and the stored fixes were never touched.
- What was thrown away was the **thread back to them**. Every fix carries the boat it belonged to, and the track lookup resolves a boat's positions by that, so the data was reachable the whole time. But the chart, the progress walk, the replay window and the track history all ask a cheaper question first — *is this entry tracked?* — and that was answered from the trackers table, which is the exact row being deleted. With the answer "no", none of them went on to look.
- The question now falls back to **the device the boat was last seen on**, whether or not it still has one. Nothing else changes: positions are still found by boat, so a boat that has been through two trackers still gets its whole track, and this only decides whether there is a track to go looking for.
- It does not invent tracks. The fallback is answered from stored fixes, so a boat that never reported is still untracked; a per-race loaner override still wins over everything; and a boat refitted with a new tracker resolves to the new one, not the one it used to have.

**If a race already went blank**, it comes back on its own with this release — nothing was lost and there is nothing to restore. Re-adding the tracker with the same unique id would also have fixed it, and still works.

## v0.229

**A map on the dashboard**, below the race and wind cards.

- Every mark, and any tracker that has reported in the **last hour** — the same window the Trackers page calls reporting, so the two pages cannot disagree about who is out. No course and no leaderboard: those belong to a race's own chart. This answers the one thing the dashboard could not, which is whether anything is on the water at all.
- **The zoom follows the activity.** With boats out it fits them and the marks around them; with nothing out it fits the racing area. Fitting every mark instead sounds right and is not: the marks reach the Gwylan Islands and the Causeway at 23 km, and a pair of temporary passage-race marks 98 and 115 km out once put the whole Llyn peninsula on screen with the bay as a smudge. Those two are gone, but marks like them get laid again, so anything beyond 40 km is left out of the *zoom* — it is still drawn, and a tracker is never excluded however far out it is.
- Its own endpoint rather than the Trackers page's, which asks Traccar for devices it has not adopted yet — a network call out of the hut, on a page every race-office screen leaves open.

**The mark edit form ran off the side of the page.**

- It lived in the actions cell of the marks table, and a table cell is sized to its content: a six-field flex row was granted every pixel it asked for, so `flex-wrap` never fired. Opening one panel made the page **1216px wider than the window** and reading the rest of the table meant scrolling past the form. The per-mark rounding radius added in v0.228 was the sixth field and tipped it over.
- The panel now has a row of its own spanning the table, and the fields lay out in a grid whose column count follows the width — one row on a desk, a stack on a phone, no breakpoint for each. The table itself uses a fixed layout so a cell can no longer widen it, and scrolls inside its own box rather than taking the page sideways.

**Also fixed while building the map:** the dashboard had no Leaflet stylesheet. Without it the map panes are `position: static`, so the tiles flow down the page as a broken mosaic and every marker lands about 3000px below the map — and nothing errors, so it reads as a broken map rather than a missing file. There is now a test that every page carrying a map loads it.

**Removed** the temporary marks `GS` (Graystones Finish) and `SC` (South Codling). Neither was used by a course, a race or the start/finish line.

## v0.228

**A rounding radius per mark**, set in **Marks → Edit**.

- One radius for every mark was always a compromise. A buoy on a long scope swings a wide circle and needs a generous radius; one boats are told to give a berth needs more room still. But a figure set large enough for those is large enough, at a mark rounded twice in the same course, to count roundings that never happened — and the walk is sequential, so a mark counted early throws off everything behind it.
- The Settings value stays as the default and is now labelled as such; a mark overrides it only if it needs to. The marks list gained a **Rounding** column showing each mark's radius, with the inherited figure in grey so a blank is not something the reader has to go and look up.
- **Blank is not stored as a number.** A mark that inherits follows the Settings value when that value changes, rather than being frozen at whatever it happened to be the day somebody last edited the mark. Clearing the field puts a mark back to inheriting.
- Bounded at 10–500 m, the same range the global setting accepts, so neither can be set to something the other would refuse. A value the file cannot parse falls back to inheriting rather than raising mid-race.
- Editing the radius does not stamp the mark as re-surveyed — only a changed position does that.

## v0.227

**A mark pinged during a race now counts for that race.** v0.226 pinned a race's marks to its **start** signal, which had the common case exactly backwards.

- A dragged mark is not usually noticed in advance. It is noticed *during* a race, because the first boats round it and do not register as having rounded. Somebody with the mark-layer role then pings it from a RIB as they round — mid-race. Pinned to the start signal, that correction is stamped after the race began and so is filed *after* the race: the race ignores it entirely. The boats already round stay unrecognised, and so does every boat behind them, because the walk is sequential. That is worse than the behaviour before v0.226, where a mid-race ping at least fixed the rest of the fleet.
- A race is now pinned to **when it ended** — the last boat home. A correction made while the race was still being sailed therefore belongs to that race, permanently, and not merely while the race happens to still be live. The earlier roundings come back too: the walk recomputes from every stored fix, so it re-reads the whole race against the corrected position.
- While any boat is still racing, the marks are simply the current ones, so a correction takes effect the moment it is made.
- The other half of the bargain is unchanged, and is still the point of the feature: a mark corrected *after* a race has finished does not reach back into it. A race relaid for a new season keeps the geometry it was sailed to.
- What this still does not recover: a drag noticed only after everyone has finished. Pinging it then is filed after the race and the chart and *Position on the water* walk for that race stay wrong. `docs/TRACKING.md` says so, and says what to do about it. Recorded finish *times* are unaffected in every case, being times rather than geometry.

## v0.226

**A race is now drawn, replayed and analysed with the marks as they stood when it was sailed.**

- Correcting a dragged mark used to reach backwards through the whole season. An old race was redrawn against a buoy that had been somewhere else on the day, its legs re-measured and its course length changed — and, worse, its boats re-walked around the course. Rounding is judged within a radius of the mark's *recorded* position, so a correction bigger than that radius put every recorded rounding outside the circle; and because the walk is sequential, the first such mark stalls every mark behind it. A race that scored perfectly on the day would afterwards read as a fleet that never got round.
- The positions were already being kept — each stamped with the moment it was set, each superseded one kept with its own stamp — so the position in force at a past moment is simply the newest whose stamp is not after it. `marks_as_of` hands back a whole marks dict with the clock wound back, and since every consumer already took a dict of that shape, nothing else had to learn about history: the course chart, the replay, the leg analysis, the course length, the rounding walk, the public competitor chart, the clubhouse display and the finish line all wind back together.
- **The finish line winds back too.** O is a laid mark like any other, and it is the seaward end of the start/finish line — a corrected O would otherwise move the line under races already scored.
- Anchored on the race's own start signal. A race with no start time yet — one still being built — uses the marks as they are now, which is what it will be sailed with. Recorded finish *times* were never affected either way, being times rather than geometry.

**A Mark layer role.**

- The job of re-measuring a mark after a storm belongs to whoever can take a RIB out, who is often neither an administrator nor the duty race officer. Until now the only way to let them do it was a race officer's login — the start sequence, the finish times and the results included, to press one button on a phone.
- A mark layer reaches the phone page, the POST behind it, and their own password. Signing in lands straight on the phone page, and any other page sends them back to it rather than showing an error: on a phone in a RIB a stray tap on a bookmark should land somewhere useful. The allow-list is deliberately an allow-list — a block-list would silently admit every route added later, which for a role that exists to be small is the wrong way round.
- The role carries the permission, so the **Set marks** tickbox is not offered on their rows; it would be a lie. It remains for a race officer who should also be able to do it without a second account.

**The Set marks tickbox showed the wrong state.**

- It saved correctly and was enforced correctly, but came back unticked however it had been set, so there was no telling who held the permission. `list_users` names its columns explicitly to keep `password_hash` off the page, and `can_set_marks` was never added to the list. Also fixed: the role dropdown treated anything that was not an administrator as a race officer, which a third role would have displayed as the wrong one.

## v0.225

- **Worked in Safari, dead in Chrome, same phone.** On an iPhone every browser has to be granted location *separately*, under its own name in Settings → Privacy & Security → Location Services. Allowing it for Safari does nothing for Chrome, so one browser can find the mark and the other sits there — which reads as a broken app rather than a permission that was never asked for.
- The advice on the page is now **chosen by the browser it is in**. It had named Safari's *Website Settings → Location* regardless, which on Chrome sends somebody in a RIB looking for a screen that does not exist. Chrome, Firefox and Edge on iOS get their own name and the phone-level setting; Safari keeps the per-site one, with the phone-level setting as the fallback; anything else gets a plain, non-specific version rather than an iPhone answer on a laptop.
- **Chrome on iOS was the silent case.** Its permissions API answers for the WebKit engine underneath rather than for Chrome's own state, so a phone-level block is not reported as denied — and no error callback arrives either. The twelve-second watchdog was the only thing left that could speak, and it only shrugged; it now gives the full advice.
- The diagnostics line **names the browser**, because the same handset behaving differently in two of them is the symptom that makes no sense without it.
- No in-browser menu paths are quoted for Chrome, Firefox or Edge: those move between versions, and once the phone-level permission is on, the browser asks for the site by itself.

## v0.224

- **The phone page told you nothing while it waited.** Taken out on a phone, the mark-setting page sat on "waiting for GPS…" and never said another word. The cause is that several mobile browsers, iOS Safari among them, only put up the location prompt in response to a **tap**: asked on page load, the request is quietly dropped — no prompt, no success callback, and no error callback either, so the page had nothing to react to and no way to know it was stuck.
- So there is now a **Use my position** button. The automatic attempt still happens where a browser allows it; the button is the route where one does not. It becomes *"Try again"* after a failure.
- **A twelve-second watchdog.** If neither callback has fired by then the page says so, and says what to check — that location is allowed for the site, and that the phone has Location Services switched on.
- **The reason is now on the page** when a browser refuses: permission denied, timed out, or an insecure address, each with what to do about it. A phone has no console, so anything the page cannot do it has to say on screen. It also shows the address it is loaded from and whether that counts as secure, which is the fact that explains most of the failures and is otherwise invisible behind a half-hidden address bar.
- The commonest of those refusals gets **directions rather than a diagnosis**. A phone remembers a "Don't Allow" tapped once months ago and never asks again, so "location is blocked in the browser settings" is true and useless to somebody holding station off a mark. It now names the taps: page settings icon → Website Settings → Location → Allow, and where to look in the iPhone's own settings if that was already set.
- **Every file in `static/` is now checked for being valid JavaScript.** A single apostrophe inside a single-quoted string — `'the club's https address'` — is enough for a browser to discard an entire file and carry on silently, which looks exactly like broken hardware rather than a broken script. The suite passed regardless, because nothing else here loads the JavaScript at all: a `<script>` that fails to compile renders the same as one that works. Each file is now handed to a browser engine to compile.

## v0.223

- **Marks can be moved, because marks move.** A laid buoy drags in a storm, and the app then looks for boats rounding it where it used to be. That is not a small inaccuracy: rounding is judged within a radius of the mark's *recorded* position (50 m by default) and the course walk is **sequential**, so a mark 60 m adrift makes every boat read as never having rounded it — and every later mark stalls behind that, because the walk will not look for mark 5 until it has seen mark 4. The symptom is a fleet stuck at the same mark and no GPS finishes at all; `docs/TROUBLESHOOTING.md` now names it.
- **Set a position from the water.** A page built for a phone in a RIB: take the boat to the mark, wait for the fix to settle, pick the mark, set it. It shows the position and the accuracy the phone claims, and how far the mark is about to move — the button reads *"Move 4 60 m to here"* rather than "Save".
- Two guards, because it is used one-handed in a small boat. A fix the phone itself calls worse than **25 m** is refused: with a 50 m rounding radius a vague fix could move a mark most of the way to the edge of its own circle. And a move of more than **2 km** must be confirmed, because at that distance the likely explanations are the wrong mark picked or a phone still reporting from the clubhouse, not a dragged anchor. Both are enforced on the server, not only in the page.
- It is a **page of its own**, not part of the race-office layout — that layout is a fixed-width sidebar built for a desk, and on a phone it rendered 819px wide in a 390px window with the button running off the edge.
- **Marks can also be edited** on the marks page, for a position off a chart plotter or a survey. Editing a name no longer stamps the position as re-measured, so fixing a typo does not claim somebody went out and surveyed it.
- **A new per-user permission**, *Set marks*, rather than another role: the person who takes the RIB out after a storm is often neither an administrator nor the duty race officer. Administrators always have it. Granted in Settings → Users.
- Every change records **who set it, when, from what, and the accuracy claimed**, and keeps the position it replaced. Nothing reads that history yet — the chart, the leg analysis and the course walk all use the current position, so **moving a mark redraws races already sailed**. Finish times are unaffected, being times rather than geometry. The history is kept because "who moved mark 4, and when" is a question a race committee will eventually ask.
- The phone page needs the club's **https** address: browsers do not hand a position to a page served over plain http, so this cannot work from a bare LAN address however many times the button is pressed. The page says so rather than sitting silently at "waiting for GPS".

## v0.222

- **The README now explains the relay.** It described the app as though it were one PC in a hut, and mentioned the relay only as a link in the documentation list — so a reader had no way to know a second machine existed, let alone what it is for or whether they needed one.
- A new section says what runs where, with a diagram: the relay is the club's **single front door** (`pro.` reaches the hut app through it), the **live camera** relay (one stream out of the hut, however many are watching, fanned out by Cloudflare), and the **GPS tracking server** (Traccar, one open port per tracker protocol). Published video goes to Cloudflare R2 rather than out of the hut on every view.
- It states the fact that explains the shape of the whole arrangement — **the hut PC opens nothing inbound**, so it can sit behind an ordinary 4G router — and what still works **without** a relay: races, results, series, video, horn and weather all run on the hut PC and its LAN. What you lose is the public site, the live camera and GPS tracking.
- It also carries the trade-off the deployment notes record: because the relay is the single front door, if the *relay* is down then `pro.` is unreachable altogether. The holding page only covers the *hut* being down.
- Tests check the section says what the relay does, that the hut opens nothing inbound, what works without one, that the diagram's box lines up, and that it points at both the guide and the working copy of the procedures.

## v0.221

- **Rewrote the README.** It had grown to 413 lines, 250 of which were **56 release notes going back to v0.165** — a second copy of the changelog sitting above anything that said what the app was. It now carries the last three and points at `docs/CHANGELOG.md` for the rest, and is 199 lines.
- **The feature list described the app of some months ago.** It had no mention of pursuit races, shortening a course, hut power monitoring, the weather station, backup and restore, the activity log, the chart's replay, the corrected-time leaderboards or the clubhouse display. Rewritten and grouped, so someone reading it for the first time is told about the whole app rather than half of it.
- **Fixed two things that were simply wrong**: the folder tree still said `app.py` held the routes, which moved into a `routes/` package, and never listed `scripts/` at all. The three PDF guides other than the relay one were not linked anywhere.
- Moved a note about retrying failed public video uploads out of *Main assumptions*, where nobody would look for it and where v0.217 had made it half-true; `docs/TROUBLESHOOTING.md` covers it properly.
- Tests now check the parts that rot silently: that the title and newest release note match `VERSION`, that the release list stays short, that every path the README names exists, and that the feature list still mentions the things it used to omit.

## v0.220

- **Documentation accuracy pass and a full rebuild of the guides and screenshots.** The three main PDFs had said *Covers app version 0.197* through twenty-two releases and described a race page that no longer exists.
- **The version can no longer drift.** Every builder now reads `VERSION` — the file the release process bumps. Two had the number typed in as a literal; the relay guide had it in a constant that *looked* generated because the cover printed it through an f-string. A test fails if any builder hard-codes one again.
- **62 screenshots recaptured** against the current UI, 39 of which had changed: the merged Chart tab with its timeline and roll-up leaderboard, boat hulls turned to their heading in the new colours, the lighter course chart, and the settings and reference pages.
- **Competitor's Guide** — the tracking chapter rewritten. It described boats as arrows on a chart with no way to wind the race back, and knew nothing of the corrected-time leaderboard. It now covers the Chart tab, playback, full screen, and both estimating methods with the ten-minute suppression explained. Also corrected a "Good to know" bullet still telling competitors to tick a box for the live camera, which was removed in v0.181.
- **Race Officer Series Guide** — new sections on what competitors see (and that the live corrected board is not a result and carries no weight in a protest) and on the clubhouse display. The tracker section now mentions that a phone can be a tracker.
- **Reference Manual** — corrected two statements that were simply wrong: that a loaner tracker can be assigned per race on the race sheet (that dropdown went in v0.207), and that boats are drawn on the chart as a heading arrow (they have been hulls since v0.208).
- **Relay Guide** — the port table said the club had only 5004 and 5027 open. 5055 is open too and deliberately so; it now explains what it is for.
- Markdown: `DEVELOPER_NOTES.md` gained `core/bardisplay.py` and lost a stale claim about the per-entry tracker override; the operational checklist, race-officer workflow and video-recording notes picked up the corrected leaderboard, the clubhouse display and the slow-connection upload behaviour; `REMOTE_ACCESS_CLOUDFLARE.md` was missing from the README index.
- Screenshots were captured against a **copy** of the database rather than the live one — the capture scripts create and delete races, and the hut's own data has no business being a test fixture.

## v0.219

- **The clubhouse display now cuts to the camera as boats round the ODM**, not only at the start and the finish. Most courses pass mark O more than once — courses 11 and 14 go round it three times — and O is the seaward end of the start/finish line, which is exactly where the hut camera is pointed, so a mid-race rounding is as watchable as a finish. About a minute either side, captioned with the boat's name.
- That window is neither a known time (like the start) nor a prediction (like a finish), just a **distance**: how far the boat is from the mark against how far it travels in a minute at the speed it is making. One test, no history to keep, and it covers the approach and the exit symmetrically without having to know which one it is looking at. A boat drifting in no wind gets a floor of 150 m so it still appears.
- **A boat that has not rounded anything yet is ignored.** It is sitting by the ODM because that is where the line is, and the start window already covers it — without this the camera would simply stay up after every start on a course whose first leg begins at the line.
- A finish still takes precedence over a rounding, though in practice the camera is looking at both: the ODM *is* the end of the line.
- Checked against the recorded track of a real race on course 1 rather than in the abstract. The display would have shown: camera 4 minutes over the start, chart for 26, camera about 2 minutes for each of the three boats rounding O, chart, then about 2 minutes for each of the three finishes.

## v0.218

- **The clubhouse display now follows the boats that are still racing** instead of showing the whole course throughout. It fits the chart to each racing boat and the mark it is sailing to, so the room can see the leg rather than just the boats on it — a whole-course view spends most of a television on empty sea once the fleet has strung out down one leg. Measured on a replayed race: the view tracked the fleet down the course and back, moving 1,134 m, 901 m and 1,381 m as they worked round, with all three boats in the clear part of the screen throughout.
- Boats that have **finished are left out** of the view on purpose: they are parked by the line and would hold it open across the whole course for the sake of somebody already in the bar. With nobody racing — before the start, or once everyone is in — the whole course is shown.
- It moves **only when it needs to**: when the view no longer holds the fleet, or when it is holding far more than it needs and could usefully close in. Between those it sat perfectly still in testing rather than twitching after every boat length. There is a floor on how far in it will go, so a fleet in close company does not fill the screen with one boat length of sea.
- It fits to the part of the chart that can actually be **seen** — allowing for the header across the top and the leaderboard down the right — rather than to the whole element, which would centre the fleet underneath the board.
- **A corrected leaderboard with nothing on it yet is skipped** in the cycle rather than shown. The estimate is withheld for the first ten minutes of racing, and a column of dashes on a television for ten minutes tells the room nothing.
- **Fixed `scripts/rerun_race.py` replaying hours of nothing.** It announced a 55-minute race as *293 min of racing*, because "everything since the gun" is not a race: some entries carry a tracker that lives on the committee boat and reports all season, and the demo trackers kept running long after their finish. It now uses the app's own replay window, which ends a couple of minutes after the last boat is in — so the same race is 1,953 fixes rather than 4,583, and the clubhouse display and the replay viewer cannot disagree about where a race ends.

## v0.217

- **Fixed public race videos failing to upload from the hut on a slow connection.** Both upload paths carried flat timeouts — 60 seconds for the app's own uploader, 90 for the curl fallback — which are fine on a desk and hopeless from a hut on 4G shared with a caravan park on a Saturday evening. A public race clip is tens of megabytes, so the transfer was being cut off with most of the file already sent, four times over, and nothing reached the bucket (an S3 PUT is atomic: a cut-off upload leaves no object and nothing to resume).
- The allowance is now **worked out from the size of the file** and a floor on throughput of about 20 kB/s, so a 20 MB clip gets roughly twenty minutes instead of ninety seconds. It is bounded at an hour so a wedged transfer cannot hold the upload lock for ever — uploads are serialised, and one that never returns would block every clip behind it.
- More useful than the bigger number: curl is now told to give up **only if the transfer stalls** — under 8 kB/s for a minute together — rather than on a stopwatch. That separates *slow but working*, which should be left alone, from *dead*, which now fails in about a minute rather than sitting out the whole allowance and then burning three more attempts doing the same. A connect timeout keeps an unreachable endpoint quick too.
- `docs/TROUBLESHOOTING.md` gains a section on this, including why the bucket is empty rather than holding a partial file, and that dropping public video quality from 1080p to 720p roughly halves what has to go up a link this slow.

## v0.216

- **Fixed the clubhouse display's race clock running on after the race had finished.** It counted up from the gun for ever, so a race that finished at lunchtime was still ticking on the bar screen hours later. It now stops at the race's elapsed time — the moment the last boat crossed — and reads *Finished* in green. The state endpoint gained `last_finish` so the display has something to stop at.
- **The leaderboard on the display cycles through every board the fleet is rated for**, fifteen seconds each: the order on the water, then IRC corrected, then YTC corrected. Nobody is going to walk up to a television and change it. Overall only — a bar screen is not the place to work through the classes one at a time. Dots beside the heading show where it is in the cycle, only the boards the fleet actually has ratings for appear, and the corrected boards say plainly that a boat still racing is an estimate.
- **`scripts/rerun_race.py` runs a race that has already been sailed again, as if it were happening now.** A new race starting in a moment, fed its own recorded fixes on the clock — for testing the bar display, the start and finish camera windows, and anything else that only comes alive during a race, without waiting for a Saturday. `--speed 6` turns an hour into ten minutes; it scales the recorded **speed over the ground** as well as the clock, because the finish camera window is decided from distance-to-go divided by speed and replaying positions faster while leaving speeds alone would put every boat six times further out than it is.
- Nothing about the source race is touched: the replay makes its own boats and trackers, so a real tracker reporting at the same moment cannot collide with it. `--purge` clears up after previous runs.
- `--list` counts a race's **own** track rather than everything its boats' trackers have ever reported. Some entries carry a tracker that lives on the committee boat and reports all season, which made every old race look like it had 45,000 fixes to replay.

## v0.215

- **A clubhouse display at `/bar`.** A page built for one job: a television in the club bar showing the race that is happening now. The course chart fills the screen with the fleet on it; the race name, start, course and a large race clock sit across the top; the order on the water runs down the right. There are **no controls at all** — nothing to press, and nothing a stray remote can put into a state somebody has to fix.
- **It cuts to the start-hut camera for the two moments worth watching**: from two minutes before the gun to two minutes after, and from about two minutes before each boat crosses the line to a minute after. The header and the order stay on screen while it is up, so the room can see which boat it is watching.
- The start is a known time. A finish is not — a boat arrives when it arrives — so the finish window is **predicted from the distance a boat still has to sail divided by the speed it is making**. Crude over a whole race, fine over the last two minutes, when the boat is on its final approach with the line in sight. A boat that has just crossed takes precedence over one still coming in, because that is the finish the room has looked up for; a boat drifting under half a knot gets no prediction rather than an arrival an hour out. The decision is `core/bardisplay.py`, on the server so it can be tested and so it uses the same course-progress figures the leaderboard does.
- The camera is **started and stopped** rather than left running. The relay serves its stream on demand, and a display left on all afternoon would otherwise hold that stream open the whole time for a room looking at the chart.
- `/bar` follows the club's **current** race and re-points itself when that moves on, so a day of racing needs nobody to touch the TV. `/bar/<race id>` pins it to one race, for showing a race already sailed.
- Everything scales from the viewport rather than being fixed in pixels, so 1080p and 4K both work.

## v0.214

- **The leaderboard now starts rolled away on every screen**, not just on a phone. Opening the Chart tab is a request to see the chart; the board is one tap on the *Leaderboard* tab when it is wanted. v0.213 opened it by default wherever there was room, which meant the first thing a desktop viewer saw was a board over the chart they had just asked for.
- It is rolled away in the markup as well as by the script, so the board does not flash open on first paint before the JavaScript runs.

## v0.213

- **The Chart tab now looks like the full-screen view: the chart owns the panel and the leaderboard floats over it.** It used to be a capped square with the board stacked underneath — on a phone a 300-pixel chart with most of the display spent on everything else. The panel is now a flex column that gives the chart whatever is left after the playback controls, and the board is a translucent roll-up over the bottom of it. On a 1440x900 PC the chart goes from 1194x495 to 1194x583; on a 390x844 phone from 300x300 to 300x423, and 300x747 in full screen.
- **Open where there is room, rolled away on a phone**, one tap either way — a board covering most of a phone-sized chart is not an improvement. Full screen always opens with it rolled away, because going full screen is a request to look at the chart.
- Full screen is now the *same* layout pinned to the viewport rather than a second one, so the two cannot drift apart. Almost every rule moved from the full-screen class onto the panel itself.
- The sheet's anchor is measured with a **ResizeObserver** rather than at load: the Chart tab starts hidden, so a load-time measurement of the control bar is zero and the board lands squarely over the controls it is meant to clear.
- Raised the roll-up above **Leaflet's control layer**. On a phone the map's attribution line wraps across the full width and was swallowing every tap on the roll-up tab, so the board could not be opened at all.

## v0.212

- **Fixed the full-screen leaderboard not being transparent on a PC.** Two layers above the blurred panel were still solid. The **table** the rows sit in had an opaque white background — only the rows had been made translucent, and on a phone that goes unnoticed because there the rows become cards and the table itself draws nothing. And a `prefers-color-scheme: dark` rule tinted the panel navy at 88% opacity: the app has no dark theme, so on a machine set to prefer dark that made the board the one dark, near-opaque thing on an otherwise light page. Both are gone; the panel now renders identically whatever the system prefers, and the chart shows through it.

## v0.211

- **Boats are no longer drawn in the colours the course is drawn in.** Reported from a phone during a live race: a red boat sat on a red leg and a green boat on a green one, and neither could be picked out. Red means a port rounding, green a starboard one, black the finish and yellow the marks — all of that is meaning and none of it is free to change, so the fleet moved instead. Boats are now blues, violets, cyans and pinks, and the sail-number label is outlined in the boat's own colour so hull, track, label and leaderboard swatch all read as one boat. A test pins the palette against the course's colours, so changing one forces a look at the other.
- **The chart can fill the screen.** On a phone it was a 300-pixel square with the leaderboard pushed below it — most of the display spent on everything except the thing you opened. **⛶ Full screen** under the chart gives it the whole window, and the leaderboard becomes a **translucent panel that rolls up from the bottom** and away again, floating over the chart rather than pushing it aside. On a 390x844 phone the chart goes from 300x300 to 390x747.
- It fills the browser window rather than asking for true full screen, because **iOS Safari refuses `requestFullscreen()` on anything but a `<video>`** — and a phone is exactly where this is worth having. Where the browser does support it the real thing is asked for as well, so the address bar goes too; nothing in the layout depends on getting it. Leaving by Escape, the ✕, or the browser's own control all put the page back.
- Three things the first attempt got wrong, found by driving it rather than reading it: the rolled-up board **covered the playback controls**, so the chart became unusable the moment the board was opened (it now sits above them, measuring the bar rather than guessing its height); it **covered the button that leaves full screen**, so there was no way out (there is now a ✕ at a higher layer that nothing can cover); and Escape did not exit when the real Fullscreen API had been granted, which would strand the panel over the page if that key never arrived.
- The roll-up is genuinely translucent. Blurring the panel counted for nothing while the board's cards sat on it in solid white — on a phone each row is a card with its own opaque background — so those let the chart through as well.

## v0.210

- **Documented port 5055, and what it is for.** The relay guide said the club had only 5004 (Queclink) and 5027 (Teltonika) open and to leave the rest closed — but 5055 is open too, and deliberately so. It carries the OsmAnd protocol, which means the free **Traccar Client** phone app can be used as a tracker: set the server to `track.pwllhelisailingclub.org:5055`, pick a device identifier, add it on the Trackers page, and a phone in a pocket appears on the chart. Useful for the rescue RIB or committee boat, for a volunteer's phone when there are more boats than trackers, and for trying the whole chain out before the GL521MGs arrive. `scripts/simulate_trackers.py` speaks the same protocol, which is how a simulated fleet reaches the real relay.
- The phone-as-a-tracker recipe is in `docs/TRACKING.md` where a race officer will look for it, not only in the relay deployment guide.
- Found while doing a live sailing-simulator run through the production Traccar: three boats at 100/93/86% of a J/122 polar sailed course 1 in 194°T, all seven marks in order, and all three finishes were detected and recorded unattended as `gps-auto`. At 29 minutes the board projected the leader at 47:07; it finished in 46:45 — 22 seconds out with 40% of the course still to sail. Median estimate error over the run: pace 25.2% → 4.4% → 2.2% → 1.4% by stage of the race, VMC 8.3% → 1.8% → 2.6% → 0.6%. That is a third independent confirmation that VMC is the better guess early and pace the better one later, and it matches what the offline harness predicts closely enough to trust the harness for iterating.

## v0.209

- **The simulator can sail.** `simulate_trackers.py --sail` puts boats round the course under a polar instead of motoring the rhumb line at a fixed speed: it cannot point closer than the polar's minimum TWA, so a windward leg is beaten in tacks, and it will not sail dead downwind, so a run is gybed. Speed comes from the polar at the angle actually being sailed, every tack or gybe costs a few seconds of it, and `--polar-pct 100,94,88` gives each boat its own share of the polar. The tacking rule is the one a crew uses — carry on until the *other* tack lays the mark, plus a working tack every few minutes.
- Measured against trigonometry on a dead beat: 115.8% of the rhumb distance where 1/cos(39°) says 117.5%, the difference being the 25 m rounding radius at each mark. On the real course 1 at 020°T a boat sails 5.84 nm for a 5.25 nm course in 47 minutes, with 16 manoeuvres.
- **`--upwind-bias` spreads the fleet across points of sail**, not just speed — the first boat quicker upwind and slower down, the last the reverse. A flat percentage is the kindest possible case for any estimate that scales one number.
- **The polar reading is pinned to the app's.** The simulator is standard-library-only by design (it runs on the relay), so it carries its own copy of the polar maths — and a test now checks that copy against `core.polars` across the whole table. A simulator that read the polar differently from the app would make every comparison meaningless.
- **`scripts/verify_leaderboard.py` measures the predicted leaderboard.** It sails the fleet offline in about a second, writes the track into the database as a race that has been run, and asks the estimator what it would have said at every step against the finish each boat actually achieved. Five boats from 100% to 82% of a J/122, course 1 in 12 kn from 020°T: the polar pace factor's median error on the projected finish falls 17.4% → 7.2% → 1.5% → 0.7% through the race, and it had the final corrected order right at 74% (IRC) and 82% (YTC) of snapshots. VMC is better for the first fifteen minutes and worse after — which is why the competitor is offered both rather than one being declared correct. `--keep` leaves the race in the database to scrub through with the real chart.
- Both findings from building it are recorded in `docs/TRACKING.md` and `scripts/README.md`, including the trap that cost the most time: `races.start_time` is the **warning** signal, the gun is five minutes later, and course progress ignores every fix before the gun — so a harness that stored the gun there had mark 1 rounded inside the discarded window, and because the walk is sequential the whole fleet read 0/7 for the entire race.

## v0.208

- **A lighter course chart.** The route was drawn in thick ribbons over dense, opaque mark discs, which buried the chart underneath it. Legs are now hairlines at reduced opacity, marks are translucent with a fine ring instead of a heavy border and drop shadow, unlit marks fade further back than the ones in the course, and the start/finish and Bridge labels no longer shout. The information is unchanged — port and starboard roundings still read at a glance — but the chart under it is legible again.
- **Fixed the leg arrows not sitting on their legs.** They were up to **10.1px** off, and consistently down-and-right, which is the tell: Leaflet's own stylesheet carries `.leaflet-marker-icon { display: block }` at the same specificity as our `.course-map-arrow { display: flex }` and later in the cascade, so the flex centring the arrows relied on silently never applied. The triangle sat at the icon's top-left corner rather than its centre — 6.5px right and 8px down. The same bug put the boat's sail number *beside* the boat instead of under it.
- Arrows and boats are now inline SVG centred on their own viewBox, so they cannot drift however the host stylesheet chooses to style the element around them. Measured again after the change: every arrow is within **0.3px** of its leg. The arrow is a light chevron rather than a filled wedge, so it marks direction without weighing down the leg it sits on.
- **Boats are boat-shaped.** A hull in plan view — fine bow, maximum beam just aft of amidships, flat transom — turned to the boat's COG, so a glance gives heading as well as position. The flat transom is what makes it read as a boat rather than a leaf at twenty-odd pixels. A boat reporting no COG is drawn as a disc rather than pointed in a guessed direction.
- **Each boat is drawn in its own colour**, matching its track and the swatch beside it in the leaderboard, so a boat can be followed through a crowded start without reading every label.

## v0.207

- **Trackers are assigned in one place.** The race sheet's *Entries & finish times* tab carried a per-entry **Tracker** dropdown — a per-race "loaner" override sitting beside Save. It is gone. Pairing a tracker with a boat is a Trackers-page job, and two places to set the same thing is two places to get it wrong on a race morning. The Save button no longer writes that field at all, so an override set before the upgrade is left alone rather than silently blanked the first time an officer saves a finish time.
- **The course chart no longer runs off the bottom of the screen.** It was a square capped only by width, so on a wide screen it drew at its full 1200px and the page ran to two and a half screens — the playback controls and the leaderboard beneath it could only be reached by scrolling the chart off the top. It is now capped at **55% of the window height**. On a laptop that takes the chart from 1194px to 495px and the public race page from 2289px to 1590px, about a third shorter.
- The chart stays **square by preference** — on a phone the width is still the smaller bound, so nothing changes there. On a wide screen the height cap wins and it goes landscape, which suits the screen and shows more of the coastline either side of the course.
- A window resize now tells Leaflet to re-measure. The chart's height depends on the viewport for the first time, so a resized or rotated window changed the container while the map carried on drawing at the size it was built at.

## v0.206

- **A corrected-time leaderboard on the public chart, while the race is still on.** The chart tab's board has a **Leaderboard** picker: *Line honours* (the order on the water, as before), *IRC corrected* or *YTC corrected*, filtered to a class or taken overall. For a boat still racing the app projects a finish time, applies the boat's rating, and ranks on that — so a competitor can see who is actually winning rather than who is in front. A boat that has already finished shows its real elapsed time.
- **Two ways to project the finish, and the competitor chooses.** There is no honest single answer, so both are offered and both are labelled an estimate:
  - **Polar pace factor** — how the boat's time so far compares with the *target* time for the legs it has sailed, applied to the legs it has not. It compares like with like: a slow beat is measured against a slow beat.
  - **VMC** — how fast the boat has actually been closing on the next mark, averaged over five minutes so a tacking duel does not read as a stop, carried over the distance it has left.
- The obvious method — average speed so far over the distance remaining — is the one we did *not* use. It is biased by whatever the boat has just been doing: 30 minutes over a 1 nm beat is 2 knots, and carrying 2 knots over the 2 nm of reaching and running still to come projects 90 minutes for a 45-minute course. Pace puts the same boat at 67.5.
- **Nothing is shown for the first ten minutes of racing** — the convention competitors know from YB Tracking, and for a good reason: before the first mark a boat's pace is mostly which end of the line it started. A boat whose tracker has been silent for three minutes drops out of the ranking rather than being projected from a stale position, and a boat sailing *away* from its next mark gets no VMC estimate rather than a finish time in the last century.
- **The rating is applied by the same code the published results use.** The server sends each boat a *factor* — what one second of elapsed time corrects to, `x TCC` for IRC and `x 1000 / YTC` for YTC — and the browser only multiplies. There is no second copy of the correction formula to drift from the results table. Switching between line honours, IRC, YTC, class and estimating method is a re-sort of data already on the page: no request, no wait.
- Everything works the same wound back in the replay, so you can watch the corrected order change through a race that has already been sailed.
- Fixed the board's **phone cards overlapping**. On a phone each boat becomes a card with two label/value pairs to a line, and the first cell spans the full width as the card's heading — but here the heading is the *position*, so the boat cell (a name over a sail number) sat in a half-width column and lapped over the cell beside it. It now takes the whole width. This was already happening on the line-honours board.

## v0.205

- **Fixed GPS finish detection being locked out of a boat for ever.** Recording a GPS finish leaves a *confirmed* proposal behind for provenance, and detection skips any entry that has one. If the finish is then cleared — a race officer undoing a GPS finish they disagreed with, or a race being re-run — the entry goes back to RACING with no time while the proposal stays, and GPS could never notice that boat finish again. It now skips an entry only while a **pending** proposal is awaiting an answer, or while a confirmed one still matches a finish the entry actually holds. A confirmed proposal with no recorded finish is stale and no longer blocks.
- Found the hard way: three boats sailed the whole course, the tracking engine reported all of them finished, the race was armed for auto-finish — and nothing was recorded, because proposals from an earlier run of the same race were still sitting in the table.

## v0.204

- **One chart, live and replay together.** The Chart and Replay tabs were showing the same fleet on the same course, so they are now a single **Chart** tab. It opens at the **latest positions** — during a race that is simply the live chart — and winds back through everything recorded so far. While you are at the live edge it keeps up on its own; the moment you wind back it stops dragging you forward, the window keeps growing behind you, and **Live** returns you to the front. Playing forward far enough rejoins live by itself.
- **It asks only for what is new.** The track endpoint takes `?since=` and returns just the fixes and order snapshots after that moment, so keeping up costs a few hundred bytes every ten seconds rather than re-sending an afternoon's tracks — which matters most to competitors watching on mobile data. An out-of-range `since` falls back to the whole race rather than leaving a hole the viewer would never fill.
- Fixed a boundary fix being re-sent on **every** poll: the payload publishes times rounded to a tenth, and the server was comparing the caller's rounded value against unrounded fix times. It now filters on the published time, and the viewer also refuses anything not newer than what it holds.
- The live-position poller no longer draws boats at all — the chart owns its own map and keeps itself up to date. Two writers on one map is what made the fleet jump in v0.199; there is now only one. That poller still updates the Entries table.
- Old `#ptab-replay` links land on the chart. With GPS tracking off the tab is the plain course chart it has always been.

## v0.203

- **The public race page no longer reloads itself while you are watching a replay.** It carried a `<meta http-equiv="refresh" content="60">` *and* a state poll that reloads when the race changes — so a replay was thrown away every minute, losing the scrub position and the tab. The meta refresh is gone (the poll already covers real changes) and the poll now holds off while the Replay tab is open. A replay is a record of a race that has already happened; nothing in it goes stale.
- **The replay's order on the water no longer lags behind the boats on the chart.** It was fetched per frame from `/positions?at=`, throttled, so at speed it trailed visibly. The server now computes the order for **every step of the race** and sends it with the track, so the viewer looks it up locally: instant, and no request while playing. Keeping the computation server-side was the point of the original design and is preserved — mark rounding, distance-to-go and finish detection stay the app's own course walk instead of a second version in JavaScript that could disagree with the live view.
- It is cheap because the whole series is **one forward pass** of each boat's track, snapshotting as the clock passes each step — the same cost as a single leaderboard however many snapshots it holds. A 24-minute race: 291 snapshots at 5-second steps, 33 KB, built in under a tenth of a second. A test pins the series against the one-off leaderboard so the two cannot drift.
- The panel says which moment the order belongs to. With 5-second steps that is the most it can ever trail the chart — about a boat length at 20 knots.

## v0.202

- **Fixed simulated boats sailing the whole course and never finishing.** `simulate_trackers.py` returned to the start/finish line's **midpoint** and turned away — and the waypoint before that is the last course mark, usually the ODM, which is an *endpoint of the line itself*. Two consecutive waypoints sitting on the line meant the boat ran along it rather than through it, so no pair of sampled fixes ever straddled it. Measured on the recorded tracks: each boat crossed the line exactly **once, at the start**, and never again. It now approaches 50 m from the course side and leaves 50 m the other side — a square 100 m pass through the middle, which is what a real boat does. Verified against the real course at 12, 20 and 30 knots: one finishing crossing, 7/7 rounded, finished.
- The simulator's perpendicular offsets are computed **in metres**. They were mixing degrees of latitude and longitude, which differ by about 40% at this latitude, so the offsets were not the distances they claimed.
- With that fixed, an armed demo race recorded all three GPS finishes automatically (`gps-auto`), and the replay's order now tracks the chart all the way to the finish.
- `scripts/README.md` records the two traps behind the false starts here: set the **gun before the boats start sailing** (progress only counts fixes after the start, and one missed rounding stalls every mark after it), and judge fix spacing by **speed x the app's poll interval**, not the simulator's `--interval` — at 30 kn that is ~77 m against a 50 m rounding radius.

## v0.201

- **Fixed one missed mark rounding wiping out a boat's whole race.** A mark counted as rounded only if an individual *fix* landed inside the rounding radius. A boat reporting every 10 seconds at 6 knots moves ~31 m between fixes, and a coarser race-day rate steps clean over a 50 m radius — and because progress is sequential, one missed rounding stalls every mark after it. The boat then reads **0/7 and last place for the rest of the race while visibly sailing mid-fleet**, which is also why the replay's order did not match the boats on the chart. Roundings are now measured against the **path between fixes**, the same reasoning that already interpolates the finish-line crossing. In the demo race this took a boat from 0/7 to 7/7 and finished. A leg is only trusted across a normal reporting gap (60 s), so a back-filled outage cannot sweep through marks the boat never approached.
- The replay's order panel now **says which moment it represents** (“order at 12:43:10”, and how far behind the chart it is when that matters). The map redraws every frame from data already in the browser, but the order comes from the server so it necessarily trails at high playback speeds; the throttle is also tightened. Stating it beats letting it look wrong.

- **The boats jumping in the replay were bad *data*, not the viewer** — a different fault from the one fixed in v0.200, which was real but was not what was being seen. Each demo boat's recorded track jumped up to **1,340 m between fixes against a 66 m median**, landing on its own position from a minute earlier and then continuing forward: exactly “backwards to a previous point on the track, then forwards again”. Two `simulate_trackers.py` instances were feeding the same device ids; Traccar keeps only the latest fix per device, so the poll picked up whichever wrote last and the stored track alternated between two boats a kilometre apart. The replay clock itself was measured monotonic over 772 frames.
- **`scripts/verify_replay.py` now checks the recorded track, not just the viewer.** Its real failure was reporting “playback OK” while boats visibly jumped — it was faithfully drawing a track that jumped. It now flags a track whose largest step dwarfs its median and says *the data is bad, not the viewer*, which is the distinction that cost a whole round trip.
- Added a **rewind check**: each drawn position is mapped back to a time on that boat's own track and those times must only increase. This matches the reported symptom most directly. It is read together with the other two — a monotonic clock plus a jumpy track means the data is going backwards; a clean track means playback is. It follows the *previous* match rather than searching the whole track, because a course that starts and finishes on the same line crosses itself: a position near the line matches two times equally well, and a global search flips between them and reports rewinds that never happened.
- The viewer exposes the exact replay time on the panel for that check. The displayed clock and the slider are whole seconds, far too coarse to tell whether playback ever runs backwards between frames.
- `scripts/README.md` warns never to run two simulators on the same device ids, and that `ps` under Git Bash does not reliably list Windows processes — which is how the second instance went unnoticed.

## v0.200

- **Fixed boats jumping backwards and forwards in the replay.** The live-position poller called `updateBoats(document, ...)` — scoped to the whole page — so every five seconds it repainted *every* course map, including the replay's, snapping the replayed fleet to the boats' live positions before the next animation frame snapped it back. Measured at up to **2.6 km of movement in a single repaint** against a 1 m median step. The poller is now scoped to the Chart tab it belongs to.
- **New: `scripts/verify_replay.py`, a playback check that actually plays.** The test suite cannot cover continuous playback — it runs on `requestAnimationFrame`, which browsers do not fire while a page is hidden, so a headless page load proves only that the viewer loaded. Playwright's Chromium reports itself visible and does fire the animation clock, so the script drives the real viewer: it seeks into the race, presses play, and watches every repaint.
- It detects the fault two ways. The deterministic one records **who** repainted the replay's map, and fails on any repaint from a caller passing `document`. The second measures each boat's step between repaints and fails on a teleport. Sampling on a timer would have missed all of this: the wrong position lasted a single frame (~16 ms) before being corrected, so anything slower than the frame rate walks straight past it — hence hooking the repaint itself.
- Verified by putting the bug back: the script reports 2 foreign repaints and 2.6 km jumps with it, and 0 foreign repaints with a largest step of 14 m without it.

## v0.199

- **The race replay viewer.** With GPS tracking on, the public race page gains a **Replay** tab: the course chart with the fleet on it, a timeline under the map, and the order on the water beside it. Drag the scrubber or press play at 1x / 4x / 16x / 60x; each boat trails the last ten minutes of its track, and clicking a boat highlights it and dims the rest. **End** jumps to the last recorded fix — for a race in progress, that is the live picture, so the same tab serves both.
- The whole race arrives in **one fetch**, so scrubbing is instant rather than a request per frame. Positions are interpolated between fixes in the browser, which is what makes 10-second reporting look like motion — and the page says so, because the line between two fixes is drawn rather than observed. The **order on the water comes from the server**, so the replay's marks-rounded and distance-to-go are the app's own course walk and cannot disagree with the live view.
- The Replay tab **stays after a race finishes**, unlike the live tracking columns on the Entries tab — replaying is mostly something you do afterwards.
- **Fixed the replay window running past the end of the track.** For a race with no recorded finishes it ended at *now*, so a race that stopped reporting an hour earlier replayed an hour of boats sitting still with their trails aged out of view. It now ends at the last fix anyone reported. Found by watching the trails vanish in the viewer, not by a test.
- Playback clamps how far one frame may advance the clock. Browsers stop firing `requestAnimationFrame` in a background tab, so without it the first frame after switching back would carry the whole time away and jump the replay to the end.
- 24 tests across the two steps. One existing test needed narrowing rather than the product changing: it asserted the GPS columns vanish from the *whole page* after a race, and the replay's leaderboard legitimately uses the same headings.

## v0.198

- **A race can now be rewound.** Groundwork for a replay viewer: `/public/race/<id>/track` (and `/api/race/<id>/track`) returns a whole race's recorded fixes in one response — each entered boat with compact `[t, lat, lon, sog, cog]` rows, from the **warning signal** to a couple of minutes after the last finish. A real 199-minute race with one tracker is 88 KB, so the viewer can hold the lot and scrub locally instead of asking the server per frame.
- **`?at=<epoch>` on the positions endpoint gives the fleet as it stood at that moment** — positions, marks rounded, next mark, distance to go and the order on the water. This needed no second implementation of any of it: course progress was already computed by walking a boat's list of fixes, so bounding that list to a moment reuses the same mark-rounding and interpolated-finish code the live view uses, and the two cannot drift apart.
- Two things that do have to follow the clock: a boat only reads **FINISHED** if it had actually finished by then (otherwise everyone is finished from the first frame of the replay), and **last-fix age** is measured from the replay clock rather than from now (otherwise every boat reads hours stale the moment you scrub back). Both are covered by tests.
- Verified end to end against a real recorded race and against a simulated fleet driven through Traccar. 16 new tests; the live view is unchanged when no `at` is given.

## v0.197

- **Corrected the relay guide's architecture diagram.** It drew the relay talking straight to the hut PC and the hut camera, which is not what happens: Caddy reverse-proxies to `hut-origin` over Cloudflare for the app, and cloudflared bridges the camera over Cloudflare Access — both go out to Cloudflare and come back down the *hut PC's own* tunnel. That is exactly why the hut needs no open port and the camera is never exposed, and the diagram was quietly saying otherwise. There is now a second Cloudflare layer between the relay and the hut, with the camera shown behind the hut PC on the hut LAN.
- **The boat trackers are the one direct link**, drawn in gold and labelled as such: they open a raw TCP socket straight to the relay because there is no tunnel client on a tracker to carry it, and that single link is the only inbound access anywhere in the system. Chapter 1 now says this in words as well as in the picture.

## v0.196

- **Fixed the wrong tracker port in every guide.** They said to point the Queclink GL521MGs at **5027/tcp**, calling it “the Queclink protocol port”. It is not: 5027 is Traccar's **Teltonika** listener — the port the club's RUTX50 test device happens to use, which is how it got written down. Queclink GL-series trackers speak the `gl200` protocol on **5004/tcp** (the port is in the address of Traccar's own protocol reference, `traccar.org/protocol/5004-gl200/`). The failure this would have caused is the silent kind: a GL521MG pointed at 5027 reaches the Teltonika decoder, which cannot parse @Track messages, so the tracker reports happily, nothing appears in Traccar, and nothing anywhere logs why. The guides now carry a device/protocol/port table and say to open one port per protocol actually in use — 5004 for the boat trackers, 5027 for the RUTX50.
- **The relay is documented as a VPS**, with a home Proxmox container as the way to start out rather than the assumed setup. A VPS has its own public IP, so the trackers reach it directly and the club's public face does not ride on a home broadband line. Only the firewall step differs (provider firewall plus `ufw`, instead of a router port-forward) and it is written out for both; `setup.sh`, the systemd units and the Caddyfile are identical either way.
- Retired the old advice to “move the relay to a VPS if an open inbound port is unacceptable” — the port is needed either way; what changes is whose router it sits on.

## v0.195

- **New: the Relay & Front Door Guide** (`docs/Pwllheli_Relay_Guide.pdf`, 17 pages, also on the app's **Documentation** page). The relay started as a box that streamed the hut camera and quietly became the piece everything depends on — it is the club's single public front door, it runs the Traccar server behind the live race map and the automatic finishes, and it is the one place every visitor is logged. That was documented only as a README in `deploy/`, filed under "live camera". It now has a proper guide: what the relay is and does, the hostnames, building it, Cloudflare and caching, the camera stream, GPS tracking, access logging, day-to-day operation, troubleshooting and a file reference — opening with a drawn architecture diagram of the whole system.
- The guide is explicit about the **trade-off**: folding the old separate front-door PC into the relay means one machine now carries the whole public face of the club. If the hut is down, visitors get a holding page and the camera still works; if the **relay** is down, `pro.pwllhelisailingclub.org` is unreachable altogether. Racing is never blocked (the app runs on the hut PC), but the public view and tracking stop.
- It also records two things that were nowhere: which relay files are **copies** of repository files that do not update themselves (an old `site/index.html` is why camera panels can sit on stills for ever), and that **Traccar's own database is not in the app's backups** — `/opt/traccar/data/` needs backing up separately if you use tracking.
- `deploy/live_stream/README.md` is retitled to match what it now covers and points at the PDF; it stays the working copy next to the config files.
- The Documentation page had **no tests**. It now has six, including one that fails if a listed guide is missing, unbuilt or not actually a PDF — previously a renamed file would only surface as a 404 when somebody clicked it.

## v0.194

- **The trackers are Queclink GL521MG, not GL530MG.** Every reference is corrected, and the model-specific advice now matches the device we are actually using: the GL521MG is a battery asset tracker built for up-to-a-year standby, so its default reporting is far too coarse for finish order — and a race-day rate fast enough to time a line crossing costs a large multiple of the standby drain, so the units need **charging between race days** (they charge wirelessly, Qi).
- **All three PDF guides rebuilt** with fresh screenshots and the missing two years of features. Every cover said *"Covers app version 0.164"* — 29 releases out of date — and nothing in them described push ingest, the tracker pick-list, or the two new backup sections. The Reference Manual gains a *Push or poll* section, the Push ingest token and its status line, *Trackers seen on the network*, the three-database backup, and a complete environment-variable table (it was missing every GPS, hut-power and cookie variable). 47 of the 49 embedded screenshots were re-captured.
- **Fixed the backup page under-reporting itself.** v0.192 added GPS tracks and Hut power history as backup sections, but the hand-written *"What each option contains"* table below still listed only the original five — so the page backed up seven things while telling you it backed up five. Caught by looking at a rebuilt manual screenshot; a test now fails if a section is backed up without the table naming its files.
- **Fixed two screenshot scripts driving the live app.** `capture_entries_tab.py` and `capture_pursuit_screens.py` hard-coded `localhost:5050` and ignored `RO_CAP_BASE`, so they created and deleted a race in the *real* database instead of the temporary capture instance; `capture_power_screens.py` pointed at a port nothing runs on. All three honour `RO_CAP_BASE` now.
- **Documentation accuracy pass over all 21 guides.** The public hostname was misspelled `pwllhelisailgclub.org` in 10 places across three guides (a dead hostname if copied); the Windows guides still used a `v0_95` folder in 9 examples; the Cloudflare guide still described a *Show live camera* checkbox removed in v0.180 and a `/public/mobile/...` address retired in v0.178, and claimed the hut app "only ever makes outbound calls" without mentioning the push endpoint. Also fixed: a malformed heading that swallowed a paragraph, the removed one-off/manual entry route still being listed in the README, and tab/feature names left over from earlier releases.
- **Documented what was never written down**: hut power settings (a whole Settings section with no coverage), the VOX wake-up tone and the two speech rates, the wind dial and the finished-race wind record, and the fact that signing in over the club network on plain http silently fails because session cookies are `Secure`.

## v0.193

- **Settings now says whether Traccar is actually pushing fixes.** A **Push ingest token** that does not match the relay's `forward.header` fails completely silently: the app carries on polling, every tracker reports, the status box stays green, and the only symptom is that the automatic horn is a poll interval late. The counters to detect that have been collected since v0.189 and returned by `/api/track/status`, but nothing ever rendered them, so there was no way to tell a working forwarder from one that had never connected. The **Settings → GPS tracking** status box now carries a **Push:** line reading *"working — 1,284 fixes received, last just now"*, *"configured, but no fix has arrived this way yet — check the relay's `forward.header` matches this token"*, or *"not configured — positions arrive on the next poll"*.
- Corrected `docs/TRACKING.md` and `deploy/live_stream/traccar-forward.xml`, which both told you to watch a Settings status for push that did not exist, and added a **"Is Traccar actually pushing fixes?"** entry to the troubleshooting guide. Counts are since the app last started, so they reset on a restart.

## v0.192

- **Backups were missing the GPS track history.** The app keeps race data in three SQLite files, not one, and only `race_officer.db` was ever in a backup. `data/track_positions.db` — every recorded tracker fix, the race map history, replays and the evidence behind GPS finishes — and `data/power_history.db` were both absent, so a hut PC rebuilt from a backup would have come back with the tracks silently gone. Both are now sections on the **Backup / restore** page, ticked by default, and restore puts each back where its module looks for it and reopens it at the current schema. Verified against the real data folder: a backup now carries 10,129 positions alongside the race database.
- Every database is copied through SQLite's **online backup API** rather than as a file, so a backup taken while the tracker poller and the power monitor are writing cannot catch a torn transaction — nothing has to be stopped first. A database that does not exist yet (a hut that never used tracking) is reported as nothing to back up rather than an error.
- **Fixed every backup ZIP being left behind in `runtime/`.** A backup is built as a temporary archive and streamed to the browser, then deleted from the response's close callback — except `send_file` sets `direct_passthrough`, so the WSGI server is handed the file wrapper and `Response.close()`, where those callbacks run, is never called. The cleanup had therefore never once run: **218 archives, 316 MB**, had collected on this PC over four weeks. The cleanup now rides the WSGI iterable so the server's close reaches it, and each new backup also sweeps anything older than six hours in case a download was interrupted.
- The backup/restore system had **no tests at all**; it now has fourteen, covering what a backup must contain, a full round trip for each database, the "never used tracking" case, and the temporary-archive cleanup. The test suite also stopped writing backup archives into the developer's real `runtime/` folder and power samples into the real `power_history.db` — the same class of leak fixed for the track database in v0.189.

## v0.191

- **Fixed a tracker you switch on never appearing on the Trackers page.** Traccar only returns devices linked to the API token's own user, and a device it registers by itself — which is exactly what a newly switched-on tracker creates — belongs to nobody. It is the same thing Traccar's own web UI does: the device is hidden until you turn on the *All Devices* switch. The pick-list now asks for unowned devices too.
- **Adopting such a device now claims it for the app's Traccar user.** Ownership is not cosmetic: an unowned device's positions are not returned to our token at all, so the poller and the back-fill would never see that boat even after it was assigned to one (push would still work, since forwarding ignores permissions). Verified with a phone running Traccar Client: before adopting, `/api/positions` returned one device; after, it returned the phone too. Where Traccar has several accounts the app cannot tell which one the token belongs to — it says so plainly and leaves the linking to you rather than guessing.

## v0.190

- **Trackers can be picked off a list instead of typing an IMEI.** The Trackers page gains **Trackers seen on the network**: devices Traccar has heard from that are not set up here yet, most recently heard from first with a last-reported indicator, each with a boat dropdown and an **Add** button. With automatic registration enabled on the relay (`database.registerUnknown`), switching a tracker on is enough to make it appear — nobody has to read a 15-digit number off a device. Adopting one links it to the Traccar device that already exists rather than creating a second, and the page notices new arrivals between refreshes and says so. Adding by IMEI is still there, for setting a tracker up before it is switched on.
- **Fixed the back-fill recovering almost nothing after an outage.** The poll stores each device's latest fix *before* the back-fill ran, so the gap it measured was a few seconds rather than the length of the outage, and the missed fixes were never fetched. Caught by testing against the real Traccar: a 100-second app outage recovered 3 fixes; it now recovers 86. The poller passes the pre-poll snapshot, so the gap is measured from where the track actually stopped.

## v0.189

- **Positions can now be pushed to the app instead of polled for.** Traccar's position forwarder POSTs each fix to a new `/api/track/ingest` endpoint the moment it decodes it, and **finish detection runs on receipt** rather than on the next poll. Measured on the bench at **0.02–0.32 s** from fix to accepted, against 0–5 s of poll wait. This does not change a recorded finish *time* — crossings are interpolated between fix timestamps, so those were always right — but it removes the app's share of the delay in the things you can see: the **automatic horn**, and the positions competitors are shown. It matters more the faster the trackers report; at one fix a second near the line a 5-second poll was the bottleneck.
- Push is off until a **Push ingest token** is set (Settings → GPS tracking) *and* the same value is set as `forward.header` on the relay. Without a token the endpoint stays closed (503); with one, anything lacking it is rejected (401). The endpoint is exempt from CSRF because Traccar has no session — the shared secret is the gate.
- Deliberately **not** Traccar's "forwarder-only" mode: Traccar keeps its database, so it keeps the device registry (needed to send a tracker commands, e.g. raising its reporting rate near the line), the history the app back-fills from, and its web UI. Relay settings to apply are documented in `deploy/live_stream/traccar-forward.xml` — nothing on the relay changes until you apply them.
- **The poller now back-fills gaps.** It asks Traccar only for `/api/positions`, which returns each device's *latest* fix, so an app outage left a permanent hole in the track. It now notices a gap on an assigned tracker and fetches the missing range from Traccar's history (`?deviceId=&from=&to=`), bounded to 6 hours and skipped for gaps under 30 seconds. With push doing the live work you can raise the poll interval to about 30 seconds; the poller stays as the safety net.
- **Fixed a tracker with a wrong clock shutting itself out permanently.** The live path only accepts fixes newer than the newest stored for that device, so a single fix dated in the future silently dropped every real fix from that tracker afterwards. Fixes dated more than 5 minutes ahead are now refused, and an already-stored future timestamp no longer blocks new ones. Found while push testing, where a stale simulated fix 48 minutes ahead made one boat invisible.
- **Fixed the test suite writing into the developer's real GPS track database.** v0.184 stopped tests touching the main database, but positions live in their own file whose path is bound at import — so any test that did not redirect it stored simulated boats in the real `track_positions.db`. Every test now gets a temp one, and a guard fails a test that opens either real database instead of silently polluting it.
- `scripts/simulate_trackers.py` gains the modes needed to test all of this without hardware: **`--forward-to`** posts Traccar-shaped forwarder JSON straight at the app (no Traccar needed), **`--fast-near-line`** reports faster within a set distance of the line — the dynamic reporting rate a real tracker would use — and **`--report-latency`** prints fix-to-accepted times and a summary. A test pins the payload the script sends against the app's parser so the two cannot drift apart.

## v0.188

- **The Live camera tab now fills the width of the screen on a phone.** The video was boxed in with a heading, a two-line description and a Watch-live button crowded around it. The header is now a single compact line, the panel drops its surrounding padding and border so the 16:9 video sits nearly edge-to-edge (about 94% of a phone's width, up from ~77%), and the separate **Watch live** button is gone — the panel itself is the live view now that it plays inline. The race page keeps its footer Watch-live link. Nothing changes on a tablet or PC.

## v0.187

- **Fixed the live stream sometimes loading but never starting** — a player sitting paused at 0:00 with a healthy stream behind it, on the relay's own `/live` page as well as in the app's camera panels. The watch page tried the browser's built-in HLS support first and only used **hls.js** if that was unavailable. Chrome answers *"maybe"* when asked whether it can play an HLS playlist on some builds, then fails to demux it (`DEMUXER_ERROR_COULD_NOT_PARSE`) — so on those machines the stream never played, while the same page worked fine elsewhere. hls.js is now tried first, and the browser's own HLS is the fallback for Safari and iOS, where hls.js reports itself unsupported.
- The page also now asks for playback whenever new data arrives, watches for a loaded-but-paused player, and offers **"Tap to start the live view"** if the browser refuses autoplay outright. The native-HLS path retries while the on-demand stream is still starting instead of giving up quietly.
- This is a **relay-side file**: copy `deploy/live_stream/site/index.html` to the relay's site root (`/opt/relay/site/`) for the fix to take effect. The app needs no other change.

## v0.186

- **Fixed the live camera never switching to video even once the relay allowed it.** Two causes, both mine:
  - The starting player was hidden with `display: none`. A browser will not start playing video in an element it is not rendering, so the player never began, never reported that it was playing, and the panel waited out its whole two minutes before falling back to still pictures. It now loads **rendered but transparent, behind the still picture**, and simply becomes visible when it takes over.
  - Waiting for a *"playing"* report was too strict. A frame that has said **"connecting"** has demonstrably loaded — only a refused frame stays silent — so it is now shown after about 25 seconds regardless, with the relay page's own connecting/buffering display doing the talking. A silent frame is still never shown, so a blocked relay policy still degrades to still pictures rather than a broken box.

## v0.185

- **Fixed the live camera giving up before the stream had started.** The relay only pulls the camera while somebody is watching, so the first viewer waits while MediaMTX connects and cuts the first segments — often longer than the 15 seconds v0.183 allowed, after which the panel settled on *"Live video is not available here"* and never switched, even with the relay's frame policy correctly updated.
- The panel now tells the two cases apart. The relay page posts **connecting** the moment it loads, which proves the browser did *not* refuse the frame: silence for 8 seconds still means blocked and falls back to stills, but once *connecting* is heard the panel waits patiently (up to 2 minutes) and shows **"Live stream starting — still pictures until it is ready…"** in the meantime.

## v0.184

- **Fixed the test suite overwriting the app's real settings.** Three tests in `tests/test_routes.py` (the public-branding FFmpeg filter tests) took no database fixture, so their `save_app_settings(...)` calls ran against the live `data/race_officer.db` instead of a temp copy. Because that helper rewrites the **whole** settings block from defaults, running `pytest` on a machine with a configured install silently reset every setting it was not given — that is what cleared the **Public live stream page** URL after v0.183 was built.
- `tests/conftest.py` now has an autouse guard that fails any test which opens the real database, naming the missing fixture. It immediately caught two more (`TestPositionsDB` in `tests/test_track.py`, which resolved the device→boat map against the live database) and showed that the **background daemons a request starts** — weather poller, track monitor, central audio, start-sequence scheduler — outlive the test that started them and then poll, write samples into and run finish detection against the real database. Their loop bodies are now no-ops in tests, the way the R2 uploader already was. (The power monitor's loop still runs: `/api/power/status` only reports its full shape once it has, and it writes to its own `power_history.db`.)
- The app-settings cache is disabled during tests. Tests that write settings with direct SQL cannot invalidate that one-second cache, so assertions on the values read back passed or failed depending on how fast the previous test ran — one PTZ test failed only in full-suite runs.
- No application code changed. If you have run the test suite on a configured machine, check **Settings** — anything you had customised (weather station URL, video/PTZ/R2, branding, audio rates, chart tile URLs, the live-stream page) may have been reset to defaults.

## v0.183

- **Fixed the live camera panels showing a "refused to connect" box** instead of the stream when the app is opened directly on a PC (`http://localhost:5050` — a test server, or the hut machine's own race sheet). Two halves to it:
  - The relay decides who may embed its watch page, and its `frame-ancestors` policy listed only `pro.pwllhelisailingclub.org` and `*.pwllhelisailingclub.org`. `deploy/live_stream/Caddyfile` now also lists `http://localhost:5050` and `http://127.0.0.1:5050`. **Copy the Caddyfile to the relay and `systemctl reload caddy`** to get live video on a locally-opened app; competitors reaching the app through `pro.…` were never affected.
  - The panel no longer reveals the player on a timer. It waits for the relay page to report that it is **playing**, and if nothing is reported within the grace period it removes the frame, stays on the still pictures and says *"Live video is not available here — showing still pictures."* A browser that refuses to load the frame reports nothing, which is exactly the case that used to leave a broken box on the page.

## v0.182

- **The live camera panels now show the relay's live stream, not just snapshots.** Both the competitor home page's **Live camera** tab and the race sheet's **Show live finish camera** panel start with the refreshing still pictures they have always shown, then **switch to live video** once the relay's stream is actually playing. The relay's stream is on demand — MediaMTX only pulls the camera while somebody is watching, so the first viewer waits a few seconds while it spins up; the stills cover exactly that gap instead of a black player. Snapshot refreshing stops when the video takes over, and leaving the tab (or closing the panel, or backgrounding the browser tab) removes the player so the relay can drop the stream again.
- The embedded player is the relay's **own watch page**, so there is one player and one copy of hls.js to keep working rather than two. That page now posts its state to the page embedding it — `{source: "psc-live", state: "connecting" | "playing" | "error"}` — which is what cues the switch. **Copy `deploy/live_stream/site/index.html` to the relay** to get the prompt switch; an older relay build that posts nothing still works, the panels just switch on a timer.
- Nothing changes when no live-stream URL is configured (Settings → Video Recording → *Public live stream page*): both panels behave exactly as before, snapshots only. The public **Watch live** button still opens the stream full-page.
- **On the race sheet, treat the video as a view, not a clock.** It reaches you hut → relay → Cloudflare → back, so it is a few seconds behind the water — the panel says so — and finish times should come from the horn and the recorded finish clips, which are cut from the hut's own recording. It also uses the hut's connection in both directions, so on 4G leave the panel closed when it is not in use.
- Internals: new `static/live_camera.js` owns both panels (snapshot loop, stream swap, teardown), replacing the two separate snapshot tickers; `public_live_stream_url` moved into the template context processor so any page can offer the stream; the snapshot refresh interval moved from the `<img>` onto the panel container.

## v0.181

- **The competitor home page now shows the wind instrument and the countdown in its race-information header.** The dial is the same analog TWD/TWS instrument the race officer sees on the dashboard — identical markup and script, now shared rather than duplicated — and it refreshes itself every 5 seconds. Beside it, a countdown to the current race's first start reads the same way as the race page (*Counting down* / *Race started* / *Race finished*), so a competitor can leave the page open on a phone and see the wind and the time to the start without opening a tab or a race page. With no current race, no countdown is shown.
- The public dial deliberately does **not** carry the weather-station status line: raw station messages stay off the public pages, and with no wind available the dial simply reads `—°` / `— kt`. The **Wind** tab still has the readings, the station message and the history plot.
- Internals: `static/dashboard_wind.js` becomes `static/wind_gauge.js` and drives every element with class `.wind-gauge`, addressing needles and readouts by class so more than one dial can appear on a page; the dial markup moves to `templates/partials/wind_gauge.html`, shared by the dashboard and the public home page. The drop-shadow filter id is now per-gauge so two dials cannot collide.

## v0.180

- **The public race page keeps its tabs after the race and gains a "Leader board" tab** for the results, instead of the results replacing everything. The leader board appears only once every boat has stopped racing, and then opens first; **Entries**, **Chart** and **Course analysis** stay available as a record of the race. (It is called *Leader board* rather than *Results* so a live handicap-corrected order during the race can live there later.)
- **A finished race's Chart and Course analysis now show the conditions that were sailed in**: the **average wind over the race**, measured at the start hut from the first warning signal to the last boat finishing, instead of the wind at the moment you happen to look. TWD is vector-averaged (a plain mean is wrong across north — 350° and 010° average to 0°, not 180°), TWS shows its mean with the range it varied over, and the gust figure is the highest seen. The chart's wind arrow uses the same averaged direction, and nothing on those tabs polls any more. The live GPS tracking columns drop off the Entries tab at the same time, since nothing updates them once the boats are home.
- **The competitor home page is now three tabs — Races, Wind and Live camera.**
  - **Races** lists **every race in the current year**, grouped into series. Each series is a rolled-up section with its race count; the **current series is open** and the **current race is highlighted**. Races not in a series are collected into a final group. Previously the list only ever showed the current series.
  - **Wind** gets the page to itself, so the wind-history plot is now **560 px tall** (420 px on a phone) instead of 320 px.
  - **Live camera** starts as soon as the tab is opened — the **Show live camera** checkbox is gone — and stops when the viewer leaves the tab or backgrounds the page, so an idle viewer still costs the hut no camera traffic.
- Wind samples inside a race window were already exempt from the 24-hour history purge, so an old race's averages remain available months later.
- Documentation: the tabs and the conditions record are described in `PUBLIC_COMPETITOR_PAGE.md`, the Competitor's Guide (with a new Wind-tab figure and refreshed home/results screenshots), the Race Officer's Guide and the Reference Manual. The Race Officer's Guide no longer claims GPS positions are hidden from competitors (they have been on the public race page since v0.177).

## v0.179

- **The public race page is now split into three tabs** below the race header — **Entries** (**Start times** for a pursuit race), **Chart** and **Course analysis**. The race name, signal times, flag panel and countdown stay visible above the tabs, so a competitor sees the important information without scrolling: on a phone the page is now less than half as tall (3962 px → 1916 px). The chosen tab is remembered in the page address, and the course chart is re-drawn when its tab is opened so it always fills the panel. Once every boat has finished, the results replace the tabs as before.
- Documentation: the tabs are described in `PUBLIC_COMPETITOR_PAGE.md`, the Competitor's Guide (with new phone screenshots of the Entries and Chart tabs) and the Reference Manual; the Reference Manual's GPS chapter no longer claims positions are race-office only (they have been on the public race page since v0.177).

## v0.178

- **Boats now show PRESTART before the warning signal**, changing to RACING once the race's first warning-signal time is reached. This is a display label — the stored status stays RACING throughout, so elapsed times, results and GPS finish detection are unchanged. Other statuses (FINISHED, DNF, OCS …) are shown as before.
- **The public pages now work properly on a phone.** On a narrow screen the wide tables (entries, results, leg analysis, pursuit start times, the race list) become **one card per boat/leg** with the column heading beside each value — nothing is cut off and the page no longer scrolls sideways (it previously overflowed by more than 200 px). Text sizes were raised for reading at arm's length, and the course chart, countdown and wind chips size to the screen. Tablets and PCs keep the familiar tables.
- **One responsive public page** replaces the separate mobile variant: the desktop/mobile toggle is gone, and the old `/public/mobile/...` addresses redirect to it so links already shared with competitors keep working.

## v0.177

- **"Live leaderboard" is now "Position on the water"**, making clear it is the order on the water and takes no account of each boat's handicap.
- **Every boat entered in the race is listed**, not just the tracked ones. A boat with no tracker assigned shows dashes with **No tracker** in the Fix column, and sorts after the tracked boats.
- **Competitors can now follow the fleet.** With GPS tracking enabled, the **public race page** draws each tracked boat on its course chart and adds **Marks / Next / To go / SOG / Fix** columns to the Entries table, refreshing every few seconds. Nothing is shown when tracking is switched off, so positions stay private simply by leaving it disabled. Tracker management remains inside the race office.
- Documentation: a new **Following the fleet (GPS tracking)** chapter in the Competitor's Guide, plus updates to the Reference Manual, Race Officer's Guide, `TRACKING.md` and `PUBLIC_COMPETITOR_PAGE.md`.

## v0.176

- **Fixed tracked boats not appearing on the race-sheet course chart** before the start, or in the first minutes after it. The map was only fed positions recorded *after* the race start (a regression introduced in v0.170), so a boat that had not yet sent a post-start fix disappeared from the chart and showed blank speed/fix columns. The map and the leaderboard's speed/last-fix columns now always use each boat's **latest known position**, while course progress (marks rounded, finishes) is still measured from the start as before. The leaderboard also shows a useful distance-to-go before the start.

## v0.175

- **Bigger maps.** The course maps are now up to 1200 px square (about 2.3× wider than before) instead of 520 px.
- **The Trackers map now zooms to show every tracker**, wherever they are — not just the race area — so a tracker miles from Pwllheli is still visible. It only re-zooms when a tracker would otherwise be off-screen, so a map you have panned or zoomed is left alone.
- **Fixed the Trackers map showing removed trackers.** Position history is deliberately kept when a tracker is deleted (so a boat keeps its past track), but the map was drawing those old fixes; it now shows only the trackers currently configured.
- The tracker map moved **below** the "Assign trackers to boats" and "Add a tracker" sections.
- Documented that **live Traccar data takes precedence over the simulator**: with a working connection configured, *Simulate boats* has no effect (the simulator is used when no connection is set, or as a fallback when Traccar is unreachable).

## v0.174

- **A map on the Trackers page** shows the live position of every reporting tracker over the club marks and start/finish line, refreshing with the rest of the page.
- **The course maps are now square** (a centred square instead of a wide rectangle) on the race sheet, competitor pages, course recommendation, course builder and the new Trackers map.

## v0.173

- **Documentation refresh for GPS tracking.** All three PDF guides and the markdown docs are brought up to date for the tracking feature: the Reference Manual and Race Officer's Guide gain a **GPS tracking and automated finishes** chapter (with new screenshots of the Trackers page, the live course-chart fleet and the leaderboard, and the GPS-tracking settings), and the README, Settings/admin, installation (env vars), race-officer workflow, operational checklist and troubleshooting docs now cover trackers, the live map/leaderboard and GPS finishes. No application code changes.

## v0.172

- **The Trackers page now updates live** (no page refresh). Each tracker's last-reported RAG dot and "x min ago" time, and the reporting summary, refresh every ~15 seconds from a new `/api/trackers/status` endpoint — so you can watch trackers come online on race morning without reloading.

## v0.171

- **Trackers page now shows when each tracker last reported.** Each tracker has a **last-reported** column with a RAG dot — **green** if it reported within 5 minutes, **amber** within the last hour, **red** after an hour (grey "never" if it has never reported) — plus a human "x min ago" time. The summary count now means "reported recently": *N of M trackers reporting (a fix within the last hour)*, rather than counting any device Traccar has ever heard from. The 5-minute / 1-hour thresholds also apply to the status shown in Settings.
- The relay `setup.sh` now installs Traccar automatically (its self-contained installer), so a fresh relay build is fully turnkey.

## v0.170

- **New Trackers page + boat-linked tracks.** Tracker management moves out of Settings to a dedicated **Trackers** item in the main menu (between Documentation and Settings), available to **race officers** — not just admins. From it you can add a tracker, assign/re-assign it to a boat, and remove it.
  - **The app can now create and delete the Traccar devices for you** via Traccar's API: add a tracker by its IMEI on the Trackers page and it's registered in Traccar automatically (removing it deletes it in Traccar too) — so all setup is done in the app. (The Traccar connection/token still lives in Settings; the token needs device-management rights.)
  - **A boat's track now stays with the boat.** Each stored position is tagged with the boat its tracker was on at the time, so if you change a boat's tracker the old track stays with the old boat and the new fixes attach to the new one. Removing a tracker keeps the boats' past tracks.
  - The per-race **loaner** tracker override (on the race sheet) is unchanged.

## v0.169

- **GPS finishes now respect a shortened course.** When the race officer shortens the course, GPS progress and finish detection follow the shortened course: boats need only round up to the shorten mark and then cross the finish line, and the live leaderboard's marks-total and distance-to-go reflect the shortened course. Previously the detector still expected every original mark to be rounded, so boats on a shortened course would never GPS-finish.

## v0.168

- **GPS finishes now follow the whole course, and a live leaderboard.** The finish detector tracks each boat's rounding of the course marks in order and only records a finish once **every** mark has been rounded and the boat then crosses the line. This fixes early finishes on the many courses that use the ODM as a mid-course mark (e.g. `O-F-O-1-O` finished a boat the first time it passed O; now it finishes on the final crossing).
  - The same progress powers a **live leaderboard** on the race sheet's *Course & start* tab: boats are ordered by how far round the course they are, showing marks rounded (e.g. `6/10`), the next mark, and the distance still to sail (plus speed and last-fix age).
  - A **mark-rounding radius** setting (Settings → GPS tracking, default 50 m) controls how close a boat must pass a mark to count it as rounded.
- Docs: `docs/TRACKING.md` updated for course progress + the leaderboard.

## v0.167

- **GPS tracking connectivity fixes** (found while bringing up the first relay/Traccar):
  - The hut app now sends a normal browser **User-Agent** on its Traccar requests, so Cloudflare's bot/WAF no longer rejects the position pull with a 403 (the Traccar base URL is behind Cloudflare — the same gotcha the live-stream branding fetch hit).
  - If the **Traccar base URL or API token** contains a non-ASCII character (e.g. a "smart" em-dash pasted in from a document), Settings now shows a clear message naming the field instead of a cryptic `latin-1 codec` error.
- New developer/test tool `scripts/simulate_trackers.py` feeds **Traccar** with simulated boats sailing a real course (OsmAnd protocol), to exercise the whole GPS chain end to end without hardware (not part of the app download).
- Known limitation being addressed next: GPS **auto-finish** does not yet track the rounding of each mark, so on courses that use the ODM as a mid-course mark it can propose a finish early (on the first pass of the line). Mark-by-mark course progress — which also enables a live leaderboard — is the next update.

## v0.166

- **Yacht tracking and automated finishes (race-office only, opt-in).** Boats carrying Queclink GL521MG GPS trackers now appear live on the race course chart, and the app can detect finish-line crossings.
  - Positions come from a **Traccar** server on the relay; the hut app pulls them outbound (no inbound port on the hut). A built-in **simulator** (Settings → GPS tracking → *Simulate boats*) previews the whole feature without any hardware.
  - **Live fleet map:** the *Course & start* tab shows tracked boats on the chart (heading + sail number) and a fleet list (speed, heading, last-fix age; stale after 60 s).
  - **Trackers → boats:** assign a tracker to a boat permanently in Settings, or override it with a loaner tracker per race on the entries tab.
  - **GPS finishes:** arm a race for auto-finish and each detected crossing is **proposed** for the race officer to confirm (with a link to the finish video), tagged `gps-confirmed`. An optional **auto-confirm (unmanned)** mode records the finish immediately (tagged `gps-auto`); every GPS finish stays reviewable and editable. The start and finish lines are the same at CHPSC, so a finish is told apart from the start by crossing direction plus a post-start guard time.
  - Setup: relay/Traccar guide in `deploy/live_stream/README.md` (§7); race-officer guide in the new `docs/TRACKING.md`.

## v0.165

- Fixed the public **"Show live camera"** image freezing on a stale frame — the rotating sponsor logos kept changing over a frozen camera picture — until the app was restarted. The hut's live-preview FFmpeg can keep running but stop producing new frames if its camera connection stalls, which is most likely once the live-stream relay is also pulling the camera. The app now detects a preview that has stopped writing fresh frames and restarts it automatically (within about 30 seconds), so the live image recovers on its own. Evidence recording is unaffected.

## v0.164

- **Security hardening for internet exposure:** session cookies are now `HttpOnly` + `SameSite=Lax` + `Secure` (set `RO_COOKIE_SECURE=0` for local http testing); baseline security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and HSTS on HTTPS); a request-body size cap (`RO_MAX_UPLOAD_MB`, default 64 MB); and login brute-force rate-limiting per client IP + username. A full pre-launch security review found no critical/high code flaws.
- Fixed an **Internal Server Error** on Settings → "Save and test selected weather source" (a pre-existing missing import).
- Tidied the **Public video and live image publishing** settings section: the start-line overlay checkbox moved onto its own line below the field grid and its help text trimmed.
- **Documentation refresh for release:** re-captured the out-of-date PDF screenshots (race-sheet tabs now show the **Shorten course** tab, the **Marks** page shows Add/Delete, the video-settings section reflects the new layout) and added a **Shorten course** screenshot to the Reference Manual and Race Officer's Guide.
- Internal: the ~174 routes in `app.py` were split into a `routes/` package (no behaviour change; `app.py` roughly halved in size).

## v0.163

- Fixed the **Code flag S** (shortened course) rendering as a plain white box in the flag panel. The flag has its own CSS now (drawn from the real flag image like the preparatory **P** flag), so it shows correctly as a white flag with a centred blue square. The flag image `static/img/class_flags/s.png` was also redrawn as a true colour-inverse of the P flag so its proportions and size match the other signal flags.
- Documentation: added a **Shortening the course** chapter to the Race Officer's Guide (new Chapter 7) and a shortened-course note to the Competitor Guide (what the Code flag S and *Shortened course* banner mean on the public page). The Reference Manual already covered it.

## v0.162

- Refined the **Shorten course** feature: (1) International Code flag **S** (blue square on white) is now shown in the flag panel on the race sheet and the public competitor page while the course is shortened, until all boats are no longer racing; (2) the spoken shortened-course announcement now starts **after** the two horn blasts finish, so it is no longer drowned out by the horn (and is still repeated a few seconds later); (3) the course chart now draws the final leg from the shorten mark **directly to the finish line** (a dashed "proceed to finish" line), instead of appearing to stop at the mark.

## v0.161

- Added a **Shorten course** tab to the race sheet (between *Start console & log* and *Entries & finish times*). Pick a mark on the course and press **Call shortened course**: the app sounds **two horn blasts** and announces over the central audio — "Shortened course called on mark <mark> — after this mark proceed to finish" — repeated a few seconds later. It records the call in the race log, shows a **Shortened course** banner on the race and public competitor pages, and truncates the shown course and predicted-time/leg analysis to end at that mark (with "→ Finish" after it). Boats round the mark then sail directly to the finish; finish times are recorded as usual. **Clear shortened course** undoes a call made in error.

## v0.160

- The **Class** column on the race entries lists (admin race page and the public race page) now shows the **rating-band class** (e.g. IRC0/IRC1/IRC2) computed from each boat's rating and the series' configured bands, instead of the boat's stored division. Previously boats kept showing their boat-database division (e.g. "Class 1"/"Class 2") even after rating bands were set on the series, which looked like the bands were being ignored. When a series has no rating-band classes configured, the boat's division is still shown, as before.

## v0.159

- All countdown timers now display in one consistent format — **D:HH:MM:SS**, dropping the days and hours segments while they are zero (so `6:14`, `14:45:00`, or `4:18:36:14`). Previously the race/start-console and dashboard countdowns showed raw minutes:seconds, so a start several days away read as e.g. `-6876:14`. A shared `static/countdown.js` formatter is now used by the race page, pursuit race page, start-times ladders, the dashboard and the public competitor page.

## v0.158

- The **Marks** page can now **add and remove marks** from the UI (administrators only). Add a simple mark by code, name, position in decimal degrees, buoy and top-mark — the position text is generated automatically. A mark can be deleted only when it is not in use: marks used in a fixed course or the start/finish line, and compound marks or their components, show "In use" (with the reason) instead of a Delete button. Race officers see the list read-only. Changes are written to `data/marks.json` and recorded in the activity log.

## v0.157

- Fixed a **"Not Found"** error when opening an admin URL with a trailing slash (e.g. `https://…/admin/`). Flask registers routes without a trailing slash, so `/admin/` didn't match and a signed-in user hit a bare 404. A trailing-slash URL now redirects to its slash-less form before 404ing (a genuinely missing page still returns a normal 404).

## v0.156

- Start-line video overlay: **one encode instead of two** (the line is now burned into the same transcode that makes the branded public copy, not a separate second pass) — roughly halves public-video processing time. Fixed a case where the overlay pass could run until timeout (the looped line image lacked `shortest=1`), which both wasted time and left the line missing. The detection outcome (line drawn, or why it was skipped, with confidence) is now written to the activity log so a missing line can be diagnosed.

## v0.155

- Added an **experimental start-line overlay for public start/finish videos** (Settings → Video Recording, off by default). It detects the orange ODM buoy and draws the actual start line from the pole base to the buoy on the **public web copy only** — the full-quality evidence clip is never touched. **Start** videos show the line red before the start signal and green after; **finish** videos show it green. Fully best-effort: if the buoy isn't confidently found (or the optional Pillow/numpy dependencies are missing) the video is published without the line. Tune detection in `data/startline_config.json`. Adds Pillow + numpy to the requirements.

## v0.154

- Fixed a **"Bad request: CSRF token missing or invalid"** error some users hit when signing in. The login page is now sent as non-cacheable (`Cache-Control: no-store`), so a browser or CDN (e.g. Cloudflare) can no longer serve a cached login page without the matching session cookie — the usual cause. If the security token still fails to validate on the login page, the user is now returned to a fresh login page with a clear message instead of a dead-end error, so they can simply sign in again.

## v0.153

- Added a **My account** page so any signed-in user — including race officers, for whom Settings is read-only — can change their **own password**. It is reached from the username in the top bar, requires the current password to confirm, and enforces a minimum length. Administrators still manage all users (and reset any password) from **Settings → Users**.

## v0.152

- Added a daily-rotating **activity log** (no UI) written to `runtime/logs/activity.log` on the race-office PC. It records a history of who did what: logins and failed logins, races created/updated/deleted, boats added to and removed from races, finishes and pursuit positions recorded, boat-database changes, settings saves and user-account changes. One file per day (the previous day rolls over to `activity.log.YYYY-MM-DD`). The log lives under `runtime/`, so it is not included in backups or release packages.

## v0.151

- Added an optional **VOX wake-up tone**: when enabled in **Settings → Horn settings & Test → Start automation and central audio**, a short tone is played a configurable number of seconds (default 2) before each spoken announcement, so a VHF's VOX is keyed up and the first words of the announcement are not clipped. Applies to all central-audio announcements (standard and pursuit).

## v0.150

- Pursuit races now **announce the next boat(s) to start** by name over the central audio: after the first boat's one-minute signal, each upcoming start is announced 10 seconds after the previous boat started. Boats starting within 20 seconds of each other are named in one announcement. These also appear in the start-console signal plan.
- Fixed the ten-second countdown running about a second late — it now starts one second earlier so "One" lands on the start signal.
- Published series HTML for a pursuit race no longer repeats the start-video list above the results table (the per-boat Start video column already covers it).

## v0.149

- Pursuit race page: the header flag box now shows just the flags (removed the extra text label).
- The public pursuit race page now shows the **finish signal time** in the header.
- Removed the redundant "Horn output" line from the pursuit Start console's manual controls.
- **Start video per boat**: the public pursuit results table and the admin pursuit Results tab now link each boat's start video (like the finish-video link on a standard race), and the separate video list under the admin results was removed.
- Published series HTML: for a pursuit race the per-boat video column now shows each boat's **start video** (a pursuit has a start video per boat and no finish video), with the column headed "Start video".

## v0.148

- Pursuit start console: removed the duplicate countdown (the always-visible header countdown remains).
- Pursuit competitor race page now shows the **course analysis** (course chart and leg-by-leg timing) as well as the start-times list.
- The pursuit start console's **horn and race log now updates live** (polls every few seconds) instead of only on a page refresh.

## v0.147

- Pursuit races: the race page and competitor page now show the **flag panel and countdown** like a standard race (the first start flies the class‑1 numeral pennant and P).
- The pursuit **Start console** now matches the normal race start console: a full scheduled **signal plan** with the RRS‑26 warning sequence and announcements for the first start, a **start signal for each boat's start time**, and the finish signal — each with its flags and horn, plus a live countdown and the horn/race log.
- Before a start time is set, the race and competitor pages now show each boat's **time offset from the first boat to start** (e.g. `+6:00`) instead of a blank, so the running order is visible while planning.
- Refreshed the PDF guides (covers 0.147).

## v0.146

- Pursuit races: fixed a misleading "no rating" label. Boats that have a rating but no start time yet (because the first warning time and fixed period are not set) now show "set start time" and a prompt, and only boats genuinely missing the chosen rating are flagged.
- Pursuit races now use the normal flag sequence for the first start, **defaulting to class 1** (the numeral‑1 pennant), so the competitor flag panel and start signals behave like a standard start.
- Pursuit races now offer the **same course-selection methods as standard races** on the Course & start tab: a fixed numbered course, **Recommend / change course** (wind-based), or **Build manual course** (made-up course), including clearing a made-up course.
- Refreshed the PDF guides; covers read *Covers app version 0.146*.

## v0.145

- Added a **pursuit race** type, selectable with the new **Race type** option on the New race page. A pursuit race runs for a fixed period with per-boat staggered starts: the slowest-rated boat starts first and each faster boat is delayed (by `period × (1 − slowest speed / boat speed)`) so, on handicap, all boats would finish together. Choose **IRC** or **YTC** as the rating system that drives the start times. There are no classes.
  - Each boat's start time is computed to the second and recomputes automatically when boats are added or removed; a boat with no rating in the chosen system is entered but flagged with no start time.
  - The **Start console** shows the start ladder with the next boat to start highlighted and a live countdown. Horns fire automatically: the five-minute warning sequence before the first start, one start signal at each boat's start time, then a single finish signal at the end of the period. There is a start video per start time and no finish video.
  - At the finish the race officer records the finishing order by the boats' position on the water (no time correction). The **competitor page** shows each boat's start time with the next to start highlighted, and the finishing order once recorded.
  - A pursuit race can belong to a series and scores into the standings by finishing position.
- Refreshed the bundled documentation and PDF guides for pursuit races; covers read *Covers app version 0.145*.
- New DB columns (auto-migrated): `races.race_type`, `races.pursuit_duration_min`, `entries.pursuit_position`.

## v0.144

- Added user **roles**. Every account is either **Admin** (full access) or **Race officer** (can run races and download a backup, but **Settings is read-only** and **restoring a backup is not allowed**). The restrictions are enforced on the server, not just hidden in the page: settings-write actions and backup restore are refused for race officers. The Users section now uses an Admin/Race officer dropdown, and the app refuses to demote, deactivate or delete the last active administrator so nobody can be locked out.
- Moved the **Settings** link into the bottom (non-race) group of the side menu, alongside Hut power, Documentation and Backup / restore.
- Settings page tidy-up: the **Save settings** bar is now pinned to the top of the page so it is always reachable; removed the redundant **Misc** section (its horn-wiring notes moved into the Horn section) and its duplicate Save button; reworked the **Weather Station** section so the two source options sit side by side with a clear selected state and only the selected source's fields are shown (also fixed the radio buttons rendering stretched full-width).
- Refreshed the bundled PDF guides for all of the above and re-captured the affected screenshots; covers now read *Covers app version 0.144*.
- No database schema changes — the `users.role` column already existed; roles are now enforced.

## v0.143

- Added an **Entries** list to the public per-race competitor page. Above the course analysis it shows every boat in the race — boat name, sail number, class, IRC and YTC rating and current status — using each entry's per-race rating snapshot. Like the course analysis, it is hidden once every competitor has finished, so the results table takes its place.
- Removed the **one-off / manual entry** feature. Boats are now added to a race only from the boat database (individually or by bulk-add); the standalone "type in a visiting boat" form and its route have been removed. (The internal series-copy path still carries any pre-existing manual entries across a series.)
- Refreshed the in-app documentation and did a full rebuild of the three bundled PDF guides. As well as the entries-list/one-off-entry changes above, this: adds a **Public live camera stream** section to the Reference Manual (the `public_live_stream_url` / Watch live setting, the `/api/branding/live` branding manifest, and the external `deploy/live_stream` relay); documents the **Watch live** link in the Competitor's Guide; fixes stale chapter cross-references left over from inserting the hut-power chapter; and re-captures the screenshots against the current build (dashboard hut-power card, the video settings including the new live-stream field, the Add entries tab, and the public race page). All three covers read *Covers app version 0.143*.
- No database schema changes.

## v0.142

- Packaging: the release ZIP now includes the `deploy/live_stream` relay setup (the external MediaMTX/Caddy/cloudflared relay that publishes the public live camera stream), alongside the existing `deploy/windows` scripts. No application code change from v0.141 — the relay runs on a separate machine (a Proxmox LXC), not the hut. Setup and troubleshooting are in `deploy/live_stream/README.md`.

## v0.141

- Packaging fix: restore the `deploy/windows` install/run scripts to the release ZIP. They were accidentally dropped from the v0.140 package when the new external live-stream relay folder (`deploy/live_stream`) was excluded by excluding all of `deploy/`; the build now excludes only the relay folder. No application code change from v0.140.

## v0.140

- Groundwork for the public live camera stream (the stream itself runs on an external relay — see `deploy/live_stream`). Added a public, read-only branding manifest endpoint **`/api/branding/live`** returning the current club and sponsor logos as absolute image URLs plus the sponsor rotation interval, straight from Settings → Public branding, so the relay can burn the current logos into the stream with nothing to sync by hand. Returns `enabled: false` and no logos when public branding is switched off.
- Added a **`public_live_stream_url`** setting (Settings → Video Recording → Public live stream). When it is set, a **Watch live** link appears on the public competitor pages — a button in the home page's live-camera panel and a link in the race page footer; hidden when unset. The URL is validated to http(s).
- No database schema changes.

## v0.139

- Updated the bundled PDF guides for the hut power feature. The **Reference Manual** gains a new *Hut power monitoring (Victron VE.Direct)* chapter (Settings, the dashboard card, the derived hut-consumption figure, and the history graph, with fresh screenshots); the **Series Guide** notes the dashboard power card; all three guides' covers now read *Covers app version 0.139*.
- Documentation only — no application code, routes or database changes.

## v0.138

- Added **hut consumption (DC load)** to the power monitoring. It is derived from the three devices — `load = solar current + charger current − battery current`, times bus voltage — since the SmartShunt only measures net battery current. Shown on the dashboard **Hut power** card and overlaid (purple) on the solar panel of the history graph so generation and consumption can be compared. Sources that are off/absent count as zero; the value is clamped at zero against measurement noise.
- Moved the **Hut power** sidebar link down into the utilities group with Documentation and Backup / restore, since it is not directly part of running a race.
- No database schema changes.

## v0.137

- Added off-grid **hut power monitoring** for the Victron VE.Direct devices (SmartShunt battery monitor, Phoenix IP43 charger, SmartSolar MPPT). A new `core/power.py` reads the read-only VE.Direct text telemetry over three serial ports, stores periodic samples, and reports live status. Monitor-only — the app never sends commands to the charger.
- New **Hut power** dashboard card showing battery SOC/voltage/current, solar input and charger state, live-updated every 5 s from `/api/power/status`.
- New **Hut power history** page (sidebar link, and `/power/history`) with a hand-rolled Canvas2D graph of battery SOC + voltage, solar watts and battery charge/discharge current over a selectable range, fed by `/api/power/history`.
- History is stored in a **separate `data/power_history.db`** (independent of the race database) with a configurable flat retention (default 365 days), so continuous telemetry does not grow the race DB or its backups.
- New **Hut power (Victron VE.Direct)** section in Settings: the three device COM ports, a sample interval and retention, and a **simulator** toggle that generates plausible readings so the dashboard and graph can be demoed before the cables are wired. Real configured ports always override the simulator.
- Settings keys are stored in the existing `hardware_settings` table; no change to the race database schema.

## v0.136

- Shortened and clarified the spoken start-sequence announcements. The full course announcement is no longer repeated at −3:00 (it is still given at −9:00 and −7:00), and the class list ("Classes IRC1, IRC2, …") is now dropped from every announcement inside six minutes of the start so the timing calls stay short and clear. Multi-start races still keep the short start name (e.g. "Start 1.") on those inner calls to tell starts apart.
- Changed the final countdown from ten separate one-word calls to a single spoken "Ten. Nine. … One." phrase over the last ten seconds, at the normal speech rate. Each spoken item re-initialises the TTS engine, so the old per-second calls drifted late and short words were clipped; one phrase is spoken cleanly. The start horn still fires exactly on the clock.
- Fixed the UI appearing to freeze while the horn sounded. `fire_horn()` holds the serial IO lock for the whole blast, and the start console polls the manual-horn input several times a second; that poll now takes the lock **without blocking**, returning an "active / horn firing" state immediately instead of every poll queueing behind the blast. Normal reads still take the lock and release it afterwards.
- No database schema or route changes.

## v0.135

- Fixed the public competitor race page so its video links use the Cloudflare R2 public copy when one is ready, instead of always pointing at the hut PC. Previously the R2-preference logic only existed in the published-HTML export path; the live competitor page hardcoded the hut route (`public_video_clip_file`) for both the start video and the per-boat finish links, so with **Public video publishing** set to *Cloudflare R2 public copy* competitors were still sent to the hut.
- Added `public_video_clip_href(clip)`: returns the clip's `public_url` when its `public_status` is `ready`, otherwise falls back to the hut route. Exposed via `competitor_race_context` and used for both links in `competitor_race.html` (now with `rel="noopener"`).
- Added `TestPublicVideoClipHref` covering ready->R2, pending->hut, off->hut, and ready-but-missing-url->hut.
- No database schema changes.

## v0.134

- Internal architecture refactor with no behaviour change: extracted the logic from the monolithic `app.py` into a `core/` package of 22 modules — `appstate`, `db`, `settings`, `timeutils`, `polars`, `ratings`, `scoring`, `weather`, `courses`, `races`, `classconfig`, `series`, `entrysync`, `boats`, `polar_io`, `backup`, `eventlog`, `weather_store`, `horn`, `audio`, `video`, `startsequence`.
- `app.py` is now the Flask web layer (routes, auth/CSRF/sessions, settings form, background-task startup, Waitress entry point) and re-imports names from `core/`; it went from ~11,340 to ~4,300 lines. Mutable/monkeypatched state (paths, `DB_PATH`, reloadable course/mark data) is single-source in `core/appstate`; `core` → `app` callbacks use registered hooks (`core.db.SCHEMA_INITIALIZER`, `core.horn.VIDEO_CLIP_SCHEDULER`) to avoid circular imports.
- Added `tests/test_smoke_routes.py` (route-level smoke sweep + core write-flow tests); the suite is now 372 tests, kept green after every extraction step. Fixed a pre-existing test-isolation bug where the backup-restore test left the reloaded course/mark globals mutated for subsequent tests.
- Put the project under git with a reconstructed commit history and added `.gitignore`.
- Updated `docs/DEVELOPER_NOTES.md`, `README.md` and the `app.py`/module docstrings to describe the new structure; removed section-divider comments in `app.py` left stale by the extraction.
- No route, template, database-schema or user-visible behaviour changes.

## v0.133

- Changed `insert_weather_sample()`'s purge rule: samples inside a race's retention window (one hour before its first warning signal to one hour after its last recorded finish) are now kept indefinitely, rather than every sample being purged after a flat 24 hours. Races with no recorded finish yet are treated as still open and are never purged.
- Added `race_wind_retention_windows()` and `sample_time_in_any_window()` helpers plus regression tests (`tests/test_weather_retention.py`) covering: ambient purge with no races, a finished race's window surviving past 24h, and an open (unfinished) race keeping its samples indefinitely.
- No route, template or database schema changes.

## v0.132

- Added three bundled PDF user guides under `docs/`: the Race Officer's Guide, the Reference Manual (Windows setup, horn/audio/RTSP camera/weather station hardware, Cloudflare, and every admin screen), and the Competitor's Guide.
- Added an in-app **Documentation** page (new sidebar link above **Backup / restore**) listing all three guides with an **Open PDF** button each; like the rest of the admin app it requires login.
- Added a `documentation_file` route (`/documentation/<slug>` and `/admin/documentation/<slug>`) serving each PDF; only the `competitor-guide` slug is reachable without login (added to the `public_endpoint()` allow-list), the other two require the normal admin session.
- Added a **Competitor's guide (PDF)** link to the public competitor pages' footer.
- Fixed a duplicate `<footer>` bug on the public competitor pages (`competitors_home.html` and `competitor_race.html`): each page rendered an old unstyled footer plus the newer `.public-footer`. Merged into a single `.public-footer` carrying the version, provisional-information notice, the guide link and the copyright line.
- No database changes.

## v0.131

- Extended the v0.130 navy header treatment to every table in the app (the global `th` style), rather than only the results tables, for a consistent look across Races, Boats, Series, Marks, Settings, Users and results tables alike.
- Fixed a contrast bug uncovered by that change: `.nested th` (the sub-tables shown on the course-recommendation results page) only set a background colour and inherited the new light header text colour, which would have been nearly invisible against its light background. Added an explicit dark text colour for nested headers.
- Removed the now-redundant `.result-card table thead th` rule, superseded by the general `th` style.
- Darkened `.publish-branding-banner` on the standalone published series-results HTML page (`series_publish.html`) from `#e7e7e7` to `#6b6b6b` so club/sponsor logos with light backgrounds keep visible edges.
- CSS/template-only change; no route, database or API behaviour changed.

## v0.130

- Reworked the admin UI to a nautical navy/brass-gold theme: dark navy sidebar with a gold active-page indicator, replacing the previous plain white sidebar on a generic bright-blue accent.
- Replaced the sidebar's text-glyph icons with inline SVG icons (including a sailboat icon for Boats and a map-pin icon for Marks).
- Added a shared `.empty-state` component (icon, heading, helper text, call-to-action) and applied it to the Races, Boats and Series admin pages plus the public competitor-information and current-race pages.
- Restyled the race-header signal-flag panel to match the countdown clock's dark treatment so the two sit together as one visual unit.
- CSS/template-only change; no route, database or API behaviour changed.

## v0.129

- Fixed manual horn-input live status polling on Settings/Start Console by using the authenticated `/admin/api/hardware/input_status` JSON endpoint.
- Hardened the browser-side status poll so an HTML login/error page is shown as a short unavailable message instead of a JSON `Unexpected token '<'` parse error.
- Made the manual horn-input status route explicitly return JSON and added `Cache-Control: no-store`.
- Added regression tests for the route content type, unauthenticated JSON response and Settings page polling URL.

## v0.128

- Tidied **Settings → Horn settings & Test** by splitting it into task-based subsections: horn output, manual horn input sensing, and start automation/central audio.
- Moved the horn test button/current output summary into the horn-output subsection and the live input status into the manual-input subsection.
- Updated documentation and added regression coverage for the reorganised horn settings layout.

## v0.127

- Refreshed backup/restore documentation after the Windows database-backup lock fix.
- Clarified code comments around temporary backup ZIP creation, SQLite online backup and cleanup.
- Added regression coverage for backup section defaults, backup manifest metadata and the rule that saved videos remain opt-in rather than selected by default.
- No intentional user-interface or race-processing behaviour changes.

## v0.126

- Fixed backup creation on Windows when the Database section is selected. The temporary SQLite backup copy is now fully closed before the backup temporary directory is removed, avoiding `[WinError 32]` file-in-use errors.
- Added a regression test to ensure the SQLite backup helper explicitly closes both source and destination database connections.

## v0.125

- Authenticated race-office sessions now carry an app-version marker. After installing a new build, an existing browser session is cleared and the user is sent back to the login page.
- Normal restarts still use the persistent `runtime/secret_key.txt`, so users are not logged out just because the app process restarts.
- Added regression tests for successful-login session marking, stale admin-page sessions and stale admin API sessions.

## v0.124

- Added a new **Backup / restore** admin page, linked at the bottom of the side menu above the human-supervised prototype warning.
- Backup ZIPs can selectively include the database, marks/courses/start-finish files, polars and sail charts, branding images and saved videos.
- Restore accepts a Race Officer backup ZIP and only replaces the selected sections that are present in the archive.
- Videos are not selected by default and show a warning because saved start/finish clips can make a large backup.
- Added a backup manifest plus path-traversal protection for restore ZIP members.
- Added regression tests for the page, backup ZIP contents, selective restore and unsafe ZIP paths.

## v0.123

- Added four extra vertical scale lines/labels to each side of the shared wind-history plot, so both TWD and TWS now show seven labelled scale positions.
- Changed the TWS history from a line trace to horizontal bars drawn from 0 kt to the measured wind speed, giving a display closer to the B&G-style wind plot.
- Added regression coverage for the extra scale ticks and TWS bar rendering.

## v0.122

- Centred the public competitor-page club logo and navigation/action buttons.
- Anchored the TWS axis at 0 kt on the shared wind-history plot used by the competitor and course-recommendation pages.
- Added a simple read-only entry list to the race **Add entries** tab.
- Tidied the start-console horn configuration display and shortened manual horn-input feedback messages.
- Split **Settings → Video Recording** into clearer subsections: camera input/recording, live preview/buffer, public publishing, PTZ presets and recorder status.
- Added route/static regression coverage for the new layout and wind-scale behaviour.

## v0.121

- Changed public camera/video branding to reduce clutter: public start/finish videos keep the club logo at top-left and rotate one sponsor logo at a time in the top-right every 5 seconds.
- Public live-camera JPEG branding uses the same five-second sponsor rotation slot, so locally served and R2-hosted live images show one sponsor at a time.
- The standalone published-results HTML keeps the full logo strip in its grey banner, because that page is static rather than time-based video.
- Added regression tests for the rotating FFmpeg overlay, live-JPEG sponsor selection and live-image cache key.

## v0.120

- Refreshed documentation and Settings-page help text after the public live-image R2 work and published-results branding/video-link changes.
- Added a direct regression test for `upload_file_to_r2(..., cache_control=...)` so live-image uploads keep their short/no-cache header.
- Hardened the weather background poller against a brand-new or swapped SQLite database during tests/startup before the weather tables are visible.

## v0.119

- Fixed the public live-image R2 uploader crash caused by `upload_file_to_r2()` not accepting the `cache_control` argument. The live JPEG upload now uses the intended `no-store, no-cache, must-revalidate, max-age=0` header.

## v0.118

- Added optional Cloudflare R2 publishing for the public live-camera JPEG.
- Settings → Video Recording now includes a **Public live image** selector and upload interval; when R2 mode is selected, the app uploads the branded frame to `<prefix>/live/latest_public.jpg`.
- The competitor home page uses the configured R2 public URL for the live image when the option is enabled, avoiding repeated live-image requests to the hut through the 4G/Cloudflare Tunnel origin.
- Added live-image R2 uploader status on the Settings page and regression tests for the public page URL and stable R2 object key.

## v0.117

- Added a grey top branding banner to the standalone published/downloaded series results page so sponsor logos with pale artwork or transparency remain visible.
- Simplified missing/unavailable public video text in the published results export to **No Video**, avoiding the long internal recorder message in website-facing results.

## v0.116

- Added public start-video links and per-boat public finish-video links to the standalone series results HTML generated by `/series/<id>/publish.html` and the download version of that page.
- The published results export now embeds the configured club/sponsor branding images as data URIs, so downloaded HTML files keep the sponsor logo strip when uploaded to the club website.
- Public video links prefer direct Cloudflare R2 URLs when available and otherwise use the existing `/public/video/clip/<id>` route.


## v0.115

- Changed the public live-camera image to burn club/sponsor branding into the JPEG returned by `/public/video/live_frame.jpg` instead of relying on browser overlay elements.
- Reused the same top-row logo layout as the public start/finish video transcode, so public screenshots/saved frames include the PSC and sponsor logos.
- Private race-office live preview and full-quality evidence clips remain unbranded.
- Added regression coverage for the public live JPEG branding filter.

## v0.114

- Changed public branding overlays so the club and sponsor logos occupy the top sky band instead of a right-hand sponsor column.
- The club logo is placed top-left and sponsor logos are evenly spaced across the remaining top row.
- Public live JPEG and public-video FFmpeg overlays cap logo height at about 15% of the image/video height.
- Added regression coverage for the new top-row public-video overlay layout.

## v0.113

- Fixed public-video branding transcodes timing out when club/sponsor logo inputs were looped indefinitely.
- Overlay filters now use `shortest=1` and `eof_action=endall`, so FFmpeg stops at the end of the evidence clip even when the logo inputs are infinite.
- Added regression coverage for the branded public-video FFmpeg overlay EOF guard.

## v0.112

- Added Settings → Public branding for competitor-facing media.
- Public live-camera preview can show a transparent club logo at the top-left and sponsor logos spaced across the top sky band.
- Public R2 start/finish videos burn the same branding into the smaller web copy while leaving full-quality evidence clips unbranded.
- Added upload/delete controls for club and sponsor logos stored under `data/branding/`.

## v0.111

- Manual Settings-page PTZ preset tests now hold the selected preset for 30 seconds before the automatic scheduler can switch back to idle/recording mode.
- This fixes the race-preset test appearing to do nothing when no race is currently in the start/finish window because the scheduler could immediately request the idle preset.
- Settings → Video Recording shows the manual hold-until time in PTZ diagnostics.
- Added regression tests for manual PTZ hold and expiry.

## v0.110

- Added a read-only **Save and test camera login only** button for Hikvision/PTZ diagnostics. It calls the PTZ capabilities endpoint without moving the camera, so credentials and channel can be checked before trying a preset.
- Added a PTZ authentication-mode selector: Digest, AnyAuth and Basic. Digest remains the default; AnyAuth is useful if the Hikvision web-auth setting differs from the original manual `curl --digest` test.
- Disabled browser/password-manager autofill on the PTZ username/password fields to avoid accidentally saving the Race Officer web-app password as the camera password.
- PTZ status now shows whether a username/password are saved, the configured auth mode and a password-masked equivalent curl command.

## v0.109

- Added a PTZ authentication-failure guard: an HTTP 401/403 from the Hikvision preset API now pauses automatic camera preset switching instead of retrying repeatedly.
- This protects the camera admin/login page from lockouts caused by repeated bad credentials during the start-sequence scheduler loop.
- Added Settings UI guidance explaining that the hidden PTZ password field keeps the saved password when blank, so a changed camera password must be entered once.
- Added regression tests for the PTZ auth-failure pause and Settings warning.


## v0.108

- Changed Hikvision PTZ preset control to prefer the system `curl --digest` implementation before Python urllib, matching the manual command already known to work on the hut PC.
- Kept urllib digest-auth as a fallback when curl is not available.
- Settings → Video Recording now shows PTZ diagnostics after tests and automatic switches, including HTTP status, auth method and the preset URL called.
- Added regression tests for curl-first PTZ calls and diagnostic status fields.

## v0.107

- Improved live JPEG preview quality for the finish/line camera.
- Added Settings → Video Recording controls for preview JPEG size, JPEG quality, frame rate and keyframe-only snapshots.
- RTSP stream-copy preview defaults to the preview/sub-stream's native size rather than forced 640 px scaling.
- Added tests for the new preview settings and FFmpeg preview filter generation.

## v0.106

- Refreshed documentation and code comments after the Cloudflare R2 public-video upload/retry work.
- Corrected the README's top release summary so v0.105 now refers to serialised/retryable public-video uploads, not the v0.104 R2 test-upload diagnostics.
- Expanded the video/settings docs for local evidence clips, smaller public copies, R2 uploads, upload retry behaviour and recommended post-race checks.

## v0.105

- Made Cloudflare R2 public-video publishing more robust for hut 4G use.
- Public-video uploads are now serialised and retried before a clip is marked as failed.
- Added a Settings → Video Recording button to retry failed or stuck public-video uploads using the existing local public copy where possible.

## v0.104

- Improved Cloudflare R2 test-upload diagnostics on the Settings page.
- The R2 account field now accepts either the account ID or the copied S3 endpoint URL.
- Test uploads now store the last result, display the generated bucket/key and public URL, verify that the public URL is readable, and include clearer upload error details.
- Added a curl AWS SigV4 fallback for R2 uploads when the built-in uploader fails.

## v0.103

- Added optional Cloudflare R2 publishing for public competitor video clips.
- Evidence clips remain local and full-quality; the app creates a smaller H.264 web copy for public viewing when R2 is enabled.
- Public video links redirect to the configured R2 public/custom-domain URL once upload is complete, reducing load on the hut 4G connection.
- Added Settings fields for R2 account ID, bucket, access key ID, secret access key, public base URL, object prefix and public clip quality.
- Added a Settings test-upload button and tests for R2 URL/key helpers and public clip status handling.

## v0.102

- Changed the RTSP input default back to camera/RTP timestamps instead of wall-clock arrival timestamps. The uploaded Hikvision test clip showed pause/jump artefacts and missing frames while the log showed repeated RTP sequence errors.
- Removed `discardcorrupt` from the RTSP input options so FFmpeg keeps as much usable video data as possible in stream-copy mode.
- Added a **RTSP timestamp mode** setting: Camera timestamps recommended, wall-clock arrival time as an explicit fallback.
- Added recorder-status warnings for RTP sequence errors, corrupt frames, timestamp problems and dropped RTSP connections.
- Added tests for timestamp-mode settings, FFmpeg command generation and log warning detection.

## v0.101

- Changed the default RTSP stream-copy rolling buffer back to fragmented MP4 after hut testing showed the Hikvision HEVC stream could leave the MPEG-TS recorder running without closing usable segments.
- Added a **Stream-copy buffer format** setting with Fragmented MP4 as the recommended default and MPEG-TS as an advanced fallback.
- Kept the separated main-stream recorder and preview/sub-stream process introduced in v0.100.
- Added tests for MP4 stream-copy command generation, TS fallback command generation and settings persistence.

## v0.100

- Split RTSP stream-copy evidence recording and live JPEG preview into separate FFmpeg processes. The main recorder now opens only the recording/main stream, so a dropped preview/sub-stream cannot stop evidence recording.
- Added RTSP timestamp-generation options and more tolerant non-keyframe TS segment cutting for Hikvision streams that send sparse or reset timestamps.
- Updated tests for the separated recording and preview commands.

## v0.99

- Changed RTSP stream-copy rolling-buffer segments from MP4 to MPEG-TS for better handling of live H.264/H.265 streams and segment/keyframe boundaries.
- Event clips are still exported as MP4 using stream copy/remuxing, so files stay small and no recording quality is lost.
- Kept support for existing MP4 buffer segments from earlier versions until they age out.
- Added regression tests for MPEG-TS copy-mode buffer generation and mixed TS/MP4 segment discovery.

## v0.98

- Added RTSP/IP camera stream-copy recording mode for Hikvision-style cameras whose main stream is already H.264/H.265.
- Added optional RTSP preview/sub-stream URL so the live camera view can use a low-bandwidth sub-stream while rolling evidence segments copy the main stream without re-encoding.
- Kept the old re-encode path for USB webcams and for installations that need the app's burned-in timestamp overlay.
- Added tests for stream-copy settings persistence, USB fallback to re-encode mode, copy-mode FFmpeg command generation and re-encode command generation.

## v0.97

- Improved Hikvision camera preset selection for cameras that reject Python urllib digest-auth but work with `curl --digest`.
- PTZ preset `PUT` requests now include an explicit zero-length body and register the exact ISAPI URL with the digest password manager.
- Added a curl digest fallback after 401 responses, using the Windows/system curl executable when available.
- Added regression tests for urllib digest success and curl fallback behaviour.

## v0.96

- Added optional camera zoom/PTZ preset control in Settings → Video Recording.
- Supports Hikvision-style ISAPI preset goto calls using HTTP digest authentication.
- Added idle/zoomed-out and race start/finish presets, with a configurable switch point before the first actual start, defaulting to 30 seconds.
- The start-sequence scheduler queues preset changes asynchronously so a slow/unreachable camera cannot delay horn or audio timing.
- Added Settings test buttons for idle and race presets, plus password preserve/clear handling.
- Added regression tests for PTZ URL validation, preset URL construction, settings persistence and automatic preset selection.

## v0.95

- Refreshed README, user docs and developer notes for the current split-view, current-course API and per-polar sail-chart workflow.
- Moved appended documentation sections back above their copyright footers.
- Updated horn/audio documentation and troubleshooting to describe central race-office-PC speech instead of browser Web Speech output.
- Clarified backup/release notes for `data/sailcharts/`, generated `runtime/` files and the local SQLite database.
- Updated a stale course-announcement docstring in `app.py`.

## v0.94

- Added the supplied J/70 polar as `data/polars/J70.txt`.
- Expanded Settings → Polars so a polar can be uploaded with an optional matching sail chart in one step.
- Enforced sail-chart filenames from the polar stem, for example uploading a chart with `J70.txt` saves it as `J70-SailChart.txt` / `.csv` / `.tsv`.
- Added Settings actions to upload/replace a sail chart for an existing polar, delete only the sail chart, or delete the polar and its matching sail chart.
- Added tests for polar upload, optional sail-chart upload, canonical filename generation, and delete actions.

## v0.93

- Changed per-polar sail charts to use the clearer `-SailChart` filename convention, for example `J109-SailChart.txt` for the `J109.txt` polar.
- Kept legacy same-name sail chart lookup as a fallback so existing installs are not broken.
- Added supplied sail charts for J/109, J/80, J/122, J/70 and the default chart.
- Added supplied polars for Elan 350, J/80, J/99 and UFO 22.
- Added tests for the new sail-chart naming convention and the new J/80 polar/sail-chart pair.

## v0.92

- Removed the Split screen link from the race-office sidebar while keeping `/split` and `/admin/split` available for desktop shortcuts.
- Added `data/sailcharts/` for per-polar sail charts. v0.92 looked for a same-name chart such as `data/sailcharts/J109.txt`; v0.93 replaces this with the clearer `-SailChart` convention.
- Added tests for sidebar split-link removal and polar-specific sail-chart resolution.

## v0.91

- Added `/split` and `/admin/split` for a large-screen race-officer split view.
- The split view embeds `/admin` in a 75% left pane and the public competitor page in a 25% right pane.
- Added a Split screen link to the race-office sidebar. This was later removed in v0.92 so the feature can be launched via a hut-PC desktop shortcut.
- Added route/CSS tests for the split-screen layout.

## v0.90

- Added public read-only `/api/current_race_course` for external range/bearing displays and the Mojito RTC app.
- The current-race course API returns race metadata, display course marks, full mark coordinates, expanded compound-mark geometry and leg bearing/distance data in one JSON response.
- Added route tests covering no-current-race behaviour, fixed-course export and manual compound-course expansion.

## v0.89

- Reworked the race-officer dashboard so the current-race summary and live wind instrument are displayed side-by-side on desktop screens.
- Added `static/dashboard_wind.js`, an analog wind dial that shows true wind direction on a compass scale and true wind speed on an outer speed scale.
- The dashboard wind dial polls `/api/weather/current` and updates without a page refresh.
- Added dashboard route tests for the wind-gauge markup and JavaScript asset.

## v0.88

- Added live polling for the horn/race log on the race detail and start-console pages, so server-side horn/audio/manual-input events appear without a page refresh.
- Reduced final-start-countdown audio logging from ten separate rows to one summary row: `Audio: countdown 10 to 1`. The audio still speaks each number separately.
- Moved competitor race-page navigation below the PSC banner/logo so the Desktop/Mobile/Competitor home buttons cannot wrap over the logo.
- Public race-page footer now includes the app version.

## v0.87 - Series scoring and version display

- Excluded in-progress races from series standings until every entry in that race has a non-`RACING` status.
- Added DNC back-fill for late-joining competitors in earlier races of the same series.
- Added visible app version text to admin/public home pages and changed static asset links to use the current app version.
- Added regression tests for the new series-scoring rules and home-page version display.

## v0.86 - Performance fix after security hardening

- Fixed the v0.85 slowdown by making database schema initialisation and the expensive default-password replacement check run once per process instead of on every request.
- Added a short in-process cache for app settings, invalidated immediately when settings are saved.
- Cached the legacy data-layout migration check so opening SQLite connections no longer repeatedly scans/copies legacy paths.

## v0.85 - Security hardening

- Fixed post-login open redirect handling by accepting only app-relative `next` paths.
- Added per-session CSRF protection for state-changing race-office requests, with automatic hidden-field and same-origin fetch header injection.
- Replaced the public Flask secret-key fallback with a persistent random `runtime/secret_key.txt` when `RO_SECRET_KEY` is not supplied.
- Removed the public default-credentials hint from the login page. First-run admin credentials are now generated locally in `runtime/initial_admin_password.txt` unless `RO_INITIAL_ADMIN_PASSWORD` is set.
- Existing active `admin` / `admin` credentials are replaced automatically unless `RO_ALLOW_DEFAULT_ADMIN=1` is explicitly set.
- Restricted IRC/YTC rating-list reads to HTTP/HTTPS URLs and rejected local file paths / `file://` sources.
- Added weather-station URL validation to block localhost, link-local, multicast and unspecified destinations; `RO_WEATHER_ALLOWED_HOSTS` can further restrict allowed hosts.

## v0.84

- Hardened `static/wind_history.js` so canvas CSS height is fixed before backing-store resizing.
- The public competitor wind plot now passes an explicit 320 px layout height, preventing repeated redraws from making it taller.
- The private course recommendation wind plot now passes an explicit 300 px layout height.
- Added cache-busting query strings to updated CSS/JS links so clients load the fixed assets after deployment.


## v0.83 - Shared wind history chart

- Added `static/wind_history.js` as the single shared wind-history chart renderer.
- Updated the public competitor page and private course recommendation page to call the shared renderer instead of maintaining separate chart code.
- Both wind plots now use the same TWD wrap-around handling, compass-bearing scale labels and fixed-height canvas sizing.

## v0.82 - Wind history chart fixes

- Fixed the public competitor wind-history canvas growing taller on each refresh until the chart stopped drawing.
- Fixed wind-direction history plotting across the 000/360° wrap by plotting a compact compass window centred on the latest sample.
- Direction scale labels now remain normal compass bearings, so the chart no longer shows negative or multi-turn TWD labels.

## v0.81

- Added compound-mark support to the marks file and course geometry.
- Parent marks can define rounding-specific component order using `components`/`rounding_order`.
- Added Gwylan Islands `Y` as `Ya`/`Yb` and Carreg Y Trai / St Tudwal's `A` as `Aa`/`Ab`.
- Course analysis, length calculation, leg tables and charts use the expanded component marks.
- Course boards, public race pages and audio announcements still show the parent mark only, for example `Yp` or `As`.
- Hidden component marks are not offered as standalone marks in the manual-course builder.

## v0.80

- Removed the remaining top-header **Current race** button from the competitor landing page, leaving the main body **Go to current race** button as the single current-race action.
- Added **6. Remove Race** to the race sheet.
- The remove tab lists completed setup/activity before deletion: start time, course, whether the race appears to have started, entry count, boats finished, scoring/status records, race log count, video clips and series membership.
- Added a confirmed race-delete route that deletes the race and its race-specific entries, finish times, race log events and video clip records/files, thereby removing the race from any series.

## v0.79

- Replaced the public page text heading with the supplied Pwllheli Sailing Club banner image.
- Removed duplicated hut wind, weather and camera status from the competitor-page race-information summary box.
- Simplified the live-camera box by removing explanatory/status text; the checkbox remains the control to load the preview.
- Renamed the race-page navigation link from **Current race** to **Competitor home** and removed the redundant **Competitor home** link from the competitor home page.

## v0.78

- Replaced the default public competitor page with a read-only competitor landing page at `/public/current`, reached from the site root `/`.
- Added a current-series race list with links to each public race page and a direct **Go to current race** button.
- Added current start-hut TWD/TWS/gust and a public wind-history plot using the same style as the course recommendation page.
- Added an optional live camera preview checkbox that loads `/public/video/live_frame.jpg` only when the competitor chooses to view it.
- Added a sanitised public live-camera status endpoint while keeping video settings and race-office controls behind `/admin`.

## v0.77

- Changed the Windows startup PowerShell script to run pip and the app through a logged command wrapper that treats stdout/stderr as normal log text and checks the native exit code explicitly.
- Added `--no-cache-dir` to deployment-time pip installs to avoid stale or corrupt local pip cache warnings on race-office PCs.
- Updated deployment troubleshooting notes for the harmless pip cache warning seen during install.

## v0.76

- Changed the Windows Scheduled Task action to run `start_race_officer.ps1` directly with `powershell.exe -WindowStyle Hidden`, so automatic startup no longer leaves a Command Prompt window open on the desktop.
- Updated Windows deployment and troubleshooting documentation to explain the hidden automatic startup behaviour and the visible manual diagnostic start script.

## v0.75

- Fixed the Windows Scheduled Task installer by using the valid PowerShell ScheduledTask run level `Limited` instead of `LeastPrivilege`.
- Added comments and troubleshooting documentation explaining that the task deliberately runs with limited privileges in the signed-in race-office user session.

## v0.74

- Added `deploy/windows/` scripts for race-hut Windows deployment.
- Added a Scheduled Task installer that starts the Waitress app when the race-office Windows user logs in.
- Added helper scripts to start, restart, check status and uninstall the startup task.
- Added `docs/DEPLOYMENT_WINDOWS.md` and updated installation, Windows getting-started, Cloudflare, data/backup, troubleshooting and developer documentation for the deployment model.

## v0.73

- Replaced Flask's built-in development server with Waitress for `python app.py`.
- Added `waitress>=3.0,<4` to `requirements.txt`; existing start commands remain the same.
- Removed the **Current race sheet** side-menu item because the dashboard now carries the active race-sheet link.
- Updated README, installation, Windows getting-started, remote-access, developer and troubleshooting documentation for the Waitress runtime.

## v0.72

- Added Weather settings source radio buttons: weather-station polling or manual TWD/TWS input.
- Reworked the dashboard into a system-status page for current race, countdown, weather, horn and video.
- Changed the Races page so series roll-ups are closed by default, with standalone races open.
- Extended series CSV export to include race-by-race results after the series standings.
- Prefilled the boat edit IRC/YTC lookup search box with the boat sail number.
- Removed public competitor-page link blocks from the race sheet.

## v0.71

- Changed the site root (`/`) to redirect to the current public read-only competitor page. This lets competitors use the bare Cloudflare hostname.
- Moved generated race-office/admin page URLs under `/admin`, including dashboard, race, series, boats, marks and settings pages.
- Kept legacy race-office GET URLs as redirects into `/admin/...` so old bookmarks and tabs remain usable.
- Kept public competitor URLs open and read-only, with admin controls still requiring login.

## v0.70

- Rolled back race public keys so the competitor pages are public read-only again.
- Public URLs no longer need `?key=...`; use `/public/current`, `/public/race/<id>` or `/public/mobile/race/<id>`.
- Removed the race-page **Reset public key** workflow.
- Kept the stricter login split: race-office/admin pages remain authenticated and only the deliberately public read-only routes are anonymous.
- Updated Cloudflare documentation to keep forwarding the whole app to `http://localhost:5050` rather than only `/public`, so static assets load correctly.

## v0.69

- Added keyed public competitor links for Cloudflare/remote access.
- Existing and new races receive a public token; public race URLs include `?key=...`.
- Public race pages, state-polling JSON and public video clip URLs require the valid race key unless the viewer is already logged in.
- Added a **Public competitor links** section to each race page with desktop/mobile links and a **Reset public key** button.
- Tightened the unauthenticated endpoint allow-list so `/public/...` is no longer a blanket bypass for any future route.
- Added `docs/REMOTE_ACCESS_CLOUDFLARE.md` and documented that the Cloudflare Tunnel should forward the whole app to `http://localhost:5050`, not only `/public`, so `/static/...` assets load.

## v0.68

- Moved the SQLite database path to `data/race_officer.db` so it is backed up with the rest of the race data.
- Moved saved event clips to `data/video_clips/`.
- Moved temporary runtime files to `runtime/`: rating-list cache, rolling video buffer, live preview frame and FFmpeg log.
- Renamed `data/SailChart J122 North.txt` to `data/DefaultSailChart.txt`.
- Removed the duplicate `data/J122.txt` polar and the bundled `data/ClubListing.csv`; current rating listings are downloaded and cached in `runtime/cache/`.
- Added upgrade migration for older root `race_officer.db`, legacy clips and the old sail-chart filename.

## v0.67

- Improved series-results performance by caching each race/rating result group while building class and overall series tables.
- Website publish HTML generation reuses the same race-result cache when adding per-race tables.

## v0.66

- Fixed RRS Appendix A7 tie scoring when corrected times are equal at the displayed whole-second precision.
- Race result ordering and equal-points scoring now use the same rounded corrected time value that competitors see in the table.
- This prevents a hidden fraction of a second from separating boats whose published corrected times are equal.

## v0.65

- Fixed series tables so races with entries but no finishers in a class are retained as score columns instead of being dropped.
- Added combined Overall IRC/YTC race and series result tables when the classes being combined use the same rating rule and share a common start time.

## v0.64

- Added the Pwllheli Sailing Club logo to the top-left corner of the standalone Sailwave-style HTML results export.
- Embedded the logo as a data URI so the downloaded website-results file remains self-contained.

## v0.63

- Added `/series/<id>/publish.html`, a standalone Sailwave-style HTML export for website publishing.
- The export contains all classed series tables and race result tables in one file.
- Added a matrix of links for switching between Overall and race tables.
- Added **Publish HTML** and **Download HTML** buttons to the series detail page.
- Added `docs/WEBSITE_PUBLISHING.md`.

## v0.62

- Added `min_races_to_constitute` to each series.
- The series detail form now has a "Minimum races to constitute series" field in the Series discards settings box.
- Series result cards and CSV exports now show the configured minimum and constituted/not-yet-constituted status.

## v0.61

- Added a per-series comma-separated discard profile. The nth value is used after n completed races; the last value is reused for longer series.
- Series results now use RRS Appendix A tie behaviour: A7 race-tie points, A8.1 best-to-worst non-excluded score comparison and A8.2 last-race-back comparison including excluded scores.
- Equal worst scores are discarded from the earliest race first, matching RRS A2.1.
- Added DNE and DGM to the entry status dropdown and made them non-excludable in series totals.
- Updated series scoring documentation and CSV export labels for profiles/non-excludable scores.

## v0.60

- Added `docs/GETTING_STARTED_WINDOWS.md`, a novice-friendly Windows installation and first-run guide.
- The guide covers Python installation, creating and activating the virtual environment, installing dependencies, first login, restarting the app, optional network access, optional FFmpeg installation for video, camera listing, troubleshooting and backups.
- Linked the guide from the README and installation documentation.

## v0.59

- Updated the public/competitor results panel to render the same classed IRC/YTC result tables as the race-office Results tab.
- Added start label, start time, status and unclassed/excluded result cards to the public results view.
- Public displays now reload when class bands, start plans, race-specific entry ratings or entry start overrides change.

This file summarises the main changes added through the MVP versions.

## v0.58

- Refreshed README, workflow docs, flags/start sequence docs, classes/start docs and developer notes to match the current UI.
- Restored `docs/DEVELOPER_NOTES.md`, which had accidentally duplicated troubleshooting content.
- Updated terminology from stored race start time to first warning-signal time where appropriate.
- Documented race-entry IRC/YTC rating snapshots, race-specific rating editing and series entry synchronisation.
- Reviewed code comments/docstrings for stale single-start/class-flag wording.

## v0.57

- IRC and YTC ratings are now snapshotted onto each race entry when a boat is added from the boat database.
- Race results use the race-entry IRC/YTC ratings rather than live boat-database ratings.
- Entries & finish-time admin now allows race-specific IRC and YTC ratings to be edited.
- IRC NS is no longer shown in the current UI.
- Adding a boat to a race in a series now adds it to the other races in that series.
- Creating a new race in a series now imports existing competitors from the series.

## v0.56

- Added the **Races** side-menu item after **New race**.
- Added a Races page with series roll-ups and a separate standalone-races roll-up.
- Changed the live graphical flag box to a white panel with a grey outline.

## v0.55

- Added the uploaded International Code Flag P image as the preparatory flag graphic.
- Updated the race-office and competitor/public dynamic flag panels so P displays as the real flag image, matching the numeral pennants.
- Kept dynamic display behaviour: P only appears during the preparatory period and flags not currently up are hidden.

## v0.54

- Added real numeral pennant images for class flags 0–9.
- Race-office and competitor flag panels now display the configured numeral pennants, stacked vertically when multiple class flags are up.
- Series setup keeps the Numeral 0–9 dropdown and adds a small image preview beside each class flag selection.

## v0.53

- Limited class-flag selection to Numeral 0 through Numeral 9.
- Updated race-office and competitor/public graphical flag panels to use dynamic signal-plan state rather than a single fixed class flag.
- Flags that are not currently up are hidden.

## v0.52

- Replaced free-text series class setup with graphical IRC/YTC rating-band tables. Each series is limited to three IRC classes and three YTC classes.
- Replaced free-text start-plan setup with a six-row graphical start table. Each start has an offset and class checkboxes.
- Added race-level graphical start-plan override while keeping the option to use the series default start plan.
- Existing stored text-style class/start settings remain compatible.

## v0.51

- Race setup now asks for the first warning-signal time. The first actual start is calculated as warning + 5 minutes.
- Start-plan offsets remain relative to the first actual start.
- Added optional per-class flag labels in the series class-band configuration.
- The scheduled signal plan now renders all configured starts, including each start's classes and class flags.
- Rolling-start overlaps are shown as combined signals and the central horn scheduler avoids sounding twice at the same instant.

## v0.50

- Added configurable series rating-band classes for IRC and YTC. Series setup can now define class names and rating ranges such as IRC0/IRC1/IRC2 and YTC0/YTC1/YTC2.
- Added configurable start plans with multiple starts per race. Series can define default starts and each race can override the start plan.
- Race results and series standings are now split by configured rating class, with each class using its assigned start time for elapsed/corrected-time calculations.
- Start-sequence horn and audio automation now schedules each configured start separately and labels multi-start signals by start name/classes.

## v0.49

- Fixed start-sequence automation after editing a race start time. Horns and central audio announcements are now deduplicated by the current race start-time sequence, not just by label.
- Saving race course/start details now resets the in-memory scheduler state and removes queued stale start-sequence audio for that race.

## v0.47

- Updated horn I/O to match the ProLog-style wiring: DTR fires the horn relay, RTS is held asserted as the feedback common, DCD indicates idle, and CTS indicates horn/manual-button active.
- When manual input sensing is enabled, the app now forces horn output to DTR and no longer treats RTS as a selectable horn output.
- Settings now shows the actual DTR/RTS/DCD/CTS wiring instead of a generic selectable input line/polarity.

## v0.46

- Prevented browser manual-input polling from opening the same serial port during a horn blast.
- Test Horn now saves and uses the posted Settings form values before firing.
- Hardware-page Test Horn now honours the requested blast duration.

## v0.45

- Opened the serial adapter with RTS/DTR set inactive first, to avoid repeated short horn pulses when manual horn input sensing is enabled.

## v0.44

- Moved automatic VHF/audio announcements from browser speech synthesis to central server-side audio on the race-office PC.
- Added a central start-sequence scheduler so automatic horns and announcements continue even if the RO is on a different app page or using a tablet.
- Added Settings controls for central audio rates and a central audio test button.
- Disabled browser-generated automatic start audio/horn actions to avoid duplicate signals.

## v0.43

- Added short purpose docstrings to every function in `app.py`, including helper functions, route handlers and nested utilities.
- Updated developer notes to make docstrings required for future functions.
- Re-checked the app syntax and Flask page loading after the documentation pass.

## v0.42

- Refreshed documentation to match the current interface and workflow.
- Added operational checklist, flag/start-sequence guide and developer notes.
- Added explanatory code comments/docstrings around the main subsystems.
- Added `VERSION` file.

## v0.41

- Made the Start console & log manual-controls area full width.
- Removed redundant explanatory text from the manual controls and Course & start tab.
- Renamed the **General Recall** button.

## v0.40

- Flag box now contains only the active flag graphics.
- Flag box is sized to match the race timer card.
- Scheduled signal-plan rows grey out automatically as each scheduled line is completed.

## v0.39

- Removed development/test database from the packaged zip so a fresh install starts cleanly.
- Added more space between Recent races and the dashboard information cards.
- Moved the current flag panel between the race summary/course details and the race timer.
- Made the flag panel compact and removed visible numbers/letters from the displayed flag graphics.

## v0.38

- Dashboard now shows Series and Recent races before the fixed Start/Finish/Radio information cards.
- Add Boat lookup buttons now read **Add IRC** and **Add YTC**.
- Horn and race log is also shown at the bottom of Entries & finish times.
- Current start-sequence flags are displayed on the race page and public competitor pages.
- Initial flag model covers one class start using class flag 1 and preparatory flag P.

## v0.37

- Added Pwllheli Sailing Club logo to the side navigation and flag favicon for browser tabs.
- Removed duplicate prototype warning at the bottom of pages.
- Removed duplicate title/timer block from the Start console & log tab.
- Renamed **Save course and start** to **Save**.
- Added scale labels to wind-history charts.
- Stacked IRC and YTC result tables vertically.
- Added mobile competitor pages.

## v0.36

- Competitor page opens as a pop-out window from the race sheet/navigation.
- Course & start layout tidied; start time is whole minutes only.
- Start console moved before finish-time admin in the race workflow.
- Start automation defaults moved to Settings.
- One-off manual entries can now carry both manual IRC and YTC ratings.
- Finish button moved to the front of the finish-admin table and relabelled **Finish**.
- Public competitor page includes the course chart while the race is active.
- Settings reorganised into collapsible sections.

## v0.35

- Added CapeNet copyright footer and copyright notices.
- Updated UI to a UniFi-inspired management-console look.

## v0.34

- Improved course chart direction arrows so they align with the actual leg bearing.
- Added the CHPSC Bridge window position and plotted the usual start/finish line to ODM O.
- Added a live wind-direction overlay to course charts, labelled as wind from the measured TWD.

## v0.33

- Added course charts to the race Course & start tab, Recommend/change course page, and manual course builder.
- Course charts plot the selected course from CHPSC mark coordinates, colour legs by port/starboard rounding, and use web map/nautical seamark tiles where available with an SVG fallback.

## v0.32

- Reworked the documentation into a structured `docs/` folder.

## v0.31

- Live finish camera moved below the entries/finish-time table.
- Live preview collapsed under **Show live finish camera**.
- Removed edit-boat links from the finish-time admin tab.

## v0.30

- Added live finish-camera preview using the same FFmpeg process as recording.
- Preview supports USB webcam and RTSP source.
- Preview includes PC time overlay.

## v0.29

- Fixed FFmpeg `drawtext` timestamp filter chain so rolling buffer segments are written correctly.

## v0.28

- Added recorder health checks and FFmpeg diagnostics log display.
- Added latest buffer segment status.

## v0.27

- Fixed Windows DirectShow camera-name decoding and added manual USB camera source override.

## v0.26

- Fixed FFmpeg DirectShow USB camera enumeration on newer FFmpeg builds.

## v0.25

- Added USB camera dropdown, timestamp overlay, and public video links after race finish.

## v0.24

- Added video recording for starts, finish-button events and manual horn events.
- IRC ratings display/export to three decimal places.

## v0.23

- Public competitor page updates more quickly and switches from course analysis to results when finished.

## v0.22

- Added public competitor pages and username/password login for the race-officer app.

## v0.21

- Simplified race creation, added series management, and added series scoring.

## v0.20 and earlier

Earlier versions introduced the race sheet, dual IRC/YTC results, weather integration, polar-based course recommendation, manual course builder, horn hardware support, boat database, rating importers, and the original race-pack/course data.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
