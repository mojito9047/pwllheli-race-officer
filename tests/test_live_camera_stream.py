"""Live camera panels: snapshots first, the relay's live stream once it plays.

Both the public competitor home page and the race sheet's finish camera use the
shared live_camera.js module. The relay stream is only offered when a live-stream
URL is configured; without one, the panels keep their snapshot-only behaviour.
"""
from __future__ import annotations

from datetime import datetime

import app as ro

STREAM_URL = "https://live.example.org/live"


def _settings(**extra):
    """save_app_settings writes the whole settings block, so pass every key at once."""
    ro.save_app_settings({"public_live_stream_url": STREAM_URL, **extra})


def _race():
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
            " VALUES ('Camera Race', 'IRC', ?, ?, 'IRC_TCC', '', ?)",
            (ro.appstate.COURSES[0]["course_no"], "2026-06-23T10:00:00", "2026-06-23T09:00:00"),
        )
        db.commit()
        return int(cur.lastrowid)


class TestSharedModule:
    def test_module_is_served(self, client):
        body = client.get("/static/live_camera.js").get_data(as_text=True)
        assert "PwllheliLiveCamera" in body
        assert "psc-live" in body                     # the relay page's message
        assert "live-camera-snapshot" in body

    def test_css_can_actually_hide_the_snapshot(self, client):
        """.live-video-frame sets display:block, which beats [hidden] on its own."""
        css = client.get("/static/style.css").get_data(as_text=True)
        assert ".live-camera .live-camera-snapshot[hidden]" in css
        assert ".live-camera .live-camera-stream[hidden]" in css

    def test_a_silent_frame_is_never_revealed(self, client):
        """A frame the browser refuses to load posts nothing at all, and must not
        be shown — that is what put a "refused to connect" box on the page in
        v0.182. A frame that said "connecting" did load, so it is shown even if
        "playing" never follows (v0.186)."""
        body = client.get("/static/live_camera.js").get_data(as_text=True)
        assert "giveUpOnStream" in body
        assert "setTimeout(() => giveUpOnStream(container), blocked)" in body
        assert "Live video is not available here" in body
        # The reveal timer is armed only inside the 'connecting' branch.
        connecting_branch = body.split("data.state === 'connecting'")[1].split("data.state === 'error'")[0]
        assert "showStream(container), reveal" in connecting_branch

    def test_loading_frame_is_rendered_not_display_none(self, client):
        """A display:none iframe may never start playing, so it would never report
        that it is — the frame loads at opacity 0 behind the snapshot instead."""
        body = client.get("/static/live_camera.js").get_data(as_text=True)
        assert "frame.dataset.loading = '1'" in body
        assert "frame.hidden = true" not in body
        css = client.get("/static/style.css").get_data(as_text=True)
        assert ".live-camera .live-camera-stream[data-loading]" in css
        loading_rule = css.split(".live-camera .live-camera-stream[data-loading]")[1].split("}")[0]
        assert "opacity: 0" in loading_rule
        assert "display: none" not in loading_rule

    def test_connecting_buys_time_instead_of_giving_up(self, client):
        """The on-demand stream can take longer to start than the old 15 s grace.

        "connecting" proves the frame loaded (so it is not blocked); only silence
        means blocked. They must have different timeouts, or a slow cold start
        looks identical to a refused frame — which is what left the panel stuck
        on stills after v0.183.
        """
        body = client.get("/static/live_camera.js").get_data(as_text=True)
        assert "DEFAULT_BLOCKED_MS" in body and "DEFAULT_STARTING_MS" in body
        assert "data.state === 'connecting'" in body
        # Patience once connecting is heard must exceed the silence timeout.
        blocked = int(body.split("DEFAULT_BLOCKED_MS = ")[1].split(";")[0])
        starting = int(body.split("DEFAULT_STARTING_MS = ")[1].split(";")[0])
        assert starting > blocked * 5, (blocked, starting)

    def test_relay_page_prefers_hls_js_over_native_hls(self):
        """Chrome answers "maybe" to canPlayType for HLS on some builds and then
        cannot demux the playlist (DEMUXER_ERROR_COULD_NOT_PARSE), leaving a
        player paused at 0:00 on a healthy stream. hls.js must be tried first;
        native HLS is only for Safari/iOS, where hls.js reports unsupported."""
        page = open("deploy/live_stream/site/index.html", encoding="utf-8").read()
        hls_branch = page.index("if (window.Hls && Hls.isSupported())")
        native_branch = page.index('v.canPlayType("application/vnd.apple.mpegurl")', hls_branch)
        assert hls_branch < native_branch

    def test_relay_page_recovers_a_paused_player(self):
        """Autoplay can be declined or not re-armed after a retry; the page asks
        for playback on new data and watches for a paused-but-loaded player."""
        page = open("deploy/live_stream/site/index.html", encoding="utf-8").read()
        assert "function tryPlay()" in page
        assert 'v.addEventListener("canplay", tryPlay)' in page
        assert "v.paused && v.readyState >= 2" in page      # watchdog
        assert "Tap to start the live view" in page         # if autoplay is refused

    def test_relay_watch_page_reports_its_state(self):
        """The relay page must post state, or panels can never switch on cue."""
        page = open("deploy/live_stream/site/index.html", encoding="utf-8").read()
        assert 'source: "psc-live"' in page
        assert 'post("connecting")' in page        # tells the panel the frame loaded
        assert 'post("playing")' in page
        assert 'post("error")' in page
        assert "window.parent === window" in page     # silent when not embedded


class TestRelayFramePolicy:
    def test_caddyfile_lets_a_local_app_embed_the_watch_page(self):
        """frame-ancestors must list the app's own origins, or the hut PC and a
        test server on http://localhost:5050 get "refused to connect"."""
        caddy = open("deploy/live_stream/Caddyfile", encoding="utf-8").read()
        line = next(ln for ln in caddy.splitlines() if "frame-ancestors" in ln)
        assert "'self'" in line
        assert "https://*.pwllhelisailingclub.org" in line
        assert "http://localhost:5050" in line
        assert "http://127.0.0.1:5050" in line


class TestPublicHomeCamera:
    def test_panel_offers_the_stream_when_configured(self, client):
        _settings()
        html = client.get("/public/current").get_data(as_text=True)
        assert 'class="live-video-frame-wrap live-camera"' in html
        assert f'data-stream-page="{STREAM_URL}"' in html
        assert "data-snapshot-src=" in html
        assert "live_camera.js" in html
        # The "starting" wording now lives on the panel's notes (the header text
        # was trimmed in v0.188 so the video can fill the width on a phone).
        assert 'data-snapshot-note="Still pictures while the live stream starts' in html
        assert "data-starting-note=" in html

    def test_no_watch_live_button_on_the_home_camera(self, client):
        """v0.188: the video plays inline, so the separate button is gone and the
        header is a single line."""
        _settings()
        html = client.get("/public/current").get_data(as_text=True)
        pane = html[html.index('id="htab-camera"'):html.index('id="competitorHomeTabs"') + 1] \
            if 'id="htab-camera"' in html else html
        assert "camera-panel-actions" not in html
        assert "The view from the start hut, while this tab is open." in html

    def test_phone_css_lets_the_camera_fill_the_width(self, client):
        """On a phone the camera pane drops its padding and reclaims the tabs
        card's padding (via :has) so the 16:9 video is near edge-to-edge."""
        css = client.get("/static/style.css").get_data(as_text=True)
        assert "#htab-camera { padding: 0; }" in css
        assert ".public-tabs:has(#htab-camera.active)" in css

    def test_snapshot_only_without_a_stream_url(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert "data-stream-page" not in html
        assert "data-snapshot-src=" in html            # snapshots still work
        assert "Updated every few seconds." in html

    def test_snapshot_image_no_longer_carries_its_own_src(self, client):
        """The container owns the refresh loop, so the img starts empty."""
        html = client.get("/public/current").get_data(as_text=True)
        img = html[html.index('id="publicCameraFrame"'):]
        img = img[:img.index(">")]
        assert "live-camera-snapshot" in img
        assert "data-src=" not in img


class TestRaceSheetFinishCamera:
    def test_finish_camera_offers_the_stream(self, logged_in_client):
        _settings(video_enabled="1")
        race_id = _race()
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'id="liveVideoCamera"' in html
        assert f'data-stream-page="{STREAM_URL}"' in html
        # The race officer is warned the stream lags the water.
        assert "a few seconds behind the water" in html

    def test_finish_camera_falls_back_to_stills(self, logged_in_client):
        ro.save_app_settings({"video_enabled": "1"})
        race_id = _race()
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'id="liveVideoCamera"' in html
        assert "data-stream-page" not in html
        assert "Near-live stills from the hut recorder." in html


class TestStreamUrlInContext:
    def test_the_race_sheet_gets_the_url_from_the_context_processor(self, logged_in_client):
        """race_detail does not pass it; the processor makes it available anyway."""
        _settings(video_enabled="1")
        race_id = _race()
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert STREAM_URL in html

    def test_a_bad_url_is_not_rendered(self, logged_in_client):
        ro.save_app_settings({"public_live_stream_url": "javascript:alert(1)", "video_enabled": "1"})
        race_id = _race()
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "javascript:alert" not in html          # sanitize_public_url drops it
        assert "data-stream-page" not in html
