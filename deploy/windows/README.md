# Windows deployment scripts

These scripts are for the race-hut Windows PC. They run the app with Waitress and create a Windows Scheduled Task so the app starts automatically when the race-office user logs in. The Scheduled Task starts the app hidden, so there should not be a command window left open on the desktop.

Use `install_startup_task.cmd` from this folder after the app has been copied to the PC. The task is called **Pwllheli Race Officer**.

Files:

- `start_race_officer.cmd` / `.ps1` — interactive/manual start script. It opens a console window, creates `.venv` if needed, installs requirements when `requirements.txt` changes and writes `runtime/logs/race_officer.log`.
- `install_startup_task.cmd` / `.ps1` — creates or updates the scheduled task and starts it.
- `restart_startup_task.cmd` / `.ps1` — restarts the scheduled task after an update or settings change.
- `status_startup_task.cmd` / `.ps1` — shows task status, HTTP status and recent log lines.
- `uninstall_startup_task.cmd` / `.ps1` — removes the scheduled task.

The task is installed for the signed-in Windows user rather than as a background service. This keeps access to desktop audio, USB serial horn hardware and cameras in the normal race-office session.


The scheduled task uses PowerShell RunLevel `Limited`, not `Highest`. This keeps it in the normal signed-in race-office user session and avoids requiring an elevated administrator task.


## Visible command window

The installed Scheduled Task should run hidden. If a black command window is left open after login, reinstall the task with the current scripts:

```cmd
uninstall_startup_task.cmd
install_startup_task.cmd
```

The manual `start_race_officer.cmd` script is intentionally visible because it is useful for diagnostics. The automatic Scheduled Task does not use `cmd.exe`; it runs `start_race_officer.ps1` with `-WindowStyle Hidden`.


## Pip cache warning

If pip prints `WARNING: Cache entry deserialization failed, entry ignored`, it is normally harmless. v0.77 runs deployment-time pip installs with `--no-cache-dir` and logs native command output as normal text, while still failing the script if pip returns a real non-zero exit code.


## Checking the race-office PC

```cmd
powershell -ExecutionPolicy Bypass -File deploy\windows\check_hut_pc.ps1
```

Reports, and changes nothing. Written after the app started refusing connections
mid-race with four plausible causes in the code and none of them measured.

It answers: is the CPU throttling (the hut's MINIX NEO Z350-0dB is fanless, so
turbo is a burst and the sustained speed is what matters); how many sockets are
actually open on port 5050, which is what the connection limit counts rather than
people; what is in `runtime/logs/slow.log`, grouped by endpoint and ranked by
total time held; how full the disk is and how big the video and databases have
grown; and whether the Windows settings that quietly cost this app - Defender
real-time scanning of the video and database paths, USB selective suspend on a
horn adapter, sleep, Delivery Optimization eating the 4G uplink - are as expected.

Run it **elevated** to include the Defender exclusions; unelevated it says it
cannot read them rather than reporting none, because not knowing is not the same
as none.

No output values are secret: settings are named, not dumped. Safe during a race,
though a quiet moment is kinder.

