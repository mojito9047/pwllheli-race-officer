"""Tests for track A of the live-stream feature: the public branding manifest
endpoint the relay pulls, and the gated "Watch live" link on the competitor pages.
"""
from __future__ import annotations

import os
import time

import app as ro


class TestBrandingManifest:
    def test_public_no_login_returns_manifest(self, client):
        resp = client.get("/api/branding/live", headers={"Accept": "application/json"})
        assert resp.status_code == 200  # public, no login
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["rotation_seconds"] == ro.video.PUBLIC_BRANDING_ROTATION_SECONDS
        assert isinstance(data["sponsors"], list)
        # Club logo is served as an absolute URL (bundled default or uploaded) so the
        # external relay can fetch it.
        assert data["club_logo_url"].startswith("http")
        assert ("/static/img/" in data["club_logo_url"]) or ("/public/branding/" in data["club_logo_url"])

    def test_manifest_empty_when_branding_disabled(self, client):
        ro.save_app_settings({"public_branding_enabled": "0"})
        data = client.get("/api/branding/live").get_json()
        assert data["enabled"] is False
        assert data["club_logo_url"] == ""
        assert data["sponsors"] == []


class TestPublicLiveStreamLink:
    def test_sanitize_public_url(self, client):
        assert ro.sanitize_public_url("https://live.example.org") == "https://live.example.org"
        assert ro.sanitize_public_url("http://x") == "http://x"
        assert ro.sanitize_public_url("javascript:alert(1)") == ""
        assert ro.sanitize_public_url("   ") == ""
        assert ro.sanitize_public_url(None) == ""

    def test_link_hidden_when_unset(self, client):
        resp = client.get("/public/current")
        assert resp.status_code == 200
        assert b"Watch live" not in resp.data

    def test_home_camera_embeds_the_stream_when_set(self, client):
        # v0.188: the home Live camera tab plays the stream inline, so there is no
        # separate "Watch live" button — the panel itself is the live view.
        ro.save_app_settings({"public_live_stream_url": "https://live.example.org"})
        resp = client.get("/public/current")
        assert resp.status_code == 200
        assert b"Watch live" not in resp.data
        assert b'data-stream-page="https://live.example.org"' in resp.data

    def test_invalid_url_is_rejected_on_save(self, client):
        ro.save_app_settings({"public_live_stream_url": "javascript:alert(1)"})
        assert ro.public_live_stream_url() == ""

    def test_link_shown_on_race_page_when_set(self, client):
        ro.save_app_settings({"public_live_stream_url": "https://live.example.org"})
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Link Test Race", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            db.commit()
            race_id = int(cur.lastrowid)
        resp = client.get(f"/public/race/{race_id}")
        assert resp.status_code == 200
        assert b"Watch live" in resp.data


class TestLivePreviewStallWatchdog:
    """The public live JPEG froze on the hut when the preview ffmpeg stayed alive
    but its RTSP read stalled (the relay competing for the camera was a trigger).
    live_preview_appears_stalled() lets the supervisor detect and relaunch it."""

    def _set_frame_mtime(self, path, age_seconds):
        path.write_bytes(b"jpeg")
        t = time.time() - age_seconds
        os.utime(path, (t, t))

    def test_not_stalled_before_grace_window(self, tmp_path, monkeypatch):
        # Preview only just started: a stale/old JPEG must NOT count as a hang.
        frame = tmp_path / "live.jpg"
        self._set_frame_mtime(frame, ro.video.PREVIEW_STALL_SECONDS + 100)
        monkeypatch.setattr(ro.video.appstate, "VIDEO_LIVE_JPG_PATH", frame)
        monkeypatch.setitem(ro.video.VIDEO_RUNTIME_STATE, "preview_started_at", time.time())
        assert ro.video.live_preview_appears_stalled() is False

    def test_not_stalled_when_frame_is_fresh(self, tmp_path, monkeypatch):
        frame = tmp_path / "live.jpg"
        self._set_frame_mtime(frame, 1)
        monkeypatch.setattr(ro.video.appstate, "VIDEO_LIVE_JPG_PATH", frame)
        monkeypatch.setitem(
            ro.video.VIDEO_RUNTIME_STATE, "preview_started_at",
            time.time() - (ro.video.PREVIEW_STALL_SECONDS + 100))
        assert ro.video.live_preview_appears_stalled() is False

    def test_stalled_when_frame_is_old(self, tmp_path, monkeypatch):
        frame = tmp_path / "live.jpg"
        self._set_frame_mtime(frame, ro.video.PREVIEW_STALL_SECONDS + 60)
        monkeypatch.setattr(ro.video.appstate, "VIDEO_LIVE_JPG_PATH", frame)
        monkeypatch.setitem(
            ro.video.VIDEO_RUNTIME_STATE, "preview_started_at",
            time.time() - (ro.video.PREVIEW_STALL_SECONDS + 100))
        assert ro.video.live_preview_appears_stalled() is True

    def test_stalled_when_frame_missing_after_grace(self, tmp_path, monkeypatch):
        frame = tmp_path / "never_written.jpg"
        monkeypatch.setattr(ro.video.appstate, "VIDEO_LIVE_JPG_PATH", frame)
        monkeypatch.setitem(
            ro.video.VIDEO_RUNTIME_STATE, "preview_started_at",
            time.time() - (ro.video.PREVIEW_STALL_SECONDS + 100))
        assert ro.video.live_preview_appears_stalled() is True

    def test_not_stalled_when_never_started(self, tmp_path, monkeypatch):
        monkeypatch.setitem(ro.video.VIDEO_RUNTIME_STATE, "preview_started_at", None)
        assert ro.video.live_preview_appears_stalled() is False
