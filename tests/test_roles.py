"""Tests for admin vs race_officer role enforcement on the Settings area.

Admin can change everything; race_officer sees Settings read-only and is
rejected by the settings-write routes as a backstop.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

ADMIN_PW = "testadminpass"
USER_PW = "pw123456"


def _seed_user(username: str, role: str, password: str = USER_PW) -> int:
    now = "2026-07-14T09:00:00"
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, display_name, role, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (username, generate_password_hash(password), username.title(), role, now, now),
        )
        db.commit()
        return int(cur.lastrowid)


def _login(client, username: str, password: str):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    return client.post("/login", data={"_csrf_token": token, "username": username, "password": password})


def _post_csrf(client, url: str, data: dict | None = None, **kwargs):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    form = dict(data or {})
    form["_csrf_token"] = token
    return client.post(url, data=form, **kwargs)


def _user_by_name(username: str):
    return next((u for u in ro.list_users() if u["username"] == username), None)


class TestRoleHelpers:
    def test_normalize_role(self, client):
        assert ro.normalize_role("admin") == "admin"
        assert ro.normalize_role("  ADMIN ") == "admin"
        assert ro.normalize_role("race_officer") == "race_officer"
        assert ro.normalize_role("nonsense") == "race_officer"
        assert ro.normalize_role("") == "race_officer"


class TestSettingsWriteGating:
    def test_admin_can_save_settings(self, client):
        _login(client, "admin", ADMIN_PW)
        resp = _post_csrf(client, "/admin/settings/save", {"weather_poll_seconds": "9"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert int(ro.weather_config()["weather_poll_seconds"]) == 9

    def test_race_officer_cannot_save_settings(self, client):
        _login(client, "admin", ADMIN_PW)
        _post_csrf(client, "/admin/settings/save", {"weather_poll_seconds": "7"})
        client.get("/logout")
        _seed_user("ro1", "race_officer")
        _login(client, "ro1", USER_PW)
        resp = _post_csrf(client, "/admin/settings/save", {"weather_poll_seconds": "42"}, follow_redirects=False)
        assert resp.status_code in (302, 303)  # redirected, not applied
        assert int(ro.weather_config()["weather_poll_seconds"]) == 7

    def test_race_officer_save_via_json_is_403(self, client):
        _seed_user("ro_json", "race_officer")
        _login(client, "ro_json", USER_PW)
        resp = _post_csrf(
            client, "/admin/settings/save", {"weather_poll_seconds": "5"},
            headers={"Accept": "application/json"}, follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_race_officer_cannot_add_user(self, client):
        _seed_user("ro2", "race_officer")
        _login(client, "ro2", USER_PW)
        before = len(ro.list_users())
        resp = _post_csrf(
            client, "/admin/settings/users/add",
            {"username": "sneaky", "password": "whatever1", "role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert len(ro.list_users()) == before
        assert _user_by_name("sneaky") is None


class TestSettingsReadOnlyRendering:
    def test_admin_settings_page_is_editable(self, client):
        _login(client, "admin", ADMIN_PW)
        body = client.get("/admin/settings").get_data(as_text=True)
        assert 'class="settings-fieldset"' in body
        assert 'class="settings-fieldset" disabled' not in body
        assert ">Save settings</button>" in body

    def test_race_officer_settings_page_is_read_only(self, client):
        _seed_user("ro3", "race_officer")
        _login(client, "ro3", USER_PW)
        body = client.get("/admin/settings").get_data(as_text=True)
        assert 'class="settings-fieldset" disabled' in body
        assert "readonly-pill" in body
        assert ">Save settings</button>" not in body


class TestLastAdminGuard:
    def test_cannot_demote_last_admin(self, client):
        _login(client, "admin", ADMIN_PW)
        admin = _user_by_name("admin")
        resp = _post_csrf(
            client, f"/admin/settings/users/{admin['id']}/password",
            {"role": "race_officer", "status": "ACTIVE", "display_name": "Administrator"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert (_user_by_name("admin")["role"] or "").lower() == "admin"

    def test_can_demote_admin_when_another_admin_exists(self, client):
        _login(client, "admin", ADMIN_PW)
        _seed_user("admin2", "admin")
        a2 = _user_by_name("admin2")
        resp = _post_csrf(
            client, f"/admin/settings/users/{a2['id']}/password",
            {"role": "race_officer", "status": "ACTIVE"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert (_user_by_name("admin2")["role"] or "").lower() == "race_officer"

class TestBackupRestoreGating:
    def test_admin_sees_restore_form(self, client):
        _login(client, "admin", ADMIN_PW)
        body = client.get("/admin/backup").get_data(as_text=True)
        assert "/backup/restore" in body
        assert ">Restore selected sections<" in body

    def test_race_officer_cannot_see_restore_form(self, client):
        _seed_user("ro_backup", "race_officer")
        _login(client, "ro_backup", USER_PW)
        body = client.get("/admin/backup").get_data(as_text=True)
        assert "/backup/restore" not in body
        assert "Administrator access required" in body
        # download (backup) is still available to race officers
        assert "/backup/download" in body

    def test_race_officer_cannot_post_restore(self, client):
        _seed_user("ro_restore", "race_officer")
        _login(client, "ro_restore", USER_PW)
        resp = _post_csrf(client, "/admin/backup/restore", {}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/backup" in resp.headers.get("Location", "")

    def test_race_officer_restore_via_json_is_403(self, client):
        _seed_user("ro_restore_json", "race_officer")
        _login(client, "ro_restore_json", USER_PW)
        resp = _post_csrf(
            client, "/admin/backup/restore", {},
            headers={"Accept": "application/json"}, follow_redirects=False,
        )
        assert resp.status_code == 403


class TestAccountPasswordSelfService:
    def test_account_page_available_to_race_officer(self, client):
        _seed_user("ro_acct", "race_officer")
        _login(client, "ro_acct", USER_PW)
        resp = client.get("/admin/account")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Change my password" in body
        assert "ro_acct" in body

    def test_race_officer_can_change_own_password(self, client):
        _seed_user("ro_pw", "race_officer")
        _login(client, "ro_pw", USER_PW)
        new_pw = "newpw12345"
        resp = _post_csrf(
            client, "/admin/account",
            {"current_password": USER_PW, "new_password": new_pw, "confirm_password": new_pw},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        # Old password no longer works; new one does.
        client.get("/logout")
        rejected = _login(client, "ro_pw", USER_PW)  # rejected → re-renders login (200)
        assert rejected.status_code == 200
        assert "Invalid username or password" in rejected.get_data(as_text=True)
        assert _login(client, "ro_pw", new_pw).status_code in (302, 303)

    def test_wrong_current_password_is_rejected(self, client):
        _seed_user("ro_pw2", "race_officer")
        _login(client, "ro_pw2", USER_PW)
        resp = _post_csrf(
            client, "/admin/account",
            {"current_password": "wrongpass", "new_password": "another12345", "confirm_password": "another12345"},
            follow_redirects=False,
        )
        assert resp.status_code == 200  # re-renders the form, not a redirect
        # Original password still works.
        client.get("/logout")
        assert _login(client, "ro_pw2", USER_PW).status_code in (302, 303)

    def test_mismatched_confirmation_is_rejected(self, client):
        _seed_user("ro_pw3", "race_officer")
        _login(client, "ro_pw3", USER_PW)
        resp = _post_csrf(
            client, "/admin/account",
            {"current_password": USER_PW, "new_password": "abcdefgh1", "confirm_password": "different1"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        client.get("/logout")
        assert _login(client, "ro_pw3", USER_PW).status_code in (302, 303)

    def test_too_short_password_is_rejected(self, client):
        _seed_user("ro_pw4", "race_officer")
        _login(client, "ro_pw4", USER_PW)
        resp = _post_csrf(
            client, "/admin/account",
            {"current_password": USER_PW, "new_password": "short", "confirm_password": "short"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        client.get("/logout")
        assert _login(client, "ro_pw4", USER_PW).status_code in (302, 303)

    def test_account_requires_login(self, client):
        resp = client.get("/admin/account", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")


class TestUserDeletion:
    def test_admin_can_delete_a_race_officer(self, client):
        # The route can't zero-out admins on its own (the actor is always an admin
        # and can't delete themselves), so the last-admin protection lives mainly in
        # the demote guard above. Here we cover the delete path still works.
        _login(client, "admin", ADMIN_PW)
        ro_id = _seed_user("ro4", "race_officer")
        resp = _post_csrf(client, f"/admin/settings/users/{ro_id}/delete", {}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert _user_by_name("ro4") is None
        assert (_user_by_name("admin")["role"] or "").lower() == "admin"


class TestAddingAUser:
    """Adding a user through the form the administrator actually uses.

    Nothing covered this. The route was posted to only as a race officer being
    refused, so the SQL underneath never ran in a test — and when the Virtual
    Race Officer permission was added to it, the column went into the INSERT
    without its placeholder: nine columns, eight values. Saving raised
    `OperationalError`, the route catches only `IntegrityError`, and the 500
    reached the relay, which serves its "hut not available" page for a 500. So a
    one-character omission read, on the live club system, as the whole hut being
    down.
    """

    def test_an_admin_can_add_one(self, client):
        _login(client, "admin", ADMIN_PW)
        resp = _post_csrf(client, "/admin/settings/users/add", {
            "username": "newro", "password": USER_PW, "display_name": "New RO",
            "role": "race_officer"}, follow_redirects=False)
        assert resp.status_code in (302, 303), resp.status_code
        made = _user_by_name("newro")
        assert made is not None, "the user was not created"
        assert (made["role"] or "").lower() == "race_officer"
        assert (made["status"] or "").upper() == "ACTIVE"
        assert not made["can_race_remotely"]

    def test_and_can_grant_the_virtual_race_officer_permission_while_doing_it(self, client):
        _login(client, "admin", ADMIN_PW)
        _post_csrf(client, "/admin/settings/users/add", {
            "username": "vroro", "password": USER_PW, "display_name": "VRO RO",
            "role": "race_officer", "can_race_remotely": "1", "can_set_marks": "1"},
            follow_redirects=False)
        made = _user_by_name("vroro")
        assert made is not None
        assert made["can_race_remotely"], "the VRO tick box did not reach the database"
        assert made["can_set_marks"]

    def test_the_new_user_can_actually_sign_in(self, client):
        """The row being there is not the same as the password working."""
        _login(client, "admin", ADMIN_PW)
        _post_csrf(client, "/admin/settings/users/add", {
            "username": "signsin", "password": USER_PW, "role": "race_officer"},
            follow_redirects=False)
        client.get("/logout")
        resp = _login(client, "signsin", USER_PW)
        assert resp.status_code in (302, 303)
        assert client.get("/admin", follow_redirects=True).status_code == 200

    def test_a_duplicate_username_is_refused_not_crashed_on(self, client):
        _login(client, "admin", ADMIN_PW)
        for _ in range(2):
            resp = _post_csrf(client, "/admin/settings/users/add", {
                "username": "twice", "password": USER_PW, "role": "race_officer"},
                follow_redirects=False)
            assert resp.status_code in (302, 303)
        with ro.get_db() as db:
            count = db.execute("SELECT COUNT(*) FROM users WHERE username = 'twice'").fetchone()[0]
        assert count == 1
