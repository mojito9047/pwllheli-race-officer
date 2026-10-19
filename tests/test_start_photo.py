"""The start photo: the camera's own still at each start signal, on the public
race page a few seconds after the gun (v1.014).

Competitors asked for early word on whether they were over the line. The start
video cannot give it -- it is cut a minute after the gun, encoded for minutes
and kept off the public page until the race is over -- so the hut asks the
camera for a still at the signal, draws the line and the club logo on it and
publishes it at once. If the camera gives nothing, the same moment comes out of
the recording instead.
"""
from __future__ import annotations

import shutil
import time as _time_module

real_sleep = _time_module.sleep
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import app as ro
from core import appstate, odm_detector as odm, startphoto


def _jpeg(path: Path, size=(800, 450), colour=None) -> Path:
    """A photograph-sized JPEG: noise, so it is not suspiciously small."""
    rng = np.random.default_rng(7)
    if colour is None:
        pixels = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    else:
        pixels = np.full((size[1], size[0], 3), colour, dtype=np.uint8)
        pixels = np.clip(pixels.astype(int) + rng.integers(-20, 20, pixels.shape), 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(path, quality=95)
    return path


def _race_with_start_at(start: datetime) -> tuple:
    """A race whose first start signal is ``start``; returns (race id, start iso)."""
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
            " VALUES ('Start photo race', 'IRC', 1, ?, 'DUAL', '', ?)",
            (start.isoformat(timespec="seconds"), start.isoformat(timespec="seconds")))
        race_id = int(cur.lastrowid)
        db.commit()
    race = ro.get_race(race_id)
    first = startphoto._race_start_times(race)[0]
    shift = datetime.fromisoformat(first) - start           # warning signal -> start signal
    with ro.get_db() as db:
        db.execute("UPDATE races SET start_time = ? WHERE id = ?",
                   ((start - shift).isoformat(timespec="seconds"), race_id))
        db.commit()
    assert startphoto._race_start_times(ro.get_race(race_id))[0] == start.isoformat(timespec="seconds")
    return race_id, start.isoformat(timespec="seconds")


def _start_clip(race_id: int, event_time: str, status: str = "pending") -> int:
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds, post_seconds, status, label,"
            " created_at, updated_at) VALUES (?, 'start', ?, 60, 60, ?, 'Start video', ?, ?)",
            (race_id, event_time, status, event_time, event_time))
        db.commit()
        return int(cur.lastrowid)


def _row(clip_id: int) -> dict:
    with ro.get_db() as db:
        return dict(db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone())


@pytest.fixture()
def hut(client, monkeypatch, tmp_path):
    """Photos on, a camera that answers, R2 that accepts, and no FFmpeg unless asked."""
    monkeypatch.setattr(appstate, "VIDEO_RUNTIME_DIR", tmp_path / "runtime_video")
    cfg = {
        "video_enabled": True, "video_start_photo_enabled": True, "video_startline_overlay_enabled": False,
        "ptz_camera_url": "http://192.168.1.157", "ptz_username": "admin", "ptz_password": "pw",
        "ptz_auth_mode": "digest", "ptz_channel": 1, "video_segment_seconds": 20, "video_buffer_minutes": 20,
        "video_public_provider": "r2", "video_public_r2_prefix": "racevideos", "video_ffmpeg_path": "ffmpeg",
    }
    seen = {"asked": [], "uploads": [], "recording": [], "camera_code": 200, "upload_fails": 0}
    monkeypatch.setattr(startphoto, "video_config", lambda: cfg)
    monkeypatch.setattr(startphoto, "ffmpeg_executable", lambda cfg: None)
    monkeypatch.setattr(startphoto, "video_public_r2_ready", lambda cfg: True)
    monkeypatch.setattr(startphoto.time, "sleep", lambda s: None)

    def camera(url, username, password, mode, dest):
        seen["asked"].append(url)
        if seen["camera_code"] == 200:
            _jpeg(Path(dest))
        return seen["camera_code"]
    monkeypatch.setattr(startphoto, "_curl_snapshot", camera)

    def upload(cfg, key, path, content_type="video/mp4"):
        if seen["upload_fails"]:
            seen["upload_fails"] -= 1
            raise RuntimeError("4G dropped")
        seen["uploads"].append((key, Path(path).name, content_type))
        return "https://assets.example.org/" + key
    monkeypatch.setattr(startphoto, "upload_file_to_r2", upload)

    def recording(cfg, ffmpeg, event_dt, dest, work):
        seen["recording"].append(event_dt)
        _jpeg(Path(dest), colour=(40, 60, 80))
    monkeypatch.setattr(startphoto, "frame_from_recording", recording)
    monkeypatch.setattr(startphoto, "_ARMED", set())
    monkeypatch.setattr(startphoto, "_SETTLED", set())
    from core import video
    monkeypatch.setattr(video, "PTZ_RUNTIME_STATE", {**video.PTZ_RUNTIME_STATE, "auth_failed": False})
    return seen, cfg


def _now_start():
    return datetime.now().replace(microsecond=0)


class TestTheSetting:
    def test_on_unless_switched_off(self, client):
        assert ro.video_config()["video_start_photo_enabled"] is True

    def test_saving_settings_without_the_box_switches_it_off(self, logged_in_client):
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/settings/save", data={"_csrf_token": token})
        assert ro.video_config()["video_start_photo_enabled"] is False
        logged_in_client.post("/admin/settings/save", data={"_csrf_token": token, "video_start_photo_enabled": "1"})
        assert ro.video_config()["video_start_photo_enabled"] is True

    def test_the_camera_still_is_the_main_stream_of_the_channel(self):
        cfg = {"ptz_camera_url": "http://192.168.1.157/", "ptz_channel": 2}
        assert startphoto.camera_snapshot_url(cfg) == "http://192.168.1.157/ISAPI/Streaming/channels/201/picture"
        assert startphoto.camera_snapshot_url({"ptz_camera_url": ""}) == ""


class TestBooking:
    def test_a_start_clip_books_the_photo_and_its_thread(self, hut, monkeypatch):
        started = []
        monkeypatch.setattr(startphoto.threading, "Thread",
                            lambda target, args, name, daemon: type("T", (), {"start": lambda self: started.append(args)})())
        race_id, start = _race_with_start_at(_now_start() + timedelta(minutes=10))
        clip_id = ro.schedule_video_clip(race_id, "start", start, label="Start video")
        assert _row(clip_id)["photo_status"] == "pending"
        assert started == [(clip_id,)]
        # The start sequence books the same clip on every tick: one thread, still.
        assert ro.schedule_video_clip(race_id, "start", start, label="Start video") == clip_id
        assert started == [(clip_id,)]

    def test_finish_clips_have_no_photo(self, hut):
        race_id, start = _race_with_start_at(_now_start() + timedelta(minutes=10))
        clip_id = ro.schedule_video_clip(race_id, "finish", start, entry_id=None, label="Finish")
        assert not startphoto.arm_start_photo(clip_id)
        assert not _row(clip_id)["photo_status"]

    def test_switched_off_books_nothing(self, hut):
        seen, cfg = hut
        cfg["video_start_photo_enabled"] = False
        race_id, start = _race_with_start_at(_now_start() + timedelta(minutes=10))
        clip_id = _start_clip(race_id, start)
        assert not startphoto.arm_start_photo(clip_id)
        assert not _row(clip_id)["photo_status"]

    def test_a_start_long_gone_is_not_taken_after_a_restart(self, hut):
        race_id, start = _race_with_start_at(_now_start() - timedelta(hours=2))
        clip_id = _start_clip(race_id, start)
        startphoto.update_start_photo(clip_id, status="pending")
        assert not startphoto.arm_start_photo(clip_id)
        assert _row(clip_id)["photo_status"] == "error"


class TestTakingIt:
    def test_the_camera_still_is_published_at_once(self, hut):
        seen, cfg = hut
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.update_start_photo(clip_id, status="pending")
        startphoto.take_start_photo(clip_id)
        row = _row(clip_id)
        assert row["photo_status"] == "ready", row["photo_message"]
        assert seen["asked"] == ["http://192.168.1.157/ISAPI/Streaming/channels/101/picture"]
        assert row["photo_source"] == "camera" and row["photo_answer_ms"] is not None
        name = f"race{race_id}_start_{clip_id}_photo.jpg"
        assert seen["uploads"] == [(f"racevideos/race{race_id}/{name}", name, "image/jpeg")]
        assert row["photo_url"] == f"https://assets.example.org/racevideos/race{race_id}/{name}"
        # The published copy, and the camera's own kept beside it for the race officer.
        assert startphoto.resolve_photo_path(row["photo_file_path"]).name == name
        assert startphoto.resolve_photo_path(row["photo_raw_path"]).parent == appstate.VIDEO_CLIPS_DIR.resolve()
        assert not seen["recording"]

    def test_at_the_gun_the_camera_is_asked_before_anything_is_written(self, hut, monkeypatch):
        """The start horn logs to the database at the same moment, and a write
        waiting on it would push the photo past the signal."""
        seen, cfg = hut
        order = []
        real_update, real_camera = startphoto.update_start_photo, startphoto._curl_snapshot
        monkeypatch.setattr(startphoto, "update_start_photo",
                            lambda clip_id, **f: (order.append(("write", f.get("status"))), real_update(clip_id, **f)))
        monkeypatch.setattr(startphoto, "_curl_snapshot",
                            lambda *a: (order.append(("camera", None)), real_camera(*a))[1])
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        assert order[0] == ("camera", None), order
        assert _row(clip_id)["photo_status"] == "ready"

    def test_no_answer_from_the_camera_falls_back_to_the_recording(self, hut, monkeypatch):
        seen, cfg = hut
        monkeypatch.setattr(startphoto, "ffmpeg_executable", lambda cfg: "ffmpeg")
        monkeypatch.setattr(startphoto, "compose_start_photo",
                            lambda ffmpeg, raw, out, cfg, work, prior=(): (shutil.copyfile(raw, out), (False, "off"))[1])
        seen["camera_code"] = 500
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        row = _row(clip_id)
        assert row["photo_status"] == "ready", row["photo_message"]
        assert row["photo_source"] == "recording" and row["photo_answer_ms"] is None
        assert seen["recording"] == [datetime.fromisoformat(start)]

    def test_a_refused_login_stops_the_app_asking_the_camera(self, hut, monkeypatch):
        seen, cfg = hut
        monkeypatch.setattr(startphoto, "ffmpeg_executable", lambda cfg: "ffmpeg")
        monkeypatch.setattr(startphoto, "compose_start_photo",
                            lambda ffmpeg, raw, out, cfg, work, prior=(): (shutil.copyfile(raw, out), (False, "off"))[1])
        seen["camera_code"] = 401
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        assert _row(clip_id)["photo_source"] == "recording"
        # Hikvision locks the account after repeated bad logins.
        assert ro.video.ptz_auth_failure_is_paused()
        assert "refused" in startphoto.camera_problem(cfg)

    def test_too_late_for_the_camera_uses_the_recording(self, hut, monkeypatch):
        seen, cfg = hut
        monkeypatch.setattr(startphoto, "ffmpeg_executable", lambda cfg: "ffmpeg")
        monkeypatch.setattr(startphoto, "compose_start_photo",
                            lambda ffmpeg, raw, out, cfg, work, prior=(): (shutil.copyfile(raw, out), (False, "off"))[1])
        race_id, start = _race_with_start_at(_now_start() - timedelta(minutes=3))
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        assert seen["asked"] == []
        assert _row(clip_id)["photo_source"] == "recording"

    def test_a_postponed_start_is_not_photographed(self, hut):
        seen, cfg = hut
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start, status="cancelled")
        startphoto.take_start_photo(clip_id)
        assert _row(clip_id)["photo_status"] == "cancelled"
        assert seen["asked"] == []

    def test_a_start_time_since_moved_is_not_photographed(self, hut):
        seen, cfg = hut
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = ? WHERE id = ?",
                       ((datetime.now() + timedelta(minutes=20)).isoformat(timespec="seconds"), race_id))
            db.commit()
        startphoto.take_start_photo(clip_id)
        row = _row(clip_id)
        assert row["photo_status"] == "cancelled" and "changed" in row["photo_message"]
        assert seen["asked"] == []

    def test_a_failed_upload_is_kept_and_retry_sends_it(self, hut):
        seen, cfg = hut
        seen["upload_fails"] = startphoto.UPLOAD_ATTEMPTS
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        row = _row(clip_id)
        assert row["photo_status"] == "error" and "4G dropped" in row["photo_message"]
        assert startphoto.resolve_photo_path(row["photo_file_path"])
        assert startphoto.retry_start_photo_uploads(cfg) == 1
        assert _row(clip_id)["photo_status"] == "ready" and _row(clip_id)["photo_url"]


class TestThePublicPage:
    def _ready(self, hut):
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        return race_id, clip_id

    def test_the_tab_follows_course_analysis_and_shows_the_photo(self, hut):
        race_id, clip_id = self._ready(hut)
        html = hut_client(hut).get(f"/public/race/{race_id}").get_data(as_text=True)
        tabs = html.index('data-tab="ptab-analysis"'), html.index('data-tab="ptab-startphoto"')
        assert tabs[0] < tabs[1]
        assert _row(clip_id)["photo_url"] in html
        assert "the race officer's calls stand" in html

    def test_waiting_for_the_signal(self, hut):
        race_id, start = _race_with_start_at(_now_start() + timedelta(minutes=10))
        clip_id = _start_clip(race_id, start)
        startphoto.update_start_photo(clip_id, status="pending")
        html = hut_client(hut).get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'id="ptab-startphoto"' in html and "Taken at the start signal" in html

    def test_the_photo_is_public_during_the_race(self, hut):
        race_id, clip_id = self._ready(hut)
        resp = hut_client(hut).get(f"/public/start-photo/{clip_id}")
        assert resp.status_code == 302 and resp.headers["Location"] == _row(clip_id)["photo_url"]

    def test_served_by_the_hut_when_not_published_to_r2(self, hut):
        seen, cfg = hut
        cfg["video_public_provider"] = "off"
        race_id, clip_id = self._ready(hut)
        assert not _row(clip_id)["photo_url"]
        resp = hut_client(hut).get(f"/public/start-photo/{clip_id}")
        assert resp.status_code == 200 and resp.mimetype == "image/jpeg"

    def test_not_ready_is_not_found(self, hut):
        race_id, start = _race_with_start_at(_now_start() + timedelta(minutes=10))
        clip_id = _start_clip(race_id, start)
        startphoto.update_start_photo(clip_id, status="pending")
        assert hut_client(hut).get(f"/public/start-photo/{clip_id}").status_code == 404

    def test_the_page_reloads_once_when_the_photo_is_ready(self, hut):
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        race = ro.get_race(race_id)
        before, _ = ro.competitor_race_state_signature(race)
        startphoto.update_start_photo(clip_id, status="taking", message="Taking the start photo...")
        assert ro.competitor_race_state_signature(race)[0] == before
        startphoto.update_start_photo(clip_id, status="ready")
        assert ro.competitor_race_state_signature(race)[0] != before

    def test_deleting_the_race_deletes_its_photos(self, hut):
        race_id, clip_id = self._ready(hut)
        row = _row(clip_id)
        files = [startphoto.resolve_photo_path(row[k]) for k in ("photo_file_path", "photo_raw_path")]
        assert all(files)
        ro.delete_race_and_related_data(race_id)
        assert not any(f.exists() for f in files)


class TestTheRaceOfficer:
    def test_the_race_page_links_both_photos(self, hut, logged_in_client):
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        html = logged_in_client.get(f"/race/{race_id}", follow_redirects=True).get_data(as_text=True)
        assert "Start photo:" in html and _row(clip_id)["photo_url"] in html
        camera = f"/video/start-photo/{clip_id}/camera"
        assert camera in html
        assert logged_in_client.get(camera, follow_redirects=True).mimetype == "image/jpeg"

    def test_the_camera_original_is_not_public(self, hut):
        race_id, start = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, start)
        startphoto.take_start_photo(clip_id)
        resp = hut_client(hut).get(f"/video/start-photo/{clip_id}/camera")
        assert resp.status_code in (302, 401, 403)

    def test_settings_offers_the_switch_and_a_test(self, hut, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert 'name="video_start_photo_enabled"' in html
        assert "Save and take a test start photo" in html

    def test_a_test_photo_is_taken_and_kept_locally(self, hut, logged_in_client, monkeypatch):
        seen, cfg = hut
        result = startphoto.take_test_start_photo()
        assert result["ok"], result["message"]
        assert "800 x 450" in result["message"]
        assert seen["uploads"] == []                     # a test is never published
        resp = logged_in_client.get("/settings/video/start_photo_test.jpg", follow_redirects=True)
        assert resp.status_code == 200 and resp.mimetype == "image/jpeg"

    def test_a_test_with_no_camera_says_why(self, hut):
        seen, cfg = hut
        cfg["ptz_camera_url"] = ""
        result = startphoto.take_test_start_photo()
        assert not result["ok"] and "no camera address" in result["message"]


def hut_client(hut):
    """The Flask test client the `client` fixture made, anonymous."""
    return ro.app.test_client()


class TestTheDarkBuoy:
    """Into the sun the buoy is a silhouette: on the camera's still of 19 October
    most of it sat just under the old brightness floor of 55, and no line was
    drawn. The floor is 45 now."""

    def _frame(self, path: Path, value: int) -> Path:
        w, h = 1920, 1080
        a = np.full((h, w, 3), (70, 72, 75), dtype=np.uint8)          # grey sea, unsaturated
        cx, cy = int(0.55 * w), int(0.56 * h)
        yy, xx = np.ogrid[:h, :w]
        buoy = ((xx - cx) / 9) ** 2 + ((yy - cy) / 14) ** 2 <= 1
        scale = value / 255
        a[buoy] = (int(255 * scale), int(140 * scale), int(20 * scale))   # orange, V = value
        Image.fromarray(a).save(path)
        return path

    def test_a_backlit_buoy_is_found(self, tmp_path):
        found = odm.detect(Image.open(self._frame(tmp_path / "dark.png", 50)), odm.load_config(None))
        assert found.found and found.fx == pytest.approx(0.55, abs=0.01)

    def test_the_old_floor_missed_it(self, tmp_path):
        cfg = odm.load_config(None)
        cfg["color"]["val_min"] = 55
        assert not odm.detect(Image.open(self._frame(tmp_path / "dark.png", 50)), cfg).found


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is not installed")
class TestWithFFmpeg:
    def test_the_line_and_only_the_club_logo_go_on(self, client, tmp_path, monkeypatch):
        """Sponsors are left off: the photo is for reading the line."""
        raw = tmp_path / "camera.jpg"
        TestTheDarkBuoy()._frame(tmp_path / "frame.png", 200)
        Image.open(tmp_path / "frame.png").save(raw, quality=95)
        club = _jpeg(tmp_path / "club.png", size=(200, 120), colour=(200, 30, 30))
        sponsor = _jpeg(tmp_path / "sponsor.png", size=(200, 120), colour=(30, 30, 200))
        monkeypatch.setattr(ro.video, "_branding_logo_items",
                            lambda sponsor_count=8: [("club", club)] + [("sponsor", sponsor)][:sponsor_count])
        out = tmp_path / "photo.jpg"
        drawn, note = startphoto.compose_start_photo(FFMPEG, raw, out, {"video_startline_overlay_enabled": True}, tmp_path)
        assert drawn, note
        img = np.asarray(Image.open(out).convert("RGB")).astype(int)
        assert img.shape[:2] == (1080, 1920)
        top_left, top_right = img[30:80, 30:150].mean(axis=(0, 1)), img[30:80, -150:-30].mean(axis=(0, 1))
        assert top_left[0] > 120 and top_left[2] < 90            # the club logo
        assert abs(top_right - np.array([70, 72, 75])).max() < 25  # no sponsor
        assert not list(tmp_path.glob(".odm_line_*"))               # its line image cleared up

    def test_the_frame_at_the_start_comes_out_of_the_recording(self, tmp_path, monkeypatch):
        """Two 20 s segments, red then blue; the start 5 s into the blue one."""
        buffer = tmp_path / "buffer"
        buffer.mkdir()
        monkeypatch.setattr(appstate, "VIDEO_BUFFER_DIR", buffer)
        first = datetime(2026, 10, 19, 11, 29, 40)
        for start, colour in ((first, "red"), (first + timedelta(seconds=20), "blue"),
                              (first + timedelta(seconds=40), "green")):
            subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"color=c={colour}:s=320x180:r=25:d=20", "-c:v", "libx264", "-g", "50",
                            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                            str(buffer / f"{start:%Y%m%dT%H%M%S}.mp4")], check=True, timeout=60)
        dest, work = tmp_path / "frame.jpg", tmp_path / "work"
        work.mkdir()
        cfg = {"video_segment_seconds": 20}
        startphoto.frame_from_recording(cfg, FFMPEG, first + timedelta(seconds=25), dest, work)
        r, g, b = np.asarray(Image.open(dest).convert("RGB")).reshape(-1, 3).mean(axis=0)
        assert b > 150 and r < 80, (r, g, b)
        startphoto.frame_from_recording(cfg, FFMPEG, first + timedelta(seconds=17), dest, work)
        r, g, b = np.asarray(Image.open(dest).convert("RGB")).reshape(-1, 3).mean(axis=0)
        assert r > 150 and b < 80, (r, g, b)


class TestTheFleetInFrontOfTheBuoy:
    """Race 90 of the 2026 autumn series: at the gun the fleet was on the line
    and the buoy behind it, so no line -- just when it was wanted. It was in
    plain view ten seconds earlier. The buoy is anchored; find it either side
    of the gun and draw the line there."""

    def test_the_frames_that_agree_win(self):
        from core import startline
        good, also_good, sail = (0.462, 0.579, 0.57), (0.463, 0.574, 0.51), (0.30, 0.52, 0.90)
        assert startline.buoy_consensus([good, sail, also_good])[2] == 2
        fx, fy, _n = startline.buoy_consensus([good, sail, also_good, None])
        assert fx == pytest.approx(0.4625) and fy == pytest.approx(0.5765)
        assert startline.buoy_consensus([None, None]) is None
        assert startline.buoy_consensus([sail])[:2] == (0.30, 0.52)
        # One each: the more confident.
        assert startline.buoy_consensus([good, sail])[:2] == (0.30, 0.52)

    def test_a_photo_with_the_buoy_hidden_still_gets_its_line(self, tmp_path):
        from core import startline
        hidden = tmp_path / "gun.png"
        Image.new("RGB", (1920, 1080), (70, 72, 75)).save(hidden)
        assert startline.detect_buoy(str(hidden)) is None
        spec, note = startline.line_spec_for_image(str(hidden), str(tmp_path), prior=[(0.55, 0.56, 0.7), None])
        assert spec and Path(spec["green"]).is_file(), note
        assert "not in the photo itself" in note and "1 of 3 frames" in note
        startline.cleanup_spec(spec)
        spec, note = startline.line_spec_for_image(str(hidden), str(tmp_path))
        assert spec is None and "no confident buoy" in note

    def test_stills_before_the_gun_find_it_for_the_photo(self, hut, monkeypatch):
        import time as real_time
        seen, cfg = hut
        cfg["video_startline_overlay_enabled"] = True
        monkeypatch.setattr(startphoto.time, "sleep", real_sleep)
        monkeypatch.setattr(startphoto, "PRE_START_SAMPLES_S", (1.0, 0.5))
        monkeypatch.setattr(startphoto.startline, "startline_overlay_enabled", lambda cfg: True)
        monkeypatch.setattr(startphoto.startline, "detect_buoy", lambda path: (0.55, 0.56, 0.7))
        given = []

        def compose(ffmpeg, raw, out, cfg, work, prior=()):
            given.append(list(prior))
            shutil.copyfile(raw, out)
            return True, "drawn"
        monkeypatch.setattr(startphoto, "compose_start_photo", compose)
        start = datetime.now() + timedelta(seconds=2)
        race_id, when = _race_with_start_at(start.replace(microsecond=0))
        clip_id = _start_clip(race_id, when)
        startphoto.take_start_photo(clip_id)
        assert len(seen["asked"]) == 3, seen["asked"]          # two to find the buoy, one at the gun
        assert given == [[(0.55, 0.56, 0.7), (0.55, 0.56, 0.7)]]
        assert _row(clip_id)["photo_status"] == "ready"
        assert not list((appstate.VIDEO_RUNTIME_DIR / "start_photos").glob("**/before_*.jpg"))

    def test_too_late_for_them_costs_nothing(self, hut, monkeypatch):
        seen, cfg = hut
        monkeypatch.setattr(startphoto.startline, "startline_overlay_enabled", lambda cfg: True)
        race_id, when = _race_with_start_at(_now_start())
        clip_id = _start_clip(race_id, when)
        startphoto.take_start_photo(clip_id)
        assert len(seen["asked"]) == 1                           # only the photo
        assert _row(clip_id)["photo_status"] == "ready"


class TestABoatTakenForTheBuoy:
    def test_race_98s_wandering_boat_does_not_outvote_the_buoy(self):
        """Race 98 of the autumn series, from the full-quality clip: something
        on a boat three frames before the gun, wandering by 0.025, and the
        buoy itself found once, in the photo, more confidently."""
        from core import startline
        frames = [None, None, (0.681, 0.570, 0.39), (0.689, 0.545, 0.35), (0.672, 0.546, 0.38), None,
                  (0.548, 0.551, 0.51)]
        assert startline.buoy_consensus(frames)[:2] == (0.548, 0.551)

    def test_the_buoys_own_small_swing_still_agrees(self):
        from core import startline
        race_95 = [None, (0.475, 0.583, 0.51), (0.474, 0.583, 0.48), (0.468, 0.587, 0.43), None,
                   (0.475, 0.584, 0.48), (0.473, 0.588, 0.49)]
        assert startline.buoy_consensus(race_95)[2] == 5
