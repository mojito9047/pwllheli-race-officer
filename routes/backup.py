"""Backup download / restore routes.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from werkzeug.wsgi import ClosingIterator

from routes import app_module

_app = app_module()
app = _app.app
audit = _app.audit
backup_sections_for_template = _app.backup_sections_for_template
create_data_backup_zip = _app.create_data_backup_zip
current_actor = _app.current_actor
current_user_is_admin = _app.current_user_is_admin
datetime = _app.datetime
describe_restore_result = _app.describe_restore_result
flash = _app.flash
jsonify = _app.jsonify
list_offsite_backups = _app.list_offsite_backups
offsite = _app.offsite
offsite_config = _app.offsite_config
offsite_restore_status = _app.offsite_restore_status
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
restore_data_backup_zip = _app.restore_data_backup_zip
send_file = _app.send_file
start_offsite_restore = _app.start_offsite_restore
url_for = _app.url_for

# The page waits on this listing, and through the relay a page that has not
# started answering in five seconds is replaced by the holding page.
OFFSITE_LIST_TIMEOUT_S = 4.0


def offsite_restore_choices():
    """The off-site backups an administrator can restore here, or why there are none."""
    cfg = offsite_config()
    ready, why = offsite.offsite_bucket_readable(cfg)
    view = {"ready": ready, "why": why, "bucket": cfg["offsite_backup_bucket"],
            "passphrase_saved": bool(cfg["offsite_backup_passphrase_set"]), "archives": [], "error": ""}
    if not ready:
        return view
    try:
        archives = list_offsite_backups(cfg, timeout=OFFSITE_LIST_TIMEOUT_S)
    except Exception as exc:
        view["error"] = str(exc)
        return view
    for obj in archives:
        made = offsite.offsite_archive_time(obj["key"])
        view["archives"].append({
            "key": obj["key"],
            "label": made.strftime("%a %d %b %Y, %H:%M") if made else obj["key"].rsplit("/", 1)[-1],
            "size_mb": f"{int(obj.get('size_bytes') or 0) / 1_048_576:.1f}",
        })
    return view


@app.route("/backup")
@app.route("/admin/backup")
def backup_restore_page():
    """Render the data-directory backup/restore page."""
    is_admin = current_user_is_admin()
    return render_template(
        "backup_restore.html", sections=backup_sections_for_template(),
        # Admins only: restoring is theirs, and the listing names what is in the
        # club's private bucket.
        offsite_restore=offsite_restore_choices() if is_admin else None,
        offsite_restore_state=offsite_restore_status() if is_admin else {},
    )


@app.route("/backup/download", methods=["POST"])
@app.route("/admin/backup/download", methods=["POST"])
def backup_download():
    """Download a ZIP backup containing the selected data sections."""
    try:
        backup_path, summary = create_data_backup_zip(request.form.getlist("sections"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("backup_restore_page"))
    except Exception as exc:
        flash(f"Could not create backup: {exc}", "error")
        return redirect(url_for("backup_restore_page"))
    audit("backup downloaded", "sections: " + (", ".join(summary.get("sections", [])) or "none"))
    filename = f"pwllheli-race-officer-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    response = send_file(backup_path, as_attachment=True, download_name=filename, mimetype="application/zip")

    def _cleanup_backup_file() -> None:
        try:
            backup_path.unlink(missing_ok=True)
        except Exception:
            pass        # still being written out; the next backup sweeps it

    # NOT response.call_on_close: send_file sets direct_passthrough, so the WSGI
    # server is handed the file wrapper itself and Response.close() — where
    # call_on_close callbacks run — is never called. The archive was therefore
    # never deleted; 218 of them (316 MB) had collected in runtime/ on the hut
    # PC. Wrapping the iterable means the server's close() reaches the callback,
    # and it runs *after* the file wrapper is closed so Windows lets it go.
    response.response = ClosingIterator(response.response, _cleanup_backup_file)
    response.headers["X-Race-Officer-Backup-Sections"] = ",".join(summary.get("sections", []))
    return response


@app.route("/backup/restore", methods=["POST"])
@app.route("/admin/backup/restore", methods=["POST"])
def backup_restore():
    """Restore selected data sections, from an off-site backup or an uploaded ZIP.

    One form, two sources. An off-site backup is downloaded by the hut itself in
    the background, and the page follows it (see backup_restore_status); the
    activity log entry is written when it finishes. An uploaded ZIP is already
    here, so it is restored in the request, as it always was.
    """
    if request.form.get("source") == "offsite":
        started, why = start_offsite_restore(
            request.form.get("offsite_key", ""),
            request.form.getlist("sections"),
            request.form.get("backup_passphrase", ""),
            current_actor(),
        )
        if not started:
            flash(why, "error")
        return redirect(url_for("backup_restore_page") + "#restore")
    try:
        result = restore_data_backup_zip(
            request.files.get("backup_file"),
            request.form.getlist("sections"),
            # Only an off-site copy is encrypted; for a plain ZIP this is ignored.
            request.form.get("backup_passphrase", ""),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("backup_restore_page"))
    except Exception as exc:
        flash(f"Could not restore backup: {exc}", "error")
        return redirect(url_for("backup_restore_page"))
    restored, message, details = describe_restore_result(result)
    # The most consequential button in the app: it replaces the data the club runs
    # on. Recorded whether or not anything was actually restored.
    audit("backup restored", details)
    flash(message, "success" if restored else "error")
    return redirect(url_for("backup_restore_page"))


@app.route("/backup/restore/status")
@app.route("/admin/backup/restore/status")
def backup_restore_status():
    """Where an off-site restore has got to, for the page to poll."""
    return jsonify(offsite_restore_status())
