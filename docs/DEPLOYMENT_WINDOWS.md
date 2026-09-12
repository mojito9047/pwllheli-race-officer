# Deploying on the race-hut Windows PC

This is the recommended deployment for the start-hut/race-office PC. It keeps the app available without needing to leave a Command Prompt open.

The deployment uses:

- Waitress, started by `python app.py`.
- A Windows Scheduled Task named **Pwllheli Race Officer**.
- The signed-in Windows race-office user, not a background Windows service.


## Desktop window behaviour

The automatic Scheduled Task runs hidden. After the race-office user logs in, the app should be available at `http://localhost:5050/admin` but there should not be a black Command Prompt window left open on the desktop.

The manual script `deploy\windows\start_race_officer.cmd` still opens a visible console window. That is intentional and is useful for diagnostics when you want to see startup messages directly.

If a command window remains open after login, reinstall the task from the new scripts:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_003\deploy\windows
uninstall_startup_task.cmd
install_startup_task.cmd
```

Running inside the signed-in user session is deliberate. The app uses desktop audio for VHF announcements, USB/serial hardware for the horn interface and optional camera devices for video. Those devices are usually easier to access reliably from the race-office user session than from a Windows service account.

## Recommended folder

Use a simple folder such as:

```text
C:\RaceOfficer\pwllheli_race_officer_v1_003
```

Avoid running the live app directly from Downloads, OneDrive sync folders or a ZIP viewer.

## First install

1. Copy or unzip the project onto the race-hut PC.
2. Open the project folder in File Explorer.
3. Open:

```text
deploy\windows
```

4. Double-click:

```text
install_startup_task.cmd
```

The installer will:

- create `.venv` if needed,
- install Python requirements when needed,
- create or update the **Pwllheli Race Officer** scheduled task,
- start the task.

Open the race-office dashboard on the hut PC:

```text
http://localhost:5050/admin
```

The public competitor page on the hut PC is:

```text
http://localhost:5050
```

With Cloudflare Tunnel configured, competitors can use the public hostname, for example:

```text
https://pro.pwllhelisailingclub.org
```

Race officers should use:

```text
https://pro.pwllhelisailingclub.org/admin
```

## Auto-start behaviour

The scheduled task starts when the configured Windows race-office user logs in.

For a race-hut PC that should recover after a reboot, configure Windows so that the race-office user logs in automatically after power-up, or make logging in part of the race-day startup checklist. Once the user logs in, the scheduled task starts the app.

This is preferred to running the app as a Windows service because a service can have problems with SAPI speech, interactive audio devices, USB cameras and serial adapters.

## Pip cache warnings during install

If the installer shows a warning like:

```text
WARNING: Cache entry deserialization failed, entry ignored
```

that is normally harmless. It means pip ignored a stale local download-cache entry. From v0.77, the deployment script installs with `--no-cache-dir` and logs this kind of native output as normal text, while still stopping if pip returns a real non-zero exit code.

## Check status

Run:

```text
deploy\windows\status_startup_task.cmd
```

This shows:

- scheduled task state,
- last run time,
- last result code,
- local admin/public URLs,
- whether port 5050 is responding,
- the last lines from the app log.

The main log file is:

```text
runtime\logs\race_officer.log
```

## Restart after an update

After replacing the app files or changing Python dependencies, run:

```text
deploy\windows\restart_startup_task.cmd
```

You can also restart from Windows Task Scheduler by stopping and starting the **Pwllheli Race Officer** task.

## Remove the startup task

Run:

```text
deploy\windows\uninstall_startup_task.cmd
```

This removes the scheduled task but does not delete the app folder or any race data.

## Upgrading to a new version

1. Stop the app or run `uninstall_startup_task.cmd` if you are replacing the folder.
2. Back up the old `data\` folder.
3. Unzip the new version to a new folder.
4. Copy the old `data\` folder into the new version folder.
5. Run `deploy\windows\install_startup_task.cmd` from the new version.
6. Open `http://localhost:5050/admin` and check Settings, races, boats, horn, weather and video.

After a version upgrade, existing race-office browser sessions are treated as stale and users must log in again. A normal restart without changing version should not force a login.

The startup task points at the folder it was installed from, so run the installer again after moving to a new version folder.

## Running a past race again

Useful for showing the clubhouse display, the start camera window or the moving
chart to somebody on a weekday, without waiting for a Saturday. It makes a **new**
race starting in a few minutes and feeds a recorded race's own fixes back in on
the clock. The original race is never touched, and the boats and trackers it
creates are its own, so a real tracker reporting at the same time cannot collide
with it.

`scripts\rerun_race.py` is a development tool and is **not** in the release ZIP —
copy it onto the hut PC alongside the app if you want it there.

Run it with the app's own Python, not a bare `python`:

```text
.venv\Scripts\python.exe scripts\rerun_race.py --list
.venv\Scripts\python.exe scripts\rerun_race.py --source 57 --speed 3
```

A bare `python` is a different interpreter with none of the app's requirements
installed, and fails with `ModuleNotFoundError: No module named 'flask'`. The
script now catches that and prints the command to use instead.

Leave the app running while it plays — that is the point, since the display and
the competitor pages read the race as it goes. `--speed 3` plays three times
faster and scales the reported boat speeds to match, so the camera windows still
open at the right moment. Ctrl-C stops it; `--clean-up` removes the race it made
when it ends, and `--purge` removes every race a previous run left behind.

## Backup reminder

Use the app's **Backup / restore** page, or back up the whole `data\` folder. It contains **three** databases — `race_officer.db` (races, entries, boats, settings, users), `track_positions.db` (recorded GPS tracks) and `power_history.db` (hut battery/solar history) — plus course/mark files, polars, per-polar sail charts, default sail chart and saved video clips.

The `runtime\` folder contains logs, caches, live preview frames and the rolling video buffer. It is useful for troubleshooting but does not need to be part of the normal race-data backup.

## Large-screen split view

For a large hut-PC monitor, create a desktop shortcut to:

```text
http://localhost:5050/split
```

The split page shows the authenticated race-office app on the left 75% and the public competitor page on the right 25%. It is intentionally not shown in the normal race-office sidebar, so it can be used only on the hut-PC shortcut where it makes sense.

## Cloudflare Tunnel

The tunnel should point the whole hostname to:

```text
http://localhost:5050
```

Leave the optional path blank. Do not point only `/public` at the app, otherwise `/static/...` CSS, JavaScript, images and flags will not load.

## Troubleshooting

If the app does not open:

1. Run `deploy\windows\status_startup_task.cmd`.
2. Open `runtime\logs\race_officer.log`.
3. Check that Python 3.10+ is installed.
4. Check that no other program is using port 5050.
5. Check Windows Firewall if other devices on the club network cannot connect.

If the public page looks like plain text through Cloudflare, hard-refresh the browser and check that the tunnel is forwarding the whole hostname, not only `/public`.

### Installer error: RunLevel LeastPrivilege

If you see an error similar to:

```text
Cannot convert value "LeastPrivilege" to type ... RunLevelEnum
Specify one of the following enumerator names: Limited, Highest
```

use v0.75 or later. The scheduled task should use the valid PowerShell run level `Limited`. This is the intended setting; it runs the app in the signed-in race-office user's normal session rather than as an elevated administrator task.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
