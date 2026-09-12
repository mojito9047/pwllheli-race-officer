"""A track repair has to say which days, and it did not.

``scripts/fix_track_misattribution.py`` detaches one device's fixes from a boat
they were never that boat's. It was written for CRACKAJACK on 8 August 2026,
where a second tracker was aboard MOJITO all day, and it did that job.

But it matched on the pairing alone, with no time bound, so it took every fix
that device had ever recorded for that boat -- 30,154 of them, from 6 August to
the 17th. These are club loaners that move from boat to boat between races, and
that same device really was aboard Crackajack a week later. She then vanished
from the chart, and from the film, of a race she had sailed with it aboard, and
the finished run said nothing to suggest it.

So: bounds, a refusal when an unbounded run would take good days with the bad,
and a way back for a repair that already took too much.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fix_track_misattribution.py"

_spec = importlib.util.spec_from_file_location("fix_track_misattribution", _SCRIPT)
fixer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixer)


WRONG, OWN, THEIRS = "WRONG-DEVICE", "OWN-DEVICE", "OTHER-BOAT-DEVICE"
BOAT, OTHER = 21, 1

# Midday UTC, so the day a fix falls on is the same day whether the script reads
# it as UTC (the SQL date()) or as local time (the --from/--until bounds). Any
# timezone within eleven hours of UTC sees these three days the same way.
DAYS = ["2026-08-06", "2026-08-07", "2026-08-08"]
ABOARD_DAY = "2026-08-07"


def _noon(day: str) -> float:
    return datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=timezone.utc).timestamp()


@pytest.fixture
def track_db(tmp_path):
    """Three days of two boats, with one device changing hands in the middle.

    The wrong device sits on its own boat on days one and three and is aboard
    the other boat on day two -- which is the shape of the real case, and the
    shape a single unbounded UPDATE cannot tell apart.
    """
    path = tmp_path / "track_positions.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE track_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
               " unique_id TEXT, boat_id INTEGER, lat REAL, lon REAL, fix_time REAL)")
    rows = []
    for day in DAYS:
        base = _noon(day)
        for i in range(60):
            t = base + i * 60
            # The other boat, and its own tracker, sitting still.
            rows.append((THEIRS, OTHER, 52.9000, -4.4000, t))
            # Our boat's own tracker, a kilometre away.
            rows.append((OWN, BOAT, 52.9100, -4.4000, t))
            # The device under suspicion: with the other boat on the aboard day,
            # with its own boat otherwise.
            if day == ABOARD_DAY:
                rows.append((WRONG, BOAT, 52.9000, -4.4000, t))
            else:
                rows.append((WRONG, BOAT, 52.9100, -4.4000, t))
    db.executemany("INSERT INTO track_positions (unique_id, boat_id, lat, lon, fix_time)"
                   " VALUES (?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return path


def _pairing_counts(path):
    db = sqlite3.connect(path)
    try:
        return {(u, b): n for u, b, n in db.execute(
            "SELECT unique_id, boat_id, COUNT(*) FROM track_positions GROUP BY unique_id, boat_id")}
    finally:
        db.close()


def _run(path, *args):
    return fixer.main(["--device", WRONG, "--boat", str(BOAT), "--db", str(path), *args])


class TestAnUnboundedRepairIsRefused:
    """The bug, as a test: one bad day among good ones must not take them all."""

    def test_it_refuses_and_writes_nothing(self, track_db, capsys):
        before = _pairing_counts(track_db)
        assert _run(track_db, "--apply") == 1
        assert _pairing_counts(track_db) == before, \
            "an unbounded repair wrote anyway; this is the change that lost a race"

    def test_it_names_the_day_and_offers_the_bounds(self, track_db, capsys):
        _run(track_db, "--apply")
        out = capsys.readouterr().out
        assert "REFUSING" in out
        assert ABOARD_DAY in out, "the refusal does not say which day it could see"
        assert f"--from {ABOARD_DAY}" in out, "the refusal does not offer a window to use"

    def test_a_device_aboard_for_its_whole_span_is_still_allowed(self, tmp_path):
        """No good days to lose, so nothing to refuse -- the original case."""
        path = tmp_path / "t.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE track_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " unique_id TEXT, boat_id INTEGER, lat REAL, lon REAL, fix_time REAL)")
        base = _noon(ABOARD_DAY)
        rows = []
        for i in range(60):
            t = base + i * 60
            rows.extend([(THEIRS, OTHER, 52.9, -4.4, t), (OWN, BOAT, 52.91, -4.4, t),
                         (WRONG, BOAT, 52.9, -4.4, t)])
        db.executemany("INSERT INTO track_positions (unique_id, boat_id, lat, lon, fix_time)"
                       " VALUES (?,?,?,?,?)", rows)
        db.commit()
        db.close()
        assert _run(path, "--apply") == 0
        assert (WRONG, BOAT) not in _pairing_counts(path)


class TestABoundedRepairTakesOnlyThoseDays:
    def test_the_other_days_keep_their_boat(self, track_db):
        assert _run(track_db, "--from", ABOARD_DAY, "--until", "2026-08-08", "--apply") == 0
        counts = _pairing_counts(track_db)
        assert counts[(WRONG, BOAT)] == 120, \
            "the days either side of the bad one were detached too"
        assert counts[(WRONG, None)] == 60

    def test_a_window_with_nothing_in_it_changes_nothing(self, track_db):
        before = _pairing_counts(track_db)
        assert _run(track_db, "--from", "2026-09-01", "--until", "2026-09-02", "--apply") == 0
        assert _pairing_counts(track_db) == before

    def test_the_window_has_to_run_forwards(self, track_db):
        with pytest.raises(SystemExit):
            _run(track_db, "--from", "2026-08-08", "--until", "2026-08-06", "--apply")

    def test_a_bound_it_cannot_read_is_refused_before_it_opens_anything(self, track_db):
        with pytest.raises(SystemExit) as exc:
            _run(track_db, "--from", "last Tuesday", "--apply")
        assert "date or time" in str(exc.value)


class TestGivingItBack:
    """The way out of a repair that took too much, which is how this was found."""

    def test_it_restores_the_days_that_were_not_the_problem(self, track_db):
        _run(track_db, "--apply")                      # refused, changes nothing
        _run(track_db, "--from", DAYS[0], "--until", "2026-08-09", "--apply")
        assert _pairing_counts(track_db).get((WRONG, BOAT)) is None, "nothing was detached"

        assert _run(track_db, "--reattach", "--from", DAYS[2], "--until", "2026-08-09",
                    "--apply") == 0
        counts = _pairing_counts(track_db)
        assert counts[(WRONG, BOAT)] == 60, "the good day did not come back"
        assert counts[(WRONG, None)] == 120, "more came back than was asked for"

    def test_it_will_not_hand_over_a_day_the_device_was_elsewhere(self, track_db):
        _run(track_db, "--from", DAYS[0], "--until", "2026-08-09", "--apply")
        assert _run(track_db, "--reattach", "--from", ABOARD_DAY, "--until", "2026-08-08",
                    "--apply") == 1, "it gave a boat fixes recorded on another boat"
        assert _pairing_counts(track_db).get((WRONG, BOAT)) is None

    def test_it_insists_on_both_bounds(self, track_db):
        """Unbounded in this direction sweeps in fixes that were never the boat's.

        The device kept reporting after it was unpaired -- in the real case for
        two more days, in a drawer. Those rows are NULL because nobody had
        claimed them, and a blanket reattach would claim them retrospectively.
        """
        for args in (("--reattach", "--apply"),
                     ("--reattach", "--from", DAYS[0], "--apply"),
                     ("--reattach", "--until", DAYS[2], "--apply")):
            with pytest.raises(SystemExit) as exc:
                _run(track_db, *args)
            assert "--from and --until" in str(exc.value)


class TestTheRefusalsComeBeforeTheWrite:
    def test_a_boat_left_with_no_track_at_all_is_still_refused(self, tmp_path):
        """The guard that was already there, kept honest by a bounded run."""
        path = tmp_path / "t.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE track_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " unique_id TEXT, boat_id INTEGER, lat REAL, lon REAL, fix_time REAL)")
        base = _noon(ABOARD_DAY)
        rows = []
        for i in range(60):
            t = base + i * 60
            rows.extend([(THEIRS, OTHER, 52.9, -4.4, t), (WRONG, BOAT, 52.9, -4.4, t)])
        db.executemany("INSERT INTO track_positions (unique_id, boat_id, lat, lon, fix_time)"
                       " VALUES (?,?,?,?,?)", rows)
        db.commit()
        db.close()
        assert _run(path, "--from", ABOARD_DAY, "--until", "2026-08-08", "--apply") == 1
        assert _pairing_counts(path)[(WRONG, BOAT)] == 60
