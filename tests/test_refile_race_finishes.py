"""Finishing a fleet against the wrong race, and getting both races back.

The race officer finishes boats from a race page, and the list offers every race
the club has ever sailed. Pick one from last season with a similar name and the
whole afternoon is recorded there: finish times, the horn events behind them,
and the evidence clips the recorder cuts and publishes. Nothing on the water
says so -- the page looks right and the horn still sounds.

It happened on 19 September 2026: today's Race 1 was scored against "R1 - 13th
Sept" from the 2025 series, three clips went to ``racevideos/race1/``, and last
year's race was overwritten in passing -- two boats set to DNC, a finisher
deleted, an entry added, the rating rule and course replaced.

So the tool has two halves, and both of them have to be careful about what they
touch: the move must take only the rows that are really today's, and the restore
must not take away something somebody added on purpose.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refile_race_finishes.py"
_spec = importlib.util.spec_from_file_location("refile_race_finishes", _SCRIPT)
refile = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("refile_race_finishes", refile)
_spec.loader.exec_module(refile)

OLD, NEW = 1, 87          # last year's race, and the one actually sailed
LAST_YEAR, TODAY = "2025-09-13", "2026-09-19"

SCHEMA = """
CREATE TABLE races (id INTEGER PRIMARY KEY, name TEXT, start_time TEXT,
                    rating_rule TEXT, custom_course_json TEXT);
CREATE TABLE entries (id INTEGER PRIMARY KEY, race_id INTEGER, boat_id INTEGER,
                      boat_name TEXT, status TEXT, finish_time TEXT);
CREATE TABLE race_events (id INTEGER PRIMARY KEY, race_id INTEGER, event_time TEXT,
                          event_type TEXT, label TEXT);
CREATE TABLE video_clips (id INTEGER PRIMARY KEY, race_id INTEGER, entry_id INTEGER,
                          clip_type TEXT, event_time TEXT, file_path TEXT,
                          public_file_path TEXT, public_object_key TEXT, public_url TEXT);
"""


def _clip(db, cid, race, entry, when, kind="manual_horn"):
    db.execute(
        "INSERT INTO video_clips (id, race_id, entry_id, clip_type, event_time, file_path,"
        " public_file_path, public_object_key, public_url) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, race, entry, kind, when,
         f"data\\video_clips\\race{race}_{kind}_{cid}.mp4",
         f"data\\video_clips\\public\\race{race}_{kind}_{cid}_public.mp4",
         f"racevideos/race{race}/race{race}_{kind}_{cid}_public.mp4",
         f"https://assets.example/racevideos/race{race}/race{race}_{kind}_{cid}_public.mp4"))


@pytest.fixture
def hut(tmp_path, monkeypatch):
    """Last year's race scribbled over with today's racing, as it was found."""
    path = tmp_path / "race_officer.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO races VALUES (?,?,?,?,?)",
               (OLD, "R1 - 13th Sept", f"{LAST_YEAR}T10:30:00", "DUAL", '{"marks": []}'))
    db.execute("INSERT INTO races VALUES (?,?,?,?,?)",
               (NEW, "Race 1", f"{TODAY}T11:30:00", "DUAL", '{"marks": []}'))
    # Last year's fleet, as the incident left it.
    db.execute("INSERT INTO entries VALUES (1,?,1,'Mojito','FINISHED',?)",
               (OLD, f"{TODAY}T12:39:16"))
    db.execute("INSERT INTO entries VALUES (2,?,2,'Mojito Bach','DNC',NULL)", (OLD,))
    db.execute("INSERT INTO entries VALUES (427,?,21,'CRACKAJACK','FINISHED',?)",
               (OLD, f"{TODAY}T12:51:10"))
    # Today's race, whose times the office has already put right.
    db.execute("INSERT INTO entries VALUES (422,?,1,'MOJITO','FINISHED',?)",
               (NEW, f"{TODAY}T12:39:16"))
    db.execute("INSERT INTO entries VALUES (425,?,21,'CRACKAJACK','FINISHED',?)",
               (NEW, f"{TODAY}T12:51:10"))
    # One clip really is last year's, and must not move.
    _clip(db, 309, OLD, None, f"{LAST_YEAR}T10:35:00", kind="start")
    _clip(db, 342, OLD, 1, f"{TODAY}T12:39:16")
    _clip(db, 343, OLD, 427, f"{TODAY}T12:51:10")
    db.execute("INSERT INTO race_events VALUES (900,?,?,'start','Last year')",
               (OLD, f"{LAST_YEAR}T10:30:00"))
    db.execute("INSERT INTO race_events VALUES (1087,?,?,'manual-horn-input','Horn')",
               (OLD, f"{TODAY}T12:39:16"))
    db.execute("INSERT INTO race_events VALUES (1088,?,?,'finish','Finish for Mojito')",
               (OLD, f"{TODAY}T12:42:09"))
    db.commit()
    db.close()

    class _Appstate:
        BASE_DIR = tmp_path
        DB_PATH = path
    monkeypatch.setitem(sys.modules, "core.appstate", _Appstate)
    return path


@pytest.fixture
def before(tmp_path):
    """A backup from before the incident: the race as it should be."""
    path = tmp_path / "backup.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO races VALUES (?,?,?,?,?)",
               (OLD, "R1 - 13th Sept", f"{LAST_YEAR}T10:30:00", "IRC_TCC", None))
    db.execute("INSERT INTO entries VALUES (1,?,1,'Mojito','FINISHED',?)",
               (OLD, f"{LAST_YEAR}T12:18:18"))
    db.execute("INSERT INTO entries VALUES (2,?,2,'Mojito Bach','FINISHED',?)",
               (OLD, f"{LAST_YEAR}T12:33:35"))
    db.execute("INSERT INTO entries VALUES (5,?,5,'Sgrech','FINISHED',?)",
               (OLD, f"{LAST_YEAR}T12:37:49"))
    db.commit()
    db.close()
    return path


def _run(hut, *extra):
    return refile.main(["--from", str(OLD), "--to", str(NEW), "--db", str(hut), *extra])


def _rows(hut, sql, *args):
    db = sqlite3.connect(hut)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(sql, args)]
    finally:
        db.close()


class TestMovingTodaysWork:
    def test_only_the_rows_dated_today_move(self, hut):
        assert _run(hut, "--apply") == 0
        assert {r["id"] for r in _rows(hut, "SELECT id FROM video_clips WHERE race_id=?", NEW)} \
            == {342, 343}
        assert {r["id"] for r in _rows(hut, "SELECT id FROM video_clips WHERE race_id=?", OLD)} \
            == {309}, "last year's own start clip was dragged along"
        assert {r["id"] for r in _rows(hut, "SELECT id FROM race_events WHERE race_id=?", OLD)} \
            == {900}

    def test_each_clip_follows_its_boat_not_its_entry_number(self, hut):
        _run(hut, "--apply")
        got = {r["id"]: r["entry_id"] for r in
               _rows(hut, "SELECT id, entry_id FROM video_clips WHERE race_id=?", NEW)}
        assert got == {342: 422, 343: 425}

    def test_the_files_and_the_key_are_renamed_the_way_the_app_names_them(self, hut):
        _run(hut, "--apply")
        clip = _rows(hut, "SELECT * FROM video_clips WHERE id=342")[0]
        assert clip["public_object_key"] == "racevideos/race87/race87_manual_horn_342_public.mp4"
        assert clip["file_path"] == "data\\video_clips\\race87_manual_horn_342.mp4"
        assert clip["public_url"].endswith("/racevideos/race87/race87_manual_horn_342_public.mp4")
        assert "race1" not in clip["public_url"]

    def test_a_dry_run_writes_nothing(self, hut):
        before_rows = _rows(hut, "SELECT id, race_id, entry_id FROM video_clips ORDER BY id")
        assert _run(hut) == 0
        assert _rows(hut, "SELECT id, race_id, entry_id FROM video_clips ORDER BY id") == before_rows

    def test_a_boat_with_no_entry_in_the_destination_stops_it(self, hut):
        db = sqlite3.connect(hut)
        db.execute("DELETE FROM entries WHERE id=425")       # CRACKAJACK not entered today
        db.commit()
        db.close()
        assert _run(hut, "--apply") == 1
        assert _rows(hut, "SELECT id FROM video_clips WHERE race_id=?", NEW) == [], \
            "it moved some clips before giving up on one"

    def test_two_races_on_one_day_are_refused(self, hut):
        """Nothing in a row would say which of them it came from."""
        db = sqlite3.connect(hut)
        db.execute("UPDATE races SET start_time=? WHERE id=?", (f"{TODAY}T09:00:00", OLD))
        db.commit()
        db.close()
        with pytest.raises(SystemExit) as exc:
            _run(hut, "--apply")
        assert "cannot tell them apart" in str(exc.value)


class TestRestoringTheRaceThatWasScribbledOn:
    def test_it_comes_back_exactly_as_the_backup_has_it(self, hut, before):
        assert _run(hut, "--restore-from", str(before), "--apply") == 0
        got = {r["id"]: (r["status"], r["finish_time"]) for r in
               _rows(hut, "SELECT * FROM entries WHERE race_id=?", OLD)}
        assert got == {
            1: ("FINISHED", f"{LAST_YEAR}T12:18:18"),
            2: ("FINISHED", f"{LAST_YEAR}T12:33:35"),
            5: ("FINISHED", f"{LAST_YEAR}T12:37:49"),
        }
        race = _rows(hut, "SELECT * FROM races WHERE id=?", OLD)[0]
        assert race["rating_rule"] == "IRC_TCC" and race["custom_course_json"] is None

    def test_the_deleted_finisher_is_put_back(self, hut, before):
        assert 5 not in {r["id"] for r in _rows(hut, "SELECT id FROM entries WHERE race_id=?", OLD)}
        _run(hut, "--restore-from", str(before), "--apply")
        assert 5 in {r["id"] for r in _rows(hut, "SELECT id FROM entries WHERE race_id=?", OLD)}

    def test_an_entry_added_during_the_incident_is_removed(self, hut, before):
        _run(hut, "--restore-from", str(before), "--apply")
        assert 427 not in {r["id"] for r in _rows(hut, "SELECT id FROM entries WHERE race_id=?", OLD)}

    def test_an_entry_added_for_a_boat_not_racing_today_is_left_alone(self, hut, before):
        """The guard against tidying away somebody's deliberate work.

        Only an entry whose boat is also in the race actually sailed can be
        explained by the mistake. Anything else was added for its own reasons
        and is none of this tool's business.
        """
        db = sqlite3.connect(hut)
        db.execute("INSERT INTO entries VALUES (500,?,99,'Latecomer','DNC',NULL)", (OLD,))
        db.commit()
        db.close()
        _run(hut, "--restore-from", str(before), "--apply")
        assert 500 in {r["id"] for r in _rows(hut, "SELECT id FROM entries WHERE race_id=?", OLD)}

    def test_a_dry_run_restores_nothing(self, hut, before):
        _run(hut, "--restore-from", str(before))
        got = _rows(hut, "SELECT id, status FROM entries WHERE race_id=? ORDER BY id", OLD)
        assert {r["id"] for r in got} == {1, 2, 427}, "the dry run changed the entry list"


class TestABadBackupPathIsCaughtBeforeAnythingIsWritten:
    """Found by running it: a mistyped backup name took the move with it.

    ``sqlite3.connect`` creates an empty database for a path that is not there,
    so a wrong name opened cleanly and fell over several screens later on a
    missing table. Worse, the move had already committed by then -- the clips
    were refiled and the scribbled-on race left as it was, which is the half of
    the repair nobody would go looking for.
    """

    def test_a_missing_backup_is_named_plainly(self, hut, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _run(hut, "--restore-from", str(tmp_path / "not-here.zip"), "--apply")
        assert "no backup at" in str(exc.value)

    def test_and_the_move_did_not_happen(self, hut, tmp_path):
        with pytest.raises(SystemExit):
            _run(hut, "--restore-from", str(tmp_path / "not-here.zip"), "--apply")
        assert _rows(hut, "SELECT id FROM video_clips WHERE race_id=?", NEW) == [], \
            "the clips were refiled before the backup was even opened"
        assert len(_rows(hut, "SELECT id FROM race_events WHERE race_id=?", OLD)) == 3

    def test_a_file_that_is_not_a_race_database_is_refused(self, hut, tmp_path):
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        with pytest.raises(SystemExit) as exc:
            _run(hut, "--restore-from", str(empty), "--apply")
        assert "not a race database" in str(exc.value)

    def test_a_backup_taken_after_the_mistake_is_refused(self, hut, tmp_path):
        """It would put the mistake back rather than take it away."""
        late = tmp_path / "late.db"
        db = sqlite3.connect(late)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO races VALUES (?,?,?,?,?)",
                   (OLD, "R1 - 13th Sept", f"{LAST_YEAR}T10:30:00", "DUAL", None))
        db.execute("INSERT INTO race_events VALUES (1087,?,?,'manual-horn-input','Horn')",
                   (OLD, f"{TODAY}T12:39:16"))
        db.commit()
        db.close()
        with pytest.raises(SystemExit) as exc:
            _run(hut, "--restore-from", str(late), "--apply")
        assert "taken after the mistake" in str(exc.value)
        assert _rows(hut, "SELECT id FROM video_clips WHERE race_id=?", NEW) == []
