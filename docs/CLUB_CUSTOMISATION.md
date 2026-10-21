# Setting the app up for another club

The app was written for Pwllheli Sailing Club, and Pwllheli's marks, line, name and
logos are its defaults. Everything that belongs to a club rather than to the
software can be replaced. This guide is the list, in the order a new club needs it.

The software keeps its own name, **Pwllheli Race Officer**, in every footer and in the
copyright line, wherever it runs. That is the program's name, like the name of any
other application, not a claim about which club is using it.

## 1. The club's water: three files in `data/`

These describe where the club races. Replace all three; nothing else needs editing.

### `marks.json`: the marks

One entry per mark, keyed by the code the club uses on its course boards (`O`, `4`,
`PLK`, `G11`…):

```json
"PLK": {
  "name": "Pluckington",
  "lat": 53.3838333333,
  "lon": -2.9926666667,
  "lat_text": "53° 23.03'N",
  "lon_text": "002° 59.56'W",
  "buoy": "Yellow special",
  "notes": ""
}
```

- **The name is what the race office hears.** A course announced on the VHF says each
  lettered mark by its name ("Pluckington to port"); a numbered mark is "mark four".
  A mark whose name is only its code again is announced as "mark G11".
- **Compound marks** (several rocks rounded as one) and **waypoints** (a point that
  bends a leg round a headland without being rounded) are described in
  [COMPOUND_MARKS.md](COMPOUND_MARKS.md) and [WAYPOINTS.md](WAYPOINTS.md).
- Marks can also be added, moved and re-measured from the **Marks** page once the app
  is running, and a mark's position can be set from a phone alongside it.

### `courses.json`: fixed courses

The club's course card, each course a list of marks with a rounding side, and the
wind band it is set for. A club that sets every course on the day can leave the list
empty: every race is then a **made-up course**, built on the race page or suggested
by the Virtual Race Officer for the wind and the time available.

### `start_finish.json`: the line

The line is where every course is measured from, so the app needs to know both ends.

```json
"start_line": {
  "seaward_end_mark": "PLK",
  "shore_end": "Brunswick lock house flagstaff",
  "shore_end_position": "53° 23.2225'N 002° 59.0625'W"
},
"finish_lines": [{
  "key": "lyc",
  "name": "LYC line",
  "default": true,
  "seaward": {"mark": "PLK", "label": "Pluckington (PLK)"},
  "seaward_extension_m": 100,
  "shore": {"lat": 53.3870418, "lon": -2.9843753,
            "label": "Brunswick lock house flagstaff", "short_label": "Lock house"}
}]
```

- **`start_line`** gives the start mark (`seaward_end_mark`, a code from `marks.json`)
  and the shore point, written the way the sailing instructions write it. Courses,
  made-up course suggestions, every course chart, the forecast and the 3D replay's
  start line all come from here.
- **`finish_lines`** lists every line a race can finish on. The one marked
  `"default": true` is the club line. Each end is either a mark (it then follows the
  mark when it is re-measured) or a fixed position. `seaward_extension_m` lets a
  boat that passes inside a buoy that has swung still be finished
  ([TRACKING.md](TRACKING.md) explains why).
- **`short_label`** is what the chart prints at the shore end ("Bridge" at
  Pwllheli). Without it the chart uses `label`.

More on the file in [DATA_AND_BACKUP.md](DATA_AND_BACKUP.md).

## 2. Settings → Club name and branding

The first section after **Users**. Changes here show everywhere at once.

- **Club name** (in full): on the public pages, the 3D replay films and published
  results.
- **Name before "Race Officer"**: the word at the top of the race office's pages and
  on every browser tab. "Liverpool" makes it *Liverpool Race Officer*.
- **Club logo**: the side menu of the race office's pages, the public pages, the
  top-left of public videos and the live image, the start photo, the film's title
  card and published results. Upload the **colour** version, because it is shown on
  light pages as well as over the camera picture. Until one is uploaded the bundled
  Pwllheli burgee is used.
- **Public page header**: a banner across the top of the public pages and the bar
  TV, about 820 × 74 pixels. Without one the club logo is shown beside the club name.
  (Pwllheli's own banner is shown only while the club name is still Pwllheli Sailing
  Club.)
- **Sponsor logos**: rotated one at a time on public videos and the live image, all
  shown together on published results.

## 3. Settings → Web server: the public address

The address competitors use, such as `https://race.yourclub.org`. It is not only for
links: the 3D replay render machine and the live-stream relay read the club's logos
from it, and without it the films come out with no logos at all. See
[REMOTE_ACCESS_CLOUDFLARE.md](REMOTE_ACCESS_CLOUDFLARE.md) for publishing the app
through a Cloudflare Tunnel.

## 4. Settings → Cloud storage (Cloudflare R2)

One public bucket holds everything the club publishes: public video copies, the live
image, start photos, 3D films and published results. **Off-site backup** uses the
same account and keys with a second, private bucket. Both are covered in
[SETTINGS_AND_ADMIN.md](SETTINGS_AND_ADMIN.md).

## 5. Boats and ratings

- **Settings → Rating lookup sources**: the IRC listing is the RORC's national one;
  the YTC listing is a Google Sheet. Check the club's boats are in them, or point
  them at the club's own.
- **Boats**: import from those listings, or add boats by hand.
- **Settings → Polars**: the speed files course recommendation and leg analysis use.
  The bundled set covers common designs; add the club's own.

## 6. The hut: horn, wind, camera and power

All in Settings, and all specific to the club's own kit:

- **Horn settings & Test**: the serial port of the horn relay and the hut's audio.
- **Weather Station**: the address of the club's station, or manual wind.
- **Video Recording**: the camera's RTSP address and its zoom presets. The start
  photo uses the same camera login.
- **The start line drawn on videos and the start photo** finds the seaward buoy by
  colour and draws from a fixed point in the camera's picture. Both are set for
  Pwllheli's camera in `data/startline_config.json`, and a club drawing its line
  needs its own (see [VIDEO_RECORDING.md](VIDEO_RECORDING.md)). The option is off by
  default.
- **Hut power** is only for a hut on Victron solar kit.

## 7. GPS tracking, the relay and the live stream

- **GPS tracking** needs a Traccar server for the club's trackers ([TRACKING.md](TRACKING.md),
  [TRACKERS.md](TRACKERS.md)).
- **The relay** (`deploy/live_stream`) is the one machine competitors reach the club
  through, and carries the live camera stream and Traccar. Pwllheli's copy names
  Pwllheli's hostnames; a club building its own sets its own in the Caddyfile and
  its `MANIFEST_URL` to its own public address. The
  [relay guide](../deploy/live_stream/README.md) walks through it.

## 8. 3D replay films

The render machine ([its guide](../deploy/render_machine/README.md)) needs the same
bucket as **Cloud storage**, and its own Mapbox token. It needs nothing about the
club's water: each job names where the race was, and the render machine fetches the
land and the satellite picture for that area itself. A club whose hut has no public
address can name its logo list on the render machine with `MANIFEST_URL`; otherwise
the films carry no logos. There is no built-in club.

## Still Pwllheli's

What a new club will still see, and why:

- **The software's name** in the footers, by design.
- **The guides** (the PDFs and much of this documentation) describe Pwllheli's water,
  marks and kit as their worked example.
- **The mark picker's order** puts Pwllheli's lettered marks (O, F, C, E, T, M, Y, A)
  straight after the numbers. Any other lettered marks follow, in code order.
- **The bundled burgee and banner**, until the club's own are uploaded.

## A checklist

1. `data/marks.json`, `data/courses.json` and `data/start_finish.json` for the club's water.
2. Settings → Club name and branding: name, the word before "Race Officer", the colour logo, a header banner if wanted.
3. Settings → Web server: the public address.
4. Settings → Cloud storage: the public bucket. Then Off-site backup: the private one.
5. Rating listings, boats and polars.
6. Horn, weather station and camera.
7. Optional: GPS tracking, the relay, the render machine.
