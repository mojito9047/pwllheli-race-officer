"""Tests for login CSRF handling.

Regression cover for the "Bad request: CSRF token missing or invalid" a user
hit on first login when the login page was served without a fresh session
cookie (e.g. from a browser/CDN cache). The login page must not be cacheable,
and a login CSRF failure must be recoverable rather than a dead-end 400.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402

ADMIN_PW = "testadminpass"


def _token_from_html(html: str) -> str:
    m = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    assert m, "login page did not contain a CSRF token field"
    return m.group(1)


class TestLoginCsrf:
    def test_login_page_is_not_cacheable(self, client):
        resp = client.get("/admin/login")
        assert resp.status_code == 200
        cache = resp.headers.get("Cache-Control", "")
        assert "no-store" in cache, f"login page must be no-store, got {cache!r}"

    def test_full_get_then_post_login_succeeds(self, client):
        """The real browser flow: GET sets the cookie+token, POST uses them."""
        page = client.get("/admin/login")
        token = _token_from_html(page.get_data(as_text=True))
        resp = client.post(
            "/admin/login",
            data={"_csrf_token": token, "username": "admin", "password": ADMIN_PW},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        with client.session_transaction() as sess:
            assert sess.get("user_id")

    def test_login_csrf_failure_redirects_not_400(self, client):
        """A missing/stale session token on login recovers to a fresh login page."""
        resp = client.post(
            "/admin/login",
            data={"_csrf_token": "bogus-token", "username": "admin", "password": ADMIN_PW},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")
        # Not authenticated by a failed-CSRF attempt.
        with client.session_transaction() as sess:
            assert not sess.get("user_id")

    def test_login_csrf_failure_shows_message(self, client):
        resp = client.post(
            "/admin/login",
            data={"_csrf_token": "bogus-token", "username": "admin", "password": ADMIN_PW},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "sign in again" in resp.get_data(as_text=True).lower()

    def test_non_login_csrf_failure_still_400(self, client):
        """Other state-changing routes keep the strict 400 (no recovery bounce)."""
        resp = client.post("/admin/settings/save", data={"weather_poll_seconds": "9"},
                            follow_redirects=False)
        assert resp.status_code == 400
