"""The configured public address, and the links the app builds from it.

Reached through the relay, the app sees the Cloudflare tunnel's own Host header
and a plain http scheme, so everything it built with ``url_for(_external=True)``
came out as ``http://hut-origin.pwllhelisailingclub.org/...``. That is an
internal hostname on the wrong scheme, and it went out in competitor share
links and in the branding manifest the live-stream relay reads. Setting the
public address in Settings -> Web server fixes it for every such link at once.
"""
from __future__ import annotations

import pytest

from core.horn import forget_public_base_url, normalise_public_base_url


@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("   ", ""),
    (None, ""),
    ("https://pro.pwllhelisailingclub.org", "https://pro.pwllhelisailingclub.org"),
    # A trailing slash, a path and a query are all dropped: the app is not
    # served under a sub-path and keeping one would build links that 404.
    ("https://pro.pwllhelisailingclub.org/", "https://pro.pwllhelisailingclub.org"),
    ("https://PRO.Example.org/some/path?x=1", "https://pro.example.org"),
    # A bare hostname is what someone actually types; assume https.
    ("pro.pwllhelisailingclub.org", "https://pro.pwllhelisailingclub.org"),
    ("localhost:5050", "https://localhost:5050"),
    ("http://192.168.1.10:5050", "http://192.168.1.10:5050"),
    # Rejected rather than stored: a bad value here would corrupt every
    # external link the app builds.
    ("ftp://nope.example", ""),
    ("javascript:alert(1)", ""),
    ("http://user:pass@evil.example", ""),
    ("https://", ""),
])
def test_normalise_public_base_url(raw, expected):
    assert normalise_public_base_url(raw) == expected


def _branding_host(client):
    """The host the branding manifest advertises its logo URLs on."""
    body = client.get("/api/branding/live").get_json()
    return body.get("club_logo_url", "")


def test_external_links_follow_the_configured_public_address(client, monkeypatch):
    """With the setting saved, external URLs are built against it."""
    import core.horn as horn

    monkeypatch.setattr(
        horn, "get_hardware_setting_overrides",
        lambda: {"server_public_base_url": "https://pro.pwllhelisailingclub.org"})
    forget_public_base_url()
    try:
        url = _branding_host(client)
        assert url.startswith("https://pro.pwllhelisailingclub.org/"), url
    finally:
        forget_public_base_url()


def test_without_the_setting_the_request_host_is_used(client, monkeypatch):
    """Empty means "use whatever Host the request arrived with", for the hut LAN."""
    import core.horn as horn

    monkeypatch.setattr(horn, "get_hardware_setting_overrides", lambda: {})
    forget_public_base_url()
    try:
        url = _branding_host(client)
        assert "pwllhelisailingclub.org" not in url, url
    finally:
        forget_public_base_url()
