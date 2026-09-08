# Clubhouse display

A page built for one job: a television in the club bar showing the race that is
happening now.

```text
https://pro.pwllhelisailingclub.org/bar
```

Open it once on the TV's browser, put it in full screen, and leave it. There are
no controls on the page at all — nothing to press, nothing that a stray remote or
a curious member can put into a state somebody then has to fix.

## What it shows

- **The course chart, full screen**, following the boats that are **still
  racing** — each of them and the mark each is sailing to, so the room can see the
  leg rather than just the boats on it. A whole-course view spends most of a
  television on empty sea once the fleet has strung out down one leg. Boats that
  have finished are deliberately left out: they are parked by the line and would
  hold the view open across the whole course for the sake of somebody who is
  already in the bar. With nobody racing — before the start, or once everyone is
  in — it shows the whole course.

  The view only moves when it needs to: when it no longer holds the fleet, or
  when it is holding far more than it needs and could usefully close in. A map
  that re-fits whenever a boat moves a length is a map permanently in motion. It
  also fits to the part of the chart that can actually be *seen*, allowing for the
  header across the top and the leaderboard down the right, so the fleet does not
  end up neatly centred underneath the board.

  Boats are drawn as hulls turned to their heading, each in its own colour,
  trailing the last fifteen minutes of track. Positions are interpolated between
  fixes so the boats move rather than jump every ten seconds.
- **Race information across the top** — the club crest, race name, first start,
  course number and the course sequence, with a large **race clock** on the right
  counting down to the gun and then up through the race. The course sequence is
  coloured red for a port rounding and green for a starboard one, the same solid
  colours as everywhere else in the app (v0.249 — they had been pale tints, which
  read as plain grey from the other side of a room).
- **It keeps up with the race office** (v0.251). The chart, the sequence, the start time the clock counts from and the schedule the flags come from are all written into the page once, by the server, so the display polls a hash of them every five seconds and reloads when it changes. Shortening the course, changing it, setting the start or re-measuring a mark from the water all reach the television on their own; before this it sat on *Waiting* with the old course until somebody found a keyboard. The hash deliberately excludes entries and finishes: reloading the display in the middle of a finish sequence would restart the map, the camera and the board cycle at the worst possible moment.
- **The hoisted signal flags, beside the clock** (v0.250) — the class flags up for
  the starts about to happen, and the preparatory flag P through its window. They are
  laid out **right to left**: the first flag hoisted sits nearest the clock and each
  later one is added to its left, the way they go up the mast. With nothing up the row
  takes no space at all. Code flag S appears while a shortened course is still being
  sailed, and nothing is shown once the race is finished.

  Which flags are up is the same rule every other page uses
  (`static/signal_flags.js`) &mdash; the competitor page, the race sheet and the pursuit
  race sheet &mdash; so the television, the race officer's screen and a competitor's
  phone cannot disagree about what is flying: a class flag from its warning signal to
  its start, P from the preparatory signal to one minute.
- **The leaderboard down the right**, cycling every 15 seconds through every
  board the fleet is rated for: the order on the water, then IRC corrected, then
  YTC corrected. Overall only — a bar screen is not the place to work through the
  classes one at a time, and nobody is going to walk up to the television and
  change it. Dots beside the heading show where it is in the cycle. Each boat's
  colour sits beside its name so the list and the chart read as one thing. Each row
  carries the boat's **speed over the ground** (v0.249) as well as its progress: the
  order says who is ahead, the speed says whether that is about to change. It is taken
  from the same interpolated position the chart draws the hull at, so the number and
  the boat agree; a finished boat shows a dash.

  On the corrected boards a boat that has finished shows its real elapsed time
  and one still racing shows the projection (see
  [`TRACKING.md`](TRACKING.md)), captioned as an estimate.
- **The start-hut camera over the chart** at the two moments there is something to
  watch (below). The header and the order stay visible while it is up, so the room
  can still see which boat it is watching.

`/bar/<race id>` pins the display to one particular race, which is what you want
for showing a race that has already been sailed. Plain `/bar` follows the club's
**current** race and re-points itself when that moves on, so the display carries
through a day of racing without anybody touching it.

### Which race is "current"

The latest race that still has boats **racing**; failing that, simply the latest race.
Ordered by first warning signal, falling back to when the race sheet was created.

The first half of that rule is deliberate and occasionally surprising: **creating a new
race sheet does not take the screen away from a race still being sailed.** If an earlier
race has a boat that was never finished or retired, that race stays current and the
display stays on it. Finishing or retiring the last boat is what moves things on — which
is worth knowing when a display seems stuck on the wrong race. Look at the race sheet of
the previous race for a boat still showing as racing.

**A deleted race no longer strands the display** (v0.252). Its state poll was asking about
a race that had ceased to exist, getting a 404, and treating that like a dropped
connection — so it asked again every five seconds and went on showing a deleted race
indefinitely. It now reloads once when it sees that 404: plain `/bar` picks up whatever is
current now, and a pinned `/bar/<id>` lands on *No race running* and stops. A 500 or a
genuinely dropped connection is still shrugged off without a reload, which is what you
want on a screen left running all afternoon.

## When the camera appears

The chart is on screen for most of a race. The camera takes over for:

| | from | until |
|---|---|---|
| **The start** | 2 minutes before the gun | 2 minutes after |
| **Each finish** | about 2 minutes before the boat crosses | 1 minute after |
| **Rounding the ODM** | about a minute before | about a minute after |

The start is a known time, so that window is exact. A finish is not — a boat
arrives when it arrives — so "two minutes before finishing" has to be predicted,
from the distance the boat still has to sail divided by the speed it is making.
That is a crude way to think about a whole race and a perfectly good one over the
last two minutes, when the boat is on its final approach with the line in sight.

**Rounding mark O** is worth showing because most courses pass it more than once —
courses 11 and 14 go round it three times — and O is the seaward end of the
start/finish line, which is exactly where the hut camera is pointed. That window
is neither a known time nor a prediction, just a distance: how far the boat is
from the mark against how far it travels in a minute at the speed it is making.
One test, no history to keep, and it covers the approach and the exit
symmetrically without having to know which one it is looking at. A boat drifting
gets a floor of 150 m so it still appears.

A boat that has not rounded anything yet is ignored here — it is sitting by the
ODM because that is where the line is, and the start window has that covered.
Without it the camera would simply stay up after every start on a course whose
first leg begins at the line.

Run over the recorded track of a real race on course 1 (`O 1 8 4 O 9 7 O`), the
display would have shown: the camera for 4 minutes over the start, the chart for
26, then the camera for about 2 minutes for each of the three boats rounding O,
then the chart again, then about 2 minutes for each of the three finishes.

A boat that has *just crossed* takes precedence over one still coming in, because
that is the finish the room has looked up at the screen for; where several boats
are inside the window, the nearest one is the one named in the caption. A boat
drifting at less than half a knot gets no prediction at all rather than an
arrival time an hour out.

The camera is **started and stopped**, not left running. The relay serves its
stream on demand, and a display left on all afternoon would otherwise hold that
stream open the whole time for a room that is looking at the chart.

The logic is `core/bardisplay.py` and is covered by `tests/test_bar_display.py`;
it lives on the server rather than in the page so it can be tested, and so it uses
the same course-progress figures the leaderboard does.

## What it needs

- **GPS tracking enabled**, with the racing boats' trackers assigned — the chart
  is the whole page, and without tracking there is nothing to draw on it.
- **A camera configured** for the video windows. It uses the same source as the
  competitor pages: the public live stream if one is set in
  Settings → Video recording, otherwise refreshing snapshots from the hut. With
  neither, the page still works — the chart simply stays up throughout.
- **A browser left in full screen.** Most smart TVs and any small PC or Raspberry
  Pi driving an HDMI input will do. The page redraws once a second and asks the
  server for new fixes every ten, so it is light enough to leave running.

## Notes for whoever sets the TV up

- Turn off the browser's screen blanking and any "return to home screen" timer.
- The page is on the public site and needs no login, like the rest of `/public`.
- It sizes itself from the viewport, so a 1080p and a 4K screen both work; type
  and the chart scale with the display rather than being fixed in pixels.
- If the bar screen is a different shape from 16:9, the leaderboard column is a
  proportion of the width (26%, between 300 and 460 px) and will follow it.


## Testing it without waiting for a Saturday

Everything interesting about this page happens during a race. `scripts/rerun_race.py`
takes a race that has already been sailed and runs it again as if it were
happening now — a **new** race starting in a moment, fed its own recorded fixes on
the clock:

```bash
python scripts/rerun_race.py --list                     # what can be replayed
python scripts/rerun_race.py --source 130               # as it happened
python scripts/rerun_race.py --source 130 --speed 6     # ten minutes, not an hour
```

It prints the race id it made and the URL to open. Nothing about the original race
is touched, and the boats and trackers it creates are its own, so a real tracker
reporting at the same moment cannot collide with it.

`--speed` scales the recorded **speed over the ground** as well as the clock,
which matters here: the finish camera window is decided from distance-to-go
divided by speed, so replaying positions six times faster while leaving the speeds
alone would put every boat six times further out than it is and the camera would
come up as the boat crossed rather than two minutes before.

Replay races are named `RERUN — …` and `--purge` removes every one a previous run
left behind.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
