"""What the first-warning-signal field offers when you open it.

It offered whatever the race was created with, which for a race made minutes ago
is a time that has already gone. The race officer then retypes it every time.

Six minutes is not arbitrary. The start sequence now begins ten minutes before
the gun -- which is five minutes before the warning signal -- so a warning time
closer than five minutes is one the sequence is already part-way through. Six
gives the five it needs and a minute to press the button in.

What it must not do is move a time somebody chose, or rewrite the record of a
race that has already been sailed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import app as ro
from core.timeutils import WARNING_TIME_MIN_LEAD_S, parse_dt, suggested_warning_time

NOW = datetime(2026, 9, 22, 11, 20, 17)


def _make_race(start_time, name="Warning Default"):
    created = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                         " VALUES (?, 1, ?, ?)", (name, start_time, created))
        db.commit()
        return int(cur.lastrowid)


class TestATimeThatHasGone:
    def test_a_past_time_is_replaced(self):
        offered = parse_dt(suggested_warning_time("2026-09-22T11:00:00", now=NOW))
        assert offered > NOW

    def test_by_one_at_least_six_minutes_out(self):
        offered = parse_dt(suggested_warning_time("2026-09-22T11:00:00", now=NOW))
        assert (offered - NOW).total_seconds() >= WARNING_TIME_MIN_LEAD_S

    def test_and_not_much_more_than_that(self):
        """Rounded up to the minute, not padded. Offering a quarter of an hour
        would be as much of a retype as offering a time that has gone."""
        offered = parse_dt(suggested_warning_time("2026-09-22T11:00:00", now=NOW))
        assert (offered - NOW).total_seconds() < WARNING_TIME_MIN_LEAD_S + 60

    def test_it_lands_on_a_whole_minute(self):
        """Signal times are whole minutes throughout: a fleet counts down to them."""
        offered = parse_dt(suggested_warning_time("2026-09-22T11:00:00", now=NOW))
        assert offered.second == 0 and offered.microsecond == 0

    def test_nothing_set_is_treated_the_same_way(self):
        for empty in ("", None, "   "):
            offered = parse_dt(suggested_warning_time(empty, now=NOW))
            assert (offered - NOW).total_seconds() >= WARNING_TIME_MIN_LEAD_S

    def test_and_so_is_something_unparseable(self):
        """A field that throws on bad stored data takes the whole race page with it."""
        offered = parse_dt(suggested_warning_time("not a time at all", now=NOW))
        assert (offered - NOW).total_seconds() >= WARNING_TIME_MIN_LEAD_S

    def test_now_itself_counts_as_gone(self):
        offered = parse_dt(suggested_warning_time(NOW.isoformat(timespec="seconds"), now=NOW))
        assert (offered - NOW).total_seconds() >= WARNING_TIME_MIN_LEAD_S

    def test_a_time_inside_the_current_minute_counts_as_gone(self):
        """Found by the test above. Rounding to the minute happens first, so a
        stored 11:20:40 read at 11:20:17 is ahead of us but the whole minute it
        becomes is not -- comparing before rounding offered a time that had
        already gone, which is the fault this whole change is about."""
        inside = NOW.replace(second=40).isoformat(timespec="seconds")
        offered = parse_dt(suggested_warning_time(inside, now=NOW))
        assert offered > NOW
        assert (offered - NOW).total_seconds() >= WARNING_TIME_MIN_LEAD_S


class TestATimeSomebodyChose:
    def test_a_future_time_is_left_alone(self):
        chosen = "2026-09-22T14:30:00"
        assert suggested_warning_time(chosen, now=NOW) == chosen

    def test_even_a_close_one(self):
        """One minute away is a poor idea and the race officer's to make. This
        field suggests; it does not overrule."""
        chosen = (NOW + timedelta(minutes=1)).replace(second=0).isoformat(timespec="seconds")
        assert suggested_warning_time(chosen, now=NOW) == chosen

    def test_seconds_are_dropped_from_it(self):
        offered = suggested_warning_time("2026-09-22T14:30:41", now=NOW)
        assert offered == "2026-09-22T14:30:00"


class TestTheFieldShowsWhatIsStored:
    """Reported after the first attempt at this, and the reason it was wrong.

    A new race is created with **no** warning time -- "Not set" is a real state,
    and no gun sounds until somebody chooses one. Pre-filling the field with a
    suggestion meant the race officer could open the race to set a course, press
    Save, and be given a warning signal time they never picked: a fleet counted
    down to a race nobody had started.

    So the field shows exactly what is stored. The suggestion arrives when the
    field is opened, which is when a time is being chosen.
    """

    FIELD = re.compile(r'name="start_time"\s+value="([^"]*)"\s+data-suggested="([^"]*)"')

    def _field(self, client, race_id):
        html = client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        found = self.FIELD.search(html)
        assert found, "the first-warning field is gone from the race page"
        return found.group(1), found.group(2)

    def test_a_new_race_shows_an_empty_field(self, logged_in_client):
        value, _ = self._field(logged_in_client, _make_race(""))
        assert value == "", f"the field offered {value!r} for a race with no time set"

    def test_but_carries_the_suggestion_for_when_it_is_opened(self, logged_in_client):
        _value, suggested = self._field(logged_in_client, _make_race(""))
        offered = parse_dt(suggested)
        assert offered and (offered - datetime.now()).total_seconds() >= WARNING_TIME_MIN_LEAD_S - 60

    def test_a_time_already_chosen_is_shown_as_chosen(self, logged_in_client):
        chosen = (datetime.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
        value, _ = self._field(logged_in_client, _make_race(chosen.isoformat(timespec="seconds")))
        assert value == chosen.strftime("%Y-%m-%dT%H:%M")

    def test_a_race_that_has_been_sailed_is_offered_nothing(self, logged_in_client):
        """Its warning time is necessarily past. Suggesting a new one would mean
        a saved form moving the warning signal of a race in the results."""
        sailed = (datetime.now() - timedelta(days=2)).replace(second=0, microsecond=0)
        value, suggested = self._field(logged_in_client, _make_race(sailed.isoformat(timespec="seconds")))
        assert value == sailed.strftime("%Y-%m-%dT%H:%M")
        assert suggested == ""

    def test_the_script_that_fills_it_is_loaded(self, logged_in_client):
        html = logged_in_client.get(f"/race/{_make_race('')}",
                                    follow_redirects=True).get_data(as_text=True)
        assert "warning_time.js" in html


class TestSavingTheFormDoesNotInventATime:
    """The reported bug itself, at the route that does the saving."""

    def test_setting_a_course_leaves_the_warning_time_unset(self, logged_in_client, csrf_post):
        race_id = _make_race("")
        res = csrf_post(f"/race/{race_id}/update",
                        {"name": "Warning Default", "course_no": "1", "start_time": ""})
        assert res.status_code in (200, 302), res.status_code
        with ro.get_db() as db:
            after = db.execute("SELECT start_time FROM races WHERE id = ?", (race_id,)).fetchone()
        assert (after["start_time"] or "") == "",             f"saving the form gave the race a warning signal time of {after['start_time']!r}"

    def test_and_a_time_that_is_typed_in_is_kept(self, logged_in_client, csrf_post):
        """The other half: the field still works."""
        race_id = _make_race("")
        chosen = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
        csrf_post(f"/race/{race_id}/update",
                  {"name": "Warning Default", "course_no": "1",
                   "start_time": chosen.strftime("%Y-%m-%dT%H:%M")})
        with ro.get_db() as db:
            after = db.execute("SELECT start_time FROM races WHERE id = ?", (race_id,)).fetchone()
        assert parse_dt(after["start_time"]) == chosen


class TestTheRestartFieldUnderAPOverH:
    """With H there is no one-minute warning to count from, so the race officer
    types the time the sequence restarts at. That field had no value at all, so
    it opened on the current minute -- the one time the sequence cannot run
    from."""

    def test_it_offers_a_time_rather_than_nothing(self):
        html = (ro.app.jinja_env.loader.get_source(ro.app.jinja_env, "race.html")[0])
        block = html.split('name="warning_time"')[1].split(">")[0]
        assert "warning_time_suggested" in block

    def test_and_will_not_accept_one_already_past(self):
        html = (ro.app.jinja_env.loader.get_source(ro.app.jinja_env, "race.html")[0])
        block = html.split('name="warning_time"')[1].split(">")[0]
        assert "min=" in block
