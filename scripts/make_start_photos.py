"""One-off: start photos for races sailed before the app took them (v1.014).

Run ON THE HUT, with the installed app's own Python, so it uses the hut's own
evidence clips, settings and bucket:

    C:\\RaceOfficer\\pwllheli_race_officer_v1_016\\.venv\\Scripts\\python.exe make_start_photos.py
        C:\\RaceOfficer\\pwllheli_race_officer_v1_016 --series 7 --preview <folder>

and again without ``--preview`` to publish them.

For every race in the series, and each of the race's own start times: the frame
at the start signal is cut from the full-quality evidence clip -- the same
frame the live feature falls back to when the camera gives no photo -- the
start line and the club logo go on it exactly as at a live start (the buoy found
in frames in the minute before the gun, where the fleet is not yet in front of it), and, unless
``--preview``, it is uploaded to the club's bucket and recorded against the
start clip, so the race page's Start photo tab shows it ("from the start
video"). Nothing else in the database is touched and nothing is restored.

``--preview`` writes the photos to a folder and uploads and records nothing.
A start that already has its photo is left alone unless ``--redo``. A start
clip whose time is not one of the race's starts (one booked for a start that
was later moved) is skipped, as the live feature would.

It deliberately does not import app.py: that would start a second copy of the
app's background work alongside the running one -- the start sequence and its
horn among it.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path


# Where in the evidence clip to look for the buoy, in seconds from the gun:
# before it only, like the live feature (90, 60 and 30 s before) and the start
# video's own line (about a minute before). After the gun the fleet sails
# across the buoy's part of the frame: in race 98 of the autumn series, the
# frames 15, 30 and 45 s after the gun all found something orange on a boat
# a fifth of the frame to the right of the buoy, and drew the line to it.
SAMPLES_BEFORE_THE_GUN_S = (-60, -50, -40, -30, -20, -10)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("app_root", help="the installed app's folder")
    ap.add_argument("--series", type=int, required=True, help="series id (the autumn series 2026 is 7)")
    ap.add_argument("--race", type=int, action="append", help="only this race (may be repeated)")
    ap.add_argument("--preview", help="write the photos to this folder; upload and record nothing")
    ap.add_argument("--redo", action="store_true", help="make a start's photo again even if it has one")
    args = ap.parse_args(argv)

    root = Path(args.app_root).resolve()
    if not (root / "core" / "startphoto.py").is_file():
        print(f"{root} is not an app folder from v1.014 or later.")
        return 2
    sys.path.insert(0, str(root))
    os.chdir(root)
    from core import db as core_db
    core_db.DB_INITIALIZED = True          # the running app has built the schema already
    from core import startline, startphoto, video
    import inspect
    if "prior" not in inspect.signature(startphoto.compose_start_photo).parameters:
        print("This needs the app at v1.016 or later, which finds the buoy in frames either side of "
              "the gun. Install it, then run this from the new version's folder.")
        return 2
    from core.db import get_db, row_get
    from core.races import get_race
    from core.timeutils import parse_dt

    cfg = video.video_config()
    ffmpeg = video.ffmpeg_executable(cfg)
    if not ffmpeg:
        print("FFmpeg was not found.")
        return 2
    with get_db() as db:
        races = db.execute("SELECT id FROM races WHERE series_id = ? ORDER BY id", (args.series,)).fetchall()
    if not races:
        print(f"Series {args.series} has no races.")
        return 1
    preview = Path(args.preview) if args.preview else None
    if preview:
        preview.mkdir(parents=True, exist_ok=True)
        (preview / "buoy_report.txt").write_text("", encoding="utf-8")
    made = failed = 0
    for row in races:
        if args.race and int(row["id"]) not in args.race:
            continue
        race = get_race(int(row["id"]))
        starts = set(startphoto._race_start_times(race))
        with get_db() as db:
            clips = db.execute("SELECT * FROM video_clips WHERE race_id = ? AND clip_type = 'start' ORDER BY event_time",
                               (int(race["id"]),)).fetchall()
        if not clips:
            print(f"race {race['id']} ({race['name']}): no start video")
        for clip in clips:
            label = f"race {race['id']} ({race['name']}), start {str(clip['event_time'])[11:16]}"
            if str(clip["event_time"]) not in starts:
                print(f"{label}: skipped, not one of the race's starts now")
                continue
            if row_get(clip, "photo_status") == "ready" and not args.redo and not preview:
                print(f"{label}: already has its photo")
                continue
            evidence = video.evidence_video_path_for_clip(clip)
            if not evidence:
                print(f"{label}: FAILED, the evidence clip is not on this PC")
                failed += 1
                continue
            event = parse_dt(clip["event_time"])
            began = parse_dt(str(row_get(clip, "footage_started_at") or "")) or (
                event - timedelta(seconds=int(clip["pre_seconds"] or 60)))
            offset = max(0.0, (event - began).total_seconds())
            work = Path(tempfile.mkdtemp(prefix=f"start_photo_{clip['id']}_"))
            try:
                raw = work / "frame.jpg"
                subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.3f}",
                                "-i", str(evidence), "-frames:v", "1", "-q:v", "2", str(raw)],
                               check=True, timeout=180, **video.background_priority())
                if not startphoto._is_jpeg(raw):
                    raise RuntimeError("no frame came out of the evidence clip")
                # At the gun the fleet is often in front of the buoy (race 90):
                # find it in the frames before, and draw the line there.
                prior = []
                seen = []
                if startline.startline_overlay_enabled(cfg):
                    for rel in SAMPLES_BEFORE_THE_GUN_S:
                        at = offset + rel
                        if at < 0.5:
                            continue
                        frame = work / f"sample_{int(at)}.jpg"
                        try:
                            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}",
                                            "-i", str(evidence), "-frames:v", "1", "-q:v", "3", str(frame)],
                                           check=True, timeout=180, **video.background_priority())
                            found = startline.detect_buoy(str(frame))
                        except Exception:
                            found = None
                        prior.append(found)
                        seen.append((rel, found))
                    seen.append((0, startline.detect_buoy(str(raw))))
                public_path, raw_path = startphoto.start_photo_paths(clip)
                if preview:
                    out = preview / public_path.name
                    drawn, note = startphoto.compose_start_photo(ffmpeg, raw, out, cfg, work, prior=prior)
                    print(f"{label}: {out.name} ({offset:.0f} s into the clip); start line {note}")
                    # Where the buoy was found in each frame, for checking the line.
                    with open(preview / "buoy_report.txt", "a", encoding="utf-8") as report:
                        report.write(f"{label}: start line {note}\n")
                        for rel, found in seen:
                            where = (f"buoy at {found[0]:.3f},{found[1]:.3f} conf {found[2]:.2f}"
                                     if found else "no buoy")
                            report.write(f"    {rel:+4d} s  {where}\n")
                    made += 1
                    continue
                public_path.parent.mkdir(parents=True, exist_ok=True)
                part = public_path.with_name(public_path.stem + ".part.jpg")
                drawn, note = startphoto.compose_start_photo(ffmpeg, raw, part, cfg, work, prior=prior)
                os.replace(part, public_path)
                shutil.copyfile(raw, raw_path)
                url = startphoto.publish_start_photo(clip, public_path, cfg)
                startphoto.update_start_photo(
                    int(clip["id"]), status="ready", source="recording", answer_ms=None,
                    taken_at=event.isoformat(timespec="milliseconds"),
                    file_path=startphoto._relative(public_path), raw_path=startphoto._relative(raw_path), url=url,
                    message=f"Made afterwards from the start video{'' if drawn else ' (no start line drawn)'}.")
                print(f"{label}: published{'' if url else ' (kept on the hut: publishing to R2 is off)'};"
                      f" start line {note}")
                made += 1
            except Exception as exc:
                print(f"{label}: FAILED, {exc}")
                failed += 1
            finally:
                shutil.rmtree(work, ignore_errors=True)
    print(f"{made} photo(s) {'previewed' if preview else 'made'}, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
