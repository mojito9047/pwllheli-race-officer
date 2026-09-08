"""The nightly off-site backup.

Everything the race office cannot recreate lives on one PC in one hut. A backup
downloaded to a laptop in the same building is not a backup against the fire,
the theft or the dead disk, so this pushes an encrypted copy to Cloudflare R2.

Four things here are worth more than the happy path, and each has its own class:

* An off-site archive must never leave the hut readable. It contains the users
  table and every password hash.
* A wrong passphrase on the restore form must not destroy anything. The restore
  code deletes the current branding, polars and clips *before* extracting, and
  decryption failures surface at the first member read — so without a check the
  cost of a typo is an emptied folder and nothing put back.
* The busy guard must be able to become false again. "Any boat still RACING"
  never does once a race sheet is abandoned, and a nightly job one stale row can
  switch off for ever is not a nightly job.
* A job that quietly stopped must look stopped. The whole feature is worthless
  if the dashboard says nothing while months go by, which is why the status
  carries an age and reports "never" and "stale" as loudly as success.
"""
from __future__ import annotations

import json
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import app as ro
from core import appstate, backup as backup_core, offsite, r2


PASSPHRASE = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(client, tmp_path, monkeypatch):
    """Point the data-folder constants at a throwaway directory."""
    root = tmp_path / "data"
    for name, value in (
        ("DATA_DIR", root),
        ("POLARS_DIR", root / "polars"),
        ("SAIL_CHARTS_DIR", root / "sailcharts"),
        ("BRANDING_DIR", root / "branding"),
        ("VIDEO_CLIPS_DIR", root / "video_clips"),
        ("LEGACY_VIDEO_CLIPS_DIR", root / "video" / "clips"),
    ):
        Path(value).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(appstate, name, Path(value))
    return root


class _Upload:
    """Minimal stand-in for a Werkzeug FileStorage."""

    def __init__(self, path):
        self.filename = Path(path).name
        self._path = Path(path)

    def save(self, destination):
        Path(destination).write_bytes(self._path.read_bytes())


class FakeBucket:
    """An in-memory stand-in for the R2 object store.

    Substituted for core.r2's verbs rather than for core.offsite's own upload
    helpers, so the tests still run the real key building, the real HEAD
    verification and the real retention arithmetic.
    """

    def __init__(self):
        self.objects = {}
        self.puts = []
        self.deleted = []
        self.short_write = False        # simulate an upload that reports success without arriving

    def install(self, monkeypatch):
        monkeypatch.setattr(offsite.r2, "put_object", self._put)
        monkeypatch.setattr(offsite.r2, "put_file", self._put_file)
        monkeypatch.setattr(offsite.r2, "head_object", self._head)
        monkeypatch.setattr(offsite.r2, "delete_object", self._delete)
        monkeypatch.setattr(offsite.r2, "list_objects", self._list)
        return self

    def _put(self, account, bucket, key, body, access, secret, content_type="", cache_control=""):
        self.puts.append({"key": key, "bucket": bucket, "content_type": content_type,
                          "cache_control": cache_control, "size": len(body)})
        self.objects[key] = body[:-1] if self.short_write and key.endswith(".zip") else body

    def _put_file(self, account, bucket, key, path, access, secret, content_type="", cache_control=""):
        self._put(account, bucket, key, Path(path).read_bytes(), access, secret,
                  content_type=content_type, cache_control=cache_control)
        return "curl"

    def _head(self, account, bucket, key, access, secret):
        if key not in self.objects:
            raise RuntimeError(f"HTTP 404 Not Found: {key}")
        return {"size_bytes": len(self.objects[key]), "etag": "fake-etag", "last_modified": ""}

    def _delete(self, account, bucket, key, access, secret):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def _list(self, account, bucket, prefix, access, secret, max_pages=20):
        return [{"key": key, "size_bytes": len(body), "last_modified": ""}
                for key, body in sorted(self.objects.items()) if key.startswith(prefix)]


def configure_offsite(enabled=True, bucket="psc-backups", passphrase=PASSPHRASE,
                      video_bucket="psc-race-videos", keep=30, sections=None, hour=3, minute=15):
    """Write off-site settings the way the Settings form would."""
    ro.save_app_settings({
        "offsite_backup_enabled": "1" if enabled else "0",
        "offsite_backup_bucket": bucket,
        "offsite_backup_prefix": "race-officer-backups",
        "offsite_backup_hour": str(hour),
        "offsite_backup_minute": str(minute),
        "offsite_backup_keep": str(keep),
        "offsite_backup_sections": sections if sections is not None else ["database"],
        "offsite_backup_passphrase": passphrase,
        "video_public_r2_account_id": "a" * 32,
        "video_public_r2_bucket": video_bucket,
        "video_public_r2_access_key_id": "AKID",
        "video_public_r2_secret_access_key": "SECRET",
        "video_public_r2_public_base_url": "https://videos.example.org",
    })
    return offsite.offsite_config()


def add_race(start_time, entry_status="FINISHED", name="Saturday Points"):
    """Insert a race sheet with one entry, for the busy-guard tests."""
    with ro.get_db() as db:
        cursor = db.execute(
            "INSERT INTO races (name, course_no, start_time, rating_rule, created_at) VALUES (?, 1, ?, 'IRC_TCC', ?)",
            (name, start_time.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
        )
        db.execute(
            "INSERT INTO entries (race_id, boat_name, status) VALUES (?, 'Test Boat', ?)",
            (cursor.lastrowid, entry_status),
        )
        db.commit()
        return int(cursor.lastrowid)


def add_video_clip(public_status, updated_at):
    with ro.get_db() as db:
        db.execute(
            """
            INSERT INTO video_clips (race_id, clip_type, event_time, status, public_status, created_at, updated_at)
            VALUES (1, 'finish', ?, 'ready', ?, ?, ?)
            """,
            (updated_at.isoformat(timespec="seconds"), public_status,
             updated_at.isoformat(timespec="seconds"), updated_at.isoformat(timespec="seconds")),
        )
        db.commit()


# ---------------------------------------------------------------------------
# It never leaves the hut readable
# ---------------------------------------------------------------------------

class TestTheArchiveIsEncrypted:
    def test_the_contents_cannot_be_read_without_the_passphrase(self, data_dir):
        """The archive holds the users table and every password hash in the club."""
        zip_path, _ = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(RuntimeError):
                zf.read("data/race_officer.db")

    def test_the_member_names_stay_readable_so_sections_are_still_detectable(self, data_dir):
        """Which sections a backup contains is knowable before anything is decrypted,
        which is what lets the restore form list them and skip the missing ones."""
        zip_path, _ = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        with zipfile.ZipFile(zip_path, "r") as zf:
            assert backup_core.backup_zip_has_section(zf, "database")

    def test_with_the_passphrase_it_reads_back_byte_for_byte(self, data_dir):
        plain_path, _ = backup_core.create_data_backup_zip(["database"])
        encrypted_path, _ = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        with zipfile.ZipFile(plain_path, "r") as plain:
            expected = plain.read("data/race_officer.db")
        with backup_core.open_backup_zip(encrypted_path, PASSPHRASE) as encrypted:
            assert encrypted.read("data/race_officer.db") == expected

    def test_an_encrypted_archive_is_recognised_as_one(self, data_dir):
        encrypted_path, _ = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        plain_path, _ = backup_core.create_data_backup_zip(["database"])
        assert backup_core.zip_is_encrypted(encrypted_path)
        assert not backup_core.zip_is_encrypted(plain_path)

    def test_it_is_the_winzip_aes_format_not_a_private_one(self, data_dir):
        """So 7-Zip opens it on a day this app is not available to.

        A format only this code understands would make the off-site copy depend on
        the very thing the off-site copy exists to survive.
        """
        zip_path, _ = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [i for i in zf.infolist() if not i.filename.endswith("/")]
            assert members
            assert all(i.compress_type == backup_core.WZ_AES_COMPRESS_TYPE for i in members)

    def test_the_manifest_records_that_it_was_encrypted(self, data_dir):
        zip_path, summary = backup_core.create_data_backup_zip(["database"], passphrase=PASSPHRASE)
        assert summary["encrypted"] is True
        with backup_core.open_backup_zip(zip_path, PASSPHRASE) as zf:
            manifest = json.loads(zf.read(backup_core.BACKUP_MANIFEST_NAME))
        assert manifest["encrypted"] is True

    def test_a_plain_backup_is_still_plain(self, data_dir):
        """The Backup/restore page download must not start demanding a passphrase."""
        zip_path, summary = backup_core.create_data_backup_zip(["database"])
        assert summary["encrypted"] is False
        with zipfile.ZipFile(zip_path, "r") as zf:
            assert zf.read("data/race_officer.db")


class TestItRefusesToUploadSomethingReadable:
    def test_without_a_passphrase_it_will_not_run(self, data_dir):
        cfg = configure_offsite(passphrase="")
        ready, why = offsite.offsite_credentials_ready(cfg)
        assert not ready
        assert "passphrase" in why.lower()

    def test_the_backup_bucket_may_not_be_the_public_video_bucket(self, data_dir):
        """That bucket is served publicly. A backup in it is one guessed key away
        from being anyone's download."""
        cfg = configure_offsite(bucket="psc-race-videos", video_bucket="psc-race-videos")
        ready, why = offsite.offsite_credentials_ready(cfg)
        assert not ready
        assert "publicly" in why

    def test_a_separate_bucket_is_accepted(self, data_dir):
        cfg = configure_offsite(bucket="psc-backups", video_bucket="psc-race-videos")
        ready, why = offsite.offsite_credentials_ready(cfg)
        assert ready, why

    def test_missing_credentials_are_reported_not_guessed_at(self, data_dir):
        cfg = configure_offsite()
        cfg["r2_secret_access_key"] = ""
        ready, why = offsite.offsite_credentials_ready(cfg)
        assert not ready and "secret key" in why


# ---------------------------------------------------------------------------
# Restoring one, including getting the passphrase wrong
# ---------------------------------------------------------------------------

class TestRestoringAnOffSiteBackup:
    def test_an_encrypted_backup_restores_with_its_passphrase(self, data_dir):
        (appstate.BRANDING_DIR / "club.png").write_bytes(b"logo-bytes")
        zip_path, _ = backup_core.create_data_backup_zip(["branding"], passphrase=PASSPHRASE)
        (appstate.BRANDING_DIR / "club.png").unlink()

        result = ro.restore_data_backup_zip(_Upload(zip_path), ["branding"], PASSPHRASE)

        assert result["restored_counts"]["branding"] == 1
        assert (appstate.BRANDING_DIR / "club.png").read_bytes() == b"logo-bytes"

    def test_a_wrong_passphrase_destroys_nothing(self, data_dir):
        """The restore clears the target folders before extracting, and a bad
        passphrase only shows up at the first member read. Without a check up
        front, a typo costs the branding folder and puts nothing back."""
        (appstate.BRANDING_DIR / "club.png").write_bytes(b"logo-bytes")
        zip_path, _ = backup_core.create_data_backup_zip(["branding"], passphrase=PASSPHRASE)

        with pytest.raises(ValueError, match="passphrase"):
            ro.restore_data_backup_zip(_Upload(zip_path), ["branding"], "not-the-passphrase")

        assert (appstate.BRANDING_DIR / "club.png").read_bytes() == b"logo-bytes"

    def test_no_passphrase_at_all_is_a_clear_message_not_a_crash(self, data_dir):
        zip_path, _ = backup_core.create_data_backup_zip(["branding"], passphrase=PASSPHRASE)
        with pytest.raises(ValueError, match="encrypted"):
            ro.restore_data_backup_zip(_Upload(zip_path), ["branding"], "")

    def test_a_plain_backup_still_restores_with_no_passphrase(self, data_dir):
        """The everyday path from the Backup/restore page is unchanged."""
        (appstate.BRANDING_DIR / "club.png").write_bytes(b"logo-bytes")
        zip_path, _ = backup_core.create_data_backup_zip(["branding"])
        (appstate.BRANDING_DIR / "club.png").unlink()

        result = ro.restore_data_backup_zip(_Upload(zip_path), ["branding"])

        assert result["restored_counts"]["branding"] == 1

    def test_the_restore_form_offers_a_passphrase_field(self, logged_in_client):
        html = logged_in_client.get("/admin/backup").get_data(as_text=True)
        assert 'name="backup_passphrase"' in html

    def test_an_empty_file_in_the_archive_does_not_pass_for_a_passphrase_check(self, data_dir):
        """Reading one byte of a zero-length member succeeds whatever the
        passphrase is. If the check probed that member it would report success and
        hand the destructive path a passphrase it had never actually verified."""
        (appstate.BRANDING_DIR / "empty.png").write_bytes(b"")
        (appstate.BRANDING_DIR / "club.png").write_bytes(b"logo-bytes")
        zip_path, _ = backup_core.create_data_backup_zip(["branding"], passphrase=PASSPHRASE)

        with pytest.raises(ValueError, match="passphrase"):
            ro.restore_data_backup_zip(_Upload(zip_path), ["branding"], "wrong")

        assert (appstate.BRANDING_DIR / "club.png").read_bytes() == b"logo-bytes"


# ---------------------------------------------------------------------------
# Standing aside for racing — and being able to stop standing aside
# ---------------------------------------------------------------------------

class TestTheBusyGuard:
    def test_boats_still_racing_hold_the_backup_back(self, data_dir):
        add_race(datetime.now() - timedelta(hours=1), entry_status="RACING")
        busy, reason = offsite.hut_is_busy()
        assert busy and "racing" in reason.lower()

    def test_the_hours_before_a_start_hold_it_back(self, data_dir):
        """The pre-start sequence is the busiest the hut ever is."""
        add_race(datetime.now() + timedelta(hours=1), entry_status="RACING")
        busy, reason = offsite.hut_is_busy()
        assert busy and "starts at" in reason

    def test_a_race_finished_hours_ago_does_not(self, data_dir):
        add_race(datetime.now() - timedelta(hours=6), entry_status="FINISHED")
        busy, _reason = offsite.hut_is_busy()
        assert not busy

    def test_an_abandoned_race_sheet_cannot_hold_it_back_for_ever(self, data_dir):
        """The failure this whole feature guards against, arriving by the back door.

        A sheet from last month left with boats marked RACING satisfies "any entry
        still RACING" for ever, so the naive guard would defer the backup every
        night and the dashboard would report a deferral rather than a failure.
        """
        add_race(datetime.now() - timedelta(days=30), entry_status="RACING", name="Abandoned")
        busy, reason = offsite.hut_is_busy()
        assert not busy, f"a month-old race sheet still blocks the backup: {reason}"

    def test_a_race_stored_in_a_different_time_format_is_still_seen(self, data_dir):
        """races.start_time is TEXT. Ordering and limiting these in SQL sorts a
        row written in another ISO shape into the wrong place, which could push
        today's race out of the window and let a backup run mid-race."""
        now = datetime.now()
        with ro.get_db() as db:
            cursor = db.execute(
                "INSERT INTO races (name, course_no, start_time, rating_rule, created_at) VALUES (?, 1, ?, 'IRC_TCC', ?)",
                ("Space Separated", now.strftime("%Y-%m-%d %H:%M:%S"), now.isoformat(timespec="seconds")),
            )
            db.execute("INSERT INTO entries (race_id, boat_name, status) VALUES (?, 'Boat', 'RACING')",
                       (cursor.lastrowid,))
            db.commit()

        busy, reason = offsite.hut_is_busy()
        assert busy, "a race stored with a space instead of a T was not noticed"
        assert "Space Separated" in reason

    def test_a_video_upload_in_progress_holds_it_back(self, data_dir):
        """The hut is on 4G. A backup must never be why a start video is lost."""
        add_video_clip("uploading", datetime.now() - timedelta(minutes=5))
        busy, reason = offsite.hut_is_busy()
        assert busy and "video" in reason.lower()

    def test_a_clip_wedged_since_last_season_does_not(self, data_dir):
        add_video_clip("uploading", datetime.now() - timedelta(days=40))
        busy, _reason = offsite.hut_is_busy()
        assert not busy

    def test_a_finished_upload_does_not(self, data_dir):
        add_video_clip("ready", datetime.now() - timedelta(minutes=5))
        busy, _reason = offsite.hut_is_busy()
        assert not busy

    def test_a_deferred_run_says_so_and_sets_a_retry(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite()
        add_race(datetime.now() - timedelta(hours=1), entry_status="RACING")

        status = offsite.run_offsite_backup_once(cfg)

        assert status["deferred"] is True
        assert not bucket.puts, "a deferred backup must not upload anything"
        assert status["next_attempt_after"]

    def test_backing_up_now_overrides_the_guard(self, data_dir, monkeypatch):
        """Somebody standing at the PC asking for a backup knows better than the guard."""
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite()
        add_race(datetime.now() - timedelta(hours=1), entry_status="RACING")

        status = offsite.run_offsite_backup_once(cfg, force=True)

        assert status["ok"], status.get("message")
        assert bucket.puts

    def test_forcing_does_not_skip_the_encryption(self, data_dir, monkeypatch):
        """force is about the busy check only."""
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite(passphrase="")
        status = offsite.run_offsite_backup_once(cfg, force=True)
        assert not status["ok"]
        assert not bucket.puts


# ---------------------------------------------------------------------------
# Uploading, and checking it arrived
# ---------------------------------------------------------------------------

class TestTheUpload:
    def test_it_uploads_the_archive_and_a_readable_manifest(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite()

        status = offsite.run_offsite_backup_once(cfg, force=True)

        assert status["ok"], status.get("message")
        archives = [k for k in bucket.objects if k.endswith(".zip")]
        manifests = [k for k in bucket.objects if k.endswith(".manifest.json")]
        assert len(archives) == 1 and len(manifests) == 1

    def test_the_manifest_is_readable_without_decrypting_anything(self, data_dir, monkeypatch):
        """So the club can see what is off-site, and check a downloaded archive,
        without needing the passphrase first. It carries counts, never race data."""
        bucket = FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)

        key = next(k for k in bucket.objects if k.endswith(".manifest.json"))
        manifest = json.loads(bucket.objects[key])
        assert manifest["encrypted"] is True
        assert manifest["sections"] == ["database"]
        assert len(manifest["sha256"]) == 64
        assert manifest["zip_size_bytes"] > 0

    def test_the_sha256_in_the_manifest_is_the_archive_that_was_uploaded(self, data_dir, monkeypatch):
        import hashlib
        bucket = FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)

        archive_key = next(k for k in bucket.objects if k.endswith(".zip"))
        manifest = json.loads(bucket.objects[offsite.manifest_key_for(archive_key)])
        assert manifest["sha256"] == hashlib.sha256(bucket.objects[archive_key]).hexdigest()

    def test_arrival_is_verified_rather_than_assumed(self, data_dir, monkeypatch):
        """An upload that returns 200 without the bytes landing is the failure
        nobody notices until a restore, so the size is read back from R2."""
        bucket = FakeBucket().install(monkeypatch)
        bucket.short_write = True

        status = offsite.run_offsite_backup_once(configure_offsite(), force=True)

        assert not status["ok"]
        assert "verification failed" in status["last_error"]

    def test_backups_are_never_cached_or_public(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)
        assert all("no-store" in put["cache_control"] for put in bucket.puts)
        assert not any("public" in put["cache_control"] for put in bucket.puts)

    def test_the_local_copy_is_removed_afterwards(self, data_dir, monkeypatch):
        """A full copy of the club's data must not accumulate in runtime/."""
        FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)
        assert not list(appstate.RUNTIME_DIR.glob(backup_core.BACKUP_ARCHIVE_GLOB))

    def test_the_local_copy_is_removed_even_when_the_upload_fails(self, data_dir, monkeypatch):
        monkeypatch.setattr(offsite.r2, "put_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden")))
        status = offsite.run_offsite_backup_once(configure_offsite(), force=True)
        assert not status["ok"]
        assert not list(appstate.RUNTIME_DIR.glob(backup_core.BACKUP_ARCHIVE_GLOB))

    def test_videos_are_not_in_the_default_sections(self, data_dir):
        """The public web copies are already on R2; backing them up pays twice."""
        assert "videos" not in offsite.DEFAULT_OFFSITE_SECTIONS
        assert "videos" not in offsite.normalise_offsite_sections(None)

    def test_each_backup_gets_its_own_key_so_a_bad_one_cannot_overwrite_a_good_one(self):
        early = offsite.offsite_object_key("backups", datetime(2026, 8, 3, 3, 15, 0))
        later = offsite.offsite_object_key("backups", datetime(2026, 8, 4, 3, 15, 0))
        assert early != later
        assert early.startswith("backups/") and early.endswith(".zip")


class TestWhenTheBucketRefusesUs:
    """The failure reported from the hut, and why it was unreadable.

    R2 answers a request it does not like and closes the connection. With a 40 MB
    body still going out, the socket is torn down before the HTTP response is read,
    so the reason — a token that does not cover this bucket, a bucket that is not
    there — is lost and what surfaces is "Network error: [WinError 10053] An
    established connection was aborted by the software in your host machine". That
    reads like a broken link and sends you to the router.
    """

    def test_a_refusal_is_found_before_the_archive_is_built(self, data_dir, monkeypatch):
        """Building it snapshots three databases and encrypts tens of megabytes.
        Finding out afterwards that we cannot write there wastes all of it."""
        built = []
        real_create = backup_core.create_data_backup_zip
        monkeypatch.setattr(backup_core, "create_data_backup_zip",
                            lambda *a, **k: (built.append(1), real_create(*a, **k))[1])
        monkeypatch.setattr(offsite.r2, "put_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden: <Error><Code>AccessDenied</Code></Error>")))

        status = offsite.run_offsite_backup_once(configure_offsite(), force=True)

        assert not status["ok"]
        assert not built, "the archive was built even though the bucket could not be written to"

    def test_the_refusal_names_the_bucket_and_keeps_r2s_own_reason(self, data_dir, monkeypatch):
        monkeypatch.setattr(offsite.r2, "put_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden: <Error><Code>AccessDenied</Code></Error>")))
        status = offsite.run_offsite_backup_once(configure_offsite(bucket="psc-backups"), force=True)
        assert "psc-backups" in status["last_error"]
        assert "AccessDenied" in status["last_error"]

    def test_the_preflight_object_is_tiny_and_not_the_archive(self, data_dir, monkeypatch):
        """It has to be small enough to be sent in one go, or it fails the same
        unreadable way the archive did."""
        bucket = FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)
        preflight = [p for p in bucket.puts if p["key"].endswith(".preflight")]
        assert len(preflight) == 1
        assert preflight[0]["size"] < 4096

    def test_the_preflight_is_not_swept_away_by_retention(self, data_dir, monkeypatch):
        """Retention matches .zip keys; a preflight object caught by it would be
        deleted and rewritten every night for no reason."""
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite(keep=1)
        offsite.run_offsite_backup_once(cfg, force=True)
        offsite.prune_offsite_backups(cfg, 1)
        assert any(k.endswith(".preflight") for k in bucket.objects)

    def test_an_aborted_upload_now_says_where_to_look(self):
        """The bare Winsock text names the wrong culprit — "the software in your
        host machine" — so the message has to say what it usually really means."""
        import urllib.error
        exc = urllib.error.URLError(OSError(10053, "An established connection was aborted by the software in your host machine"))
        message = r2.error_message(exc)
        assert "10053" in message
        assert "bucket" in message.lower() and "token" in message.lower()


class TestTheArchiveUploadIsStreamed:
    def test_it_goes_through_put_file_rather_than_reading_the_whole_zip_in(self, data_dir, monkeypatch):
        """A backup archive is tens of megabytes. The in-process signer has to hold
        all of it in memory to hash it, and has no stall detection."""
        seen = {}

        def fake_put_file(account, bucket, key, path, access, secret, content_type="", cache_control=""):
            seen["path"] = Path(path)
            seen["key"] = key
            return "curl"

        monkeypatch.setattr(offsite.r2, "put_file", fake_put_file)
        monkeypatch.setattr(offsite.r2, "put_object", lambda *a, **k: None)
        monkeypatch.setattr(offsite.r2, "head_object",
                            lambda account, bucket, key, access, secret: {"size_bytes": seen["path"].stat().st_size, "etag": "e"})
        monkeypatch.setattr(offsite.r2, "list_objects", lambda *a, **k: [])

        status = offsite.run_offsite_backup_once(configure_offsite(), force=True)

        assert status["ok"], status.get("message")
        assert seen["key"].endswith(".zip")
        assert status["upload_method"] == "curl"

    def test_curl_is_tried_first_then_the_built_in_signer(self, tmp_path, monkeypatch):
        order = []
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"x" * 32)
        monkeypatch.setattr(r2.shutil, "which", lambda name: "curl")
        monkeypatch.setattr(r2, "put_file_with_curl",
                            lambda *a, **k: (order.append("curl"), (_ for _ in ()).throw(RuntimeError("curl: (55) send failed")))[0])
        monkeypatch.setattr(r2, "put_object", lambda *a, **k: order.append("built-in"))

        method = r2.put_file("a" * 32, "b", "k.zip", archive, "AK", "SK")

        assert order == ["curl", "built-in"]
        assert method == "built-in"

    def test_both_failing_reports_both_reasons(self, tmp_path, monkeypatch):
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"x" * 32)
        monkeypatch.setattr(r2.shutil, "which", lambda name: "curl")
        monkeypatch.setattr(r2, "put_file_with_curl",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("curl: (55) send failed")))
        monkeypatch.setattr(r2, "put_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden")))

        with pytest.raises(RuntimeError, match="curl.*403|403.*curl"):
            r2.put_file("a" * 32, "b", "k.zip", archive, "AK", "SK")

    def test_curl_streams_from_the_file_and_guards_against_a_stall_not_slowness(self, tmp_path, monkeypatch):
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"x" * (20 * 1024 * 1024))
        captured = {}

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            return _Proc()

        monkeypatch.setattr(r2.subprocess, "run", fake_run)
        monkeypatch.setattr(r2.shutil, "which", lambda name: "curl")

        r2.put_file_with_curl("a" * 32, "psc-backups", "backups/x.zip", archive, "AK", "SK")

        cmd = captured["cmd"]
        assert "--upload-file" in cmd and str(archive) in cmd
        assert cmd[cmd.index("--speed-limit") + 1] == str(r2.R2_STALL_BYTES_PER_S)
        assert cmd[cmd.index("--speed-time") + 1] == str(r2.R2_STALL_SECONDS)
        # The allowance follows the file, as it does for a race video.
        assert captured["timeout"] > r2.r2_upload_timeout_s(20 * 1024 * 1024)

    def test_the_backup_is_still_never_cached_or_public_when_streamed(self, tmp_path, monkeypatch):
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"x" * 16)
        captured = {}

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(r2.subprocess, "run", lambda cmd, **k: (captured.update(cmd=cmd), _Proc())[1])
        monkeypatch.setattr(r2.shutil, "which", lambda name: "curl")
        r2.put_file_with_curl("a" * 32, "b", "k.zip", archive, "AK", "SK")
        headers = " ".join(captured["cmd"])
        assert "Cache-Control: private, no-store" in headers


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

class TestBackUpNowDoesNotHangTheRequest:
    """From the hut: pressing the button returned Cloudflare's "Gateway time-out
    504" while the backup carried on behind it and completed. The archive was in
    R2 and the page said it had failed.

    A backup snapshots three databases, encrypts tens of megabytes and pushes them
    over 4G — minutes — and the hut is reached through a cloudflared tunnel that
    abandons a request at around 100 seconds. It cannot run inside the request,
    however much nicer it is to report the outcome directly.
    """

    def test_the_route_returns_at_once_and_does_not_wait_for_the_upload(self, logged_in_client, data_dir, monkeypatch):
        import time as _time
        configure_offsite()
        released = threading.Event()

        def slow_upload(*a, **k):
            released.wait(timeout=10)        # stands in for minutes on 4G
            return "curl"

        monkeypatch.setattr(offsite.r2, "put_file", slow_upload)
        monkeypatch.setattr(offsite.r2, "put_object", lambda *a, **k: None)
        monkeypatch.setattr(offsite.r2, "head_object", lambda *a, **k: {"size_bytes": 0, "etag": "e"})
        monkeypatch.setattr(offsite.r2, "list_objects", lambda *a, **k: [])

        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        started = _time.time()
        resp = logged_in_client.post("/admin/settings/offsite_backup/run", data={"_csrf_token": token})
        elapsed = _time.time() - started
        released.set()

        assert resp.status_code in (200, 302)
        assert elapsed < 5, f"the request blocked for {elapsed:.1f}s; Cloudflare gives up around 100s"

    def test_a_second_press_does_not_start_a_parallel_backup(self, data_dir, monkeypatch):
        configure_offsite()
        with offsite.OFFSITE_LOCK:
            offsite.OFFSITE_STATE["running"] = True
        try:
            started, message = offsite.start_offsite_backup_now()
        finally:
            with offsite.OFFSITE_LOCK:
                offsite.OFFSITE_STATE["running"] = False
        assert not started
        assert "already running" in message

    def test_the_status_carries_a_progress_line_while_running(self, data_dir):
        configure_offsite()
        offsite.save_offsite_status({"ok": False, "message": "Uploading 39.3 MB to Cloudflare R2…"})
        with offsite.OFFSITE_LOCK:
            offsite.OFFSITE_STATE["running"] = True
        try:
            status = offsite.offsite_dashboard_status()
        finally:
            with offsite.OFFSITE_LOCK:
                offsite.OFFSITE_STATE["running"] = False
        assert status["running"]
        assert "39.3 MB" in status["progress"]

    def test_the_settings_page_refreshes_itself_while_one_runs(self, logged_in_client, data_dir):
        configure_offsite()
        with offsite.OFFSITE_LOCK:
            offsite.OFFSITE_STATE["running"] = True
        try:
            html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        finally:
            with offsite.OFFSITE_LOCK:
                offsite.OFFSITE_STATE["running"] = False
        assert 'data-offsite-running="1"' in html

    def test_it_does_not_refresh_when_nothing_is_running(self, logged_in_client, data_dir):
        configure_offsite()
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert 'data-offsite-running="0"' in html


class TestRetention:
    def test_it_keeps_the_newest_and_removes_the_rest(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite(keep=3)
        for day in range(1, 6):
            key = offsite.offsite_object_key(cfg["offsite_backup_prefix"], datetime(2026, 7, day, 3, 15))
            bucket.objects[key] = b"old-archive"
            bucket.objects[offsite.manifest_key_for(key)] = b"{}"

        result = offsite.prune_offsite_backups(cfg, 3)

        assert result["removed"] == 2
        remaining = sorted(k for k in bucket.objects if k.endswith(".zip"))
        assert len(remaining) == 3
        assert "2026070" + "3" in remaining[0]        # the two oldest went

    def test_the_manifest_goes_with_its_archive(self, data_dir, monkeypatch):
        """Otherwise the bucket slowly fills with orphaned manifests."""
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite(keep=1)
        for day in (1, 2):
            key = offsite.offsite_object_key(cfg["offsite_backup_prefix"], datetime(2026, 7, day, 3, 15))
            bucket.objects[key] = b"old-archive"
            bucket.objects[offsite.manifest_key_for(key)] = b"{}"

        offsite.prune_offsite_backups(cfg, 1)

        assert len([k for k in bucket.objects if k.endswith(".manifest.json")]) == 1

    def test_it_never_deletes_the_only_copy(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite()
        key = offsite.offsite_object_key(cfg["offsite_backup_prefix"], datetime(2026, 7, 1, 3, 15))
        bucket.objects[key] = b"the-only-archive"

        offsite.prune_offsite_backups(cfg, 0)        # a nonsense retention count

        assert key in bucket.objects

    def test_a_failed_delete_is_reported_not_swallowed(self, data_dir, monkeypatch):
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite(keep=1)
        for day in (1, 2):
            key = offsite.offsite_object_key(cfg["offsite_backup_prefix"], datetime(2026, 7, day, 3, 15))
            bucket.objects[key] = b"old-archive"
        monkeypatch.setattr(offsite.r2, "delete_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden")))

        result = offsite.prune_offsite_backups(cfg, 1)

        assert result["errors"]


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

class TestWhenItRuns:
    def test_it_is_due_once_the_scheduled_time_has_passed(self, data_dir):
        cfg = configure_offsite(hour=3, minute=15)
        now = datetime(2026, 8, 3, 3, 20)
        assert offsite.offsite_backup_due(cfg, {}, now)

    def test_it_is_not_due_again_the_same_night(self, data_dir):
        cfg = configure_offsite(hour=3, minute=15)
        now = datetime(2026, 8, 3, 4, 0)
        status = {"last_success_at": datetime(2026, 8, 3, 3, 16).isoformat(timespec="seconds")}
        assert not offsite.offsite_backup_due(cfg, status, now)

    def test_a_pc_switched_off_at_three_am_still_gets_its_backup(self, data_dir):
        """The normal state of a hut PC. Asking "is it 03:15 now?" would mean a
        machine that is only on during the day never backs up at all."""
        cfg = configure_offsite(hour=3, minute=15)
        monday_evening = datetime(2026, 8, 3, 19, 0)
        status = {"last_success_at": datetime(2026, 8, 1, 3, 16).isoformat(timespec="seconds")}
        assert offsite.offsite_backup_due(cfg, status, monday_evening)

    def test_before_the_scheduled_time_yesterdays_run_is_enough(self, data_dir):
        cfg = configure_offsite(hour=3, minute=15)
        now = datetime(2026, 8, 3, 2, 0)
        status = {"last_success_at": datetime(2026, 8, 2, 3, 16).isoformat(timespec="seconds")}
        assert not offsite.offsite_backup_due(cfg, status, now)

    def test_a_backoff_after_a_failure_is_respected(self, data_dir):
        cfg = configure_offsite(hour=3, minute=15)
        now = datetime(2026, 8, 3, 3, 20)
        status = {"next_attempt_after": datetime(2026, 8, 3, 4, 0).isoformat(timespec="seconds")}
        assert not offsite.offsite_backup_due(cfg, status, now)
        assert offsite.offsite_backup_due(cfg, status, datetime(2026, 8, 3, 4, 5))

    def test_switched_off_means_never_due(self, data_dir):
        cfg = configure_offsite(enabled=False)
        assert not offsite.offsite_backup_due(cfg, {}, datetime(2026, 8, 3, 3, 20))


# ---------------------------------------------------------------------------
# Being honest about it on the dashboard
# ---------------------------------------------------------------------------

class TestTheDashboardTellsTheTruth:
    def test_switched_off_says_the_only_copies_are_in_the_hut(self, data_dir):
        configure_offsite(enabled=False)
        status = offsite.offsite_dashboard_status()
        assert status["state"] == "off"
        assert not status["ok"]
        assert "hut" in status["message"]

    def test_enabled_but_never_run_is_not_reported_as_healthy(self, data_dir):
        configure_offsite()
        status = offsite.offsite_dashboard_status()
        assert status["state"] == "never"
        assert not status["ok"]

    def test_a_backup_from_last_month_reads_as_stale(self, data_dir):
        """The failure mode the whole card exists for: a job that stopped while
        everybody went on believing there was one."""
        configure_offsite()
        offsite.save_offsite_status({
            "ok": True,
            "last_success_at": (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds"),
        })
        status = offsite.offsite_dashboard_status()
        assert status["state"] == "stale"
        assert not status["ok"]
        assert "40 days" in status["message"]

    def test_a_backup_from_last_night_reads_as_healthy(self, data_dir):
        configure_offsite()
        offsite.save_offsite_status({
            "ok": True,
            "last_success_at": (datetime.now() - timedelta(hours=8)).isoformat(timespec="seconds"),
        })
        status = offsite.offsite_dashboard_status()
        assert status["state"] == "ok" and status["ok"]

    def test_tonights_failure_does_not_erase_last_nights_success(self, data_dir, monkeypatch):
        """Otherwise one bad night makes the card read "never", which is both
        wrong and the wrong shape of alarming."""
        bucket = FakeBucket().install(monkeypatch)
        cfg = configure_offsite()
        offsite.run_offsite_backup_once(cfg, force=True)
        good = offsite.read_offsite_status()["last_success_at"]
        assert good

        monkeypatch.setattr(offsite.r2, "put_object",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 403 Forbidden")))
        failed = offsite.run_offsite_backup_once(cfg, force=True)

        assert not failed["ok"]
        assert failed["last_success_at"] == good
        assert "403" in failed["last_error"]

    def test_the_status_survives_an_app_restart(self, data_dir, monkeypatch):
        """A hut PC is restarted often. In-memory-only status would mean the card
        said "never" every morning."""
        FakeBucket().install(monkeypatch)
        offsite.run_offsite_backup_once(configure_offsite(), force=True)
        with offsite.OFFSITE_LOCK:                  # as a fresh process would start
            offsite.OFFSITE_STATE["last_status"] = {}

        assert offsite.read_offsite_status()["last_success_at"]

    def test_the_card_is_on_the_dashboard(self, logged_in_client):
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert "Off-site backup" in html

    def test_the_settings_page_shows_the_section(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert 'id="offsite-backup"' in html
        assert 'name="offsite_backup_bucket"' in html
        assert 'name="offsite_backup_passphrase"' in html


# ---------------------------------------------------------------------------
# Settings round trip
# ---------------------------------------------------------------------------

class TestSettings:
    def test_a_blank_passphrase_field_keeps_the_saved_one(self, data_dir):
        """Every archive already in the bucket was encrypted with it. Wiping it on
        an unrelated settings save would leave them unopenable."""
        configure_offsite(passphrase=PASSPHRASE)
        ro.save_app_settings({"offsite_backup_passphrase": "", "offsite_backup_bucket": "psc-backups"})
        assert offsite.offsite_config()["offsite_backup_passphrase"] == PASSPHRASE

    def test_clearing_it_is_deliberate_and_works(self, data_dir):
        configure_offsite(passphrase=PASSPHRASE)
        ro.save_app_settings({"offsite_backup_passphrase": "", "offsite_backup_passphrase_clear": "1"})
        assert offsite.offsite_config()["offsite_backup_passphrase"] == ""

    def test_the_passphrase_is_not_rendered_into_the_settings_page(self, logged_in_client, data_dir):
        configure_offsite(passphrase="unmistakable-passphrase-value")
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "unmistakable-passphrase-value" not in html

    def test_a_nonsense_bucket_name_is_rejected_rather_than_stored(self, data_dir):
        ro.save_app_settings({"offsite_backup_bucket": "Not A Bucket!"})
        assert offsite.offsite_config()["offsite_backup_bucket"] == ""

    def test_the_schedule_and_retention_are_clamped(self, data_dir):
        ro.save_app_settings({"offsite_backup_hour": "99", "offsite_backup_minute": "-5",
                              "offsite_backup_keep": "0"})
        cfg = offsite.offsite_config()
        assert 0 <= cfg["offsite_backup_hour"] <= 23
        assert 0 <= cfg["offsite_backup_minute"] <= 59
        assert cfg["offsite_backup_keep"] >= 1

    def test_unknown_section_ids_are_dropped(self, data_dir):
        ro.save_app_settings({"offsite_backup_sections": ["database", "nonsense"]})
        assert offsite.offsite_config()["offsite_backup_sections"] == ["database"]

    def test_the_back_up_now_button_is_admin_only(self):
        assert "settings_offsite_backup_now" in ro.ADMIN_ONLY_ENDPOINTS


# ---------------------------------------------------------------------------
# The R2 request layer the backup needs beyond a plain PUT
# ---------------------------------------------------------------------------

class TestTheR2Layer:
    def test_a_listing_response_is_parsed(self):
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>false</IsTruncated>
          <Contents><Key>backups/a.zip</Key><Size>1234</Size><LastModified>2026-08-03T03:15:00.000Z</LastModified></Contents>
          <Contents><Key>backups/b.zip</Key><Size>2345</Size><LastModified>2026-08-04T03:15:00.000Z</LastModified></Contents>
        </ListBucketResult>"""
        objects, token = r2.parse_list_objects(body)
        assert [o["key"] for o in objects] == ["backups/a.zip", "backups/b.zip"]
        assert objects[0]["size_bytes"] == 1234
        assert token is None

    def test_a_truncated_listing_hands_back_its_continuation_token(self):
        """A bucket with more than a thousand objects would otherwise be silently
        half-listed, and a retention sweep would keep deleting the same page."""
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>true</IsTruncated>
          <NextContinuationToken>tok123</NextContinuationToken>
          <Contents><Key>backups/a.zip</Key><Size>1</Size></Contents>
        </ListBucketResult>"""
        _objects, token = r2.parse_list_objects(body)
        assert token == "tok123"

    def test_query_parameters_are_sorted_the_way_the_signature_requires(self):
        """R2 refuses the request if this ordering differs from the signed one."""
        assert r2.canonical_query({"prefix": "b", "list-type": "2"}) == "list-type=2&prefix=b"

    def test_the_upload_allowance_still_follows_the_file_size(self):
        """Shared with video publishing — the hut's 4G is one fact, not two."""
        assert r2.r2_upload_timeout_s(50 * 1024 ** 2) > r2.r2_upload_timeout_s(5 * 1024 ** 2)

    def test_an_aborted_upload_is_recognised_however_it_is_worded(self):
        import socket
        assert r2.looks_like_aborted_upload(OSError(10053, "An established connection was aborted"))
        assert r2.looks_like_aborted_upload("[WinError 10053] An established connection was aborted")
        assert r2.looks_like_aborted_upload(socket.error(10054, "reset by peer"))
        assert not r2.looks_like_aborted_upload("HTTP 404 Not Found")

    def test_an_r2_error_body_reaches_the_message(self):
        """"HTTP 403" alone gives an admin nothing to act on; R2 explains itself
        in the body (SignatureDoesNotMatch, NoSuchBucket)."""
        import urllib.error
        import io
        exc = urllib.error.HTTPError("https://example", 403, "Forbidden", {},
                                     io.BytesIO(b"<Error><Code>SignatureDoesNotMatch</Code></Error>"))
        assert "SignatureDoesNotMatch" in r2.error_message(exc)


class TestGivingUpOnStuckVideoUploads:
    """Reported from the hut: "84 race video upload(s) still in progress" kept
    deferring the nightly backup, and there was a button to *retry* them and
    nothing to stop trying.
    """

    def test_stuck_uploads_can_be_given_up_on(self, data_dir):
        for status in ("pending", "processing", "uploading"):
            add_video_clip(status, datetime.now() - timedelta(minutes=5))

        count = ro.abandon_stuck_public_video_uploads()

        assert count == 3
        with ro.get_db() as db:
            left = db.execute(
                "SELECT COUNT(*) AS n FROM video_clips "
                "WHERE COALESCE(public_status,'') IN ('pending','processing','uploading')").fetchone()
        assert int(left["n"]) == 0

    def test_that_unblocks_the_backup(self, data_dir):
        """The whole point: the guard stops counting them."""
        for _ in range(84):
            add_video_clip("uploading", datetime.now() - timedelta(minutes=5))
        assert offsite.hut_is_busy()[0]

        ro.abandon_stuck_public_video_uploads()

        busy, reason = offsite.hut_is_busy()
        assert not busy, reason

    def test_nothing_is_deleted(self, data_dir):
        """Only the publishing state changes — the evidence clip is the record of
        the race and must survive giving up on publishing it."""
        add_video_clip("uploading", datetime.now() - timedelta(minutes=5))
        with ro.get_db() as db:
            before = db.execute("SELECT COUNT(*) AS n FROM video_clips").fetchone()["n"]
            db.execute("UPDATE video_clips SET file_path = 'data/video_clips/keep.mp4', status = 'ready'")
            db.commit()

        ro.abandon_stuck_public_video_uploads()

        with ro.get_db() as db:
            row = db.execute("SELECT COUNT(*) AS n, file_path, status FROM video_clips").fetchone()
        assert row["n"] == before
        assert row["file_path"] == "data/video_clips/keep.mp4"
        assert row["status"] == "ready", "the evidence clip itself must be untouched"

    def test_a_ready_upload_is_left_alone(self, data_dir):
        add_video_clip("ready", datetime.now() - timedelta(minutes=5))
        assert ro.abandon_stuck_public_video_uploads() == 0

    def test_retry_can_still_resurrect_them_once_r2_is_fixed(self, data_dir):
        """Given up on, not written off. The retry button still selects abandoned
        clips, so fixing the bucket later and pressing Retry re-queues the lot."""
        import inspect

        import core.video as video_core

        selected = inspect.getsource(video_core.retry_public_video_uploads_once)
        assert "'abandoned'" in selected, "the retry button must still pick these up"

    def test_the_button_is_on_the_settings_page(self, logged_in_client, data_dir):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "Give up on stuck public video uploads" in html

    def test_it_is_admin_only(self):
        assert "settings_video_uploads_abandon" in ro.ADMIN_ONLY_ENDPOINTS

    def test_the_deferral_message_says_how_to_clear_it(self, data_dir):
        add_video_clip("uploading", datetime.now() - timedelta(minutes=5))
        _busy, reason = offsite.hut_is_busy()
        assert "Settings" in reason and "give up" in reason.lower()


class TestARunningBackupIsNotReportedAsDeferred:
    def test_a_scheduler_tick_does_not_overwrite_a_running_backup(self, data_dir):
        """The hut showed "Running now: Deferred — 84 race video upload(s)", which
        is two contradictory things. A scheduler tick during a forced run fell
        through to the busy check, deferred, and saved that over the live status."""
        cfg = configure_offsite()
        offsite.save_offsite_status({"ok": False, "message": "Uploading 39.3 MB to Cloudflare R2…"})
        add_video_clip("uploading", datetime.now() - timedelta(minutes=5))
        with offsite.OFFSITE_LOCK:
            offsite.OFFSITE_STATE["running"] = True
        try:
            result = offsite.run_offsite_backup_once(cfg)          # the scheduler tick
            status = offsite.read_offsite_status()
        finally:
            with offsite.OFFSITE_LOCK:
                offsite.OFFSITE_STATE["running"] = False

        assert "already running" in result["message"]
        assert not result.get("deferred")
        assert "39.3 MB" in status["message"], "the running progress line was overwritten"
