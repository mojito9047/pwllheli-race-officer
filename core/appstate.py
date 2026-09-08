"""Application path, URL and version configuration.

This is the config foundation for the module split: the single home for the
filesystem layout, external data-source URLs, the app version and the surveyed
start-line position. app.py imports these names back into its namespace, so
existing references and the Jinja context processor keep resolving them
unchanged. Later refactor steps (db.py, etc.) read the paths from here so there
is one source of truth.

BASE_DIR is the PROJECT ROOT. Because this module lives in core/, that is two
levels up from this file (core/appstate.py -> core/ -> project root), not one.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = BASE_DIR / "runtime"
POLARS_DIR = DATA_DIR / "polars"
SAIL_CHARTS_DIR = DATA_DIR / "sailcharts"
CACHE_DIR = RUNTIME_DIR / "cache"
VIDEO_CLIPS_DIR = DATA_DIR / "video_clips"
BRANDING_DIR = DATA_DIR / "branding"
BRANDING_MANIFEST_PATH = BRANDING_DIR / "sponsor_logos.json"
DEFAULT_CLUB_LOGO_PATH = BASE_DIR / "static" / "img" / "pwllheli_sailing_club_logo.png"
VIDEO_RUNTIME_DIR = RUNTIME_DIR / "video"
VIDEO_BUFFER_DIR = VIDEO_RUNTIME_DIR / "buffer"
VIDEO_LOG_PATH = VIDEO_RUNTIME_DIR / "ffmpeg_recorder.log"
R2_TEST_RESULT_PATH = VIDEO_RUNTIME_DIR / "r2_test_result.json"
BRANDING_EXTENSIONS = {".png", ".jpg", ".jpeg"}
VIDEO_LIVE_DIR = VIDEO_RUNTIME_DIR / "live"
VIDEO_LIVE_JPG_PATH = VIDEO_LIVE_DIR / "latest.jpg"
VIDEO_PUBLIC_LIVE_JPG_PATH = VIDEO_LIVE_DIR / "latest_public.jpg"
VIDEO_PUBLIC_LIVE_HASH_PATH = VIDEO_LIVE_DIR / "latest_public.sha256"
PUBLIC_LIVE_R2_STATUS_PATH = VIDEO_RUNTIME_DIR / "r2_live_status.json"
DB_PATH = DATA_DIR / "race_officer.db"
LEGACY_DB_PATH = BASE_DIR / "race_officer.db"
LEGACY_VIDEO_CLIPS_DIR = DATA_DIR / "video" / "clips"
LEGACY_SAIL_CHART_PATH = DATA_DIR / "SailChart J122 North.txt"
SAIL_CHART_PATH = DATA_DIR / "DefaultSailChart.txt"
APP_VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip() if (BASE_DIR / "VERSION").exists() else "dev"
SESSION_APP_VERSION_KEY = "_app_version"
IRC_LISTING_URL = "https://www.topyacht.com.au/rorc/data/ClubListing.csv"
YTC_LISTING_URL = "https://docs.google.com/spreadsheets/d/1Se2j64wyx_61Ux8GB08i4gK1M2TezDe8uhZQtZhhtkQ/edit?usp=sharing?widget=true&headers=false"
DEFAULT_WEATHER_STATION_URL = "http://10.10.10.106/get_livedata_info?"

# Where the app itself lives. Shown in the public footer so a competitor who
# wonders what is calculating their corrected time can go and read it.
SOURCE_URL = "https://github.com/mojito9047/pwllheli-race-officer"
DEFAULT_COURSE_CHART_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_COURSE_CHART_OVERLAY_URL = "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png"
# Surveyed/entered bridge-window position used to draw the CHPSC start/finish line.
# Position supplied as 52 52.9238 N, 004 24.0609 W.
BRIDGE_WINDOW_LAT = 52.0 + (52.9238 / 60.0)
BRIDGE_WINDOW_LON = -(4.0 + (24.0609 / 60.0))


# ---------------------------------------------------------------------------
# Reloadable course / mark / start-finish source data.
#
# These are loaded once at import for speed, but the backup-restore page can
# replace the underlying JSON files while the app is running, so
# reload_course_mark_data() refreshes them in place. All readers (app.py, the
# extracted course-geometry module and Jinja templates) read these through this
# module so a reload is seen everywhere without a restart.
# ---------------------------------------------------------------------------
with (DATA_DIR / "courses.json").open("r", encoding="utf-8") as f:
    COURSES_DATA = json.load(f)
COURSES: List[Dict[str, Any]] = COURSES_DATA["courses"]
COURSE_BY_NO: Dict[int, Dict[str, Any]] = {int(c["course_no"]): c for c in COURSES}

with (DATA_DIR / "marks.json").open("r", encoding="utf-8") as f:
    MARKS_DATA = json.load(f)
MARKS: Dict[str, Dict[str, Any]] = MARKS_DATA["marks"]

with (DATA_DIR / "start_finish.json").open("r", encoding="utf-8") as f:
    START_FINISH = json.load(f)

# NOTE: the reload-after-restore function lives in app.py (reload_course_mark_data).
# It reads the data directory that the restore code wrote to and reassigns the
# module globals above. Keeping the reader there means it honours a
# monkeypatched DATA_DIR in the restore test, while these globals remain the
# single source every reader shares.
