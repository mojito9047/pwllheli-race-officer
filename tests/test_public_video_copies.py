"""The public web copy of a race video: whole, or not published.

Race 96 (4 October 2026): the start's public copy was 5 s of a 139 s clip and
MOJITO BACH's finish 11 s of 160 s, both marked ready and uploaded, and the 3D
replay then held one frame of the finish for over a minute. The evidence clips
were whole. A shared start-line image was rewritten under a running encode, and
FFmpeg exits 0 for an encode that stopped early -- so "it ran" was taken as "it
is all there".
"""
from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest

import app as ro
from core import appstate, video


def _mvhd_mp4(path: Path, seconds: float, version: int = 0, timescale: int = 1000) -> Path:
    """The smallest file the duration reader has to understand: ftyp, then moov/mvhd."""
    duration = int(round(seconds * timescale))
    if version == 1:
        body = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        body = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, timescale, duration)
    body += b"\x00" * 80                                   # the rest of mvhd, unread
    mvhd = struct.pack(">I4s", 8 + len(body), b"mvhd") + body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x02\x00"
    path.write_bytes(ftyp + moov)
    return path


class TestHowLongAnMp4Is:
    def test_from_its_movie_header(self, tmp_path):
        assert video.mp4_duration_seconds(_mvhd_mp4(tmp_path / "a.mp4", 139.15)) == pytest.approx(139.15)
        assert video.mp4_duration_seconds(_mvhd_mp4(tmp_path / "b.mp4", 5.48, version=1)) == pytest.approx(5.48)

    def test_not_a_movie_is_not_a_length(self, tmp_path):
        junk = tmp_path / "junk.mp4"
        junk.write_bytes(b"not a movie at all")
        assert video.mp4_duration_seconds(junk) is None
        assert video.mp4_duration_seconds(tmp_path / "missing.mp4") is None

    def test_a_copy_short_of_its_evidence(self, tmp_path):
        evidence = _mvhd_mp4(tmp_path / "e.mp4", 159.5)
        assert video.public_copy_is_short(_mvhd_mp4(tmp_path / "p.mp4", 10.9), evidence) == (
            pytest.approx(10.9), pytest.approx(159.5))
        # Re-encoding moves the length by a frame or two: that is a whole copy.
        assert video.public_copy_is_short(_mvhd_mp4(tmp_path / "w.mp4", 159.48), evidence) is None


def _clip(race_id: int = 96, clip_id: int = 393) -> int:
    evidence = appstate.VIDEO_CLIPS_DIR / f"race{race_id}_manual_horn_{clip_id}.mp4"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    _mvhd_mp4(evidence, 159.5)
    now = "2026-10-04T14:56:03"
    with ro.get_db() as db:
        db.execute("INSERT INTO video_clips (id, race_id, clip_type, event_time, pre_seconds, post_seconds,"
                   " status, label, file_path, created_at, updated_at, public_status)"
                   " VALUES (?, ?, 'manual_horn', ?, 60, 60, 'ready', 'Manual horn', ?, ?, ?, 'pending')",
                   (clip_id, race_id, now, str(evidence), now, now))
        db.commit()
    return clip_id


@pytest.fixture()
def publishing(client, monkeypatch, tmp_path):
    """R2 ready, the start line on, and FFmpeg and the uploader replaced by
    stand-ins that record what they were asked to do."""
    seen = {"encodes": [], "uploads": [], "public_lengths": [], "served": {}, "running": 0, "most": 0}
    monkeypatch.setattr(video, "video_public_r2_ready", lambda cfg: True)
    monkeypatch.setattr(video.startline, "startline_overlay_enabled", lambda cfg: True)

    def spec(ffmpeg, evidence, clip_type, offset, out_dir):
        line = Path(out_dir) / ".odm_green_test.png"
        line.write_bytes(b"png")
        return {"green": str(line), "red": None, "offset": offset, "is_start": False}, "drawn"
    monkeypatch.setattr(video.startline, "build_startline_spec", spec)

    def encode(cmd, **kwargs):
        seen["running"] += 1
        seen["most"] = max(seen["most"], seen["running"])
        try:
            out = Path(cmd[-1])
            with_line = any(".odm_green_" in str(part) for part in cmd)
            seen["encodes"].append({"out": out, "with_line": with_line, "kwargs": kwargs})
            length = seen["public_lengths"].pop(0) if seen["public_lengths"] else 159.5
            if length is None:                         # an encode cut off: no movie header
                out.write_bytes(b"\x00\x00\x00\x08mdat")
            else:
                _mvhd_mp4(out, length)
            if seen.get("slow"):
                time.sleep(seen["slow"])
        finally:
            seen["running"] -= 1
    monkeypatch.setattr(video.subprocess, "run", encode)
    # What the club's public address says it serves; nothing is fetched.
    monkeypatch.setattr(video, "published_size", lambda url, timeout=15.0: seen["served"].get(url))

    def upload(clip_id, clip, path, cfg, attempts=3):
        seen["uploads"].append({"clip": clip_id, "key": clip["public_object_key"], "path": path})
        video.update_video_clip_public_status(clip_id, "ready", "uploaded", "", "", f"https://x/{clip['public_object_key']}")
        return True
    monkeypatch.setattr(video, "upload_public_video_file_with_retries", upload)
    cfg = {"video_public_provider": "r2", "video_public_quality": "720p", "video_public_r2_prefix": "racevideos"}
    return seen, cfg


def _status(clip_id):
    with ro.get_db() as db:
        return dict(db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone())


class TestPublishingAPublicCopy:
    def test_a_whole_copy_is_published(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        evidence = Path(_status(clip_id)["file_path"])
        video.publish_public_video_clip(clip_id, evidence, cfg, "ffmpeg")
        row = _status(clip_id)
        assert [e["with_line"] for e in seen["encodes"]] == [True], row["public_message"]
        assert len(seen["uploads"]) == 1 and row["public_status"] == "ready", row["public_message"]

    def test_a_short_copy_is_made_again_without_the_line(self, publishing):
        seen, cfg = publishing
        seen["public_lengths"] = [10.9, 159.5]
        clip_id = _clip()
        video.publish_public_video_clip(clip_id, Path(_status(clip_id)["file_path"]), cfg, "ffmpeg")
        assert [e["with_line"] for e in seen["encodes"]] == [True, False]
        assert _status(clip_id)["public_status"] == "ready"

    def test_a_copy_that_stays_short_is_never_published(self, publishing):
        seen, cfg = publishing
        seen["public_lengths"] = [10.9, 10.9]
        clip_id = _clip()
        video.publish_public_video_clip(clip_id, Path(_status(clip_id)["file_path"]), cfg, "ffmpeg")
        row = _status(clip_id)
        assert not seen["uploads"]
        assert row["public_status"] == "error"
        assert "is 11 s long against 160 s of evidence, so it was not published" in row["public_message"]

    def test_its_line_images_do_not_outlive_it(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        video.publish_public_video_clip(clip_id, Path(_status(clip_id)["file_path"]), cfg, "ffmpeg")
        scratch = appstate.VIDEO_RUNTIME_DIR / "public_work"
        assert not any(scratch.rglob("*")), "the clip's own scratch folder is removed"
        assert not list((appstate.VIDEO_CLIPS_DIR / "public").glob(".odm_*"))


class TestRepairingShortCopies:
    def test_a_short_published_copy_is_rebuilt_under_a_new_name(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        short = video.public_clip_output_path(_status(clip_id))
        short.parent.mkdir(parents=True, exist_ok=True)
        _mvhd_mp4(short, 10.9)
        old_key = "racevideos/race96/" + short.name
        video.update_video_clip_public_status(clip_id, "ready", "uploaded", str(short), old_key,
                                              f"https://x/{old_key}")
        assert video.rebuild_short_public_copies(cfg) == 1
        row = _status(clip_id)
        assert seen["uploads"][-1]["key"] != old_key, "a new address, past the year-long cache"
        assert row["public_url"].endswith(seen["uploads"][-1]["key"])
        assert not short.exists(), "the short copy goes once its replacement is up"
        assert video.short_public_copies() == []

    def test_a_whole_copy_is_left_alone(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        whole = video.public_clip_output_path(_status(clip_id))
        whole.parent.mkdir(parents=True, exist_ok=True)
        _mvhd_mp4(whole, 159.48)
        video.update_video_clip_public_status(clip_id, "ready", "uploaded", str(whole), "k", "https://x/k")
        assert video.rebuild_short_public_copies(cfg) == 0
        assert not seen["encodes"]

    def test_the_old_codes_leftover_images_are_cleared(self, client):
        public = appstate.VIDEO_CLIPS_DIR / "public"
        public.mkdir(parents=True, exist_ok=True)
        old = public / ".odm_green_15316.png"
        fresh = public / ".odm_green_99999.png"
        keep = public / "race96_start_388_public.mp4"
        for path in (old, fresh, keep):
            path.write_bytes(b"x")
        long_ago = time.time() - 7200
        os.utime(old, (long_ago, long_ago))
        os.utime(keep, (long_ago, long_ago))
        assert video.remove_stray_overlay_images() == 1
        assert not old.exists() and fresh.exists() and keep.exists()



class TestRace97:
    """17 October 2026: the hut was restarted in the middle of six finish-clip
    encodes it was running at once. The retry button then uploaded the
    half-written files as they were -- five unplayable videos, and a render
    machine that failed on every attempt -- and two short ones."""

    def test_an_encode_cut_off_is_never_published(self, publishing):
        seen, cfg = publishing
        seen["public_lengths"] = [None, None]
        clip_id = _clip()
        video.publish_public_video_clip(clip_id, Path(_status(clip_id)["file_path"]), cfg, "ffmpeg")
        row = _status(clip_id)
        assert not seen["uploads"] and row["public_status"] == "error"
        assert "cannot be read as a video" in row["public_message"]

    def test_it_is_written_under_another_name_until_it_is_whole(self, publishing):
        seen, cfg = publishing
        seen["public_lengths"] = [10.9, 10.9]
        clip_id = _clip()
        final = video.public_clip_output_path(_status(clip_id))
        video.publish_public_video_clip(clip_id, Path(_status(clip_id)["file_path"]), cfg, "ffmpeg")
        assert all(e["out"].name.endswith(".part.mp4") for e in seen["encodes"])
        assert not final.exists(), "nothing ever appears under the name the retry button uploads"
        assert not list(final.parent.glob("*.part.mp4")), "and the half-made file goes"

    def test_encodes_run_one_at_a_time_below_the_apps_priority(self, publishing):
        import threading
        seen, cfg = publishing
        seen["slow"] = 0.2
        clips = [_clip(clip_id=n) for n in (399, 400, 401)]
        threads = [threading.Thread(target=video.publish_public_video_clip,
                                    args=(n, Path(_status(n)["file_path"]), cfg, "ffmpeg")) for n in clips]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(seen["encodes"]) == 3 and seen["most"] == 1
        assert all(set(e["kwargs"]) & {"creationflags", "preexec_fn"} for e in seen["encodes"])

    def test_retry_does_not_upload_a_half_written_copy(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        half = video.public_clip_output_path(_status(clip_id))
        half.parent.mkdir(parents=True, exist_ok=True)
        half.write_bytes(b"\x00\x00\x00\x08mdat")
        video.update_video_clip_public_status(clip_id, "processing", "Creating public web video...", str(half))
        video.retry_public_video_uploads_once(cfg)
        assert len(seen["encodes"]) == 1, "made again from the evidence"
        assert seen["uploads"] and seen["uploads"][0]["path"] != half
        assert not half.exists()

    def test_retry_uploads_a_whole_copy_as_it_is(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        whole = video.public_clip_output_path(_status(clip_id))
        whole.parent.mkdir(parents=True, exist_ok=True)
        _mvhd_mp4(whole, 159.48)
        video.update_video_clip_public_status(clip_id, "error", "upload failed", str(whole))
        video.retry_public_video_uploads_once(cfg)
        assert not seen["encodes"]
        assert [u["path"] for u in seen["uploads"]] == [whole]

    def test_a_copy_on_cloudflare_that_is_not_the_hut_copy_is_rebuilt(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        whole = video.public_clip_output_path(_status(clip_id))
        whole.parent.mkdir(parents=True, exist_ok=True)
        _mvhd_mp4(whole, 159.48)
        url = "https://assets.example.org/racevideos/race97/" + whole.name
        video.update_video_clip_public_status(clip_id, "ready", "uploaded", str(whole), "k", url)
        seen["served"][url] = 8_650_000                  # half of it, uploaded mid-encode
        found = video.broken_public_copies()
        assert len(found) == 1 and "uploaded before it was finished" in found[0][1]
        assert video.rebuild_broken_public_copies(cfg) == 1

    def test_an_unreadable_published_copy_is_rebuilt(self, publishing):
        seen, cfg = publishing
        clip_id = _clip()
        broken = video.public_clip_output_path(_status(clip_id))
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"\x00\x00\x00\x08mdat")
        video.update_video_clip_public_status(clip_id, "ready", "uploaded", str(broken), "k", "https://x/k")
        assert video.rebuild_broken_public_copies(cfg) == 1
        assert not broken.exists()

    def test_half_written_copies_left_by_a_restart_are_cleared(self, client):
        public = appstate.VIDEO_CLIPS_DIR / "public"
        public.mkdir(parents=True, exist_ok=True)
        part = public / "race97_finish_400_public.part.mp4"
        part.write_bytes(b"x")
        long_ago = time.time() - 7200
        os.utime(part, (long_ago, long_ago))
        assert video.remove_stray_overlay_images() == 1 and not part.exists()

