"""The in-app Documentation page and the PDFs it offers.

The guides are built by scripts/build_*.py and committed into docs/. Nothing
checked that a guide listed on the page actually existed, so a renamed or
un-built PDF would only show up as a 404 when somebody clicked it — which is
exactly when they need it.
"""
from __future__ import annotations

from pathlib import Path

import app as ro


DOCS_DIR = Path(ro.__file__).resolve().parent / "docs"


class TestTheGuidesExist:
    def test_every_listed_guide_is_present_and_is_a_pdf(self):
        assert ro.DOCUMENTATION_FILES, "no guides are listed at all"
        for slug, (filename, title, description) in ro.DOCUMENTATION_FILES.items():
            path = DOCS_DIR / filename
            assert path.exists(), f"{slug} lists {filename}, which is not in docs/"
            assert path.stat().st_size > 50_000, f"{filename} is suspiciously small — did the build fail?"
            assert path.read_bytes()[:5] == b"%PDF-", f"{filename} is not a PDF"
            assert title and description, f"{slug} is missing a title or description"

    def test_the_relay_guide_is_offered(self):
        """It documents the one machine the whole club is reached through."""
        assert "relay-guide" in ro.DOCUMENTATION_FILES
        filename, title, _ = ro.DOCUMENTATION_FILES["relay-guide"]
        assert filename == "Pwllheli_Relay_Guide.pdf"
        assert "Relay" in title


class TestThePage:
    def test_lists_every_guide(self, logged_in_client):
        from markupsafe import escape          # match Jinja's escaping (&#39; for ')

        html = logged_in_client.get("/admin/documentation").get_data(as_text=True)
        for slug, (_f, title, _d) in ro.DOCUMENTATION_FILES.items():
            assert str(escape(title)) in html, f"{slug} is not shown on the page"

    def test_serves_each_guide(self, logged_in_client):
        for slug in ro.DOCUMENTATION_FILES:
            resp = logged_in_client.get(f"/admin/documentation/{slug}")
            assert resp.status_code == 200, f"{slug} did not download"
            assert resp.headers["Content-Type"].startswith("application/pdf")

    def test_an_unknown_slug_is_not_found(self, logged_in_client):
        assert logged_in_client.get("/admin/documentation/no-such-guide").status_code == 404

    def test_only_the_competitor_guide_is_public(self, client):
        """The rest describe the race office and the relay; they stay behind login."""
        assert ro.PUBLIC_DOCUMENTATION_SLUGS == {"competitor-guide"}
        for slug in ro.DOCUMENTATION_FILES:
            resp = client.get(f"/documentation/{slug}")
            if slug in ro.PUBLIC_DOCUMENTATION_SLUGS:
                assert resp.status_code == 200, f"{slug} should be public"
            else:
                assert resp.status_code in (302, 401, 403), f"{slug} was served without a login"
