"""Reading the bundled docs/*.md documentation inside the app.

Twenty-odd markdown files describe how everything works, and until now the only
way to read one was to find the file on the PC — no use when TROUBLESHOOTING is
the one you want *while* something is going wrong.

The two things worth guarding hardest:

* **A slug must never become a path.** The slug is looked up in a globbed list and
  the stored filename used. Joining a request-supplied slug onto a directory is how
  you end up serving ``runtime/secret_key.txt``.
* **These stay behind login.** They describe the race office. Only the competitor
  guide is public, and that is a PDF.
"""
from __future__ import annotations

import pathlib

import pytest

import app as ro
from core import docsview


class TestTheListFindsItself:
    def test_every_markdown_file_in_docs_is_listed(self):
        """Discovered by globbing, so a new document needs no registration."""
        on_disk = {f"docs/{path.name}" for path in (ro.BASE_DIR / "docs").glob("*.md")}
        listed = {doc["relpath"] for doc in docsview.markdown_documents()}
        assert on_disk <= listed
        assert len(on_disk) > 15, "expected the full documentation set"

    def test_titles_come_from_each_document_own_heading(self):
        """So the page cannot drift from what the document calls itself."""
        by_file = {doc["relpath"].split("/")[-1]: doc["title"] for doc in docsview.markdown_documents()}
        assert by_file["TROUBLESHOOTING.md"] == "Troubleshooting"
        assert by_file["DATA_AND_BACKUP.md"] == "Data, storage and backup"

    def test_a_file_with_no_heading_still_gets_a_title(self, tmp_path, monkeypatch):
        monkeypatch.setattr(docsview.appstate, "BASE_DIR", tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "ODD_ONE.md").write_text("no heading here\n", encoding="utf-8")
        assert docsview.markdown_documents()[0]["title"] == "Odd one"

    def test_slugs_are_url_shaped(self):
        assert docsview.slug_for_filename("DATA_AND_BACKUP.md") == "data-and-backup"

    def test_the_list_is_sorted_by_title(self):
        titles = [doc["title"] for doc in docsview.markdown_documents()]
        assert titles == sorted(titles, key=str.lower)


class TestASlugNeverBecomesAPath:
    @pytest.mark.parametrize("slug", [
        "../secret_key",
        "../../runtime/secret_key.txt",
        "..%2f..%2fapp",
        "....//app",
        "/etc/passwd",
        "app",                      # a real file, but not in docs/
        "README",                   # a real file, and markdown, but not in docs/
        "",
    ])
    def test_nothing_outside_the_globbed_list_resolves(self, slug):
        assert docsview.document_for_slug(slug) is None

    def test_a_traversing_slug_is_a_404_not_a_file(self, logged_in_client):
        resp = logged_in_client.get("/admin/documentation/reference/..%2f..%2fapp")
        assert resp.status_code == 404

    def test_a_slug_is_matched_case_insensitively(self):
        """Not a hole: it still has to be in the globbed list to resolve at all."""
        assert docsview.document_for_slug("TROUBLESHOOTING")["relpath"] == "docs/TROUBLESHOOTING.md"
        assert docsview.document_for_slug(" troubleshooting ")["relpath"] == "docs/TROUBLESHOOTING.md"

    def test_the_path_used_is_the_stored_one(self):
        """Not anything derived from the request."""
        document = docsview.document_for_slug("troubleshooting")
        assert document["relpath"] == "docs/TROUBLESHOOTING.md"
        assert ".." not in document["relpath"]

    def test_no_listed_document_escapes_the_app_folder(self):
        """Everything, extras included, resolves inside BASE_DIR."""
        root = (ro.BASE_DIR).resolve()
        for doc in docsview.markdown_documents():
            assert ".." not in doc["relpath"]
            assert root in (ro.BASE_DIR / doc["relpath"]).resolve().parents


class TestItStaysBehindLogin:
    def test_an_anonymous_visitor_cannot_read_one(self, client):
        resp = client.get("/documentation/reference/troubleshooting")
        assert resp.status_code in (302, 401, 403), "the race-office docs are not public"

    def test_a_signed_in_user_can(self, logged_in_client):
        assert logged_in_client.get("/admin/documentation/reference/troubleshooting").status_code == 200

    def test_it_is_not_on_the_public_allow_list(self):
        assert ro.PUBLIC_DOCUMENTATION_SLUGS == {"competitor-guide"}


class TestTheRenderedPage:
    def test_the_document_is_rendered_as_html_not_shown_raw(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/troubleshooting").get_data(as_text=True)
        assert "<h2" in html, "markdown headings should become headings"
        assert "## Cannot log in" not in html, "that is unrendered markdown"

    def test_fenced_code_becomes_a_code_block(self, logged_in_client):
        """The documentation is full of commands; they have to be readable."""
        html = logged_in_client.get("/admin/documentation/reference/installation").get_data(as_text=True)
        assert "<pre>" in html or "<pre " in html

    def test_the_title_is_the_documents_own(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/data-and-backup").get_data(as_text=True)
        assert "Data, storage and backup" in html

    def test_it_says_which_file_you_are_reading(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/tracking").get_data(as_text=True)
        assert "docs/TRACKING.md" in html

    def test_the_other_documents_are_listed_alongside(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/tracking").get_data(as_text=True)
        assert "Troubleshooting" in html and "Series scoring" in html

    def test_an_unknown_document_is_a_404(self, logged_in_client):
        assert logged_in_client.get("/admin/documentation/reference/no-such-doc").status_code == 404


class TestLinksBetweenDocumentsWork:
    """The documentation cross-references itself constantly. A rendered page whose
    every internal link 404s is worse than no rendering."""

    def test_a_link_to_another_document_points_at_the_viewer(self):
        rendered = '<p>see <a href="DATA_AND_BACKUP.md">backups</a></p>'
        out = docsview.rewrite_internal_links(rendered, md_url="/admin/documentation/reference/{slug}")
        assert 'href="/admin/documentation/reference/data-and-backup"' in out

    def test_a_docs_prefixed_link_works_too(self):
        rendered = '<a href="docs/TRACKING.md">tracking</a>'
        out = docsview.rewrite_internal_links(rendered, md_url="/r/{slug}")
        assert 'href="/r/tracking"' in out

    def test_an_anchor_is_kept(self):
        rendered = '<a href="DATA_AND_BACKUP.md#off-site-backup">off-site</a>'
        out = docsview.rewrite_internal_links(rendered, md_url="/r/{slug}")
        assert 'href="/r/data-and-backup#off-site-backup"' in out

    def test_a_link_to_something_we_do_not_offer_is_left_alone(self):
        """scripts/ is excluded from the release ZIP, so it is deliberately not
        one of ours — the link stays as written rather than becoming a 404."""
        rendered = '<a href="../scripts/README.md">build scripts</a>'
        out = docsview.rewrite_internal_links(rendered, md_url="/r/{slug}")
        assert 'href="../scripts/README.md"' in out

    def test_an_external_markdown_url_is_left_alone(self):
        rendered = '<a href="https://example.org/thing.md">external</a>'
        out = docsview.rewrite_internal_links(rendered, md_url="/r/{slug}")
        assert 'href="https://example.org/thing.md"' in out

    def test_a_pdf_link_points_at_the_pdf_route(self):
        rendered = '<a href="Pwllheli_Relay_Guide.pdf">relay guide</a>'
        out = docsview.rewrite_internal_links(
            rendered, md_url="/r/{slug}",
            pdf_urls={"Pwllheli_Relay_Guide.pdf": "/admin/documentation/relay-guide"})
        assert 'href="/admin/documentation/relay-guide"' in out
        assert 'target="_blank"' in out

    def test_an_unknown_pdf_is_left_alone(self):
        rendered = '<a href="Some_Other.pdf">x</a>'
        out = docsview.rewrite_internal_links(rendered, md_url="/r/{slug}", pdf_urls={})
        assert 'href="Some_Other.pdf"' in out

    def test_real_cross_links_resolve_on_a_real_page(self, logged_in_client):
        """DATA_AND_BACKUP.md links to the security notes and the relay guide."""
        html = logged_in_client.get("/admin/documentation/reference/website-publishing").get_data(as_text=True)
        assert ".md\"" not in html, "an unrewritten .md link would 404"


class TestItSurvivesWithoutTheMarkdownPackage:
    def test_the_text_is_still_readable(self, monkeypatch):
        """Being able to read the words matters more than the formatting, so a
        missing package must not take the page down."""
        monkeypatch.setattr(docsview, "_markdown", None)
        out = docsview.render_markdown("# Title\n\nSome **words**.\n")
        assert "Some **words**." in out, "the raw text should still be there"
        assert "markdown" in out.lower(), "and it should say why it is unformatted"

    def test_the_raw_fallback_escapes_the_text(self, monkeypatch):
        """It goes into the page unrendered, so it has to be escaped."""
        monkeypatch.setattr(docsview, "_markdown", None)
        out = docsview.render_markdown("<script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_the_page_says_so(self, logged_in_client, monkeypatch):
        monkeypatch.setattr(docsview, "_markdown", None)
        html = logged_in_client.get("/admin/documentation/reference/troubleshooting").get_data(as_text=True)
        assert "not installed" in html


class TestTheDocumentationPage:
    def test_it_lists_the_reference_documents(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation").get_data(as_text=True)
        assert "Reference documentation" in html
        assert "documentation/reference/troubleshooting" in html

    def test_the_common_links_are_still_there(self, logged_in_client):
        """The section was split in two, not replaced."""
        html = logged_in_client.get("/admin/documentation").get_data(as_text=True)
        assert "Common links" in html
        assert "Clubhouse display" in html

    def test_the_pdf_guides_are_still_offered(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation").get_data(as_text=True)
        assert "Open PDF" in html


class TestTheDocumentsOutsideDocs:
    """A few markdown files worth reading do not live in docs/. They are named
    explicitly rather than found by widening the glob, so "every file comes from a
    known list" stays true and no slug can reach a parent directory.
    """

    def _by_slug(self):
        return {doc["slug"]: doc for doc in docsview.markdown_documents()}

    def test_the_readme_is_offered(self):
        """The only document that explains the whole system — what runs where, the
        quick start and the safety notes."""
        doc = self._by_slug()["overview"]
        assert doc["relpath"] == "README.md"

    def test_the_readme_title_is_fixed_not_taken_from_its_heading(self):
        """Its own heading carries the version number, which would rename and
        re-sort the entry at every release."""
        assert self._by_slug()["overview"]["title"] == "Overview and quick start"
        assert "0." not in self._by_slug()["overview"]["title"]

    def test_the_relay_setup_is_offered(self):
        """REMOTE_ACCESS_CLOUDFLARE.md links to it twice, and those links used to
        dead-end in the viewer."""
        assert self._by_slug()["relay-setup"]["relpath"] == "deploy/live_stream/README.md"

    def test_the_build_scripts_readme_is_not_offered(self):
        """scripts/ is excluded from the release ZIP, so listing it would give every
        hut PC a link that 404s. Good material, wrong place."""
        assert "scripts/README.md" not in {d["relpath"] for d in docsview.markdown_documents()}

    def test_an_extra_that_is_not_installed_is_skipped(self, tmp_path, monkeypatch):
        """Rather than offering a link to a file that is not there."""
        monkeypatch.setattr(docsview.appstate, "BASE_DIR", tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "A.md").write_text("# A\n", encoding="utf-8")
        assert [d["relpath"] for d in docsview.markdown_documents()] == ["docs/A.md"]

    def test_they_render_like_any_other(self, logged_in_client):
        for slug in ("overview", "relay-setup", "windows-deployment-scripts"):
            resp = logged_in_client.get(f"/admin/documentation/reference/{slug}")
            assert resp.status_code == 200, slug
            assert "<h2" in resp.get_data(as_text=True), slug

    def test_the_page_says_where_the_file_lives(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/relay-setup").get_data(as_text=True)
        assert "deploy/live_stream/README.md" in html

    def test_a_path_style_link_to_one_resolves(self):
        """However many ../ it is written with."""
        for href in ("../deploy/live_stream/README.md", "deploy/live_stream/README.md"):
            out = docsview.rewrite_internal_links(f'<a href="{href}">relay</a>', md_url="/r/{slug}")
            assert 'href="/r/relay-setup"' in out, href

    def test_the_cloudflare_document_no_longer_has_a_dead_relay_link(self, logged_in_client):
        html = logged_in_client.get("/admin/documentation/reference/remote-access-cloudflare").get_data(as_text=True)
        body = html.split("doc-markdown", 1)[1].split("</article>", 1)[0]
        assert "reference/relay-setup" in body, "the relay link should reach the viewer"
        assert ".md\"" not in body, "no unrewritten markdown link should remain"
