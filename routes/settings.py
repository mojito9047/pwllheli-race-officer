"""Settings, hardware config and user-management routes.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from core.assistant_llm import interpreter_status
from routes import app_module

_app = app_module()
app = _app.app
DEFAULT_COURSE_CHART_OVERLAY_URL = _app.DEFAULT_COURSE_CHART_OVERLAY_URL
DEFAULT_COURSE_CHART_TILE_URL = _app.DEFAULT_COURSE_CHART_TILE_URL
DEFAULT_WEATHER_STATION_URL = _app.DEFAULT_WEATHER_STATION_URL
HARDWARE_RUNTIME_STATE = _app.HARDWARE_RUNTIME_STATE
IRC_LISTING_URL = _app.IRC_LISTING_URL
START_SEQUENCE_STATE = _app.START_SEQUENCE_STATE
YTC_LISTING_URL = _app.YTC_LISTING_URL
audit = _app.audit
activitylog = _app.activitylog
branding_assets_for_template = _app.branding_assets_for_template
central_audio_status = _app.central_audio_status
course_chart_config = _app.course_chart_config
datetime = _app.datetime
flash = _app.flash
generate_password_hash = _app.generate_password_hash
get_db = _app.get_db
get_events = _app.get_events
get_user = _app.get_user
hardware_config = _app.hardware_config
list_polar_assets = _app.list_polar_assets
list_polar_files = _app.list_polar_files
list_usb_video_sources = _app.list_usb_video_sources
list_users = _app.list_users
listing_config = _app.listing_config
list_offsite_backups = _app.list_offsite_backups
normalize_role = _app.normalize_role
offsite_config = _app.offsite_config
offsite_dashboard_status = _app.offsite_dashboard_status
power_runtime_status = _app.power_runtime_status
track_runtime_status = _app.track_runtime_status
ptz_runtime_status = _app.ptz_runtime_status
public_live_r2_status = _app.public_live_r2_status
public_live_stream_url = _app.public_live_stream_url
race_console_config = _app.race_console_config
read_manual_horn_input = _app.read_manual_horn_input
read_r2_test_result = _app.read_r2_test_result
read_settings_form = _app.read_settings_form
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
reset_ptz_runtime_state = _app.reset_ptz_runtime_state
row_get = _app.row_get
save_app_settings = _app.save_app_settings
save_hardware_config = _app.save_hardware_config
session = _app.session
sqlite3 = _app.sqlite3
start_public_live_r2_uploader = _app.start_public_live_r2_uploader
start_video_background_recorder = _app.start_video_background_recorder
url_for = _app.url_for
user_is_admin = _app.user_is_admin
video_config = _app.video_config
video_runtime_status = _app.video_runtime_status
weather_config = _app.weather_config
weather_runtime_status = _app.weather_runtime_status


@app.route("/settings")
@app.route("/admin/settings")
def settings_page():
    """Render the grouped Settings page."""
    weather_status = weather_runtime_status()
    # What is actually in the backup bucket, not what the app believes it put
    # there. Best-effort: a bad key or a 4G hiccup must not stop Settings loading,
    # and the status box below the list carries the same error anyway.
    try:
        offsite_backups = list_offsite_backups()
    except Exception:
        offsite_backups = []
    return render_template(
        "settings.html",
        assistant_status=interpreter_status(hardware_config()),
        config=hardware_config(),
        offsite=offsite_config(),
        offsite_status=offsite_dashboard_status(),
        offsite_backups=offsite_backups,
        backup_sections=_app.backup_sections_for_template(),
        listings=listing_config(),
        weather=weather_config(),
        weather_status=weather_status,
        course_chart=course_chart_config(),
        video=video_config(),
        video_status=video_runtime_status(),
        r2_test_result=read_r2_test_result(),
        public_live_r2_status=public_live_r2_status(),
        ptz_status=ptz_runtime_status(),
        public_branding=branding_assets_for_template(),
        video_usb_sources=list_usb_video_sources(),
        polar_files=list_polar_files(),
        polar_assets=list_polar_assets(),
        events=get_events(None, limit=20),
        input_status=read_manual_horn_input(log_edges=False),
        users=list_users(),
        race_console=race_console_config(),
        audio_status=central_audio_status(),
        start_sequence_status=START_SEQUENCE_STATE.get("last_status", {}),
        power_status=power_runtime_status(),
        server_status=_app.server_runtime_status(),
        track_status=track_runtime_status(),
        public_live_stream_url=public_live_stream_url(),
        defaults={"irc_listing_url": IRC_LISTING_URL, "ytc_listing_url": YTC_LISTING_URL, "weather_station_url": DEFAULT_WEATHER_STATION_URL, "course_chart_tile_url": DEFAULT_COURSE_CHART_TILE_URL, "course_chart_overlay_url": DEFAULT_COURSE_CHART_OVERLAY_URL},
    )


@app.route("/settings/save", methods=["POST"])
@app.route("/admin/settings/save", methods=["POST"])
def settings_save():
    """Save settings from the grouped Settings page."""
    listing_settings, hardware_settings = read_settings_form()
    # The two summaries together are what actually changed, redacted; see
    # core/activitylog.settings_change_summary. Logged even when nothing changed,
    # so "who was in Settings" stays answerable.
    changes = [c for c in (save_app_settings(listing_settings),
                           save_hardware_config(hardware_settings)) if c]
    HARDWARE_RUNTIME_STATE["last_input_active"] = None
    reset_ptz_runtime_state()
    start_video_background_recorder()
    start_public_live_r2_uploader()
    audit("settings saved", "; ".join(changes) if changes else "no changes")
    flash("Settings saved.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/activity-log")
@app.route("/admin/settings/activity-log")
def settings_activity_log():
    """Read the activity log in the browser (admin only).

    The file was always there on the race-office PC, which is no help to somebody
    asking "who changed that, and what was it before?" from the other side of the
    club — and it is the one record that answers it. Read-only: there is no way to
    edit or clear it from here, which is the point of an audit trail.

    ``?day=YYYY-MM-DD`` picks a rolled-over day; the filename is built from a
    validated date inside core.activitylog, never from the query string.
    """
    day = request.args.get("day", "")
    log = activitylog.read_log(day)
    return render_template("activity_log.html", log=log, days=activitylog.log_days(),
                           max_lines=activitylog.MAX_LOG_LINES)


@app.route("/settings/users/add", methods=["POST"])
@app.route("/admin/settings/users/add", methods=["POST"])
def settings_user_add():
    """Create a new local application user."""
    username = request.form.get("username", "").strip()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "")
    role = normalize_role(request.form.get("role", "race_officer"))
    can_set_marks = 1 if request.form.get("can_set_marks") else 0
    can_race_remotely = 1 if request.form.get("can_race_remotely") else 0
    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("settings_page") + "#users")
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (username, password_hash, display_name, role, status, can_set_marks,"
                " can_race_remotely, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (username, generate_password_hash(password), display_name, role, can_set_marks,
                 can_race_remotely, now, now),
            )
            db.commit()
        audit("user added", f"username={username} role={role}")
        flash(f"User {username} added.", "success")
    except sqlite3.IntegrityError:
        flash("That username already exists.", "error")
    return redirect(url_for("settings_page") + "#users")


@app.route("/settings/users/<int:user_id>/password", methods=["POST"])
@app.route("/admin/settings/users/<int:user_id>/password", methods=["POST"])
def settings_user_password(user_id: int):
    """Change a local user's password or active state."""
    password = request.form.get("password", "")
    status = request.form.get("status", "ACTIVE").strip() or "ACTIVE"
    display_name = request.form.get("display_name", "").strip()
    role = normalize_role(request.form.get("role", "race_officer"))
    can_set_marks = 1 if request.form.get("can_set_marks") else 0
    can_race_remotely = 1 if request.form.get("can_race_remotely") else 0
    if status not in ("ACTIVE", "INACTIVE"):
        status = "ACTIVE"
    user = get_user(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("settings_page") + "#users")
    stays_active_admin = role == "admin" and status == "ACTIVE"
    if user_is_admin(user) and user["status"] == "ACTIVE" and not stays_active_admin:
        with get_db() as db:
            other_admins = db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE id <> ? AND status = 'ACTIVE' AND lower(role) = 'admin'",
                (user_id,),
            ).fetchone()["n"]
        if other_admins == 0:
            flash("At least one active administrator must remain.", "error")
            return redirect(url_for("settings_page") + "#users")
    with get_db() as db:
        if password:
            db.execute(
                "UPDATE users SET password_hash = ?, display_name = ?, role = ?, status = ?,"
                " can_set_marks = ?, can_race_remotely = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(password), display_name, role, status, can_set_marks,
                 can_race_remotely,
                 datetime.now().isoformat(timespec="seconds"), user_id),
            )
            flash(f"User {user['username']} updated and password changed.", "success")
        else:
            db.execute(
                "UPDATE users SET display_name = ?, role = ?, status = ?, can_set_marks = ?,"
                " can_race_remotely = ?,"
                " updated_at = ? WHERE id = ?",
                (display_name, role, status, can_set_marks, can_race_remotely,
                 datetime.now().isoformat(timespec="seconds"), user_id),
            )
            flash(f"User {user['username']} updated.", "success")
        db.commit()
    audit("user updated",
          f"username={user['username']} role={role} status={status} marks={can_set_marks}"
          f" onwater={can_race_remotely}")
    return redirect(url_for("settings_page") + "#users")


@app.route("/settings/users/<int:user_id>/delete", methods=["POST"])
@app.route("/admin/settings/users/<int:user_id>/delete", methods=["POST"])
def settings_user_delete(user_id: int):
    """Delete a local user when allowed."""
    current_id = session.get("user_id")
    if current_id and int(current_id) == int(user_id):
        flash("You cannot delete the user you are currently logged in as.", "error")
        return redirect(url_for("settings_page") + "#users")
    target = get_user(user_id)
    with get_db() as db:
        remaining = db.execute("SELECT COUNT(*) AS n FROM users WHERE status = 'ACTIVE' AND id <> ?", (user_id,)).fetchone()["n"]
        other_admins = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE id <> ? AND status = 'ACTIVE' AND lower(role) = 'admin'",
            (user_id,),
        ).fetchone()["n"]
        if remaining == 0:
            flash("At least one active user must remain.", "error")
        elif user_is_admin(target) and other_admins == 0:
            flash("At least one active administrator must remain.", "error")
        else:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            db.commit()
            audit("user deleted", f"username={row_get(target, 'username', user_id)}")
            flash("User deleted.", "success")
    return redirect(url_for("settings_page") + "#users")


@app.route("/hardware")
@app.route("/admin/hardware")
def hardware_page():
    """Redirect the legacy Hardware page to the new Settings section."""
    return redirect(url_for("settings_page") + "#hardware")


@app.route("/hardware/save", methods=["POST"])
@app.route("/admin/hardware/save", methods=["POST"])
def hardware_save():
    # Backwards-compatible endpoint for older templates/bookmarks. Saves the settings page form.
    """Save hardware settings from the legacy Hardware route."""
    listing_settings, hardware_settings = read_settings_form()
    # The two summaries together are what actually changed, redacted; see
    # core/activitylog.settings_change_summary. Logged even when nothing changed,
    # so "who was in Settings" stays answerable.
    changes = [c for c in (save_app_settings(listing_settings),
                           save_hardware_config(hardware_settings)) if c]
    HARDWARE_RUNTIME_STATE["last_input_active"] = None
    reset_ptz_runtime_state()
    start_video_background_recorder()
    start_public_live_r2_uploader()
    audit("settings saved", "; ".join(changes) if changes else "no changes")
    flash("Settings saved.", "success")
    return redirect(url_for("settings_page") + "#hardware")
