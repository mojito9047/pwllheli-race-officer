"""The render job and status file the hut writes for a 3D replay.

The hut cannot render a film in a useful time, so it exports the race and puts
a job where a render machine can find it. The renderer never talks back: it
writes progress beside the job, and the app reads that. These cover the parts
of that contract that are pure logic, and one that is easy to get wrong -- a
renderer that dies mid-job leaves a healthy-looking status behind.
"""
from __future__ import annotations

import time

from core import replay3d


class TestWhereThingsLiveInTheBucket:
    def test_the_keys_are_stable_and_separate(self):
        """The app builds these and the renderer parses them; drifting is a silent break."""
        assert replay3d.job_key(69) == "replay3d/jobs/race_69.json"
        assert replay3d.status_key(69) == "replay3d/status/race_69.json"
        assert replay3d.film_key(69) == "replay3d/films/race_69.mp4"

    def test_a_race_id_is_coerced(self):
        """Race ids arrive from a form as strings often enough to be worth it."""
        assert replay3d.job_key("69") == replay3d.job_key(69)

    def test_the_heartbeat_is_not_per_race(self):
        """It answers "is there a renderer at all", which no single job can."""
        assert "race" not in replay3d.HEARTBEAT_KEY
        assert replay3d.HEARTBEAT_KEY.startswith(replay3d.STATUS_PREFIX)


class TestTheJob:
    def _scene(self):
        return {"format": replay3d.SCENE_FORMAT, "race": {"id": 69, "name": "R9 Summer"},
                "boats": [], "videos": []}

    def test_it_carries_the_scene_with_it(self):
        """One object, not two: a renderer must never see a job whose scene is missing."""
        job = replay3d.build_job(self._scene())
        assert job["boats"] == [] and job["race"]["name"] == "R9 Summer"
        assert job["job"]["race_id"] == 69

    def test_it_names_where_the_film_goes(self):
        job = replay3d.build_job(self._scene())
        assert job["job"]["out_key"] == replay3d.film_key(69)

    def test_a_job_names_no_terrain(self):
        """The coastline is the render machine's business, not the job's.

        It is the same for every race at a club, so the renderer works out
        which map tiles a scene covers and keeps them in a cache of its own.
        A job that named terrain would be a job that could name the wrong
        terrain, and something somebody had to build before the first film.
        """
        assert "assets" not in replay3d.build_job(self._scene())["job"]

    def test_the_render_settings_travel_with_the_job(self):
        """So a re-render at a different speed does not need the renderer changing."""
        job = replay3d.build_job(self._scene(), speed=20.0, slow_step=1)
        assert job["job"]["render"] == {"speed": 20.0, "slow_step": 1}


class TestTheStatus:
    def test_a_new_job_starts_queued_with_no_progress(self):
        st = replay3d.blank_status(69)
        assert st["state"] == "queued" and st["progress"] == 0.0 and st["race_id"] == 69

    def test_an_unknown_state_is_not_stored(self):
        """The app renders these; a typo should not become a state it must handle."""
        assert replay3d.blank_status(69, "sideways")["state"] == "queued"

    def test_every_state_the_renderer_may_report_is_known(self):
        for state in ("queued", "claimed", "rendering", "composing", "uploading", "done", "failed"):
            assert state in replay3d.JOB_STATES


class TestASilentlyDeadRenderer:
    """The failure that actually happens: the render box is off, or it crashed."""

    def test_a_working_job_that_stopped_reporting_is_stale(self):
        st = replay3d.blank_status(69, "rendering")
        st["updated_at"] = time.time() - 3600
        assert replay3d.status_is_stale(st) is True

    def test_a_job_reporting_normally_is_not(self):
        assert replay3d.status_is_stale(replay3d.blank_status(69, "rendering")) is False

    def test_a_finished_job_may_sit_there_for_ever(self):
        for state in ("done", "failed", "queued"):
            st = replay3d.blank_status(69, state)
            st["updated_at"] = time.time() - 86400
            assert replay3d.status_is_stale(st) is False, state

    def test_no_status_at_all_is_not_stale(self):
        """Nothing has been asked for yet, which is a different thing from broken."""
        assert replay3d.status_is_stale(None) is False


class TestOneClipOnScreenAtATime:
    """The hut cuts a clip per finishing boat, so a close finish leaves overlaps."""

    def _clip(self, kind, t_start, t_end, t_event):
        return {"kind": kind, "t_start": t_start, "t_end": t_end, "t_event": t_event}

    def test_two_clips_of_the_same_moment_become_one(self):
        clips = [self._clip("finish", 100, 220, 160), self._clip("finish", 102, 222, 162)]
        assert len(replay3d.trim_video_windows(clips)) == 1

    def test_overlapping_windows_are_trimmed_rather_than_dropped(self):
        clips = [self._clip("finish", 100, 220, 160), self._clip("finish", 180, 300, 240)]
        out = replay3d.trim_video_windows(clips)
        assert len(out) == 2
        assert out[1]["t_start"] == out[0]["t_end"], "the second must start where the first ends"

    def test_a_window_trimmed_to_nothing_is_dropped(self):
        """Better no picture than a panel that flashes on for two seconds."""
        clips = [self._clip("finish", 100, 220, 160), self._clip("finish", 150, 223, 200)]
        assert len(replay3d.trim_video_windows(clips)) == 1

    def test_they_come_back_in_time_order(self):
        clips = [self._clip("finish", 400, 520, 460), self._clip("start", 100, 220, 160)]
        out = replay3d.trim_video_windows(clips)
        assert [c["kind"] for c in out] == ["start", "finish"]


class TestTheWindOverTheRace:
    """One mean direction for an eighty-minute race is a fiction.

    Race 69's mean was 162 degrees; the wind actually went from 122 to 196 and
    built from 2 knots to 8. The fleet was trimmed to the wind of the moment,
    so the scene carries the series and the renderer follows it.
    """

    def _wind(self, twd, tws=None, step=30.0):
        return {"twd_deg": 180.0, "tws_kn": 5.0,
                "series": {"step_s": step, "count": len(twd), "twd": twd,
                           "tws": tws if tws is not None else [5.0] * len(twd),
                           "gust": [6.0] * len(twd)}}

    def test_it_reads_the_bin_for_that_moment(self):
        wind = self._wind([100.0, 200.0, 300.0])
        assert replay3d.wind_at(wind, 0)[0] == 100.0
        assert replay3d.wind_at(wind, 45)[0] == 200.0
        assert replay3d.wind_at(wind, 75)[0] == 300.0

    def test_a_gap_holds_the_last_known_wind(self):
        """Which is what wind does; it does not stop because the station missed a poll."""
        wind = self._wind([100.0, None, None, 400.0])
        assert replay3d.wind_at(wind, 45)[0] == 100.0
        assert replay3d.wind_at(wind, 75)[0] == 100.0
        assert replay3d.wind_at(wind, 105)[0] == 400.0

    def test_past_the_end_holds_the_last_bin(self):
        """Post-roll runs past the last sample and still has to draw sails."""
        assert replay3d.wind_at(self._wind([100.0, 200.0]), 9999)[0] == 200.0

    def test_a_scene_with_no_series_falls_back_to_the_mean(self):
        """Older scene files have no series and must still render."""
        assert replay3d.wind_at({"twd_deg": 170.0, "tws_kn": 4.0}, 600) == (170.0, 4.0)

    def test_a_leading_gap_falls_back_to_the_mean(self):
        wind = self._wind([None, None, 300.0])
        assert replay3d.wind_at(wind, 0)[0] == 180.0, "the race mean, not None"


class TestReadingBackFromTheBucket:
    """The return path. The renderer cannot call the hut, so the hut reads R2.

    Everything the renderer writes is small JSON, and a status nobody has
    written yet is the normal case rather than a fault.
    """

    def test_a_missing_object_reads_as_no_news(self, monkeypatch):
        from core import r2
        monkeypatch.setattr(r2, "signed_request",
                            lambda *a, **k: (404, {}, b"<Error>NoSuchKey</Error>"))
        assert r2.get_object("acc", "buck", "k", "id", "secret") is None
        assert r2.get_json("acc", "buck", "k", "id", "secret") is None

    def test_json_comes_back_parsed(self, monkeypatch):
        from core import r2
        monkeypatch.setattr(r2, "signed_request",
                            lambda *a, **k: (200, {}, b'{"state": "rendering", "progress": 0.4}'))
        assert r2.get_json("acc", "buck", "k", "id", "secret")["state"] == "rendering"

    def test_half_written_json_reads_as_no_news_too(self, monkeypatch):
        """A status read mid-upload must not take out the card that shows it."""
        from core import r2
        monkeypatch.setattr(r2, "signed_request", lambda *a, **k: (200, {}, b'{"state": "rend'))
        assert r2.get_json("acc", "buck", "k", "id", "secret") is None

    def test_something_that_is_not_an_object_is_rejected(self, monkeypatch):
        from core import r2
        monkeypatch.setattr(r2, "signed_request", lambda *a, **k: (200, {}, b'[1, 2, 3]'))
        assert r2.get_json("acc", "buck", "k", "id", "secret") is None

    def test_a_real_failure_still_raises(self, monkeypatch):
        """403 is a broken key, not an absent file, and must not read as empty."""
        import pytest
        from core import r2
        monkeypatch.setattr(r2, "signed_request", lambda *a, **k: (403, {}, b""))
        with pytest.raises(RuntimeError):
            r2.get_object("acc", "buck", "k", "id", "secret")


class TestTheRenderMachineCarriesNoClubData:
    """The renderer must not reach the app, and this is easy to undo by accident.

    ``core.appstate`` reads courses.json, marks.json and start_finish.json at
    import time. One convenient ``from core.replay3d import ...`` in the
    renderer is therefore enough to require a copy of the club's data
    directory on every render machine -- and to stop the renderer starting at
    all on a machine that has none. Checked in a subprocess because by the
    time this test file runs, the app has long since been imported.
    """

    def test_importing_the_renderer_does_not_import_the_app(self):
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys; sys.path.insert(0, 'scripts/replay3d'); sys.path.insert(0, '.');"
            "import renderer;"
            "print(','.join(sorted(m for m in sys.modules if m.startswith('core.'))))"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=root,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        pulled = set(filter(None, out.stdout.strip().split(",")))
        assert pulled == {"core.r2", "core.replay3d_protocol"}, (
            f"the renderer now pulls in {sorted(pulled)}; anything reaching core.appstate "
            f"makes every render machine need the club's data directory")
