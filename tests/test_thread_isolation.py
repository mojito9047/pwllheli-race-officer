"""Regression guard for the R2 public-live uploader daemon leaking across tests.

settings_save / hardware_save call start_public_live_r2_uploader(); with an R2
provider configured that launches a daemon looping on upload_public_live_frame_once.
Its "started" latch is never reset between tests, so it kept running and, when a
later test monkeypatched video.upload_file_to_r2 / public_live_frame_path, the
background call ran the test's fakes and clobbered its shared assertion state
(made test_upload_public_live_frame_once_uses_stable_r2_key flaky).

The autouse `no_r2_uploader_thread` fixture (conftest) neutralises the loop body
so even if the daemon is launched it performs no uploads. This test verifies that
guarantee survives — including the routes/ split, where route modules bind their
own copy of the launcher (so patching it on the app module alone would miss it).
"""
import time

import app as ro


def test_settings_save_r2_triggers_no_background_upload(logged_in_client, monkeypatch):
    uploads = []
    monkeypatch.setattr(ro.video, "upload_file_to_r2",
                        lambda *a, **k: uploads.append(a) or "https://example/x")
    monkeypatch.setattr(ro.video, "public_live_frame_path", lambda: __file__)

    token = "test-csrf-token"
    with logged_in_client.session_transaction() as sess:
        sess["_csrf_token"] = token
    resp = logged_in_client.post("/admin/settings/save", data={
        "_csrf_token": token,
        "video_public_live_provider": "r2",
        "video_public_r2_account_id": "0" * 32,
        "video_public_r2_bucket": "race-media",
        "video_public_r2_access_key_id": "key-id",
        "video_public_r2_secret_access_key": "secret",
        "video_public_r2_public_base_url": "https://media.example.com",
        "video_public_r2_prefix": "race-videos",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    # Give any (neutralised) background uploader thread a chance to run.
    time.sleep(0.2)
    assert uploads == [], "the R2 uploader loop must be neutralised in tests"
