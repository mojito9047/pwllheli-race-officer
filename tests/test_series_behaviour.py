"""Regression tests for series entry synchronisation and scoring rules."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro
from core import entrysync


def _create_series_with_two_races(db):
    now = "2026-06-01T08:00:00"
    cur = db.execute(
        """
        INSERT INTO race_series (name, description, discard_profile, min_races_to_constitute, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Spring Series", "", ro.DEFAULT_DISCARD_PROFILE, 1, now, now),
    )
    series_id = int(cur.lastrowid)
    race_ids = []
    for name, start_time in [("Race 1", "2026-06-01T10:00:00"), ("Race 2", "2026-06-08T10:00:00")]:
        cur = db.execute(
            """
            INSERT INTO races (name, class_name, series_id, course_no, start_time, rating_rule, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, "IRC", series_id, 1, start_time, "DUAL", "", start_time),
        )
        race_ids.append(int(cur.lastrowid))
    return series_id, race_ids[0], race_ids[1]


def _create_boat(db, name: str, sail_no: str, irc: float = 1.000, ytc: int = 1000):
    now = "2026-06-01T08:00:00"
    cur = db.execute(
        """
        INSERT INTO boats (boat_name, sail_no, class_name, owner, design, club, irc_rating, ytc_rating, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '', ?, ?)
        """,
        (name, sail_no, "IRC", "", "", "", irc, ytc, now, now),
    )
    return db.execute("SELECT * FROM boats WHERE id = ?", (int(cur.lastrowid),)).fetchone()


def _race(db, race_id: int):
    return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _entry_statuses_by_race(db, boat_id: int) -> dict[int, str]:
    rows = db.execute(
        "SELECT race_id, status FROM entries WHERE boat_id = ? ORDER BY race_id",
        (boat_id,),
    ).fetchall()
    return {int(row["race_id"]): row["status"] for row in rows}


class TestLateSeriesEntries:
    def test_database_boat_added_from_later_race_is_dnc_in_previous_races(self, client):
        with ro.get_db() as db:
            series_id, race1_id, race2_id = _create_series_with_two_races(db)
            late_boat = _create_boat(db, "Late Entry", "GBR 99", irc=1.010)

            added = ro.add_boat_database_entry_to_series_races(
                db,
                series_id,
                late_boat,
                class_override="IRC",
                rating_source="AUTO",
                source_race_id=race2_id,
            )
            db.commit()

            assert added == 2
            assert _entry_statuses_by_race(db, int(late_boat["id"])) == {
                race1_id: "DNC",
                race2_id: "RACING",
            }

    def test_manual_boat_added_from_later_race_is_dnc_in_previous_races(self, client):
        with ro.get_db() as db:
            series_id, race1_id, race2_id = _create_series_with_two_races(db)

            added = entrysync.add_manual_entry_to_series_races(
                db,
                series_id,
                boat_name="Late Manual",
                sail_no="GBR 100",
                class_name="IRC",
                irc_rating=1.015,
                ytc_rating=1000,
                source_race_id=race2_id,
            )
            db.commit()

            rows = db.execute(
                "SELECT race_id, status FROM entries WHERE boat_name = 'Late Manual' ORDER BY race_id"
            ).fetchall()
            assert added == 2
            assert {int(row["race_id"]): row["status"] for row in rows} == {
                race1_id: "DNC",
                race2_id: "RACING",
            }


class TestSeriesScoringRaceReadiness:
    def test_in_progress_race_is_not_included_in_series_scoring(self, client):
        with ro.get_db() as db:
            series_id, race1_id, race2_id = _create_series_with_two_races(db)
            alpha = _create_boat(db, "Alpha", "GBR 1", irc=1.000)
            beta = _create_boat(db, "Beta", "GBR 2", irc=1.020)

            ro.add_boat_database_entry_to_race(db, _race(db, race1_id), alpha, "IRC", status="FINISHED")
            ro.add_boat_database_entry_to_race(db, _race(db, race1_id), beta, "IRC", status="DNC")
            ro.add_boat_database_entry_to_race(db, _race(db, race2_id), alpha, "IRC", status="RACING")
            ro.add_boat_database_entry_to_race(db, _race(db, race2_id), beta, "IRC", status="RACING")
            db.execute(
                "UPDATE entries SET finish_time = ? WHERE race_id = ? AND boat_id = ?",
                ("2026-06-01T11:00:00", race1_id, int(alpha["id"])),
            )
            db.commit()

        table = ro.build_series_result_table(series_id, "IRC")

        assert table["race_count"] == 1
        assert [int(race["id"]) for race in table["races"]] == [race1_id]
        rows_by_name = {row["competitor"]["boat_name"]: row for row in table["rows"]}
        assert set(rows_by_name) == {"Alpha", "Beta"}
        assert rows_by_name["Alpha"]["scores"][0]["code"] == "1"
        assert rows_by_name["Beta"]["scores"][0]["code"] == "DNC"

    def test_completed_race_is_included_once_no_boats_are_racing(self, client):
        with ro.get_db() as db:
            series_id, race1_id, race2_id = _create_series_with_two_races(db)
            alpha = _create_boat(db, "Alpha", "GBR 1", irc=1.000)
            beta = _create_boat(db, "Beta", "GBR 2", irc=1.020)

            for race_id, finish_time in [(race1_id, "2026-06-01T11:00:00"), (race2_id, "2026-06-08T11:00:00")]:
                ro.add_boat_database_entry_to_race(db, _race(db, race_id), alpha, "IRC", status="FINISHED")
                ro.add_boat_database_entry_to_race(db, _race(db, race_id), beta, "IRC", status="DNC")
                db.execute(
                    "UPDATE entries SET finish_time = ? WHERE race_id = ? AND boat_id = ?",
                    (finish_time, race_id, int(alpha["id"])),
                )
            db.commit()

        table = ro.build_series_result_table(series_id, "IRC")

        assert table["race_count"] == 2
        assert [int(race["id"]) for race in table["races"]] == [race1_id, race2_id]
        assert all(len(row["scores"]) == 2 for row in table["rows"])
