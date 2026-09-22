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


class TestTheFieldOnTheRacePage:
    FIELD = re.compile(r'name="start_time" value="([^"]*)"')

    def _field_value(self, client, race_id):
        html = client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        found = self.FIELD.search(html)
        assert found, "the first-warning field is gone from the race page"
        return found.group(1)

    def test_a_race_created_just_now_is_offered_a_time_it_can_run_in(self, logged_in_client):
        """The reported case: create a race, open the field, and it shows the
        minute you are standing in."""
        race_id = _make_race(datetime.now().isoformat(timespec="seconds"))
        offered = parse_dt(self._field_value(logged_in_client, race_id))
        assert (offered - datetime.now()).total_seconds() >= WARNING_TIME_MIN_LEAD_S - 60

    def test_a_time_already_chosen_for_later_is_shown_as_chosen(self, logged_in_client):
        chosen = (datetime.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
        race_id = _make_race(chosen.isoformat(timespec="seconds"))
        assert self._field_value(logged_in_client, race_id) == chosen.strftime("%Y-%m-%dT%H:%M")

    def test_a_race_that_has_been_sailed_keeps_the_time_it_was_sailed_at(self, logged_in_client):
        """The one that would really hurt. Its warning time is necessarily in the
        past, so a blanket "replace anything past" would offer a new time -- and
        saving the form to fix a course number would quietly move the warning
        signal of a race that is already in the results."""
        sailed = (datetime.now() - timedelta(days=2)).replace(second=0, microsecond=0)
        race_id = _make_race(sailed.isoformat(timespec="seconds"), name="Sailed")
        assert self._field_value(logged_in_client, race_id) == sailed.strftime("%Y-%m-%dT%H:%M")


class TestTheRestartFieldUnderAPOverH:
    """With H there is no one-minute warning to count from, so the race officer
    types the time the sequence restarts at. That field had no value at all, so
    it opened on the current minute -- the one time the sequence cannot run
    from."""

    def test_it_offers_a_time_rather_than_nothing(self):
        html = (ro.app.jinja_env.loader.get_source(ro.app.jinja_env, "race.html")[0])
        block = html.split('name="warning_time"')[1].split(">")[0]
        assert "warning_time_value" in block

    def test_and_will_not_accept_one_already_past(self):
        html = (ro.app.jinja_env.loader.get_source(ro.app.jinja_env, "race.html")[0])
        block = html.split('name="warning_time"')[1].split(">")[0]
        assert "min=" in block
