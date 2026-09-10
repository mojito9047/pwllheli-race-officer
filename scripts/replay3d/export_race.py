#!/usr/bin/env python3
"""Export one sailed race as a scene file for the 3D replay, with its assets.

The race itself comes from :mod:`core.replay3d`, which is the half that runs on
the hut and needs the database. This script is the developer's way in to the
same thing, plus the parts a hut has no business doing:

* the terrain grid, decoded from a GeoTIFF digital elevation model, and the
  satellite imagery draped over it. Both are the same for every race at one
  club, so they belong in a shared asset built once, not in every scene;
* downloading the start and finish clips and probing them, which the renderer
  does for itself when it works from a job;
* the title and results cards, and the app's typefaces converted to TTF;
* reading somebody else's data directory -- a hut backup -- instead of this
  checkout's, which is how a real race gets exported on a developer's machine.

    python scripts/replay3d/export_race.py --race 457
    python scripts/replay3d/export_race.py --race 457 --dem data/dem/N52_W005.tif
    python scripts/replay3d/export_race.py --list
    python scripts/replay3d/export_race.py --backup ~/Downloads/backup.zip --list
    python scripts/replay3d/export_race.py --backup hut_backup.zip --race 512 --dem ...

Output goes to runtime/replay3d/race_<id>.json unless --out is given.

The data normally comes from this checkout's data/ directory. That is a
development copy, edited since most of its races were sailed, so for a real
race point the exporter at the hut's data instead: ``--backup ZIP`` unpacks a
backup taken on the hut's Backup page (or the nightly off-site copy) and reads
from it; ``--data DIR`` reads an already-unpacked data directory. Tracks live in
the backup only when its "tracks" section was included; ``--tracks FILE`` names a
track_positions.db to use when they were not.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import urllib.parse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)          # cards.py and replay_time.py sit beside this script
os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "export-only")

from core import appstate  # noqa: E402
from core.db import row_get  # noqa: E402
import core.db as _core_db  # noqa: E402

# The scorer calls init_db, and only app.py normally registers the schema
# builder. This reads an existing database rather than making one, so a
# no-op keeps core.series usable without importing the whole web app.
if _core_db.SCHEMA_INITIALIZER is None:
    _core_db.SCHEMA_INITIALIZER = lambda: None

from core.replay3d import (  # noqa: E402
    DEFAULT_LEAD_S,
    DEFAULT_STEP_S,
    DEFAULT_TAIL_S,
    _parse_iso,
    build_scene,
)
import core.track as track_module  # noqa: E402  (re-pointed by use_data_dir)
from fonts import ensure_fonts  # noqa: E402  (beside this script)
from branding import (  # noqa: E402  (beside this script)
    BRANDING_MANIFEST_URL,
    BRANDING_ROTATE_S,
    branding_from_api,
    branding_take,
)
from terrain import (  # noqa: E402  (beside this script; no app imports)
    imagery_credit_for,
    imagery_for_terrain,
    terrain_grid,
)

DEFAULT_OUT_DIR = appstate.RUNTIME_DIR / "replay3d"



# ---------------------------------------------------------------------------
# Reading somebody else's data directory (a hut backup) instead of this checkout's.

def use_data_dir(data_dir: str, tracks_path: Optional[str] = None) -> Dict[str, Any]:
    """Point the app's readers at another data directory for the rest of this process.

    Mirrors what the app's restore path does: the database, the track database
    and the course/mark/start-finish JSON are all read through module
    attributes, so re-pointing those is enough. Returns what was found.
    """
    d = Path(data_dir).resolve()
    if not (d / "race_officer.db").exists():
        raise SystemExit(f"{d} has no race_officer.db")
    appstate.DATA_DIR = d
    appstate.DB_PATH = d / "race_officer.db"
    with (d / "courses.json").open("r", encoding="utf-8") as f:
        appstate.COURSES_DATA = json.load(f)
    appstate.COURSES = appstate.COURSES_DATA["courses"]
    appstate.COURSE_BY_NO = {int(c["course_no"]): c for c in appstate.COURSES}
    with (d / "marks.json").open("r", encoding="utf-8") as f:
        appstate.MARKS_DATA = json.load(f)
    appstate.MARKS = appstate.MARKS_DATA["marks"]
    sf = d / "start_finish.json"
    if sf.exists():
        with sf.open("r", encoding="utf-8") as f:
            appstate.START_FINISH = json.load(f)
    tracks = Path(tracks_path).resolve() if tracks_path else d / "track_positions.db"
    found_tracks = tracks.exists()
    if found_tracks:
        track_module.TRACK_DB_PATH = tracks
        track_module.TRACK_DB_INITIALIZED = False
    return {"data_dir": str(d), "tracks": str(tracks) if found_tracks else None}


def extract_backup(zip_path: str) -> str:
    """Unpack a Race Officer backup ZIP's data/ members and return that data directory.

    Goes under runtime/replay3d/restored/<zip name>/ so a second run reuses it.
    Only members under data/ are taken, and only plain files inside it (no
    paths that climb out), because a backup is just a file somebody handed us.
    """
    import zipfile

    zp = Path(zip_path)
    if not zp.exists():
        raise SystemExit(f"no such backup: {zip_path}")
    target = DEFAULT_OUT_DIR / "restored" / zp.stem
    data_dir = target / "data"
    if not (data_dir / "race_officer.db").exists():
        with zipfile.ZipFile(zp) as zf:
            for member in zf.infolist():
                name = member.filename.replace("\\", "/")
                if not name.startswith("data/") or member.is_dir() or ".." in name.split("/"):
                    continue
                dest = (target / name).resolve()
                if target.resolve() not in dest.parents:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest.open("wb") as out:
                    out.write(src.read())
    if not (data_dir / "race_officer.db").exists():
        raise SystemExit(f"{zip_path} holds no data/race_officer.db (is it a Race Officer backup?)")
    return str(data_dir)
# Seconds of track kept before the first start and after the last finish.
DEFAULT_LEAD_S = 300.0
DEFAULT_TAIL_S = 120.0
# Regular clock the tracks are resampled onto. Trackers report every 5-30 s;
# 5 s keeps every real fix's influence without inventing detail.
DEFAULT_STEP_S = 5.0
# A boat with no fix for this long is out of sight: the track is broken rather
# than drawn as a straight line across the bay.
GAP_BREAK_S = 180.0
# Boats slower than this are treated as stopped, so the heading is held rather
# than derived from GPS jitter.
STOPPED_KN = 0.6
# If nobody finished and no boat is still racing, the race window ends this
# long after the first start.
FALLBACK_DURATION_S = 4 * 3600.0



# ---------------------------------------------------------------------------
# The scene, plus everything a local render wants beside it.

def fetch_clips(clips: List[Dict[str, Any]], out_dir: str, race_id: int) -> List[Dict[str, Any]]:
    """Download the published clips and probe them, for a render on this machine.

    A renderer working from a job does this for itself. The windows are not
    recomputed here: ``core.replay3d`` settled those, and two opinions about
    which clip is on screen is exactly the bug that would follow.
    """
    import urllib.request

    video_dir = os.path.join(out_dir, "video", f"race_{race_id}")
    kept: List[Dict[str, Any]] = []
    for clip in clips:
        url = clip.get("url") or ""
        if not url:
            continue
        os.makedirs(video_dir, exist_ok=True)
        dest = os.path.join(video_dir, os.path.basename(urllib.parse.urlparse(url).path))
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "pwllheli-replay3d/1.0"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(dest + ".part", "wb") as f:
                    f.write(resp.read())
                os.replace(dest + ".part", dest)
                print(f"  fetched {os.path.basename(dest)} ({os.path.getsize(dest) // 1024} KB)")
            except Exception as ex:
                print(f"  could not fetch {url}: {ex!r}")
                continue
        clip = dict(clip)
        clip["file"] = os.path.relpath(dest, out_dir).replace("\\", "/")
        try:
            import av
            with av.open(dest) as container:
                stream = container.streams.video[0]
                clip["fps"] = round(float(stream.average_rate), 4) if stream.average_rate else 25.0
                clip["frames"] = int(stream.frames or 0)
                clip["duration_s"] = (round(float(container.duration or 0) / 1e6, 2)
                                      if container.duration else None)
                clip["width"], clip["height"] = stream.width, stream.height
        except Exception:
            clip["fps"] = 25.0
        kept.append(clip)
    return kept


def export_race(race_id: int, *, step_s: float = DEFAULT_STEP_S, lead_s: float = DEFAULT_LEAD_S,
                tail_s: float = DEFAULT_TAIL_S, dem_path: Optional[str] = None,
                dem_radius_m: float = 7000.0, dem_cell_m: float = 60.0,
                branding_url: Optional[str] = BRANDING_MANIFEST_URL,
                branding_dir: Optional[str] = None,
                imagery_path: Optional[str] = None, out_dir: Optional[str] = None,
                videos: bool = False) -> Dict[str, Any]:
    """A scene from the database, with terrain, imagery, clips and branding added."""
    out = build_scene(race_id, step_s=step_s, lead_s=lead_s, tail_s=tail_s, videos=videos)
    folder = out_dir or str(DEFAULT_OUT_DIR)

    out["branding"] = copy_branding(folder, branding_url, branding_dir)
    if videos:
        out["videos"] = fetch_clips(out.get("videos") or [], folder, race_id)

    lat0, lon0 = float(out["origin"]["lat"]), float(out["origin"]["lon"])
    if dem_path:
        out["terrain"] = terrain_grid(dem_path, lat0, lon0, dem_radius_m, dem_cell_m)
        if imagery_path:
            os.makedirs(folder, exist_ok=True)
            png = os.path.join(folder, f"race_{race_id}_imagery.png")
            info = imagery_for_terrain(imagery_path, out["terrain"], lat0, lon0, png)
            out["terrain"]["imagery"] = info["file"]          # relative to the JSON's folder
            out["terrain"]["imagery_info"] = info
    elif imagery_path:
        print("note: --imagery needs --dem too (the image is draped over the terrain grid); ignored")
    return out




def _branding_from_dir(dest: Path, src_dir: str) -> Optional[Dict[str, Any]]:
    """Logos from a directory the caller names explicitly.

    Never the race data directory by default. A hut backup is encrypted, and
    the render machine has no business holding the key, so branding comes off
    the public manifest and this is only for a caller who has an unencrypted
    copy of the images to hand.
    """
    src = Path(src_dir)
    manifest_path = src / "sponsor_logos.json"
    if not manifest_path.exists():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None

    dest.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {"rotate_s": BRANDING_ROTATE_S, "club": None, "sponsors": [],
                           "source": str(src)}
    club_name = str(manifest.get("club_logo") or "").strip()
    club = src / club_name if club_name else None
    if club is None or not club.exists():
        fallback = getattr(appstate, "DEFAULT_CLUB_LOGO_PATH", None)
        club = Path(fallback) if fallback else None
    if club is not None:
        out["club"] = branding_take(club, dest)
    for item in manifest.get("sponsors") or []:
        name = str((item or {}).get("filename") or "").strip()
        rel = branding_take(src / name, dest) if name else None
        if rel:
            out["sponsors"].append({"label": str(item.get("label") or Path(name).stem), "file": rel})
    if not out["club"] and not out["sponsors"]:
        return None
    print(f"  branding: {len(out['sponsors'])} sponsor(s) from {src}")
    return out


def copy_branding(out_dir: str, manifest_url: Optional[str] = BRANDING_MANIFEST_URL,
                  branding_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Club logo and sponsor images for the film, beside the export.

    The film is branded the way the club's own start and finish videos are:
    the club mark top left, one sponsor at a time top right, changing every
    five seconds. The club's public manifest is the single source of truth and
    is what the live-stream relay reads, so the film agrees with everything
    else the club publishes, and a render machine needs nothing but the URL.

    It deliberately does **not** fall back to the images inside a hut backup.
    Those are encrypted, and nothing about making a film should require the
    backup key to travel. ``branding_dir`` is there for a caller who already
    has the images unencrypted and wants to work offline.
    """
    dest = Path(out_dir) / "branding"
    out = branding_from_api(dest, manifest_url) if manifest_url else None
    if out is None and branding_dir:
        out = _branding_from_dir(dest, branding_dir)
    if out is None:
        print("  branding: none (no manifest reachable, no --branding-dir given)")
    return out

def list_races() -> None:
    """Print the races that have any track to replay, newest first."""
    with get_db() as db:
        races = db.execute("SELECT * FROM races WHERE start_time != '' ORDER BY start_time DESC").fetchall()
    for race in races:
        start_dt = race_first_start_dt(race)
        if start_dt is None:
            continue
        t0 = start_dt.timestamp()
        tracked = 0
        for e in get_entries(int(race["id"])):
            if positions_for_entry_since(e, t0 - DEFAULT_LEAD_S, t0 + FALLBACK_DURATION_S):
                tracked += 1
        if tracked:
            print(f"{race['id']:>5}  {race['start_time']:<19}  course {row_get(race, 'course_no', '?'):<4} "
                  f"{tracked} tracked  {race['name']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--race", type=int, help="race id to export")
    ap.add_argument("--list", action="store_true", help="list races that have tracks")
    ap.add_argument("--out", help="output JSON path (default runtime/replay3d/race_<id>.json)")
    ap.add_argument("--step", type=float, default=DEFAULT_STEP_S, help="resample step in seconds")
    ap.add_argument("--lead", type=float, default=DEFAULT_LEAD_S, help="seconds before the first start")
    ap.add_argument("--tail", type=float, default=DEFAULT_TAIL_S, help="seconds after the last finish")
    ap.add_argument("--dem", help="GeoTIFF DEM covering the area, for terrain")
    ap.add_argument("--dem-radius", type=float, default=7000.0, help="half-width of the terrain grid in metres")
    ap.add_argument("--dem-cell", type=float, default=60.0, help="terrain grid cell size in metres")
    ap.add_argument("--imagery", help="georeferenced lat/lon RGB GeoTIFF to drape over the terrain (needs --dem)")
    ap.add_argument("--backup", help="read from a Race Officer backup ZIP (e.g. from the hut) instead of data/")
    ap.add_argument("--data", help="read from an unpacked data directory instead of data/")
    ap.add_argument("--tracks", help="track_positions.db to use when the backup/data dir has none")
    ap.add_argument("--branding-url", default=BRANDING_MANIFEST_URL,
                    help="the club's public branding manifest; empty string to skip it")
    ap.add_argument("--branding-dir",
                    help="unencrypted directory of logos to use if the manifest is unreachable "
                         "(never a hut backup: those are encrypted and the key stays at the club)")
    ap.add_argument("--videos", action="store_true",
                    help="include the race's start/finish clips (fetching the published copies if the "
                         "files are not in the data directory) so the replay can show them picture-in-picture")
    args = ap.parse_args(argv)

    if args.backup or args.data:
        source = extract_backup(args.backup) if args.backup else args.data
        found = use_data_dir(source, args.tracks)
        print(f"reading {found['data_dir']}")
        if found["tracks"]:
            print(f"  tracks from {found['tracks']}")
        else:
            print("  WARNING: no track_positions.db there and no --tracks given; using this checkout's tracks, "
                  "which are probably not the hut's")

    if args.list:
        list_races()
        return 0
    if args.race is None:
        ap.error("--race is required (or --list)")

    out_path = args.out or str(DEFAULT_OUT_DIR / f"race_{args.race}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    data = export_race(args.race, step_s=args.step, lead_s=args.lead, tail_s=args.tail,
                       dem_path=args.dem, dem_radius_m=args.dem_radius, dem_cell_m=args.dem_cell,
                       imagery_path=args.imagery, out_dir=os.path.dirname(os.path.abspath(out_path)),
                       branding_url=args.branding_url, branding_dir=args.branding_dir,
                       videos=args.videos)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    out_dir = os.path.dirname(os.path.abspath(out_path))
    fonts = ensure_fonts(out_dir)

    # The title and results cards, drawn now so the builder only has to show them.
    from cards import build_cards
    branding = [str(appstate.BRANDING_DIR / "club_logo.png"),
                str(appstate.BASE_DIR / "static" / "img" / "pwllheli_sailing_club_logo_white.png"),
                str(appstate.DEFAULT_CLUB_LOGO_PATH)]
    try:
        data["cards"] = build_cards(data, out_dir, os.path.join(out_dir, "fonts"), branding, int(args.race))
    except Exception as ex:
        print(f"  cards: could not draw them ({ex!r})")
        data["cards"] = {}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    boats = data["boats"]
    print(f"wrote {out_path}")
    for kind, name in (data.get("cards") or {}).items():
        if name:
            print(f"  card {kind}: {name}")
    for table in data.get("results") or []:
        print(f"  results: {table['title']} ({len(table['rows'])} boats)")
    if fonts["skipped"]:
        print(f"  fonts: {fonts['skipped']}; Blender will use its built-in face")
    elif fonts["written"]:
        print(f"  fonts: wrote {', '.join(fonts['written'])} to {fonts['dir']}")
    print(f"  race {data['race']['id']} '{data['race']['name']}'  course {data['race']['course_no']} "
          f"{data['race']['course_text']}")
    print(f"  window {data['time']['t0_iso']} + {data['time']['duration_s'] / 60:.1f} min, "
          f"step {data['time']['step_s']} s, extent {data['extent_m']:.0f} m")
    print(f"  wind {data['wind']['twd_deg']}° {data['wind']['tws_kn']} kn ({data['wind']['from']})")
    terrain = data["terrain"] or {}
    print(f"  {len(data['marks'])} marks, {len(data['course'])} course points, "
          f"finish line {'yes' if data['finish_line'] else 'no'}, terrain {'yes' if terrain else 'no'}"
          + (f", imagery {terrain['imagery']} ({terrain['imagery_info']['px']} px)" if terrain.get("imagery") else ""))
    if terrain.get("imagery_info", {}).get("credit"):
        print(f"  imagery credit: {terrain['imagery_info']['credit']}")
    for clip in data.get("videos") or []:
        print(f"  video {clip['kind']:<7} {clip['t_start']:>7.0f}s to {clip['t_end']:>7.0f}s  "
              f"{clip.get('width', '?')}x{clip.get('height', '?')} @ {clip.get('fps', '?')} fps  {clip['file']}")
    for b in boats:
        live = sum(1 for s in b["samples"] if s[1] is not None)
        print(f"  boat {b['name']:<20} {b['sail_no']:<8} {b['status']:<9} {b['fix_count']} fixes -> {live} samples")
    if not boats:
        print("  no boat in this race has any track; nothing will move")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
