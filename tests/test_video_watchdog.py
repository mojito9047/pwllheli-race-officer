"""The rolling recorder is supervised, and its card says when it is not running.

Written after a race went unrecorded. FFmpeg exited around three in the morning
and nothing brought it back: `start_video_background_recorder` was called at app
start, when video settings were saved, and when a clip was scheduled or built,
so an overnight death lasted until somebody happened to save a setting. The
recorder's log shows one ending on 13 August and not starting again until a
settings save two days later, while the live preview (which did have a watchdog)
was restarted overnight and kept the dashboard looking healthy.

The race was lost to the other half of it: a recorder that stayed alive and
stopped writing at about 03:00. The trim went on deleting closed segments as they
aged out, all except the one FFmpeg still held open, which Windows will not let
anything delete -- so the race officer found a single orphaned 03:00 file, and the
clip build could only report, correctly and uselessly, that no buffered segments
covered the event.

Two properties are held here: the recorder comes back by itself, and the
dashboard looks wrong while it is not running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import video  # noqa: E402


@pytest.fixture(autouse=True)
def clean_recorder_state(reset_module_state):
    """The recorder's state is module-level, so each test starts from known."""
    video.VIDEO_RUNTIME_STATE.update({"process": None, "preview_process": None,
                                      "config_hash": None, "restarts": 0,
                                      "last_restart_at": None, "watchdog_started": False,
                                      "last_restart_log_at": 0.0, "started_at": None})
    yield


class TestTheWatchdogBringsItBack:
    def test_a_dead_recorder_is_restarted(self, monkeypatch):
        started = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: False)
        monkeypatch.setattr(video, "start_video_background_recorder",
                            lambda: started.append(True))
        assert video.video_watchdog_tick() is True
        assert started, "the watchdog did not start a recorder"
        assert video.VIDEO_RUNTIME_STATE["restarts"] == 1

    def test_a_live_recorder_is_left_alone(self, monkeypatch):
        """Restarting a healthy recorder would break the buffer it is writing."""
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: True)
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        assert video.video_watchdog_tick() is False
        assert video.VIDEO_RUNTIME_STATE["restarts"] == 0

    def test_but_the_buffer_is_still_trimmed_while_it_is_healthy(self, monkeypatch):
        """The trim runs inside the recorder's own "still running" branch, so a
        healthy tick must still reach it: this is the housekeeping that ages the
        buffer out, and it is the only thing that does."""
        called = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: True)
        monkeypatch.setattr(video, "start_video_background_recorder",
                            lambda: called.append("ordinary start"))
        video.video_watchdog_tick()
        assert called == ["ordinary start"], "the healthy path skipped its housekeeping"

    def test_it_does_nothing_when_recording_is_switched_off(self, monkeypatch):
        started = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": False})
        monkeypatch.setattr(video, "start_video_background_recorder",
                            lambda: started.append(True))
        assert video.video_watchdog_tick() is False
        assert started == []

    def test_it_goes_through_the_same_start_the_settings_page_uses(self, monkeypatch):
        """No second way to start a recorder: whatever is wrong with the camera
        fails the ordinary way and is logged the ordinary way."""
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: False)
        seen = []
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: seen.append(1))
        video.video_watchdog_tick()
        assert seen == [1]

    def test_a_restart_is_recorded_where_somebody_can_read_it(self, monkeypatch):
        logged = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: False)
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        monkeypatch.setattr(video, "log_activity",
                            lambda action, details="", user="system": logged.append(action))
        video.video_watchdog_tick()
        assert "video recorder restarted" in logged

    def test_the_loop_survives_a_tick_that_raises(self, monkeypatch):
        """A watchdog that can die is not one."""
        calls = []

        def exploding():
            calls.append(1)
            raise RuntimeError("camera on fire")

        monkeypatch.setattr(video, "video_watchdog_tick", exploding)
        monkeypatch.setattr(video.time, "sleep",
                            lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            video.video_watchdog_loop()
        assert calls == [1], "the loop stopped at the first failing tick"

    def test_the_thread_is_started_once(self, monkeypatch):
        made = []
        monkeypatch.setattr(video.threading, "Thread",
                            lambda *a, **k: made.append(k.get("name")) or type(
                                "T", (), {"start": lambda self: None})())
        video.start_video_watchdog()
        video.start_video_watchdog()
        assert made == ["video-recorder-watchdog"]


class TestTheDashboardSaysSo:
    def test_the_card_is_flagged_while_the_recorder_is_down(self, logged_in_client, monkeypatch):
        """It said "Video recorder is not running" in the same grey as everything
        else, all one Saturday morning."""
        from routes import pages
        # The dashboard route binds this at import, so patch it where the
        # route actually looks it up (see DEVELOPER_NOTES.md).
        monkeypatch.setattr(pages, "video_runtime_status",
                            lambda: {"ok": False, "enabled": True, "segment_count": 0,
                                     "message": "Video recorder is not running. FFmpeg exit code: 1.",
                                     "restarts": 3, "live_frame_age_seconds": None})
        page = logged_in_client.get("/admin").get_data(as_text=True)
        card = page[page.index("<h3>Video"):page.index("<h3>Video") + 700]
        assert "warn-pill" in card, "a dead recorder looked like a healthy one"
        assert "not running" in card
        assert "restarted 3" in card, "the restarts were not shown"

    def test_and_not_flagged_when_it_is_recording(self, logged_in_client, monkeypatch):
        from routes import pages
        # The dashboard route binds this at import, so patch it where the
        # route actually looks it up (see DEVELOPER_NOTES.md).
        monkeypatch.setattr(pages, "video_runtime_status",
                            lambda: {"ok": True, "enabled": True, "segment_count": 12,
                                     "message": "Video recorder running.", "restarts": 0,
                                     "live_frame_age_seconds": 2.0})
        page = logged_in_client.get("/admin").get_data(as_text=True)
        card = page[page.index("<h3>Video"):page.index("<h3>Video") + 700]
        assert "warn-pill" not in card

    def test_nor_when_video_is_switched_off_on_purpose(self, logged_in_client, monkeypatch):
        """Disabled is a decision, not a fault."""
        from routes import pages
        # The dashboard route binds this at import, so patch it where the
        # route actually looks it up (see DEVELOPER_NOTES.md).
        monkeypatch.setattr(pages, "video_runtime_status",
                            lambda: {"ok": False, "enabled": False, "segment_count": 0,
                                     "message": "Video recording is disabled.", "restarts": 0,
                                     "live_frame_age_seconds": None})
        page = logged_in_client.get("/admin").get_data(as_text=True)
        card = page[page.index("<h3>Video"):page.index("<h3>Video") + 700]
        assert "warn-pill" not in card


class TestItDoesNotShoutForever:
    """A camera left unplugged fails every time the watchdog looks. Two restarts
    a minute in the activity log is a log nobody can read the rest of."""

    def test_the_restart_is_logged_once_in_ten_minutes(self, monkeypatch):
        logged = []
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: False)
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        monkeypatch.setattr(video, "log_activity",
                            lambda action, details="", user="system": logged.append(details))
        for _ in range(5):
            video.video_watchdog_tick()
        assert len(logged) == 1, f"logged {len(logged)} times for one broken camera"
        # But it still keeps trying, and the count is there for whoever looks.
        assert video.VIDEO_RUNTIME_STATE["restarts"] == 5

    def test_the_count_reaches_the_dashboard(self, monkeypatch):
        monkeypatch.setattr(video, "video_config", lambda: {"video_enabled": True})
        monkeypatch.setattr(video, "video_process_is_running", lambda: False)
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        monkeypatch.setattr(video, "log_activity", lambda *a, **k: None)
        video.video_watchdog_tick()
        assert video.video_runtime_status()["restarts"] == 1


class TestARecorderThatIsAliveAndWritingNothing:
    """The other half of the same loss. An RTSP camera that goes away without
    closing the connection leaves FFmpeg sitting there: the process is alive, the
    buffer is empty, and the evidence is just as gone."""

    def test_a_stalled_recorder_is_restarted(self, monkeypatch):
        monkeypatch.setattr(video, "video_config",
                            lambda: {"video_enabled": True, "video_segment_seconds": 5})
        monkeypatch.setattr(video, "video_process_is_running", lambda: True)
        monkeypatch.setattr(video, "recent_video_segments", lambda max_age_seconds=None: [])
        monkeypatch.setattr(video, "stop_video_recorder_locked", lambda message: None)
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        monkeypatch.setattr(video, "log_activity", lambda *a, **k: None)
        import time as _time
        # A real started_at is epoch seconds; 0.0 would read as "never started".
        video.VIDEO_RUNTIME_STATE["started_at"] = _time.time() - 3600
        assert video.video_watchdog_tick() is True

    def test_but_not_one_that_has_only_just_started(self, monkeypatch):
        """Restarting a recorder that has not had time to write its first
        segment would guarantee the silence this is here to prevent."""
        import time as _time
        monkeypatch.setattr(video, "video_config",
                            lambda: {"video_enabled": True, "video_segment_seconds": 5})
        monkeypatch.setattr(video, "video_process_is_running", lambda: True)
        monkeypatch.setattr(video, "recent_video_segments", lambda max_age_seconds=None: [])
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        video.VIDEO_RUNTIME_STATE["started_at"] = _time.time()
        assert video.video_watchdog_tick() is False

    def test_nor_one_that_is_writing_segments(self, monkeypatch):
        from pathlib import Path as _Path
        monkeypatch.setattr(video, "video_config",
                            lambda: {"video_enabled": True, "video_segment_seconds": 5})
        monkeypatch.setattr(video, "video_process_is_running", lambda: True)
        monkeypatch.setattr(video, "recent_video_segments",
                            lambda max_age_seconds=None: [_Path("20260815T120000.mp4")])
        monkeypatch.setattr(video, "start_video_background_recorder", lambda: None)
        import time as _time
        video.VIDEO_RUNTIME_STATE["started_at"] = _time.time() - 3600
        assert video.video_watchdog_tick() is False

class TestFFmpegGivesUpOnASilentCamera:
    """The camera reboots to a schedule. FFmpeg, left alone, waits for it
    forever -- measured on the hut's own build, a black-holed RTSP address was
    still being waited on after 25 seconds with no timeout set and exited in 8.8
    with one. Waiting forever is what turns a two-minute reboot into a lost race,
    because the recorder keeps the segment it was writing open the whole time.
    """

    def rtsp_config(self, **over):
        cfg = {"video_source_type": "rtsp", "video_rtsp_url": "rtsp://cam/main",
               "video_preview_rtsp_url": "rtsp://cam/sub", "video_rtsp_timestamp_mode": "camera"}
        cfg.update(over)
        return cfg

    def test_the_recorder_is_told_to_give_up(self):
        args = video.video_input_args(self.rtsp_config(), preview=False)
        assert "-timeout" in args, "FFmpeg would wait for a silent camera forever"
        assert args[args.index("-timeout") + 1] == str(int(video.VIDEO_RTSP_TIMEOUT_SECONDS * 1_000_000))

    def test_and_so_is_the_preview(self):
        args = video.video_input_args(self.rtsp_config(), preview=True)
        assert "-timeout" in args

    def test_it_is_microseconds_because_ffmpeg_wants_microseconds(self):
        """Seconds here would be ten millionths of a second: every read would
        time out and the recorder would never keep a camera at all."""
        args = video.video_input_args(self.rtsp_config())
        assert int(args[args.index("-timeout") + 1]) == 10_000_000

    def test_it_comes_before_the_input_it_applies_to(self):
        """An input option after -i belongs to the next input, so it would be
        accepted, ignored, and change nothing."""
        args = video.video_input_args(self.rtsp_config())
        assert args.index("-timeout") < args.index("-i")

    def test_a_usb_camera_is_left_alone(self):
        """It is an RTSP socket option; a webcam has no socket."""
        args = video.video_input_args({"video_source_type": "usb", "video_usb_source": "USB Camera"})
        assert "-timeout" not in args

    def test_it_can_be_switched_off_without_editing_code(self, monkeypatch):
        monkeypatch.setattr(video, "VIDEO_RTSP_TIMEOUT_SECONDS", 0)
        assert "-timeout" not in video.video_input_args(self.rtsp_config())

    def test_the_recorder_command_carries_it_through(self):
        """The args are built in one place, but the command is what runs."""
        cmd = video.build_video_recorder_command("ffmpeg", self.rtsp_config(
            video_recording_mode="copy", video_segment_seconds=20))
        assert "-timeout" in cmd
