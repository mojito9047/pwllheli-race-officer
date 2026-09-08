"""How long an R2 upload is allowed to take.

Reported from the hut: every public start video failed with

    Could not upload public video after 4 attempts:
    Built-in upload failed: Network error: The write operation timed out
    | curl fallback failed: ... timed out after 90 seconds

and nothing appeared in the bucket. Both timeouts were flat numbers — 60 seconds
for the built-in uploader, 90 for the curl fallback — which are fine on a desk
and hopeless from a hut on 4G shared with a caravan park on a Saturday evening.
A race clip is tens of megabytes; it was being cut off with most of it sent.

An S3 PUT is atomic, which is why the bucket was empty rather than holding a
partial file: there is nothing to resume, so each of the four attempts started
again from nothing.
"""
from __future__ import annotations

import core.video as video


class TestTheAllowanceFollowsTheFile:
    def test_a_small_object_still_gets_a_sensible_floor(self):
        """The live JPEG is a few tens of kilobytes; it should not inherit a
        timeout computed as though it were nothing."""
        assert video.r2_upload_timeout_s(50_000) >= video.R2_UPLOAD_BASE_TIMEOUT_S

    def test_a_race_clip_gets_minutes_rather_than_ninety_seconds(self):
        """20 MB at the assumed floor throughput needs about seventeen minutes."""
        allowance = video.r2_upload_timeout_s(20 * 1024 * 1024)
        assert allowance > 15 * 60
        assert allowance >= (20 * 1024 * 1024) / video.R2_MIN_BYTES_PER_S

    def test_the_allowance_grows_with_the_file(self):
        assert video.r2_upload_timeout_s(50 * 1024 * 1024) > video.r2_upload_timeout_s(5 * 1024 * 1024)

    def test_it_is_bounded_so_a_wedged_upload_cannot_hold_the_lock_for_ever(self):
        """Uploads are serialised, so one that never returns blocks every clip
        behind it."""
        assert video.r2_upload_timeout_s(10 * 1024 ** 3) == video.R2_UPLOAD_MAX_TIMEOUT_S

    def test_rubbish_sizes_do_not_raise(self):
        for bad in (None, "", -5, "big"):
            assert video.r2_upload_timeout_s(bad) >= video.R2_UPLOAD_BASE_TIMEOUT_S


class TestCurlGivesUpOnADeadLinkNotASlowOne:
    """The distinction the flat timeout could not make.

    A slow upload that is still moving should be left alone; one that has stopped
    should fail quickly rather than sit out a twenty-minute allowance and then
    burn three more attempts doing the same.
    """

    def _cmd(self, monkeypatch, size_bytes):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            return _Proc()

        monkeypatch.setattr(video.subprocess, "run", fake_run)
        monkeypatch.setattr(video.shutil, "which", lambda name: "curl")
        # A real-shaped account id: the validator requires 32 hex characters.
        cfg = {"video_public_r2_account_id": "0" * 32, "video_public_r2_bucket": "racevideos",
               "video_public_r2_access_key_id": "key", "video_public_r2_secret_access_key": "secret",
               "video_public_r2_public_base_url": "https://videos.example.org"}
        video._upload_bytes_to_r2_with_curl(cfg, "race57/clip.mp4", b"x" * size_bytes,
                                            "video/mp4", "public, max-age=31536000, immutable")
        return captured

    def test_it_asks_curl_to_abort_only_on_a_stall(self, monkeypatch):
        cmd = self._cmd(monkeypatch, 1000)["cmd"]
        assert "--speed-limit" in cmd and "--speed-time" in cmd
        i = cmd.index("--speed-limit")
        assert cmd[i + 1] == str(video.R2_STALL_BYTES_PER_S)
        j = cmd.index("--speed-time")
        assert cmd[j + 1] == str(video.R2_STALL_SECONDS)

    def test_a_connect_timeout_keeps_a_dead_endpoint_quick(self, monkeypatch):
        cmd = self._cmd(monkeypatch, 1000)["cmd"]
        assert "--connect-timeout" in cmd

    def test_the_process_timeout_is_only_a_backstop_past_the_allowance(self, monkeypatch):
        size = 20 * 1024 * 1024
        got = self._cmd(monkeypatch, size)
        assert got["timeout"] > video.r2_upload_timeout_s(size)

    def test_the_old_ninety_seconds_is_gone(self, monkeypatch):
        """The number in the reported failure."""
        assert self._cmd(monkeypatch, 20 * 1024 * 1024)["timeout"] > 90
