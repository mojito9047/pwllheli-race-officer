r"""Phone screenshot of the public Start photo tab, for the Competitor Guide.

    .venv\Scripts\python.exe scripts\capture_start_photo_screen.py


Runs the app from a throwaway copy of the data folder, with one finished race
whose start photo is race 90's as published (the buoy hidden at the gun, the
line drawn from the minute before), and captures the tab at phone size.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "scripts" / "competitor_screens" / "07_race_start_photo_mobile.png"
PHOTO_URL = "https://assets.pwllhelisailingclub.org/racevideos/race90/race90_start_363_photo.jpg"
os.chdir(REPO)
sys.path.insert(0, str(REPO))
os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "capturepass")
os.environ["RO_COOKIE_SECURE"] = "0"

import app as ro  # noqa: E402
from core import appstate, db, power, settings, track  # noqa: E402

sandbox = Path(tempfile.mkdtemp(prefix="startphoto_capture_"))
data = sandbox / "data"
for source in appstate.DATA_DIR.iterdir():
    if source.name.startswith(".") or ".db" in source.name.lower() or source.name in ("video_clips", "video", "dem"):
        continue
    if source.is_dir():
        shutil.copytree(source, data / source.name, copy_function=shutil.copyfile,
                        ignore=lambda _d, names: [n for n in names if ".db" in n.lower()])
    else:
        data.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, data / source.name)
appstate.DATA_DIR = data
for name, parts in {"POLARS_DIR": ("polars",), "SAIL_CHARTS_DIR": ("sailcharts",),
                    "VIDEO_CLIPS_DIR": ("video_clips",), "LEGACY_VIDEO_CLIPS_DIR": ("video", "clips"),
                    "BRANDING_DIR": ("branding",), "BRANDING_MANIFEST_PATH": ("branding", "sponsor_logos.json"),
                    "SAIL_CHART_PATH": ("DefaultSailChart.txt",),
                    "LEGACY_SAIL_CHART_PATH": ("SailChart J122 North.txt",)}.items():
    setattr(appstate, name, data.joinpath(*parts))
appstate.DB_PATH = sandbox / "race_officer.db"
appstate.RUNTIME_DIR = sandbox / "runtime"
appstate.VIDEO_RUNTIME_DIR = appstate.RUNTIME_DIR / "video"
track.TRACK_DB_PATH = sandbox / "track_positions.db"
track.TRACK_DB_INITIALIZED = False
power.POWER_DB_PATH = sandbox / "power_history.db"
power.POWER_DB_INITIALIZED = False
db.DB_INITIALIZED = False
db.DATA_LAYOUT_MIGRATED = False
settings.APP_SETTINGS_CACHE_VALUES = None
with ro.app.app_context():
    ro._init_db_uncached()

warning = "2026-09-20T14:15:00"
with ro.get_db() as conn:
    cur = conn.execute("INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
                       " VALUES ('Race 4', 'IRC', 3, ?, 'DUAL', '', ?)", (warning, warning))
    race_id = int(cur.lastrowid)
    for i, (boat, sail, done) in enumerate((("Mojito Bach", "GBR 9338", "15:31:12"),
                                             ("Andromeda", "GBR 4822R", "15:33:40"),
                                             ("Erasmus", "GBR 1234", "15:36:05")), start=1):
        conn.execute("INSERT INTO entries (race_id, boat_name, sail_no, class_name, status, finish_time)"
                     " VALUES (?, ?, ?, 'IRC', 'FINISHED', ?)", (race_id, boat, sail, f"2026-09-20T{done}"))
    conn.commit()
event = ro.startphoto._race_start_times(ro.get_race(race_id))[0]
with ro.get_db() as conn:
    cur = conn.execute("INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds, post_seconds, status, label,"
                       " created_at, updated_at) VALUES (?, 'start', ?, 60, 60, 'ready', 'Start video', ?, ?)",
                       (race_id, event, event, event))
    clip_id = int(cur.lastrowid)
    conn.commit()
public = appstate.VIDEO_CLIPS_DIR / "public" / f"race{race_id}_start_{clip_id}_photo.jpg"
public.parent.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(urllib.request.Request(PHOTO_URL, headers={"User-Agent": "pwllheli-capture/1.0"}),
                            timeout=60) as resp, open(public, "wb") as f:
    shutil.copyfileobj(resp, f)
ro.startphoto.update_start_photo(clip_id, status="ready", source="recording", file_path=str(public),
                                 message="Made afterwards from the start video.")

from werkzeug.serving import make_server  # noqa: E402
server = make_server("127.0.0.1", 5059, ro.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright  # noqa: E402
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:5059/public/race/{race_id}#ptab-startphoto", wait_until="load")
        page.wait_for_function("() => { const i = document.querySelector('#ptab-startphoto img');"
                               " return i && i.complete && i.naturalWidth > 0; }", timeout=30000)
        page.evaluate("document.querySelector('#ptab-startphoto .tab-nav, .public-tabs .tab-nav').scrollIntoView({block: 'start'})")
        page.evaluate("window.scrollBy(0, -8)")
        time.sleep(0.5)
        page.screenshot(path=str(OUT))
        # The tabs and the photo only: the page's fixed header cuts across the race
        # status box above them, and the footer is not what the figure is about.
        from PIL import Image
        with Image.open(OUT) as shot:
            shot.crop((0, 296, shot.width, 1362)).save(OUT)
        browser.close()
    print("saved", OUT)
finally:
    server.shutdown()
    shutil.rmtree(sandbox, ignore_errors=True)
