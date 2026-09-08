"""Regression tests for wind-history retention tied to races.

Ambient wind samples are purged after 24 hours, but samples inside a race's
window (one hour before its first warning signal to one hour after its last
recorded finish) are kept regardless of age. A race with no finishes yet is
treated as still open and never purged.
"""
from __future__ import annotations

import time
from datetime import datetime

import app as ro


def _insert_race(db, *, start_time: str, course_no: int = 1) -> int:
    now_iso = datetime.now().isoformat(timespec="seconds")
    cur = db.execute(
        "INSERT INTO races (name, course_no, start_time, rating_rule, created_at) VALUES (?, ?, ?, 'IRC_TCC', ?)",
        ("Test race", course_no, start_time, now_iso),
    )
    return int(cur.lastrowid)


def _insert_entry(db, race_id: int, *, finish_time: str | None) -> None:
    db.execute(
        "INSERT INTO entries (race_id, boat_name, status, finish_time) VALUES (?, ?, ?, ?)",
        (race_id, "Test Boat", "FINISHED" if finish_time else "RACING", finish_time),
    )


def _insert_sample(db, sample_time: float) -> None:
    iso = datetime.fromtimestamp(sample_time).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sample_time, iso, 180.0, 12.0, 14.0, "test", "{}"),
    )


def _sample_times(db) -> list[float]:
    return [row["sample_time"] for row in db.execute("SELECT sample_time FROM weather_samples ORDER BY sample_time").fetchall()]


class TestWindRetention:
    def test_ambient_samples_purged_after_24h_with_no_races(self, client):
        now = time.time()
        with ro.get_db() as db:
            _insert_sample(db, now - 25 * 3600)
            db.commit()

        ro.insert_weather_sample(180.0, 10.0, 12.0, "test", {})

        with ro.get_db() as db:
            remaining = _sample_times(db)
        assert all(t >= now - 24 * 3600 for t in remaining)

    def test_sample_inside_finished_race_window_survives_past_24h(self, client):
        now = time.time()
        start_iso = datetime.fromtimestamp(now - 30 * 3600).isoformat(timespec="seconds")
        finish_iso = datetime.fromtimestamp(now - 29 * 3600).isoformat(timespec="seconds")
        with ro.get_db() as db:
            race_id = _insert_race(db, start_time=start_iso)
            _insert_entry(db, race_id, finish_time=finish_iso)
            # Window is [start - 1h, finish + 1h] = [now-31h, now-28h].
            _insert_sample(db, now - 29.5 * 3600)
            # Well outside any race window and old -> should be purged.
            _insert_sample(db, now - 50 * 3600)
            db.commit()

        ro.insert_weather_sample(180.0, 10.0, 12.0, "test", {})

        with ro.get_db() as db:
            remaining = _sample_times(db)
        assert any(abs(t - (now - 29.5 * 3600)) < 1 for t in remaining)
        assert all(abs(t - (now - 50 * 3600)) > 1 for t in remaining)

    def test_open_race_with_no_finish_keeps_old_samples_indefinitely(self, client):
        now = time.time()
        start_iso = datetime.fromtimestamp(now - 30 * 3600).isoformat(timespec="seconds")
        with ro.get_db() as db:
            _insert_race(db, start_time=start_iso)
            # No entries/finishes recorded, so the race is treated as still open.
            _insert_sample(db, now - 29 * 3600)
            db.commit()

        ro.insert_weather_sample(180.0, 10.0, 12.0, "test", {})

        with ro.get_db() as db:
            remaining = _sample_times(db)
        assert any(abs(t - (now - 29 * 3600)) < 1 for t in remaining)
