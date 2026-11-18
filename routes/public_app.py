# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""The public competitor pages as a home-screen app.

A web app manifest and the icons it names, so a phone can add the competitor
pages to its home screen and open them full-screen like an app. Nothing here
changes what the pages show; it only tells the phone what to call them, what
picture to use and where to start.

Registered like every other route module (see routes/__init__.py). Each endpoint
is also named in ``public_endpoint()`` in app.py, which is an allow-list: a
route is private until it is added there.
"""
from routes import app_module

_app = app_module()
app = _app.app
Response = _app.Response
jsonify = _app.jsonify
url_for = _app.url_for
club_config = _app.club_config
branding_assets = _app.branding_assets

from core import appicon

# The paper of the public pages (--paper in theme_race_document.css): the splash
# screen and the phone's status bar match the page that then opens.
APP_BACKGROUND = "#f7f4ec"


@app.route("/public/app.webmanifest")
def public_app_manifest():
    """What a phone needs to know to put the competitor pages on its home screen."""
    club = club_config()
    icon_192 = url_for("public_app_icon", size=192)
    icon_512 = url_for("public_app_icon", size=512)
    response = jsonify({
        "name": club["name"],
        "short_name": club["short_name"],
        "description": f"Racing at {club['name']}: start times, live tracking, wind and results.",
        # Fixed, so moving the start page later does not make phones treat it
        # as a different app.
        "id": "/public/",
        # The club's landing page: the race list, the wind and the camera.
        "start_url": url_for("competitor_current_race"),
        # Only the competitor pages. The race office under /admin is a different
        # thing behind a login, and must not open inside the installed app.
        "scope": "/public/",
        "display": "standalone",
        "background_color": APP_BACKGROUND,
        "theme_color": APP_BACKGROUND,
        "icons": [
            {"src": icon_192, "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon_512, "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon_512, "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.route("/public/app-icon/<int:size>.png")
def public_app_icon(size: int):
    """The club logo as a square home-screen icon, at one of the sizes phones ask for."""
    if size not in appicon.ICON_SIZES:
        return Response("Icon size not available", status=404)
    try:
        logo_path = branding_assets().get("club_logo_path")
    except Exception:
        logo_path = None      # a bad branding manifest must not cost the icon
    data = appicon.render_app_icon(logo_path, size)
    if not data:
        return Response("Icon not available", status=404)
    response = Response(data, mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
