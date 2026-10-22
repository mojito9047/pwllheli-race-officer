"""The dashboard says what each machine runs, and whether GitHub has newer.

Three machines, each installed from a release: the app on the hut PC, the
relay, and the render machine. The relay is updated a file at a time by hand,
so it is judged by checksums of its files against this release's copies rather
than by a version number nobody remembers to change.

Nothing here touches the network: the fetchers are replaced, and the worker's
loop is neutralised for every test (conftest).
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from core import appstate, versions

ROOT = Path(__file__).resolve().parent.parent
LIVE_STREAM = ROOT / "deploy" / "live_stream"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def state(monkeypatch):
    """A fresh worker state, and this app pretending to be v1.019."""
    fresh = {"started": False, "github": {}, "github_at": 0.0, "relay": {}, "traccar": {},
             "renderer": {}, "local_at": 0.0, "local_checked_at": 0.0}
    monkeypatch.setattr(versions, "VERSIONS_STATE", fresh)
    monkeypatch.setattr(appstate, "APP_VERSION", "1.019")
    monkeypatch.setattr(versions, "UPDATE_CHECK_ENABLED", True)
    return fresh


class TestComparingVersions:
    @pytest.mark.parametrize("running, latest, verdict", [
        ("1.019", "v1.019", "current"),
        ("1.019", "v1.020", "upgrade"),
        ("1.020", "v1.019", "ahead"),
        ("1.009", "v1.010", "upgrade"),          # numerically, not as text
        ("dev", "v1.019", "unknown"),
        ("1.019", "", "unknown"),
    ])
    def test_the_verdict(self, running, latest, verdict):
        assert versions.compare(running, latest) == verdict


class TestTheRelayIsJudgedByItsFiles:
    def test_the_relay_script_and_the_hut_agree_on_every_file(self, monkeypatch):
        """Run the relay's own script over this repository's copies: all must match."""
        relay_version = _load(LIVE_STREAM / "relay_version.py", "relay_version_under_test")
        monkeypatch.setattr(relay_version, "FILES", {name: str(LIVE_STREAM / name) for name in relay_version.FILES})
        monkeypatch.setattr(relay_version, "OPTIONAL", {})
        monkeypatch.setattr(appstate, "BASE_DIR", ROOT)
        verdict = versions.compare_relay_files(relay_version.inventory()["files"])
        assert verdict["differ"] == [] and verdict["missing"] == [] and verdict["unknown"] == []
        assert len(verdict["matched"]) == len(relay_version.FILES)

    def test_every_file_the_relay_lists_is_in_the_release(self):
        """A renamed file would leave the relay listing something the hut cannot check."""
        relay_version = _load(LIVE_STREAM / "relay_version.py", "relay_version_names")
        for name in list(relay_version.FILES) + list(relay_version.OPTIONAL):
            assert (LIVE_STREAM / name).is_file(), name

    def test_the_files_with_local_edits_and_secrets_are_not_listed(self):
        relay_version = _load(LIVE_STREAM / "relay_version.py", "relay_version_exclusions")
        listed = set(relay_version.FILES) | set(relay_version.OPTIONAL)
        assert not listed & {"Caddyfile", "startline_config.json", "lxc/relay.env.example"}

    def test_a_crlf_copy_is_the_same_file(self, tmp_path, monkeypatch):
        """The hut's copies come out of a ZIP built on Windows."""
        (tmp_path / "deploy" / "live_stream").mkdir(parents=True)
        (tmp_path / "deploy" / "live_stream" / "relay_wind.py").write_bytes(b"a = 1\r\nb = 2\r\n")
        monkeypatch.setattr(appstate, "BASE_DIR", tmp_path)
        lf = versions.relay_file_hash(_write(tmp_path / "lf.py", b"a = 1\nb = 2\n"))
        assert versions.compare_relay_files({"relay_wind.py": lf})["matched"] == ["relay_wind.py"]

    def test_differ_missing_and_unknown_are_told_apart(self, tmp_path, monkeypatch):
        base = tmp_path / "deploy" / "live_stream"
        base.mkdir(parents=True)
        for name in ("same.py", "changed.py", "gone.py"):
            (base / name).write_bytes(name.encode())
        monkeypatch.setattr(appstate, "BASE_DIR", tmp_path)
        verdict = versions.compare_relay_files({
            "same.py": versions.relay_file_hash(base / "same.py"),
            "changed.py": "0" * 64,
            "gone.py": None,
            "newer_than_this_release.py": "1" * 64,
            "../../app.py": "2" * 64,                 # never read outside the folder
        })
        assert verdict == {"matched": ["same.py"], "differ": ["changed.py"], "missing": ["gone.py"],
                           "unknown": ["newer_than_this_release.py"]}


def _write(path, data):
    path.write_bytes(data)
    return path


class TestFetchingFromTheRelay:
    def test_without_a_public_address_it_says_where_to_set_one(self, monkeypatch):
        monkeypatch.setattr("core.horn.public_base_url", lambda: "")
        assert "Public address" in versions.fetch_relay()["message"]

    def test_a_relay_without_the_block_is_told_from_one_that_is_down(self, monkeypatch):
        monkeypatch.setattr("core.horn.public_base_url", lambda: "https://pro.example.org")

        def not_found(url, headers=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))

        monkeypatch.setattr(versions, "_get_json", not_found)
        assert "does not list its files yet" in versions.fetch_relay()["message"]

        def down(url, headers=None):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(versions, "_get_json", down)
        assert "Could not reach the relay" in versions.fetch_relay()["message"]

    def test_its_list_is_compared(self, monkeypatch):
        monkeypatch.setattr("core.horn.public_base_url", lambda: "https://pro.example.org")
        seen = {}

        def fake(url, headers=None):
            seen["url"] = url
            return {"schema": 1, "generated_at": "2026-10-22T10:00:00+00:00",
                    "files": {"relay_wind.py": versions.relay_file_hash(LIVE_STREAM / "relay_wind.py")}}

        monkeypatch.setattr(versions, "_get_json", fake)
        monkeypatch.setattr(appstate, "BASE_DIR", ROOT)
        result = versions.fetch_relay()
        assert seen["url"] == "https://pro.example.org/relay/version.json"
        assert result["ok"] and result["matched"] == ["relay_wind.py"]


class TestWhatTheDashboardShows:
    def test_everything_current(self, state):
        state.update(github={"version": "1.019", "tag": "v1.019", "url": "https://x/v1.019"},
                     relay={"ok": True, "matched": ["a", "b"], "differ": [], "missing": [], "unknown": []},
                     traccar={"version": "6.6"},
                     renderer={"configured": True, "seen": True, "up": True, "release": "1.019"},
                     local_checked_at=1.0)
        status = versions.versions_status()
        assert [r["state"] for r in status["rows"]] == ["current", "current", "current"]
        assert not status["upgrade_available"]
        assert "Traccar 6.6" in status["rows"][1]["detail"]

    def test_a_newer_release_shows_as_an_upgrade_on_every_machine_behind_it(self, state):
        state.update(github={"version": "1.020", "tag": "v1.020", "url": "https://x/v1.020"},
                     relay={"ok": True, "matched": ["a"], "differ": [], "missing": [], "unknown": []},
                     renderer={"configured": True, "seen": True, "up": True, "release": "1.018"},
                     local_checked_at=1.0)
        status = versions.versions_status()
        assert [r["state"] for r in status["rows"]] == ["upgrade", "upgrade", "upgrade"]
        assert status["upgrade_available"]

    def test_relay_files_out_of_step_are_named(self, state):
        state.update(github={"version": "1.019", "tag": "v1.019"},
                     relay={"ok": True, "matched": ["a"], "differ": ["relay_wind.py"], "missing": ["site/index.html"],
                            "unknown": []},
                     renderer={"configured": False}, local_checked_at=1.0)
        status = versions.versions_status()
        relay = status["rows"][1]
        assert relay["state"] == "differs" and status["upgrade_available"]
        assert "relay_wind.py" in relay["detail"] and "site/index.html (missing)" in relay["detail"]
        assert [r["key"] for r in status["rows"]] == ["app", "relay"], "no bucket, so no render machine row"

    def test_a_render_machine_from_before_it_reported_says_so(self, state):
        state.update(github={"version": "1.020", "tag": "v1.020"},
                     renderer={"configured": True, "seen": True, "up": False, "release": "", "updated_at": 0.0},
                     local_checked_at=1.0)
        row = versions.versions_status()["rows"][2]
        assert row["state"] == "unknown"
        assert "before render machines reported" in row["detail"] and "switched off" in row["detail"]

    def test_github_out_of_reach_still_shows_what_is_running(self, state):
        state.update(github={"error": "Could not check GitHub (URLError)."}, local_checked_at=1.0,
                     relay={"ok": False, "message": "x"}, renderer={"configured": False})
        status = versions.versions_status()
        assert status["rows"][0]["running"] == "v1.019" and status["rows"][0]["state"] == "unknown"
        assert "Could not check GitHub" in status["note"]

    def test_the_check_can_be_switched_off(self, state, monkeypatch):
        monkeypatch.setattr(versions, "UPDATE_CHECK_ENABLED", False)
        assert "switched off" in versions.versions_status()["note"]


class TestTheWorker:
    def test_it_fetches_what_is_due_and_github_less_often(self, state, monkeypatch):
        calls = {"github": 0, "relay": 0}
        monkeypatch.setattr(versions, "fetch_latest_release",
                            lambda: (calls.__setitem__("github", calls["github"] + 1), {"version": "1.019"})[1])
        monkeypatch.setattr(versions, "fetch_relay",
                            lambda: (calls.__setitem__("relay", calls["relay"] + 1), {"ok": True})[1])
        monkeypatch.setattr(versions, "fetch_traccar", lambda: {})
        monkeypatch.setattr(versions, "fetch_renderer", lambda: {"configured": False})

        versions.refresh(now=1000.0)
        versions.refresh(now=1000.0 + versions.LOCAL_EVERY_S + 1)

        assert calls == {"github": 1, "relay": 2}

    def test_a_github_failure_is_retried_sooner_than_a_success(self, state, monkeypatch):
        def boom():
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(versions, "fetch_latest_release", boom)
        for name in ("fetch_relay", "fetch_traccar", "fetch_renderer"):
            monkeypatch.setattr(versions, name, lambda: {})
        versions.refresh(now=0.0)
        assert state["github_at"] == versions.GITHUB_RETRY_S
        assert "Could not check GitHub" in state["github"]["error"]

    def test_one_part_failing_does_not_stop_the_others(self, state, monkeypatch):
        def boom():
            raise RuntimeError("traccar down")

        monkeypatch.setattr(versions, "fetch_latest_release", lambda: {"version": "1.019"})
        monkeypatch.setattr(versions, "fetch_relay", lambda: {"ok": True, "matched": ["a"]})
        monkeypatch.setattr(versions, "fetch_traccar", boom)
        monkeypatch.setattr(versions, "fetch_renderer", lambda: {"configured": False})
        versions.refresh(now=0.0)
        assert state["relay"]["ok"] and state["traccar"]["error"] == "RuntimeError"

    def test_the_latest_release_is_read_from_github(self, monkeypatch):
        seen = {}

        def fake(url, headers=None):
            seen["url"] = url
            return {"tag_name": "v1.020", "html_url": "https://github.com/x/releases/tag/v1.020",
                    "published_at": "2026-10-30T12:00:00Z"}

        monkeypatch.setattr(versions, "_get_json", fake)
        latest = versions.fetch_latest_release()
        assert seen["url"] == f"https://api.github.com/repos/{versions.RELEASES_REPO}/releases/latest"
        assert latest["version"] == "1.020" and latest["tag"] == "v1.020"


class TestTheRenderMachineReportsItsRelease:
    def test_the_heartbeat_carries_the_release_beside_it(self):
        sys.path.insert(0, str(ROOT / "scripts" / "replay3d"))
        renderer = _load(ROOT / "scripts" / "replay3d" / "renderer.py", "renderer_under_test")
        written = {}

        class Store:
            def put_json(self, key, value):
                written[key] = value

        renderer.heartbeat(Store(), "test-box")
        beat = next(iter(written.values()))
        assert beat["release"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


class TestTheDashboardCard:
    def test_it_is_on_the_dashboard_without_reaching_the_network(self, logged_in_client):
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert "Software versions" in html
        assert f"v{appstate.APP_VERSION}" in html
        assert "Checking GitHub" in html or "Checking…" in html


def test_the_app_line_names_the_hut_pc_not_whoever_is_looking(state):
    """The dashboard is read from laptops and phones through the relay as often as
    on the hut PC itself, so "this PC" was untrue more often than not."""
    assert versions.versions_status()["rows"][0]["name"] == "Race Officer (hut PC)"
