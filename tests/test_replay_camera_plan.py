"""The film's shot list, and the one shot that was taken out of it.

The replay cuts between a keyframed overview, a camera planted beside each mark
the leader rounds, the start line, and the finish line. It also used to cut to a
**chase** camera -- locked behind the leader -- for 150 seconds after every mark
rounding: five times and 14% of an eighty-minute club race.

That is the wrong shot for a race. It holds on the leader's transom while the
boats it is racing are behind the camera, and at 30x there is nothing to see in
the wake. Following the leader earns its place at a mark rounding, where the
rest of the fleet is arriving into the same frame, and the mark cameras already
do that. So the film keeps those and drops the chase, which survives only as
``--shots follow`` -- an option that asks for one boat by name.

Read from the source rather than by building a scene: ``build_scene`` imports
bpy, bmesh and mathutils, so it cannot be imported without Blender, and a
render is an hour. What is worth holding is the shape of the plan, and that is
visible here.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "replay3d" / "build_scene.py"
_TREE = ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _function(name):
    for node in ast.walk(_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is gone from build_scene.py")


def _follow_branch(fn):
    """The ``if shots == "follow":`` body, which is the one place chase belongs."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        left, ops, rights = node.test.left, node.test.ops, node.test.comparators
        if (isinstance(left, ast.Name) and left.id == "shots"
                and isinstance(ops[0], ast.Eq)
                and isinstance(rights[0], ast.Constant) and rights[0].value == "follow"):
            return node
    raise AssertionError('build_cameras() no longer has an `if shots == "follow"` branch')


def _shot_dicts(root):
    """Every ``{"name": ..., "camera": ...}`` shot literal under this node."""
    out = []
    for node in ast.walk(root):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "name"
                    and isinstance(value, ast.Constant)):
                out.append((node, value.value))
    return out


@pytest.fixture(scope="module")
def plan():
    """Shot names inside the follow branch and outside it.

    Split by node identity, not by string: the ``if leader_root is not None``
    statement *contains* the follow branch, so anything that walks it from the
    top collects the chase again and the test passes whatever the code says.
    """
    fn = _function("build_cameras")
    follow = _follow_branch(fn)
    # Its *body* only. An ast.If carries the `else:` too, and here that else is
    # the entire film plan -- walking the If node collects every shot in the
    # function and the test then passes no matter what the code does.
    within = {id(node) for stmt in follow.body for node, _name in _shot_dicts(stmt)}
    inside, outside = set(), set()
    for node, name in _shot_dicts(fn):
        (inside if id(node) in within else outside).add(name)
    return {"fn": fn, "follow": follow, "follow_body": follow.body,
            "inside": inside, "outside": outside}


class TestTheFilmDoesNotFollowTheLeader:
    def test_no_shot_in_the_film_plan_is_the_chase(self, plan):
        """The change itself. Cutting to it after every rounding lost the fleet."""
        assert "chase" not in plan["outside"], (
            "the film plan names the chase camera again; it holds on the leader's "
            "transom while the race happens off-camera")

    def test_the_follow_option_still_has_it(self, plan):
        """--shots follow asks for one boat, and that is a fair thing to ask for."""
        assert "chase" in plan["inside"]

    def test_the_chase_camera_is_not_even_built_for_a_film(self, plan):
        """It keyframes an anchor once per track sample -- thousands, unused.

        Cheap to get wrong: leaving the construction behind costs every render
        that work and leaves a dead camera in the scene for anyone reading it.
        """
        built_in_follow = False
        for node in (n for stmt in plan["follow_body"] for n in ast.walk(stmt)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_new_camera"
                    and any(isinstance(a, ast.Constant) and a.value == "chase" for a in node.args)):
                built_in_follow = True
        assert built_in_follow, "the chase camera is built outside the follow branch"

        follow_ids = {id(n) for stmt in plan["follow_body"] for n in ast.walk(stmt)}
        for node in ast.walk(plan["fn"]):
            if id(node) in follow_ids:
                continue
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_new_camera"
                    and any(isinstance(a, ast.Constant) and a.value == "chase" for a in node.args)):
                raise AssertionError("a chase camera is still built for the film")


class TestWhatTheFilmStillCutsTo:
    def test_the_shots_it_still_names_for_itself(self, plan):
        """The opening wide shot, the start line, and the cuts back to the fleet."""
        assert {"overview", "start"} <= plan["outside"]

    def test_the_finish_is_planned_by_the_clock_module(self):
        """Its timing depends on how long a gap *lasts on screen*, which is the
        film clock's business and is testable without Blender -- see
        tests/test_finish_shots.py. build_cameras owns the cameras, not the cut
        times, so what is checked here is only that it still asks."""
        source = _SOURCE.read_text(encoding="utf-8")
        assert "from replay_time import TimeWarp, finish_shot_times" in source
        assert "finish_shot_times(times, frame_of)" in source

    def test_every_finisher_gets_a_camera_on_its_own_run_in(self):
        """One camera framed on the leader left the fourth boat out of shot."""
        source = _SOURCE.read_text(encoding="utf-8")
        assert "def _finish_camera(" in source
        assert "_finish_camera(scene, coll, data, line, b, extent, n)" in source

    def test_the_finish_cameras_are_numbered_in_finishing_order(self):
        """So "finish 1" is the first boat home, not whichever the export
        listed first -- the shot list is read by people."""
        source = _SOURCE.read_text(encoding="utf-8")

    def test_the_mark_cameras_are_named_per_rounding(self):
        """`mark 6 #2` -- the same mark rounded twice is two shots, not one."""
        source = _SOURCE.read_text(encoding="utf-8")
        assert 'f"mark {point.get(\'display\', point[\'mark\'])} #{n}"' in source

    def test_leaving_a_mark_cuts_away_from_its_camera(self, plan):
        """The mark camera is planted beside the mark and the fleet sails out of it.

        The chase used to be this cut. Something still has to be, or the film
        holds on an empty patch of water for the length of a leg.
        """
        fn_source = ast.get_source_segment(_SOURCE.read_text(encoding="utf-8"), plan["fn"]) or ""
        after_mark = fn_source.split('"t0": t_round - cut_in')[1]
        assert '"name": "overview"' in after_mark.split("if not any")[0], \
            "nothing cuts away from the mark camera once the leader is past it"


class TestTheFinishIsShotDownTheBoatsOwnTrack:
    """Square to the line put the boat in the corner of the frame.

    The line is a fixed 350 m of water and boats finish at whichever end suits
    them, so a camera on the line's perpendicular aimed at its middle framed
    350 m of rope: SGRECH BACH came in half out of the right-hand edge. The
    camera now stands beyond the line on the extension of the boat's own track
    and looks back down it, so the boat sails at the lens.

    Read from the source: build_scene needs Blender. The framing itself was
    checked by rendering race 90's four finishes and looking at them.
    """

    SRC = _SOURCE.read_text(encoding="utf-8")

    def _body(self):
        fn = _function("_finish_camera")
        return ast.get_source_segment(self.SRC, fn) or ""

    def test_there_is_a_finish_camera_per_boat(self):
        assert "def _finish_camera(" in self.SRC

    def test_it_aims_at_where_this_boat_crosses(self):
        body = self._body()
        assert "at_line" in body
        assert "focus.location = at_line" in body

    def test_it_stands_down_the_boats_own_heading(self):
        """Not on the line's perpendicular, which is what put it in the corner."""
        body = self._body()
        assert "heading" in body
        assert "cam_pos = at_line + u * d" in body

    def test_the_heading_comes_from_a_fix_far_enough_back(self):
        """SGRECH BACH reported 176 times in ninety minutes, so her last two
        minutes can be one fix and a windowed heading is a direction of
        nothing -- which dropped her back to the square-on shot."""
        body = self._body()
        assert "HEADING_FROM_M" in body
        assert "for s in reversed(live)" in body

    def test_a_boat_with_no_usable_track_falls_back_rather_than_failing(self):
        body = self._body()
        assert body.count("_line_camera(") >= 2, "no square-on fallback left"

    def test_low_land_beyond_the_line_is_stood_on_not_avoided(self):
        """Following the boat's track can put the camera on the beach -- it does
        for CRACKAJACK on race 90 -- and dropping the shot there would put the
        camera behind the fleet again."""
        body = self._body()
        assert "LAND_CAMERA_CLEARANCE_M" in body
        assert "LAND_CAMERA_MAX_M" in body

    def test_a_hill_is_still_too_much_to_stand_on(self):
        body = self._body()
        assert "if ground > LAND_CAMERA_MAX_M:" in body


class TestWhatIsDrawnOnTheWater:
    """Two things reported from watching a finished film.

    The course path is every leg the fleet has sailed, drawn as a tube through
    the marks. It belongs on the race and not on the finish: from a camera a
    couple of hundred metres from the line those legs radiate out of the mark
    and cross the frame around the boat that is finishing.

    And the line itself said nothing about whether the race had started. It is
    red now until the gun and green on it -- and back to red once the fleet is
    away, because at this club the same line is the finish line and green
    afterwards would say it was still open.
    """

    SRC = _SOURCE.read_text(encoding="utf-8")

    def _body(self):
        return ast.get_source_segment(self.SRC, _function("build_course")) or ""

    def test_the_course_legs_come_off_for_the_finish(self):
        body = self._body()
        assert "_step_visibility(" in body
        assert 'n.startswith("finish")' in body

    def test_they_are_still_there_for_the_race(self):
        """Hidden per shot, not switched off for the whole film: the legs are
        what makes the overview readable while the race is being sailed."""
        fn = ast.get_source_segment(self.SRC, _function("_step_visibility")) or ""
        assert "for shot in sorted(shots" in fn, "visibility is not decided per shot"
        assert "hide_on(" in fn

    def test_hiding_steps_rather_than_fades(self):
        fn = ast.get_source_segment(self.SRC, _function("_step_visibility")) or ""
        assert '"CONSTANT"' in fn and 'only_prefix="hide_"' in fn

    def test_the_line_is_red_before_the_gun_and_green_on_it(self):
        body = self._body()
        assert "LINE_SHUT" in body and "LINE_OPEN" in body
        assert "frame_of(first_start)" in body

    def test_and_red_again_once_the_fleet_is_away(self):
        """Only where the start and the finish are the same line, which is the
        club's normal case; a race with two lines keeps its start line green."""
        body = self._body()
        assert "frame_of(first_start + START_CUT_OUT)" in body
        assert "if not (start and not same):" in body

    def test_a_separate_start_line_is_drawn_when_there_is_one(self):
        """An ISORA passage race does not start and finish on the same line,
        and only the finish line was ever drawn."""
        body = self._body()
        assert '"Start line"' in body

    def test_the_colour_change_steps_rather_than_fades(self):
        fn = ast.get_source_segment(self.SRC, _function("_step_colour")) or ""
        assert '"CONSTANT"' in fn
        assert "_fcurves(mat.node_tree)" in fn, "Blender 5 actions have no .fcurves"
