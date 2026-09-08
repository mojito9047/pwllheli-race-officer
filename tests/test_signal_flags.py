"""Hoisted signal flags on the clubhouse display, and the rule they share.

The clubhouse display had no flags at all: the one screen in the room during a start
sequence, and it showed the countdown without saying what was up the mast.

Which flags are hoisted is now one rule in ``static/signal_flags.js``, called by both
the competitor page and the clubhouse display. It was inline in
``templates/competitor_race.html`` before, and a second copy for the bar is exactly how
this codebase has gone wrong twice already — the live course walk against the replay's,
and the harness against the app. So these tests care as much about there being *one*
implementation as about the markup.

The flag layout itself is asserted here only as far as Python can see it; the ordering
was checked in a browser (three flags up, reading P / Class 2 / Class 1 left to right,
so the first hoisted sits nearest the clock).
"""
from __future__ import annotations

import pathlib

import pytest

import app as ro


_STATIC = ro.appstate.BASE_DIR / "static"
_TEMPLATES = ro.appstate.BASE_DIR / "templates"

# Every page that shows hoisted flags. There were four copies of the rule; the first
# pass at this shared only two, which is how race_pursuit.html was found to have quietly
# lost Code flag S altogether.
PAGES_WITH_FLAGS = ("competitor_race.html", "bar_display.html",
                    "race.html", "race_pursuit.html")
# Where the rendering actually happens. Three pages do it inline; the clubhouse display
# does it from its own script file, which is why this is not just the template list.
FLAG_RENDERERS = (_TEMPLATES / "competitor_race.html",
                  _TEMPLATES / "race.html",
                  _TEMPLATES / "race_pursuit.html",
                  _STATIC / "bar_display.js")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheRuleLivesInOnePlace:
    def test_the_shared_module_exists(self):
        assert (_STATIC / "signal_flags.js").is_file()

    @pytest.mark.parametrize("name", ["liveFlags", "markup", "summary"])
    def test_it_exposes_what_both_pages_need(self, name):
        assert f"{name}:" in _read(_STATIC / "signal_flags.js")

    def test_it_carries_the_rrs_26_windows(self):
        """A class flag is up from its warning signal (five minutes) to its start; P is
        up from the preparatory signal (four minutes) to one minute."""
        source = _read(_STATIC / "signal_flags.js")
        assert "WARNING_S = 300" in source
        assert "PREP_UP_S = 240" in source
        assert "PREP_DOWN_S = 60" in source

    def test_the_competitor_page_no_longer_has_its_own_copy(self):
        """The extraction has to be finished, not merely started: a leftover inline
        copy would be the two-implementations problem with extra steps."""
        source = _read(_TEMPLATES / "competitor_race.html")
        assert "function liveSignalFlags" not in source
        assert "SignalFlags.liveFlags(" in source

    def test_every_page_that_shows_flags_loads_it(self):
        for template in PAGES_WITH_FLAGS:
            assert "signal_flags.js" in _read(_TEMPLATES / template), template

    def test_no_source_keeps_its_own_copy_of_the_rule(self):
        """There were four copies, and unifying two would have been worse than leaving
        all four: the two that agreed would drift from the two that did not.

        Guarded on the rule's own workings rather than on the RRS numbers, because
        race.html and race_pursuit.html use the same 300/240/60 for a different job —
        naming the phase of the countdown ("Warning signal window", "Preparatory
        period"), which is not a decision about what is flying.
        """
        for source_file in FLAG_RENDERERS:
            source = _read(source_file)
            assert "function liveSignalFlags" not in source, source_file.name
            assert "prepUp" not in source, f"{source_file.name} reimplements the P window"

    def test_every_renderer_calls_the_shared_module(self):
        for source_file in FLAG_RENDERERS:
            source = _read(source_file)
            assert "SignalFlags.liveFlags(" in source, source_file.name
            assert "SignalFlags.markup(" in source, source_file.name

    def test_the_pursuit_page_can_show_code_flag_s(self):
        """It could not, and that was a real divergence rather than a tidiness point: a
        shortened pursuit race showed S on the competitor page and nothing on the race
        officer's own. The panel now carries the flag the renderer needs."""
        source = _read(_TEMPLATES / "race_pursuit.html")
        assert "data-course-shortened=" in source
        assert "code-s" in source

    def test_the_pursuit_route_supplies_it(self, client):
        """A data attribute that is always 0 would look fixed and not be."""
        source = (ro.appstate.BASE_DIR / "app.py").read_text(encoding="utf-8")
        block = source.split('"race_pursuit.html"')[0][-1200:]
        assert "course_shortened" in block, "the pursuit render never computes it"

    def test_the_bar_loads_it_before_its_own_script(self):
        """bar_display.js calls SignalFlags at init, so the order matters."""
        source = _read(_TEMPLATES / "bar_display.html")
        # The script tags, not the first mention: the template's own comments name
        # both files, and the first draft of this test compared those instead.
        shared = source.index("filename='signal_flags.js'")
        own = source.index("filename='bar_display.js'")
        assert shared < own

class TestTheClubhouseFlagRow:
    def test_the_page_carries_a_flag_row(self, client):
        html = self._bar(client)
        assert 'class="bar-flags"' in html

    def test_it_sits_before_the_clock_in_the_markup(self, client):
        """The header is a flex row, so document order is what puts the flags to the
        left of the countdown."""
        html = self._bar(client)
        assert html.index('class="bar-flags"') < html.index('class="bar-clock"')

    def test_it_carries_the_start_schedule(self, client):
        html = self._bar(client)
        assert "data-schedule=" in html

    def test_it_carries_the_flag_images(self, client):
        html = self._bar(client)
        for attr in ("data-numeral-base=", "data-prep-src=", "data-code-s-src="):
            assert attr in html, attr

    def test_it_knows_about_a_shortened_course_and_a_finished_race(self, client):
        """Code flag S is up while a shortened course is still being sailed, and no
        flag is up once the race is over."""
        html = self._bar(client)
        assert "data-shortened=" in html
        assert "data-race-finished=" in html

    def _bar(self, client):
        from datetime import datetime, timedelta
        now = datetime.now()
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute(
                    "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
                    " VALUES ('On the telly', 1, ?, 'DUAL', ?)",
                    ((now + timedelta(minutes=1)).isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds")))
                db.commit()
        return client.get("/bar").get_data(as_text=True)


class TestTheLayoutIsRightToLeft:
    """"Adding right to left if there is more than one flag" — the first flag hoisted
    nearest the clock, each later one to its left, the way they go up the mast."""

    def test_the_row_is_reversed(self):
        css = _read(_STATIC / "style.css")
        block = css.split(".bar-flags {")[1].split("}")[0]
        assert "row-reverse" in block

    def test_an_empty_row_takes_no_space(self):
        """The header has a 24px gap between its parts; with no flags up there should
        not be a hole where they would go."""
        css = _read(_STATIC / "style.css")
        assert ".bar-flags-empty { display: none; }" in css

    def test_the_flags_are_sized_for_a_television(self):
        """Not the competitor page's sizes: this is read from across a room."""
        css = _read(_STATIC / "style.css")
        block = css.split(".bar-flags .signal-flag.numeral-flag {")[1].split("}")[0]
        assert "clamp(" in block, "should scale with the screen"

    def test_the_bar_script_renders_through_the_shared_module(self):
        source = _read(_STATIC / "bar_display.js")
        assert "SignalFlags.liveFlags(" in source
        assert "SignalFlags.markup(" in source

    def test_it_only_touches_the_dom_when_the_flags_change(self):
        """This redraws every second on a screen left on all afternoon."""
        source = _read(_STATIC / "bar_display.js")
        assert "flagMarkupShown" in source
