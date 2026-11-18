"""The competitor pages can be added to a phone's home screen.

Competitors have followed these races for years in an app with an icon on their
phone. The club's own pages were a web address: nothing to tap, and every visit
began in a browser with its bars round it. A web app manifest and a set of icons
are what let a phone put the page on the home screen and open it full-screen.

What matters about them, and so what is tested:

* a phone fetching them is signed in to nothing, so they must be on the
  allow-list (``public_endpoint()``), where a new route is private by default;
* the name and the picture are the club's own from Settings -> Club name and
  branding, not Pwllheli's written in, so another club gets its own;
* the installed app covers the competitor pages only (``scope``), never the race
  office behind the login;
* both public pages carry the tags, because either may be the one a competitor
  has open when they add it.
"""
from __future__ import annotations

import io

from PIL import Image

from core import appicon
from tests.test_club_branding import png_bytes, post, save_club


class TestTheManifest:
    def test_a_phone_can_fetch_it_without_signing_in(self, client):
        resp = client.get("/public/app.webmanifest")
        assert resp.status_code == 200
        assert resp.mimetype == "application/manifest+json"

    def test_it_names_the_club_and_starts_on_the_competitor_home(self, client):
        manifest = client.get("/public/app.webmanifest").get_json()
        assert manifest["name"] == "Pwllheli Sailing Club"
        assert manifest["short_name"] == "Pwllheli"
        assert manifest["start_url"] == "/public/current"
        assert manifest["display"] == "standalone"

    def test_it_covers_the_competitor_pages_and_not_the_race_office(self, client):
        manifest = client.get("/public/app.webmanifest").get_json()
        assert manifest["scope"] == "/public/"
        assert manifest["start_url"].startswith(manifest["scope"])

    def test_another_club_gets_its_own_name(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "LYC")
        manifest = logged_in_client.get("/public/app.webmanifest").get_json()
        assert (manifest["name"], manifest["short_name"]) == ("Liverpool Yacht Club", "LYC")

    def test_every_icon_it_names_is_really_served(self, client):
        manifest = client.get("/public/app.webmanifest").get_json()
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        assert {"192x192", "512x512"} <= sizes
        assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
        for icon in manifest["icons"]:
            assert client.get(icon["src"]).status_code == 200, icon["src"]


class TestTheIcons:
    def test_each_size_is_a_square_png_of_that_size(self, client):
        for size in appicon.ICON_SIZES:
            resp = client.get(f"/public/app-icon/{size}.png")
            assert resp.status_code == 200 and resp.mimetype == "image/png"
            with Image.open(io.BytesIO(resp.data)) as icon:
                assert icon.size == (size, size)

    def test_no_transparent_corners(self, client):
        """An iPhone paints transparency black, which turns the corners of a
        logo with a clear background into a black square."""
        resp = client.get("/public/app-icon/180.png")
        with Image.open(io.BytesIO(resp.data)) as icon:
            assert icon.mode == "RGB"
            assert icon.getpixel((0, 0)) == appicon.PAPER[:3]

    def test_the_logo_stays_inside_the_part_android_may_keep(self, client):
        """A maskable icon can be cut to a circle over the middle 80%. The edge
        of the square, outside that, must be paper and nothing else."""
        resp = client.get("/public/app-icon/512.png")
        with Image.open(io.BytesIO(resp.data)) as icon:
            margin = int(512 * 0.1)
            for xy in ((256, margin // 2), (margin // 2, 256), (511 - margin // 2, 256),
                       (256, 511 - margin // 2)):
                assert icon.getpixel(xy) == appicon.PAPER[:3], xy

    def test_only_the_sizes_phones_ask_for(self, client):
        assert client.get("/public/app-icon/9999.png").status_code == 404

    def test_an_uploaded_club_logo_becomes_the_icon(self, logged_in_client):
        before = logged_in_client.get("/public/app-icon/192.png").data
        resp = post(logged_in_client, "/settings/branding/club_logo",
                    {"club_logo_upload": (png_bytes(colour=(200, 30, 30, 255), size=(80, 60)),
                                          "logo.png")},
                    content_type="multipart/form-data")
        assert resp.status_code in (200, 302)
        after = logged_in_client.get("/public/app-icon/192.png").data
        assert after != before
        with Image.open(io.BytesIO(after)) as icon:
            assert icon.getpixel((96, 96)) == (200, 30, 30)

    def test_a_logo_that_cannot_be_read_still_gives_an_icon(self, tmp_path):
        bad = tmp_path / "not_an_image.png"
        bad.write_bytes(b"this is not a picture")
        data = appicon.render_app_icon(bad, 192)
        assert data == appicon.FALLBACK_ICON_PATH.read_bytes()


class TestBothPublicPagesCarryTheTags:
    TAGS = ('rel="manifest" href="/public/app.webmanifest"',
            'rel="apple-touch-icon" href="/public/app-icon/180.png"',
            'name="apple-mobile-web-app-capable" content="yes"',
            'name="theme-color"',
            "public_app.js")

    def test_the_competitor_home(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        for tag in self.TAGS:
            assert tag in html, tag

    def test_the_race_page(self, client):
        from tests.test_competitor_follows_the_current_race import _race
        rid = _race("Race One")
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        for tag in self.TAGS:
            assert tag in html, tag

    def test_the_title_on_an_iphone_home_screen_is_the_club(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "LYC")
        html = logged_in_client.get("/public/current").get_data(as_text=True)
        assert 'name="apple-mobile-web-app-title" content="LYC"' in html

    def test_the_race_office_pages_do_not(self, logged_in_client):
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "app.webmanifest" not in html
