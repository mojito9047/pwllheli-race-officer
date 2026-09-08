"""Creating and updating a race, without a browser in the way.

These are the tests the extraction was for. Every one of them was previously
only reachable by POSTing a form: the rules lived inside the route handlers, so
exercising "a pursuit with no period falls back to 90 minutes" meant building a
request, and most of them were simply never written.

That matters beyond tidiness. The next caller of these rules is a natural
language command sent from the water, and the safety of that feature rests on
its validation being the *same* validation the race sheet uses, rather than a
second copy that happens to agree today.
"""
from __future__ import annotations

import time

import pytest

import app as ro
from core import appstate, raceadmin
from core.raceadmin import (
    DEFAULT_PURSUIT_DURATION_MIN,
    DEFAULT_RACE_NAME,
    EntryScope,
    RaceSettings,
    RaceSpec,
    RaceValidationError,
    add_entries,
    clear_shortening,
    create_race,
    shorten_course_at,
    update_pursuit_settings,
    update_race_settings,
)


@pytest.fixture()
def db_ready(reset_module_state):
    """An initialised, sandboxed database with no Flask request in sight."""
    ro.init_db()
    yield


@pytest.fixture()
def logged(monkeypatch):
    """Capture activity-log lines instead of writing them to runtime/logs."""
    written = []
    monkeypatch.setattr(raceadmin, "log_activity",
                        lambda action, details="", user="system": written.append((action, details, user)))
    return written


def _race(db, race_id):
    return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _first_course() -> int:
    return int(appstate.COURSES[0]["course_no"])


class TestCreatingARace:
    def test_a_race_with_no_name_still_gets_one(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="   "))
            assert _race(db, created.race_id)["name"] == DEFAULT_RACE_NAME

    def test_the_course_and_warning_signal_are_left_unset(self, db_ready):
        """A race is created before either is known; the race sheet is where
        they are chosen, and the stored course number is only a fallback."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Club Race"))
            row = _race(db, created.race_id)
            assert row["start_time"] == ""
            assert int(row["course_no"]) == _first_course()

    def test_gps_finish_detection_is_armed_from_the_start(self, db_ready):
        """A finish the detector catches is worth more than one the RO was going
        to time anyway, and a missed detection cannot be recovered after the
        race. It is a tick box on the Entries tab for anyone who wants it off,
        which is the only place the reasoning belongs."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Club Race"))
            row = _race(db, created.race_id)
            assert int(row["gps_finish_enabled"]) == 1

    def test_but_auto_confirm_is_not(self, db_ready):
        """Detection proposes; a person disposes. An ordinary club race has a
        race officer in the hut, and a detection they can see and dismiss costs
        one click -- where a wrong finish written straight into the results of a
        race still being sailed has to be found before it can be corrected. Only
        the unmanned case turns it on, and it does so explicitly."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Club Race"))
            assert int(_race(db, created.race_id)["gps_auto_confirm"]) == 0

    def test_and_a_caller_can_still_ask_for_it(self, db_ready):
        """The on-the-water page does exactly this: nobody is in the hut."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Unmanned", gps_auto_confirm=True))
            assert int(_race(db, created.race_id)["gps_auto_confirm"]) == 1

    def test_but_a_caller_can_still_create_a_race_with_them_off(self, db_ready):
        """The default is a default. Nothing about it should stop a race being
        created unarmed — a training day with the trackers ashore, say."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Quiet", gps_finish_enabled=False,
                                               gps_auto_confirm=False))
            row = _race(db, created.race_id)
            assert int(row["gps_finish_enabled"]) == 0
            assert int(row["gps_auto_confirm"]) == 0

    def test_a_pursuit_with_no_period_falls_back_rather_than_refusing(self, db_ready):
        """Without a period no start time can be computed at all, so a sensible
        club-length default beats refusing to create the race."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(race_type="pursuit", pursuit_duration_min=None))
            row = _race(db, created.race_id)
            assert row["race_type"] == "pursuit"
            assert float(row["pursuit_duration_min"]) == pytest.approx(DEFAULT_PURSUIT_DURATION_MIN)

    def test_a_pursuit_has_no_class_and_one_rating_system(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(race_type="pursuit", pursuit_rating="YTC",
                                               class_name="IRC 1", pursuit_duration_min=75))
            row = _race(db, created.race_id)
            assert row["class_name"] == ""
            assert row["rating_rule"] == "YTC"

    def test_a_standard_race_scores_both_systems(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(class_name="IRC 1"))
            row = _race(db, created.race_id)
            assert row["rating_rule"] == "DUAL"
            assert row["class_name"] == "IRC 1"

    def test_an_unknown_series_is_refused_by_name(self, db_ready):
        """Rather than attaching the race to nothing and quietly leaving it out
        of the standings."""
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as caught:
                create_race(db, RaceSpec(series_id=9999))
            assert caught.value.field == "series_id"
            assert "series" in caught.value.message.lower()

    def test_creating_a_race_is_recorded_against_the_caller(self, db_ready, logged):
        """An automated caller is logged as itself, not as whoever happened to
        have a session open."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="From the water"), actor="assistant:pd")
        assert ("race created", f"#{created.race_id} 'From the water' type=standard",
                "assistant:pd") in logged


class TestUpdatingCourseAndStart:
    def test_an_invalid_course_number_is_refused(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            with pytest.raises(RaceValidationError) as caught:
                update_race_settings(db, _race(db, created.race_id), RaceSettings(course_no=99999))
            assert caught.value.field == "course_no"

    @pytest.mark.parametrize("given", ["half past eleven", "10:55", "tomorrow at 11"])
    def test_a_time_the_app_cannot_read_is_refused_not_discarded(self, db_ready, given):
        """`normalise_start_time_value` answers "" for anything it cannot parse,
        so normalising and then asking whether the result parses can never fail.
        A bare "10:55" went in and a race with no start time came out, reported
        as saved -- which a person typing a command would never suspect."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            with pytest.raises(RaceValidationError) as caught:
                update_race_settings(db, _race(db, created.race_id),
                                     RaceSettings(course_no=_first_course(), start_time=given))
            assert caught.value.field == "start_time"
            assert _race(db, created.race_id)["start_time"] == ""

    def test_a_blank_warning_time_is_allowed(self, db_ready):
        """The course is often chosen before the sequence time is known."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course(), start_time=""))
            assert _race(db, created.race_id)["start_time"] == ""

    def test_the_stored_time_is_the_first_warning_signal(self, db_ready):
        """The most misread field in the app: the first gun is five minutes
        after what this column holds."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course(),
                                              start_time="2026-08-12T10:55"))
            row = _race(db, created.race_id)
        assert row["start_time"].startswith("2026-08-12T10:55")
        assert ro.race_first_start_time(row) == "2026-08-12T11:00:00"

    def test_an_unknown_finish_line_falls_back_to_the_club_line(self, db_ready):
        """A race with no finish line at all would mean no GPS finish could
        ever be detected, and nothing on the page would say why."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course(),
                                              finish_line_key="not-a-line"))
            assert (_race(db, created.race_id)["finish_line_key"] or "") == ""

    def test_start_plan_problems_come_back_as_warnings_not_refusals(self, db_ready):
        """The race sheet saves the race and tells you about the plan; refusing
        the save would throw away the rest of the edit."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            result = update_race_settings(
                db, _race(db, created.race_id),
                RaceSettings(course_no=_first_course(),
                             start_plan={"starts": [], "errors": ["Start 2 has no classes"]}))
        assert result.warnings == ["Start 2 has no classes"]

    def test_no_start_plan_means_follow_the_series_default(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course(), start_plan=None))
            assert not (_race(db, created.race_id)["start_plan_json"] or "")

    def test_a_pursuit_race_is_refused_rather_than_overwritten(self, db_ready):
        """Pursuit races have their own update path; writing standard-race
        fields over one would strip its period and rating system."""
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(race_type="pursuit", pursuit_duration_min=60))
            with pytest.raises(RaceValidationError):
                update_race_settings(db, _race(db, created.race_id),
                                     RaceSettings(course_no=_first_course()))


class TestUpdatingAPursuit:
    """The other half of the Course & start tab, which lived only in the race
    sheet's form handler. A pursuit created anywhere else could be made and then
    never given a start time: `update_race_settings` refuses a pursuit, so the
    call came back "use the pursuit update instead" -- after the race existed."""

    def _pursuit(self, db):
        created = create_race(db, RaceSpec(name="Pursuit", race_type="pursuit",
                                           pursuit_duration_min=90))
        return _race(db, created.race_id)

    def test_a_pursuit_can_be_given_a_start_time_and_a_course(self, db_ready):
        with ro.get_db() as db:
            race = self._pursuit(db)
            update_pursuit_settings(db, race, RaceSettings(course_no=_first_course(),
                                                           start_time="2026-08-12T10:55"))
            row = _race(db, race["id"])
        assert row["start_time"].startswith("2026-08-12T10:55")
        assert row["course_no"] == _first_course()

    def test_choosing_its_course_counts_as_choosing(self, db_ready):
        """Saving the tab is the whole of what choosing a course means. Without
        this a pursuit stayed "Course not set" for ever -- and the VHF course
        announcement stays silent until a course is chosen."""
        with ro.get_db() as db:
            race = self._pursuit(db)
            assert race["course_set"] == 0
            update_pursuit_settings(db, race, RaceSettings(course_no=_first_course()))
            assert _race(db, race["id"])["course_set"] == 1

    def test_a_time_it_cannot_read_is_refused_here_too(self, db_ready):
        with ro.get_db() as db:
            race = self._pursuit(db)
            with pytest.raises(RaceValidationError) as caught:
                update_pursuit_settings(db, race, RaceSettings(start_time="half past eleven"))
            assert caught.value.field == "start_time"

    def test_what_is_not_restated_is_kept(self, db_ready):
        """A caller changing the start time must not have to know the rating
        system and the period as well, or it will quietly reset them."""
        with ro.get_db() as db:
            race = self._pursuit(db)
            update_pursuit_settings(db, race, RaceSettings(pursuit_rating="YTC",
                                                           course_no=_first_course()))
            update_pursuit_settings(db, _race(db, race["id"]),
                                    RaceSettings(start_time="2026-08-12T10:55"))
            row = _race(db, race["id"])
        assert row["rating_rule"] == "YTC"
        assert row["pursuit_duration_min"] == 90

    def test_each_kind_of_race_refuses_the_other_one(self, db_ready):
        with ro.get_db() as db:
            standard = _race(db, create_race(db, RaceSpec()).race_id)
            with pytest.raises(RaceValidationError):
                update_pursuit_settings(db, standard, RaceSettings(course_no=_first_course()))
            with pytest.raises(RaceValidationError):
                update_race_settings(db, self._pursuit(db), RaceSettings(course_no=_first_course()))


class TestAddingEntries:
    def test_a_boat_that_is_not_in_the_database_is_refused(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            with pytest.raises(RaceValidationError) as caught:
                add_entries(db, _race(db, created.race_id),
                            EntryScope(kind="boat", boat_id=999999))
            assert caught.value.field == "boat_id"

    def test_there_is_no_fleet_scope_to_ask_for(self):
        """It selected active boats whose own free-text class matched a name.
        Both ends of that comparison retired -- the race's fleet label in
        v0.271, the boat's in v0.283 -- so it could only ever have matched rows
        typed before then. An unknown kind falls through to every active boat
        rather than adding nobody and reporting success; the argument itself is
        gone, so nothing can ask for it by mistake.
        """
        with pytest.raises(TypeError):
            EntryScope(kind="fleet", fleet="IRC 1")

    def test_adding_every_active_boat_reports_how_many(self, db_ready):
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            result = add_entries(db, _race(db, created.race_id), EntryScope(kind="all_active"))
            entries = db.execute("SELECT COUNT(*) FROM entries WHERE race_id = ?",
                                 (created.race_id,)).fetchone()[0]
        assert result.added == entries

    def test_adding_a_boat_to_a_pursuit_recomputes_the_start_ladder(self, db_ready, monkeypatch):
        """Every boat changes every other boat's start time, so the ladder is
        recomputed as part of adding rather than by whoever remembered to."""
        recomputed = []
        monkeypatch.setattr(raceadmin, "recompute_pursuit_start_times", recomputed.append)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(race_type="pursuit", pursuit_duration_min=60))
            add_entries(db, _race(db, created.race_id), EntryScope(kind="all_active"))
        assert recomputed == [created.race_id]

    def test_adding_to_a_standard_race_leaves_the_pursuit_maths_alone(self, db_ready, monkeypatch):
        recomputed = []
        monkeypatch.setattr(raceadmin, "recompute_pursuit_start_times", recomputed.append)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            add_entries(db, _race(db, created.race_id), EntryScope(kind="all_active"))
        assert recomputed == []


class TestShorteningTheCourse:
    def test_a_mark_not_on_the_course_is_refused(self, db_ready, monkeypatch):
        monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: None)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            with pytest.raises(RaceValidationError) as caught:
                shorten_course_at(db, _race(db, created.race_id), 999)
            assert caught.value.field == "index"
            assert _race(db, created.race_id)["shortened_at_index"] is None

    def test_shortening_records_the_mark_and_the_time(self, db_ready, monkeypatch):
        monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: None)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            call = shorten_course_at(db, _race(db, created.race_id), 1)
            row = _race(db, created.race_id)
        assert row["shortened_at_index"] == call.index == 1
        assert row["shortened_at_mark"] == call.mark
        assert row["shortened_at_time"] == call.at_time

    def test_the_signal_is_part_of_the_call_not_the_caller(self, db_ready, monkeypatch):
        """A shortened course recorded silently is a fleet still sailing the
        full course, which is the one thing shortening exists to prevent."""
        fired = []
        monkeypatch.setattr(raceadmin, "signal_shortened_course",
                            lambda race_id, announcement: fired.append((race_id, announcement)))
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            call = shorten_course_at(db, _race(db, created.race_id), 1)
        for _ in range(50):                      # the signal runs on its own thread
            if fired:
                break
            time.sleep(0.02)
        assert fired and fired[0][0] == created.race_id
        assert call.mark in fired[0][1] and "proceed to finish" in fired[0][1]

    def test_the_signal_can_be_suppressed_for_a_dry_run(self, db_ready, monkeypatch):
        """A caller checking whether a mark is shortenable should not sound the
        horn over the bay to find out."""
        fired = []
        monkeypatch.setattr(raceadmin, "signal_shortened_course",
                            lambda *a, **k: fired.append(a))
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            shorten_course_at(db, _race(db, created.race_id), 1, signal=False)
        time.sleep(0.1)
        assert fired == []

    def test_clearing_undoes_the_call(self, db_ready, monkeypatch):
        monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: None)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec())
            update_race_settings(db, _race(db, created.race_id),
                                 RaceSettings(course_no=_first_course()))
            shorten_course_at(db, _race(db, created.race_id), 1)
            clear_shortening(db, _race(db, created.race_id))
            row = _race(db, created.race_id)
        assert row["shortened_at_index"] is None
        assert row["shortened_at_mark"] is None


class TestTheFormAndTheServiceAgree:
    """The point of the extraction: one path, so the browser cannot drift from
    any other caller."""

    def test_a_race_created_by_form_matches_one_created_directly(self, logged_in_client):
        with ro.get_db() as db:
            direct = create_race(db, RaceSpec(name="Via service", class_name="IRC 1"))
            direct_row = dict(_race(db, direct.race_id))

        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        resp = logged_in_client.post("/admin/race/new", data={
            "_csrf_token": token, "name": "Via form", "class_name": "IRC 1",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with ro.get_db() as db:
            posted_row = dict(db.execute(
                "SELECT * FROM races WHERE name = 'Via form' ORDER BY id DESC LIMIT 1").fetchone())

        ignore = {"id", "name", "created_at"}
        differing = {k: (direct_row[k], posted_row.get(k)) for k in direct_row
                     if k not in ignore and direct_row[k] != posted_row.get(k)}
        assert not differing, f"the form and the service disagree about {differing}"
