"""A club with no fixed courses can make a race.

Liverpool Yacht Club sets every course on the day, so its courses.json is empty.
A new race is still given course number 1, and looking that up raised KeyError:
the race was on the list, but its sheet and every public page answered 500
while it was the current race.
"""
from __future__ import annotations

import re

import pytest

import app as ro
from core import appstate, courses
from core.races import get_race

TOKEN = "test-csrf-token"


@pytest.fixture()
def no_fixed_courses(monkeypatch):
    # appstate only. app.COURSES is forwarded to it (PEP 562 __getattr__), and
    # patching it too recorded the already-emptied list as the value to put back,
    # which left app.COURSES == [] for every test that ran after these.
    monkeypatch.setattr(appstate, "COURSES", [])
    monkeypatch.setattr(appstate, "COURSE_BY_NO", {})


def make_race(client) -> int:
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    resp = client.post("/admin/race/new", data={"_csrf_token": TOKEN, "name": "River race",
                                                "start_time": "2026-10-25T14:00"})
    assert resp.status_code == 302
    return int(re.search(r"/race/(\d+)", resp.headers["Location"]).group(1))


def test_its_race_sheet_opens(no_fixed_courses, logged_in_client):
    race_id = make_race(logged_in_client)
    resp = logged_in_client.get(f"/admin/race/{race_id}")
    assert resp.status_code == 200
    assert b"Course not set" in resp.data


def test_the_public_pages_still_answer(no_fixed_courses, logged_in_client):
    race_id = make_race(logged_in_client)
    for url in ("/public/current", "/public/current/state", f"/public/race/{race_id}", "/bar"):
        assert logged_in_client.get(url).status_code == 200, url


def test_the_course_is_empty_until_one_is_made_up(no_fixed_courses, logged_in_client):
    race_id = make_race(logged_in_client)
    with ro.app.app_context():
        course = courses.course_for_race(get_race(race_id))
    assert course["marks"] == [] and course["course_no"] == "No course set"
