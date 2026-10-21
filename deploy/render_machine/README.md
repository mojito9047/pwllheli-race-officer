# The render machine — 3D race replays

The hut PC cannot make these films. It is a fanless box that also has to run the
race, and a single replay is around eleven thousand 1080p frames. So it doesn't:
it writes a **job** into the club's R2 bucket and gets on with the race day. A
render machine somewhere else picks the job up, makes the film, and puts it back
in the same bucket beside the start and finish clips it is made from.

```
   Hut PC (Windows, on the water)                Cloudflare R2
   race results page                             ┌───────────────────────────────┐
     [ Render a 3D film ] ──── job ───────────► │ replay3d/jobs/race_649.json   │
                                                 │ replay3d/status/race_649.json │◄─┐
   dashboard "3D replay" card ◄──── status ───── │                               │  │
   race page  ◄────────────────── the film ───── │ replay3d/films/race_649.mp4   │  │
                                                 └───────────────────────────────┘  │
                                                         ▲     │                    │
   The render machine (anywhere with a GPU)              │     ▼                    │
     renderer.py ── polls every 30 s ────────────────────┘  claims the job ─────────┘
       Blender × N ── frames ──► compose ── overlay ──► MP4 ──► upload
       runtime/replay3d/ ── its own tile cache ◄── Mapbox + Copernicus, on a miss
```

Nothing connects **to** the render machine and it never talks to the hut. Both
ends only read and write the bucket, so the render machine can be a desktop at
home, switched on when there is something to make, behind any router.

> **The hut and the render machine must be pointed at the same bucket.** The
> renderer's `R2_*` settings are the ones in the app under **Settings → Cloud
> storage (Cloudflare R2)**. A renderer on a different bucket sits there
> politely seeing no work, for ever.

---

## What the machine needs

**A GPU.** Not for speed — EEVEE will not start without one. It is a realtime
engine and wants a working OpenGL/Vulkan driver, so a CPU-only VPS is no good
here however many cores it has. Anything that can play a modern game will do;
this was developed on a desktop with an ordinary discrete card.

- **Disk:** about **1 GB of scratch per race** (755 MB for the three-boat R9
  Summer test, most of it intermediate JPEGs) under `runtime/replay3d/jobs/`,
  plus the cached map tiles. Give it 20 GB and don't think about it again.
- **RAM:** roughly 4 GB per Blender worker, so 8 GB for the default two.
- **Time:** a frame is around 0.6–0.8 s at 1080p, 8 samples. R9 Summer is 11,502
  frames — eight minutes of film — so **an hour or so on two workers**. More
  workers, proportionally less; `RENDER_WORKERS` is the dial.
- **Network:** it downloads the hut's start and finish clips and uploads the
  film. No inbound anything.

Windows is the tested platform, because it is what the films to date were made
on. The Linux unit file below is written and reviewed but has not yet had a race
put through it — say so to yourself before promising anyone a film.

---

## 1. Install

**Blender 5.2 LTS** — <https://www.blender.org/download/lts/>. Either the
installer or the portable ZIP; the Microsoft Store build works too. Check it
runs headless, because that is the only way the renderer ever calls it:

```bash
blender --version
```

**The app.** Unzip the same release ZIP the clubhouse PC runs — the renderer
ships inside it. No git, and no separate build for a render machine.

On **Windows**, unzip `pwllheli_race_officer_v<version>.zip` and run one script
from that folder:

```powershell
.\deploy\render_machine\setup_render_machine.cmd
```

It makes the virtual environment, installs what the renderer needs, puts a
`renderer.env` in place from the example, and then tells you what is still
missing. Safe to run again — it skips whatever is already done, so it is also
how you pick up a new release's dependencies after an upgrade.

On **Linux**, the same steps by hand. The systemd unit expects the folder at
`/opt/pwllheli`, so it is renamed on the way in:

```bash
sudo unzip pwllheli_race_officer_v<version>.zip -d /opt
sudo mv /opt/pwllheli_race_officer_v<version> /opt/pwllheli && cd /opt/pwllheli
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r deploy/render_machine/requirements.txt
```

The renderer imports exactly two modules from `core/` — the S3 signer and the
bucket contract it shares with the hut. It opens no database, starts no web
server, and never reads the club's course, mark or start-line files: all of that
is already baked into the job it is handed. The rest of the folder simply sits
there unused.

## 2. Configure

```bash
cp deploy/render_machine/renderer.env.example deploy/render_machine/renderer.env
```

Fill in the five `R2_*` values from the app's **Settings → Cloud storage** section and a
`MAPBOX_TOKEN`. Every setting is commented in the file. It holds a secret key:
`chmod 600` it, and don't commit it. On Linux the folder belongs to root, so copying,
editing and `chmod` all want `sudo`.

**On the hut, check Settings → Web server → "Public address" is set.** That is
where the render machine reads the club's logos from, so the film carries the
same club mark and rotating sponsors as the start and finish videos. Unset, the
film still renders — just unbranded, and the log says so. (`MANIFEST_URL` in
`renderer.env` can name the logo list for a hut that has no public address; the
render machine has no club's built in.)

## 3. Prove it before trusting it

Uncomment `RENDER_FRAME_RANGE=1-120` in `renderer.env`. That renders five
seconds instead of eight minutes, so a wrong secret key or a missing font shows
up in ten minutes rather than at the end of an hour.

`run_renderer.ps1` has no `.cmd` beside it, so pass PowerShell the execution
policy yourself, as the `.cmd` files do for the others:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\render_machine\run_renderer.ps1 -Check
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\render_machine\run_renderer.ps1 -Once
```

On Linux there is no separate check: do step 4 with the line uncommented and follow the
service's log (`journalctl -u pwllheli-renderer -f`). The service reads
`/etc/pwllheli/renderer.env` only when it starts, so commenting the line out afterwards
means editing that copy and `sudo systemctl restart pwllheli-renderer`.

Then queue a race: on the app's **race results** tab, **Render a 3D film**. The
renderer claims it within thirty seconds and the dashboard's **3D replay** card
follows it. What a good run says:

```
renderer 'RENDERBOX' watching pwllheli-video/replay3d/jobs (blender: blender.exe)

=== race 649: R9 Summer
  fonts: 4 ready
  land for race 649
  elevation: 1 tile(s) {'disk': 1, 'source': 0}
  imagery: 380 tile(s) {'disk': 380, 'source': 0}
  far land: 9 km out, 240 m cells, elevation 1 tile(s) {'disk': 1, 'source': 0}
  far imagery: 49 tile(s) at zoom 13 {'disk': 0, 'source': 49}
  branding: 3 sponsor(s) from the hut api
  2 clip(s) downloaded
  cards: title, results
  $ prepare_video_frames.py --json race_649.json --speed 30.0
  NOTE: RENDER_FRAME_RANGE=1-120, this is a smoke test and not the whole film
  $ render_parallel.py race_649.json --workers 2 --slow-step 5 ...
  overlay track 2000/11502 (17.4%)
=== done in 9 min -> https://videos.pwllhelisailingclub.org/replay3d/films/race_649.mp4?v=1760997600
```

`disk` is tiles this machine already held and `source` is tiles it fetched; the
numbers above are for a course reaching about a kilometre from its centre.

**The overlay track is the one stage that looks like a hang.** Before any frame
is drawn, one Blender process walks the whole film working out where every boat
and mark lands on screen — only Blender knows where the camera was pointing.
It renders nothing, so it holds a single core and leaves the graphics card
idle, and on a full-length film that is minutes. It counts itself off every two
thousand frames. It is kept between runs and only rewritten when the race's
data has changed, so most re-renders skip it entirely.

Two lines to read carefully, because both fail quietly and only show up in the
finished film: **`branding:`** — "hut api unreachable" means no logos; and
a line starting **`FONTS:`**, which means the clock and the leaderboard came
out in a default bitmap face. Neither stops the render.

When that film plays, **comment `RENDER_FRAME_RANGE` out again**. The five-second
film went into the bucket as that race's film, and the public pages link to it, so
press **Render it again** on the same race once the line is out.

## 4. Leave it running

**Linux:**

```bash
sudo useradd --system --home /var/lib/pwllheli-render -g render render
sudo install -d -o render /opt/pwllheli/runtime
sudo install -d -m 700 -o render /etc/pwllheli
sudo install -m 600 -o render deploy/render_machine/renderer.env /etc/pwllheli/renderer.env
sudo cp deploy/render_machine/pwllheli-renderer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pwllheli-renderer
```

On Debian and Ubuntu a `render` group already exists, for the graphics card's
devices, and `-g render` puts the user in it; where there is no such group, leave
`-g render` off and `useradd` makes one. `runtime/` is the only part of
`/opt/pwllheli` the renderer writes to, so it is the only part the user owns.

```bash
journalctl -u pwllheli-renderer -f
```

**Windows:**

```powershell
.\deploy\render_machine\install_render_task.cmd
```

That registers a Scheduled Task which starts the renderer at every logon and
restarts it if it stops. It refuses to install if the check fails, because a
task that cannot render only hides the problem.

**At logon, as the signed-in user — not "whether logged on or not".** EEVEE is a
realtime engine and wants a working graphics driver in a real desktop session; a
task set to run without a logon gets a session with no GPU and fails at the
first frame. It is the same reason the hut's own task runs in the signed-in
session, there for its audio and serial hardware.

```powershell
.\deploy\render_machine\status_render_task.cmd
```

```powershell
.\deploy\render_machine\uninstall_render_task.cmd
```

`status` reports the task **and** what the club's bucket says, which is the
honest half: the task can be running perfectly while the renderer is pointed at
the wrong bucket and seeing no work. It ages the heartbeat, so a machine that
stopped mid-render says so rather than still claiming to be working on a race.

Either way the dashboard card is the thing to look at, not the log: it says
whether a render machine has checked in at all. A job queued against a machine
that is switched off looks exactly like one being worked on.

---

## The pieces (files in this folder)

Each `.ps1` except `run_renderer.ps1` has a `.cmd` beside it, so they can be
double-clicked as well as run from a prompt — the same pairing as `deploy/windows`.

| File | What it is |
| --- | --- |
| `setup_render_machine.ps1` | One-time setup: virtual environment, packages, settings file, then what is still missing. Re-runnable, and how you pick up a new release's dependencies. |
| `install_render_task.ps1` | Register the Scheduled Task that keeps the renderer running. Checks the machine first. |
| `status_render_task.ps1` | What the task is doing, and what the bucket says about it. |
| `uninstall_render_task.ps1` | Stop it and remove the task. Keeps the settings and the cached tiles. |
| `run_renderer.ps1` | The runner the task calls. `-Check`, `-Once`, or the loop. |
| `render_status.py` | The bucket half of `status`. A file rather than a one-liner, because python source passed through PowerShell to a native command loses its quotes. |
| `renderer.env.example` | Every setting, commented. Copy to `renderer.env`. |
| `requirements.txt` | The render side's Python packages, and why each is there. |
| `pwllheli-renderer.service` | Linux: the systemd unit. |

The code is in `scripts/replay3d/`: `renderer.py` is the loop, `assets.py` the
map-tile cache, `build_scene.py` the Blender scene, `render_parallel.py` the
frame farm, `compose_film.py` and `overlay.py` the film and what is drawn on it.

---

## Troubleshooting

**Nothing ever happens.** Almost always the bucket. Check `R2_BUCKET` against
the app's Settings → Cloud storage, character for character — an app writing jobs into
`pwllheli-video-test` and a renderer watching `pwllheli-video` is silent at both
ends, which is the confusing part.

**`missing environment: R2_...`** — the env file was not loaded. Under systemd
check `EnvironmentFile=`; in a shell remember `set -a` before sourcing it.

**Blender exits immediately, or "Unable to open a display" / EGL errors.** No
usable GPU. On a headless Linux box install the vendor driver and make sure
`libEGL` is present; in a VM, pass a GPU through. There is no software fallback
for EEVEE.

**`PermissionError: [WinError 5] Access is denied` when a job starts rendering.**
Windows saying "that is not something I can run", not a permissions problem.
Two things cause it, and both look identical: `BLENDER_BIN` pointing at the
install *folder* rather than `blender.exe` inside it, and a dead App Execution
Alias at `%LOCALAPPDATA%\Microsoft\WindowsApps\blender-launcher.exe` — a
zero-byte stub that exists whether or not the Store app behind it does.
`run_renderer.ps1 -Check` now resolves Blender exactly the way a render does,
by running it, so it catches both before a job is claimed.

**The film has no land, or plain land.** The `land for race N` step said why. No
land at all means no elevation tiles: Copernicus could not be reached (`land:
no elevation tiles` or `land: skipped`). Land in plain height colours instead of
satellite imagery is usually `MAPBOX_TOKEN`. Without a token the renderer drapes
only imagery it already has cached; the detailed square or the far ring that
would need even one tile fetched says `imagery: skipped` or `far imagery:
skipped` and comes out in height colours, and the film is still made. Once the
tiles are in this machine's own cache under `runtime/replay3d/` it renders that
water offline. The cache is worth keeping if you ever move the checkout; losing
it costs one job's downloads.

**The film is set in the wrong typeface.** `fontTools` or `brotli` missing, so
the woff2 faces could not be converted and PIL fell back to its bitmap font.
Reinstall from `requirements.txt`.

**A job renders twice, or two machines fight over one.** Only run one renderer
per bucket. A claim goes stale once the race's status has gone ten minutes
without an update, so another machine can rescue an abandoned job.

**The upload fails.** The upload is tried three times, ten and then twenty
seconds apart, which covers the DNS blip that once threw away a finished
two-hour render. If all three fail the status says `failed`, but the finished
film is still on disk under `runtime/replay3d/jobs/race_N/renders/`. Copy it out
quickly: the job is still in the bucket, so the renderer takes it straight back
and renders the race from the start, replacing that file when it finishes.
