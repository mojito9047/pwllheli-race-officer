# 3D race replay (Blender)

Turns a sailed race into an animated 3D scene: the sea, the marks as they stood
that day, the course actually sailed, the finish line the race used, and one
animated yacht per tracked boat, keyframed from its GPS track. With a digital
elevation model the real coastline and hills behind the harbour are built too.

Two halves, joined by a JSON file so neither needs to know about the other:

```
race_officer.db + track_positions.db + marks.json
        │  core/replay3d.py  (the app's own Python; needs the database)
        ▼
a scene file                       local metres, resampled tracks, wind, results
        │  build_scene.py  (runs inside Blender)
        ▼
a Blender scene: sea, land, buoys, course, yachts, camera, sun, race clock
```

The split is deliberate and it is where the work is going to be deployed. The hut PC
cannot render a film in a useful time and should not try, so the halves are meant to run
on different machines:

- **`core/replay3d.py`** is the hut half, because it is the half that needs the database.
  It imports no Blender, no PIL, no GeoTIFF reader and nothing from `scripts/`, so it ships
  with the app.
- **`scripts/replay3d/*`** is the renderer half. Every file there imports nothing from
  `core/`, which is what lets a render machine hold a copy of this folder, Blender, and no
  app at all.
- **`export_race.py`** is a developer's way in to both from one command, and the home of
  the things a hut has no business doing: decoding a digital elevation model, draping
  imagery, downloading clips, drawing cards.

A scene deliberately carries no terrain. The height grid and the satellite imagery are the
same for every race at one club, so they are built once and shared; a scene names them and
the renderer fetches them. Fifty-four thousand height samples in every job would be silly.

### The job and the status beside it

For a render on another machine the scene travels inside a **job**, and the renderer writes
a **status** next to it. They live in the bucket the race videos already use, so the hut
only ever pushes and the renderer only ever polls: the render machine needs no route into
the hut, which is the point.

| key | what |
|---|---|
| `replay3d/jobs/race_<id>.json` | the scene, wrapped in what to do with it |
| `replay3d/status/race_<id>.json` | state, progress, ETA, the finished film's address |
| `replay3d/status/renderer.json` | a heartbeat, so "nothing happened" can be told from "no renderer" |
| `replay3d/assets/…` | terrain, imagery and fonts, uploaded once |
| `replay3d/films/race_<id>.mp4` | the result |

The job and the scene are one object rather than two, because two means a renderer can
pick up a job whose scene has not landed yet. States run `queued`, `claimed`, `rendering`,
`composing`, `uploading`, then `done` or `failed`. A render is hours long, so a status in a
moving state that has not been updated for ten minutes is treated as stale: a renderer that
dies mid-job otherwise leaves a healthy-looking status behind for ever.

## Requirements

- The app's Python (the exporter imports `core/`). `tifffile` and `imagecodecs` only if you
  use `--dem` (Copernicus tiles are deflate-compressed with a floating-point predictor,
  which tifffile alone cannot decode).
- Blender 4.4 or newer (5.2 tested). Nothing extra inside Blender; it uses `bpy`, `bmesh`
  and the bundled `numpy` only.
- Optional: Blender's MCP add-on + `blender-mcp` bridge, so Claude Code can build and
  render the scene in a running Blender. Not required — the command lines below do the
  same thing.

## Step 1 — export a race

```bash
python scripts/replay3d/export_race.py --list          # races that have tracks
python scripts/replay3d/export_race.py --race 457
```

**Use the hut's data, not this checkout's.** The `data/` directory here is a development
copy whose races, courses and finish lines have been edited since they were sailed. Take a
backup on the hut's Backup page with the *tracks* section ticked (or use the nightly
off-site copy), drop the ZIP under `HutData/` (gitignored), and point the exporter at it:

```bash
python scripts/replay3d/export_race.py --backup HutData/pwllheli-race-officer-backup-20260909-150255.zip --list
python scripts/replay3d/export_race.py --backup HutData/pwllheli-race-officer-backup-20260909-150255.zip --race 512 --dem ... --imagery ...
```

The ZIP is unpacked once under `runtime/replay3d/restored/<name>/` and every reader is
re-pointed at it for that run. `--data DIR` does the same for an already-unpacked copy;
`--tracks FILE` supplies a `track_positions.db` when the backup was taken without one.

Options: `--step` seconds between samples (default 5), `--lead`/`--tail` seconds kept
before the first start and after the last finish (300 / 120), `--out` path, and `--dem`
(see Terrain). The JSON holds:

| key | what |
|-----|------|
| `origin` | lat/lon of the course-mark centroid; every `xy` is metres east/north of it, using the same projection as `core.track` |
| `time` | window start (`t0_epoch`), `first_start_rel`, `duration_s`, `step_s` |
| `marks` | every mark with a position that day (`core.track.race_marks`), `in_course` flagged |
| `course` | the expanded course points after any shortening, ending at the finish line's midpoint |
| `finish_line` | the line that race was sailed to (`race_finish_line_points`) |
| `wind` | mean true wind over the window from the hut's samples, else the course's design wind |
| `boats` | per tracked entry: `samples` of `[t_rel, x, y, heading_deg, speed_kn]`; `null` x/y where the boat was out of sight |
| `terrain` | optional height grid: `x0, y0, cell_m, nx, ny, heights[]` (row-major, south row first), plus draped `imagery` and its required `credit` |
| `videos` | optional hut-camera clips: window on the replay clock, where the event falls inside the file, duration and frame rate |

Tracks are resampled onto a regular clock so Blender can key every sample. Gaps longer
than three minutes are left as gaps (the boat hides) rather than drawn as a straight
line. Heading is the tracker's course over the ground, else the direction of travel,
and is held while the boat is stopped so it does not twitch on the mooring.

## Step 1b — plan the film and cut the video frames

```bash
python scripts/replay3d/prepare_video_frames.py --json runtime/replay3d/race_69.json
```

This owns the film's clock and must run before the build. It works out where the
replay should slow down, records that plan in the race JSON, and extracts one image per
film frame of each hut-camera clip against it.

**A flat 30x rushes past the two things people want to watch.** By default the film runs
at 30x, drops to **real time for a minute either side of the start and thirty seconds
either side of each finish**, and eases between the two rates over twenty race seconds so
the change is not a jolt. It also holds four seconds of title card at the head and eight of
results at the tail. Options: `--speed`, `--slow-speed` (1 is real time), `--slow-start`,
`--slow-finish`, `--ease`, `--pre-roll`, `--post-roll`.

That costs frames. R9 Summer is 164 seconds of film at a flat 30x and 479 at these
defaults, so about three times the render. `--slow-speed 2` halves the difference and is
still far more watchable than 30x.

The mapping is a shared, invertible time warp (`replay_time.py`): the builder keyframes
against it, the extractor cuts footage against it, and the clock in the corner inverts it.
Because the footage is locked to the replay clock, during a slow window the hut camera runs
at its natural speed beside a 3D scene doing the same.

## Step 2 — build the scene in Blender

From a shell:

```bash
blender --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_457.json --speed 30
blender --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_457.json --follow "Demo Boat A"
```

From Blender's Python console or the MCP bridge:

```python
ns = {}
exec(open(r"scripts/replay3d/build_scene.py").read(), ns)
ns["build"](r"runtime/replay3d/race_457.json", speed=30)
ns["render_still"]("Replay 457 Club Race test ph line", 1200, r"C:\tmp\frame.png")
```

`--speed` is race seconds per video second (30 makes an 80-minute race a 2.7-minute
film at 24 fps). The scene is self-contained and named `Replay <id> <race name>`;
building it again replaces the earlier one and nothing else in the .blend is touched.

Options: `--shots film|overview|follow` (see Cameras), `--follow BOAT` (the boat for
`follow`), `--boat-model FILE` and `--boat-forward +Y` (see Boat models).

What is in the scene:

- **Environment** — the sea, the **sun where it actually was** for the first start, computed from the race time
  and the origin's latitude and longitude, a matching sky, and distance haze on sea and
  land. **Land** if the export has terrain: textured with the exported imagery when there is
  any, otherwise coloured marsh → pasture → upland by height.
- **The sea** is one quad and all of it is shading: a sea this wide cannot carry real
  geometry. Three noise scales are rotated into the wind's frame and squashed across it so
  the crests run across the wind rather than sitting in blobs; the roughness varies at
  ripple scale, which breaks the sun's reflection into a **glitter track** instead of one
  hard blob and is the strongest single cue that this is water; and the tops go white where
  the chop is steepest, in proportion to the wind that was blowing. Each boat trails a
  **foam wake** on a pivot that copies the hull's position and heading but not its heel
  (heel turns about the wake's own long axis, so a wake parented to the hull rolls its
  edges clear of the water), and its opacity is keyed to boat speed, so the fleet drifting
  on the line before the start leaves none.

  The original shader fed its noise textures no coordinates, so they fell back to the
  *generated* ones, which run nought to one across the whole plane. At nine kilometres wide
  that is less than one wave from the shore to the horizon, which is why the sea used to be
  flat. Texture coordinates now come from object space, which for this plane is metres.
- **Lines** — the export carries the **start line and the finish line separately**, because
  they are not always the same. Pwllheli starts every race on the club line, the ODM to the
  transit on the bridge, but an ISORA passage race finishes on a different line nearly a
  kilometre away: on race 61 the two are 760 m apart. The start line comes from the
  configured `start_line`, whose shore end is a surveyed position in degrees and decimal
  minutes, falling back to the club's default finish line, which is the same geometry in
  machine-readable form. An export made before this existed has no `start_line` and the
  scene falls back to the finish line, which is right for a race sailed to the club line
  and wrong for an ISORA one.
- **Marks and course** — a barrel buoy and floating code label per mark (course marks
  yellow, others muted; passage marks beyond the sea plane are skipped), the course as an
  orange path, the finish line in red.
- **Boats** — a yacht per boat: lofted hull with fin keel and rudder, cabin, mast, boom,
  main, headsail, spinnaker with pole, name label. Position and heading are keyframed from
  the GPS; heel and boom angle come from the true wind angle. The **spinnaker** is a lofted
  symmetric kite (rounded shoulders widest a third of the way up, arc cross-sections, foot
  lifting in the middle, white panel stripes in the boat's colour; shaped against photos of
  a masthead kite on a 40-footer) with its pole to the windward clew. It hoists (and the
  headsail furls) once the boat has sailed with the true wind angle at or beyond 125° for
  25 s, and drops once it has been inside 108° for 15 s, so a wobble in heading does not
  make it flash in and out (`KITE_*` constants). Yachts are drawn at `BOAT_SCALE` (4×)
  because a 10 m hull is a speck on a 3 km course; positions are exact.
- **Trails** — a wake behind each boat in its own colour, the last 150 seconds of its
  track. The whole track is one curve and the two bevel factors that decide how much of a
  curve is drawn are keyframed to a sliding window, so it costs two animated numbers rather
  than a mesh rebuilt every frame. The mapping must be `RESOLUTION`, whose name is
  misleading: it maps a factor onto the evaluated points, which for a poly spline are the
  fixes themselves, so a factor is a position in time. `SEGMENTS` maps by distance along
  the water instead, and because a boat is slowest at the start, that drew the trail *in
  front of* the boat there. The two agree only when the points are evenly spaced, so a
  straight test line will not catch it.

  A trail is a tube of a fixed radius in the **world**, not on the screen, so it is a fine
  line at a kilometre and a pipe across the frame when a chase or mark camera passes within
  a few boat lengths of it. `_fade_near_camera` dissolves it between `TRAIL_FADE_NEAR_M`
  and `TRAIL_FADE_FAR_M`, which keeps the line that carries the information and loses the
  one that only fills space. It is the mirror image of the distance haze. The course and
  finish lines are the same kind of object and get the same treatment.
- **Title and results cards** — full-frame images that dissolve rather than cut, drawn by
  the compositor (see The overlay, below). The title
  lifts off the opening aerial, which is already moving underneath it; the leaderboard
  settles over the finish. Both are drawn by `cards.py` with PIL at the film's own
  resolution: the title on the instrument ground with the club's mark, and the results as a
  broadcast timing tower, dark and staged, each boat carrying the hull colour it wore round
  the course and everyone behind the winner shown as a corrected-time gap. The result comes
  from `core.series`, so it says what the results page says. The app's own tables are
  deliberately plain because they are documents; this is the end of a film.
- **Cameras** — see below.

### The overlay

Boat names, mark numbers, the clock, the course board, the credit line and the cards are
**not in the 3D scene**. They are drawn in pixels by `overlay.py` while `compose_film.py`
assembles the film. They used to be geometry parented to whichever camera was live, and
that was wrong three ways over, all of which showed:

- the camera focuses on what it is pointed at, hundreds of metres away, so a panel sitting
  1.6 m from the lens was thrown far out of focus and the race clock rendered as a smear;
- motion blur hit the same geometry for the same reason;
- curves rasterised at whatever size the perspective gave them can never be as crisp as
  type drawn straight into the pixel grid.

Drawing them in the compositor fixes all three, costs milliseconds a frame instead of
geometry in every shot, and lets the whole overlay be restyled without re-rendering a
single 3D frame. `--overlays-3d` puts them back in the scene, which is only useful for a
one-off still.

Anything anchored to the world needs to know where the camera was pointing, and only
Blender knows that. One quick pass writes it out:

```bash
blender -b --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_69.json \
    --overlay-track runtime/replay3d/race_69_overlay.json
```

That is about 20 s for a 11,500-frame film and `render_parallel.py` does it for you if the
file is not already there (`--overlay-track` forces a rewrite). `compose_film.py --track`
picks it up, defaulting to `<export>_overlay.json`.

Boat tags are a coloured stripe, the name on a near-black plate, and a leader line down to
a dot on the hull. Plates are placed nearest boat first and pushed up out of each other's
way: at the start the whole fleet is inside a boat length of the line, and a fixed offset
per boat is not enough to keep three names apart.

The style is the app's race-document one: the instrument panel (near-black, thin rule,
  small narrow "RACE CLOCK" label, the time in IBM Plex Mono, the state in amber) and the
  course board (paper, race name in Archivo, "COURSE N" and the marks as solid red port /
  green starboard chips in Archivo Narrow), colours and faces taken from
`static/theme_race_document.css`. The exporter converts the app's woff2 fonts to TTF under
`runtime/replay3d/fonts/` (needs `pip install fonttools brotli`; SIL OFL); without them
PIL's built-in face is used.

### Branding

The film carries the club's marks the same way its start and finish videos do: the club
burgee top left at 82% opacity, one sponsor at a time top right at 90%, changing every five
seconds, to the sizes and margins in `core/video.py`. It is drawn by `overlay.py` during
compositing, so changing it needs a re-compose of about ten minutes and no re-render.

The images come from the club's **public branding manifest**, the same endpoint the
live-stream relay reads:

```bash
python scripts/replay3d/export_race.py --race 69     --branding-url https://pro.pwllhelisailingclub.org/api/branding/live
```

`https://hut-origin.pwllhelisailingclub.org/api/branding/live` answers today as well, but
do not depend on it: that hostname is the relay's own way in to the hut and the September
2026 security check flagged it as a public bypass of the relay and of every rule scoped to
`pro.*`. Once it is behind a Cloudflare Access service token it will refuse anything
without the token, so `pro` is the address to use. The manifest builds its
image URLs from whatever host the app saw, so they come back pointing at the origin over
plain HTTP; the exporter rewrites them onto the host you asked for, which keeps the fetch
encrypted and uses the one already known to answer. `rotation_seconds` comes from the
manifest too, so the film and the live stream stay in step.

It deliberately does **not** read the logos out of a hut backup. Backups are encrypted, and
nothing about making a film should need the backup key to travel to a render machine.
`--branding-dir` is there for a caller who already holds the images unencrypted and wants
to work offline; without either, the film simply carries no branding.

One wrinkle inherited from the club's own output: a sponsor supplied as a JPEG has no
transparency, so it shows as a white panel. The same is true of the start and finish
videos, because FFmpeg does the same thing to it.

### The look

The render used to read as a game, and four settings were most of the reason.

| | was | is | why |
|---|---|---|---|
| view transform | Standard | AgX + Punchy, exposure +0.5 | Standard clips every highlight to flat white. AgX rolls them off the way film does. On its own it also pulls the saturation out and the bay goes grey, which is what happened the first time it was tried; the look and the exposure put that back. |
| motion blur | off | on, 0.4 shutter | Under a 180-degree shutter because the film already runs at 30x and a full one smears the fleet into streaks. |
| depth of field | off | on, f/1.8, focused on whatever the camera tracks | Honest but nearly a no-op: every camera sits hundreds of metres from its subject, where even a wide aperture leaves the background sharp. It bites a little on the close shots and nowhere else. |
| glare | none | Bloom, strength 0.16 | What actually carries the lens, since depth of field cannot. Real glass scatters light around a highlight and a rasteriser does not. |

**Sailcloth has to transmit light.** A plain diffuse sail, backlit, measured
(166,176,184) against a sky of (168,178,188). Four levels apart, so the boats vanished
whenever the camera looked towards the sun, which is most of the interesting shots. The
camera-facing side of a backlit sail is lit by nothing except the sky, so diffuse alone
can only ever match the sky. Real cloth is thin and transmits, which is why a backlit sail
glows instead of silhouetting; mixing in a Translucent BSDF (`_cloth`) gives it the sun
from behind and it now separates in both directions. The same shader is on the spinnakers.

The sun also changed with the tonemap: its specular used to be held down to 0.3 because
under Standard the reflection clipped to a white slab, and it can now run at full strength
with the sun at its real angular size, which is what turns the reflection into a glitter
track.

`REPLAY_VIEW`, `REPLAY_LOOK`, `REPLAY_EXPOSURE`, `REPLAY_MBLUR`, `REPLAY_SHUTTER`,
`REPLAY_FSTOP`, `REPLAY_GLARE` and `REPLAY_GLARE_STRENGTH` override these for a quick A/B
without editing the script.

**Blender 5 moved the compositor.** `scene.node_tree` is gone, replaced by
`scene.compositing_node_group`, a node group wired from a Render Layers node to a **Group
Output**; there is no Composite node any more, and every Glare setting is an input socket
rather than a property. Wiring a Group Input instead of Render Layers renders black.

### Cameras

`--shots film` (default) cuts a short film with timeline markers switching the camera:

1. **overview** from seaward looking at the shore, from the start;
1b. **start** — a locked-off shot of the line, cut in `START_CUT_IN` seconds before the gun
   and held until `START_CUT_OUT` after it. The film runs at real time either side of the
   start, so that is nearly a minute and a half of screen time that used to sit on the
   opening wide shot. The camera stands beyond the line on the side of the **first mark of
   the course**, so the fleet sails towards the lens; if that side would be over land it
   falls back to the other one and they sail away instead. Taking the side from the course
   rather than from where the fleet happens to be avoids being fooled by a boat circling
   early. It uses a wide lens close in rather than a long one far out: both ends of the
   line have to be in shot, and at 28 mm from most of a kilometre back the boats are specks
   in a frame of coastline;
2. **mark N** — for **every** mark the leader rounds, in order, a camera beside that mark
   tracks the leader through the rounding, cutting in 90 s before and out 60 s after (the
   leader is the first finisher, else the boat with the most track). Each pass gets its own
   camera placed off that pass's approach line. The planner keeps cycling the course marks
   while the leader keeps passing them, so a track with more laps than the recorded course
   (a race shortened after the fact) still gets every rounding;
3. **chase** — behind and above the leader after each rounding, following its smoothed
   direction of travel so it does not inherit the heel;
4. **overview** for the middle of any leg longer than about five minutes;
5. **finish** — a wide shot from beside the finish line as the leader comes in.

`--shots overview` is the single seaward camera; `--shots follow --follow "Boat"` is the
chase camera on one boat for the whole film. Markers are ordinary Blender timeline
markers, so shots can be nudged or re-cut by hand afterwards.

The **overview is keyframed, not fixed**: at each sample it frames the boats still on the
water together with the marks at both ends of the leg the leader is on, and sits seaward of
that at whatever distance fits, so a long first leg comes in close instead of leaving the
fleet as specks on a shot of the whole course. Distance and aim are smoothed over two
minutes so it glides. The **finish camera** is placed by the same framing solver from the
line's two ends and the last 200 s of the leader's approach, beyond the line looking back
up the course so the boats sail towards it, falling back to the approach side when beyond
the line would put the camera over land (checked against the terrain grid). Both use
`_fit_distance`, which solves the required camera distance exactly rather than guessing it.

### Hut camera video, picture-in-picture

`export_race.py --videos` adds the race's start and finish clips to the export, and the
builder shows each one bottom right while the replay clock is inside the window it covers,
captioned with its kind and event time. Evidence video is deliberately left out of backups,
so a clip's file is taken from the data directory when it is there and otherwise fetched
from the public copy the app published to the club's CDN, cached under
`runtime/replay3d/video/race_<id>/`.

Two things the app already knew and this reuses rather than re-deriving:

- a clip's **file does not start at `event - pre_seconds`**. Clips are concatenated from
  whole rolling-buffer segments (twenty seconds on the hut), so the file opens at the
  segment boundary at or before the window. `core.barreplay.footage_start_ts` returns the
  recorded footage start where there is one and the middle-of-the-segment guess where there
  is not. Clips cut before v0.268 have no recorded start, so the picture can be out by up
  to half a segment: R9 Summer's are about six seconds early against the burnt-in camera
  clock. Newer clips are exact.
- **Blender indexes a movie at the frame rate it infers**, which for these 15 fps clips it
  reads as 25, so the builder takes the indexing rate from Blender's own frame count over
  the real duration rather than from the file's rate.

`--video-speed` is the playback rate relative to real time: the default `1.0` plays the
footage at natural speed centred on the event, which is the only way the moment itself is
watchable when the replay around it is running 30x; `0` makes the footage follow the replay
clock instead, fast-forwarding the whole two minutes into a few seconds. Overlapping clips
are trimmed and duplicates of the same finish dropped, so only one is ever on screen.

### Boat models

`--boat-model hull.glb` (also `.gltf`, `.fbx`, `.obj`) replaces the procedural hull with an
imported model, one linked copy per boat. The model is scaled to `LOA_M × BOAT_SCALE`,
turned so `--boat-forward` (its bow axis after import: `+Y`, `-Y`, `+X`, `-X`) points along
+Y, centred, and lowered so `--boat-waterline` (default 0.3) of its height is below the
water; a hull with a deep keel wants more like 0.6. The procedural rig (mast, boom,
sails, label) is still added, so pick a bare hull or a model whose own rig you do not mind
doubling up. An imported model keeps its own materials, so every boat is the same colour;
the name label still tells them apart. Free, licence-clean hull models exist on Poly Pizza
and Sketchfab under CC0; check the licence before dropping one in.

Verified with a hull exported from this very scene: `blender -b --python` a script that
builds once, exports one boat's hull parts to GLB at the origin, and rebuilds with
`--boat-model`. Export the model **at the origin**: the importer measures the model's
bounding box to scale it, and a hull parked a kilometre away measures a kilometre long.

Render settings are pre-set to 1920×1080 EEVEE at 16 samples, H.264 MP4 into
`runtime/replay3d/renders/`. Nothing is rendered by `build()` itself.

## Step 3 — render the film

Render in a **background** Blender rather than the one you are working in: a frame takes
about a second, so an 80-minute race at 30× is well over an hour, and an interactive
Blender (and any MCP bridge in it) is frozen for the duration.

```bash
blender -b --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_457.json --speed 30 --render
```

`--render` builds the scene and renders the whole animation; `--out PATH` overrides the
output prefix. Progress is written to `<output>.progress.log` beside the MP4 (a line at
the start, every 50 frames with an ETA, and on completion or failure), because the
Microsoft Store build of Blender can only be started through `blender-launcher.exe`,
which shows no console. From PowerShell that looks like:

```powershell
Start-Process "$env:LOCALAPPDATA\Microsoft\WindowsApps\blender-launcher.exe" -WindowStyle Hidden `
  -ArgumentList '-b','--python','scripts\replay3d\build_scene.py','--','runtime\replay3d\race_457.json','--speed','30','--render'
Get-Content runtime\replay3d\renders\race_457.progress.log -Wait
```

For a quick look before committing an hour: `--stills 600,1200,3400 --stills-dir DIR`
renders those frames as PNGs from whichever camera is live, and `--reel 3` renders the first
three seconds of every cut back to back at half resolution (a Video Sequencer scene per
shot), a few minutes for a nine-cut race:

```bash
blender -b --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_457.json --reel 3
```

### Rendering in parallel

One Blender renders frames strictly in sequence, and on the hut laptop that leaves the CPU
at 20% and the integrated GPU at 50%: the time between frames is single-threaded.
`render_parallel.py` runs several background Blenders on slices of the frame range and
joins the parts:

```bash
python scripts/replay3d/render_parallel.py runtime/replay3d/race_457.json --workers 3 --speed 30
```

Options not listed by `--help` are passed through to `build_scene.py` (`--shots`,
`--follow`, `--boat-model`, ...). `--frame-range 1-96` renders just that stretch as a smoke
test; `--keep-parts` keeps the pieces.

**Not every frame is rendered.** Where the film runs at real time the boats crawl, and the
fixes behind them are fifteen to sixty seconds apart, so the frames between are
interpolation of data nobody measured. `--slow-step` (default 5) renders every fifth frame
in those stretches and holds it, which halves the work on a film like R9 Summer:

| | frames |
|---|---|
| film | 11,502 |
| actually rendered | 5,742 |

The one thing that must not be held is the hut-camera footage: it is real video, and
stepping it turns a start into a slideshow. So the 3D renders without the panel
(the default; `--with-video` puts it back) and `compose_film.py` lays the footage over the finished
frames at the full 24 fps, holding the 3D underneath. Composing a whole film takes about
five minutes. `--no-composite` puts the panel back in the 3D, which is only sensible with
`--slow-step 1`.

The orchestrator plans the stretches, chops them into jobs sized by *rendered* frames so
the workers finish together, runs `--workers` of them at a time, and prints a status line
naming the running jobs. Measured on this laptop: removing the panel from the 3D scene
saved nothing at all (83 s for 96 frames either way), so the cost is the frame count and
nothing else.

**Watch out on integrated graphics.** Two workers roughly halve the wall time, but on this
laptop's Intel iGPU a second Blender rendering at the same time has twice crashed inside
the display driver (`igxe3icd64.dll`) — once taking the interactive session with it, once
killing a worker 50 frames into a film. The crashed process stays alive, idle and silent
rather than exiting, so `--stall-minutes` (default 6) restarts a worker that has stopped
reporting frames, once. If a render matters more than the clock, use `--workers 1`; the
risk rises with what the scene asks of the GPU, and the crash came with the video textures
in the scene.

Two Blender Video Sequencer behaviours bit here and are worth knowing: in a background
render, *scene* strips and *movie* strips both appear to return their first frame only, so
neither is used. The reel renders PNG frames and joins them with an image-sequence strip,
and the parallel join is done outside Blender.

Do not render from an interactive Blender while a background render is running. On the
hut laptop, whose Blender runs on Intel integrated graphics, that crashed the OpenGL
driver. Raytraced reflections (`--raytrace`) are off by default for the same reason.

## Terrain

The export can carry a height grid if you give it a GeoTIFF DEM covering the bay:

```bash
python scripts/replay3d/export_race.py --race 457 --dem data/dem/N52_W005.tif --dem-radius 7000 --dem-cell 60
```

Any plain geographic (lat/lon) GeoTIFF works — Copernicus GLO-30, SRTM, or OS Terrain 50
re-projected to WGS84. Cells at or below sea level are dropped just under the sea plane so
the water draws the shoreline. `--dem-radius` is the half-width of the grid in metres and
`--dem-cell` its resolution; 7 km at 60 m is 234 × 234 cells and builds in a second.

Suggested source for Pwllheli: the Copernicus GLO-30 tile `N52_00_W005_00`
(1° × 1°, 30 m, ~10 MB, free) from the public AWS bucket `copernicus-dem-30m`.
Put it under `data/dem/` (gitignored) and point `--dem` at it.

### Satellite imagery on the land: Mapbox (sharpest)

```bash
python scripts/replay3d/fetch_mapbox.py --zoom 15 --half-deg 0.065 --out data/dem/pwllheli_mapbox_z15.tif
python scripts/replay3d/export_race.py --backup HutData/<zip> --race 69 --dem <dem> --imagery data/dem/pwllheli_mapbox_z15.tif
```

Mapbox satellite at zoom 15 is 1.44 m per pixel against Sentinel-2's 10, so the marina
pontoons, the harbour arm and the beaches all read properly. 273 tiles cover a 14 km square
and fetch in about fifteen seconds; they are cached under `runtime/replay3d/mapbox_cache/`,
so re-running costs nothing and a later change of extent only downloads what is missing.

The access token is read from `--token`, then `$MAPBOX_TOKEN`, then
`runtime/replay3d/mapbox_token.txt`. That file is gitignored and the token is never written
to a tracked file. **Mapbox's terms allow caching tiles and using them in your own renders
with attribution**, unlike Google, Bing or Esri: anything published must carry
**"© Mapbox © OpenStreetMap"**. The exporter reads the credit out of the GeoTIFF's own
description and the builder prints it in the bottom-right corner of every frame, so a race
exported months later still carries the right line.

### Satellite imagery on the land: Sentinel-2 (free)

```bash
python scripts/replay3d/fetch_sentinel2.py --search                       # clear scenes over the bay
python scripts/replay3d/fetch_sentinel2.py --fetch S2B_30UVD_20260712_0_L2A --out data/dem/pwllheli_s2.tif
python scripts/replay3d/export_race.py --race 457 --dem data/dem/N52_W005.tif --imagery data/dem/pwllheli_s2.tif
```

`--imagery` takes any **lat/lon** georeferenced RGB GeoTIFF, crops it to the terrain
footprint, resamples it onto the terrain's metre grid and writes `race_<id>_imagery.png`
beside the JSON; the builder drapes it over the land mesh. `fetch_sentinel2.py` produces
such a file from Sentinel-2, the EU Copernicus satellite: 10 m pixels, free to use with
the credit "Contains modified Copernicus Sentinel data". It searches the public Earth
Search catalogue for cloud-free scenes and reads only the tiles covering the bay from the
scene's cloud-optimised GeoTIFF (a few MB, not the whole scene).

The window in use is `data/dem/pwllheli_sentinel2.tif`, from scene
`S2B_30UVD_20260712_0_L2A` (12 July 2026, cloud-free): 1800 × 1800 pixels at 10 m, 20 km
square, fetched as six tiles (13.6 MB). Anything published from the replay should carry
the credit **"Contains modified Copernicus Sentinel data 2026"**.

Google, Bing and Esri imagery are **not** usable here: their terms allow display only
inside their own map products, not texturing a 3D scene, whatever the purpose. That
includes fetching their tiles through BlenderGIS or similar add-ons. For sharper imagery
than 10 m, MapTiler and Mapbox sell satellite tiles under terms that do allow use in your
own renders with attribution.

## Tuning

Constants at the top of `build_scene.py`: `BOAT_SCALE`, `BUOY_RADIUS_M`, `LABEL_SIZE_M`,
`MAX_HEEL_DEG`, `TERRAIN_Z_SCALE`, `PALETTE`, `FPS`, `DEFAULT_SPEED`.
