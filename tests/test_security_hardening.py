"""Tests for the pre-launch security hardening bundle."""
from core import loginguard


class TestResponseHeaders:
    def test_security_headers_present(self, client):
        resp = client.get("/admin/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_headers_on_public_page(self, client):
        resp = client.get("/public/current")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"


class TestAppConfig:
    def test_session_cookie_flags(self, client):
        import app as ro
        assert ro.app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert ro.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_max_content_length_set(self, client):
        import app as ro
        assert ro.app.config["MAX_CONTENT_LENGTH"] == 64 * 1024 * 1024


class TestLoginGuardUnit:
    def setup_method(self):
        loginguard.reset_all()

    def test_locks_after_max_attempts(self):
        key = "1.2.3.4|admin"
        for _ in range(loginguard.MAX_ATTEMPTS - 1):
            loginguard.record_failure(key, now=1000.0)
        assert not loginguard.is_locked(key, now=1000.0)
        loginguard.record_failure(key, now=1000.0)
        assert loginguard.is_locked(key, now=1000.0)

    def test_window_expiry_frees_the_key(self):
        key = "1.2.3.4|admin"
        for _ in range(loginguard.MAX_ATTEMPTS):
            loginguard.record_failure(key, now=1000.0)
        assert loginguard.is_locked(key, now=1000.0)
        # after the window, old failures no longer count
        later = 1000.0 + loginguard.WINDOW_SECONDS + 1
        assert not loginguard.is_locked(key, now=later)

    def test_clear_on_success(self):
        key = "1.2.3.4|admin"
        for _ in range(loginguard.MAX_ATTEMPTS):
            loginguard.record_failure(key, now=1000.0)
        assert loginguard.is_locked(key, now=1000.0)
        loginguard.clear(key)
        assert not loginguard.is_locked(key, now=1000.0)

    def test_keys_are_independent(self):
        for _ in range(loginguard.MAX_ATTEMPTS):
            loginguard.record_failure("1.2.3.4|admin", now=1000.0)
        # a different user from the same ip is unaffected
        assert not loginguard.is_locked("1.2.3.4|other", now=1000.0)


class TestLoginLockoutRoute:
    def test_repeated_failures_lock_the_login(self, csrf_post):
        last = None
        for _ in range(loginguard.MAX_ATTEMPTS):
            last = csrf_post("/admin/login", {"username": "admin", "password": "wrong"})
            assert b"Invalid username or password" in last.data
        # the next attempt is blocked before the password is even checked
        blocked = csrf_post("/admin/login", {"username": "admin", "password": "wrong"})
        assert b"Too many failed sign-in attempts" in blocked.data
        assert b"Invalid username or password" not in blocked.data
