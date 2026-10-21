"""The club's name and logos come from Settings -> Club name and branding.

Pwllheli's were written into the pages: "Pwllheli Race Officer" at the top of every
race office page and tab, the Pwllheli banner across every public page, the
bundled burgee in the side menu and on published results, PWLLHELI SAILING CLUB on
the films, and Pwllheli's names for marks O to Y on the VHF. A club running the app
for itself now sets its own. The footers keep the software's name.
"""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

import app as ro
from core import appstate, courses, replay3d, settings
from core.races import get_race

TOKEN = "test-csrf-token"


def post(client, url, data=None, **kwargs):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    return client.post(url, data={"_csrf_token": TOKEN, **(data or {})}, **kwargs)


def save_club(client, name, short):
    resp = post(client, "/settings/save", {"club_name": name, "club_short_name": short})
    assert resp.status_code in (200, 302)


def png_bytes(colour=(200, 30, 30, 255), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, "PNG")
    buf.seek(0)
    return buf


class TestTheDefaultsArePwllheli:
    def test_the_names(self, client):
        club = settings.club_config()
        assert (club["name"], club["short_name"], club["app_name"]) == \
               ("Pwllheli Sailing Club", "Pwllheli", "Pwllheli Race Officer")

    def test_the_race_office_pages(self, logged_in_client):
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "Races - Pwllheli Race Officer</title>" in html
        assert 'brand-title">Pwllheli Race Officer<small>' in html

    def test_the_public_pages_keep_the_banner(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert "img/psc_public_header.png" in html
        assert 'alt="Clwb Hwylio Pwllheli Sailing Club"' in html


class TestAnotherClub:
    def test_the_top_of_the_race_office_pages(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "Races - Liverpool Race Officer</title>" in html
        assert 'brand-title">Liverpool Race Officer<small>' in html

    def test_the_footer_keeps_the_softwares_name(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "Pwllheli Race Officer v" in html

    def test_the_login_page(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        logged_in_client.get("/logout")
        html = logged_in_client.get("/admin/login").get_data(as_text=True)
        assert "<h1>Liverpool Race Officer</h1>" in html

    def test_its_public_pages_show_its_logo_and_name_not_the_pwllheli_banner(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        html = logged_in_client.get("/public/current").get_data(as_text=True)
        assert "psc_public_header.png" not in html
        assert '<span class="public-club-name">Liverpool Yacht Club</span>' in html
        assert "<title>Liverpool Yacht Club - Competitor information</title>" in html

    def test_an_uploaded_header_is_used(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        resp = post(logged_in_client, "/settings/branding/public_header",
                    {"public_header_upload": (png_bytes(size=(820, 74)), "banner.png")},
                    content_type="multipart/form-data")
        assert resp.status_code == 302
        manifest = json.loads(appstate.BRANDING_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert manifest["public_header"]
        html = logged_in_client.get("/public/current").get_data(as_text=True)
        assert f"/public/branding/{manifest['public_header']}" in html
        assert 'class="public-club-name"' not in html

    def test_removing_the_header(self, logged_in_client):
        post(logged_in_client, "/settings/branding/public_header",
             {"public_header_upload": (png_bytes(), "banner.png")}, content_type="multipart/form-data")
        post(logged_in_client, "/settings/branding/public_header/delete")
        manifest = json.loads(appstate.BRANDING_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert manifest["public_header"] == ""

    def test_a_form_without_the_fields_keeps_the_name(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        post(logged_in_client, "/settings/save", {"power_sample_seconds": "45"})
        assert settings.club_config()["name"] == "Liverpool Yacht Club"

    def test_clearing_the_field_goes_back_to_the_default(self, logged_in_client):
        save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
        save_club(logged_in_client, "", "")
        assert settings.club_config()["app_name"] == "Pwllheli Race Officer"

    def test_a_name_is_one_line(self, client):
        assert settings.clean_club_text("Liverpool\n  Yacht   Club", "x", 80) == "Liverpool Yacht Club"


class TestTheClubLogo:
    def test_uploading_it_no_longer_fails(self, logged_in_client):
        """It saved the logo and then answered 500: the audit named a variable
        that did not exist."""
        resp = post(logged_in_client, "/settings/branding/club_logo",
                    {"club_logo_upload": (png_bytes(), "lyc.png")}, content_type="multipart/form-data")
        assert resp.status_code == 302

    def test_it_is_in_the_side_menu(self, logged_in_client):
        post(logged_in_client, "/settings/branding/club_logo",
             {"club_logo_upload": (png_bytes(), "lyc.png")}, content_type="multipart/form-data")
        manifest = json.loads(appstate.BRANDING_MANIFEST_PATH.read_text(encoding="utf-8"))
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert f'brand-mark logo-mark"><img src="/public/branding/{manifest["club_logo"]}"' in html

    def test_published_results_use_it(self, logged_in_client):
        before = ro.published_logo_data_uri()
        post(logged_in_client, "/settings/branding/club_logo",
             {"club_logo_upload": (png_bytes(colour=(10, 200, 10, 255)), "lyc.png")},
             content_type="multipart/form-data")
        assert ro.published_logo_data_uri() != before


def test_the_film_names_the_club(logged_in_client):
    save_club(logged_in_client, "Liverpool Yacht Club", "Liverpool")
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at)"
                             " VALUES (1, 'River race', 1, '2026-10-20T14:00:00', '2026-10-20T14:00:00')")
            db.commit()
            race_id = int(cur.lastrowid)
    with ro.app.test_request_context():
        scene = replay3d.build_scene(race_id, videos=False)
    assert scene["club"] == {"name": "Liverpool Yacht Club"}


class TestTheSettingsPage:
    def test_the_bucket_has_a_section_of_its_own(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        r2 = html[html.index('id="r2"'):html.index('id="video"')]
        for field in ("video_public_r2_account_id", "video_public_r2_bucket", "video_public_r2_access_key_id",
                      "video_public_r2_secret_access_key", "video_public_r2_public_base_url",
                      "video_public_r2_prefix"):
            assert f'name="{field}"' in r2, field
        video = html[html.index('id="video"'):html.index('id="offsite-backup"')]
        assert "video_public_r2_account_id" not in video

    def test_the_club_section_comes_straight_after_users(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert html.index('id="users"') < html.index('id="branding"') < html.index('id="hardware"')
        assert 'name="club_name"' in html and 'name="club_short_name"' in html

    def test_the_r2_test_does_not_ask_for_video_publishing(self, logged_in_client):
        """Published results and the films use the bucket too; a club with no
        camera was told to turn video publishing on before it could test it."""
        resp = post(logged_in_client, "/settings/video/r2_test", {"video_public_provider": "off"},
                    follow_redirects=True)
        html = resp.get_data(as_text=True)
        assert "before testing upload" not in html
        assert "settings are incomplete" in html


class TestSpokenMarks:
    def test_pwllheli_is_unchanged(self, client):
        said = {k: courses.spoken_mark(k) for k in ("O", "F", "C", "E", "T", "M", "Y", "A", "YA", "1")}
        assert said == {"O": "outer distance mark", "F": "fairway", "C": "causeway", "E": "Eurwyn",
                        "T": "Tom buoy", "M": "Madog fairway", "Y": "Gwylan Islands", "A": "mark A",
                        "YA": "Gwylan Islands corner Ya", "1": "mark one"}

    def test_another_clubs_c_is_not_the_causeway(self, monkeypatch):
        monkeypatch.setattr(appstate, "MARKS", {"C": {"name": "Crosby"}, "PLK": {"name": "Pluckington"},
                                                "G11": {"name": "G11"}})
        assert courses.spoken_mark("C") == "Crosby"
        assert courses.spoken_mark("PLK") == "Pluckington"
        assert courses.spoken_mark("G11") == "mark G11"
