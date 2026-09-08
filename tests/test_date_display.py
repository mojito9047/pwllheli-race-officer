"""Dates the way a race officer in Gwynedd reads them.

The app showed every date as ``2026-09-19`` — the races list, the race log, the
audit trail and the SIM pages alike. That is unambiguous but it is nobody's
handwriting, and the people reading these screens write dates day-first.

So display is now ``19 Sep 2026``: day first, and the month **named** rather
than numbered, which is the part that matters. ``05/09/2026`` is read as the 5th
of September by one person and the 9th of May by another, and a race date is
exactly the sort of thing that gets read quickly.

ISO stays wherever a machine is the reader — log filenames, which sort by name,
and the Sailwave export, which is an interchange format. Those are not display.
"""
from __future__ import annotations

import datetime

from core.timeutils import date_display, dt_full_display


class TestTheFormat:
    def test_a_date_reads_day_first_with_the_month_named(self):
        assert date_display("2026-09-19") == "19 Sep 2026"

    def test_a_time_keeps_its_seconds(self):
        """Finish times are argued about to the second, so the time half of the
        format is untouched."""
        assert dt_full_display("2026-09-05 18:14:47") == "05 Sep 2026 18:14:47"

    def test_the_iso_t_separator_is_understood(self):
        """Half the app stores ``2026-08-05T18:45:06`` and half uses a space."""
        assert dt_full_display("2026-08-05T18:45:06") == "05 Aug 2026 18:45:06"

    def test_the_ambiguous_days_are_the_point(self):
        """Under 13 both readings are plausible, which is why the month is a
        word. Neither of these can be taken for the other."""
        assert date_display("2026-09-05") == "05 Sep 2026"
        assert date_display("2026-05-09") == "09 May 2026"

    def test_every_month_has_a_name(self):
        for month in range(1, 13):
            shown = date_display("2026-%02d-15" % month)
            assert shown.startswith("15 ") and shown.endswith(" 2026")
            assert not shown.split()[1].isdigit()

    def test_the_month_name_does_not_follow_the_machine_locale(self):
        """Written out rather than left to ``%b``. The club is in Gwynedd, and a
        Welsh-locale host would otherwise render the same race in Welsh on one
        machine and English on another."""
        import locale
        try:
            locale.setlocale(locale.LC_TIME, "cy_GB.UTF-8")
        except locale.Error:
            pass                        # locale absent: the point still holds
        try:
            assert date_display("2026-09-19") == "19 Sep 2026"
        finally:
            locale.setlocale(locale.LC_TIME, "C")


class TestWhatItDoesWithRubbish:
    def test_nothing_in_nothing_out(self):
        for value in ("", None, "not a date", "2026-13-45"):
            assert date_display(value) == ""
            assert dt_full_display(value) == ""

    def test_it_never_shows_a_bare_time_with_no_date(self):
        """``dt_full_display`` builds on ``date_display``, so a value the date
        half cannot parse must not leak through as a lone timestamp."""
        assert dt_full_display("rubbish") == ""


class TestWhereIsoSurvives:
    def test_log_filenames_stay_sortable(self):
        """They are named by date and listed by name, so the ordering is the
        format's whole job."""
        from core import logfiles
        assert logfiles.today() == datetime.date.today().isoformat()

    def test_the_sailwave_export_stays_iso(self):
        """An interchange format read by other software, not by a person."""
        from core import sailwave
        assert sailwave.sailwave_date(datetime.datetime(2026, 9, 19, 14, 30)) == "2026-09-19"
