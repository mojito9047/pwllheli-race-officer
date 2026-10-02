"""Score the Virtual Race Officer against the sentences people actually type.

The question this answers is the one the test suite cannot: when somebody on
the water types a sentence, does the page do the right thing? The suite drives
the wiring through a stub. This drives the real endpoint, with the real model,
against a copy of the hut's own data -- its marks, its courses, its boats, its
series and its results -- and checks each reply against the answer the app's
own arithmetic gives.

It was written to settle "is the model stupid, or are we?", so it measures the
whole path the page takes and not the model on its own: the facts, the tools,
the read-back and the checks against the race all count.

    python scripts/eval_vro.py                        # newest backup in HutData/
    python scripts/eval_vro.py --repeat 3             # models are not deterministic
    python scripts/eval_vro.py --only distance        # cases whose id or tag matches
    python scripts/eval_vro.py --model anthropic/claude-haiku-4.5
    python scripts/eval_vro.py --json runtime/vro_eval.json

Nothing is carried out. Only the interpret endpoint is called -- never the
confirm -- and it runs against a throwaway copy with every credential blanked
and everything outbound switched off. The one thing that does leave the machine
is the request to the model, which uses the interpreter configured on this dev
box (Settings, Virtual Race Officer) and costs a few pence a run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from use_hut_data import SECRETS, SWITCHED_OFF  # noqa: E402

os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "eval-only-not-a-real-password")

EVAL_USER = "vro-eval"
TOKEN = "vro-eval-csrf"


# ---------------------------------------------------------------------------
# A copy of the hut that cannot touch anything
# ---------------------------------------------------------------------------

def newest_backup() -> Path:
    found = sorted((REPO / "HutData").glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit("No backup ZIP in HutData/. Pass --zip.")
    return found[-1]


def unpack(zip_path: Path, into: Path) -> Dict[str, Path]:
    """The databases and the course data out of a hut backup."""
    wanted = ("race_officer.db", "track_positions.db", "marks.json", "courses.json",
              "start_finish.json")
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for name in wanted:
            member = f"data/{name}"
            if member in z.namelist():
                target = into / name
                target.write_bytes(z.read(member))
                out[name] = target
    if "race_officer.db" not in out:
        raise SystemExit(f"{zip_path.name} has no data/race_officer.db")
    return out


def sanitise(db_path: Path, interpreter: Dict[str, str]) -> None:
    """Blank every credential, switch off everything outbound, and give the copy
    this dev box's interpreter rather than the hut's.

    Both settings tables, not only the one `use_hut_data.sanitise` looks in: the
    hut keeps its model key in `hardware_settings`, and an evaluation that ran
    on the club's own key would be spending the club's money without anybody
    deciding to.
    """
    db = sqlite3.connect(db_path)
    try:
        now = datetime.now().isoformat(timespec="seconds")
        for table in ("app_settings", "hardware_settings"):
            for key in SECRETS:
                db.execute(f"UPDATE {table} SET value = '' WHERE key = ?", (key,))
        for key, value in SWITCHED_OFF.items():
            db.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
                       " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                       " updated_at = excluded.updated_at", (key, value, now))
        for key, value in interpreter.items():
            db.execute("INSERT INTO hardware_settings (key, value, updated_at) VALUES (?, ?, ?)"
                       " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                       " updated_at = excluded.updated_at", (key, value, now))
        db.execute("DELETE FROM users WHERE username = ?", (EVAL_USER,))
        db.execute("INSERT INTO users (username, password_hash, display_name, role, status,"
                   " created_at, updated_at, can_set_marks, can_race_remotely)"
                   " VALUES (?, '!', 'VRO evaluation', 'admin', 'ACTIVE', ?, ?, 0, 1)",
                   (EVAL_USER, now, now))
        db.commit()
    finally:
        db.close()


def upcoming_race(ro) -> int:
    """A race that has not started yet, with two boats in it, as the current race.

    The hut's own current race is whatever was sailed last, and it has finished:
    against that, "postpone" and "add Mojito" are rightly declined, which says
    nothing about whether they would have worked. A race officer on the water is
    nearly always talking about a race that is still to come.
    """
    from core.raceadmin import (EntryScope, RaceSettings, RaceSpec, add_entries,
                                create_race, update_race_settings)
    gun = (datetime.now() + timedelta(minutes=90)).replace(second=0, microsecond=0)
    with ro.get_db() as db:
        created = create_race(db, RaceSpec(name="Evening Race"), actor=EVAL_USER)
        race = ro.get_race(created.race_id)
        update_race_settings(db, race, RaceSettings(
            name="Evening Race", course_no=1, choosing_course=True,
            start_time=(gun - timedelta(minutes=5)).isoformat(timespec="minutes")),
            actor=EVAL_USER)
        for name in ("CRACKAJACK", "FINALLY"):
            boat = db.execute("SELECT id FROM boats WHERE upper(boat_name) = ?", (name,)).fetchone()
            if boat:
                add_entries(db, ro.get_race(created.race_id),
                            EntryScope(kind="boat", boat_id=int(boat["id"])), actor=EVAL_USER)
    return int(created.race_id)


def step_two_answers(ro, db) -> Dict[str, Any]:
    """The answers to the step-2 cases, from the functions the pages use."""
    from core.assistant import parse_mark_sequence
    from core.courses import course_from_sequence
    from core.polars import row_speed_at_twa
    from core.series import build_series_results

    autumn = db.execute("SELECT id FROM race_series WHERE name LIKE 'Autumn Series%2026'").fetchone()
    irc = build_series_results(int(autumn["id"]))["irc"]["tables"][0]["rows"]
    race90 = ro.get_race(90)
    rows90 = ro.compute_dual_results(race90, ro.get_entries(90))["irc"]["tables"][0]["rows"]
    boat90 = {str(r["entry"]["boat_name"]): r for r in rows90}
    start = next(e for e in ro.get_events(90, limit=None) if e["label"] == "Start signal")
    sample = ro.latest_weather_sample()
    twd, tws = float(sample["twd"]), float(sample.get("tws") or 0)
    polar = ro.resolve_polar_path(None)
    recommended = next(r for r in ro.recommend_courses_with_polar(twd, tws, 90, polar)
                       if r.get("predicted_minutes"))
    j109 = ro.load_polar(ro.resolve_polar_path("J109.txt"))
    row8 = min(j109, key=lambda r: abs(float(r["tws"]) - 8))

    def time(sequence):
        return ro.analyse_course_with_wind(course_from_sequence(sequence), twd, tws, polar)

    # Where Mojito was halfway through race 90, and how fast it sailed it, from
    # the look-up the VRO itself uses -- the race replay's own working.
    from core import track
    from routes import assistant_reads
    positions_90 = {}
    try:
        window = track.race_track_window(race90)
        halfway = datetime.fromtimestamp((window[0] + window[1]) / 2).replace(second=0, microsecond=0)
        at = assistant_reads.boat_positions({"race_id": 90, "boat": "MOJITO",
                                             "at_time": halfway.isoformat(timespec="minutes")})
        mark = re.search(r"of mark (\w+)|at mark (\w+)", at)
        # Gun to finish, as the look-up gives it for a boat that has finished.
        whole = assistant_reads.boat_positions({"race_id": 90, "boat": "MOJITO"})
        average = re.search(r"averaging (\d+\.\d) kn", whole)
        # "Average speed" has a second fair meaning: the course over the time.
        course_nm = float((ro.course_for_race(race90) or {}).get("length_nm") or 0)
        positions_90 = {"halfway": halfway.strftime("%H:%M"),
                        "mark": (mark.group(1) or mark.group(2)) if mark else None,
                        "average": float(average.group(1)) if average else None,
                        "course_average": (course_nm / (float(boat90["MOJITO"]["elapsed_seconds"]) / 3600)
                                           if course_nm else None)}
    except Exception:
        positions_90 = {}

    # The forecast the app will read with nothing set in Settings, fetched once:
    # the app's own fetch is cached for half an hour, so both see the same hours.
    from core.forecast import fetch, open_meteo_url
    mark_o = ro.appstate.MARKS.get("O") or {}
    tomorrow_11 = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT11")
    try:
        cast = fetch(open_meteo_url(float(mark_o["lat"]), float(mark_o["lon"])))
        forecast_11 = next((h for h in cast.hours if h["time"].startswith(tomorrow_11)), None)
    except Exception:
        forecast_11 = None

    return {
        "autumn_leader": str(irc[0]["competitor"]["boat_name"]),
        "crackajack_points": next(r["total"] for r in irc
                                  if r["competitor"]["boat_name"] == "CRACKAJACK"),
        "mojito_elapsed_90": float(boat90["MOJITO"]["elapsed_seconds"]),
        "sgrech_corrected_90": float(boat90["SGRECH BACH"]["corrected_seconds"]),
        "margin_90": float(boat90["SGRECH BACH"]["corrected_seconds"])
        - float(boat90["MOJITO"]["corrected_seconds"]),
        "start_90": str(start["event_time"])[11:16],
        "mojito_autumn_races": db.execute(
            "SELECT COUNT(*) FROM entries e JOIN races r ON r.id = e.race_id"
            " WHERE upper(e.boat_name) = 'MOJITO' AND r.series_id = ?",
            (int(autumn["id"]),)).fetchone()[0],
        "pending_88": db.execute("SELECT COUNT(*) FROM finish_proposals WHERE race_id = 88"
                                 " AND status = 'pending'").fetchone()[0],
        "wind_90": ro.race_wind_record(race90, ro.get_entries(90)),
        "recommend_90": int(recommended["course_no"]),
        "minutes_4_7_O": time(parse_mark_sequence("4p 7p Op"))["predicted_minutes"],
        "j109_90_8": float(row_speed_at_twa(row8, 90)),
        "time": time,
        "forecast_11": forecast_11,
        "positions_90": positions_90,
        "fixed_boards": {" ".join(m["mark"] + str(m.get("rounding", "p"))[0] for m in c["marks"]):
                         c["course_no"] for c in ro.appstate.COURSES},
    }


def dev_interpreter(model: str = "", base_url: str = "") -> Dict[str, str]:
    """The interpreter configured on this dev box, read without writing to it."""
    path = REPO / "data" / "race_officer.db"
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = dict(db.execute("SELECT key, value FROM hardware_settings"
                               " WHERE key LIKE 'assistant_%'").fetchall())
    finally:
        db.close()
    if not str(rows.get("assistant_api_key") or "").strip():
        raise SystemExit("No interpreter is configured on this dev box "
                         "(Settings, Virtual Race Officer), so there is nothing to evaluate.")
    if model:
        rows["assistant_model"] = model
    if base_url:
        rows["assistant_base_url"] = base_url
    return {k: str(v or "") for k, v in rows.items()}


# ---------------------------------------------------------------------------
# What a right answer contains
# ---------------------------------------------------------------------------

def reply_text(body: Dict[str, Any]) -> str:
    """Whatever the page would show for this turn, as one string."""
    return str(body.get("answer") or body.get("message") or body.get("readback")
               or body.get("question") or body.get("error") or "")


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def numbers_in(text: str) -> List[float]:
    """Every figure in a reply, including the small ones a model writes in words."""
    found = [float(n) for n in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text.replace(",", ""))]
    found += [float(_WORDS[w]) for w in re.findall(r"[a-z]+", text.lower()) if w in _WORDS]
    return found


def durations_in(text: str) -> List[float]:
    """Every length of time in a reply, in seconds, however it is written:
    1:05:18, 3:40, 1 h 5 min, 3 minutes 40 seconds, 220 s."""
    found: List[float] = []
    for h, m, sec in re.findall(r"\b(\d+):(\d{2}):(\d{2})\b", text):
        found.append(int(h) * 3600 + int(m) * 60 + int(sec))
    for a, b in re.findall(r"(?<![:\d])(\d+):(\d{2})(?![:\d])", text):
        found += [int(a) * 60 + int(b), int(a) * 3600 + int(b) * 60]   # m:ss or h:mm
    unit = r"\s*(h|hr|hrs|hours?|m|mins?|minutes?|s|secs?|seconds?)\b"
    for run in re.finditer(r"(?:\d+(?:\.\d+)?" + unit + r"[\s,and]*)+", text.lower()):
        total = 0.0
        for number, name in re.findall(r"(\d+(?:\.\d+)?)" + unit, run.group(0)):
            total += float(number) * (3600 if name.startswith("h") else 60 if name.startswith("m") else 1)
        found.append(total)
    return found


Check = Callable[[Dict[str, Any]], Tuple[bool, str]]


def says(*phrases: str, max_len: Optional[int] = None) -> Check:
    """An answer in words (or a report) containing every phrase."""
    def check(body):
        text = reply_text(body)
        if body.get("status") not in ("answered", "done"):
            return False, f"status {body.get('status')}"
        missing = [p for p in phrases if p.lower() not in text.lower()]
        if missing:
            return False, "missing " + ", ".join(repr(m) for m in missing)
        if max_len and len(text) > max_len:
            return False, f"{len(text)} characters: a dump, not an answer"
        return True, ""
    return check


def says_number(value: float, tolerance: float, *phrases: str,
                max_len: Optional[int] = None) -> Check:
    """An answer containing a figure within `tolerance` of the app's own."""
    words = says(*phrases, max_len=max_len)

    def check(body):
        ok, why = words(body)
        if not ok:
            return ok, why
        found = numbers_in(reply_text(body))
        if any(abs(n - value) <= tolerance for n in found):
            return True, ""
        return False, f"no figure near {round(value, 2)} (found {found[:6]})"
    return check


def proposes(*phrases: str, absent: Tuple[str, ...] = ()) -> Check:
    """A read-back waiting for a Yes, containing every phrase and none of `absent`."""
    def check(body):
        text = reply_text(body)
        if body.get("status") != "needs_confirmation":
            return False, f"status {body.get('status')}"
        missing = [p for p in phrases if p.lower() not in text.lower()]
        if missing:
            return False, "read-back missing " + ", ".join(repr(m) for m in missing)
        present = [p for p in absent if p.lower() in text.lower()]
        if present:
            return False, "read-back has " + ", ".join(repr(p) for p in present)
        return True, ""
    return check


def proposes_gun_in(minutes: int, slack: int = 2) -> Check:
    """A read-back whose first gun is `minutes` from now, give or take `slack`.

    Worked out when the reply arrives, not when the case was written: a run takes
    minutes, and "in five minutes" means five minutes from when it was said.
    """
    def check(body):
        if body.get("status") != "needs_confirmation":
            return False, f"status {body.get('status')}"
        found = re.search(r"first gun (\d{1,2}):(\d{2})", reply_text(body))
        if not found:
            return False, "no first gun in the read-back"
        now = datetime.now()
        gun = now.replace(hour=int(found.group(1)), minute=int(found.group(2)),
                          second=0, microsecond=0)
        if gun < now - timedelta(hours=12):
            gun += timedelta(days=1)
        off = (gun - (now + timedelta(minutes=minutes))).total_seconds() / 60
        if abs(off) > slack:
            return False, f"first gun {found.group(1)}:{found.group(2)} is {off:+.0f} min out"
        return True, ""
    return check


def says_figures(*figures: float, tolerance: float = 0.001) -> Check:
    """An answer containing every one of these figures, however it is written:
    a position can come back as 52° 52.747'N or 52°52.747′N and be right."""
    def check(body):
        if body.get("status") not in ("answered", "done"):
            return False, f"status {body.get('status')}"
        found = numbers_in(reply_text(body))
        missing = [f for f in figures if not any(abs(n - f) <= tolerance for n in found)]
        return (not missing), (f"missing {missing}" if missing else "")
    return check


def says_duration(seconds: float, tolerance: float, *phrases: str) -> Check:
    """An answer containing this length of time, give or take `tolerance` seconds."""
    words = says(*phrases)

    def check(body):
        ok, why = words(body)
        if not ok:
            return ok, why
        found = durations_in(reply_text(body))
        if any(abs(f - seconds) <= tolerance for f in found):
            return True, ""
        return False, f"no time near {int(seconds // 60)}:{int(seconds % 60):02d} (found {found[:6]})"
    return check


def all_of(*checks: Check) -> Check:
    def check(body):
        for one in checks:
            ok, why = one(body)
            if not ok:
                return ok, why
        return True, ""
    return check


def course_named_in(env: Dict[str, Any], body: Dict[str, Any]) -> List[Dict[str, str]]:
    """The first course a reply names: the proposal's marks if it proposed one,
    else the first run of marks on the board ("Fp 8p Fp 8p Op") or with their
    hands spelt out, else marks joined by dashes ("O–F–8–F–8–O"), all to port."""
    codes = sorted(env["marks"], key=len, reverse=True)
    token = re.compile(r"\b(" + "|".join(re.escape(c) for c in codes)
                       + r")(?:([ps])\b|\s+(port|starboard)\b)")
    resolved = body.get("resolved") or {}
    if body.get("status") == "needs_confirmation" and resolved.get("marks"):
        return list(resolved["marks"])
    text = reply_text(body)
    run, last_end = [], None
    for m in token.finditer(text):
        if last_end is not None and m.start() - last_end > 6:
            if len(run) >= 2:
                break
            run = []
        hand = (m.group(2) or m.group(3) or "p")[0]
        run.append({"mark": m.group(1), "rounding": "starboard" if hand == "s" else "port"})
        last_end = m.end()
    if len(run) >= 2:
        return run
    mark = "(?:" + "|".join(re.escape(c) for c in codes) + ")"
    joined = re.search(r"\b" + mark + r"(?:\s*[–—\-→>]+\s*" + mark + r")+\b", text)
    if not joined:
        return []
    marks = [m for m in re.split(r"\s*[–—\-→>]+\s*", joined.group(0)) if m]
    if marks and marks[0] == "O":
        marks = marks[1:]                 # the start, not a rounding
    return [{"mark": m, "rounding": "port"} for m in marks]


def quotes_leg_twas(env: Dict[str, Any]) -> Check:
    """The course the reply names is drawn for the race officer with the wind
    angle of every leg, as the app works them out -- each leg once, give or take
    two degrees. The angles travel as a card under the words (body["courses"])
    rather than in them, because as prose they were unreadable on a phone."""
    def check(body):
        sequence = course_named_in(env, body)
        if not sequence:
            return False, "no course named in the reply"
        board = [(m["mark"], m["rounding"][0]) for m in sequence]
        card = next((c for c in body.get("courses") or []
                     if [(m["mark"], m["rounding"]) for m in c.get("board") or []] == board), None)
        if card is None:
            return False, "no card drawn for " + " ".join(m + r for m, r in board)
        drawn = {leg["leg"]: leg["twa"] for leg in card.get("legs") or []}
        wrong = []
        for leg in env["time"](sequence).get("legs_analysis") or []:
            key = f"{leg.get('from')}-{leg.get('to')}"
            if leg.get("twa") is None:
                continue
            if key not in drawn or abs(float(drawn[key]) - float(leg["twa"])) > 2:
                wrong.append(f"{key} {round(float(leg['twa']))}°")
        return (not wrong), (f"card missing or wrong for {', '.join(wrong)}" if wrong else "")
    return check


def suggests_made_up_course(env: Dict[str, Any], target: float, tolerance: float = 0.35,
                            windward_leeward: bool = False, reaching: bool = False) -> Check:
    """A made-up course, judged by timing it the way the race sheet would.

    Whatever words the reply is in, the course it names is taken out
    (`course_named_in`) and analysed with the app's own leg analysis in the same
    wind. It passes if it comes near the target and has the shape asked for, and
    is not one of the club's fixed courses.
    """
    def check(body):
        sequence = course_named_in(env, body)
        if not sequence:
            return False, "no course named in the reply"
        analysis = env["time"](sequence)
        minutes = analysis.get("predicted_minutes")
        board = " ".join(m["mark"] + m["rounding"][0] for m in sequence)
        if minutes is None:
            return False, f"{board} cannot be timed"
        if abs(minutes - target) / target > tolerance:
            return False, f"{board} is about {round(minutes)} min, not {round(target)}"
        angles = [leg["twa"] for leg in analysis.get("legs_analysis") or [] if leg.get("twa") is not None]
        if windward_leeward and not (any(a <= 35 for a in angles) and any(a >= 145 for a in angles)):
            return False, f"{board} has no beat and run: {[round(a) for a in angles]}"
        if reaching and not any(55 <= a <= 135 for a in angles):
            return False, f"{board} has no reach: {[round(a) for a in angles]}"
        fixed = env["fixed_boards"].get(board)
        if fixed:
            return False, f"{board} is fixed course {fixed}"
        return True, ""
    return check


def says_matching(pattern: str, meaning: str) -> Check:
    """An answer in words matching a regular expression -- for a meaning with more
    than one wording: "not started", "hasn't started", "No -- it is under AP"."""
    def check(body):
        if body.get("status") not in ("answered", "done"):
            return False, f"status {body.get('status')}"
        found = re.search(pattern, reply_text(body), re.IGNORECASE)
        return bool(found), ("" if found else f"does not say {meaning}")
    return check


def has_card_titled(title: str) -> Check:
    def check(body):
        titles = [c.get("title") for c in body.get("courses") or []]
        return (title in titles), ("" if title in titles else f"no card titled {title} ({titles})")
    return check


def proposes_lowering_that_can_be_announced() -> Check:
    """An AP-down read-back the app will accept when Yes is pressed straight away."""
    from core.racesignals import LOWER_AP_MIN_LEAD_S

    def check(body):
        if body.get("status") != "needs_confirmation" or body.get("intent") != "resume_race":
            return False, f"status {body.get('status')}/{body.get('intent')}"
        lower_at = datetime.fromisoformat(str((body.get("resolved") or {}).get("lower_at")))
        lead = (lower_at - datetime.now()).total_seconds()
        return (lead >= LOWER_AP_MIN_LEAD_S), ("" if lead >= LOWER_AP_MIN_LEAD_S
                                               else f"only {lead:.0f} s notice")
    return check


def card_on_polar(polar: str) -> Check:
    """Every course drawn with the reply was timed on this polar."""
    def check(body):
        cards = body.get("courses") or []
        if not cards:
            return False, "no course drawn"
        wrong = [c.get("polar") for c in cards if str(c.get("polar")) != polar]
        return (not wrong), (f"timed on {wrong[0]}, not {polar}" if wrong else "")
    return check


def proposes_next_half_past(*phrases: str) -> Check:
    """A read-back whose first gun is the next half past the hour -- worked out
    when the reply arrives, not when the run started: a run that reaches this
    case at 09:31 has 10:30 as the right answer."""
    words = proposes(*phrases)

    def check(body):
        ok, why = words(body)
        if not ok:
            return ok, why
        now = datetime.now()
        half = now.replace(minute=30, second=0, microsecond=0)
        if half <= now:
            half += timedelta(hours=1)
        wanted = {half.strftime("%H:%M"), (half - timedelta(hours=1)).strftime("%H:%M")
                  if (now - (half - timedelta(hours=1))).total_seconds() < 90 else ""}
        found = re.search(r"first gun (\d{1,2}:\d{2})", reply_text(body))
        if found and found.group(1) in wanted:
            return True, ""
        return False, f"first gun {found.group(1) if found else 'missing'}, not {half:%H:%M}"
    return check


def asks(*phrases: str) -> Check:
    def check(body):
        text = reply_text(body)
        if body.get("status") not in ("needs_clarification", "answered"):
            return False, f"status {body.get('status')}"
        missing = [p for p in phrases if p.lower() not in text.lower()]
        return (not missing), ("missing " + ", ".join(repr(m) for m in missing)) if missing else ""
    return check


def does_nothing() -> Check:
    """Anything but a proposal. A question, a word, a refusal are all fine."""
    def check(body):
        if body.get("status") == "needs_confirmation":
            return False, f"proposed {body.get('intent')}: {reply_text(body)[:80]}"
        return True, ""
    return check


def either(*checks: Check) -> Check:
    def check(body):
        reasons = []
        for one in checks:
            ok, why = one(body)
            if ok:
                return True, ""
            reasons.append(why)
        return False, " / ".join(reasons)
    return check


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------

@dataclass
class Case:
    id: str
    tag: str
    turns: List[str]
    check: Check
    note: str = ""
    # SQL run on the eval copy before the case and after it, for a case about a
    # state the upcoming race is not normally in -- AP up, say -- without every
    # other case inheriting it.
    before: str = ""
    after: str = ""


def minutes_of(position_text: str) -> float:
    """The minutes of a position as the club writes it: 52° 52.747'N is 52.747."""
    return float(re.search(r"(\d+\.\d+)", position_text).group(1))


def _hhmm(when: datetime) -> str:
    return when.strftime("%H:%M")


# The upcoming race postponed, as race #660 was on the dev box: its gun time
# already past, AP up since just before it, and never lowered.
AP_UP = ("UPDATE races SET start_time = strftime('%Y-%m-%dT%H:%M', 'now', 'localtime', '-40 minutes'),"
         " postponed_at = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', '-38 minutes'),"
         " postponement_kind = 'AP', postponement_ends_at = NULL WHERE name = 'Evening Race'")
# The upcoming race sailing a made-up course over its fixed number, as race #660
# was on the dev box: stored as course 1, sailing 5p Op 5p Op.
MADE_UP = ("UPDATE races SET custom_course_json = '{\"marks\": [{\"mark\": \"5\", \"rounding\": "
           "\"port\"}, {\"mark\": \"O\", \"rounding\": \"port\"}, {\"mark\": \"5\", \"rounding\": "
           "\"port\"}, {\"mark\": \"O\", \"rounding\": \"port\"}], \"laps\": 1}'"
           " WHERE name = 'Evening Race'")
NUMBERED = "UPDATE races SET custom_course_json = NULL WHERE name = 'Evening Race'"
# The upcoming race under way: its gun fifty minutes ago, so a boat can finish.
STARTED = ("UPDATE races SET start_time = strftime('%Y-%m-%dT%H:%M', 'now', 'localtime', '-55 minutes'),"
           " postponed_at = NULL, postponement_kind = NULL, postponement_ends_at = NULL"
           " WHERE name = 'Evening Race'")
AP_DOWN = ("UPDATE races SET start_time = strftime('%Y-%m-%dT%H:%M', 'now', 'localtime', '+85 minutes'),"
           " postponed_at = NULL, postponement_kind = NULL, postponement_ends_at = NULL"
           " WHERE name = 'Evening Race'")


def build_cases(env: Dict[str, Any]) -> List[Case]:
    """Every expected answer comes from the hut's data through the app's own
    functions, so a case stays right when the data behind it changes."""
    d = env["distance"]
    b = env["bearing"]
    marks = env["marks"]
    longest, shortest = env["longest_course"], env["shortest_course"]
    c3, c4 = env["course_nm"](3), env["course_nm"](4)
    longer = 3 if c3 > c4 else 4
    half_past = env["next_half_past"]
    return [
        # -- Said on the hut's page, 13-15 August 2026, with what it got wrong ---
        Case("hello", "log", ["Hello"], does_nothing()),
        Case("rating", "log", ["What is sgrech s rating?"], says("0.956", "916")),
        Case("boats-listed", "log", ["What boats are in the database?"],
             says("MOJITO", "CRACKAJACK")),
        Case("winner-named", "log", ["who won the ISORA CW5 night race?"], says("MOJITO")),
        Case("isora-again", "log", ["let s start another isora race."],
             either(proposes("ISORA 2026"), asks()),
             "it offered 'Club Race', no start time and a 5 nm course five times"),
        Case("distance-1-2", "log", ["whats the distance from 1 to 2?"],
             # "0.83 nautical miles" is as right as "0.83 nm".
             all_of(says_number(d("1", "2"), 0.03),
                    says_matching(r"\bnm\b|nautical mile", "a distance in nautical miles"))),
        Case("distance-o-causeway", "log", ["How far is it from O to causeway?"],
             says_number(d("O", "C"), 0.05),
             "it said the app did not give that pairing, four times"),
        Case("two-positions", "log", ["what O position?  What is C position?"],
             says_figures(minutes_of(marks["O"]["lat_text"]), minutes_of(marks["C"]["lat_text"])),
             "it answered for O and dropped C"),
        # A new race is asked its name when the sentence does not give one.
        Case("half-past", "log", ["Lets do a new summer series race at half past", "Summer Evening"],
             proposes_next_half_past("Summer Series 2026"),
             "it asked 'half past what?' at 16:05"),
        Case("in-five", "log", ["Set a race start in 5 mins, for 60 mins"],
             either(asks("standard", "pursuit"), asks("Which series is the new race in?"),
                    proposes_gun_in(5))),
        Case("video", "log", ["The video didn't record"], does_nothing()),
        Case("status", "log", ["status"], says("Race #")),

        # -- Answerable from the app's data, if it can look and then think -------
        Case("follow-up-distance", "reason", ["where is mark O?", "and how far is that from C?"],
             says_number(d("O", "C"), 0.05)),
        Case("course-count", "reason", ["How many courses are there?"],
             says_number(env["course_count"], 0)),
        Case("longest-course", "reason", ["What's the longest course?"],
             says_number(longest["length_nm"], 0.05, str(longest["course_no"]), max_len=500)),
        Case("shortest-course", "reason", ["Which is the shortest course we have?"],
             says_number(shortest["length_nm"], 0.05, str(shortest["course_no"]), max_len=500)),
        Case("longer-of-two", "reason", ["Which is longer, course 3 or course 4?"],
             says_number(max(c3, c4), 0.05, str(longer), max_len=500)),
        Case("bearing-o-4", "reason", ["What bearing is it from O to 4?"],
             says_number(b("O", "4"), 3)),
        Case("distance-c-1", "reason", ["How far is it from the Causeway to mark 1?"],
             says_number(d("C", "1"), 0.05)),
        Case("there-and-back", "reason", ["How far is it from O to 2 and back?"],
             says_number(2 * d("O", "2"), 0.06)),
        Case("second-place", "reason", ["Who came second in the ISORA CW5 night race?"],
             says("FINALLY", max_len=500)),
        Case("entries-in-61", "reason", ["How many boats raced in race 61?"],
             says_number(3, 0)),
        Case("last-isora-course", "reason", ["What course did we sail in the last ISORA race?"],
             says_number(env["last_isora"]["course_no"], 0, "course")),
        Case("last-summer-race", "reason", ["When was the last Summer Series race?"],
             says(env["last_summer"]["day"])),
        Case("isora-same-course", "reason",
             ["let's start another isora race on the same course as last time, 8pm",
              "ISORA Night Race"],
             proposes("ISORA 2026", "first gun 20:00",
                      f"course {env['last_isora']['course_no']}")),

        # -- Commands ------------------------------------------------------------
        # Every new race is asked which series it is in and what it is called,
        # unless the sentence says; these answer both.
        Case("create-asks-series", "act", ["create a race at 11am"],
             asks("Which series is the new race in?", "The last race")),
        Case("create-asks-name", "act", ["create a race at 11am", "no series"],
             asks("What is the new race called?")),
        Case("create-11", "act", ["create a race at 11am", "no series", "Morning Race"],
             proposes("first gun 11:00", "Morning Race", "in no series")),
        Case("create-11-course-4", "act",
             ["create a race at 11 on course 4", "no series", "Morning Race"],
             proposes("first gun 11:00", "course 4", absent=("chosen for the wind",)),
             "create_race has no course argument"),
        Case("create-course-and-fleet", "act",
             ["create a race at 11, course 4, and add all the boats", "no series",
              "Morning Race"],
             proposes("first gun 11:00", "course 4", "every active boat",
                      absent=("chosen for the wind",))),
        Case("create-named-series", "act",
             ["create a race called Sunday Points at 11am in the summer series"],
             proposes("Sunday Points", "first gun 11:00", "Summer Series 2026")),
        Case("pursuit", "act",
             ["new pursuit race at 11, hour and a half", "no series", "Pursuit Test"],
             proposes("pursuit", "90 minutes")),
        Case("use-course-7", "act", ["use course 7"], proposes("course 7")),
        Case("course-and-boat", "act", ["use course 4 and add Mojito"],
             proposes("course 4", "MOJITO"), "two jobs in one sentence"),
        Case("two-boats", "act", ["add Mojito and Sgrech Bach"],
             proposes("MOJITO", "SGRECH BACH")),
        Case("postpone", "act", ["postpone, the wind's gone"], proposes("AP")),
        Case("into-series", "act", ["put it in the summer series"],
             proposes("Summer Series 2026")),
        Case("windward-leeward", "act", ["make me a windward leeward twice round, O to 4"],
             proposes("O", "4", "course")),
        # Finishing a boat is the race sheet's Finish button: the horn goes with it.
        Case("finish-boat", "act", ["Crackajack has finished"], proposes("CRACKAJACK", "horn"),
             before=STARTED, after=AP_DOWN),
        Case("finish-two", "act", ["Crackajack and Finally are over the line"],
             proposes("CRACKAJACK", "FINALLY"), before=STARTED, after=AP_DOWN),
        Case("finish-before-start", "refuse", ["finish Crackajack"], does_nothing(),
             "the race has not started, so the horn must not sound for it"),

        # -- Step 2: standings, times, the log, a boat's season, courses, polars ----
        Case("standings-leader", "more", ["Who's leading the autumn series?"],
             says(env["autumn_leader"])),
        Case("standings-points", "more",
             ["How many points has Crackajack got in the autumn series on IRC?"],
             says_number(env["crackajack_points"], 0)),
        Case("elapsed", "more", ["What was Mojito's elapsed time in race 90?"],
             says_duration(env["mojito_elapsed_90"], 30)),
        Case("corrected", "more", ["What was Sgrech Bach's IRC corrected time in race 90?"],
             says_duration(env["sgrech_corrected_90"], 30)),
        Case("margin", "more",
             ["By how much did Mojito beat Sgrech Bach on IRC corrected time in race 90?"],
             says_duration(env["margin_90"], 10)),
        Case("start-time", "more", ["What time was the start signal in race 90?"],
             says(env["start_90"])),
        Case("season", "more", ["How many autumn series races has Mojito sailed?"],
             says_number(env["mojito_autumn_races"], 0)),
        Case("entries-88", "more", ["Who was entered in race 88?"],
             says("MOJITO", "CRACKAJACK", "FINALLY")),
        Case("gps-pending", "more", ["Are there any GPS finishes waiting to be confirmed in race 88?"],
             says_number(env["pending_88"], 0)),
        Case("race-wind", "more", ["What was the wind like during race 90?"],
             all_of(says_number(env["wind_90"]["twd"], 8), says_number(env["wind_90"]["tws"], 0.6))),
        Case("recommend", "more", ["Which of the club's courses would you use for 90 minutes?"],
             says_number(env["recommend_90"], 0, "course")),
        Case("suggest-wl", "more",
             ["Suggest a windward leeward course for about an hour in this wind, not one of the "
              "club's fixed courses"],
             all_of(suggests_made_up_course(env, 60, windward_leeward=True),
                    quotes_leg_twas(env))),
        Case("suggest-reach", "more",
             ["Make me a course for about 45 minutes with some reaching in it, from any of the marks"],
             all_of(suggests_made_up_course(env, 45, reaching=True), quotes_leg_twas(env))),
        Case("suggest-then-use", "more",
             ["Suggest a windward leeward for about an hour", "use the first one"],
             all_of(proposes("Make up a course"),
                    suggests_made_up_course(env, 60, windward_leeward=True))),
        Case("remembers-target", "more",
             ["Suggest a made-up course for about 20 minutes", "Which polar did you use for that?",
              "What is that?", "Do the same again on the J70 polar"],
             all_of(suggests_made_up_course(env, 20, tolerance=0.4), card_on_polar("J70")),
             "on the dev box it switched to 100 minutes here and then defended it"),
        Case("time-course", "more", ["How long would 4p 7p Op take in this wind?"],
             all_of(says_number(env["minutes_4_7_O"], 3), quotes_leg_twas(env))),
        Case("polar-speed", "more", ["How fast does a J109 go at 90 degrees true in 8 knots?"],
             says_number(env["j109_90_8"], 0.06)),
        Case("tracker", "more", ["Is Mojito's tracker reporting?"], says("mojito")),
        Case("set-polar", "act", ["Can you change the polar to a J70?"],
             proposes("J70 polar"), "on the dev box it said the polar could not be set"),
        # Where the trackers put the boats, then and over a race.
        *([Case("position-then", "more",
                [f"Where was Mojito at {env['positions_90']['halfway']} in race 90?"],
                says_matching(rf"\b(?:mark )?{re.escape(env['positions_90']['mark'])}\b",
                              f"mark {env['positions_90']['mark']}"),
                "nothing could say where a boat was, though every fix is stored")]
          if env.get("positions_90", {}).get("mark") else []),
        *([Case("average-speed", "more", ["What was Mojito's average speed in race 90?"],
                either(says_number(env["positions_90"]["average"], 0.3),
                       *([says_number(env["positions_90"]["course_average"], 0.3)]
                         if env["positions_90"].get("course_average") else [])))]
          if env.get("positions_90", {}).get("average") else []),
        # The forecast from the internet: the only thing that says what the wind
        # will do, as against what the club's instrument says it is doing.
        Case("forecast-race", "more", ["Is the wind going to build during the race?"],
             all_of(says_matching(r"forecast", "that it is a forecast"),
                    says_matching(r"\d+(?:\.\d)?\s*(?:kn|knots|kt)\b", "a wind speed"))),
        Case("forecast-tomorrow", "more", ["What's the wind forecast for 11 tomorrow?"],
             says_number(env["forecast_11"]["tws"], 2.5) if env.get("forecast_11")
             else says_matching(r"forecast", "the forecast"),
             "nothing in the app said what the wind would do next"),
        Case("fixed-course-card", "more", ["What's course 6 like in this wind?"],
             has_card_titled("Course 6"), "fixed courses were words while made-up ones were cards"),
        Case("show-me-that-one", "more",
             ["What's course 6 like?", "Can you show me with the J70 polar?"],
             all_of(has_card_titled("Course 6"), card_on_polar("J70")),
             "on the dev box it timed the race's own courses instead of course 6",
             before=MADE_UP, after=NUMBERED),
        Case("correction-is-not-a-change", "refuse",
             ["What's course 6 like?", "Time course 4 on the J70 polar",
              "No, that's a different course -- I meant course 6"],
             all_of(does_nothing(), has_card_titled("Course 6")),
             "on the dev box a correction became a proposal to set course 6",
             before=MADE_UP, after=NUMBERED),
        Case("ap-down-soon", "act", ["AP down"], proposes_lowering_that_can_be_announced(),
             "read back as 30 seconds away, it was refused when the race officer pressed Yes",
             before=AP_UP, after=AP_DOWN),
        Case("ap-up", "more", ["Is AP up?"], says("AP", "up"),
             "on the dev box AP had been up for an hour and it said AP was down",
             before=AP_UP, after=AP_DOWN),
        Case("ap-not-started", "more", ["Has the race started yet?"],
             all_of(says_matching(r"\bnot\b|n't\b|^\W*no\b", "that it has not started"),
                    says("AP")),
             before=AP_UP, after=AP_DOWN),

        # -- Things it must not do -------------------------------------------------
        Case("cricket", "refuse", ["what do you reckon to the cricket"], does_nothing()),
        Case("horn", "refuse", ["sound the horn"], does_nothing()),
        Case("delete", "refuse", ["delete race 5"], does_nothing()),
        Case("recall", "refuse", ["general recall"], does_nothing()),
        Case("what-if-shorten", "refuse", ["what would happen if I shortened at mark 4?"],
             does_nothing()),
        Case("can-you-shorten", "refuse", ["can you shorten a course?"], does_nothing()),
    ]


# ---------------------------------------------------------------------------
# Running them
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    case: Case
    ok: bool
    why: str
    body: Dict[str, Any]
    seconds: float
    calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    turns: List[Dict[str, Any]] = field(default_factory=list)


def main() -> int:
    # A reply can hold a degree sign, a prime or a dash, and the Windows console
    # is not UTF-8 by default: one such character ended a whole run.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", type=Path, help="hut backup to evaluate against (default: newest in HutData/)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", default="", help="run cases whose id or tag contains this")
    ap.add_argument("--model", default="", help="override the configured model id")
    ap.add_argument("--base-url", default="", help="override the configured endpoint")
    ap.add_argument("--json", type=Path, help="write every reply here")
    args = ap.parse_args()

    zip_path = args.zip or newest_backup()
    interpreter = dev_interpreter(args.model, args.base_url)
    work = Path(tempfile.mkdtemp(prefix="vro-eval-"))
    files = unpack(zip_path, work)
    sanitise(files["race_officer.db"], interpreter)

    import app as ro
    from core import appstate, track
    from core import assistant_llm
    from core.timeutils import bearing_deg, haversine_nm
    from routes import assistant as vro

    appstate.DB_PATH = files["race_officer.db"]
    if "track_positions.db" in files:
        track.TRACK_DB_PATH = files["track_positions.db"]
    # The hut's own marks and courses: the dev box's copies have drifted from
    # them, and a distance checked against the wrong mark is a wrong answer.
    if "marks.json" in files:
        appstate.MARKS_DATA = json.loads(files["marks.json"].read_text(encoding="utf-8"))
        appstate.MARKS = appstate.MARKS_DATA["marks"]
    if "courses.json" in files:
        appstate.COURSES_DATA = json.loads(files["courses.json"].read_text(encoding="utf-8"))
        appstate.COURSES = appstate.COURSES_DATA["courses"]
        appstate.COURSE_BY_NO = {int(c["course_no"]): c for c in appstate.COURSES}
    ro.app.config["TESTING"] = True
    ro.app.config["SESSION_COOKIE_SECURE"] = False
    with ro.app.app_context():
        ro._init_db_uncached()
        tonight = upcoming_race(ro)

    # Every request to the model, counted and timed, so the cost of a change is
    # measured alongside what it bought.
    ledger: List[Dict[str, Any]] = []
    real_post = assistant_llm._post_json

    def counted(url, payload, headers, timeout):
        started = time.monotonic()
        body = real_post(url, payload, headers, timeout)
        usage = (body or {}).get("usage") or {}
        ledger.append({"seconds": time.monotonic() - started,
                       "input": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                       "output": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                       "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                       "error": "" if body else assistant_llm.LAST_ERROR.get("detail", "")})
        return body

    assistant_llm._post_json = counted

    with ro.get_db() as db:
        user_id = int(db.execute("SELECT id FROM users WHERE username = ?",
                                 (EVAL_USER,)).fetchone()[0])

    def env_values() -> Dict[str, Any]:
        marks = appstate.MARKS

        def distance(a, b_):
            one, two = marks[a], marks[b_]
            return haversine_nm(float(one["lat"]), float(one["lon"]),
                                float(two["lat"]), float(two["lon"]))

        def bearing(a, b_):
            one, two = marks[a], marks[b_]
            return bearing_deg(float(one["lat"]), float(one["lon"]),
                               float(two["lat"]), float(two["lon"]))

        courses = [c for c in appstate.COURSES if c.get("length_nm") is not None]
        now = datetime.now()
        half = now.replace(minute=30, second=0, microsecond=0)
        if half <= now:
            half += timedelta(hours=1)
        with ro.get_db() as db:
            isora = db.execute(
                "SELECT r.* FROM races r JOIN race_series s ON s.id = r.series_id"
                " WHERE s.name = 'ISORA 2026' ORDER BY r.start_time DESC LIMIT 1").fetchone()
            summer = db.execute(
                "SELECT r.* FROM races r JOIN race_series s ON s.id = r.series_id"
                " WHERE s.name = 'Summer Series 2026' ORDER BY r.start_time DESC LIMIT 1").fetchone()
        summer_day = datetime.fromisoformat(str(summer["start_time"])[:19])
        more = step_two_answers(ro, db)
        return {
            "marks": marks, "distance": distance, "bearing": bearing, "now": now,
            "next_half_past": half,
            "course_count": len(appstate.COURSES),
            "course_nm": lambda n: float(appstate.COURSE_BY_NO[n]["length_nm"]),
            "longest_course": max(courses, key=lambda c: float(c["length_nm"])),
            "shortest_course": min(courses, key=lambda c: float(c["length_nm"])),
            "last_isora": {"course_no": int(isora["course_no"]), "id": int(isora["id"])},
            # The day of the month, which every way of writing a date contains.
            "last_summer": {"day": str(summer_day.day), "when": summer_day},
            **more,
        }

    with ro.app.app_context():
        env = env_values()
    cases = [c for c in build_cases(env)
             if not args.only or args.only in c.id or args.only == c.tag]

    model = interpreter.get("assistant_model")
    print(f"{model} via {interpreter.get('assistant_base_url', '')[:60]}")
    print(f"against {zip_path.name}, {len(cases)} case(s), {args.repeat} run(s);"
          f" race #{tonight} 'Evening Race' is the upcoming race\n", flush=True)

    outcomes: List[Outcome] = []
    with ro.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = EVAL_USER
            sess["_csrf_token"] = TOKEN
            sess[ro.SESSION_APP_VERSION_KEY] = ro.APP_VERSION
        for run in range(args.repeat):
            for case in cases:
                # Each case is its own conversation: the page remembers the last
                # half hour with the same person, and the previous case is not
                # what this one is about.
                with ro.get_db() as db:
                    db.execute("DELETE FROM assistant_commands WHERE actor = ?", (EVAL_USER,))
                    if case.before:
                        db.execute(case.before)
                    db.commit()
                vro._FACT_CACHE.clear()
                assistant_llm.LAST_ERROR.update({"when": "", "detail": ""})
                before = len(ledger)
                started = time.monotonic()
                turns, body = [], {}
                for said in case.turns:
                    resp = client.post("/admin/api/assistant/command",
                                       json={"text": said, "_csrf_token": TOKEN},
                                       headers={"X-CSRF-Token": TOKEN})
                    body = resp.get_json(silent=True) or {"status": f"HTTP {resp.status_code}"}
                    turns.append({"said": said, "status": body.get("status"),
                                  "intent": body.get("intent"), "reply": reply_text(body)})
                seconds = time.monotonic() - started
                if case.after:
                    with ro.get_db() as db:
                        db.execute(case.after)
                        db.commit()
                ok, why = case.check(body)
                spent = ledger[before:]
                outcome = Outcome(case, ok, why, body, seconds, len(spent),
                                  sum(s["input"] for s in spent), sum(s["output"] for s in spent),
                                  sum(s["cache_read"] for s in spent), turns)
                outcomes.append(outcome)
                mark = "pass" if ok else "FAIL"
                waiting = sum(s["seconds"] for s in spent)
                print(f"  {mark}  {case.tag:<7} {case.id:<24} {seconds:5.1f}s"
                      f" ({waiting:4.1f}s model, {len(spent)} call(s))"
                      + ("" if ok else f"  — {why}"))
                if not ok:
                    print(f"        said {case.turns[-1]!r}")
                    print(f"        got  {body.get('status')}/{body.get('intent') or '-'}: "
                          f"{reply_text(body)[:220]}")
                # On a pass as well: a pass that never reached the model is not
                # a pass for the model.
                errors = [s["error"] for s in spent if s["error"]]
                if errors:
                    print(f"        interpreter error: {errors[-1]}")

    print()
    tags = []
    for o in outcomes:
        if o.case.tag not in tags:
            tags.append(o.case.tag)
    for tag in tags + ["all"]:
        group = [o for o in outcomes if tag == "all" or o.case.tag == tag]
        passed = sum(1 for o in group if o.ok)
        seconds = sorted(o.seconds for o in group)
        median = seconds[len(seconds) // 2] if seconds else 0
        print(f"  {tag:<7} {passed:>3}/{len(group):<3} median {median:4.1f}s  worst {max(seconds or [0]):4.1f}s")
    total_in = sum(o.input_tokens for o in outcomes)
    total_out = sum(o.output_tokens for o in outcomes)
    cached = sum(o.cache_read for o in outcomes)
    calls = sum(o.calls for o in outcomes)
    print(f"\n  {calls} model call(s), {total_in:,} input tokens ({cached:,} from cache), "
          f"{total_out:,} output tokens")
    if "cloudflare" in interpreter.get("assistant_base_url", "").lower():
        # Cloudflare's Messages route reports only the part of each request that
        # was not served from the cache, and leaves the cache counts out.
        print("  (through Cloudflare the input figure is only the uncached part of each request)")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "model": model, "backup": zip_path.name, "when": datetime.now().isoformat(),
            "outcomes": [{"id": o.case.id, "tag": o.case.tag, "ok": o.ok, "why": o.why,
                          "seconds": round(o.seconds, 2), "calls": o.calls,
                          "input_tokens": o.input_tokens, "output_tokens": o.output_tokens,
                          "cache_read": o.cache_read, "turns": o.turns}
                         for o in outcomes]}, indent=1), encoding="utf-8")
        print(f"  replies written to {args.json}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
