# 3D race replay films

The app can turn a sailed race into a **film**: the fleet on the water in three dimensions,
sailing the course they actually sailed, over the real coastline, with the hut camera cut in
at the start and the finishes. It is made from what the race office already recorded — GPS
tracks, the course as sailed, the wind log, the results and the published start and finish
videos — so there is nothing extra to do on the water to get one.

Competitors reach it from the public pages. The race office presses one button.

## The one thing to understand

**The hut PC does not make the film.** It cannot: a single replay is around eleven thousand
frames, a couple of hours of work for a machine with a graphics card, and the hut PC is a
fanless box that is also running the race.

So it doesn't try. Pressing **Render a 3D film** writes a *job* into the same Cloudflare R2
bucket the club's race videos already use. A **render machine** somewhere else — a desktop
at home, switched on when there is something to make — picks the job up, makes the film, and
puts it back in that bucket beside the clips it is made from.

```
   Hut PC                              Cloudflare R2                 Render machine
   [ Render a 3D film ] ── job ──────────►  jobs/          ◄────────── polls every 30 s
   dashboard card       ◄── status ─────── status/  ◄───────────────── reports progress
   public pages         ◄── the film ────── films/   ◄───────────────── uploads the film
```

Neither machine connects to the other. The hut only ever pushes, the render machine only
ever polls, so the render machine needs no route into the club and can sit behind any
router. It also means **nothing happens until a render machine is switched on** — a job
queued against one that is off waits patiently, which is what the dashboard card is for.

Setting one up is [`deploy/render_machine/README.md`](../deploy/render_machine/README.md).
A club with no render machine simply never sees the button do anything, and everything else
in the app is unaffected.

## Asking for a film

On a race's **Results** tab, beside *Replay this race in the bar*, there is **Render a 3D
film**. Any signed-in user can press it — it costs a little bucket space and somebody else's
computer, and the person who wants the film after a race is not always an administrator.

What it tells you when you press it:

- **how many boats and how long the racing was**, so you can see it picked up the race you
  meant;
- **a warning if race videos have not been published yet.** The film cuts the hut camera in
  at the start and at each finish, and it can only use clips that have reached the bucket.
  This is not a failure — the film is made either way, it simply has no inset for the clips
  that are missing. If you want them in it, publish the videos first and render afterwards.
- **whether any render machine has checked in.** If none has, the job is still queued and
  will be picked up whenever one appears.

The same strip then shows progress, and finally **Watch the film**. You can press **Render
it again** at any time; the new film replaces the old one everywhere, including on links
already shared.

### How long it takes

The race office should treat this as *this evening*, not *in a minute*. An eight-minute film
of a three-boat race took two hours on a modest graphics card and about twenty minutes on a
good one. The page shows a percentage and an estimate, both of which are honest but move in
steps, because the work is divided into a handful of unequal pieces.

## On the dashboard

The **3D replay** card is not really about progress. It is about whether there is a render
machine at all, because a job queued against a machine that is switched off looks exactly
like one being worked on until somebody notices hours later. It says which race is being
worked on, or that no render machine has checked in, and it flags a render that stopped
reporting part way through.

The card hides itself completely when the club has not set this up, so it costs a race day
nothing.

## What competitors see

Once a film exists, a link to it appears by itself in three places:

- the **public races list**, as a *3D replay* button beside *Open*;
- the **public race page**, in the header under the entry counts;
- the **published results document**, in each race's section above the start video.

Nothing appears until the film is actually in the bucket, so a race that has not been
rendered looks exactly as it did before. The link goes straight to the bucket rather than
through the hut, so it works whether or not the clubhouse PC is switched on, and it does not
put a large download through the hut's 4G connection.

## What is in the film

- **The boats**, each in its own colour, heeling and trimmed to the wind of that moment and
  setting a spinnaker when the angle calls for one. The wind is the race's own log, sampled
  through the race rather than averaged — on a day that went from 122° to 196°, an average
  would have shown a fleet trimmed to a wind that was only briefly true.
- **The course actually sailed**, including a shortened course, with the marks where they
  stood on the day rather than where they are now.
- **The start line and the finish line.** For an ISORA passage race these are not the same
  line, and the film uses both.
- **The hut camera**, cut in at the start and at each finish, locked to the same clock as the
  3D view so the picture and the boats agree.
- **A race clock, the course board, the true wind, and boat names**, drawn in the app's own
  typefaces.
- **The club burgee and the sponsors**, exactly as on the club's start and finish videos and
  from the same source, so changing a sponsor in Settings changes the films too with nothing
  to copy anywhere.

The replay runs at 30× real time, dropping to real time at the start and at each finish so
those can be watched properly.

## Before it will work

Three things, all one-time:

1. **Public video (Cloudflare R2)** configured under **Settings → Video** — the films go in
   the same bucket as the race videos, and both ends find each other through it.
2. **Settings → Web server → Public address** set to the address competitors use. The render
   machine reads the club's logos from there; without it the film still renders, just
   unbranded.
3. **A render machine**, set up per
   [`deploy/render_machine/README.md`](../deploy/render_machine/README.md).

## When something looks wrong

**The button says no render machine has checked in.** The render machine is off, or its
settings do not match the club's bucket. That guide's `-Check` says which.

**The film has no coastline.** The render machine could not fetch map tiles the first time it
saw that stretch of water. It caches them afterwards, so this is a one-off per area.

**The film has no club or sponsor logos.** The render machine could not reach the club's
public address — see *Before it will work*, item 2.

**A re-rendered film still looks like the old one.** It should not: each film's link carries
its own version. If you have an old link saved from before this was fixed, take a fresh one
from the race page.

**Nothing happens for hours and the dashboard card looks fine.** Check the card says a render
machine is *ready* or *working*, not just that the job is queued.

---

Setting up the machine that renders these:
[`deploy/render_machine/README.md`](../deploy/render_machine/README.md). It is built from the
same release ZIP the clubhouse PC runs — the renderer ships inside it, so there is no
separate download and nothing to fetch from a source repository.

Code structure: [`DEVELOPER_NOTES.md`](DEVELOPER_NOTES.md). The pipeline in detail is
`scripts/replay3d/README.md`, in the release beside the code it describes.
