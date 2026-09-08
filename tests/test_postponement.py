"""Postponing a race is a signal, not an edit of the start time.

Written after the first race one Sunday, when the wind died before the start and
the club delayed it by changing the start time. Nothing was signalled, so the
fleet's only notice was VHF or nothing at all -- and because the horn scheduler
is keyed on that time, editing it during a sequence drops the rest of the
running sequence and plans a fresh one. A fleet that has heard a warning and
then gets no preparatory and no gun is a general recall at best.

The app made that the only option: there was no AP anywhere in it, and the
Virtual Race Officer's prompt said in as many words that there was no postpone
tool and it should offer to move the start instead.

The rule that matters most here is that **the race officer does not choose the
new start time**. They choose when the wind is back; the warning signal follows
one minute after AP is lowered. That is what the flag is for, and it is the part
the old habit could not express.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import raceadmin, races, racesignals  # noqa: E402
from core.raceadmin import RaceValidationError  # noqa: E402


# Captured before the autouse fixture stubs them out, for the two tests that are
# about the sounds themselves rather than the record.
REAL_SIGNAL_POSTPONED = raceadmin.signal_postponed
REAL_SIGNAL_RESUMED = raceadmin.signal_resumed


def make_race(warning_in_minutes=30, name="Postpone Test"):
    """A race whose first warning signal is a while off, so it has not started."""
    warning = (datetime.now() + timedelta(minutes=warning_in_minutes)).isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "", 1, warning, "DUAL", "", datetime.now().isoformat(timespec="seconds")))
        db.commit()
        return int(cur.lastrowid)


def get(race_id):
    with ro.get_db() as db:
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


@pytest.fixture(autouse=True)
def no_real_horn(monkeypatch):
    """The signal is fired on a thread; these tests are about the record."""
    monkeypatch.setattr(raceadmin, "signal_postponed", lambda *a, **k: None)
    monkeypatch.setattr(raceadmin, "signal_resumed", lambda *a, **k: None)


class TestFlyingAP:
    def test_it_records_the_flag_and_the_time(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            call = raceadmin.postpone_race(db, get(race_id), "AP", actor="ro", signal=False)
        assert call.flag == "AP"
        row = get(race_id)
        assert races.race_is_postponed(row)
        assert races.postponement_flag(row) == "AP"

    def test_the_scheduled_time_is_not_touched(self, client):
        """The race keeps its time; AP is what changed. Moving the time is the
        habit this replaces, and doing both would be the worst of each."""
        race_id = make_race()
        before = get(race_id)["start_time"]
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert get(race_id)["start_time"] == before

    @pytest.mark.parametrize("said,flag", [
        ("AP", "AP"), ("ap", "AP"),
        ("AP over H", "AP over H"), ("ap_h", "AP over H"), ("AP/H", "AP over H"),
        ("AP over A", "AP over A"), ("AP_A", "AP over A"),
    ])
    def test_the_flag_can_be_said_the_way_it_is_spoken(self, client, said, flag):
        race_id = make_race()
        with ro.get_db() as db:
            call = raceadmin.postpone_race(db, get(race_id), said, signal=False)
        assert call.flag == flag

    def test_a_flag_that_is_not_a_postponement_is_refused(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError):
                raceadmin.postpone_race(db, get(race_id), "N", signal=False)

    def test_a_race_already_started_is_refused(self, client):
        """AP postpones races that have not started. After the gun it is
        abandonment -- flag N -- with different consequences for the boats
        already round the first mark, so doing AP quietly would be wrong."""
        race_id = make_race(warning_in_minutes=-60)
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert "already started" in str(exc.value)
        assert "flag N" in str(exc.value)

    def test_it_is_sounded_twice(self, monkeypatch):
        """Two sounds is what Race Signals specifies for a postponement, and it
        is how a boat too far off to read the flag knows to look at the mast."""
        blasts = []
        monkeypatch.setattr(raceadmin, "fire_horn",
                            lambda ms=None: blasts.append(ms) or {"ok": True})
        monkeypatch.setattr(raceadmin, "hardware_config", lambda: {"horn_duration_ms": 1})
        monkeypatch.setattr(raceadmin, "queue_central_audio", lambda *a, **k: None)
        monkeypatch.setattr(raceadmin.time, "sleep", lambda s: None)
        REAL_SIGNAL_POSTPONED(1, "AP", "Races not started are postponed.")
        assert len(blasts) == 2, f"AP sounded {len(blasts)} time(s), not two"

    def test_lowering_it_is_sounded_once(self, monkeypatch):
        blasts = []
        monkeypatch.setattr(raceadmin, "fire_horn",
                            lambda ms=None: blasts.append(ms) or {"ok": True})
        monkeypatch.setattr(raceadmin, "hardware_config", lambda: {"horn_duration_ms": 1})
        monkeypatch.setattr(raceadmin, "queue_central_audio", lambda *a, **k: None)
        monkeypatch.setattr(raceadmin.time, "sleep", lambda s: None)
        REAL_SIGNAL_RESUMED(1, "Postponement ended.")
        assert len(blasts) == 1, f"AP down sounded {len(blasts)} time(s), not one"

    def test_it_is_written_where_a_protest_would_look(self, client, monkeypatch):
        logged = []
        monkeypatch.setattr(raceadmin, "log_event",
                            lambda *a, **k: logged.append((a, k)) or 1)
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert logged, "the postponement was not logged as an event"
        assert "postpone" in str(logged[0])


class TestTheOneMinuteRule:
    """The reason postponing is a command of its own rather than an edit.

    And why "lower it now" is not good enough: pressed at 14:15:41 it put the
    warning at 14:16:41 and the gun at 14:21:41, which is not a countdown a
    fleet can follow. The race officer names the minute; everything follows
    from it on whole minutes.
    """

    def postponed(self, kind="AP"):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), kind, signal=False)
        return race_id

    def test_the_warning_is_one_minute_after_the_chosen_time(self, client):
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        assert datetime.fromisoformat(call.warning_time) == when + timedelta(minutes=1)

    def test_every_derived_time_is_a_whole_minute(self, client):
        """The complaint that produced this: 14:16:41 is not a countdown."""
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=3)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        for label, value in (("AP down", call.ends_at), ("warning", call.warning_time)):
            assert datetime.fromisoformat(value).second == 0, f"{label} has seconds in it"

    def test_seconds_typed_into_the_time_are_dropped(self, client):
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=6)).replace(second=37, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        assert datetime.fromisoformat(call.ends_at).second == 0

    def test_the_race_keeps_that_warning_time(self, client):
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        assert get(race_id)["start_time"] == call.warning_time

    def test_the_flag_stays_up_until_the_chosen_minute(self, client):
        """The displays must show what is on the mast, not what has been decided
        about it. The flag has not come down yet."""
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert races.race_is_postponed(get(race_id)) is True
        assert races.postponement_flag(get(race_id)) == "AP"

    def test_and_is_down_once_that_minute_has_passed(self, client):
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        later = when + timedelta(seconds=1)
        assert races.race_is_postponed(get(race_id), now=later) is False
        assert races.postponement_is_due_to_end(get(race_id), now=later) is True

    def test_no_time_given_means_the_next_usable_whole_minute(self, client):
        """One press for the commonest case, a round number, and far enough off
        that the announcement one minute beforehand still fits."""
        race_id = self.postponed()
        # Measured from before the call: comparing with a fresh `now` afterwards
        # makes the bound depend on how long the call took, which on a loaded
        # machine is a test that fails for no reason.
        before = datetime.now()
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), signal=False)
        ends = datetime.fromisoformat(call.ends_at)
        assert ends.second == 0
        lead = (ends - before).total_seconds()
        assert racesignals.LOWER_AP_MIN_LEAD_S <= lead <= racesignals.LOWER_AP_MIN_LEAD_S + 60

    def frozen(self, monkeypatch, at):
        """Hold the clock still. A whole minute that is both in the future and
        closer than the minimum lead only exists for part of each real minute,
        so testing that boundary against the wall clock is a coin toss."""
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return at
        monkeypatch.setattr(raceadmin, "datetime", Frozen)

    def test_a_time_too_soon_to_announce_is_refused(self, client, monkeypatch):
        """The app says "AP coming down in one minute" a minute beforehand. A
        flag lowered forty seconds from now is one nobody was told about -- and
        that is the case where the horn went unheard on the water."""
        race_id = self.postponed()
        at = datetime.now().replace(second=0, microsecond=0)
        self.frozen(monkeypatch, at)
        soon = at + timedelta(minutes=1)          # 60s away, under the 90s floor
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), lower_at=soon.isoformat(), signal=False)
        assert "too soon" in str(exc.value)
        assert exc.value.field == "lower_at"

    def test_and_the_message_says_the_earliest_that_works(self, client, monkeypatch):
        """An error that does not say what to type instead is half an error."""
        import re
        race_id = self.postponed()
        at = datetime.now().replace(second=0, microsecond=0)
        self.frozen(monkeypatch, at)
        soon = at + timedelta(minutes=1)
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), lower_at=soon.isoformat(), signal=False)
        assert re.search(r"\d{1,2}:\d{2}", str(exc.value)), str(exc.value)

    def test_but_two_minutes_away_is_fine(self, client, monkeypatch):
        """The floor is a floor, not a general suspicion of nearby times."""
        race_id = self.postponed()
        at = datetime.now().replace(second=0, microsecond=0)
        self.frozen(monkeypatch, at)
        when = at + timedelta(minutes=2)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        assert datetime.fromisoformat(call.ends_at) == when

    def test_a_time_that_has_gone_rolls_forward_rather_than_failing(self, client):
        """A proposal agreed to five minutes after it was read back must not
        error in the middle of a start, and must not put the gun on a stray
        second either."""
        race_id = self.postponed()
        gone = (datetime.now() - timedelta(minutes=10)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=gone.isoformat(),
                                         signal=False)
        ends = datetime.fromisoformat(call.ends_at)
        assert ends > datetime.now() and ends.second == 0

    def test_the_announcement_names_all_three_times(self, client):
        """AP down, warning, gun. The gun is the one people actually plan to."""
        race_id = self.postponed()
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(),
                                         signal=False)
        for t in (when, when + timedelta(minutes=1), when + timedelta(minutes=6)):
            assert t.strftime("%H:%M") in call.announcement, call.announcement

    def test_a_race_that_is_not_postponed_cannot_be_resumed(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError):
                raceadmin.resume_race(db, get(race_id), signal=False)

    def test_ap_over_h_has_no_one_minute_rule(self, client):
        """Further signals ashore means exactly that: the next signals come
        later, ashore. Inventing a warning one minute away would be a signal
        nobody ashore is waiting for."""
        race_id = self.postponed("AP over H")
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), signal=False)
        assert "ashore" in str(exc.value)

    def test_but_it_can_be_resumed_with_a_warning_time(self, client):
        race_id = self.postponed("AP over H")
        when = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            call = raceadmin.resume_race(db, get(race_id), warning_time=when.isoformat(),
                                         signal=False)
        assert datetime.fromisoformat(call.warning_time) == when
        assert not races.race_is_postponed(get(race_id))

    def test_ap_over_a_is_not_resumed_at_all(self, client):
        """No more racing today is a decision, not a pause."""
        race_id = self.postponed("AP over A")
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), signal=False)
        assert "no more racing today" in str(exc.value).lower()


class TestTheFlagComesDownOnTime:
    """The single sound is made when the flag actually comes down, by the
    scheduler that makes every other timed signal -- not when the race officer
    pressed the button some minutes earlier."""

    def due_race(self):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        gone = (datetime.now() - timedelta(minutes=1)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponement_ends_at = ? WHERE id = ?",
                       (gone.isoformat(timespec="seconds"), race_id))
            db.commit()
        return race_id

    def horn(self, monkeypatch, blasts, enabled=True):
        from core import startsequence
        monkeypatch.setattr(startsequence, "fire_horn", lambda ms=None: blasts.append(ms))
        monkeypatch.setattr(startsequence, "hardware_config", lambda: {"horn_duration_ms": 1})
        monkeypatch.setattr(startsequence, "race_console_config",
                            lambda: {"start_automation_horn_enabled": enabled})
        return startsequence

    def test_it_sounds_once_and_clears_the_flag(self, client, monkeypatch):
        blasts = []
        startsequence = self.horn(monkeypatch, blasts)
        race_id = self.due_race()
        assert startsequence.end_due_postponement(get(race_id)) is True
        assert blasts == [1]
        assert not races.race_is_postponed(get(race_id))

    def test_and_only_once(self, client, monkeypatch):
        """The loop runs every second; a second blast means something else
        entirely on the water."""
        blasts = []
        startsequence = self.horn(monkeypatch, blasts)
        race_id = self.due_race()
        startsequence.end_due_postponement(get(race_id))
        assert startsequence.end_due_postponement(get(race_id)) is False
        assert len(blasts) == 1

    def test_it_sounds_even_with_start_automation_off(self, client, monkeypatch):
        """The reported fault: two sounds went out putting AP up and nothing came
        out taking it down, because this one blast was gated on start automation
        and the other was not. AP up, a shortened course and AP down are all
        signals the race officer asked for -- this one merely at a minute they
        named -- so all three sound. Only the signals the app makes on its own
        behalf wait for automation to be switched on."""
        blasts = []
        startsequence = self.horn(monkeypatch, blasts, enabled=False)
        race_id = self.due_race()
        assert startsequence.end_due_postponement(get(race_id)) is True
        assert blasts == [1], "AP came down in silence"
        assert not races.race_is_postponed(get(race_id))

    def test_a_horn_that_will_not_fire_still_lowers_the_flag(self, client, monkeypatch):
        """Otherwise the database says postponed while the mast says otherwise."""
        from core import startsequence

        def broken(ms=None):
            raise OSError("serial port gone")

        monkeypatch.setattr(startsequence, "fire_horn", broken)
        monkeypatch.setattr(startsequence, "hardware_config", lambda: {"horn_duration_ms": 1})
        monkeypatch.setattr(startsequence, "race_console_config",
                            lambda: {"start_automation_horn_enabled": True})
        race_id = self.due_race()
        assert startsequence.end_due_postponement(get(race_id)) is True
        assert not races.race_is_postponed(get(race_id))


class TestTheSequenceGoesQuiet:
    def test_the_scheduler_skips_a_postponed_race(self, client, monkeypatch):
        """The difference between postponing a race and re-planning it. A horn
        that sounds under AP is the fault, not a detail."""
        from core import startsequence
        race_id = make_race(warning_in_minutes=2)
        fired = []
        monkeypatch.setattr(startsequence, "run_due_start_sequence_event",
                            lambda race, ev: fired.append(ev))
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert startsequence.race_is_postponed(get(race_id)) is True
        assert fired == []

    def test_and_the_predicate_the_scheduler_uses_is_the_shared_one(self):
        """Not a second reading of the column in the scheduler."""
        from core import startsequence
        assert startsequence.race_is_postponed is races.race_is_postponed


class TestTheFleetIsToldBeforeTheFlagMoves:
    """A single horn is easy to miss on the water, and a flag coming down is
    easier to miss still. So the app says it is about to happen, and then says
    what the sound was. This is also why the race officer cannot choose a moment
    less than ninety seconds away: there has to be room for the first of them.
    """

    def postponed_ending_at(self, when):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponement_ends_at = ?, start_time = ? WHERE id = ?",
                       (when.isoformat(timespec="seconds"),
                        (when + timedelta(minutes=1)).isoformat(timespec="seconds"), race_id))
            db.commit()
        return race_id

    def spoken(self, monkeypatch):
        from core import startsequence
        said = []
        monkeypatch.setattr(startsequence, "queue_central_audio",
                            lambda text, **k: said.append(text))
        return startsequence, said

    def test_one_minute_before_it_says_so(self, client, monkeypatch):
        startsequence, said = self.spoken(monkeypatch)
        now = datetime.now().replace(second=0, microsecond=0)
        race_id = self.postponed_ending_at(now + timedelta(seconds=45))
        assert startsequence.announce_postponement_ending_soon(get(race_id), now) is True
        assert "one minute" in said[0].lower(), said
        assert "answering pennant" in said[0].lower(), said

    def test_it_names_the_minute(self, client, monkeypatch):
        startsequence, said = self.spoken(monkeypatch)
        now = datetime.now().replace(second=0, microsecond=0)
        when = now + timedelta(seconds=45)
        race_id = self.postponed_ending_at(when)
        startsequence.announce_postponement_ending_soon(get(race_id), now)
        assert when.strftime("%H:%M") in said[0], said

    def test_not_earlier_than_a_minute_before(self, client, monkeypatch):
        """Said five minutes out it is not a warning, it is noise."""
        startsequence, said = self.spoken(monkeypatch)
        now = datetime.now().replace(second=0, microsecond=0)
        race_id = self.postponed_ending_at(now + timedelta(minutes=5))
        assert startsequence.announce_postponement_ending_soon(get(race_id), now) is False
        assert said == []

    def test_and_only_once(self, client, monkeypatch):
        """The loop runs every second."""
        startsequence, said = self.spoken(monkeypatch)
        now = datetime.now().replace(second=0, microsecond=0)
        race_id = self.postponed_ending_at(now + timedelta(seconds=45))
        for _ in range(5):
            startsequence.announce_postponement_ending_soon(get(race_id), now)
        assert len(said) == 1, said

    def test_but_changing_the_time_arms_it_again(self, client, monkeypatch):
        """Otherwise a race officer who moves the time gets no announcement for
        the time that actually happens."""
        startsequence, said = self.spoken(monkeypatch)
        now = datetime.now().replace(second=0, microsecond=0)
        race_id = self.postponed_ending_at(now + timedelta(seconds=45))
        startsequence.announce_postponement_ending_soon(get(race_id), now)
        later = now + timedelta(minutes=10)
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponement_ends_at = ? WHERE id = ?",
                       ((later + timedelta(seconds=45)).isoformat(timespec="seconds"), race_id))
            db.commit()
        assert startsequence.announce_postponement_ending_soon(get(race_id), later) is True
        assert len(said) == 2

    def test_when_it_comes_down_it_says_what_the_sound_was(self, client, monkeypatch):
        """A horn on its own means nothing in particular: on a start day there
        are several, and this one is not part of the sequence."""
        startsequence, said = self.spoken(monkeypatch)
        monkeypatch.setattr(startsequence, "fire_horn", lambda ms=None: None)
        monkeypatch.setattr(startsequence, "hardware_config", lambda: {"horn_duration_ms": 1})
        race_id = self.postponed_ending_at(datetime.now() - timedelta(minutes=1))
        startsequence.end_due_postponement(get(race_id))
        assert said, "AP came down without a word"
        assert "ap coming down" in said[0].lower() or "pennant down" in said[0].lower(), said

    def test_and_names_the_warning_and_the_gun(self, client, monkeypatch):
        startsequence, said = self.spoken(monkeypatch)
        monkeypatch.setattr(startsequence, "fire_horn", lambda ms=None: None)
        monkeypatch.setattr(startsequence, "hardware_config", lambda: {"horn_duration_ms": 1})
        when = (datetime.now() - timedelta(minutes=1)).replace(second=0, microsecond=0)
        race_id = self.postponed_ending_at(when)
        startsequence.end_due_postponement(get(race_id))
        warning = when + timedelta(minutes=1)
        assert warning.strftime("%H:%M") in said[0], said
        assert (warning + timedelta(minutes=5)).strftime("%H:%M") in said[0], said

    def test_the_announcements_respect_the_audio_setting_by_themselves(self):
        """`queue_central_audio` returns early when central audio is switched
        off, so there is no second gate to keep in step here -- unlike the horn,
        which had one and should not have."""
        source = (Path(__file__).resolve().parent.parent / "core" / "audio.py").read_text(
            encoding="utf-8")
        i = source.index("def queue_central_audio")
        assert "start_automation_audio_enabled" in source[i:i + 500]


class TestPressingLowerAPTwice:
    """The page is rendered once and the flag comes down by itself, so a race
    officer looking at an unrefreshed page can press a button for something that
    has already happened. "That race is not postponed." was true and useless."""

    def test_it_says_what_happened_and_what_is_scheduled(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        when = (datetime.now() + timedelta(minutes=3)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        # The flag comes down; the race officer's page still shows the button.
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponement_ends_at = ? WHERE id = ?",
                       ((datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds"),
                        race_id))
            db.commit()
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), signal=False)
        assert "already come down" in str(exc.value)
        assert "warning signal" in str(exc.value).lower()

    def test_a_race_never_postponed_still_says_so_plainly(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.resume_race(db, get(race_id), signal=False)
        assert "not postponed" in str(exc.value)


class TestThePagesSaySo:
    def test_the_race_sheet_shows_the_flag_and_offers_to_lower_it(self, logged_in_client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "AP is up" in page
        assert "Lower AP" in page

    def test_and_offers_to_postpone_when_it_is_not(self, logged_in_client):
        race_id = make_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'name="kind"' in page and "AP over H" in page

    def test_the_flag_reaches_the_panel_the_pages_draw_from(self, logged_in_client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP over A", signal=False)
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'data-postponed="AP over A"' in page

    def test_the_competitor_page_shows_it_too(self, client):
        """A postponement the fleet cannot see is the fault being fixed."""
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        page = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'data-postponed="AP"' in page


class TestTheCountdownStopsCounting:
    """Found by looking at the page rather than by a test: the flag was flying
    and the clock underneath it was still counting down to the old start time.
    A postponed race keeps its scheduled time until AP comes down, so a
    countdown left running tells the fleet a gun is coming that is not -- the
    display version of the fault this whole feature exists to fix.
    """

    PAGES = ("templates/race.html", "templates/competitor_race.html",
             "templates/race_pursuit.html")

    def source(self, name):
        return (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")

    @pytest.mark.parametrize("page", PAGES)
    def test_the_countdown_box_is_told_about_the_flag(self, page):
        text = self.source(page)
        assert 'class="countdown"' in text
        box = text[text.index('class="countdown"'):][:400]
        assert "data-postponed" in box, f"{page}: the countdown cannot see the postponement"

    @pytest.mark.parametrize("page", PAGES)
    def test_and_says_so_instead_of_counting(self, page):
        assert "postponedLabel" in self.source(page), page

    def test_the_clubhouse_display_too(self):
        """The screen a room full of people is looking at."""
        assert "postponedLabel" in self.source("static/bar_display.js")

    def test_the_wording_comes_from_the_shared_module(self):
        """Four pages saying "Postponed" four slightly different ways is how the
        flag rules drifted the first time."""
        shared = self.source("static/signal_flags.js")
        assert "function postponedLabel" in shared
        assert "postponedLabel: postponedLabel" in shared

    def test_it_names_the_variant(self):
        shared = self.source("static/signal_flags.js")
        assert "No more racing today" in shared and "ashore" in shared


class TestTheVirtualRaceOfficerCanDoIt:
    """The strongest case for that page: a race officer on a boat watching the
    wind die is exactly who needs this, and the alternative was the wrong
    procedure."""

    def context(self, race_id):
        from core.assistant import CommandContext
        return CommandContext(now=datetime.now(), current_race_id=race_id,
                              current_race_name="Sunday Points")

    def test_the_readback_names_the_flag_and_the_sounds(self):
        from core.assistant import Intent, resolve
        out = resolve(Intent("postpone_race", {"kind": "AP"}), self.context(3))
        assert "AP" in out.readback and "two horn blasts" in out.readback

    def test_it_says_the_rule_rather_than_asking_for_a_new_time(self):
        """Asking "what time shall I restart it?" would be the old habit wearing
        the new tool's clothes."""
        from core.assistant import Intent, resolve
        out = resolve(Intent("postpone_race", {}), self.context(3))
        assert "one minute after AP is lowered" in out.readback
        assert out.status == "needs_confirmation"

    def test_the_variants_read_back_differently(self):
        from core.assistant import Intent, resolve
        ashore = resolve(Intent("postpone_race", {"kind": "AP over H"}), self.context(3))
        done = resolve(Intent("postpone_race", {"kind": "AP over A"}), self.context(3))
        assert "Further signals ashore" in ashore.readback
        assert "No more racing today" in done.readback

    def test_resuming_names_all_three_times(self):
        """It used to name none of them, because AP came down the instant the
        button was pressed and no time could be promised. Now the race officer
        is agreeing to a minute, so the read-back has to show what follows from
        it -- above all the gun, which is what the fleet plans to."""
        import re
        from core.assistant import Intent, resolve
        out = resolve(Intent("resume_race", {}), self.context(3))
        times = re.findall(r"\d{1,2}:\d{2}", out.readback)
        assert len(times) == 3, out.readback
        assert "first gun" in out.readback

    def test_and_they_are_whole_minutes_a_minute_apart(self):
        from core.assistant import Intent, resolve
        out = resolve(Intent("resume_race", {}), self.context(3))
        lower = datetime.fromisoformat(out.resolved["lower_at"])
        assert lower.second == 0, out.resolved
        assert (lower + timedelta(minutes=1)).strftime("%H:%M") in out.readback

    def test_a_time_the_race_officer_names_is_used(self):
        from core.assistant import Intent, resolve
        out = resolve(Intent("resume_race", {"lower_at": "2026-08-17T15:30:00"}),
                      self.context(3))
        assert out.resolved["lower_at"].endswith("15:30:00")
        assert "15:31" in out.readback and "15:36" in out.readback

    def test_it_asks_which_race_when_there_is_none(self):
        from core.assistant import Intent, resolve
        out = resolve(Intent("postpone_race", {}),
                      self.context(None))
        assert out.status == "needs_clarification"
        assert out.clarify_field == "race_id"

    def test_the_interpreter_still_imports_no_service_layer(self):
        """It gained one core import -- the race-signal names, a module that
        imports nothing itself. The rule it must keep is that it cannot act."""
        text = (Path(__file__).resolve().parent.parent / "core" / "assistant.py").read_text(
            encoding="utf-8")
        assert "import raceadmin" not in text and "from core.raceadmin" not in text


class TestTheVocabularyLivesInOnePlace:
    def test_the_leaf_module_has_no_project_imports(self):
        """Four layers need these names, including the interpreter, which may not
        import the service layer. That only works if this module imports nothing."""
        text = (Path(__file__).resolve().parent.parent / "core" / "racesignals.py").read_text(
            encoding="utf-8")
        assert "from core" not in text and "import core" not in text

    def test_everyone_reads_the_same_kinds(self):
        from core import assistant
        assert assistant.POSTPONEMENT_KINDS is racesignals.POSTPONEMENT_KINDS
        assert races.POSTPONEMENT_KINDS is racesignals.POSTPONEMENT_KINDS
        assert raceadmin.POSTPONEMENT_KINDS is racesignals.POSTPONEMENT_KINDS

    def test_the_one_minute_rule_is_a_named_constant(self):
        assert racesignals.RESUME_WARNING_DELAY_S == 60


class TestItIsWhereTheRaceOfficerIsStanding:
    """Postponing belongs on the Start console tab. Before the start, that is the
    tab that is open -- the countdown, the signal plan and the manual controls
    are all there. It was first put on the shorten-course tab, which is where
    somebody goes half an hour *into* a race."""

    def source(self, name):
        return (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")

    def test_the_postpone_card_is_on_the_start_console_tab(self, logged_in_client):
        race_id = make_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        start_tab = page[page.index('<div id="tab-start"'):page.index('<div id="tab-shorten"')]
        assert "Postpone (AP)" in start_tab

    def test_and_not_on_the_shorten_tab(self, logged_in_client):
        race_id = make_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        shorten_tab = page[page.index('<div id="tab-shorten"'):]
        assert "Postpone (AP)" not in shorten_tab[:4000]

    def test_the_obsolete_manual_ap_button_is_gone(self):
        """It sounded two horns and wrote a note, and did none of the postponing:
        the sequence carried on underneath it, so it signalled AP to the fleet
        and then fired the warning gun anyway. Worse than nothing."""
        for page in ("templates/race.html", "templates/start_console.html"):
            assert "Postpone - AP" not in self.source(page), page
            assert "Postponement AP" not in self.source(page), page

    def post(self, client, url, data):
        """Logged in *and* with a token the session agrees with: `csrf_post` uses
        the anonymous client, and logging in replaces the session."""
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        return client.post(url, data=dict(data, _csrf_token=token))

    def test_pressing_it_comes_back_to_the_same_tab(self, logged_in_client):
        """The race sheet remembers the tab in the URL fragment and a redirect
        has none, so postponing threw the race officer back to Course & start --
        mid-sequence, looking at the wrong page."""
        race_id = make_race()
        resp = self.post(logged_in_client, f"/admin/race/{race_id}/postpone",
                         {"kind": "AP"})
        assert resp.status_code in (302, 303), resp.status_code
        assert resp.headers["Location"].endswith("#tab-start"), resp.headers["Location"]

    def test_and_so_does_shortening(self, logged_in_client):
        """Same defect, same tab-remembering scheme."""
        race_id = make_race()
        resp = self.post(logged_in_client, f"/admin/race/{race_id}/shorten",
                         {"index": "0"})
        assert resp.headers["Location"].endswith("#tab-shorten"), resp.headers["Location"]


class TestItReachesEveryPublicScreen:
    """Found by looking rather than by a test, twice. The clubhouse display and
    the club's landing page each keep their own clock, and each went on counting
    down to a gun that AP had stopped."""

    def test_the_clubhouse_display_is_told(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        page = client.get(f"/bar/{race_id}").get_data(as_text=True)
        flags = page[page.index('<div class="bar-flags"'):]
        assert 'data-postponed="AP"' in flags[:600], "the flag box was not told"

    def test_the_landing_page_says_postponed_rather_than_a_countdown(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        with ro.app.test_request_context():
            card = ro.public_race_card(get(race_id), race_id)
        assert card["postponed_flag"] == "AP"
        assert card["status"] == "Postponed", card["status"]

    def test_the_render_signature_moves_when_ap_goes_up(self, client):
        """The clubhouse display only re-renders when this changes. Without it,
        AP goes up in the hut and the television in the bar carries on counting
        down until somebody finds a keyboard -- which is the exact fault this
        signature was added for, in a new place."""
        race_id = make_race()
        before = ro.public_render_signature(get(race_id))
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert ro.public_render_signature(get(race_id)) != before

    def test_and_when_the_time_it_comes_down_is_set(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        before = ro.public_render_signature(get(race_id))
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert ro.public_render_signature(get(race_id)) != before


class TestTheFlagIsDrawnLikeAPennant:
    """Corrected twice from a photograph. A rectangle of red and white stripes is
    not a rough likeness of AP -- it is a different flag, and the race sheet is
    the one place that must not teach the wrong one."""

    def css(self):
        return (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(
            encoding="utf-8")

    def test_it_tapers_along_its_whole_length(self):
        """Not a rectangle with a triangle stuck on the end: the hoist edge is
        full height and the fly edge is short, with straight edges between."""
        css = self.css()
        assert "points='1,1 98,16 98,50 1,65'" in css, "the pennant outline is not the taper"

    def test_and_ends_blunt_rather_than_at_a_point(self):
        css = self.css()
        assert "98,16 98,50" in css, "the fly end has collapsed to a point"

    def test_the_outline_follows_the_same_shape_as_the_stripes(self):
        """Two polygons, one clipping the stripes and one drawing the edge. If
        they ever differ the flag gets a border down the middle of nothing."""
        assert self.css().count("points='1,1 98,16 98,50 1,65'") == 2

    def test_it_needs_nothing_from_the_network(self):
        """The numerals and P are PNG files; this is a data URI in the
        stylesheet, so there is no second thing that can fail to arrive."""
        css = self.css()
        i = css.index(".signal-flag.ap, .mini-flag.ap")
        block = css[i:i + 1400]
        assert "data:image/svg+xml" in block
        assert "/static/img" not in block


class TestEveryClockInTheApp:
    """The question was "are there any more places?", so this is the answer in a
    form that stays answered. Every page that counts down to a start has to know
    about AP, because a race under AP keeps its scheduled time -- so a clock that
    has not been told counts down to a gun that is not coming.

    The dashboard and the Virtual Race Officer strip were both missed the first
    time round, and each was found by looking at the page rather than by a test.
    """

    # Every element in the app that counts towards a start, with the file it is
    # rendered by. Adding a clock without adding it here is the failure mode.
    CLOCKS = (
        ("templates/index.html", "the race office dashboard"),
        ("templates/race.html", "the race sheet, and its start-console tab"),
        ("templates/race_pursuit.html", "the pursuit race sheet"),
        ("templates/competitor_race.html", "the competitor race page"),
        ("templates/competitors_home.html", "the club's landing page"),
        ("templates/bar_display.html", "the clubhouse display"),
        ("templates/onwater.html", "the Virtual Race Officer strip"),
        ("templates/start_console.html", "the standalone start console"),
    )

    def source(self, name):
        return (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")

    @pytest.mark.parametrize("path,what", CLOCKS)
    def test_it_is_told_the_flag_is_up(self, path, what):
        assert "data-postponed" in self.source(path), f"{what} is not told about AP"

    @pytest.mark.parametrize("path,what", CLOCKS)
    def test_and_when_it_comes_down(self, path, what):
        assert "data-postponement-ends-at" in self.source(path), \
            f"{what} cannot tell when AP comes down, so it would show it for ever"

    @pytest.mark.parametrize("path,what", CLOCKS)
    def test_and_says_postponed_instead_of_counting(self, path, what):
        """In the template, or in the script file that drives it -- the clubhouse
        display and the Virtual Race Officer strip both render from their own."""
        companion = "static/" + Path(path).stem + ".js"
        sources = [self.source(path)]
        if (Path(__file__).resolve().parent.parent / companion).exists():
            sources.append(self.source(companion))
        assert any("postponedLabel" in text for text in sources),             f"{what} still counts down under AP"

    def test_no_clock_was_left_off_this_list(self):
        """A new countdown that nobody adds here is the next one to be missed."""
        import re
        found = set()
        root = Path(__file__).resolve().parent.parent
        for path in list((root / "templates").glob("*.html")) + list((root / "static").glob("*.js")):
            text = path.read_text(encoding="utf-8")
            # A race clock is something that counts towards a stored start time.
            if re.search(r"dataset\.start\b|data-start=|startIso|startTs", text):
                found.add(path.relative_to(root).as_posix())
        known = {p for p, _ in self.CLOCKS} | {
            "static/bar_display.js", "static/onwater.js", "static/countdown.js",
            # Charts and replays, which draw a race that has been sailed rather
            # than counting towards one that has not started.
            "static/race_replay.js", "static/course_map.js", "templates/race_replay.html",
        }
        assert not (found - known), f"a clock nobody told about AP: {sorted(found - known)}"


class TestTheFlagComesDownWithoutAReload:
    """Reported from the hut: the moment passed, and the race sheet went on
    showing AP with a stopped clock until it was refreshed. Both times are in
    the page already, so no polling is needed -- but the rule has to be asked
    every tick rather than once when the page loads."""

    def source(self, name):
        return (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")

    def test_the_rule_is_shared(self):
        shared = self.source("static/signal_flags.js")
        assert "function postponedNow" in shared
        assert "postponedNow: postponedNow" in shared

    def test_it_is_up_until_the_moment_and_not_after(self):
        """The rule itself, read out of the module: no ends-at means up until
        further notice; before it, up; after it, down."""
        shared = self.source("static/signal_flags.js")
        i = shared.index("function postponedNow")
        body = shared[i:shared.index("}", shared.index("return", i))]
        assert "if (!flagName) return ''" in body
        assert "return flagName" in body        # no ends-at: still up
        assert "< ends" in body                  # before the moment: still up

    @pytest.mark.parametrize("path", [
        "templates/race.html", "templates/race_pursuit.html",
        "templates/competitor_race.html", "templates/competitors_home.html",
        "templates/index.html", "static/bar_display.js", "static/onwater.js",
    ])
    def test_every_renderer_asks_the_clock_not_the_attribute(self, path):
        """`dataset.postponed` on its own is the bug: it never changes, so the
        flag never comes down."""
        source = self.source(path)
        assert "postponedNow(" in source, f"{path} reads the attribute directly"

    def test_the_lowering_form_retires_itself(self):
        """And the button that acts on a flag already down stops being pressable,
        which is where "That race is not postponed." came from."""
        source = self.source("templates/race.html")
        assert "retireLoweringForm" in source
        assert "AP has come down" in source


class TestTheSignalPlanUnderAP:
    """The plan is the list a race officer reads to know what the app is about
    to do, so under AP it was wrong in both directions at once: it listed the
    course announcements due before the flag comes down as though they would be
    made -- they are not, because nothing sounds while AP flies -- and it left
    out the two signals that will actually happen.

    Reported from the start console, where a plan starting at 16:51 sat under a
    banner saying AP comes down at 16:54.
    """

    def plan_for(self, minutes_ahead=5, ends_in_minutes=4):
        """A race postponed now, with AP coming down before the warning signal."""
        from core.classconfig import start_signal_plan_rows
        now = datetime.now().replace(second=0, microsecond=0)
        race_id = make_race()
        warning = now + timedelta(minutes=minutes_ahead)
        ends = now + timedelta(minutes=ends_in_minutes)
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = ?, postponed_at = ?,"
                       " postponement_kind = 'AP', postponement_ends_at = ? WHERE id = ?",
                       (warning.isoformat(timespec="seconds"),
                        now.isoformat(timespec="seconds"),
                        ends.isoformat(timespec="seconds"), race_id))
            db.commit()
        return start_signal_plan_rows(get(race_id)), ends, warning

    def test_the_flag_coming_down_is_in_the_plan(self, client):
        rows, ends, _ = self.plan_for()
        ap = [r for r in rows if r.get("postponement")]
        assert ap, "the plan does not mention AP at all"
        assert any(r["signal_dt"] == ends and r["horn"] == "1 sound" for r in ap), \
            "the one sound as AP is lowered is not in the plan"

    def test_and_so_is_the_announcement_a_minute_before(self, client):
        rows, ends, _ = self.plan_for()
        minute_before = ends - timedelta(seconds=racesignals.AP_DOWN_WARNING_LEAD_S)
        assert any(r.get("postponement") and r["signal_dt"] == minute_before for r in rows), \
            "the one-minute announcement is not in the plan"

    def test_signals_before_the_flag_comes_down_are_marked_held(self, client):
        """These are the ones that will not be made. Shown rather than removed,
        because a race officer looking for the course announcement needs to see
        that it is not coming rather than not find it."""
        rows, ends, _ = self.plan_for()
        before = [r for r in rows if not r.get("postponement") and r["signal_dt"] < ends]
        assert before, "this race has no signals scheduled before AP comes down"
        assert all(r.get("suppressed") for r in before), \
            "a signal due under AP is still shown as though it will be made"
        assert all("AP" in (r.get("suppressed_note") or "") for r in before)

    def test_signals_after_it_are_left_alone(self, client):
        rows, ends, warning = self.plan_for()
        after = [r for r in rows if not r.get("postponement") and r["signal_dt"] >= ends]
        assert after, "nothing scheduled after AP comes down"
        assert not any(r.get("suppressed") for r in after)

    def test_with_no_moment_chosen_the_whole_plan_is_held(self, client):
        """AP up and no time set yet: none of it is going to happen."""
        from core.classconfig import start_signal_plan_rows
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        rows = start_signal_plan_rows(get(race_id))
        assert rows and all(r.get("suppressed") for r in rows)
        assert not any(r.get("postponement") for r in rows), \
            "an AP-down row was invented for a time nobody has chosen"

    def test_a_race_that_is_not_postponed_is_untouched(self, client):
        from core.classconfig import start_signal_plan_rows
        rows = start_signal_plan_rows(get(make_race()))
        assert rows
        assert not any(r.get("suppressed") or r.get("postponement") for r in rows)

    def test_the_plan_stays_in_time_order(self, client):
        """The AP rows are inserted, not appended."""
        rows, _, _ = self.plan_for()
        times = [r["signal_dt"] for r in rows if r.get("signal_dt")]
        assert times == sorted(times)

    @pytest.mark.parametrize("page", ["templates/race.html", "templates/start_console.html",
                                      "templates/race_pursuit.html"])
    def test_every_plan_table_shows_it(self, page):
        """Three pages render this table; all three have to say the same thing."""
        text = (Path(__file__).resolve().parent.parent / page).read_text(encoding="utf-8")
        assert "row.suppressed" in text, f"{page} shows held signals as though they will be made"
        assert "row.postponement" in text, f"{page} does not mark the AP rows"

    def test_it_reaches_the_start_console(self, logged_in_client):
        race_id = make_race()
        now = datetime.now().replace(second=0, microsecond=0)
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = ?, postponed_at = ?,"
                       " postponement_kind = 'AP', postponement_ends_at = ? WHERE id = ?",
                       ((now + timedelta(minutes=5)).isoformat(timespec="seconds"),
                        now.isoformat(timespec="seconds"),
                        (now + timedelta(minutes=4)).isoformat(timespec="seconds"), race_id))
            db.commit()
        page = logged_in_client.get(f"/admin/race/{race_id}/start_console").get_data(as_text=True)
        assert "signal-postponement" in page, "the AP rows are not in the rendered plan"
        assert "signal-held" in page, "nothing is marked as held"


class TestTheHornAndRaceLog:
    """Every AP signal is in the race log, which is the record a protest or a
    "what happened?" the following week is settled from. Three entries: the flag
    going up, the announcement a minute before it comes down, and the flag
    coming down with its sound.
    """

    def events(self, race_id):
        from core.eventlog import get_events
        return [(e["event_type"], e["label"], e["source"]) for e in get_events(race_id, limit=50)]

    def test_flying_ap_is_logged(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        kinds = self.events(race_id)
        assert any(t == "postpone" and "AP up" in label for t, label, _ in kinds), kinds

    def test_it_says_which_flag(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP over H", signal=False)
        assert any("AP over H" in label for _, label, _ in self.events(race_id))

    def test_the_time_it_will_come_down_is_logged_when_it_is_set(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert any(when.strftime("%H:%M") in label for _, label, _ in self.events(race_id))

    def test_the_one_minute_announcement_is_logged(self, client, monkeypatch):
        from core import startsequence
        monkeypatch.setattr(startsequence, "queue_central_audio", lambda *a, **k: None)
        race_id = make_race()
        now = datetime.now().replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
            db.execute("UPDATE races SET postponement_ends_at = ? WHERE id = ?",
                       ((now + timedelta(seconds=45)).isoformat(timespec="seconds"), race_id))
            db.commit()
        startsequence.announce_postponement_ending_soon(get(race_id), now)
        assert any("one minute" in label for _, label, _ in self.events(race_id))

    def test_and_the_flag_coming_down_with_its_sound(self, client, monkeypatch):
        from core import startsequence
        monkeypatch.setattr(startsequence, "queue_central_audio", lambda *a, **k: None)
        monkeypatch.setattr(startsequence, "fire_horn", lambda ms=None: None)
        monkeypatch.setattr(startsequence, "hardware_config", lambda: {"horn_duration_ms": 1})
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
            db.execute("UPDATE races SET postponement_ends_at = ? WHERE id = ?",
                       ((datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds"),
                        race_id))
            db.commit()
        startsequence.end_due_postponement(get(race_id))
        assert any("AP down, one sound" in label for _, label, _ in self.events(race_id))

    def test_the_log_table_on_the_race_sheet_shows_them(self, logged_in_client):
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        log = page[page.index('id="liveLog"'):]
        assert "AP up" in log[:4000], "the postponement is not in the Horn and race log"

    def test_and_the_live_poll_picks_them_up_without_a_reload(self, logged_in_client):
        """The table polls for events newer than the last it has, so a
        postponement made from the water appears in the hut without anybody
        refreshing. Nothing filters by event type, which is what makes this work."""
        race_id = make_race()
        before = logged_in_client.get(
            f"/api/race/{race_id}/events?after_id=0").get_json()["events"]
        highest = max([e["id"] for e in before] or [0])
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        after = logged_in_client.get(
            f"/api/race/{race_id}/events?after_id={highest}").get_json()["events"]
        labels = [e.get("label", "") for e in after]
        assert any("AP up" in text for text in labels), labels


class TestPostponingAfterTheStart:
    """AP postpones races that have not started. Once the gun has gone the signal
    is abandonment -- flag N -- which this app does not make. The service layer
    always refused; the page went on offering the button and answering with an
    error, which is a worse way of saying no."""

    def started_race(self):
        return make_race(warning_in_minutes=-30)     # gun 25 minutes ago

    def test_the_button_is_not_offered(self, logged_in_client):
        race_id = self.started_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        start_tab = page[page.index('<div id="tab-start"'):page.index('<div id="tab-shorten"')]
        assert 'name="kind"' not in start_tab, "postpone is still offered after the start"

    def test_and_the_page_says_why_and_what_to_do_instead(self, logged_in_client):
        race_id = self.started_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "This race has started" in page
        assert "flag <strong>N</strong>" in page
        assert "shorten the course" in page

    def test_before_the_start_it_is_offered(self, logged_in_client):
        race_id = make_race(warning_in_minutes=30)
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        start_tab = page[page.index('<div id="tab-start"'):page.index('<div id="tab-shorten"')]
        assert 'name="kind"' in start_tab

    def test_and_the_rule_is_still_enforced_underneath(self, client):
        """The page not offering it is a courtesy; the refusal is the guarantee.
        The Virtual Race Officer reaches the same function from the water."""
        race_id = self.started_race()
        with ro.get_db() as db:
            with pytest.raises(RaceValidationError) as exc:
                raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert "already started" in str(exc.value)

    def test_a_race_already_postponed_still_offers_to_lower_it(self, logged_in_client):
        """A race postponed before its scheduled gun, whose gun time has since
        passed, must still be able to lower AP -- otherwise the flag is stuck up
        with no way to take it down."""
        race_id = make_race(warning_in_minutes=-30)
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponed_at = ?, postponement_kind = 'AP' WHERE id = ?",
                       (datetime.now().isoformat(timespec="seconds"), race_id))
            db.commit()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "Lower AP" in page, "AP was stuck up with no way to lower it"


class TestTheStartVideoFollowsTheStart:
    """A start clip is booked the moment a start time is set, and its builder
    thread then sleeps until that moment and cuts the clip out of the rolling
    buffer. Postponing does not move the stored time -- the flag is what changed
    -- so without this the club gets a minute of empty start line filed as the
    start video, and afterwards two clips to choose between, one of nothing.

    This is the one that matters most of the display faults, because the clip is
    the evidence a protest is settled from.
    """

    def booked_clip(self, race_id, when):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds,"
                " post_seconds, status, label, message, created_at, updated_at)"
                " VALUES (?, 'start', ?, 60, 60, 'pending', 'Start 1 video', '', ?, ?)",
                (race_id, when, datetime.now().isoformat(timespec="seconds"),
                 datetime.now().isoformat(timespec="seconds")))
            db.commit()
            return int(cur.lastrowid)

    def status_of(self, clip_id):
        with ro.get_db() as db:
            return db.execute("SELECT status, message FROM video_clips WHERE id = ?",
                              (clip_id,)).fetchone()

    def test_postponing_cancels_the_clip_booked_for_the_old_start(self, client):
        race_id = make_race()
        gun = ro.race_first_start_time(get(race_id))
        clip_id = self.booked_clip(race_id, gun)
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        row = self.status_of(clip_id)
        assert row["status"] == "cancelled", "a clip of the empty line was left booked"
        assert "postponed" in row["message"].lower()

    def test_a_clip_that_has_already_been_built_is_left_alone(self, client):
        """Only pending ones. A clip already cut is a record of something that
        happened, whatever happens next."""
        race_id = make_race()
        clip_id = self.booked_clip(race_id, ro.race_first_start_time(get(race_id)))
        from core.video import update_video_clip_status
        update_video_clip_status(clip_id, "ready", "Clip built.")
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        assert self.status_of(clip_id)["status"] == "ready"

    def test_resuming_books_one_for_the_new_gun(self, client, monkeypatch):
        from core import video
        booked = []
        monkeypatch.setattr(video, "schedule_video_clip",
                            lambda race_id, kind, when, **k: booked.append((kind, when)))
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        race_id = make_race()
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        gun = when + timedelta(minutes=6)          # AP down + 1 warning + 5 to the gun
        assert any(kind == "start" and gun.strftime("%H:%M") in str(at)
                   for kind, at in booked), booked

    def test_and_cancels_anything_still_waiting_for_an_older_one(self, client):
        race_id = make_race()
        stale = self.booked_clip(race_id, ro.race_first_start_time(get(race_id)))
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        when = (datetime.now() + timedelta(minutes=4)).replace(second=0, microsecond=0)
        with ro.get_db() as db:
            raceadmin.resume_race(db, get(race_id), lower_at=when.isoformat(), signal=False)
        assert self.status_of(stale)["status"] == "cancelled"

    def test_the_builder_checks_again_when_it_wakes(self, client, monkeypatch):
        """The thread is already asleep when the postponement happens -- it is
        booked when the time is set and can wait an hour. Cancelling the row is
        no use if the sleeper does not look at it again."""
        from core import video
        race_id = make_race()
        clip_id = self.booked_clip(race_id, datetime.now().isoformat(timespec="seconds"))
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        built = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True,
                                                            "video_post_seconds": 0,
                                                            "video_segment_seconds": 1})
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        monkeypatch.setattr(video, "ffmpeg_executable", lambda cfg: built.append("looked") or "")
        monkeypatch.setattr(video.time, "sleep", lambda s: None)
        video.build_video_clip_after_delay(clip_id)
        assert built == [], "the builder went ahead with a cancelled clip"

    def test_the_scheduler_books_nothing_new_while_ap_flies(self, client):
        """`ensure_start_video_scheduled` is reached after the postponed check in
        the loop, so a postponed race is skipped before it can rebook."""
        source = (Path(__file__).resolve().parent.parent / "core" / "startsequence.py").read_text(
            encoding="utf-8")
        loop = source[source.index("def start_sequence_scheduler_loop"):]
        assert loop.index("race_is_postponed") < loop.index("ensure_start_video_scheduled"), \
            "a postponed race can still book a start clip"


class TestTheClubhouseCameraUnderAP:
    """The bar screen cuts to the start-hut camera for two minutes either side of
    the gun, captioned "Start in 1:35". Under AP that counted down to the old
    time under a banner reading POSTPONED, and would have put the camera up for
    a start that was not happening."""

    def test_no_start_window_while_ap_is_up(self, client):
        from core import bardisplay
        race_id = make_race(warning_in_minutes=5)
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        race = get(race_id)
        start_dt = ro.race_first_start_dt(race)
        from core.races import race_is_postponed
        start_ts = start_dt.timestamp() if start_dt and not race_is_postponed(race) else None
        window = bardisplay.video_window(start_ts, [], start_dt.timestamp() - 60)
        assert window["show"] is False, "the camera opened for a start under AP"
        assert bardisplay.caption_for(window) == ""

    def test_and_it_comes_back_once_the_flag_is_down(self, client):
        from core import bardisplay
        from core.races import race_is_postponed
        race_id = make_race(warning_in_minutes=5)
        race = get(race_id)
        start_dt = ro.race_first_start_dt(race)
        start_ts = start_dt.timestamp() if start_dt and not race_is_postponed(race) else None
        window = bardisplay.video_window(start_ts, [], start_dt.timestamp() - 60)
        assert window["show"] is True and window["reason"] == "start"
        assert bardisplay.caption_for(window) == "Start in 1:00"

    def test_the_bar_state_endpoint_holds_the_camera(self, client):
        race_id = make_race(warning_in_minutes=5)
        with ro.get_db() as db:
            raceadmin.postpone_race(db, get(race_id), "AP", signal=False)
        state = client.get(f"/bar/state/{race_id}").get_json()
        assert state["video"]["show"] is False, state["video"]
        assert not state["video"].get("caption")
