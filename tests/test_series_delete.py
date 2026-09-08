"""A series can be deleted, but only once nothing is in it.

There was no way to remove one at all: a series created by mistake, or a season
that never ran, stayed on the list for ever and went on being offered in every
race's series picker.

The rule is the one the club asked for, and it is the conservative reading. A
series is not just a label on a group of races — it carries the rating bands, the
start plan and the discard profile those races were *scored under*. Deleting the
row while races point at it would leave them claiming to belong to a series that
is not there, dropped out of the standings, with no record of what they had been
scored against. Cascading the delete instead would remove a season's racing from
a button on a list page. So neither: the races have to be moved out or deleted
first, and until they are the page says so and how.

The guard is asked twice — once by the page, to decide whether to offer the
button, and again by the route, so a page left open while somebody else adds a
race cannot delete a season.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import core.raceadmin as raceadmin
from core.raceadmin import RaceValidationError


def _post(client, url, data=None):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    payload = dict(data or {})
    payload["_csrf_token"] = token
    return client.post(url, data=payload, follow_redirects=True)


def _make_series(name="Autumn 2026"):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, discard_profile,"
            " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, "", "0,0,1", 3, now, now))
        db.commit()
        return int(cur.lastrowid)


def _make_race(series_id=None, name="R1"):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, series_id, course_no, start_time, notes, created_at)"
            " VALUES (?,?,?,?,?,?)", (name, series_id, 1, "", "", now))
        db.commit()
        return int(cur.lastrowid)


def _series_exists(series_id):
    from core.db import get_db
    with get_db() as db:
        return db.execute("SELECT 1 FROM race_series WHERE id = ?",
                          (series_id,)).fetchone() is not None


def _block(series_id):
    from core.db import get_db
    with get_db() as db:
        return raceadmin.series_delete_block(db, series_id)


class TestTheGuard:
    def test_an_empty_series_is_free_to_go(self, client):
        assert _block(_make_series()) is None

    def test_a_series_with_a_race_is_held(self, client):
        sid = _make_series()
        _make_race(series_id=sid)
        assert _block(sid) is not None

    def test_the_reason_counts_the_races_and_says_what_to_do(self, client):
        """"In use" on its own leaves a race officer guessing. The message has to
        carry the way out, because the way out is not obvious: a race's series is
        changed on its own Course & start tab, not from here."""
        sid = _make_series()
        for i in range(3):
            _make_race(series_id=sid, name=f"R{i}")
        reason = _block(sid)
        assert "3 races" in reason
        assert "Course & start" in reason

    def test_one_race_reads_as_one_race(self, client):
        sid = _make_series()
        _make_race(series_id=sid)
        reason = _block(sid)
        assert "1 race still" in reason, reason

    def test_a_series_that_is_not_there(self, client):
        assert _block(999999) == "Series not found."

    def test_another_series_race_does_not_hold_this_one(self, client):
        """The count must be per series, not a count of races."""
        mine, theirs = _make_series("Mine"), _make_series("Theirs")
        _make_race(series_id=theirs)
        assert _block(mine) is None

    def test_a_race_in_no_series_holds_nothing(self, client):
        sid = _make_series()
        _make_race(series_id=None)
        assert _block(sid) is None


class TestTheDelete:
    def _delete(self, series_id):
        from core.db import get_db
        with get_db() as db:
            return raceadmin.delete_series(db, series_id, actor="tester")

    def test_an_empty_series_goes(self, client):
        sid = _make_series("Gone")
        assert self._delete(sid) == "Gone"
        assert not _series_exists(sid)

    def test_a_populated_series_is_refused(self, client):
        sid = _make_series()
        _make_race(series_id=sid)
        with pytest.raises(RaceValidationError):
            self._delete(sid)
        assert _series_exists(sid)

    def test_a_refused_delete_leaves_the_races_alone(self, client):
        """The failure mode worth naming: a half-done delete that took the racing
        with it and then refused."""
        from core.db import get_db
        sid = _make_series()
        rid = _make_race(series_id=sid)
        with pytest.raises(RaceValidationError):
            self._delete(sid)
        with get_db() as db:
            row = db.execute("SELECT series_id FROM races WHERE id = ?", (rid,)).fetchone()
        assert row is not None and int(row[0]) == sid

    def test_moving_the_race_out_releases_it(self, client):
        """The flow the club described: move the races, then delete."""
        from core.db import get_db
        sid = _make_series()
        rid = _make_race(series_id=sid)
        assert _block(sid) is not None
        with get_db() as db:
            db.execute("UPDATE races SET series_id = NULL WHERE id = ?", (rid,))
            db.commit()
        assert _block(sid) is None
        self._delete(sid)
        assert not _series_exists(sid)

    def test_deleting_the_race_releases_it_too(self, client):
        from core.db import get_db
        sid = _make_series()
        rid = _make_race(series_id=sid)
        with get_db() as db:
            db.execute("DELETE FROM races WHERE id = ?", (rid,))
            db.commit()
        self._delete(sid)
        assert not _series_exists(sid)

    def test_deleting_one_series_leaves_the_others(self, client):
        a, b = _make_series("A"), _make_series("B")
        self._delete(a)
        assert not _series_exists(a) and _series_exists(b)


class TestOverTheWeb:
    def test_the_route_deletes_an_empty_series(self, logged_in_client):
        sid = _make_series("Webbed")
        _post(logged_in_client, f"/admin/series/{sid}/delete")
        assert not _series_exists(sid)

    def test_the_route_asks_the_guard_again(self, logged_in_client):
        """A page open since before somebody added a race still shows the button.
        The route must not take the page's word for it."""
        sid = _make_series()
        _make_race(series_id=sid)
        resp = _post(logged_in_client, f"/admin/series/{sid}/delete")
        assert _series_exists(sid)
        assert "Cannot delete this series" in resp.get_data(as_text=True)

    def test_a_series_that_is_gone_does_not_crash(self, logged_in_client):
        resp = _post(logged_in_client, "/admin/series/999999/delete")
        assert resp.status_code == 200

    def test_it_is_a_post_only(self, logged_in_client):
        """A GET would let a link — or a crawler following one — delete a series."""
        sid = _make_series()
        assert logged_in_client.get(f"/admin/series/{sid}/delete").status_code == 405
        assert _series_exists(sid)

    def test_it_is_audited(self, logged_in_client, monkeypatch):
        import core.activitylog as activitylog
        seen = []
        monkeypatch.setattr(activitylog, "log_activity",
                            lambda *a, **k: seen.append((a, k)))
        monkeypatch.setattr(raceadmin, "log_activity",
                            lambda *a, **k: seen.append((a, k)))
        sid = _make_series("Audited")
        _post(logged_in_client, f"/admin/series/{sid}/delete")
        assert any("series deleted" in str(a) for a, _ in seen), seen


class TestWhatThePagesOffer:
    def test_the_list_offers_delete_for_an_empty_series(self, logged_in_client):
        sid = _make_series("Empty")
        html = logged_in_client.get("/admin/series").get_data(as_text=True)
        assert f"/series/{sid}/delete" in html

    def test_the_list_offers_no_button_for_a_populated_one(self, logged_in_client):
        sid = _make_series("Busy")
        _make_race(series_id=sid)
        html = logged_in_client.get("/admin/series").get_data(as_text=True)
        assert f"/series/{sid}/delete" not in html
        assert "still in this series" in html

    def test_the_series_page_offers_delete_when_empty(self, logged_in_client):
        sid = _make_series("Empty")
        html = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        assert f"/series/{sid}/delete" in html

    def test_the_series_page_explains_instead_when_it_cannot(self, logged_in_client):
        sid = _make_series("Busy")
        _make_race(series_id=sid)
        html = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        assert f"/series/{sid}/delete" not in html
        assert "still in this series" in html
