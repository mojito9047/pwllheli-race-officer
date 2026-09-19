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
    def test_the_shots_that_show_the_fleet_are_all_there(self, plan):
        """A mark rounding, the start, the finish and the overview between them."""
        assert {"overview", "start", "finish"} <= plan["outside"]

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
