"""Things the dashboard was saying twice, and two dialogs laid out too tight.

**The wind card drew its numbers twice.** The analog gauge has the direction and
the speed in the middle of it, large, which is the whole point of an instrument
you read at a glance. Underneath sat three chips repeating the same direction,
the same speed, and a source the status line directly above already tells you.

**Two lines in the Current race box said nothing anyone acts on.** "The public
site root redirects to this race for competitors" and "Public address: /" — true,
and neither is a thing a race officer does anything with on race morning.

**And the SIM dialogs were laid out as tightly as the tracker table they came
from**, which is a full page wide where these are a dialog. `padding: 4px 0` gave
the columns no horizontal gap at all, so in the narrower per-SIM dialog a row
label ran straight into its number: *This billing period6.60$0.20*. Measured
against the real stylesheets, a row was 29px and is now 35px, with 14px between
every pair of columns and 15px between the first column and the table's own
border.

**The two dashboard cards had different heads.** *Current race* was an `h2` in a
`.section-head`; *Wind* was an `h3` in a head of its own — so one was set in the
reading face with a rule drawn by the row, and the other in the condensed
uppercase the theme gives section headings, with a rule that stopped at the end
of the word and sat higher up the card. Side by side, the two rules did not line
up across the page. Both are the same head now, and the head has a floor so the
Weather settings button cannot make one of them taller than the other.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = (_ROOT / "static" / "style.css").read_text(encoding="utf-8")
INDEX = (_ROOT / "templates" / "index.html").read_text(encoding="utf-8")


class TestTheWindCardSaysItOnce:
    def test_the_chip_row_is_gone(self):
        assert "dashboard-wind-meta" not in INDEX

    def test_and_its_styling_went_with_it(self):
        """A rule with no markup left is the kind of thing that survives for
        years because nobody can tell whether it is still load-bearing."""
        assert "dashboard-wind-meta" not in CSS

    def test_the_gauge_is_still_there(self):
        assert "dashboardWindGauge" in INDEX
        assert "partials/wind_gauge.html" in INDEX

    def test_the_status_line_is_still_wired_to_it(self):
        """It is the one thing under the heading that the dial cannot show: what
        the weather station is doing, and whether the wind is being typed in."""
        assert "gauge_status_target='dashboardWindStatus'" in INDEX
        assert 'id="dashboardWindStatus"' in INDEX

    def test_the_dropped_targets_are_not_left_dangling(self):
        """wind_gauge.js writes to each target only if it exists, so a stale
        parameter naming an element that is gone is silent -- which is exactly
        why it is worth asserting."""
        for gone in ("dashboardWindTwdText", "dashboardWindTwsText", "dashboardWindSource"):
            assert gone not in INDEX, gone


class TestTheCurrentRaceBox:
    def test_it_does_not_explain_the_public_root(self):
        assert "public site root redirects" not in INDEX

    def test_it_does_not_print_the_public_address(self):
        assert "Public address" not in INDEX

    def test_what_it_is_for_is_still_there(self):
        """The race, its numbers, the countdown and the way in."""
        assert "dashboardCountdownTime" in INDEX
        assert "Open current race sheet" in INDEX


class TestTheTwoCardHeadsMatch:
    THEME = (_ROOT / "static" / "theme_race_document.css").read_text(encoding="utf-8")

    def test_both_cards_use_the_same_head(self):
        """One was an h2 in a .section-head and the other an h3 in a head of its
        own, which is why they were set in different faces at different sizes
        with rules at different heights."""
        assert 'class="dashboard-wind-head"' not in INDEX
        assert "<h3>Wind</h3>" not in INDEX
        assert "<h2>Wind</h2>" in INDEX
        assert "<h2>Current race</h2>" in INDEX

    def test_the_stray_h2_bottom_margin_is_reset(self):
        """style.css sets `h2 { margin-top: 0 }` and stops there, so the
        browser's 0.83em BOTTOM margin stayed -- 16.6px measured, sitting inside
        a flex row and pushing that row's rule down the card. The theme already
        did this for h3; h2 was missed."""
        assert ".section-head h2, .section-head > div > h2 { margin-bottom: 0; }" in self.THEME

    def test_a_subtitle_under_a_heading_still_has_room(self):
        """The reset is app-wide, and several heads are `<h2>` followed by a
        paragraph. Swept across the dashboard, Races, Series, Boats, Marks,
        Trackers, Settings and a race sheet: every such paragraph still clears
        its heading by 15px, from its own top margin. No rule needed, but the
        sweep is the thing worth recording."""
        assert "h2 + p" not in self.THEME.split(".section-head h2")[1][:200]

    def test_no_min_height_crutch(self):
        """With the margin gone the heading governs both cards on its own -- 31px
        against a 30px button -- so a floor would only put back the air that was
        just removed."""
        block = CSS.split(".dashboard-wind-card > .section-head")[1].split("}")[0]
        assert "min-height" not in block


class TestTheCourseBoardOnTheDashboard:
    def test_the_board_is_rendered_between_the_clock_and_the_button(self):
        board = INDEX.index("dashboard-course-board")
        clock = INDEX.index("dashboardCountdownTime")
        button = INDEX.index("Open current race sheet")
        assert clock < board < button

    def test_it_is_only_drawn_when_there_are_marks(self):
        """Empty until a course is chosen: the server sends board_marks rather
        than a course number, because a new race always has a number and that is
        not the same as a decision."""
        assert "{% if current_race_status.board_marks %}" in INDEX

    def test_a_shortened_course_says_so(self):
        assert "current_race_status.course_shortened" in INDEX


class TestWhatTheServerSendsTheBoard:
    """The markup above only draws what it is given; this is the deciding."""

    @staticmethod
    def _race(course_set=1, shorten=None):
        from core.db import get_db
        from datetime import datetime, timedelta
        now = datetime.now().isoformat(timespec="seconds")
        when = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, course_no, course_set, start_time, notes,"
                " shortened_at_index, created_at) VALUES ('Club Race',1,?,?,'',?,?)",
                (course_set, when, shorten, now))
            rid = int(cur.lastrowid)
            db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status)"
                       " VALUES (?,'Mojito','GBR1','RACING')", (rid,))
            db.commit()
        return rid

    def _status(self):
        import app as ro
        with ro.app.test_request_context("/admin"):
            return ro.dashboard_current_race_status()

    def test_a_chosen_course_sends_its_marks(self, client):
        self._race(course_set=1)
        assert self._status()["board_marks"], "no marks for a race with a course"

    def test_a_course_nobody_chose_sends_none(self, client):
        """The v0.276 rule, which this new board has to follow too or the
        dashboard becomes the one place still showing the fallback."""
        self._race(course_set=0)
        assert self._status()["board_marks"] == []

    def test_a_shortened_course_is_marked_and_truncated(self, client):
        self._race(course_set=1, shorten=1)
        st = self._status()
        assert st["course_shortened"] is True
        assert len(st["board_marks"]) <= 2, st["board_marks"]

    def test_an_unshortened_one_is_not(self, client):
        self._race(course_set=1)
        assert self._status()["course_shortened"] is False


class TestTheSimDialogsHaveRoom:
    def test_the_columns_have_a_gap(self):
        """The reported fault, and the one that made text collide rather than
        merely look cramped."""
        assert ".sim-usage th, .sim-usage td { padding: 7px 14px;" in CSS

    def test_the_outer_cells_are_padded_too(self):
        """The first attempt zeroed these, to keep the rules running the full
        width of the table. They do that anyway -- a cell's border-bottom spans
        its whole box and padding does not shorten it -- so all the zeroing did
        was press the first column against the table's own 1px border. Measured
        after: the text sits 15px in (1px border + 14px padding), and the header
        rule still spans 441 of the table's 442px."""
        block = CSS.split(".sim-usage th:first-child")[1][:140]
        assert "padding-left: 0" not in block
        assert "padding-right: 0" not in CSS.split(".sim-usage th, .sim-usage td")[1][:200]

    def test_the_balance_list_has_room_too(self):
        assert ".sim-detail" in CSS
        block = CSS.split(".sim-detail {")[1].split("}")[0]
        assert "gap: 7px 18px" in block

    def test_no_zero_padding_row_rule_survives(self):
        """The old rule and the new one both match `.sim-usage th, .sim-usage
        td`, so a leftover would win or lose on order alone."""
        assert "padding: 4px 0;" not in CSS.split(".sim-usage")[1][:400]
