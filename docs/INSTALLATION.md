# Installation and first run

## Requirements

Recommended race-office PC:

- Windows 10/11, macOS or Linux.
- Python 3.10 or newer.
- Waitress is installed from `requirements.txt` and is the default server when running `python app.py`.
- Reliable system clock, ideally NTP or GPS disciplined.
- Network access to the weather station and rating-list URLs if used.
- FFmpeg installed if video recording or live camera preview is required.
- USB serial adapter or similar interface if horn output/manual horn sensing is required.
- `pyzipper` from `requirements.txt` if the nightly off-site backup is used. It writes the AES-256 encrypted archive; without it the app refuses to run an off-site backup rather than uploading a readable one. See [`DATA_AND_BACKUP.md`](DATA_AND_BACKUP.md).


## Novice Windows guide

For a step-by-step Windows guide aimed at first-time users, including Python installation, virtual-environment setup, optional FFmpeg/video setup, restarting the app and basic troubleshooting, see [`GETTING_STARTED_WINDOWS.md`](GETTING_STARTED_WINDOWS.md).

## Race-hut PC deployment

For the hut PC, use the deployment scripts in `deploy/windows/` rather than leaving a Command Prompt open. See [`DEPLOYMENT_WINDOWS.md`](DEPLOYMENT_WINDOWS.md).

The normal deployment is a Windows Scheduled Task called **Pwllheli Race Officer**. It starts the Waitress app automatically when the race-office Windows user logs in.

## Install Python dependencies

From the unzipped project folder:

```bash
cd pwllheli_race_officer_v1_002
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

`python app.py` starts Waitress on `0.0.0.0:5050` by default. You can override this with `RO_HOST`, `RO_PORT` and `RO_THREADS` if needed.

Open the race-office/admin dashboard:

```text
http://localhost:5050/admin
```

The root page `http://localhost:5050` is the public read-only current-race page.

## First login

Default first-run login:

```text
Username: admin
Password: see runtime/initial_admin_password.txt on the race-office PC
```

If neither `RO_INITIAL_ADMIN_PASSWORD` nor an existing database user is present, the app generates a random first-run password locally and writes it to `runtime/initial_admin_password.txt`.

To set your own initial admin password before the database is first created:

```powershell
$env:RO_INITIAL_ADMIN_PASSWORD="your-password"
python app.py
```

On macOS/Linux:

```bash
export RO_INITIAL_ADMIN_PASSWORD="your-password"
python app.py
```

## Upgrade from an older version

1. Stop the old app.
2. Back up your existing `data/` folder. For older versions, also back up the root `race_officer.db` and any required `data/video/clips/` files.
3. Unzip the new version.
4. Copy your existing `data/` folder into the new project folder.
5. For older versions, you may alternatively copy the old root `race_officer.db`; the app will migrate it to `data/race_officer.db` on first run.
6. Start the app.
7. Check Settings, especially serial ports, camera names, weather station IP and FFmpeg path.

The app creates missing database columns at startup. Keep the backup until you have checked that races, boats, users and settings loaded correctly.

## FFmpeg

FFmpeg is only required for video recording and live finish-camera preview.

Windows camera listing command:

```cmd
ffmpeg -list_devices true -f dshow -i dummy
```

The app also has a USB camera dropdown and a manual camera-name override in **Settings → Video Recording**.

## Running on a club network

For other devices to view the app or public pages, run on a PC reachable on the club network and browse to that PC’s IP address on port 5050. On the race-hut PC, the Windows Scheduled Task deployment is preferred so the app restarts after login. Waitress is suitable for the race-office service; for public internet access use a reverse proxy or Cloudflare Tunnel in front of it, and set a strong `RO_SECRET_KEY`.

### Security-related environment variables

For an internet-facing deployment the defaults are already hardened: session cookies are `Secure`, `HttpOnly` and `SameSite=Lax`, security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS) are sent, request bodies are capped, and repeated failed logins are rate-limited per client IP + username.

- `RO_COOKIE_SECURE` — session cookies are `Secure` by default (they only work over HTTPS). When testing locally over plain `http://` (e.g. `http://localhost:5050`), set `RO_COOKIE_SECURE=0` or the browser will refuse to store the login cookie. Leave it unset in production behind Cloudflare/HTTPS.
- `RO_PUBLIC_BASE_URL` — the address the outside world reaches the app on, e.g. `https://pro.pwllhelisailingclub.org`. Sets the initial value of **Settings → Web server → Public address**, which is where you would normally change it. Behind a Cloudflare Tunnel the app only ever sees the tunnel's own internal hostname and a plain `http` scheme, so without this every address it builds for the outside world — the logo addresses in `/api/branding/live` that the live-stream relay downloads, and the same manifest a 3D replay render machine reads to brand a film — points at that internal name. Leave it empty on the hut LAN. Scheme and host only.
- `RO_MAX_UPLOAD_MB` — maximum request/upload size in MB (default `64`). Raise it only if restoring a very large backup ZIP that contains video clips.
- `RO_WEATHER_ALLOWED_HOSTS` — optional comma-separated allow-list restricting which hosts the weather-station poller may connect to (defence-in-depth against SSRF on an exposed box).
- `RO_DB_TIMEOUT_S` — how long a database write waits for another writer before giving up (default `30`). The database uses rollback journalling rather than WAL, so a writer excludes readers; waiting beats failing, because a finish that is not recorded is the one thing the app cannot afford to lose.

### GPS tracking environment variables (optional)

GPS tracking is normally configured in **Settings → GPS tracking**, but these environment variables provide first-run defaults (all optional; see [`TRACKING.md`](TRACKING.md)):

- `RO_TRACK_ENABLED` — set to `1` to enable GPS tracking.
- `RO_TRACCAR_BASE_URL` — the Traccar API base URL (e.g. `https://pro.pwllhelisailingclub.org/traccar`).
- `RO_TRACCAR_TOKEN` — a Traccar API token (needs device-management rights so the app can add/remove devices).
- `RO_TRACK_SIM` — set to `1` to run the built-in boat simulator (demo without hardware).
- `RO_TRACK_POLL_SECONDS` — how often to pull positions from Traccar (default `5`).
- `RO_TRACK_RETENTION_DAYS` — how long to keep stored positions (default `90`).
- `RO_TRACK_ROUNDING_RADIUS_M` — how close a boat must pass a mark to count it as rounded whatever else the geometry says (default `50`).
- `RO_TRACK_GATE_REACH_M` — how far past a mark, on the hand the course requires, still counts as rounding it (default `750`). On the wrong hand only the radius counts; that asymmetry is the only thing in the app that knows a mark has a required side.
- `RO_GPS_FINISH_HORN` — set to `1` to sound the horn on an auto-confirmed GPS finish (default off).
- `RO_TRACK_RACE_POLL` — set to `0` to stop the app asking racing boats for their position every 20 s while a race is on (default on; Queclink GL521MG only). See [`TRACKING.md`](TRACKING.md).
- `RO_HOLOGRAM_ENABLED` — set to `1` to show each tracker's SIM state and network on the Trackers page (default off).
- `RO_HOLOGRAM_API_KEY` — Hologram REST API key. Hologram keys cannot be scoped, so limit the key by limiting its owner: make a separate Hologram user with the **Editor** role rather than using an owner or admin login. Editor is confirmed sufficient — it reads SIM state, usage, plan pricing and the account balance.
- `RO_HOLOGRAM_ORG_ID` — Hologram organisation to query. Only needed when the key's owner belongs to more than one.
- `RO_TRACK_INGEST_SECRET` — shared secret for Traccar's position forwarder to POST fixes to `/api/track/ingest`. Must match `forward.header` on the relay; without it the endpoint stays closed and positions arrive only on the poll.

### On-the-water command page (optional)

The **Virtual Race Officer** at `/vro` lets somebody run racing by typing plain English when there is no race officer in the hut. Access is a **per-user permission**, not a role: tick **VRO** against the account in Settings → Users. Nobody holds it by default, administrators included.

A model is **required**, and is **set up in Settings → Virtual Race Officer** rather than by environment variable. Without one the page says the feature is not available and the command endpoints refuse: the app's own small grammar covers about six sentence shapes, and on a page that invites plain English that reads as an app understanding nothing, so it no longer answers.

The environment variables below are first-run defaults for a scripted deployment; the Settings page is the normal way in.

- `RO_ASSISTANT_API_KEY` — API key for the model that interprets commands. Empty means the Virtual Race Officer page is unavailable.
- `RO_VRO_FACTS_BUDGET_S` — how long the app may spend assembling what the interpreter is told (default `2.0` seconds). Every fact is a database read or a walk over a race, and on a hut whose track database is a season deep, with the relay writing to it while the clubhouse display and every phone read, "usually fast" is not "bounded". Whatever is not ready inside the budget is left out and the interpreter is told the list is short. Raise it only if the hut is fast and idle; lowering it makes the page answer with less rather than not answer.
- `RO_VIDEO_RTSP_TIMEOUT_S` — how long FFmpeg waits on a silent camera before giving up (default `10` seconds; `0` disables it and restores the old behaviour). Left to itself FFmpeg waits indefinitely: it stays running, holds the segment it was writing open, and records nothing, which is how a two-minute scheduled camera reboot at three in the morning cost the club the video for a whole race. Ten seconds is far longer than any gap between frames on a working camera. Raise it only for a camera on a genuinely slow link, and expect the recorder watchdog to restart the recorder shortly after the timeout fires — that is the recovery.
- `RO_ASSISTANT_MODEL` — model name (default `claude-sonnet-5`).
- `RO_ASSISTANT_BASE_URL` — the endpoint to call. Point it at a gateway (for example Cloudflare AI Gateway) to get logging, rate limiting and model fallback without an app change. If you enable caching there, key it on the whole request: "start a race at 11" means a different time tomorrow.

The model only ever chooses a command and its arguments. Every rule — the five minutes between the warning signal and the gun, whether a mark is on the course, whether a series exists — is applied afterwards in Python, and nothing happens until the read-back is confirmed.

### Hut power environment variables (optional)

Normally configured in **Settings → Hut power (Victron VE.Direct)**:

- `RO_VEDIRECT_SMARTSHUNT_PORT`, `RO_VEDIRECT_SMARTSOLAR_PORT`, `RO_VEDIRECT_PHOENIX_PORT` — serial ports for the battery monitor, solar charger and charger/inverter.
- `RO_POWER_SAMPLE_SECONDS` — how often to record a power sample.
- `RO_POWER_RETENTION_DAYS` — how long to keep power history.
- `RO_POWER_SIM` — set to `1` to run the power monitor with simulated readings (demo without hardware).

## Remote access

For Cloudflare Tunnel / off-site access notes, see [`REMOTE_ACCESS_CLOUDFLARE.md`](REMOTE_ACCESS_CLOUDFLARE.md).

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
