"""The countdown speech rate, the whole race log, and announcing the AP time.

Three things from using it on a race day:

* The club slowed the **Normal speech rate** down so the course announcements
  could be followed, and that dragged the ten-count with it, so "One" landed
  after the gun. The ten-count was the one announcement not using the setting
  called *Countdown speech rate*.
* The Horn and race log showed the most recent twenty events, which on a race
  with a start sequence in it is not the whole race.
* Setting the time AP will come down told nobody afloat.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import raceadmin, startsequence  # noqa: E402


def make_race(warning_in_minutes=30, name="Rate Test"):
    warning = (datetime.now() + timedelta(minutes=warning_in_minutes)).replace(
        second=0, microsecond=0)
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "", 1, warning.isoformat(timespec="seconds"), "DUAL", "",
             datetime.now().isoformat(timespec="seconds")))
        db.commit()
        return int(cur.lastrowid)


def get(race_id):
    with ro.get_db() as db:
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def countdown_event(race):
    events = startsequence.central_start_sequence_events(race)
    return next(e for e in events
                if str(e.get("label", "")).startswith("Audio: countdown"))


class TestTheCountdownUsesTheCountdownRate:
    """It is the announcement whose timing matters and the only one that was not
    using the setting named for it."""

    def test_the_ten_count_is_spoken_at_the_countdown_rate(self, client):
        ro.save_app_settings({"central_audio_rate": "140", "central_audio_fast_rate": "185"})
        event = countdown_event(get(make_race()))
        assert event["rate"] == 185, "the ten-count is still on the normal rate"

    def test_and_not_dragged_by_the_normal_rate(self, client):
        """The reported fault: slowing the normal rate for clarity stretched the
        count until "One" landed after the gun."""
        ro.save_app_settings({"central_audio_rate": "110", "central_audio_fast_rate": "185"})
        slow_normal = countdown_event(get(make_race()))
        ro.save_app_settings({"central_audio_rate": "200", "central_audio_fast_rate": "185"})
        fast_normal = countdown_event(get(make_race()))
        assert slow_normal["sec"] == fast_normal["sec"]
        assert slow_normal["rate"] == fast_normal["rate"] == 185

    def test_the_course_announcements_still_use_the_normal_rate(self, client):
        """That is what the club slowed down, and it should stay slowed."""
        ro.save_app_settings({"central_audio_rate": "140", "central_audio_fast_rate": "185"})
        events = startsequence.central_start_sequence_events(get(make_race()))
        course = [e for e in events if "course announcement" in str(e.get("label", ""))]
        assert course and all(e["rate"] == 140 for e in course)


class TestWhenTheCountBegins:
    """One utterance, so how early it must start depends on how fast it is said.
    Fixing that at eleven seconds is what put "One" after the gun."""

    def test_a_slower_countdown_rate_starts_earlier(self, client):
        ro.save_app_settings({"central_audio_fast_rate": "120"})
        slow = countdown_event(get(make_race()))["sec"]
        ro.save_app_settings({"central_audio_fast_rate": "185"})
        normal = countdown_event(get(make_race()))["sec"]
        assert slow > normal, "a slower voice was given no more time to say it"

    def test_and_a_faster_one_starts_later(self, client):
        ro.save_app_settings({"central_audio_fast_rate": "185"})
        normal = countdown_event(get(make_race()))["sec"]
        ro.save_app_settings({"central_audio_fast_rate": "300"})
        fast = countdown_event(get(make_race()))["sec"]
        assert fast < normal, "a faster voice would have finished early and waited"

    def test_the_reference_rate_still_gives_the_eleven_seconds_it_was_tuned_at(self):
        """The number the old hard-coded eleven came from. If this changes, the
        count moves for every club on the default."""
        assert startsequence.countdown_lead_seconds(185) == 11.0

    def test_it_scales_inversely_with_the_rate(self):
        """Twice as fast, half as long. That is the model; these are the knobs
        to re-tune if a real voice disagrees."""
        assert startsequence.countdown_lead_seconds(370) == pytest.approx(5.5, abs=0.1)

    def test_a_missing_or_daft_rate_falls_back_rather_than_raising(self):
        """A settings row with nothing in it must not stop the start sequence."""
        for value in (None, "", "fast", 0):
            assert startsequence.countdown_lead_seconds(value) == 11.0

    def test_the_default_is_the_rate_the_count_was_tuned_at(self, monkeypatch):
        """It was 285, which only ever affected the single word "Start". Now it
        governs the timing of the ten-count, so an install that has never set it
        gets the rate the count was tuned at.

        Note what this does *not* do: a club with 285 already stored keeps it,
        because a default is not a migration. They have to set it, which is why
        the guide says so.
        """
        from core import settings
        monkeypatch.setattr(settings, "get_app_setting_overrides", dict)
        assert settings.race_console_config()["central_audio_fast_rate"] == 185


class TestTheRaceLogShowsTheWholeRace:
    def test_every_event_for_the_race_is_shown(self, client):
        """Twenty rows hid the start of the very sequence somebody was looking at:
        a single start puts about a dozen in on its own, before any finishes."""
        race_id = make_race()
        for i in range(45):
            ro.log_event(race_id, "note", f"event {i}", "test", {})
        from core.eventlog import get_events
        assert len(get_events(race_id, limit=None)) == 45

    def test_the_race_sheet_asks_for_all_of_them(self, logged_in_client):
        race_id = make_race()
        for i in range(30):
            ro.log_event(race_id, "note", f"marker {i}", "test", {})
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "marker 0" in page, "the oldest events were cut off"
        assert "marker 29" in page

    def test_and_so_does_the_start_console(self, logged_in_client):
        race_id = make_race()
        for i in range(30):
            ro.log_event(race_id, "note", f"marker {i}", "test", {})
        page = logged_in_client.get(
            f"/admin/race/{race_id}/start_console").get_data(as_text=True)
        assert "marker 0" in page and "marker 29" in page

    def test_it_is_still_only_that_race(self, client):
        """All of *its* events, not everybody's."""
        mine, theirs = make_race(), make_race(name="Someone else")
        ro.log_event(mine, "note", "mine", "test", {})
        ro.log_event(theirs, "note", "theirs", "test", {})
        from core.eventlog import get_events
        labels = [e["label"] for e in get_events(mine, limit=None)]
        assert "mine" in labels and "theirs" not in labels

    def test_the_dashboard_list_is_still_a_handful(self, client):
        """That one is every race at once, and is a glance rather than a record."""
        from core.eventlog import get_events
        race_id = make_race()
        for i in range(40):
            ro.log_event(race_id, "note", f"event {i}", "test", {})
        assert len(get_events(None, limit=20)) == 20


class TestTheFleetIsToldTheIntendedTime:
    """Setting the time AP comes down told the race officer, by way of a green
    banner, and nobody afloat."""

    def spoken(self, monkeypatch):
        said = []
        monkeypatch.setattr(raceadmin, "queue_central_audio",
                            lambda text, **k: said.append(text))
        monkeypatch.setattr(raceadmin, "signal_postponed", lambda *a, **k: None)
        return said

    def postponed(self):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        return race_id

    def test_setting_the_time_announces_it(self, client, monkeypatch):
        said = self.spoken(monkeypatch)
        race_id = self.postponed()
        said.clear()
        when = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert said, "the time was set and nobody afloat was told"
        assert when.strftime("%H:%M") in said[0], said

    def test_it_names_the_warning_and_the_gun_too(self, client, monkeypatch):
        said = self.spoken(monkeypatch)
        race_id = self.postponed()
        said.clear()
        when = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert (when + timedelta(minutes=1)).strftime("%H:%M") in said[0]
        assert (when + timedelta(minutes=6)).strftime("%H:%M") in said[0]

    def test_changing_the_time_announces_it_again(self, client, monkeypatch):
        """Otherwise the fleet is holding the first time it heard."""
        said = self.spoken(monkeypatch)
        race_id = self.postponed()
        said.clear()
        first = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        second = (datetime.now() + timedelta(minutes=12)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=first.isoformat(), signal=False)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=second.isoformat(), signal=False)
        assert len(said) == 2, said
        assert second.strftime("%H:%M") in said[1]

    def test_it_speaks_whole_minutes_not_seconds(self, client, monkeypatch):
        """Every time here is a whole minute by construction, so the seconds are
        always zero -- and this is read aloud, where they are one more thing to
        listen past."""
        import re
        said = self.spoken(monkeypatch)
        race_id = self.postponed()
        said.clear()
        when = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert not re.search(r"\d{1,2}:\d{2}:\d{2}", said[0]), said[0]
