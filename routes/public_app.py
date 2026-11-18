# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""The public competitor pages as a home-screen app.

A web app manifest and the icons it names, so a phone can add the competitor
pages to its home screen and open them full-screen like an app. Nothing here
changes what the pages show; it only tells the phone what to call them, what
picture to use and where to start.

And phone notifications: the service worker that shows them, and the two
requests by which a phone asks for them and asks for them to stop. What is
sent, and when, is ``core/notify.py``; these routes only keep the list.

Registered like every other route module (see routes/__init__.py). Each endpoint
is also named in ``public_endpoint()`` in app.py, which is an allow-list: a
route is private until it is added there.
"""
from routes import app_module

_app = app_module()
app = _app.app
Response = _app.Response
jsonify = _app.jsonify
request = _app.request
send_file = _app.send_file
sqlite3 = _app.sqlite3
url_for = _app.url_for
club_config = _app.club_config
branding_assets = _app.branding_assets
BASE_DIR = _app.BASE_DIR

from core import appicon, notify

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


# --------------------------------------------------------------------------- notifications

# A subscription is an address and two short keys; with a dozen sail numbers it
# is under a kilobyte. Anything much larger is not a phone subscribing.
MAX_PUSH_BODY_BYTES = 4096


@app.route("/public/sw.js")
def public_service_worker():
    """The service worker that shows a notification when one arrives.

    Served from under /public/ rather than /static/, because a service worker
    may only look after pages at or below the folder it was fetched from.
    """
    response = send_file(BASE_DIR / "static" / "public_sw.js", mimetype="text/javascript")
    # Always revalidated: a browser otherwise keeps an old worker for a day.
    response.headers["Cache-Control"] = "no-cache"
    return response


def _push_request():
    """The JSON body of a subscribe/unsubscribe request, or the refusal to send back."""
    if not notify.public_config()["enabled"]:
        return None, (jsonify({"ok": False, "error": "Notifications are switched off."}), 404)
    length = request.content_length
    if length is None or length > MAX_PUSH_BODY_BYTES:
        return None, (jsonify({"ok": False, "error": "Request too large."}), 413)
    # The relay passes the caller's own address on; behind it every request
    # would otherwise seem to come from the relay.
    address = request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"
    if not notify.allow_request(address):
        return None, (jsonify({"ok": False, "error": "Too many requests. Try again later."}), 429)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"ok": False, "error": "Not a subscription."}), 400)
    return data, None


@app.route("/public/push/subscribe", methods=["POST"])
def public_push_subscribe():
    """A phone asking for notifications, or changing which it wants."""
    data, refusal = _push_request()
    if refusal:
        return refusal
    subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    try:
        notify.subscribe(subscription.get("endpoint"), keys.get("p256dh"), keys.get("auth"),
                         notices=bool(data.get("notices", True)), boats=bool(data.get("boats", True)),
                         boat_keys=data.get("boat_keys"))
    except notify.SubscriptionRefused as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except sqlite3.OperationalError:
        # Busy for a moment. The phone tries again; nothing is half-kept.
        return jsonify({"ok": False, "error": "Busy. Try again in a moment."}), 503
    return jsonify({"ok": True})


@app.route("/public/push/unsubscribe", methods=["POST"])
def public_push_unsubscribe():
    """A phone asking for notifications to stop."""
    data, refusal = _push_request()
    if refusal:
        return refusal
    try:
        notify.unsubscribe(data.get("endpoint"))
    except sqlite3.OperationalError:
        return jsonify({"ok": False, "error": "Busy. Try again in a moment."}), 503
    # The same answer whether or not it was known: the phone has what it wanted.
    return jsonify({"ok": True})
