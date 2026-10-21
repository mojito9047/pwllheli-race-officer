# Installing and running on the hut PC (Windows)

This is the one guide for putting the app on the race-office PC in the start hut, a Windows 10 or Windows 11 PC. It assumes no experience of Python or the command line.

There is one route, and it is short: install Python once, unzip the release, run the installer. The installer does everything else. It sets the app up and starts it, and from then on Windows starts it whenever the race-office user logs in, hidden, with no window left open on the desktop.

Running the app by hand from Command Prompt is near the end. It is for testing and diagnosis, not for race days.

## What you need

- A Windows 10 or Windows 11 PC, with the Windows user the race office signs in as.
- The release ZIP, for example `pwllheli_race_officer_v1_019.zip`, from the project's GitHub releases page.
- Internet access for the first installation: the installer downloads the Python packages the app needs.
- FFmpeg only for video recording and the live finish-camera preview (step 8).
- A USB serial horn interface only if the app is to sound the horn.

You do not need FFmpeg to set up races, run starts and finishes, score results or show the competitor pages.

## 1. Install Python (once per PC)

1. Download the latest stable **Windows installer (64-bit)** from `https://www.python.org/downloads/windows/`. Any Python from 3.10 on will do.
2. Run it. On the first screen tick **Add python.exe to PATH**, then click **Install Now**.
3. If it offers **Disable path length limit**, click it.
4. Check it worked. Click **Start**, type `cmd`, open **Command Prompt** and type:

```cmd
py --version
```

It should answer with a version, for example `Python 3.13.x`. If `py` is not recognised, close Command Prompt and open a new one. If it is still not recognised, run the Python installer again with **Add python.exe to PATH** ticked.

## 2. Unzip the app

1. In File Explorer, open `Local Disk (C:)` and create a folder called `C:\RaceOfficer`.
2. Put the release ZIP in it, right-click the ZIP, choose **Extract All...** and extract into `C:\RaceOfficer`.

You should then have a folder like:

```text
C:\RaceOfficer\pwllheli_race_officer_v1_019
```

The number is the version. Each version unzips into a folder of its own, and an upgrade puts the next one beside it.

Do not run the app from inside the ZIP, from Downloads, from an email attachment or from a OneDrive folder. It keeps its database in its own folder, so it needs an ordinary folder it can write to.

## 3. Run the installer

In Command Prompt, as the Windows user the race office signs in as:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_019\deploy\windows
install_startup_task.cmd
```

Double-clicking `install_startup_task.cmd` in File Explorer does the same, but its window closes the moment it finishes and takes any error message with it.

The installer:

- creates the app's own Python environment, `.venv`, inside the app folder;
- downloads and installs the packages the app needs. The first time, this takes a minute or two and prints a great deal; that is normal;
- creates a Windows Scheduled Task called **Pwllheli Race Officer**, which starts the app hidden whenever this Windows user logs in;
- starts it now.

It ends with:

```text
Installed. Admin page: http://localhost:5050/admin
Public page: http://localhost:5050
Log file: C:\RaceOfficer\pwllheli_race_officer_v1_019\runtime\logs\race_officer.log
```

The task belongs to the user who ran the installer and starts at that user's login, so run it as the race-office user, not as somebody else.

## 4. First login

Give the app a few seconds to start, then open this in a browser on the hut PC:

```text
http://localhost:5050/admin
```

```text
Username: admin
Password: the one in runtime\initial_admin_password.txt, inside the app folder
```

The app makes that password up on its first start. Change it straight away: **Settings → Users**, to something the race-office team will know.

`http://localhost:5050`, without `/admin`, is the public read-only current-race page.

## 5. Make it come back after a power cut

The task starts the app when the race-office user logs in, not when the PC is switched on. For a hut PC that should recover by itself, set Windows to log that user in automatically at power-up, or make logging in part of the race-day checklist. If the app stops on its own, the task restarts it, up to three times a minute apart.

It is a task in the signed-in user's session rather than a Windows service on purpose. The app speaks VHF announcements through the desktop audio, drives the horn through a USB serial adapter and can record from a USB camera, and all three work reliably from the signed-in user's session and badly from a service account. For the same reason the task runs with the user's ordinary rights (`Limited`), not as an administrator.

## 6. Windows Defender exclusions

Real-time scanning inspects every write the app makes: each 20-second video segment the recorder writes, and every SQLite commit — several a second while trackers are reporting. Excluding the app's **data** folders from scanning is the biggest Windows-side saving there is. Exclude data only, never the app code or the Python install: those are where a program could actually land, and the hut is reachable through the Cloudflare tunnel.

Each version unzips into a folder of its own, so an exclusion that names one version's folder covers nothing once the next is installed. The hut's still named version 0.257's folder in October 2026, so every version installed since August had been scanned in full. Use a `*` for the version folder, so every version is covered — it stands for any one folder name, and each exclusion includes everything inside it. In PowerShell opened as administrator:

```powershell
Add-MpPreference -ExclusionPath 'C:\RaceOfficer\*\data', 'C:\RaceOfficer\*\runtime\video'
```

Check them with `deploy\windows\check_hut_pc.ps1`, run as administrator from the installed version's folder: its *Windows Defender* section lists the exclusions, says which of the app's data paths are not covered, and flags one that names a folder no longer there. Remove a stale one with `Remove-MpPreference -ExclusionPath '<the path it lists>'`.

## 7. Other devices: the club network and the internet

**On the club network**, another device reaches the hut PC by its IP address instead of `localhost`. Find it with `ipconfig` in Command Prompt (the active adapter's **IPv4 Address**, for example `192.168.1.25`), then browse to:

```text
http://192.168.1.25:5050
```

When Windows asks whether to let Python through the firewall, allow it on **Private networks** only, and only if this is the club's own trusted network.

**The public page works over the club network, but signing in will not.** Login cookies are marked `Secure`, and browsers only keep those over HTTPS — so at `http://192.168.1.25:5050/admin` you can enter the right password and simply be returned to the login page. Do race-office work on the hut PC itself, or over HTTPS through the Cloudflare Tunnel. On a trusted club network only, the app can be run by hand with `RO_COOKIE_SECURE=0` (see *Running it by hand*).

**From the internet**, competitors and race officers come in through the Cloudflare Tunnel and the club's relay, for example `https://pro.pwllhelisailingclub.org` and `https://pro.pwllhelisailingclub.org/admin`. The tunnel must send the **whole hostname** to `http://localhost:5050`, with the optional path left blank: pointing only `/public` at the app leaves `/static/...` behind, and the pages arrive without their CSS, JavaScript, images and flags. Setting the tunnel up is in [`REMOTE_ACCESS_CLOUDFLARE.md`](REMOTE_ACCESS_CLOUDFLARE.md).

## 8. Optional: FFmpeg and video

FFmpeg is only needed for video recording and the live finish-camera preview.

**With Winget**, which most Windows 10 and 11 PCs have (check with `winget --version`):

```cmd
winget install -e --id Gyan.FFmpeg
```

**By hand**, if Winget is not there or does not work:

1. From `https://ffmpeg.org/download.html`, choose **Windows**, pick one of the linked builds and download a release build ZIP. Any normal build with `ffmpeg.exe` will do.
2. Extract it so that this file exists: `C:\ffmpeg\bin\ffmpeg.exe`.
3. Add `C:\ffmpeg\bin` to the PATH: **Start** → *Edit the system environment variables* → **Environment Variables...** → under **User variables** select **Path** → **Edit** → **New** → `C:\ffmpeg\bin` → **OK** on every window.

Either way, open a **new** Command Prompt (Windows only updates the PATH for new windows) and check:

```cmd
ffmpeg -version
```

Then, in the app, open **Settings → Video Recording**:

1. Enable video recording if required.
2. Choose the source: **USB webcam** for a directly connected camera, **RTSP stream** for a network camera.
3. For a Hikvision or other IP camera, use the main stream as the recording URL (for example channel `101`), select **Camera stream copy**, and optionally enter the sub-stream as the preview URL (for example channel `102`).
4. Save.

If the app cannot find a USB camera, list them with the command below and type the name exactly into **Settings → Video Recording**:

```cmd
ffmpeg -list_devices true -f dshow -i dummy
```

If the app cannot find FFmpeg itself, set its path in the same section to `C:\ffmpeg\bin\ffmpeg.exe`.

The task started before FFmpeg was installed, so restart it once afterwards to give the app the new PATH: `deploy\windows\restart_startup_task.cmd`.

## Day to day

### Is it running?

```cmd
deploy\windows\status_startup_task.cmd
```

It shows the task's state, when it last ran and how that ended, the admin and public addresses, whether port 5050 is answering, and the last lines of the app's log. The log itself is `runtime\logs\race_officer.log`.

### Restarting it

```cmd
deploy\windows\restart_startup_task.cmd
```

Use it after a change that needs a restart, or if the app has stopped answering. You can also stop and start the **Pwllheli Race Officer** task in Windows Task Scheduler. It is **not** the step after an upgrade: see *Upgrading* below.

### A black window left open after login

The task runs hidden. If a Command Prompt window stays open after login, the task was installed by an old script; reinstall it from the installed version's folder:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_019\deploy\windows
uninstall_startup_task.cmd
install_startup_task.cmd
```

### Large-screen split view

For a large hut-PC monitor, make a desktop shortcut to `http://localhost:5050/split`. It shows the race-office app on the left three quarters and the public competitor page on the right. It is deliberately not in the sidebar, so it is only where a shortcut puts it.

### Backing up

The easiest way is the app's own **Backup / restore** page at the bottom of the side menu: tick the sections and download one ZIP. Otherwise stop the app and copy the whole `data` folder. It holds everything worth keeping:

- **three** databases, not one: `race_officer.db` (races, entries, boats, settings, users), `track_positions.db` (recorded GPS tracks) and `power_history.db` (hut battery and solar history);
- the course and mark files, polars, sail charts and branding;
- the saved evidence clips, in `data\video_clips`.

The `runtime` folder holds logs, caches, live preview frames and the rolling video buffer. It is useful for troubleshooting, but it is not race data and does not need backing up.

## Upgrading to a new version

1. Back up (above).
2. Stop the old version by removing its task: run `uninstall_startup_task.cmd` from the **old** version's `deploy\windows`.
3. Unzip the new version into `C:\RaceOfficer`, beside the old one.
4. Copy the old version's `data` folder into the new version's folder, replacing the one that came in the ZIP.
5. Run `install_startup_task.cmd` from the **new** version's `deploy\windows`, as in step 3.
6. Log in again — sessions from the old version are treated as stale — and check Settings, races, boats, horn, weather and video.

The task runs the folder it was installed from. That is why the installer, run from the new folder, is the step after an upgrade, and why restarting the task would only start the old version again. The Defender exclusions (step 6) already cover the new folder. Keep the old folder until you are happy with the new one, then delete it.

## Removing it

```cmd
deploy\windows\uninstall_startup_task.cmd
```

This stops the app and removes the task. It deletes nothing: the app folder and all race data stay where they are.

## Running it by hand (testing and diagnosis)

Stop the task first (`uninstall_startup_task.cmd`, or **End** in Task Scheduler): two copies of the app cannot both have port 5050.

The easy way is `deploy\windows\start_race_officer.cmd`. It is exactly what the task runs, but in a visible window, so the startup messages are on screen.

Fully by hand, in **Command Prompt** (not PowerShell, whose execution policy refuses the activation script):

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_019
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python app.py
```

`py -m venv .venv` and the `pip install` are only needed if the installer has not already run in this folder. The prompt starts with `(.venv)` while the environment is active. Leave the window open: closing it, or pressing **Ctrl + C**, stops the app. Do not start the app by double-clicking `app.py`.

Settings for a run by hand go before `python app.py`, in the same window:

- `set RO_PORT=5051` — another port. The default is 5050.
- `set RO_INITIAL_ADMIN_PASSWORD=...` — your own first admin password instead of a made-up one. It is only read when the app creates its database, so it only matters on the very first start.
- `set RO_COOKIE_SECURE=0` — lets other devices on a trusted club network sign in over plain `http://` (step 7).

Afterwards, put the task back with `install_startup_task.cmd`.

[`INSTALLATION.md`](INSTALLATION.md) lists every environment variable the app reads.

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

## Troubleshooting

**The app does not open.**

1. Run `deploy\windows\status_startup_task.cmd`.
2. Read `runtime\logs\race_officer.log`.
3. Check that Python 3.10 or later is installed (`py --version`).
4. Check that nothing else has port 5050 — a copy started by hand, for instance.
5. If only other devices cannot connect, check Windows Firewall (step 7).

**`py` or `python` is not recognised.** Open a new Command Prompt. If that does not help, run the Python installer again with **Add python.exe to PATH** ticked.

**The installer stops while installing packages.** Check the PC has internet access and run it again. A line like `WARNING: Cache entry deserialization failed, entry ignored` is harmless — pip ignored a stale entry in its download cache — and the installer carries on past it; it stops only if pip itself fails.

**Signing in from another device just returns to the login page.** That is the `Secure` cookie, working as intended over plain `http://` (step 7).

**FFmpeg is installed but the app cannot use it.** Restart the task so the app sees the new PATH (step 8), or enter the full path, `C:\ffmpeg\bin\ffmpeg.exe`, in **Settings → Video Recording**.

**The camera does not appear.** Run `ffmpeg -list_devices true -f dshow -i dummy` and type the camera's name exactly into **Settings → Video Recording**.

**The public page looks like plain text through Cloudflare.** Hard-refresh the browser, then check the tunnel forwards the whole hostname, not only `/public` (step 7).

## Reference links

- Python for Windows: `https://www.python.org/downloads/windows/`
- Python on Windows documentation: `https://docs.python.org/3/using/windows.html`
- Winget documentation: `https://learn.microsoft.com/windows/package-manager/winget/`
- FFmpeg downloads: `https://ffmpeg.org/download.html`
- The scripts themselves: [`deploy/windows/README.md`](../deploy/windows/README.md)

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
