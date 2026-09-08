"""Media, branding and video-serving routes.

Split out of app.py as the first slice of the routes/ refactor. These views are
registered on the shared Flask ``app`` by importing this module near the end of
app.py, after every shared helper exists. Endpoint names are unchanged, so all
``url_for(...)`` calls in templates and the public-endpoint allow-list keep
working with no other edits.

The app object and helpers are read off the *running* app module (see
``routes.app_module``) rather than ``from app import ...`` so this works whether
app.py was started with ``python app.py`` (module ``__main__``) or imported.
"""
from pathlib import Path

from flask import Response, jsonify, send_file, url_for

from routes import app_module

_app = app_module()
app = _app.app
BASE_DIR = _app.BASE_DIR
VIDEO_LIVE_JPG_PATH = _app.VIDEO_LIVE_JPG_PATH
branding_assets = _app.branding_assets
branding_file_path = _app.branding_file_path
list_usb_video_sources = _app.list_usb_video_sources
public_live_frame_path = _app.public_live_frame_path
public_live_image_context = _app.public_live_image_context
public_live_r2_status = _app.public_live_r2_status
send_video_clip_response = _app.send_video_clip_response
start_video_background_recorder = _app.start_video_background_recorder
video = _app.video
video_runtime_status = _app.video_runtime_status


@app.route("/public/branding/<path:filename>")
def public_branding_file(filename: str):
    """Serve uploaded public branding logos used by competitor pages."""
    path = branding_file_path(filename)
    if not path:
        return Response("Branding image not found", status=404)
    mimetype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    response = send_file(path, mimetype=mimetype)
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.route("/api/branding/live")
def api_branding_live():
    """Public branding manifest for the external live-stream relay.

    Returns the current club and sponsor logos as absolute image URLs plus the
    sponsor rotation interval, straight from Settings -> Public branding, so the
    relay can burn the current logos into the live stream with nothing to sync by
    hand. Public and read-only. When branding is disabled the relay gets
    enabled=false and no logos, and should overlay nothing.
    """
    assets = branding_assets()
    branding_enabled = bool(assets.get("enabled"))
    club_url = ""
    if branding_enabled and assets.get("club_logo_enabled") and assets.get("club_logo_path"):
        club_path = Path(assets["club_logo_path"])
        if club_path.parent == (BASE_DIR / "static" / "img"):
            club_url = url_for("static", filename=f"img/{club_path.name}", _external=True)
        else:
            club_url = url_for("public_branding_file", filename=club_path.name, _external=True)
    sponsors = []
    if branding_enabled:
        for sponsor in assets.get("sponsors") or []:
            sponsors.append({
                "id": sponsor.get("id", ""),
                "label": sponsor.get("label", ""),
                "url": url_for("public_branding_file", filename=sponsor["filename"], _external=True),
            })
    response = jsonify({
        "enabled": branding_enabled,
        "club_logo_url": club_url,
        "sponsors": sponsors,
        "rotation_seconds": video.PUBLIC_BRANDING_ROTATION_SECONDS,
    })
    response.headers["Cache-Control"] = "public, max-age=60"
    return response


@app.route("/video/live_frame.jpg")
@app.route("/admin/video/live_frame.jpg")
def video_live_frame():
    """Return the latest near-live camera preview frame for the race officer UI."""
    start_video_background_recorder()
    try:
        if not VIDEO_LIVE_JPG_PATH.exists() or VIDEO_LIVE_JPG_PATH.stat().st_size <= 0:
            return Response("Live video frame is not ready", status=404)
        response = send_file(VIDEO_LIVE_JPG_PATH, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    except Exception as exc:
        return Response(f"Live video frame is not available: {exc}", status=404)

@app.route("/public/video/live_frame.jpg")
def public_video_live_frame():
    """Return the latest branded public live-camera preview frame."""
    start_video_background_recorder()
    try:
        frame_path = public_live_frame_path()
        if not frame_path.exists() or frame_path.stat().st_size <= 0:
            return Response("Live video frame is not ready", status=404)
        response = send_file(frame_path, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    except Exception as exc:
        return Response(f"Live video frame is not available: {exc}", status=404)


@app.route("/public/video/status")
def public_video_status():
    """Return a sanitised public live-camera status."""
    status = video_runtime_status()
    return jsonify({
        "ok": bool(status.get("ok")),
        "enabled": bool(status.get("enabled")),
        "running": bool(status.get("running")),
        "live_frame": bool(status.get("live_frame")),
        "live_frame_age_seconds": status.get("live_frame_age_seconds"),
        "public_live_image": public_live_image_context(),
        "public_live_r2": public_live_r2_status(),
        "message": status.get("message", ""),
    })


@app.route("/video/clip/<int:clip_id>")
@app.route("/admin/video/clip/<int:clip_id>")
def video_clip_file(clip_id: int):
    """Serve an authenticated race-officer video clip."""
    return send_video_clip_response(clip_id, public=False)


@app.route("/public/video/clip/<int:clip_id>")
def public_video_clip_file(clip_id: int):
    """Serve a public video clip after the race has finished."""
    return send_video_clip_response(clip_id, public=True)


@app.route("/api/video/usb_sources")
@app.route("/admin/api/video/usb_sources")
def api_video_usb_sources():
    """Return detected USB camera sources for the settings dropdown."""
    return jsonify({"ok": True, "sources": list_usb_video_sources()})


@app.route("/api/video/status")
@app.route("/admin/api/video/status")
def api_video_status():
    """Return recorder health and live-preview status."""
    return video_runtime_status()
