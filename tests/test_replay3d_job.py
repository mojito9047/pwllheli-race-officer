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


class TestWhichRacesHaveAFilm:
    """The index the public pages and the published results document read.

    One bucket listing answers for a whole season. The alternative -- a status
    read per race -- is a network call per row on a page competitors open on
    the water, and on a document that is read long after the hut is off.
    """

    BUCKET = {"account_id": "acc", "bucket": "buck", "access_key": "id",
              "secret_key": "secret", "public_base_url": "https://films.example"}

    def _listing(self, monkeypatch, objects):
        from core import r2, replay3d
        monkeypatch.setattr(replay3d, "bucket_config", lambda: dict(self.BUCKET))
        monkeypatch.setattr(r2, "list_objects", lambda *a, **k: list(objects))
        replay3d.forget_published_films()
        return replay3d.published_films()

    def test_a_film_is_found_and_carries_its_own_version(self, monkeypatch):
        """The token is the object's modification time, so nothing is recorded.

        The film's key is only the race id and it is served with a day of
        cache, so a re-render would otherwise sit behind the old copy.
        """
        films = self._listing(monkeypatch, [
            {"key": "replay3d/films/race_649.mp4", "size_bytes": 128832083,
             "last_modified": "2026-09-10T21:24:10.000Z"},
        ])
        assert films == {649: "https://films.example/replay3d/films/race_649.mp4?v=1789075450"}

    def test_an_empty_object_is_not_a_film(self, monkeypatch):
        """A link to nothing is worse than no link."""
        assert self._listing(monkeypatch, [
            {"key": "replay3d/films/race_649.mp4", "size_bytes": 0,
             "last_modified": "2026-09-10T21:24:10.000Z"},
        ]) == {}

    def test_anything_not_shaped_like_a_film_is_ignored(self, monkeypatch):
        """The prefix is shared, and one day something else will be put there."""
        assert self._listing(monkeypatch, [
            {"key": "replay3d/films/race_649.mp4.part", "size_bytes": 10, "last_modified": ""},
            {"key": "replay3d/films/notes.txt", "size_bytes": 10, "last_modified": ""},
            {"key": "replay3d/films/race_.mp4", "size_bytes": 10, "last_modified": ""},
        ]) == {}

    def test_an_unreadable_bucket_costs_the_links_and_nothing_else(self, monkeypatch):
        from core import r2, replay3d

        def boom(*a, **k):
            raise RuntimeError("R2 is having a day")

        monkeypatch.setattr(replay3d, "bucket_config", lambda: dict(self.BUCKET))
        monkeypatch.setattr(r2, "list_objects", boom)
        replay3d.forget_published_films()
        assert replay3d.published_films() == {}

    def test_no_bucket_configured_is_not_an_error(self, monkeypatch):
        from core import replay3d
        monkeypatch.setattr(replay3d, "bucket_config", dict)
        replay3d.forget_published_films()
        assert replay3d.published_films() == {}


class TestAReRenderDoesNotLeaveTheOldLinkUp:
    """Re-rendering a race replaces a film whose key never changes.

    Which races have a film is a ten-minute memo over one bucket listing, and
    each link carries the object's modification time so a new cut cannot sit
    behind the old one in a cache. Both are right on their own and wrong
    together: for ten minutes after a render lands, every page still offers the
    previous cut's token -- and those links are served with a day of cache, so
    a competitor clicking inside that window is pinned to yesterday's film
    until tomorrow. The race office sees it first, having just watched the
    render finish and pressed Watch the film.
    """

    BUCKET = {"account_id": "acc", "bucket": "buck", "access_key": "id",
              "secret_key": "secret", "public_base_url": "https://films.example"}
    OLD = "2026-09-10T21:24:10.000Z"        # v=1789075450
    NEW = "2026-09-12T13:38:51.000Z"        # v=1789220331

    def _wire(self, monkeypatch, in_bucket, state, finished_ago):
        """A bucket holding one film, and a renderer saying what it just did.

        ``in_bucket`` is a one-item list so a test can replace the film under
        the memo, which is what a re-render does. Times are offsets from now,
        because the memo's own freshness is measured against the real clock.
        """
        from core import r2, replay3d
        listed = {"n": 0}

        def list_objects(*a, **k):
            listed["n"] += 1
            return [{"key": "replay3d/films/race_69.mp4", "size_bytes": 108272940,
                     "last_modified": in_bucket[0]}]

        status = {"state": state, "updated_at": time.time() - finished_ago}
        monkeypatch.setattr(replay3d, "bucket_config", lambda: dict(self.BUCKET))
        monkeypatch.setattr(r2, "list_objects", list_objects)
        monkeypatch.setattr(replay3d, "read_heartbeat", lambda: {"renderer": "RENDERBOX"})
        monkeypatch.setattr(replay3d, "renderer_is_up", lambda *a, **k: True)
        monkeypatch.setattr(replay3d, "status_is_stale", lambda *a, **k: False)
        monkeypatch.setattr(replay3d, "read_status", lambda race_id: dict(status))
        replay3d.forget_published_films()
        return listed

    @staticmethod
    def _listed_ago(seconds):
        """Pretend the memo was filled this long ago, rather than just now."""
        from core import replay3d
        replay3d._FILM_INDEX["at"] = time.time() - seconds

    def test_the_new_film_is_offered_as_soon_as_the_render_is_done(self, monkeypatch):
        from core import replay3d
        in_bucket = [self.OLD]
        self._wire(monkeypatch, in_bucket, "done", finished_ago=60)

        assert replay3d.film_url_for(69).endswith("?v=1789075450")
        self._listed_ago(300)       # the listing is older than the render
        in_bucket[0] = self.NEW     # which has since landed in the bucket

        assert replay3d.dashboard_status(69)["state"] == "done"
        assert replay3d.film_url_for(69).endswith("?v=1789220331"), \
            "the page is still offering the film the re-render replaced"

    def test_a_render_older_than_the_listing_does_not_re_list_the_bucket(self, monkeypatch):
        """Otherwise the memo is no memo: the card polls this every few seconds.

        A finished render stays "done" in the bucket for ever, so the common
        case by far is a status describing a film the listing already has.
        """
        from core import replay3d
        listed = self._wire(monkeypatch, [self.NEW], "done", finished_ago=300)

        replay3d.published_films()
        self._listed_ago(60)        # listed after that render landed
        assert listed["n"] == 1
        for _ in range(5):
            replay3d.dashboard_status(69)
            replay3d.film_url_for(69)
        assert listed["n"] == 1, "the film index is being re-listed on every poll"

    def test_a_render_still_running_leaves_the_index_alone(self, monkeypatch):
        """Nothing has replaced the film yet, so the current link is the right one."""
        from core import replay3d
        listed = self._wire(monkeypatch, [self.OLD], "rendering", finished_ago=60)

        replay3d.published_films()
        self._listed_ago(300)
        assert listed["n"] == 1
        replay3d.dashboard_status(69)
        replay3d.film_url_for(69)
        assert listed["n"] == 1
