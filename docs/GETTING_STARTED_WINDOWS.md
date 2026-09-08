# Getting started on Windows

This guide is for a race-office PC running Windows 10 or Windows 11. It assumes you are not familiar with Python or command-line tools.

The app runs locally on the race-office PC. Other phones, tablets or laptops can view it over the club network if you choose to allow that later.

## What you need

- A Windows 10 or Windows 11 PC.
- The Pwllheli Race Officer ZIP file.
- Internet access for the first installation.
- Python 3.10 or newer.
- FFmpeg only if you want video recording or the live finish-camera preview.
- A USB serial/horn interface only if you want the app to sound the horn automatically.

You do not need FFmpeg for normal race setup, starts, finishes, results or competitor pages.

## 1. Create a safe folder for the app

1. Open **File Explorer**.
2. Open `Local Disk (C:)`.
3. Create a new folder called:

```text
C:\RaceOfficer
```

4. Put the downloaded app ZIP file in that folder.
5. Right-click the ZIP file and choose **Extract All...**.
6. Extract it into `C:\RaceOfficer`.

After extraction you should have a folder like:

```text
C:\RaceOfficer\pwllheli_race_officer_v1_000
```

The exact version number may be different.

Avoid running the app directly from the ZIP file, from an email attachment, or from a temporary download folder. The app creates a database file and needs a normal folder it can write to.

## 2. Install Python

1. Open a web browser.
2. Go to the official Python download page:

```text
https://www.python.org/downloads/windows/
```

3. Download the latest stable **Windows installer (64-bit)**.
4. Run the installer.
5. On the first installer screen, tick:

```text
Add python.exe to PATH
```

6. Click **Install Now**.
7. If the installer offers **Disable path length limit**, click it. This is helpful on Windows.
8. Close the installer.

## 3. Check Python installed correctly

1. Click **Start**.
2. Type:

```text
cmd
```

3. Open **Command Prompt**.
4. Type:

```cmd
py --version
```

You should see a Python version, for example:

```text
Python 3.13.x
```

Then type:

```cmd
py -m pip --version
```

You should see a pip version. Pip is the Python package installer.

If `py` is not recognised, close Command Prompt and open it again. If it still does not work, reinstall Python and make sure **Add python.exe to PATH** is ticked.

## 4. Open the app folder in Command Prompt

In Command Prompt, change into the app folder. For example:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_000
```

Use your actual folder name if the version number is different.

To check you are in the correct folder, type:

```cmd
dir
```

You should see files such as:

```text
app.py
requirements.txt
docs
static
templates
```

## 5. Create the Python virtual environment

A virtual environment keeps the app's Python packages separate from the rest of the PC.

In the app folder, type:

```cmd
py -m venv .venv
```

This creates a folder called `.venv`.

## 6. Activate the virtual environment

In the same Command Prompt window, type:

```cmd
.venv\Scripts\activate.bat
```

The prompt should now start with:

```text
(.venv)
```

That means the virtual environment is active.

## 7. Install the app dependencies

With the virtual environment active, type:

```cmd
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The first install can take a minute or two. It is normal to see a lot of text scrolling past. This installs Flask, Waitress and the hardware/video support libraries used by the app.

If the install finishes without an error message, continue to the next step.

## 8. Start the app for the first time

Still in the same Command Prompt window, type:

```cmd
python app.py
```

Leave this Command Prompt window open. It is running the app with the Waitress server on port 5050.

Open a web browser and go to the race-office/admin dashboard:

```text
http://localhost:5050/admin
```

You should see the Race Officer login page. The root address `http://localhost:5050` is now the public read-only current-race page.

## 9. First login

Use the first-run login:

```text
Username: admin
Password: see runtime/initial_admin_password.txt on the race-office PC
```

If you set `RO_INITIAL_ADMIN_PASSWORD` before first launch, use that password instead. Change the password after login if needed:

1. Open **Settings**.
2. Open **Users**.
3. Change the admin password to something suitable for the race-office team.

## 10. Stop and restart the app

To stop the app:

1. Go back to the Command Prompt window that is running the app.
2. Press:

```text
Ctrl + C
```

To start it again later:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_000
.venv\Scripts\activate.bat
python app.py
```

Then open the race-office/admin dashboard:

```text
http://localhost:5050/admin
```

The default port is 5050. Advanced users can change it by setting `RO_PORT` before starting the app.

## 11. Optional: set your own first admin password before first run

This only matters before the app has created its database for the first time. If you skip this step, the app generates a random password and writes it to `runtime/initial_admin_password.txt` on the race-office PC.

In Command Prompt, before `python app.py`, type:

```cmd
set RO_INITIAL_ADMIN_PASSWORD=choose-a-good-password-here
python app.py
```

Then log in with:

```text
Username: admin
Password: choose-a-good-password-here
```

## 12. Optional: allow other devices on the club network

The public read-only page opens on the race-office PC at:

```text
http://localhost:5050
```

The race-office/admin dashboard is:

```text
http://localhost:5050/admin
```

For another device on the same network, use the race-office PC's IP address instead of `localhost`.

To find the PC's IP address:

1. Open Command Prompt.
2. Type:

```cmd
ipconfig
```

3. Look for the active network adapter and note the **IPv4 Address**, for example:

```text
192.168.1.25
```

4. On another device connected to the same network, browse to:

```text
http://192.168.1.25:5050
```

Replace `192.168.1.25` with your actual IP address.

When Windows asks whether to allow Python through the firewall, allow it on **Private networks** if this is the trusted club network. Do not allow it on public networks unless you understand the security implications.

**The public page works over the club network, but signing in will not.** Login cookies are marked `Secure`, and browsers only keep those over HTTPS — so at `http://192.168.1.25:5050/admin` you can enter the right password and simply be returned to the login page. Either do race-office work on the hut PC itself, reach it over HTTPS through the Cloudflare Tunnel, or (for a trusted club network only) start the app with `RO_COOKIE_SECURE=0`:

```cmd
set RO_COOKIE_SECURE=0
python app.py
```

## 13. Optional: install FFmpeg for video

FFmpeg is only needed for video recording and live finish-camera preview.

### Option A: install FFmpeg using Winget

Most modern Windows 10/11 systems include Winget, the Windows Package Manager.

1. Open **Command Prompt**.
2. Type:

```cmd
winget --version
```

If a version number appears, install FFmpeg with:

```cmd
winget install -e --id Gyan.FFmpeg
```

When it finishes, close Command Prompt and open a new Command Prompt window. Then test:

```cmd
ffmpeg -version
```

If you see FFmpeg version information, FFmpeg is installed.

### Option B: install FFmpeg manually

Use this method if Winget is not available or the Winget install does not work.

1. Open the official FFmpeg download page:

```text
https://ffmpeg.org/download.html
```

2. Choose **Windows**.
3. Use one of the linked Windows builds. The app only needs a normal build with `ffmpeg.exe`.
4. Download a release build ZIP.
5. Extract it.
6. Create this folder:

```text
C:\ffmpeg
```

7. Copy or move the extracted FFmpeg files so that this file exists:

```text
C:\ffmpeg\bin\ffmpeg.exe
```

8. Add FFmpeg to the Windows PATH:
   - Click **Start**.
   - Search for **Edit the system environment variables**.
   - Click **Environment Variables...**.
   - Under **User variables**, select **Path** and click **Edit**.
   - Click **New**.
   - Add:

```text
C:\ffmpeg\bin
```

   - Click **OK** on all windows.

9. Close Command Prompt and open a new Command Prompt window.
10. Test:

```cmd
ffmpeg -version
```

If you see FFmpeg version information, FFmpeg is installed.

## 14. Configure video in the app

1. Start the app.
2. Log in.
3. Open **Settings**.
4. Open **Video Recording**.
5. Enable video recording if required.
6. Select the video source:
   - **USB webcam** for a directly connected camera.
   - **RTSP stream** for a network camera.
7. For a Hikvision/IP camera, use the main stream as the recording URL, for example channel `101`, select **Camera stream copy**, and optionally enter the sub-stream preview URL, for example channel `102`.
8. Save settings.

To list USB cameras on Windows, open Command Prompt and type:

```cmd
ffmpeg -list_devices true -f dshow -i dummy
```

If the app cannot find the camera automatically, copy the camera name from that command and enter it manually in **Settings → Video Recording**.

If the app cannot find FFmpeg, set the FFmpeg path in settings to either:

```text
ffmpeg
```

or the full path:

```text
C:\ffmpeg\bin\ffmpeg.exe
```

## 15. Common beginner problems

### The app does not open when I double-click app.py

Do not start it by double-clicking `app.py`. Start it from Command Prompt using:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_000
.venv\Scripts\activate.bat
python app.py
```

### `python` is not recognised

Use:

```cmd
py --version
```

If `py` works, start the app setup using `py -m venv .venv`. Once the virtual environment is active, use `python` as shown in this guide.

### I cannot activate the virtual environment in PowerShell

This guide uses **Command Prompt**, not PowerShell, to avoid PowerShell execution-policy problems. Open **Command Prompt** and use:

```cmd
.venv\Scripts\activate.bat
```

### The browser says it cannot connect

Check that:

- The Command Prompt window running `python app.py` is still open and shows the Waitress startup message.
- You used `http://localhost:5050/admin` on the race-office PC for admin work.
- You did not accidentally close the app with `Ctrl + C`.
- Windows Firewall is not blocking access from other devices.

### `pip install -r requirements.txt` fails

Try:

```cmd
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If it still fails, check that the PC has internet access and that you are in the app folder containing `requirements.txt`.

### FFmpeg is installed but the app still cannot use video

Close and reopen Command Prompt after installing FFmpeg. Windows only updates PATH for new command windows.

If it still fails, enter the full FFmpeg path in **Settings → Video Recording**:

```text
C:\ffmpeg\bin\ffmpeg.exe
```

### The camera does not appear

Run:

```cmd
ffmpeg -list_devices true -f dshow -i dummy
```

Copy the camera name exactly and enter it manually in **Settings → Video Recording**.

## 16. Back up your data

The easiest way is the app's own **Backup / restore** page at the bottom of the side menu: tick the sections and download one ZIP. Otherwise copy the whole `data` folder — it holds everything worth keeping.

Before upgrading to a new app version:

1. Stop the app.
2. Copy the whole `data` folder somewhere safe.
3. There are **three** databases, not one:
   - `data\race_officer.db` — races, entries, boats, settings, users.
   - `data\track_positions.db` — recorded GPS tracks (if trackers are used).
   - `data\power_history.db` — hut battery/solar history.
4. Saved evidence clips are in `data\video_clips`.

The `runtime` folder contains temporary cache files, the rolling video buffer and FFmpeg logs. It normally does not need to be backed up.

## Deploy so it starts automatically

Once the app has been tested from Command Prompt, the hut PC should normally use the deployment scripts rather than relying on somebody to start it manually.

Open this folder:

```text
deploy\windows
```

Then double-click:

```text
install_startup_task.cmd
```

This creates a Windows Scheduled Task called **Pwllheli Race Officer**. The task starts the Waitress app hidden when the race-office Windows user logs in, so it should not leave a Command Prompt window open on the desktop. It also writes a log to:

```text
runtime\logs\race_officer.log
```

Useful helper scripts in the same folder are:

```text
status_startup_task.cmd
restart_startup_task.cmd
uninstall_startup_task.cmd
```

See [`DEPLOYMENT_WINDOWS.md`](DEPLOYMENT_WINDOWS.md) for the full hut-PC deployment procedure.

## Quick command summary

For a normal first install after Python is installed:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_000
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For later starts:

```cmd
cd /d C:\RaceOfficer\pwllheli_race_officer_v1_000
.venv\Scripts\activate.bat
python app.py
```

Then open the race-office/admin dashboard:

```text
http://localhost:5050/admin
```

The default port is 5050. Advanced users can change it by setting `RO_PORT` before starting the app.

## Reference links

- Python for Windows downloads: `https://www.python.org/downloads/windows/`
- Python on Windows documentation: `https://docs.python.org/3/using/windows.html`
- Microsoft Winget documentation: `https://learn.microsoft.com/windows/package-manager/winget/`
- FFmpeg downloads: `https://ffmpeg.org/download.html`

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
