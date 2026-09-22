"""A made-up course sailed more than once round.

The hut's course board is small. A race officer building a two-lap course had to
enter the whole sequence twice, so the board carried six marks where three and a
"x2" would have done.

The count is stored with the marks and **expanded where the course is built**.
The chart, the leg analysis, the rounding walk, the leaderboard and the 3D replay
all read ``marks``, and ``marks`` is the course as it is sailed.

Two things show the short form instead: the board, which is the point of the
exercise, and the spoken VHF announcement, which reads the lap and then "times
two" because that is shorter on the radio and is what the board says.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

import app as ro
from core import courses, raceadmin

SEQ = [{"mark": "1", "rounding": "port"},
       {"mark": "9", "rounding": "port"},
       {"mark": "5", "rounding": "starboard"}]


def _race():
    now = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                         " VALUES ('Laps', 1, ?, ?)", (now, now))
        db.commit()
        return int(cur.lastrowid)


def _saved(race_id):
    with ro.get_db() as db:
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


class TestTheCourseIsExpanded:
    @pytest.mark.parametrize("laps", [1, 2, 3, 9])
    def test_the_sailed_course_is_the_lap_that_many_times(self, client, laps):
        course = courses.course_from_sequence(SEQ, laps=laps)
        assert len(course["marks"]) == len(SEQ) * laps

    def test_the_marks_repeat_in_order(self, client):
        course = courses.course_from_sequence(SEQ, laps=2)
        codes = [m["mark"] for m in course["marks"]]
        assert codes == ["1", "9", "5", "1", "9", "5"]

    def test_the_roundings_repeat_with_them(self, client):
        """A starboard rounding is still starboard on the second lap."""
        course = courses.course_from_sequence(SEQ, laps=2)
        assert [m["rounding"] for m in course["marks"]] == \
            ["port", "port", "starboard"] * 2

    def test_the_length_grows_with_the_laps(self, client):
        one = courses.course_from_sequence(SEQ, laps=1)["length_nm"]
        two = courses.course_from_sequence(SEQ, laps=2)["length_nm"]
        assert two > one, "a two-lap course is not measured as longer than a one-lap one"

    def test_one_lap_is_exactly_what_it_always_was(self, client):
        """Every course in the club's history is a one-lap course. None of them
        may change shape because this exists."""
        plain = courses.course_from_sequence(SEQ)
        assert plain["marks"] == courses.course_from_sequence(SEQ, laps=1)["marks"]
        assert plain["laps"] == 1
        assert "×" not in plain["sequence_text"]


class TestWhatTheBoardShows:
    def test_the_board_carries_one_lap_not_the_expansion(self, client):
        """The whole point: the board is small."""
        course = courses.course_from_sequence(SEQ, laps=3)
        assert len(course["board_marks"]) == len(SEQ)

    def test_and_the_multiplier_is_in_the_text(self, client):
        assert courses.course_from_sequence(SEQ, laps=2)["sequence_text"].endswith("×2")

    def test_the_text_is_the_lap_not_the_whole_thing(self, client):
        text = courses.course_from_sequence(SEQ, laps=2)["sequence_text"]
        assert text.count("1p") == 1, text

    def test_a_single_lap_says_nothing_about_laps(self, client):
        assert "×" not in courses.course_from_sequence(SEQ, laps=1)["sequence_text"]


class TestReopeningTheBuilderDoesNotDoubleTheCourse:
    """The trap in this design, and the reason `lap_marks` exists.

    The builder seeds itself from the course on the race. Seeded from the
    expanded `marks`, a three-mark course saved as x2 would come back as six
    marks, and saving it again as x2 would make twelve -- every time anybody
    opened it to look.
    """

    def test_the_course_keeps_the_lap_it_was_built_from(self, client):
        course = courses.course_from_sequence(SEQ, laps=2)
        assert [m["mark"] for m in course["lap_marks"]] == ["1", "9", "5"]

    def test_the_builder_is_seeded_from_the_lap(self):
        source = (ro.app.root_path and open("routes/race_course.py", encoding="utf-8").read())
        assert 'initial_marks=current_course.get("lap_marks")' in source

    def test_a_saved_course_reloads_at_its_original_length(self, client):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=2)
        course = courses.custom_course_from_race(_saved(race_id))
        assert len(course["lap_marks"]) == 3
        assert len(course["marks"]) == 6
        assert course["laps"] == 2


class TestItSurvivesTheDatabase:
    def test_the_count_is_stored_with_the_marks(self, client):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=3)
        stored = json.loads(_saved(race_id)["custom_course_json"])
        assert stored["laps"] == 3
        assert len(stored["marks"]) == 3, "the stored course should be the lap, not the expansion"

    def test_a_course_saved_before_laps_existed_still_reads(self, client):
        """Every made-up course already in the database has no `laps` key."""
        race_id = _race()
        with ro.get_db() as db:
            db.execute("UPDATE races SET custom_course_json = ?, course_set = 1 WHERE id = ?",
                       (json.dumps({"marks": SEQ}), race_id))
            db.commit()
        course = courses.custom_course_from_race(_saved(race_id))
        assert course["laps"] == 1
        assert len(course["marks"]) == 3


class TestTheCountIsKeptSane:
    @pytest.mark.parametrize("given,expected", [
        (1, 1), (2, 2), ("3", 3), (9, 9),
        (0, 1), (-4, 1), (None, 1), ("", 1), ("lots", 1), (99, courses.MAX_COURSE_LAPS),
    ])
    def test_it_is_clamped_rather_than_refused(self, given, expected):
        """A daft value must not stop a course being saved on a race morning."""
        assert courses.normalise_laps(given) == expected

    def test_a_course_cannot_be_stored_with_a_daft_count(self, client):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=500)
        assert json.loads(_saved(race_id)["custom_course_json"])["laps"] == courses.MAX_COURSE_LAPS


class TestEverythingElseSeesTheWholeCourse:
    """The reason for expanding rather than teaching each consumer about laps."""

    def test_the_spoken_announcement_is_the_one_exception(self, client):
        """Everything else reads the expansion; the VHF does not. Three marks
        read twice is longer on the radio than three marks and "times two", and
        it is not what the board in the hut says either. See
        TestTheAnnouncementSaysTimesTwo below."""
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=2)
        race = _saved(race_id)
        said = courses.course_announcement_text(race, courses.custom_course_from_race(race))
        assert said.lower().count("mark one") == 1, said
        assert said.endswith("Times two.")

    def test_the_rounding_sequence_has_a_place_for_each_lap(self, client):
        """The walk that decides where a boat has got to. It rounds the lap
        twice and then takes the finish mark, so two laps of three marks is
        seven points and not six -- the tail is the finish, not a rounding that
        got duplicated."""
        from core.track import course_rounding_sequence
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=2)
        walk = course_rounding_sequence(_saved(race_id))
        assert [p["code"] for p in walk[:6]] == ["1", "9", "5", "1", "9", "5"]
        assert len(walk) == len(SEQ) * 2 + 1

    def test_and_grows_a_lap_at_a_time(self, client):
        from core.track import course_rounding_sequence
        race_id = _race()
        lengths = []
        for laps in (1, 2, 3):
            with ro.get_db() as db:
                raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=laps)
            lengths.append(len(course_rounding_sequence(_saved(race_id))))
        assert lengths == [4, 7, 10]


class TestTheMultiplierReachesTheBoards:
    """Six pages draw the board from the same course dict. The chip has to be on
    all of them, or the fleet reads a different course depending where it looks.
    """

    def _with_laps(self, laps):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=laps)
        return race_id

    def test_the_race_page_shows_it(self, logged_in_client):
        race_id = self._with_laps(2)
        html = logged_in_client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        assert 'class="mark laps"' in html
        assert "&times;2" in html or "×2" in html

    def test_and_shows_one_lap_of_chips_rather_than_two(self, logged_in_client):
        """The point of the whole thing: the board stays short."""
        race_id = self._with_laps(2)
        html = logged_in_client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        assert html.count('<span class="mark p">1p</span>') <= 2,             "the board is drawing the expanded course, not the lap"

    def test_the_competitor_page_shows_it_too(self, client):
        """Competitors read the same board, and they read it on a phone."""
        race_id = self._with_laps(3)
        html = client.get(f"/public/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        assert 'class="mark laps"' in html, "the public board does not say how many laps"

    def test_a_one_lap_course_grows_no_chip(self, logged_in_client):
        race_id = self._with_laps(1)
        html = logged_in_client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        assert 'class="mark laps"' not in html

    def test_the_chip_has_a_colour_of_its_own(self):
        """Not port red or starboard green: it is not a mark to round."""
        css = open("static/style.css", encoding="utf-8").read()
        assert ".mark.laps {" in css


class TestTheAnnouncementSaysTimesTwo:
    """Reading three marks twice is longer on the radio than reading them once
    and saying how many times round -- and it is not what the board says."""

    def _said(self, laps):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=laps)
        race = _saved(race_id)
        return courses.course_announcement_text(race, courses.custom_course_from_race(race))

    def test_each_mark_is_named_once(self, client):
        said = self._said(2)
        assert said.lower().count("mark one") == 1, said

    def test_and_the_count_follows_them(self, client):
        assert self._said(2).endswith("Times two.")

    def test_in_words_rather_than_digits(self, client):
        """The rest of the sequence spells its numbers because a synthesiser
        starting an utterance on a bare digit is unreliable."""
        said = self._said(3)
        assert said.endswith("Times three.")
        assert "3" not in said

    def test_a_single_lap_says_nothing_about_times(self, client):
        assert "Times" not in self._said(1)

    def test_a_numbered_course_is_untouched(self, client):
        """Fixed courses have no laps and must read exactly as they always did."""
        race_id = _race()
        race = _saved(race_id)
        said = courses.course_announcement_text(race, courses.course_for_race(race))
        assert "Times" not in said


class TestTheBuilderRedrawsWhenTheLapsChange:
    """Reported: changing the laps selector left the chart and the leg analysis
    below showing the one-lap course. The selector was wired to nothing.
    """

    def _page(self, client, laps=1):
        race_id = _race()
        with ro.get_db() as db:
            raceadmin.set_custom_course(db, _saved(race_id), SEQ, laps=laps)
        return client.get(f"/race/{race_id}/course/builder",
                          follow_redirects=True).get_data(as_text=True)

    def test_the_selector_redraws_the_page(self, logged_in_client):
        page = self._page(logged_in_client)
        assert "lapsEl.addEventListener('change'" in page,             "the laps selector still changes nothing"

    def test_the_chart_is_drawn_from_the_sailed_course(self, logged_in_client):
        page = self._page(logged_in_client)
        assert "RaceCourseMap.render(mapEl, sailedMarks())" in page

    def test_and_the_analysis_is_asked_about_the_sailed_course(self, logged_in_client):
        page = self._page(logged_in_client)
        assert "marks: sailedMarks()" in page

    def test_the_saved_form_still_carries_one_lap(self, logged_in_client):
        """The hidden field is the lap, because `course_laps` is posted beside
        it. Posting the expansion *and* the count would square the course."""
        page = self._page(logged_in_client)
        assert "function syncHidden() { hidden.value = JSON.stringify(marks); }" in page

    def test_the_selector_comes_back_on_the_lap_that_was_saved(self, logged_in_client):
        page = self._page(logged_in_client, laps=3)
        block = page.split('name="course_laps"')[1].split("</select>")[0]
        assert 'value="3" selected' in block, block


class TestTheAnalysisItselfChangesWithTheLaps:
    """The wiring is only worth having if the answer differs. This is the
    endpoint the builder asks, given one lap and then two."""

    def _legs(self, client, marks):
        res = client.post("/api/custom_leg_analysis",
                          json={"marks": marks, "use_live": True})
        assert res.status_code == 200, res.status_code
        return res.get_json()

    def test_two_laps_is_analysed_as_more_legs(self, logged_in_client):
        one = self._legs(logged_in_client, SEQ)
        two = self._legs(logged_in_client, SEQ * 2)
        assert one.get("ok") and two.get("ok"), (one, two)
        assert len(two["legs"]) > len(one["legs"]),             "the analysis is the same for one lap and two"


class TestShorteningNamesTheLap:
    """Reported from the shorten-course selector on a two-lap course.

    It listed "7 (rounding 1)", "7 (rounding 2)", "7 (rounding 3)"... which is a
    true description of a twelve-rounding course and no use to somebody holding
    a radio. The race officer is looking at a fleet on its second lap; they are
    not counting roundings since the gun.
    """

    LAP = [{"mark": m, "rounding": "port"} for m in ("7", "F", "O", "4", "7", "O")]

    def _labels(self, laps):
        course = courses.course_from_sequence(self.LAP, laps=laps)
        return [o["label"] for o in courses.course_shorten_options(course)]

    def test_every_option_says_which_lap(self, client):
        assert all("lap " in label for label in self._labels(2))

    def test_a_mark_passed_once_a_lap_needs_no_rounding_number(self, client):
        """"F (lap 2)" is the whole of what there is to say about it."""
        assert "F (lap 2)" in self._labels(2)

    def test_a_mark_passed_twice_a_lap_is_numbered_within_the_lap(self, client):
        labels = self._labels(2)
        assert "7 (lap 1, rounding 1)" in labels
        assert "7 (lap 1, rounding 2)" in labels
        assert "7 (lap 2, rounding 1)" in labels, \
            "the count should restart each lap, not run 1..4 across the race"

    def test_a_one_lap_course_is_worded_exactly_as_before(self, client):
        """Every course the club has ever sailed is a one-lap course."""
        labels = self._labels(1)
        assert not any("lap" in label for label in labels)
        assert labels[:4] == ["7 (rounding 1)", "F", "O (rounding 1)", "4"]

    def test_the_index_still_points_into_the_sailed_course(self, client):
        """The label is what changed. The value behind it has to keep meaning
        the same thing, or shortening calls the wrong mark."""
        course = courses.course_from_sequence(self.LAP, laps=2)
        opts = courses.course_shorten_options(course)
        second_lap_f = next(o for o in opts if o["label"] == "F (lap 2)")
        assert second_lap_f["index"] == 7
        assert course["marks"][second_lap_f["index"]]["mark"] == "F"

    def test_shortening_at_a_second_lap_mark_still_truncates_there(self, client):
        course = courses.course_from_sequence(self.LAP, laps=2)
        opts = courses.course_shorten_options(course)
        at = next(o for o in opts if o["label"] == "F (lap 2)")
        shortened = courses.apply_course_shortening(course, at["index"])
        assert len(shortened["marks"]) == at["index"] + 1
        assert shortened["marks"][-1]["mark"] == "F"
