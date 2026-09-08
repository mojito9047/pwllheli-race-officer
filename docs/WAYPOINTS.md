# Waypoints

A **waypoint** bends a leg. It is not a mark: boats are not asked to round it, it
does not appear on the course board or in the audio announcement, it is not
offered as a place to shorten the course, it is not counted in a boat's mark
tally, and it is not drawn on the chart. The course simply changes direction
there.

## Why they exist

Pwllheli's long courses run west past the Llŷn peninsula. A straight line from
mark 2 to the Gwylan Islands crosses Mynytho, Llangian and Trwyn Cilan — so the
chart drew a course over land, which is how this was first noticed. The picture
was the least of it:

| | straight | round the corner |
|---|---|---|
| leg 2 → Ya | 10.90 nm | **13.06 nm** |
| bearing | one leg at 243°T | **211°T then 278°T** |

The distance was **20% short**, so distance-to-go and the predicted leaderboard
were wrong for the whole leg. And with the wind at 145°T, a 66° TWA reach
followed by a 133° TWA broad reach was being modelled as a single leg at 98° —
so the polar projection was wrong for both halves of it.

A waypoint fixes the geometry without inventing a mark.

## Making one

**Marks → Add a mark**, with the **Waypoint (bends a leg, not rounded)** box
ticked. Give it a position in the water, on the line boats actually sail — a
little outside the headland, not on the rocks.

A waypoint carries no rounding radius. Nothing rounds it, so a radius would be a
number that reads as meaningful and is never consulted.

The club currently has two:

- **TC** — Turning Cilan, off Trwyn Cilan.
- **TP** — Turning Porth Ceiriad.

## Using one

In the **manual course builder**, a waypoint has a single **Add** button rather
than Port and Starboard: boats pass a turning point, they do not leave it on a
hand. It appears in the sequence as a grey `via TC` chip rather than a red or
green rounding chip, and the chart draws a grey leg into it.

Add it wherever the course would otherwise cut across the land, including more
than once — an out-and-back course round the Gwylans normally wants it on both
legs.

## What the rest of the app does with it

**In the geometry**, and only there:

- The chart bends the leg at it and draws nothing there — not even in the faint
  background layer of other marks, because there is no buoy at a turning point
  and showing one would say there is.
- Course length, leg bearings, TWA, sail selection and the predicted time all
  follow the routed course.
- Distance-to-go on the water goes round the corner rather than through it.

**Not a mark**, so it is absent from the course board and the public course
sequence, the audio announcement, the shortening options, a boat's `n/N` mark
count, and the ← → mark-correction arrows, which step between marks.

## How a boat gets past one

By a **gate**, not a radius: a waypoint is passed once the boat crosses the line
drawn through it at right angles to the leg arriving there.

A radius cannot do this job, and the reasoning is worth keeping. Boats beating
past a headland pass a long way offshore, so a radius would have to be a mile or
more — and the radius test fires on proximity alone, with no departure test to
hold it back, so a 2 km radius on a waypoint 12.6 km down the leg advances the
walk while the boat is still 84% short of the corner. Widening the
closest-approach neighbourhood instead fails differently: its departure threshold
is a fixed 50 m, so a single tack at 1900 m counts as leaving a 2 km
neighbourhood.

The gate has neither problem. It cannot fire early, because the boat has to draw
level with the turning point; and it does not care how far offshore that happens,
which is the whole reason waypoints exist. Tested out to 5 km either side of the
leg.

## Removing one

A waypoint used by a fixed course, or by any race's made-up course, cannot be
deleted — the app names the race. Deleting one would silently straighten a leg
back across the land on a race already sailed, changing its recorded length and
bearings after the event.

## For developers

A waypoint is a mark record in `data/marks.json` with `"waypoint": true`, used in
a course sequence with `"rounding": "via"`.

The single source of the course path is `courses.expand_course_points()`, which
feeds the chart payload, `course_legs()`, the TWA analysis **and**
`track.course_rounding_sequence()`. Sequence entries carry `via: True`;
`boat_course_progress` walks through them with `track.passed_waypoint()` instead
of `rounded_mark()`, and reports counts and `next_mark` over the non-`via`
entries only.

`course["board_marks"]` is the waypoint-free list every course-board template
prints from. Do not loop `course.marks` for chips — a test fails if any template
does, because six of them used to.

`static/course_map.js` expands the course a second time, in the browser. It takes
the waypoint flag from the mark record rather than the saved rounding, so a course
saved before a mark became a waypoint still draws correctly. Tests assert that
both its route builder and its background-mark layer look at the flag; that copy
of the expansion is the reason a waypoint appeared on the chart as a mark in the
first place.
