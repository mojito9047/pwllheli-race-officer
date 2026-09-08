"""Tests for adding/removing racing marks (core/marks.py).

Runs against a temp data dir so the real data/marks.json is never touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import appstate, marks as core_marks  # noqa: E402


@pytest.fixture()
def marks_env(tmp_path, monkeypatch):
    marks = {
        "O": {"name": "ODM", "lat": 52.87, "lon": -4.39},
        "1": {"name": "One", "lat": 52.8, "lon": -4.4},
        "Y": {"name": "Y compound", "compound": True, "components": ["Ya", "Yb"]},
        "Ya": {"name": "Ya", "component_of": "Y"},
        "5": {"name": "Five", "lat": 52.7, "lon": -4.5},  # unused -> deletable
    }
    (tmp_path / "marks.json").write_text(json.dumps({"marks": marks}), encoding="utf-8")
    monkeypatch.setattr(appstate, "DATA_DIR", tmp_path)
    monkeypatch.setattr(appstate, "MARKS", marks)
    monkeypatch.setattr(appstate, "COURSES", [
        {"course_no": 1, "marks": [{"mark": "1", "rounding": "port"},
                                   {"mark": "O", "rounding": "starboard"}]},
    ])
    monkeypatch.setattr(appstate, "START_FINISH", {
        "start_line": {"seaward_end_mark": "O"},
        "finish_line": {"seaward_end_mark": "O"},
    })
    return tmp_path


def test_coordinate_text_formatting():
    assert core_marks.format_lat_text(52.8791166667).startswith("52° 52.7")
    assert core_marks.format_lat_text(52.8791166667).endswith("N")
    assert core_marks.format_lat_text(-1.5).endswith("S")
    assert core_marks.format_lon_text(-4.3993333333).endswith("W")
    assert core_marks.format_lon_text(4.0).endswith("E")


def test_validate_new_mark(marks_env):
    assert core_marks.validate_new_mark("11", "New", 52.5, -4.5)[0]
    assert not core_marks.validate_new_mark("1", "dup", 52.5, -4.5)[0]        # duplicate
    assert not core_marks.validate_new_mark("bad code", "x", 52.5, -4.5)[0]   # bad code
    assert not core_marks.validate_new_mark("11", "", 52.5, -4.5)[0]          # no name
    assert not core_marks.validate_new_mark("11", "x", "abc", -4.5)[0]        # non-numeric
    assert not core_marks.validate_new_mark("11", "x", 200, -4.5)[0]          # out of range


def test_add_mark_persists(marks_env):
    ok, code = core_marks.add_mark("11", "New Mark", 52.5, -4.5, "Barrel", "Sphere")
    assert ok and code == "11"
    data = json.loads((marks_env / "marks.json").read_text(encoding="utf-8"))
    m = data["marks"]["11"]
    assert m["name"] == "New Mark"
    assert m["buoy"] == "Barrel" and m["top_mark"] == "Sphere"
    assert m["lat"] == 52.5 and m["lon"] == -4.5
    assert m["lat_text"].endswith("N") and m["lon_text"].endswith("W")


def test_delete_blocks(marks_env):
    assert core_marks.mark_delete_block("1")    # used in a course
    assert core_marks.mark_delete_block("O")    # start/finish seaward mark
    assert core_marks.mark_delete_block("Y")    # compound
    assert core_marks.mark_delete_block("Ya")   # component of a compound
    assert core_marks.mark_delete_block("5") is None  # safe to delete


def test_delete_mark(marks_env):
    ok, _ = core_marks.delete_mark("5")
    assert ok
    data = json.loads((marks_env / "marks.json").read_text(encoding="utf-8"))
    assert "5" not in data["marks"]
    ok, reason = core_marks.delete_mark("1")
    assert not ok and "course" in reason.lower()


def test_endpoints_are_admin_gated():
    import app as ro
    assert "marks_add" in ro.ADMIN_ONLY_ENDPOINTS
    assert "mark_delete" in ro.ADMIN_ONLY_ENDPOINTS
