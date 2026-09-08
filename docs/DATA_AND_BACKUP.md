# Data, storage and backup

## v0.68 data layout

From v0.68, the files that should be backed up are kept under the single `data/` folder:

```text
data/race_officer.db          Main SQLite database
data/courses.json             Fixed CHPSC course definitions
data/marks.json               Racing mark positions
data/start_finish.json        Start/finish definitions
data/DefaultSailChart.txt     Default sail chart for course analysis
data/polars/                  Boat polar files
data/sailcharts/              Optional per-polar sail charts, named <Polar>-SailChart.txt/csv/tsv
data/video_clips/             Start/finish/manual horn event clips
data/branding/                Public club/sponsor branding logos
```

Temporary files are kept outside `data/` under `runtime/`:

```text
runtime/cache/                Downloaded IRC/YTC listing cache
runtime/video/buffer/         Temporary rolling FFmpeg buffer segments
runtime/video/live/latest.jpg Live preview frame
runtime/video/ffmpeg_recorder.log
runtime/secret_key.txt        Generated Flask session secret when RO_SECRET_KEY is not set
runtime/initial_admin_password.txt  Generated first-run admin password when needed
runtime/offsite_backup_status.json  Last off-site backup result, so the dashboard is honest after a restart
```

The `runtime/` folder is useful for troubleshooting. For a normal race-data backup it does not need to be copied, but keep `runtime/initial_admin_password.txt` until the first admin password has been changed or recorded securely. The rolling video buffer is deliberately outside `data/` because it is temporary and can grow quickly.

`runtime/secret_key.txt` is kept persistent on the race-office PC so a simple app restart does not log everyone out. Authenticated browser sessions also carry the app version, so installing a newer build still clears old race-office sessions and requires users to log in again.

## Automatic migration from older versions

On first run after upgrading, the app will gently migrate common older paths:

- If `race_officer.db` exists in the app root and `data/race_officer.db` does not, it is copied to `data/race_officer.db`.
- If old clips exist in `data/video/clips/`, they are copied to `data/video_clips/`.
- If `data/SailChart J122 North.txt` exists and `data/DefaultSailChart.txt` does not, it is renamed/copied to the new default sail-chart name.
- If an old `data/J122.txt` polar exists and `data/polars/J122.txt` does not, it is copied into `data/polars/`.
- Per-polar sail charts should live in `data/sailcharts/` with names such as `J109-SailChart.txt`; old same-name charts are still accepted as a fallback.

## Marks file and compound marks

`data/marks.json` is backup-worthy race data. It may contain normal single-position marks and compound parent marks. A compound parent mark keeps the short course-board label, while `components` and `rounding_order` define the physical corner positions used for charting and leg analysis.

A mark also carries the **history of its own position**: who set it, when, from what and how accurate the fix claimed to be, plus every position it has replaced. That history is what lets a race be drawn and replayed with the marks as they stood when it was sailed, so it is not incidental &mdash; restoring an older `marks.json` over a newer one rolls back those corrections along with the positions. A mark may also carry its own **rounding radius**, overriding the one in Settings.

The current default file includes `Y` with component corners `Ya`/`Yb` and `A` with component corners `Aa`/`Ab`.

## Database contents

The SQLite database stores operational data such as:

- Users.
- Settings.
- Boats and ratings.
- Races and entries, including race-entry IRC/YTC rating snapshots.
- Race event logs.
- Weather samples.
- Series definitions and membership.
- Video clip metadata.

## In-app backup and restore

From v0.124, the race-office app has a **Backup / restore** page at the bottom of the side menu. It creates and restores ZIP files with selectable sections:

- **Database (Races & settings)** — `data/race_officer.db`, including races, entries, boats, settings, users, event logs, weather samples and video metadata.
- **GPS tracks** — `data/track_positions.db`, every recorded tracker fix: the race map history, replays, and the evidence behind GPS finishes. This is a *separate* database from the one above, and until v0.192 it was not in any backup.
- **Hut power history** — `data/power_history.db`, battery/solar/load readings from the power monitor. Also its own database, also missing before v0.192.
- **Marks, Courses, start/finish line** — `data/marks.json`, `data/courses.json`, `data/start_finish.json`.
- **Polars & Sail charts** — `data/polars/`, `data/sailcharts/`, `data/DefaultSailChart.txt`.
- **Branding Images** — `data/branding/`, including the branding manifest and uploaded club/sponsor logos.
- **Videos** — `data/video_clips/`, containing saved start/finish/manual-horn evidence clips. This option can make a very large ZIP and is not selected by default.

Backup ZIPs include a small `race_officer_backup_manifest.json` file describing the app version, creation time, selected sections and file counts. The ZIP member names are stable `data/...` paths so a backup can be restored into a new app folder even if Windows short paths or user names are different.

Restore only replaces the sections ticked on the restore form and present in the uploaded ZIP. Sections not present in the ZIP are skipped. Make a fresh backup before restoring and avoid editing races/settings while a restore is in progress.

**Restoring is admin only** — because it overwrites current data, only administrators see the Restore panel and the server refuses restore requests from race officers. Creating and downloading a backup is available to race officers as well.

**Every database is copied through SQLite's online backup API**, not copied as a file, so a backup taken while the tracker poller and power monitor are writing cannot catch a half-written transaction — nothing has to be stopped to take one. On Windows the temporary copies are explicitly closed before the temporary folder is cleaned up, which avoids the `[WinError 32]` file-in-use error. A database that does not exist yet (a hut that has never used tracking has no `track_positions.db`) is reported as nothing to back up rather than an error.

Restoring one of these puts it back where its module looks for it and reopens it, so the restored file is brought up to the current schema immediately rather than being trusted as-is until the next restart.

Backups are built as a temporary ZIP in `runtime/` and streamed to the browser, then deleted. Anything left behind by an interrupted download is swept by the next backup once it is more than six hours old.

Automated tests cover the close behaviour, the presence of all three databases in a backup, a full round trip for each, and the default rule that saved videos stay opt-in because they can make very large ZIP files.

## Off-site backup (from v0.238)

A backup downloaded from the Backup/restore page is a copy in the same building as the original. From v0.238 the app can also push an **encrypted copy off-site**, nightly, to Cloudflare R2. Settings &rarr; **Off-site backup**.

It is the *same ZIP*, with the same `data/...` member names, so an off-site copy restores on the Backup/restore page like any other backup — there is no second format to keep working.

### What to set up

1. In the Cloudflare dashboard, create a **second R2 bucket** for backups, with **no public access**. Do not reuse the bucket that serves public race videos.
2. In Settings &rarr; Off-site backup, enter that bucket name. The account ID and access keys are the ones already in Settings &rarr; Video Recording; there is only one set to look after.
3. Choose a **passphrase** and record it in the club password manager, next to the admin login.
4. Press **Save and back up off-site now**, and read the result. An unattended 3 am job that has never succeeded is not a backup.

The app **refuses to use the public video bucket** for backups. That bucket is reachable from the public base URL, so an archive in it would be one guessed object key away from being anyone's download — and a backup contains every user account and password hash in the club.

### Encryption

The archive is **AES-256 encrypted before it leaves the hut**, in the WinZip AES format. That is deliberate rather than incidental: **7-Zip or WinRAR will extract the `data/` folder given the passphrase**, with this app not installed and no script to run. A format only this code understood would have made the off-site copy depend on the very thing it exists to survive.

Nothing can open an off-site backup without the passphrase — not the app, not Cloudflare, not the developer. Changing the passphrase does not re-encrypt archives already uploaded, so keep the old one until they have aged out of retention.

To restore one: download the `.zip` from R2, then use the Backup/restore page as normal and enter the passphrase in the **Backup passphrase** field. A wrong passphrase is refused *before* anything is deleted — the restore clears the target folders before extracting, so that check is what stops a typo costing you the branding folder.

If the archive is larger than the upload limit (`RO_MAX_UPLOAD_MB`, 64 MB by default) the restore upload is rejected; raise it for that restore, or extract the ZIP with 7-Zip and copy the `data/` folder in by hand with the app stopped.

### When it runs

Nightly at the configured time (03:15 by default), and **held back** while:

- boats are still racing,
- a start is within the next three hours,
- race videos are still uploading.

The hut is on 4G shared with a caravan park, and start videos have already been lost to Saturday-evening contention. A backup must never be the reason one is lost. A held-back run tries again about twenty minutes later; a failed one waits about forty-five minutes.

Each of those checks is bounded by a time window, which matters more than it looks: an abandoned race sheet left with boats marked `RACING`, or a clip stuck in `uploading` since last season, would otherwise defer the backup every night for ever — and it would look like a deferral rather than a failure.

A backup missed because the PC was switched off at 03:15 (the normal state of a hut PC) is taken when the app next starts, rather than skipped for that day.

### What is included

Everything except the saved videos: the race database, GPS tracks, hut power history, marks/courses/start-finish, polars and sail charts, and branding. Roughly a few tens of megabytes.

**Videos are left out on purpose.** The public web copies are already on R2, so backing them up would pay twice for the same bytes. The consequence is worth stating plainly: those R2 copies are *branded, re-encoded web versions*, so **the unbranded evidence clips on the hut PC are not off-site**. If a particular clip matters as evidence, copy it off by hand.

### Checking it, and retention

- The **dashboard** carries the age of the last success, and says so when the answer is "never" or "40 days". A backup job that quietly stopped months ago is worse than none, because the club believes it has one.
- Settings lists **what is actually in the bucket**, rather than what the app believes it put there.
- Each upload is **verified with a HEAD request** against the expected byte count. An upload that returns success without the bytes arriving is the failure nobody notices until a restore.
- An unencrypted **sidecar manifest** (`....manifest.json`) goes up beside each archive with the sections, file counts, size and SHA-256, so the club can see what is off-site and check a downloaded archive without decrypting anything. It contains counts only, never race data.
- **Retention** keeps the newest 30 archives by default; older ones are removed with their manifests after each successful upload.

Encrypted backups need the `pyzipper` package from `requirements.txt`. Without it the app refuses to run an off-site backup rather than uploading a readable archive.

### Not included yet

The agreed design also called for **a few copies on the relay**, as a second destination. That is not built: it needs a file transport to the relay LXC that does not exist yet. R2 is the off-site half, and the relay would not be an off-site copy on its own — it is one machine, and it is the club's front door.

## Backup before racing

Before racing, back up the whole `data/` folder. At minimum, this includes:

```text
data/race_officer.db
data/track_positions.db  the recorded GPS tracks
data/power_history.db    hut battery/solar history
data/polars/
data/sailcharts/
data/DefaultSailChart.txt
data/video_clips/        if there are clips you need to keep
data/branding/           if public branding logos have been uploaded
```

## Backup after racing

After racing, back up:

- The whole `data/` folder.
- Exported race/series CSV or HTML results, if saved outside the app.

Do not include `runtime/video/buffer/` in normal backups. It is only the temporary rolling buffer used to create event clips.

## Moving to a new PC

1. Install the same app version on the new PC.
2. Stop the app on both PCs.
3. Copy the old PC's `data/` folder into the new app folder.
4. Check Settings, especially serial ports, camera names, FFmpeg path and weather station network address. These are often PC-specific.

## Resetting the app

To start with a clean database, stop the app and rename or remove `data/race_officer.db`. Keep a backup first.

Packaged releases should not include a development/test `data/race_officer.db`, `runtime/secret_key.txt` or `runtime/initial_admin_password.txt`; those are generated on the race-office PC.

## Windows deployment logs

The Windows deployment scripts write the app log to:

```text
runtime/logs/race_officer.log
```

This file is useful for troubleshooting, but it is not part of the normal race-data backup. Back up `data/`; keep `runtime/` only if you want diagnostics.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
