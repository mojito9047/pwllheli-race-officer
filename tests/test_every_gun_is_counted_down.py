"""Every gun gets the same run-in, and the flags are no longer read out.

Reported by competitors as "different audio announcements around each gun", and
they were right: the start was counted down from ten and the warning,
preparatory and one-minute signals were not. Each of those had a "stand by"
fifteen seconds out and then nothing until the horn.

All four guns now run the same way -- "Stand by 15 seconds", the count from ten,
the signal -- and the start's thirty- and twenty-second calls are gone, replaced
by the same stand-by the other three use.

The class flags are no longer spoken. The race officer raising them does not
need telling, and the fleet can see them on the boat and on the public page.
They stay on the *log* line, which is the instruction to whoever raises them.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import startsequence  # noqa: E402
from core.startsequence import GUN_SIGNALS, STAND_BY_LEAD_SECONDS, countdown_lead_seconds  # noqa: E402

GUN_SECONDS = [300.0, 240.0, 60.0, 0.0]


def make_race(name="Gun Test"):
    warning = (datetime.now() + timedelta(minutes=30)).replace(second=0, microsecond=0)
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "", 1, warning.isoformat(timespec="seconds"), "DUAL", "",
             datetime.now().isoformat(timespec="seconds")))
        db.commit()
        return int(cur.lastrowid)


@pytest.fixture
def events(client):
    with ro.get_db() as db:
        race = db.execute("SELECT * FROM races WHERE id = ?", (make_race(),)).fetchone()
    return startsequence.central_start_sequence_events(race)


def spoken(events):
    return [e for e in events if e["kind"] == "audio"]


def fast_rate():
    return ro.race_console_config().get("central_audio_fast_rate")


class TestTheFourGunsAreAlike:
    def test_there_are_four_guns(self, events):
        assert sorted({e["sec"] for e in events if e["kind"] == "horn"}, reverse=True) == GUN_SECONDS

    @pytest.mark.parametrize("gun", GUN_SECONDS)
    def test_each_one_is_stood_by_fifteen_seconds_out(self, events, gun):
        at = [e for e in spoken(events) if e["sec"] == gun + STAND_BY_LEAD_SECONDS]
        assert len(at) == 1, f"no stand-by fifteen seconds before the {gun:.0f} s gun"
        assert at[0]["text"].endswith("Stand by 15 seconds.")

    @pytest.mark.parametrize("gun", GUN_SECONDS)
    def test_each_one_is_counted_down_from_ten(self, events, gun):
        """The reported fault: only the start was."""
        at = [e for e in spoken(events) if e["sec"] == gun + countdown_lead_seconds(fast_rate())]
        assert len(at) == 1, f"the {gun:.0f} s gun is not counted down"
        assert at[0]["text"] == startsequence.TEN_COUNT_TEXT

    def test_the_stand_by_says_the_same_thing_at_every_gun(self, events):
        said = {e["text"] for e in spoken(events)
                if e["sec"] in [g + STAND_BY_LEAD_SECONDS for g in GUN_SECONDS]}
        assert said == {"Stand by 15 seconds."}, said

    def test_the_run_in_is_built_from_one_table(self):
        """So a fifth signal cannot be given a different shape by accident."""
        assert [g[0] for g in GUN_SIGNALS] == GUN_SECONDS


class TestTheStartNoLongerHasItsOwnPattern:
    def test_the_thirty_and_twenty_second_calls_are_gone(self, events):
        for phrase in ("Thirty seconds", "Twenty seconds"):
            assert not any(phrase in e["text"] for e in spoken(events)), \
                f"the start still has its own {phrase!r} call"

    def test_and_nothing_is_spoken_between_the_stand_by_and_the_count(self, events):
        lead = countdown_lead_seconds(fast_rate())
        between = [e for e in spoken(events) if lead < e["sec"] < STAND_BY_LEAD_SECONDS]
        assert not between, [e["text"] for e in between]


class TestTheCountAlwaysStartsOnTime:
    """The audio worker speaks one item at a time, so the stand-by has to be
    finished -- and the VOX tone behind it played -- before the count is due.

    This is why the stand-by does not name the signal. "Stand by 15 seconds to
    preparatory signal" is seven words, which at the 80 wpm the settings allow is
    still being spoken when the count should have begun, and the count then ends
    after the gun. That is the exact fault `countdown_lead_seconds` was written
    for, and it should not be reintroduced from the other end.
    """

    SLOWEST_RATE = 80          # core.settings clamps central_audio_rate to 80..320
    TONE_SECONDS = 0.45        # play_vox_tone's default duration

    def _seconds_to_say(self, text, rate):
        words = len(re.findall(r"[A-Za-z0-9]+", text))
        return words / float(rate) * 60.0 + text.count(".") * 0.35

    def _room(self):
        return STAND_BY_LEAD_SECONDS - countdown_lead_seconds(fast_rate()) - self.TONE_SECONDS

    def test_the_stand_by_clears_the_count_even_at_the_slowest_voice(self, events):
        stand_by = next(e for e in spoken(events) if e["sec"] == STAND_BY_LEAD_SECONDS)
        assert self._seconds_to_say(stand_by["text"], self.SLOWEST_RATE) < self._room(), (
            "the stand-by is too long: at the slowest speech rate the settings allow it is "
            "still being spoken when the ten-count is due, which puts 'One' after the gun")

    def test_naming_the_signal_is_what_would_break_it(self, events):
        """Documents the trade-off rather than leaving it to be rediscovered."""
        assert self._seconds_to_say("Stand by 15 seconds to preparatory signal.",
                                    self.SLOWEST_RATE) > self._room()


class TestTheFlagsAreNotReadOut:
    def test_no_announcement_names_a_flag(self, events):
        for e in spoken(events):
            assert "Raise" not in e["text"], e["text"]
            assert "Numeral" not in e["text"], e["text"]

    def test_the_warning_still_says_what_it_is(self, events):
        at_gun = [e for e in spoken(events) if e["sec"] == 300.0]
        assert [e["text"] for e in at_gun] == ["Five minutes. Warning signal."]

    def test_but_the_log_line_still_tells_the_race_officer_which_flag(self):
        """It is the instruction to the person raising it, not an announcement."""
        source = (Path(__file__).resolve().parent.parent
                  / "core" / "startsequence.py").read_text(encoding="utf-8")
        assert '{horn} / {class_flags_text}' in source


class TestTheCountdownLabelIsShared:
    def test_every_count_carries_the_same_label(self, events):
        counts = [e for e in spoken(events) if e["text"] == startsequence.TEN_COUNT_TEXT]
        assert len(counts) == 4
        assert {e["label"] for e in counts} == {"Audio: countdown 10 to 1"}

    def test_it_carries_no_start_name(self, events):
        """In a rolling multi-start one start's gun lands on another's. Two
        ten-counts queued on the same eleven seconds would run twenty-two and
        finish after the gun; a shared label lets the scheduler's own
        already-fired-at-this-moment guard collapse them into one."""
        counts = [e for e in spoken(events) if e["text"] == startsequence.TEN_COUNT_TEXT]
        assert all(not e["label"].startswith("Start") for e in counts)


class TestTheRaceOfficersPlanSaysTheSameThing:
    """The console shows a table of the sequence, built from its own list. It
    claimed a course announcement at -03:00 that nothing has ever spoken."""

    def test_the_displayed_plan_has_a_row_for_every_run_in(self):
        from core.classconfig import SIGNAL_PLAN_TEMPLATE
        shown = {float(r["sec"]) for r in SIGNAL_PLAN_TEMPLATE}
        for gun in GUN_SECONDS:
            assert gun + STAND_BY_LEAD_SECONDS in shown, f"no plan row before the {gun:.0f} s gun"
            assert gun in shown

    def test_it_does_not_promise_anything_that_is_never_spoken(self, events):
        from core.classconfig import SIGNAL_PLAN_TEMPLATE
        spoken_secs = {float(e["sec"]) for e in events}
        for row in SIGNAL_PLAN_TEMPLATE:
            if row["sound"]:
                continue
            assert float(row["sec"]) in spoken_secs, \
                f"the plan shows {row['rel']} {row['action']!r} but nothing says it"


class TestTheTenMinuteHeadsUp:
    """Before any of it starts, what is coming and when.

    Ten minutes out is early enough to be useful to a boat still motoring out,
    and it is the only announcement that describes the sequence rather than
    being part of it.
    """

    def test_there_is_an_announcement_ten_minutes_out(self, events):
        at = [e for e in spoken(events) if e["sec"] == 600.0]
        assert len(at) == 1, "nothing is said at -10:00"

    def test_it_says_when_the_warning_signal_is(self, events):
        said = next(e for e in spoken(events) if e["sec"] == 600.0)["text"]
        assert "Warning signal in five minutes" in said

    def test_and_when_the_course_announcements_are(self, events):
        said = next(e for e in spoken(events) if e["sec"] == 600.0)["text"]
        assert "one minute and three minutes" in said

    def test_what_it_claims_is_true_of_the_schedule_it_describes(self, events):
        """The point of the test. Move a course announcement and this becomes a
        lie told to the whole fleet over the VHF, with nothing else to catch it.
        """
        warning = max(e["sec"] for e in events if e["kind"] == "horn")
        course = sorted((e["sec"] for e in spoken(events)
                         if "course announcement" in e["label"]), reverse=True)
        assert 600.0 - warning == 5 * 60, "the warning is no longer five minutes after it"
        assert len(course) == 2, course
        assert 600.0 - course[0] == 60, "the first course announcement is not a minute after it"
        assert 600.0 - course[1] == 3 * 60, "the second is not three minutes after it"

    def test_it_is_spoken_at_the_normal_rate(self, events):
        said = next(e for e in spoken(events) if e["sec"] == 600.0)
        assert said["rate"] == ro.race_console_config().get("central_audio_rate")

    def test_the_numbers_are_spelled_out_for_the_synthesiser(self, events):
        """"1 and 3 minutes" is read back as "one and three minutes"."""
        assert not re.search(r"\d", startsequence.PRESTART_HEADS_UP_TEXT)


class TestTheSchedulerWatchesEarlyEnoughToSayIt:
    """The window used to stop at exactly 600 s. The heads-up sits on that
    boundary and its VOX tone two seconds the wrong side of it, so the radio
    would never have been keyed and the announcement itself would have depended
    on which 2.5 s the poll happened to land in."""

    def test_the_window_clears_the_earliest_event(self, events):
        assert startsequence.SEQUENCE_WINDOW_SECONDS > max(e["sec"] for e in events)

    def test_including_its_vox_tone(self, client):
        ro.save_app_settings({"central_audio_vox_tone_enabled": "1",
                              "central_audio_vox_lead_seconds": "10"})
        try:
            with ro.get_db() as db:
                race = db.execute("SELECT * FROM races WHERE id = ?", (make_race(),)).fetchone()
            evs = startsequence.central_start_sequence_events(race)
            assert startsequence.SEQUENCE_WINDOW_SECONDS > max(e["sec"] for e in evs),                 "a race would enter the scheduler's window after its first tone was due"
        finally:
            ro.save_app_settings({"central_audio_vox_tone_enabled": "0",
                                  "central_audio_vox_lead_seconds": "2"})


class TestTheLogDoesNotSayItIsEmptyWhileFull:
    """Seen on a real start: fifteen rows of sequence events with "No horn or
    race events logged yet." underneath them.

    The race page is left open through the whole sequence, so the message is
    rendered while the log genuinely is empty and the rows arrive afterwards,
    from the live poll rather than from a reload. Nothing took it away.
    """

    TPL = (Path(__file__).resolve().parent.parent / "templates" / "race.html").read_text(encoding="utf-8")

    def test_the_message_is_findable(self):
        assert self.TPL.count('class="muted race-log-empty"') == 2, \
            "both copies of the empty-state message should be tagged"

    def test_adding_a_row_hides_it(self):
        body = self.TPL.split("function prependLog(")[1].split("\n  }")[0]
        assert "race-log-empty" in body, \
            "rows are added to the log without clearing the it-is-empty message"
        assert "hidden = true" in body

    def test_it_is_cleared_before_the_rows_go_in(self):
        """So a row and the message are never both on screen for a frame."""
        body = self.TPL.split("function prependLog(")[1].split("\n  }")[0]
        assert body.index("race-log-empty") < body.index("tbody.prepend(row)")

    def test_the_pursuit_page_carries_the_same_hook(self):
        """It has no live insert today, so its message is only ever right --
        but a future one should find the same handle rather than a bare <p>."""
        tpl = (Path(__file__).resolve().parent.parent / "templates" / "race_pursuit.html").read_text(encoding="utf-8")
        assert "race-log-empty" in tpl
