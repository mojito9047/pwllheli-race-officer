"""What the film's title and results cards say about the race.

"Race 4" says nothing a season later, and these films are kept and shared. The
series is what puts a race in a year and against a trophy, so both cards carry
it: on the title card with the day and the course, under the rule; on the
results card under the race name, above the day.

Two things make it awkward enough to be worth testing. Club series names are
long -- "Autumn Series - Pwllheli Challenge Cup 2026" is 42 characters -- and
they vary from club to club, so a size that suits one runs off the side of the
card for the next. And a race need not belong to a series at all, in which case
the cards must look exactly as they did before this was added.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPLAY3D = Path(__file__).resolve().parents[1] / "scripts" / "replay3d"
sys.path.insert(0, str(_REPLAY3D))

cards = pytest.importorskip("cards", reason="the card drawer needs PIL")
PIL = pytest.importorskip("PIL", reason="the card drawer needs PIL")

SERIES = "Autumn Series - Pwllheli Challenge Cup 2026"


def _scene(series=None, name="Race 4"):
    race = {"id": 90, "name": name, "course_no": 65, "course_text": "1p 9p 5p Op",
            "first_start_iso": "2026-09-20T14:15:00"}
    if series is not None:
        race["series"] = series
    return {
        "race": race,
        "wind": {"twd_deg": 300.1, "tws_kn": 5.2},
        "results": [{"title": "IRC Overall results", "rating_label": "IRC TCC", "rows": [
            {"pos": 1, "boat": "MOJITO", "sail_no": "GBR4822R", "status": "FINISHED",
             "elapsed": "1:05:18", "corrected": "1:10:47", "corrected_s": 4247.0,
             "elapsed_s": 3918.0, "colour": [217, 26, 26]},
            {"pos": 2, "boat": "FINALLY", "sail_no": "GBR6939R", "status": "FINISHED",
             "elapsed": "1:26:25", "corrected": "1:28:04", "corrected_s": 5284.0,
             "elapsed_s": 5185.0, "colour": [26, 89, 217]},
        ]}],
    }


def _draw(tmp_path, which, series, name="Race 4"):
    fn = cards.title_card if which == "title" else cards.results_card
    out = str(tmp_path / f"{which}_{series and 'with' or 'without'}.png")
    return fn(_scene(series, name), out, str(tmp_path / "no-fonts"), [], size=(960, 540))


def _pixels(path):
    from PIL import Image
    with Image.open(path) as img:
        return list(img.convert("RGB").getdata())


class TestTheSeriesIsOnBothCards:
    @pytest.mark.parametrize("which", ["title", "results"])
    def test_adding_it_changes_the_card(self, tmp_path, which):
        """Drawn, not merely accepted and dropped on the floor."""
        without = _pixels(_draw(tmp_path, which, None))
        with_series = _pixels(_draw(tmp_path, which, SERIES))
        assert without != with_series, f"the {which} card looks identical with a series on it"

    @pytest.mark.parametrize("which", ["title", "results"])
    def test_a_race_with_no_series_is_untouched(self, tmp_path, which):
        """Most of the club's races are in a series, but not all, and a one-off
        must not gain a blank line or lose its spacing."""
        missing = _pixels(_draw(tmp_path, which, None))
        empty = _pixels(_draw(tmp_path, which, ""))
        assert missing == empty

    @pytest.mark.parametrize("which", ["title", "results"])
    def test_a_very_long_series_name_does_not_break_the_card(self, tmp_path, which):
        """It should shrink, not raise and not spill."""
        assert _draw(tmp_path, which, "Something " * 20)


class TestALineIsShrunkToFitRatherThanRunningOff:
    """The title card sets the series with the day and the course, and any of
    the three can be long -- a course with a dozen marks, or a series named
    after a cup and a season and a year.
    """

    def _width(self, text, size, room=10_000):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1920, 200))
        draw = ImageDraw.Draw(img)
        used = cards._centred_to_fit(draw, text, "no-fonts", 1920, 100,
                                     start=size, room=room)
        return used

    def test_a_line_that_fits_keeps_its_size(self):
        assert self._width("RACE 4", 32) == 32

    def test_a_line_that_does_not_fit_is_made_smaller(self):
        wide = self._width("A" * 200, 32, room=300)
        assert wide < 32

    def test_it_stops_shrinking_rather_than_vanishing(self):
        """A pathological name must not loop to a zero-sized font."""
        assert self._width("A" * 5000, 32, room=50) >= 12


class TestAMadeUpCourseIsNotGivenANumber:
    """Reported from a finished film: the title card said "COURSE 1" for a race
    sailed round a course somebody built on the day.

    Setting a made-up course leaves ``course_no`` at whatever it happened to be
    -- 1, on the race this came from -- so the number names a club course that
    nobody sailed, next to a mark sequence that is not that course's. The app
    says "made up course" on the race page, the competitor page and the bar
    display, and the film now says the same.
    """

    def _line(self, tmp_path, **race):
        """The course line as it would be drawn, via the same formatting."""
        scene = _scene(None)
        scene["race"].update(race)
        drawn = []

        real = cards._centred_to_fit
        cards._centred_to_fit = lambda draw, text, *a, **k: (drawn.append(text), 32)[1]
        try:
            cards.title_card(scene, str(tmp_path / "t.png"), str(tmp_path / "nf"), [],
                             size=(960, 540))
        finally:
            cards._centred_to_fit = real
        return drawn

    def test_a_made_up_course_says_so(self, tmp_path):
        lines = self._line(tmp_path, course_is_custom=True, course_no=1,
                           course_text="2p 6s 2p 4p 6p Op")
        assert any(line.startswith("MADE UP COURSE") for line in lines), lines

    def test_and_does_not_name_a_number(self, tmp_path):
        lines = self._line(tmp_path, course_is_custom=True, course_no=1,
                           course_text="2p 6s 2p 4p 6p Op")
        assert not any("COURSE 1" in line for line in lines), lines

    def test_a_real_course_keeps_its_number(self, tmp_path):
        lines = self._line(tmp_path, course_is_custom=False, course_no=65,
                           course_text="1p 9p 5p Op")
        assert any("COURSE 65" in line for line in lines), lines

    def test_the_marks_are_shown_either_way(self, tmp_path):
        """The sequence is the part that says what was actually sailed."""
        for custom in (True, False):
            lines = self._line(tmp_path, course_is_custom=custom, course_no=1,
                               course_text="2p 6s 2p 4p 6p Op")
            assert any("2P 6S 2P 4P 6P OP" in line for line in lines), (custom, lines)
