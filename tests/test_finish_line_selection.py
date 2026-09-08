"""A race chooses which finish line it is sailed to.

Pwllheli's usual line is the ODM (mark O) to the surveyed bridge window: 347 m
long, and start and finish are geographically the same. An ISORA passage race is
not sailed to it. Its sailing instruction 15 gives the finish as the transit
between the Pwllheli Fairway Buoy and the bridge at Plas Heli — a line 1.6 km
long whose shore end is 787 m from the club one, and which the instructions go
out of their way to say is *not* the usual line.

Finishing boats on the wrong line does not fail loudly. The crossing test would
simply never fire, and every boat would sit unfinished at the end of a race that
had plainly finished.

The two positions in the sailing instruction check out against each other: they
bear 292.7 degrees true apart, and the instruction says 297 magnetic, which with
local variation is about 294.5 true.
"""
from __future__ import annotations

import time

import pytest

import app as ro
import core.track as track
from core.timeutils import bearing_deg, haversine_nm


NOW = time.time()
STAMP = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 7200))


def make_race(finish_line_key=None):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at,"
                             " finish_line_key) VALUES (1, 'ISORA', 1, ?, ?, ?)",
                             (STAMP, STAMP, finish_line_key))
            db.commit()
            return int(cur.lastrowid)


def race(race_id):
    with ro.app.app_context():
        return ro.get_race(race_id)


class TestTheLinesAreConfigured:
    def test_both_lines_exist(self, client):
        keys = {line["key"] for line in track.finish_lines()}
        assert {"psc", "isora_plas_heli"} <= keys

    def test_the_club_line_is_the_default(self, client):
        assert track.default_finish_line_key() == "psc"

    def test_the_seaward_end_is_mark_f(self, client):
        """The Fairway buoy *is* mark F, so the line follows it when F is pinged
        from the water rather than being frozen at the position in the SI."""
        line = track.finish_line_by_key("isora_plas_heli")
        assert (line["seaward"] or {}).get("mark") == "F"

    def test_the_shore_end_is_the_position_from_the_instruction(self, client):
        """SI 15.3. Not a mark: it is a building."""
        shore = track.finish_line_by_key("isora_plas_heli")["shore"]
        assert shore["lat"] == pytest.approx(52 + 53.336 / 60, abs=1e-6)
        assert shore["lon"] == pytest.approx(-(4 + 24.230 / 60), abs=1e-6)

    def test_it_lies_on_the_bearing_the_instruction_gives(self, client):
        """297 magnetic is about 294.5 true here, from mark F as it is held."""
        pts = track.finish_line_points(line_key="isora_plas_heli")
        brg = bearing_deg(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
        assert brg == pytest.approx(297, abs=6), f"{brg:.1f} true against 297 magnetic"

    def test_re_measuring_the_fairway_buoy_moves_the_line(self, client):
        """The whole reason for using the mark rather than a fixed position."""
        moved = {"F": {"name": "Fairway", "lat": 52.885, "lon": -4.379}}
        assert track.finish_line_points(moved, "isora_plas_heli")[0] == (52.885, -4.379)

    def test_it_really_is_a_different_line(self, client):
        """Worth stating: it is not a tweak to the club line but another one."""
        psc = track.finish_line_points(line_key="psc")
        isora = track.finish_line_points(line_key="isora_plas_heli")
        assert haversine_nm(*psc[1], *isora[1]) * 1852 > 700     # shore ends apart
        assert haversine_nm(*isora[0], *isora[1]) * 1852 > 1500  # and much longer


class TestResolvingALineForARace:
    def test_a_race_with_nothing_chosen_uses_the_club_line(self, client):
        assert track.race_finish_line_key(race(make_race(None))) == "psc"

    def test_a_race_can_be_set_to_the_isora_line(self, client):
        assert track.race_finish_line_key(race(make_race("isora_plas_heli"))) == "isora_plas_heli"

    def test_an_unknown_key_falls_back_rather_than_leaving_no_line(self, client):
        """A race with no finish line at all could never finish a boat."""
        points = track.finish_line_points(line_key="nonsense")
        assert points == track.finish_line_points(line_key="psc")

    def test_the_club_line_still_follows_a_re_measured_odm(self, client, monkeypatch):
        """Its seaward end is a mark, so correcting O moves the line with it."""
        moved = {"O": {"name": "ODM", "lat": 52.9, "lon": -4.42}}
        assert track.finish_line_points(moved, "psc")[0] == (52.9, -4.42)

    def test_the_isora_line_does_not_care_where_the_odm_is(self, client):
        """It is a different line entirely; O is not one of its ends."""
        moved = {"O": {"name": "ODM", "lat": 52.9, "lon": -4.42},
                 "F": {"name": "Fairway", "lat": 52.8833, "lon": -4.3833}}
        pts = track.finish_line_points(moved, "isora_plas_heli")
        assert pts[0] == (52.8833, -4.3833)

    def test_a_races_points_come_from_its_own_key(self, client):
        isora = race(make_race("isora_plas_heli"))
        assert track.race_finish_line_points(isora) == track.finish_line_points(
            track.race_marks(isora), "isora_plas_heli")


class TestTheChartIsToldWhichLine:
    def test_the_club_line_leaves_the_chart_to_its_own_construction(self, client):
        """Blank, so the chart keeps looking O up as a mark and follows it."""
        data = track.race_chart_line(race(make_race(None)))
        assert data["line_seaward_lat"] == ""

    def test_the_isora_line_is_handed_over_outright(self, client):
        """Neither of its ends is a mark on the course, so the chart cannot
        look them up."""
        data = track.race_chart_line(race(make_race("isora_plas_heli")))
        assert data["line_seaward_lat"] == pytest.approx(52 + 53.000 / 60, abs=1e-6)
        assert data["line_shore_lat"] == pytest.approx(52 + 53.336 / 60, abs=1e-6)
        assert "Fairway" in data["line_seaward_label"]
        assert "Plas Heli" in data["line_shore_label"]


class TestThePage:
    def test_the_course_tab_offers_the_choice(self, logged_in_client):
        race_id = make_race(None)
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'name="finish_line_key"' in html
        assert "ISORA" in html

    def test_choosing_it_is_saved(self, logged_in_client, csrf_post):
        race_id = make_race(None)
        csrf_post(f"/race/{race_id}/update", {"name": "ISORA", "course_no": "1",
                                              "finish_line_key": "isora_plas_heli"})
        assert track.race_finish_line_key(race(race_id)) == "isora_plas_heli"

    def test_and_can_be_set_back(self, logged_in_client, csrf_post):
        race_id = make_race("isora_plas_heli")
        csrf_post(f"/race/{race_id}/update", {"name": "ISORA", "course_no": "1",
                                              "finish_line_key": "psc"})
        assert track.race_finish_line_key(race(race_id)) == "psc"

    def test_a_junk_key_is_not_stored(self, logged_in_client, csrf_post):
        race_id = make_race(None)
        csrf_post(f"/race/{race_id}/update", {"name": "ISORA", "course_no": "1",
                                              "finish_line_key": "../../etc"})
        assert track.race_finish_line_key(race(race_id)) == "psc"


class TestTheLastMarkIsCrossedNotRounded:
    """Boats do not round the finish-line mark; they cross the line.

    The walk never asks for the *last* element of the sequence to be rounded — it
    is reached by crossing the line — so a course that already ends at the finish
    line's own mark must leave that mark as the last element rather than adding
    another one behind it. Getting this wrong leaves every boat unfinished: they
    would have to pass within the rounding radius of the buoy before the line
    crossing counted, which is not what they sail.
    """

    def sequence(self, race_id, marks, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda r: {
            "marks": [{"mark": m, "rounding": "port"} for m in marks]})
        return [s["code"] for s in track.course_rounding_sequence(race(race_id))]

    def test_a_club_course_ending_at_o_is_not_given_another_o(self, client, monkeypatch):
        seq = self.sequence(make_race(None), ["1", "2", "O"], monkeypatch)
        assert seq == ["1", "2", "O"]

    def test_a_club_course_ending_elsewhere_still_reaches_the_line(self, client,
                                                                   monkeypatch):
        """A shortened course ends at the shorten mark and must still sail to O."""
        seq = self.sequence(make_race(None), ["1", "2"], monkeypatch)
        assert seq == ["1", "2", "O"]

    def test_an_isora_course_ending_at_f_is_not_given_another_mark(self, client,
                                                                   monkeypatch):
        """The reported case: F is the last mark and is crossed, not rounded."""
        seq = self.sequence(make_race("isora_plas_heli"), ["1", "2", "F"], monkeypatch)
        assert seq == ["1", "2", "F"]

    def test_an_isora_course_reaches_f_and_not_o(self, client, monkeypatch):
        """The line to sail to is the one the race finishes on."""
        seq = self.sequence(make_race("isora_plas_heli"), ["1", "2"], monkeypatch)
        assert seq == ["1", "2", "F"]

    def test_an_isora_course_still_rounds_o_when_it_is_mid_course(self, client,
                                                                  monkeypatch):
        """O is an ordinary rounding mark on a course that does not finish there."""
        seq = self.sequence(make_race("isora_plas_heli"), ["O", "2"], monkeypatch)
        assert seq == ["O", "2", "F"]

    def test_the_finish_element_sits_on_the_line(self, client, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda r: {
            "marks": [{"mark": "1", "rounding": "port"}]})
        r = race(make_race("isora_plas_heli"))
        seq = track.course_rounding_sequence(r)
        seaward = track.race_finish_line_points(r)[0]
        assert (seq[-1]["lat"], seq[-1]["lon"]) == seaward
