"""A sponsor's logo on the published results links to their website.

The club's sponsors pay for the banner across the top of every published
results document, and until now the logos were pictures. A website can be
recorded against each one in Settings, and the logo becomes a link to it.

The address needs checking rather than trusting. This document is written to
the club's public bucket and opened by competitors, so an ``href`` typed into
Settings is an ``href`` served from the club's own domain: a ``javascript:``
entry there would be script, not a bad link. Only http and https get through,
and a sponsor who types "partingtonmarine.co.uk" -- which is what somebody will
type -- gets https rather than a refusal.
"""
from __future__ import annotations

import pathlib

import pytest

from core.video import safe_sponsor_url

TPL = (pathlib.Path(__file__).resolve().parent.parent
       / "templates" / "series_publish.html").read_text(encoding="utf-8")


class TestOnlyAWebAddressBecomesALink:
    @pytest.mark.parametrize("typed", [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "  javascript:alert(1)  ",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox",
        "file:///c:/windows",
    ])
    def test_a_scheme_that_is_not_the_web_is_refused(self, typed):
        """Not rewritten into something harmless -- refused.

        The first attempt tested for "://" and prepended https to anything
        without it, which turned javascript:alert(1) into the nonsense host
        https://javascript:alert(1): a dead link rather than a dangerous one,
        but input that should have been rejected being quietly mangled.
        """
        assert safe_sponsor_url(typed) == ""

    @pytest.mark.parametrize("typed", ["//evil.example", "/results/series-7", "  "])
    def test_something_that_is_not_an_address_is_refused(self, typed):
        assert safe_sponsor_url(typed) == ""

    def test_a_protocol_relative_address_is_refused(self):
        """It would inherit https from the page and point off the club's site
        while looking, in Settings, like a bare domain."""
        assert safe_sponsor_url("//evil.example/path") == ""

    @pytest.mark.parametrize("typed", [
        'https://x.example/"onmouseover=alert(1)',
        "https://x.example/<script>",
        "https://x.example/a b",
        "https://x.example/\nnewline",
    ])
    def test_anything_that_could_break_out_of_the_attribute_is_refused(self, typed):
        assert safe_sponsor_url(typed) == ""

    def test_a_very_long_address_is_refused(self):
        assert safe_sponsor_url("https://x.example/" + "a" * 400) == ""

    def test_what_somebody_actually_types_is_accepted(self):
        assert safe_sponsor_url("partingtonmarine.co.uk") == "https://partingtonmarine.co.uk"
        assert safe_sponsor_url("www.jacydo.com") == "https://www.jacydo.com"

    def test_a_full_address_is_kept_as_given(self):
        assert safe_sponsor_url("http://x.example/a?b=1") == "http://x.example/a?b=1"
        assert safe_sponsor_url("https://x.example/a#b") == "https://x.example/a#b"

    def test_nothing_entered_means_no_link(self):
        assert safe_sponsor_url("") == ""
        assert safe_sponsor_url(None) == ""


class TestTheBannerLinksWhereThereIsAWebsite:
    def test_a_sponsor_with_a_website_is_wrapped_in_a_link(self):
        assert 'class="publish-sponsor-link"' in TPL
        assert "{% if logo.website %}" in TPL

    def test_it_opens_in_a_new_tab_without_handing_over_the_page(self):
        """rel=noopener: the results document is a page the club published, and
        a sponsor's site should not get a handle on it."""
        block = TPL.split('class="publish-sponsor-link"')[1][:300]
        assert 'target="_blank"' in block
        assert "noopener" in block

    def test_a_sponsor_without_one_is_still_just_a_picture(self):
        """Most clubs will fill these in slowly, or never."""
        assert "{% else %}" in TPL.split("{% if logo.website %}")[1][:600]

    def test_a_linked_logo_is_the_same_size_as_a_plain_one(self):
        """Measured at five widths: 160/160 on a desktop, 92/92 on a phone.

        The anchor carries the width cap, because with none at all it sized
        itself from content that was sizing itself from the anchor and linked
        sponsors came out at 78px beside unlinked ones at 160. The *height*
        cap stays on the image -- see TestALinkedLogoIsStillCappedInHeight for
        what putting a percentage one here cost.
        """
        link = TPL.split(".publish-sponsor-link { display: inline-flex")[1].split("}")[0]
        assert "max-width: 160px" in link

    def test_and_on_a_phone_too(self):
        """The nested rule outranks a bare .publish-sponsor-logo, so a cap set
        only on the image loses to it inside the media query."""
        phone = TPL.split("@media (max-width: 700px) {")[1].split("@media")[0]
        assert ".publish-sponsor-logo, .publish-sponsor-link { max-height: 32px; max-width: 92px; }" in phone

    def test_the_link_shrinks_with_the_strip(self):
        """It is the flex item for a linked sponsor, so it carries the rules
        that stop the banner overflowing."""
        rule = TPL.split(".publish-sponsor-logo, .publish-sponsor-link {")[1].split("}")[0]
        assert "min-width: 0" in rule and "flex: 0 1 auto" in rule


class TestTheSponsorsAreNamed:
    def test_the_published_page_uses_the_sponsor_label(self, monkeypatch):
        """They were all called "Sponsor".

        ``branding_assets()`` returns each sponsor under "label"; the publish
        model asked for "name", which nothing sets, so every logo on every
        published document carried alt text of "Sponsor logo" -- and that is
        what a screen reader read out, and now what the link is announced as.
        """
        import app as ro

        logo = pathlib.Path(ro.DEFAULT_CLUB_LOGO_PATH)
        monkeypatch.setattr(ro, "branding_assets", lambda: {
            "enabled": True, "club_logo_enabled": False,
            "sponsors": [{"label": "Partington Marine", "path": logo,
                          "website": "https://partingtonmarine.co.uk"}],
        })
        with ro.app.app_context():
            logos = ro.published_branding_logos()
        sponsors = [x for x in logos if x["kind"] == "sponsor"]
        assert sponsors, "the sponsor was dropped"
        assert sponsors[0]["name"] == "Partington Marine"
        assert sponsors[0]["website"] == "https://partingtonmarine.co.uk"

    def test_a_sponsor_with_no_website_carries_an_empty_one(self, monkeypatch):
        import app as ro

        logo = pathlib.Path(ro.DEFAULT_CLUB_LOGO_PATH)
        monkeypatch.setattr(ro, "branding_assets", lambda: {
            "enabled": True, "club_logo_enabled": False,
            "sponsors": [{"label": "Free slot", "path": logo}],
        })
        with ro.app.app_context():
            logos = ro.published_branding_logos()
        assert [x for x in logos if x["kind"] == "sponsor"][0]["website"] == ""


class TestSavingAWebsiteFromSettings:
    """The template can be right and the Save button still fail.

    A sponsor already uploaded gets its website from a small form in the
    branding table, so there is a route behind it, and the only way to know it
    works is to press it.
    """

    @staticmethod
    def _sponsor(monkeypatch, tmp_path, **extra):
        """One sponsor in a manifest of our own, not the developer's."""
        import json

        from core import appstate, video

        branding = tmp_path / "branding"
        branding.mkdir(parents=True, exist_ok=True)
        (branding / "sponsor_abc123.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
        manifest = branding / "sponsor_logos.json"
        manifest.write_text(json.dumps({
            "club_logo": "",
            "sponsors": [dict({"id": "abc123", "label": "Partington",
                               "filename": "sponsor_abc123.png"}, **extra)],
        }), encoding="utf-8")
        monkeypatch.setattr(appstate, "BRANDING_DIR", branding)
        monkeypatch.setattr(appstate, "BRANDING_MANIFEST_PATH", manifest)
        monkeypatch.setattr(video.appstate, "BRANDING_DIR", branding)
        monkeypatch.setattr(video.appstate, "BRANDING_MANIFEST_PATH", manifest)
        return manifest

    def _saved(self, manifest):
        import json
        return json.loads(manifest.read_text(encoding="utf-8"))["sponsors"][0]

    def test_a_website_is_saved_against_the_sponsor(self, logged_in_client, csrf_post,
                                                    monkeypatch, tmp_path):
        manifest = self._sponsor(monkeypatch, tmp_path)
        resp = csrf_post("/settings/branding/sponsor/website",
                         {"sponsor_id": "abc123", "sponsor_url": "partingtonmarine.co.uk"},
                         follow_redirects=True)
        assert resp.status_code == 200
        assert self._saved(manifest)["website"] == "https://partingtonmarine.co.uk"

    def test_blanking_it_unlinks_the_logo(self, logged_in_client, csrf_post,
                                          monkeypatch, tmp_path):
        manifest = self._sponsor(monkeypatch, tmp_path, website="https://old.example")
        csrf_post("/settings/branding/sponsor/website",
                  {"sponsor_id": "abc123", "sponsor_url": ""}, follow_redirects=True)
        assert self._saved(manifest)["website"] == ""

    def test_an_address_that_is_not_the_web_is_refused_and_says_so(
            self, logged_in_client, csrf_post, monkeypatch, tmp_path):
        """Refused loudly. A sponsor whose logo silently does not link is the
        sort of thing nobody notices until they ask why."""
        manifest = self._sponsor(monkeypatch, tmp_path)
        resp = csrf_post("/settings/branding/sponsor/website",
                         {"sponsor_id": "abc123", "sponsor_url": "javascript:alert(1)"},
                         follow_redirects=True)
        assert "not saved" in resp.get_data(as_text=True)
        assert self._saved(manifest).get("website", "") == ""

    def test_an_unknown_sponsor_is_an_error_not_a_crash(self, logged_in_client, csrf_post,
                                                        monkeypatch, tmp_path):
        self._sponsor(monkeypatch, tmp_path)
        resp = csrf_post("/settings/branding/sponsor/website",
                         {"sponsor_id": "nope", "sponsor_url": "https://x.example"},
                         follow_redirects=True)
        assert resp.status_code == 200
        assert "not found" in resp.get_data(as_text=True).lower()

    def test_the_settings_page_offers_the_field(self, logged_in_client):
        body = logged_in_client.get("/settings", follow_redirects=True).get_data(as_text=True)
        assert 'name="sponsor_url"' in body, "no way to type a website in"


class TestTheWebsiteIsNotTheLogosOwnAddress:
    """Reported from the settings page: every Website box arrived pre-filled
    with ``/public/branding/sponsor_89dfe558bbf0.png``.

    The field was called ``url``, and on that page ``sponsor.url`` already
    meant *where the logo image is served from* --
    ``branding_assets_for_template()`` builds each row as
    ``{**sponsor, "url": url_for("public_branding_file", ...)}``, so it
    overwrote the website with the image path. Hence ``website``, which cannot
    be mistaken for either.
    """

    def test_the_template_asks_for_the_website_not_the_image(self):
        settings = (pathlib.Path(__file__).resolve().parent.parent
                    / "templates" / "settings.html").read_text(encoding="utf-8")
        # The row form, not the upload form: only the row pre-fills a value.
        row = [chunk for chunk in settings.split('name="sponsor_url"') if chunk.startswith(" value=")]
        assert row, "the sponsor row no longer pre-fills the website"
        assert "sponsor.website" in row[0][:80]
        assert "sponsor.url" not in row[0][:80], "that is the logo's own image path"

    def test_the_image_url_still_reaches_the_thumbnail(self, monkeypatch, tmp_path):
        """The other half of the collision: renaming must not break the picture."""
        settings = (pathlib.Path(__file__).resolve().parent.parent
                    / "templates" / "settings.html").read_text(encoding="utf-8")
        assert 'src="{{ sponsor.url }}?v=' in settings

    def test_the_two_survive_going_through_the_template_model(self, monkeypatch):
        """branding_assets_for_template() spreads the sponsor and sets url; the
        website has to come out the other side untouched."""
        import app as ro

        monkeypatch.setattr(ro, "branding_assets", lambda: {
            "enabled": True, "club_logo_enabled": False, "club_logo_path": None,
            "sponsors": [{"id": "abc", "label": "Partington", "filename": "sponsor_abc.png",
                          "website": "https://partingtonmarine.co.uk"}],
        })
        with ro.app.test_request_context("/"):
            model = ro.branding_assets_for_template()
        sponsor = model["sponsors"][0]
        assert sponsor["website"] == "https://partingtonmarine.co.uk"
        assert sponsor["url"].endswith("sponsor_abc.png"), "the thumbnail lost its source"


class TestALinkedLogoIsStillCappedInHeight:
    """Reported from a published page: one sponsor's logo hung out of the banner.

    Nothing was wrong with the file. A near-square logo -- Firmhelm is 729x651
    -- wants to be 143px tall at the 160px width cap, and the 70px height cap
    is what normally stops it. Wrapping it in a link removed that cap, because
    the override said ``max-height: 100%`` and a percentage max-height resolves
    against the containing block: an inline-flex anchor has no definite height,
    so the limit was none at all. Wide logos were unaffected, which is why only
    one of them changed and it looked like that file had grown.

    The width stays relative, so a linked logo still shrinks with the strip;
    only the height goes back to the absolute cap. Measured afterwards with
    every logo linked, at 1440, 1100, 820 and 390: tallest 70, 70, 70, 32.
    """

    def test_the_height_cap_is_absolute_not_a_percentage(self):
        rule = TPL.split(".publish-sponsor-link .publish-sponsor-logo {")[1].split("}")[0]
        assert "max-height" not in rule, (
            "a percentage max-height here resolves to no limit, which is the bug")

    def test_the_image_keeps_its_own_height_cap(self):
        logo = TPL.split(".publish-sponsor-logo { max-height:")[1].split(";")[0]
        assert "70px" in logo

    def test_the_width_is_still_relative_so_it_can_shrink(self):
        """Absolute on both would stop a linked logo shrinking with the strip
        and bring the banner overflow back."""
        rule = TPL.split(".publish-sponsor-link .publish-sponsor-logo {")[1].split("}")[0]
        assert "max-width: 100%" in rule

    def test_the_anchor_does_not_cap_the_height_either(self):
        """It capped the anchor and the image separately at one point; the
        anchor's cap did nothing useful and hid where the real limit was."""
        link = TPL.split(".publish-sponsor-link { display: inline-flex")[1].split("}")[0]
        assert "max-height" not in link


class TestTheBannerSaysTheLogosCanBeClicked:
    """A logo that links to somewhere looks exactly like one that does not.

    So the banner says so, in the bottom right corner of the grey, under the
    strip: small white text, which is 5.3:1 against #6b6b6b and carries at
    this size. The line is drawn only when a sponsor actually has an address
    saved -- most clubs will set none for a while -- because inviting a click
    that does nothing is worse than saying nothing at all.
    """

    def _render(self, sponsors):
        """The banner as a browser gets it. The guard is a Jinja expression,
        and reading it out of the source only says it was typed."""
        from jinja2 import Environment, FileSystemLoader

        root = pathlib.Path(__file__).resolve().parent.parent
        env = Environment(loader=FileSystemLoader(str(root / "templates")))
        logos = [{"kind": "sponsor", "name": label, "data_uri": "data:,", "website": site}
                 for label, site in sponsors]
        return env.get_template("series_publish.html").render(
            series={"name": "Autumn Series 2026", "description": ""},
            groups=[], races=[], branding_logos=logos, club_logo_data_uri="",
            ordinal_text=lambda n: str(n), published_score_cell=lambda s: s)

    def test_the_line_is_there_when_a_sponsor_has_a_website(self):
        html = self._render([("S&S", ""), ("Firmhelm", "https://firmhelm.com/")])
        assert "Click logos for sponsors' websites" in html

    def test_and_not_when_none_of_them_do(self):
        html = self._render([("S&S", ""), ("Plas Heli", "")])
        assert "Click logos for sponsors' websites" not in html
        assert "publish-sponsor-hint" not in html.split("<style>")[1].split("</style>")[1]

    def test_nor_when_there_are_no_sponsors_at_all(self):
        html = self._render([])
        assert "Click logos for sponsors' websites" not in html

    def test_it_sits_in_the_bottom_right_of_the_grey(self):
        """Measured at 1800/1440/1100/820: the text's right edge lands on the
        strip's own right edge, one line, 17px above the banner's bottom."""
        rule = TPL.split(".publish-sponsor-hint {")[1].split("}")[0]
        assert "text-align: right" in rule
        assert "color: #fff" in rule

    def test_it_spans_the_banner_rather_than_sharing_the_logo_column(self):
        """The banner is a two-column grid -- club logo, sponsor strip -- and
        a third child without this drops into the club logo's column."""
        rule = TPL.split(".publish-sponsor-hint {")[1].split("}")[0]
        assert "grid-column: 1 / -1" in rule

    def test_it_is_small_enough_to_stay_out_of_the_way(self):
        rule = TPL.split(".publish-sponsor-hint {")[1].split("}")[0]
        size = float(rule.split("font-size:")[1].split("em")[0])
        assert 0.6 <= size <= 0.8, rule

    def test_it_is_centred_on_a_phone_like_the_rest_of_the_banner(self):
        phone = TPL.split("@media (max-width: 700px) {")[1]
        assert ".publish-sponsor-hint { text-align: center; }" in phone

    def test_it_is_not_printed(self):
        """There is nothing to click on a sheet of paper."""
        printed = TPL.split("@media print {")[1]
        block = printed.split("display: none;")[0]
        assert "publish-sponsor-hint" in block

    def test_the_row_gap_is_not_the_column_gap(self):
        """The grid's 20px is the space between the club logo and the strip.
        Reusing it below the strip pushed the banner 20px taller for a line
        of 12px text."""
        rule = TPL.split(".publish-branding { display: grid;")[1].split("}")[0]
        assert "gap: 6px 20px" in rule
