"""The mark-layer role, and the permission column the Settings page could not see.

Two related faults, both about who is allowed to move a mark.

The first was invisible rather than dangerous: `list_users` names its columns
explicitly to keep `password_hash` off the page, and `can_set_marks` was not in
the list. The permission saved correctly and was enforced correctly — but the
checkbox came back unticked every time, so there was no way to tell who held it.

The second is that the permission was the wrong shape for the job. Whoever takes
a RIB out after a storm is often neither an administrator nor the duty race
officer, and the only way to let them re-measure a mark was to give them a race
officer's login: the start sequence, the finish times and the results included,
to press one button on a phone. `mark_layer` is a role that reaches the phone
page and nothing else.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app as ro


NOW = datetime.now().isoformat(timespec="seconds")
TOKEN = "test-csrf-token"


def make_user(username: str, role: str, *, can_set_marks: int = 0,
              password: str = "ribpass123") -> None:
    with ro.app.app_context():
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO users (username, password_hash, display_name, role,"
                " status, can_set_marks, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                (username, ro.generate_password_hash(password), username.title(),
                 role, can_set_marks, NOW, NOW),
            )
            db.commit()


def user_row(html: str, username: str) -> str:
    """The Settings table row for one user.

    Anchored on the cell rather than the bare username: a flash message such as
    "User plain updated." appears earlier in the page and would otherwise be
    mistaken for the row, making the test pass or fail for the wrong reason.
    """
    cell = f"<strong>{username}</strong>"
    assert cell in html, f"no row for {username}"
    return html.split(cell)[1].split("</tr>")[0]


def login(client, username: str, password: str = "ribpass123"):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    return client.post("/login", data={"_csrf_token": TOKEN,
                                       "username": username, "password": password})


@pytest.fixture
def rib(client):
    """A client signed in as a mark layer."""
    make_user("rib", "mark_layer")
    login(client, "rib")
    return client


class TestThePermissionIsVisible:
    """The regression: saved, enforced, and impossible to see."""

    def test_list_users_returns_the_column(self, client):
        make_user("sets", "race_officer", can_set_marks=1)
        with ro.app.app_context():
            row = next(u for u in ro.list_users() if u["username"] == "sets")
        assert row["can_set_marks"] == 1

    def test_the_checkbox_comes_back_ticked(self, logged_in_client):
        make_user("sets", "race_officer", can_set_marks=1)
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        row = user_row(html, "sets")
        assert 'name="can_set_marks" value="1" checked' in row

    def test_and_stays_unticked_for_somebody_without_it(self, logged_in_client):
        make_user("plain", "race_officer", can_set_marks=0)
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        row = user_row(html, "plain")
        assert 'name="can_set_marks" value="1" checked' not in row
        assert 'name="can_set_marks"' in row

    def test_saving_it_on_then_reading_it_back(self, logged_in_client, csrf_post):
        """The round trip the user actually reported: tick it, save, look again."""
        make_user("plain", "race_officer", can_set_marks=0)
        with ro.app.app_context():
            uid = next(u["id"] for u in ro.list_users() if u["username"] == "plain")
        csrf_post(f"/settings/users/{uid}/password",
                  {"display_name": "Plain", "role": "race_officer",
                   "status": "ACTIVE", "can_set_marks": "1"})
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        row = user_row(html, "plain")
        assert 'name="can_set_marks" value="1" checked' in row


class TestTheSettingsPageOffersIt:
    def test_the_add_form_lists_the_role(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert '<option value="mark_layer">Mark layer</option>' in html

    def test_an_existing_mark_layer_shows_as_one(self, logged_in_client):
        """The row's dropdown used to treat anything that was not admin as a race
        officer, so a third role would have displayed as the wrong one."""
        make_user("rib", "mark_layer")
        row = user_row(logged_in_client.get("/admin/settings").get_data(as_text=True), "rib")
        assert '<option value="mark_layer" selected>' in row
        assert '<option value="race_officer" selected>' not in row

    def test_a_race_officer_still_shows_as_one(self, logged_in_client):
        make_user("ro", "race_officer")
        row = user_row(logged_in_client.get("/admin/settings").get_data(as_text=True), "ro")
        assert '<option value="race_officer" selected>' in row

    def test_the_checkbox_is_not_offered_where_it_would_be_a_lie(self, logged_in_client):
        """A mark layer always may, so an unticked box beside one would mislead."""
        make_user("rib", "mark_layer")
        row = user_row(logged_in_client.get("/admin/settings").get_data(as_text=True), "rib")
        assert 'name="can_set_marks"' not in row
        assert "always" in row


class TestTheRoleIsKnown:
    def test_it_survives_normalisation(self, client):
        assert ro.normalize_role("mark_layer") == "mark_layer"

    def test_an_unknown_role_still_falls_back(self, client):
        assert ro.normalize_role("commodore") == "race_officer"

    def test_a_mark_layer_may_set_marks_without_the_checkbox(self, client):
        """The role is the permission. A mark layer with the box unticked could
        log in and reach nothing whatsoever, which is not a state worth having."""
        make_user("rib", "mark_layer", can_set_marks=0)
        with ro.app.app_context():
            row = next(u for u in ro.list_users() if u["username"] == "rib")
        assert ro.user_can_set_marks(row) is True
        assert ro.user_is_mark_layer(row) is True

    def test_a_race_officer_is_not_one(self, client):
        make_user("ro", "race_officer")
        with ro.app.app_context():
            row = next(u for u in ro.list_users() if u["username"] == "ro")
        assert ro.user_is_mark_layer(row) is False


class TestAMarkLayerLandsOnThePhonePage:
    def test_login_goes_straight_there(self, client):
        make_user("rib", "mark_layer")
        resp = login(client, "rib")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/marks/ping")

    def test_the_page_opens(self, rib):
        resp = rib.get("/admin/marks/ping")
        assert resp.status_code == 200
        assert "Set a mark position" in resp.get_data(as_text=True)

    def test_there_is_no_link_back_to_a_page_they_cannot_reach(self, rib):
        """It would bounce straight back here — a button that does nothing."""
        html = rib.get("/admin/marks/ping").get_data(as_text=True)
        assert "Sign out" in html
        assert "&larr; Marks" not in html

    def test_a_race_officer_who_may_set_marks_still_gets_the_link(self, client):
        make_user("sets", "race_officer", can_set_marks=1)
        login(client, "sets")
        html = client.get("/admin/marks/ping").get_data(as_text=True)
        assert "&larr; Marks" in html


class TestAMarkLayerReachesNothingElse:
    @pytest.mark.parametrize("path", [
        "/admin/settings",
        "/admin/marks",
        "/admin/races",
        "/admin/boats",
        "/admin/backup",
    ])
    def test_every_other_page_sends_them_back(self, rib, path):
        resp = rib.get(path)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/marks/ping"), path

    def test_but_they_can_do_the_one_thing_the_role_is_for(self, rib, monkeypatch,
                                                          tmp_path):
        """Confinement is worthless if it also blocks the job. This is the POST
        the phone page makes, from a mark layer's session."""
        import json

        import core.marks as marks
        path = tmp_path / "marks.json"
        data = {"marks": {"T1": {"name": "Test one", "lat": 52.88, "lon": -4.40,
                                 "lat_text": "", "lon_text": ""}}}
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(marks, "_marks_path", lambda: path)
        monkeypatch.setattr(ro.appstate, "MARKS", data["marks"])

        with rib.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = rib.post("/marks/T1/position",
                        json={"lat": 52.8801, "lon": -4.4009, "accuracy_m": 5.0},
                        headers={"X-CSRFToken": TOKEN})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["ok"] is True
        stored = json.loads(path.read_text(encoding="utf-8"))["marks"]["T1"]
        assert stored["lat"] == 52.8801
        assert stored["position_set_by"] == "rib"

    def test_an_api_call_is_refused_rather_than_redirected(self, rib):
        resp = rib.get("/api/track/positions",
                       headers={"Accept": "application/json"})
        assert resp.status_code == 403
        assert resp.get_json()["ok"] is False

    def test_they_can_still_change_their_own_password(self, rib):
        """The one page besides the phone page: everyone gets their own account."""
        assert rib.get("/admin/account").status_code == 200

    def test_they_can_still_sign_out(self, rib):
        resp = rib.get("/logout")
        assert resp.status_code == 302
        assert "/marks/ping" not in resp.headers["Location"]

    def test_the_public_pages_are_untouched(self, rib):
        """Confinement is about the race office, not the competitor site."""
        assert rib.get("/public").status_code in (200, 302)


class TestTheRestOfTheAppIsUnaffected:
    def test_an_admin_still_reaches_settings(self, logged_in_client):
        assert logged_in_client.get("/admin/settings").status_code == 200

    def test_a_race_officer_still_reaches_the_races_page(self, client):
        make_user("ro", "race_officer")
        login(client, "ro")
        assert client.get("/admin/races").status_code == 200


class TestEditingAMarkFollowsThePermission:
    """A user with **Set marks** may now edit a mark, not only re-measure one.

    The permission already lets somebody stand next to a mark and set it to where
    they are. Typing a correction to the same mark's name, buoy description or
    rounding radius is the lesser act, and whoever has just re-laid it is the one
    who knows — so requiring an administrator for that was the wrong line.

    Adding and deleting marks stay admin-only: those change the set of marks that
    every course sequence refers to.
    """

    def _edit(self, client, code="1", name="Renamed by the RIB"):
        with client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        marks = ro.appstate.MARKS
        md = marks.get(code) or next(iter(marks.values()))
        return client.post(f"/admin/marks/{code}/edit", data={
            "_csrf_token": TOKEN, "name": name,
            "lat": md.get("lat"), "lon": md.get("lon"),
            "buoy": md.get("buoy") or "", "top_mark": md.get("top_mark") or "",
        })

    def _editable_code(self):
        from core import marks as core_marks
        for code in ro.appstate.MARKS:
            if core_marks.mark_edit_block(code) is None:
                return code
        pytest.skip("no editable mark in the bundled data")

    def test_a_race_officer_with_the_permission_may_edit(self, client):
        make_user("sets", "race_officer", can_set_marks=1)
        login(client, "sets")
        code = self._editable_code()
        resp = self._edit(client, code)
        assert resp.status_code == 302
        assert ro.appstate.MARKS[code]["name"] == "Renamed by the RIB"

    def test_a_race_officer_without_it_may_not(self, client):
        make_user("plain", "race_officer", can_set_marks=0)
        login(client, "plain")
        code = self._editable_code()
        before = ro.appstate.MARKS[code]["name"]
        self._edit(client, code)
        assert ro.appstate.MARKS[code]["name"] == before

    def test_the_edit_form_is_offered_to_them(self, client):
        make_user("sets2", "race_officer", can_set_marks=1)
        login(client, "sets2")
        html = client.get("/admin/marks").get_data(as_text=True)
        assert "mark-edit" in html

    def test_but_not_the_add_form_or_the_delete_button(self, client):
        """Both change the set of marks courses are written against."""
        make_user("sets3", "race_officer", can_set_marks=1)
        login(client, "sets3")
        html = client.get("/admin/marks").get_data(as_text=True)
        assert 'id="add-mark"' not in html
        assert "requires administrator access" in html

    def test_the_table_still_lines_up_without_the_actions_column(self, client):
        """The edit panel spans the table, so its colspan has to follow the column
        count. A stale 7 against 6 columns stretches the table past the window,
        which this page has done before."""
        make_user("sets5", "race_officer", can_set_marks=1)
        login(client, "sets5")
        html = client.get("/admin/marks").get_data(as_text=True)
        head = html.split("<thead>")[1].split("</thead>")[0]
        assert head.count("<th") == 6, "no actions column for a non-admin"
        assert 'colspan="6"' in html
        assert 'colspan="7"' not in html

    def test_adding_a_mark_is_still_refused(self, client):
        make_user("sets4", "race_officer", can_set_marks=1)
        login(client, "sets4")
        with client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        before = set(ro.appstate.MARKS)
        client.post("/admin/marks/add", data={
            "_csrf_token": TOKEN, "code": "ZZ9", "name": "Sneaked in",
            "lat": "52.88", "lon": "-4.40"})
        assert set(ro.appstate.MARKS) == before

    def test_an_admin_still_sees_everything(self, logged_in_client):
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert 'id="add-mark"' in html
        assert "mark-edit" in html

    def test_a_mark_layer_still_cannot_reach_the_marks_page(self, rib):
        """user_can_set_marks is true for the role, but confinement is separate and
        stricter: the role exists to press one button on a phone."""
        # Re-seed the token after the fixture's login, or this is rejected as a CSRF
        # failure before confinement is ever consulted — and would pass whether or
        # not the mark layer is actually confined.
        with rib.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = rib.post("/admin/marks/1/edit", data={"_csrf_token": TOKEN, "name": "X"})
        assert resp.status_code in (302, 403)
        assert "marks/ping" in resp.headers.get("Location", ""),             "a mark layer should be bounced back to the phone page"
        assert ro.appstate.MARKS.get("1", {}).get("name") != "X"
