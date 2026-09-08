"""Settings action routes (hardware/video/weather tests, branding + polar/sail-chart uploads).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from core.weather_store import WEATHER_POLLER_LOCK, WEATHER_POLLER_STATE
from routes import app_module

_app = app_module()
app = _app.app
ALLOWED_POLAR_EXTENSIONS = _app.ALLOWED_POLAR_EXTENSIONS
APP_VERSION = _app.APP_VERSION
HARDWARE_RUNTIME_STATE = _app.HARDWARE_RUNTIME_STATE
Path = _app.Path
appstate = _app.appstate
datetime = _app.datetime
delete_branding_file_if_unused = _app.delete_branding_file_if_unused
delete_sail_charts_for_polar = _app.delete_sail_charts_for_polar
ensure_polars_dir = _app.ensure_polars_dir
fetch_weather_station_sample = _app.fetch_weather_station_sample
fire_horn = _app.fire_horn
flash = _app.flash
hardware_config = _app.hardware_config
hold_ptz_after_manual_test = _app.hold_ptz_after_manual_test
int_in_range = _app.int_in_range
list_polar_files = _app.list_polar_files
log_event = _app.log_event
normalise_video_public_live_provider = _app.normalise_video_public_live_provider
normalise_video_public_provider = _app.normalise_video_public_provider
ptz_goto_preset = _app.ptz_goto_preset
ptz_test_login = _app.ptz_test_login
queue_central_audio = _app.queue_central_audio
r2_s3_endpoint_host = _app.r2_s3_endpoint_host
race_console_config = _app.race_console_config
read_branding_manifest = _app.read_branding_manifest
audit = _app.audit
read_settings_form = _app.read_settings_form
redirect = _app.redirect
request = _app.request
reset_ptz_runtime_state = _app.reset_ptz_runtime_state
abandon_stuck_public_video_uploads = _app.abandon_stuck_public_video_uploads
retry_public_video_uploads_once = _app.retry_public_video_uploads_once
start_offsite_backup_now = _app.start_offsite_backup_now
safe_r2_key_prefix = _app.safe_r2_key_prefix
save_app_settings = _app.save_app_settings
save_hardware_config = _app.save_hardware_config
save_r2_test_result = _app.save_r2_test_result
save_uploaded_branding_image = _app.save_uploaded_branding_image
save_uploaded_sail_chart_for_polar = _app.save_uploaded_sail_chart_for_polar
secrets = _app.secrets
secure_filename = _app.secure_filename
start_central_audio_worker = _app.start_central_audio_worker
start_video_background_recorder = _app.start_video_background_recorder
threading = _app.threading
time = _app.time
url_for = _app.url_for
video_config = _app.video_config
video_public_r2_credentials_ready = _app.video_public_r2_credentials_ready
video_public_r2_ready = _app.video_public_r2_ready
write_branding_manifest = _app.write_branding_manifest


def save_settings_from_form():
    """Save the posted settings form and record any change in the activity log.

    Every button on the Settings page posts the whole form, so pressing Test Horn
    after editing the serial port saves that edit — which means these routes change
    settings too, and used to do it without a trace. Only logged when something
    actually changed: an unchanged save through a test button is not news, unlike the
    explicit Save, which records the visit either way.
    """
    listing_settings, hardware_settings = read_settings_form()
    changes = [c for c in (save_app_settings(listing_settings),
                           save_hardware_config(hardware_settings)) if c]
    if changes:
        audit("settings changed", "; ".join(changes))
    return listing_settings, hardware_settings


@app.route("/hardware/test_horn", methods=["POST"])
@app.route("/admin/hardware/test_horn", methods=["POST"])
def hardware_test_horn():
    """Save posted hardware settings, then fire a test horn blast.

    The Settings page uses the same large form for Save and Test Horn.  If the
    user changes the serial port, output line, active polarity, or manual-input
    setting and immediately presses Test Horn, use those posted values rather
    than the previously saved database values.
    """
    if "serial_port" in request.form:
        save_settings_from_form()
        HARDWARE_RUNTIME_STATE["last_input_active"] = None
        start_video_background_recorder()
    requested_duration = request.form.get("duration_ms") or request.form.get("horn_duration_ms") or hardware_config()["horn_duration_ms"]
    result = fire_horn(int_in_range(requested_duration, hardware_config()["horn_duration_ms"], 50, 5000))
    log_event(None, "horn-test", "Hardware test horn", "manual", result)
    flash(result["message"], "success" if result.get("ok") else "error")
    return redirect(url_for("settings_page") + "#hardware")


@app.route("/settings/test_audio", methods=["POST"])
@app.route("/admin/settings/test_audio", methods=["POST"])
def settings_test_audio():
    """Queue a central audio test announcement from Settings."""
    save_settings_from_form()
    start_central_audio_worker()
    queue_central_audio("Pwllheli Race Officer central audio test.", race_console_config().get("central_audio_rate", 185), "Settings audio test", None, source="settings-test")
    log_event(None, "audio-test", "Settings central audio test", "central-audio")
    flash("Central audio test queued on the race-office PC.", "success")
    return redirect(url_for("settings_page") + "#hardware")


@app.route("/settings/video/ptz_login_test", methods=["POST"])
@app.route("/admin/settings/video/ptz_login_test", methods=["POST"])
def settings_video_ptz_login_test():
    """Save settings and run a read-only camera login test."""
    save_settings_from_form()
    reset_ptz_runtime_state()
    result = ptz_test_login()
    flash(result.get("message", "Camera login test complete."), "success" if result.get("ok") else "error")
    return redirect(url_for("settings_page") + "#video")


@app.route("/settings/video/ptz_test/<preset_kind>", methods=["POST"])
@app.route("/admin/settings/video/ptz_test/<preset_kind>", methods=["POST"])
def settings_video_ptz_test(preset_kind: str):
    """Save settings and test one configured camera zoom preset."""
    save_settings_from_form()
    reset_ptz_runtime_state()
    result = ptz_goto_preset(preset_kind, reason="settings test", force=True)
    result = hold_ptz_after_manual_test(result, seconds=30)
    flash(result.get("message", "Camera preset test complete."), "success" if result.get("ok") else "error")
    return redirect(url_for("settings_page") + "#video")


@app.route("/settings/video/r2_test", methods=["POST"])
@app.route("/admin/settings/video/r2_test", methods=["POST"])
def settings_video_r2_test():
    """Save settings, upload a tiny test object to R2 and verify its public URL."""
    save_settings_from_form()
    cfg = video_config()
    key = "/".join(p for p in (safe_r2_key_prefix(cfg.get("video_public_r2_prefix") or "race-videos"), "test", "race-officer-r2-test.txt") if p)
    result = {
        "ok": False,
        "key": key,
        "bucket": cfg.get("video_public_r2_bucket") or "",
        "endpoint": f"https://{r2_s3_endpoint_host(cfg)}" if r2_s3_endpoint_host(cfg) else "",
        "public_base_url": cfg.get("video_public_r2_public_base_url") or "",
        "public_url": "",
        "message": "",
        "public_check_ok": False,
        "public_check_message": "",
    }
    if normalise_video_public_provider(cfg.get("video_public_provider")) != "r2" and normalise_video_public_live_provider(cfg.get("video_public_live_provider")) != "r2":
        result["message"] = "Set Public video publishing or Public live image to Cloudflare R2 before testing upload."
        save_r2_test_result(result)
        flash(result["message"], "error")
        return redirect(url_for("settings_page") + "#video")
    if not video_public_r2_credentials_ready(cfg):
        result["message"] = "Cloudflare R2 settings are incomplete. Account ID or S3 endpoint, bucket, access key ID, secret key and public base URL are required."
        save_r2_test_result(result)
        flash(result["message"], "error")
        return redirect(url_for("settings_page") + "#video")
    body_text = f"Pwllheli Race Officer {APP_VERSION} R2 test {datetime.now().isoformat(timespec='seconds')}\n"
    try:
        url = _app.upload_bytes_to_r2(
            cfg,
            key,
            body_text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            cache_control="no-store",
        )
        public_ok, public_message = _app.check_public_r2_url(url, expected_text="Pwllheli Race Officer")
        result.update({
            "ok": True,
            "public_url": url,
            "message": "Upload to Cloudflare R2 worked.",
            "public_check_ok": public_ok,
            "public_check_message": public_message,
        })
        if public_ok:
            flash(f"Cloudflare R2 test upload and public URL check worked: {url}", "success")
        else:
            flash(f"Cloudflare R2 upload worked, but the public URL check failed: {public_message} URL: {url}", "error")
    except Exception as exc:
        result["message"] = f"Cloudflare R2 test upload failed: {exc}"
        flash(result["message"], "error")
    save_r2_test_result(result)
    return redirect(url_for("settings_page") + "#video")


@app.route("/settings/video/r2_retry", methods=["POST"])
@app.route("/admin/settings/video/r2_retry", methods=["POST"])
def settings_video_r2_retry():
    """Save settings and retry failed or stuck public-video R2 uploads."""
    save_settings_from_form()
    cfg = video_config()
    if normalise_video_public_provider(cfg.get("video_public_provider")) != "r2" or not video_public_r2_ready(cfg):
        flash("Cloudflare R2 settings are incomplete; save and test R2 before retrying public video uploads.", "error")
        return redirect(url_for("settings_page") + "#video")

    def _worker() -> None:
        retry_public_video_uploads_once(cfg)

    threading.Thread(target=_worker, name="r2-public-video-retry", daemon=True).start()
    audit("public video uploads retried")
    flash("Retrying failed or stuck public video uploads in the background.", "success")
    return redirect(url_for("settings_page") + "#video")


@app.route("/settings/branding/club_logo", methods=["POST"])
@app.route("/admin/settings/branding/club_logo", methods=["POST"])
def settings_branding_club_logo_upload():
    """Upload or replace the club logo used for public-image/video branding."""
    upload = request.files.get("club_logo_upload")
    try:
        manifest = read_branding_manifest()
        old_name = str(manifest.get("club_logo") or "")
        filename = save_uploaded_branding_image(upload, "club_logo")
        manifest["club_logo"] = filename
        write_branding_manifest(manifest)
        if old_name and old_name != filename:
            delete_branding_file_if_unused(old_name, manifest)
        start_video_background_recorder()
        audit("club logo replaced", stored)
        flash("Saved public branding club logo.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("settings_page") + "#branding")


@app.route("/settings/branding/club_logo/delete", methods=["POST"])
@app.route("/admin/settings/branding/club_logo/delete", methods=["POST"])
def settings_branding_club_logo_delete():
    """Remove the uploaded club logo and return to the bundled PSC logo."""
    manifest = read_branding_manifest()
    old_name = str(manifest.get("club_logo") or "")
    manifest["club_logo"] = ""
    write_branding_manifest(manifest)
    delete_branding_file_if_unused(old_name, manifest)
    start_video_background_recorder()
    audit("club logo reverted to bundled")
    flash("Removed uploaded club logo; the bundled PSC logo will be used.", "success")
    return redirect(url_for("settings_page") + "#branding")


@app.route("/settings/branding/sponsor", methods=["POST"])
@app.route("/admin/settings/branding/sponsor", methods=["POST"])
def settings_branding_sponsor_upload():
    """Upload a sponsor logo for public videos and public camera overlays."""
    upload = request.files.get("sponsor_logo_upload")
    label = (request.form.get("sponsor_label") or "").strip()
    try:
        sponsor_id = secrets.token_hex(6)
        filename = save_uploaded_branding_image(upload, f"sponsor_{sponsor_id}")
        manifest = read_branding_manifest()
        sponsors = manifest.get("sponsors") if isinstance(manifest.get("sponsors"), list) else []
        sponsors.append({"id": sponsor_id, "label": label or Path(filename).stem, "filename": filename})
        manifest["sponsors"] = sponsors
        write_branding_manifest(manifest)
        start_video_background_recorder()
        audit("sponsor logo added", label or filename)
        flash(f"Saved sponsor logo {label or filename}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("settings_page") + "#branding")


@app.route("/settings/branding/sponsor/delete", methods=["POST"])
@app.route("/admin/settings/branding/sponsor/delete", methods=["POST"])
def settings_branding_sponsor_delete():
    """Delete one sponsor logo from the public branding set."""
    sponsor_id = str(request.form.get("sponsor_id") or "").strip()
    manifest = read_branding_manifest()
    old_sponsors = manifest.get("sponsors") if isinstance(manifest.get("sponsors"), list) else []
    removed = [s for s in old_sponsors if str(s.get("id") or "") == sponsor_id]
    manifest["sponsors"] = [s for s in old_sponsors if str(s.get("id") or "") != sponsor_id]
    write_branding_manifest(manifest)
    for item in removed:
        delete_branding_file_if_unused(str(item.get("filename") or ""), manifest)
    start_video_background_recorder()
    if removed:
        audit("sponsor logo deleted", str(sponsor_id))
    flash("Deleted sponsor logo." if removed else "Sponsor logo not found.", "success" if removed else "error")
    return redirect(url_for("settings_page") + "#branding")


@app.route("/settings/weather/test", methods=["POST"])
@app.route("/admin/settings/weather/test", methods=["POST"])
def settings_weather_test():
    """Poll the configured weather station once and report the result."""
    save_settings_from_form()
    status = fetch_weather_station_sample(force=True)
    with WEATHER_POLLER_LOCK:
        WEATHER_POLLER_STATE["last_status"] = status
        WEATHER_POLLER_STATE["last_poll_at"] = time.time()
    flash(status.get("message", "Weather station test complete."), "success" if status.get("ok") else "error")
    return redirect(url_for("settings_page") + "#weather")


@app.route("/settings/polars/upload", methods=["POST"])
@app.route("/admin/settings/polars/upload", methods=["POST"])
def settings_polar_upload():
    """Upload a new polar and optional matching sail chart."""
    upload = request.files.get("polar_upload")
    if not upload or not upload.filename:
        flash("Choose a polar file to upload.", "error")
        return redirect(url_for("settings_page") + "#polars")
    filename = secure_filename(upload.filename)
    if not filename or Path(filename).suffix.lower() not in ALLOWED_POLAR_EXTENSIONS:
        flash("Polar upload must be a .txt, .pol or .csv file.", "error")
        return redirect(url_for("settings_page") + "#polars")
    ensure_polars_dir()
    upload.save(appstate.POLARS_DIR / filename)
    chart_name = None
    chart_upload = request.files.get("sail_chart_upload")
    try:
        chart_name = save_uploaded_sail_chart_for_polar(chart_upload, filename)
    except ValueError as exc:
        flash(f"Uploaded polar {filename}, but the sail chart was not saved: {exc}", "error")
        return redirect(url_for("settings_page") + "#polars")
    if chart_name:
        audit("polar uploaded", f"{filename} with sail chart {chart_name}")
        flash(f"Uploaded polar {filename} with sail chart {chart_name}.", "success")
    else:
        audit("polar uploaded", filename)
        flash(f"Uploaded polar {filename}.", "success")
    return redirect(url_for("settings_page") + "#polars")


@app.route("/settings/polars/sailchart/upload", methods=["POST"])
@app.route("/admin/settings/polars/sailchart/upload", methods=["POST"])
def settings_sail_chart_upload():
    """Upload or replace the sail chart for an existing polar."""
    polar_name = Path((request.form.get("polar_file") or "").replace("\\", "/")).name
    if polar_name not in list_polar_files():
        flash("Choose an installed polar before uploading a sail chart.", "error")
        return redirect(url_for("settings_page") + "#polars")
    upload = request.files.get("sail_chart_upload")
    if not upload or not upload.filename:
        flash("Choose a sail chart to upload.", "error")
        return redirect(url_for("settings_page") + "#polars")
    try:
        chart_name = save_uploaded_sail_chart_for_polar(upload, polar_name)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("settings_page") + "#polars")
    audit("sail chart uploaded", f"{chart_name} for {polar_name}")
    flash(f"Saved sail chart {chart_name} for {polar_name}.", "success")
    return redirect(url_for("settings_page") + "#polars")


@app.route("/settings/polars/delete", methods=["POST"])
@app.route("/admin/settings/polars/delete", methods=["POST"])
def settings_polar_delete():
    """Delete an installed polar and any matching sail chart files."""
    polar_name = Path((request.form.get("polar_file") or "").replace("\\", "/")).name
    if polar_name not in list_polar_files():
        flash("Polar file not found.", "error")
        return redirect(url_for("settings_page") + "#polars")
    polar_path = appstate.POLARS_DIR / polar_name
    deleted_charts = delete_sail_charts_for_polar(polar_name)
    try:
        polar_path.unlink()
        msg = f"Deleted polar {polar_name}"
        if deleted_charts:
            msg += f" and sail chart {', '.join(deleted_charts)}"
        audit("polar deleted", msg)
        flash(msg + ".", "success")
    except OSError as exc:
        flash(f"Could not delete {polar_name}: {exc}", "error")
    return redirect(url_for("settings_page") + "#polars")


@app.route("/settings/polars/sailchart/delete", methods=["POST"])
@app.route("/admin/settings/polars/sailchart/delete", methods=["POST"])
def settings_sail_chart_delete():
    """Delete the sail chart for an installed polar but keep the polar."""
    polar_name = Path((request.form.get("polar_file") or "").replace("\\", "/")).name
    if polar_name not in list_polar_files():
        flash("Polar file not found.", "error")
        return redirect(url_for("settings_page") + "#polars")
    deleted = delete_sail_charts_for_polar(polar_name)
    if deleted:
        audit("sail chart deleted", f"{', '.join(deleted)} for {polar_name}")
        flash(f"Deleted sail chart {', '.join(deleted)} for {polar_name}.", "success")
    else:
        flash(f"No polar-specific sail chart was installed for {polar_name}.", "error")
    return redirect(url_for("settings_page") + "#polars")


@app.route("/settings/offsite_backup/run", methods=["POST"])
@app.route("/admin/settings/offsite_backup/run", methods=["POST"])
def settings_offsite_backup_now():
    """Save settings, then start one off-site backup in the background.

    This used to run the backup inside the request so the admin saw the real
    outcome. That was the right goal and the wrong mechanism: a backup takes
    minutes on the hut's 4G, and the hut is reached through a cloudflared tunnel
    that abandons a request at around 100 seconds. The race officer got
    Cloudflare's "Gateway time-out 504" while the backup carried on behind it and
    completed — the archive in R2, the page reporting failure.

    So it starts the work and returns immediately; the status box on this page
    carries the progress and the result, and refreshes itself while one is running.

    The busy check is still skipped: somebody standing at the PC asking for a
    backup has better information than the guard does. Encryption and verification
    are not skipped.
    """
    save_settings_from_form()
    started, message = start_offsite_backup_now()
    audit("off-site backup started by hand" if started else "off-site backup already running")
    flash(message, "success" if started else "error")
    return redirect(url_for("settings_page") + "#offsite-backup")


@app.route("/settings/video/uploads/abandon", methods=["POST"])
@app.route("/admin/settings/video/uploads/abandon", methods=["POST"])
def settings_video_uploads_abandon():
    """Stop trying to publish the public-video uploads that are stuck.

    There was a button to retry them and nothing to stop. A backlog left over
    from a misconfigured bucket also holds up the nightly off-site backup, which
    stands aside while race videos are still uploading — 84 of them kept deferring
    it on the hut.

    Nothing is deleted: no clip, no evidence file, no public copy. Only the
    publishing state changes, and an abandoned clip is still picked up by the retry
    button, so fixing R2 later and pressing Retry queues them all again.
    """
    count = abandon_stuck_public_video_uploads()
    audit("public video uploads given up on", f"{count} clip(s)")
    if count:
        flash(f"Gave up on {count} stuck public video upload{'s' if count != 1 else ''}. "
              "The evidence clips are untouched; use Retry to try publishing them again.", "success")
    else:
        flash("No public video uploads were waiting.", "success")
    return redirect(url_for("settings_page") + "#video")
