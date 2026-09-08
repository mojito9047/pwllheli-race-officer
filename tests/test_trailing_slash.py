"""A trailing slash on an admin URL (e.g. /admin/) must not dead-end on a 404.

Flask routes are registered without a trailing slash and Werkzeug does not
auto-redirect the added-slash form, so a logged-in user who pastes /admin/ used
to get a bare "Not Found". A 404 handler now redirects to the slash-less form.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_admin_trailing_slash_redirects_to_slashless(logged_in_client):
    resp = logged_in_client.get("/admin/", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers.get("Location", "").endswith("/admin")


def test_admin_trailing_slash_reaches_dashboard(logged_in_client):
    resp = logged_in_client.get("/admin/", follow_redirects=True)
    assert resp.status_code == 200


def test_genuinely_missing_path_still_404s(logged_in_client):
    # The retry is one hop only — a truly missing page still 404s, no loop.
    resp = logged_in_client.get("/definitely-not-a-real-page/", follow_redirects=True)
    assert resp.status_code == 404


def test_query_string_preserved_on_redirect(logged_in_client):
    resp = logged_in_client.get("/admin/?foo=bar", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers.get("Location", "").endswith("/admin?foo=bar")
