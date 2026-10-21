"""DEVELOPMENT ONLY. Not for a production system.

A temporary Liverpool YC race officer on :5059, with one simulated race already sailed,
for trying another club's files on a developer's PC: charts, results, a 3D film.

    python club_configs\\liverpool_yc\\sim_app.py            start it (keeps what is there)
    python club_configs\\liverpool_yc\\sim_app.py --rebuild  start again from nothing

It is not how the app is installed, and it must not be run at a club:

* Flask's development server rather than Waitress, on 127.0.0.1 only, with the
  cookies' Secure flag off for plain http, and no startup task.
* The admin password is a generated test one written to a plain text file.
* Its race, tracks and wind are made up and written straight into its database.
* It points the app at a sandbox by patching module attributes at start-up.

A real Liverpool installation is an ordinary release with these three JSON files
copied into its data/ folder (docs/CLUB_CUSTOMISATION.md). club_configs is not in
the release ZIP.

* Its own data folder, runtime/lyc_sim/data, kept between runs: Liverpool's
  marks.json, courses.json and start_finish.json, a database of its own (the
  admin password is a generated test one, in runtime/lyc_sim_login.txt) and no
  Pwllheli sponsor logos. Anything set in its Settings survives a restart.
* Seven Liverpool YC boats with their real IRC TCC and YTC numbers from the
  app's own cached listings.
* Race 9001 -- an id no Pwllheli race has, so a film made from it cannot land on
  one of theirs in the shared bucket -- at 14:00 on the day it was built (the day
  before, if that was less than two hours ago), on a made-up river course:
  PLK (the line) -> G11 to port -> NEL to starboard -> back to the line.
* Every boat sailed offline under a similar design's polar, its track written
  into the database as if a tracker had sent it, its finish taken where the
  track crossed the line, and the hut wind written as weather samples.
"""
import json
import math
import os
import random
import secrets
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LYC = Path(__file__).resolve().parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

SANDBOX = REPO / "runtime" / "lyc_sim"
LOGIN = REPO / "runtime" / "lyc_sim_login.txt"
RACE_ID = 9001
# A south-westerly, the Mersey's usual wind. It puts both legs along the river on
# a reach, so nobody tacks: the simulated boats know nothing about land, and a
# beat straight up the river sent them a kilometre into Dingle on one board.
TWD, TWS = 235.0, 12.0
# G11 to E04 is the upwind leg: 1.38 nm on 256 degrees, 21 off the wind, in the
# wide water below Garston where there is room to tack. The rest are reaches.
COURSE = [{"mark": "G11", "rounding": "port"}, {"mark": "E04", "rounding": "port"},
          {"mark": "NEL", "rounding": "starboard"}]
# name, sail no, design, IRC TCC, YTC, polar, percentage of it sailed
FLEET = [
    ("ARCTURUS OF LYMINGTON", "GBR5622T", "Jeanneau Sun Fast 42", 1.014, 850, "Sunfast 3600.txt", 0.97),
    ("BOJANGLES", "GBR9496T", "J/109", 0.996, 865, "J109.txt", 0.98),
    ("ONLY JUST", "GBR1510R", "J/105", 0.993, 856, "J105.txt", 0.97),
    ("DAYDREAM BELIEVER", "GBR7347R", "Beneteau First 34.7", 0.968, 894, "Beneteau First 367.txt", 0.95),
    ("SHAULA", "GBR9227T", "Jeanneau Sun Fast 37", 0.962, 894, "Sunfast 3600.txt", 0.93),
    ("NO MERCY", "GBR9807R", "Prism 25 custom", 0.946, 900, "J80.txt", 0.96),
    ("TILLER GIRL", "4483", "Sigma 33 OD", 0.906, 938, "X332.txt", 0.86),
]

if "--rebuild" in sys.argv and SANDBOX.exists():
    shutil.rmtree(SANDBOX)
data = SANDBOX / "data"
fresh = not (data / "race_officer.db").exists()

if fresh:
    data.mkdir(parents=True, exist_ok=True)
    for item in (REPO / "data").iterdir():
        if item.is_dir():
            if item.name in ("polars", "sailcharts"):
                shutil.copytree(item, data / item.name, dirs_exist_ok=True)
        elif item.suffix in (".txt", ".json"):
            shutil.copy2(item, data / item.name)
    for name in ("marks.json", "courses.json", "start_finish.json"):
        shutil.copy2(LYC / name, data / name)
    password = secrets.token_urlsafe(12)
    LOGIN.write_text(f"Temporary Liverpool app at http://localhost:5059/admin/login\n"
                     f"username: admin\npassword: {password}\n", encoding="utf-8")
    # Only read when the database is first made, which is now.
    os.environ["RO_INITIAL_ADMIN_PASSWORD"] = password
else:
    os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", secrets.token_urlsafe(12))
os.environ["RO_COOKIE_SECURE"] = "0"
# The chart's tracks and replay player only show with GPS tracking on. No Traccar
# address is set, so nothing is polled: the tracks are the ones written below.
os.environ["RO_TRACK_ENABLED"] = "1"

from core import appstate  # noqa: E402

appstate.DATA_DIR = data
for attr, parts in {"POLARS_DIR": ("polars",), "SAIL_CHARTS_DIR": ("sailcharts",),
                    "VIDEO_CLIPS_DIR": ("video_clips",), "LEGACY_VIDEO_CLIPS_DIR": ("video", "clips"),
                    "BRANDING_DIR": ("branding",), "BRANDING_MANIFEST_PATH": ("branding", "sponsor_logos.json"),
                    "SAIL_CHART_PATH": ("DefaultSailChart.txt",)}.items():
    setattr(appstate, attr, data.joinpath(*parts))
appstate.DB_PATH = data / "race_officer.db"
appstate.RUNTIME_DIR = SANDBOX / "runtime"
appstate.VIDEO_RUNTIME_DIR = appstate.RUNTIME_DIR / "video"

import app as ro  # noqa: E402
from core import db, power, settings, track  # noqa: E402
import simulate_trackers as sim  # noqa: E402

track.TRACK_DB_PATH = data / "track_positions.db"
track.TRACK_DB_INITIALIZED = False
power.POWER_DB_PATH = data / "power_history.db"
power.POWER_DB_INITIALIZED = False
db.DB_INITIALIZED = False
db.DATA_LAYOUT_MIGRATED = False
settings.APP_SETTINGS_CACHE_VALUES = None
ro.reload_course_mark_data()


def sail_the_race() -> None:
    """Sail the fleet offline and write the race as if it had happened."""
    warning = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
    if warning > datetime.now() - timedelta(hours=2):
        warning -= timedelta(days=1)
    gun = warning + timedelta(minutes=5)
    gun_ts = gun.timestamp()

    marks = appstate.MARKS
    line = appstate.START_FINISH["finish_lines"][0]
    seaward = (float(marks[line["seaward"]["mark"]]["lat"]), float(marks[line["seaward"]["mark"]]["lon"]))
    shore = (float(line["shore"]["lat"]), float(line["shore"]["lon"]))
    course_pts = [(float(marks[m["mark"]]["lat"]), float(marks[m["mark"]]["lon"])) for m in COURSE]
    path = sim.build_path(course_pts, seaward, True, shore)

    def side(p):
        return math.copysign(1.0, (shore[0] - seaward[0]) * (p[1] - seaward[1])
                             - (shore[1] - seaward[1]) * (p[0] - seaward[0]))

    def crossed_between_ends(a, b):
        """Did the step a->b cross the line segment itself (plus its seaward extension)?"""
        ext = float(line.get("seaward_extension_m") or 0.0)
        m_lat, m_lon = 111_320.0, 111_320.0 * math.cos(math.radians(seaward[0]))
        sx, sy = (seaward[1] - shore[1]) * m_lon, (seaward[0] - shore[0]) * m_lat
        length = math.hypot(sx, sy)
        ex, ey = sx / length * (length + ext), sy / length * (length + ext)
        ax, ay = (a[1] - shore[1]) * m_lon, (a[0] - shore[0]) * m_lat
        bx, by = (b[1] - shore[1]) * m_lon, (b[0] - shore[0]) * m_lat
        d1, d2 = ex * ay - ey * ax, ex * by - ey * bx
        if d1 * d2 > 0:
            return False
        t = d1 / (d1 - d2) if d1 != d2 else 0.0
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        return 0.0 <= (px * ex + py * ey) / (ex * ex + ey * ey) <= 1.0

    sailed = {}
    for i, (name, sail, design, irc, ytc, polar, pct) in enumerate(FLEET):
        rows = sim.load_polar_rows(str(data / "polars" / polar))
        boat = sim.SailingBoat(f"LYC-{i + 1}", path, pct, 3.0 * 60.0, rows, 0.0, upwind_bias=0.0)
        fixes, t, next_fix, finish_t, marks_done = [], 0.0, 0.0, None, False
        prev = boat.pos
        while boat.step(t, 1.0, TWD, TWS) and t < 4 * 3600:
            t += 1.0
            if boat.wp >= len(path) - 2:
                marks_done = True
            if (marks_done and finish_t is None and side(boat.pos) != side(prev)
                    and crossed_between_ends(prev, boat.pos)):
                finish_t = t
            prev = boat.pos
            if t >= next_fix:
                next_fix += 5.0
                fixes.append({"t": gun_ts + t, "lat": boat.pos[0], "lon": boat.pos[1],
                              "sog": boat.speed_kn, "cog": boat.heading})
        sailed[f"LYC-{i + 1}"] = {"fixes": fixes, "finish_s": finish_t, "fleet": FLEET[i]}
        print(f"  {name:<22} {polar:<24} {pct:.0%}  finish "
              f"{'none' if finish_t is None else f'{finish_t / 60:.1f} min'}", flush=True)

    now_iso = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as conn:
        # DUAL, as the app's own New race and Edit race always save a standard
        # race. Left out, the column default 'IRC_TCC' is what a 2025 race has.
        conn.execute("INSERT INTO races (id, course_set, name, course_no, start_time, created_at,"
                     " custom_course_json, polar_file, finish_line_key, gps_finish_enabled, rating_rule)"
                     " VALUES (?, 1, ?, 1, ?, ?, ?, ?, ?, 1, 'DUAL')",
                     (RACE_ID, "LYC River Race (simulated)", warning.isoformat(timespec="seconds"),
                      now_iso, json.dumps({"marks": COURSE}), "J109.txt", line["key"]))
        for dev, info in sailed.items():
            name, sail, design, irc, ytc, polar, pct = info["fleet"]
            cur = conn.execute("INSERT INTO boats (boat_name, sail_no, status, irc_rating, ytc_rating,"
                               " created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?)",
                               (name, sail, irc, ytc, now_iso, now_iso))
            boat_id = int(cur.lastrowid)
            finish = (gun + timedelta(seconds=info["finish_s"])).isoformat(timespec="seconds") \
                if info["finish_s"] is not None else None
            conn.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status, finish_time,"
                         " finish_source, manual_irc_rating, manual_ytc_rating)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (RACE_ID, boat_id, name, sail, "FINISHED" if finish else "RACING",
                          finish, "gps-simulated" if finish else None, irc, ytc))
            conn.execute("INSERT INTO trackers (unique_id, label, boat_id, active, updated_at)"
                         " VALUES (?, ?, ?, 1, ?)", (dev, name, boat_id, now_iso))
        # The hut wind: the same south-westerly, backing and veering a few degrees.
        last = max(gun_ts + (i["finish_s"] or 0) for i in sailed.values()) + 600
        rnd = random.Random(RACE_ID)
        ts = warning.timestamp() - 600
        while ts <= last:
            drift = 6.0 * math.sin((ts - gun_ts) / 1500.0)
            conn.execute("INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source)"
                         " VALUES (?, ?, ?, ?, ?, 'simulated')",
                         (ts, datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                          round((TWD + drift + rnd.uniform(-3, 3)) % 360, 1),
                          round(TWS + rnd.uniform(-1.2, 1.2), 1), round(TWS + 3 + rnd.uniform(0, 2), 1)))
            ts += 10.0
        conn.commit()

    for i, (dev, info) in enumerate(sailed.items()):
        track.insert_positions(
            [{"device_id": 900 + i, "unique_id": dev, "name": dev, "boat_id": None,
              "lat": f["lat"], "lon": f["lon"], "speed_kn": f["sog"], "course_deg": f["cog"],
              "fix_time": f["t"], "server_time": f["t"]} for f in info["fixes"]], retention_days=3650)
    print(f"race {RACE_ID} sailed at {warning:%d %b %H:%M}", flush=True)


def forget_the_race() -> None:
    """Take the simulated race out again, leaving the login and the settings alone."""
    devices = [f"LYC-{i + 1}" for i in range(len(FLEET))]
    with ro.get_db() as conn:
        conn.execute("DELETE FROM entries WHERE race_id = ?", (RACE_ID,))
        conn.execute("DELETE FROM races WHERE id = ?", (RACE_ID,))
        conn.executemany("DELETE FROM trackers WHERE unique_id = ?", [(d,) for d in devices])
        conn.executemany("DELETE FROM boats WHERE boat_name = ? AND sail_no = ?",
                         [(name, sail) for name, sail, *_ in FLEET])
        conn.execute("DELETE FROM weather_samples WHERE source = 'simulated'")
        conn.commit()
    with track.get_track_db() as tdb:
        tdb.executemany("DELETE FROM track_positions WHERE unique_id = ?", [(d,) for d in devices])
        tdb.commit()


with ro.app.app_context():
    ro._init_db_uncached()
    with ro.get_db() as conn:
        # The club's name, as Settings -> Club name and branding would save it --
        # once, so a change made in the app is not put back on the next start.
        for key, value in (("club_name", "Liverpool Yacht Club"), ("club_short_name", "Liverpool")):
            conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                         (key, value, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        settings.invalidate_app_settings_cache()
        row = conn.execute("SELECT custom_course_json FROM races WHERE id = ?", (RACE_ID,)).fetchone()
    # Edit COURSE above and restart, and the race is sailed again on the new one.
    if row is not None and json.loads(row["custom_course_json"] or "{}").get("marks") != COURSE:
        print(f"race {RACE_ID}'s course has changed; sailing it again", flush=True)
        forget_the_race()
        row = None
    if row is None:
        sail_the_race()

print("DEVELOPMENT ONLY: a simulated Liverpool race on Flask's development server,"
      " not a production system.", flush=True)
print(f"{'new' if fresh else 'existing'} data in {data}", flush=True)
print(f"race {RACE_ID}: http://localhost:5059/public/race/{RACE_ID}  (login details in {LOGIN})", flush=True)
ro.app.run(host="127.0.0.1", port=5059, use_reloader=False)
