"""A course nobody has chosen must not read as a course somebody chose.

A new race stores the first fixed course as a fallback so the chart and the leg
analysis have something to work with. That fallback was indistinguishable from a
decision: the race sheet's header, the competitor page and the spoken VHF
announcement all said "Course 1" before a race officer had looked at the race —
and the announcement is the one that would send a fleet round the wrong course.
"""
from __future__ import annotations

import pytest

import app as ro
from core import appstate
from core.courses import course_announcement_text, course_for_race
from core.raceadmin import RaceSettings, RaceSpec, create_race, update_race_settings


@pytest.fixture()
def db_ready(reset_module_state):
    ro.init_db()
    yield


def _race(db, race_id):
    return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _first_course() -> int:
    return int(appstate.COURSES[0]["course_no"])


class TestANewRaceHasNoCourse:
    def test_creating_a_race_does_not_choose_a_course(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            assert _race(db, created.race_id)["course_set"] == 0

    def test_but_a_workable_course_is_still_stored_underneath(self, db_ready):
        """The chart, the leg analysis and the shortening options all need a
        course to work with; the flag says it is a fallback, not a choice."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            course = course_for_race(_race(db, created.race_id))
        assert course["marks"]

    def test_saving_the_course_and_start_tab_is_choosing(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            assert _race(db, created.race_id)["course_set"] == 1


class TestNothingClaimsACourseNobodyPicked:
    def test_the_announcement_is_silent_until_a_course_is_chosen(self, db_ready):
        """The one that matters: this is read out over the VHF, and a fleet sent
        round a course the race officer never picked is a general recall at best."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            race = _race(db, created.race_id)
            course = course_for_race(race)
            assert course_announcement_text(race, course) == ""

    def test_and_speaks_once_it_is(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            race = _race(db, created.race_id)
            spoken = course_announcement_text(race, course_for_race(race))
        assert spoken and "mark" in spoken.lower()

    def test_the_race_sheet_says_the_course_is_not_set(self, logged_in_client):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Fresh"))
        page = logged_in_client.get(f"/admin/race/{created.race_id}").get_data(as_text=True)
        assert "Course not set" in page
        assert "Nobody has chosen a course" in page

    def test_the_competitor_page_does_not_name_one(self, client):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Fresh"))
        page = client.get(f"/public/race/{created.race_id}").get_data(as_text=True)
        assert "not set yet" in page

    def test_once_chosen_the_race_sheet_names_it(self, logged_in_client):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Fresh"))
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
        page = logged_in_client.get(f"/admin/race/{created.race_id}").get_data(as_text=True)
        assert "Course not set" not in page
        assert f"Course {_first_course()}" in page


class TestExistingRacesAreLeftAlone:
    def test_a_race_with_a_start_time_counts_as_chosen(self, db_ready):
        """The back-fill: every race in the database when the column arrived had
        been through the Course & start tab, so none of them should suddenly read
        as unset.

        WHEN THE COLUMN ARRIVES, and not on every start-up -- which is what this
        test used to assert, by forcing the migration to run again and expecting
        it to fire. That reasoning is sound for races that predate the
        distinction and false for one being set up now, and applying it for ever
        is how a race given a start time before its course was marked as having
        chosen one at the next restart. Dropping the column is the closest a test
        can get to a database from before it existed; the whole rule, both ways
        round, is in tests/test_course_set_backfill_runs_once.py.
        """
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            db.execute("UPDATE races SET course_set = 0, start_time = '2026-08-15T10:55'"
                       " WHERE id = ?", (created.race_id,))
            db.execute("ALTER TABLE races DROP COLUMN course_set")
            db.commit()
        from core import db as core_db
        core_db.DB_INITIALIZED = False     # a database from before the column
        ro.init_db()
        with ro.get_db() as db:
            assert _race(db, created.race_id)["course_set"] == 1
