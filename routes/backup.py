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
BACKUP_SECTION_BY_ID = _app.BACKUP_SECTION_BY_ID
BACKUP_SECTION_DEFINITIONS = _app.BACKUP_SECTION_DEFINITIONS
backup_sections_for_template = _app.backup_sections_for_template
create_data_backup_zip = _app.create_data_backup_zip
datetime = _app.datetime
flash = _app.flash
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
restore_data_backup_zip = _app.restore_data_backup_zip
send_file = _app.send_file
url_for = _app.url_for


@app.route("/backup")
@app.route("/admin/backup")
def backup_restore_page():
    """Render the data-directory backup/restore page."""
    return render_template("backup_restore.html", sections=backup_sections_for_template())


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
    """Restore selected data sections from an uploaded backup ZIP."""
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
    restored_parts = []
    for section in BACKUP_SECTION_DEFINITIONS:
        section_id = str(section["id"])
        count = int(result.get("restored_counts", {}).get(section_id, 0) or 0)
        if count:
            restored_parts.append(f"{section['label']} ({count} file{'s' if count != 1 else ''})")
    # The most consequential button in the app: it replaces the data the club runs
    # on. Recorded whether or not anything was actually restored.
    audit("backup restored", "; ".join(restored_parts) if restored_parts else "nothing restored")
    message = "Restored: " + "; ".join(restored_parts) if restored_parts else "No files were restored."
    skipped = [BACKUP_SECTION_BY_ID.get(section_id, {"label": section_id})["label"] for section_id in result.get("skipped_sections", [])]
    if skipped:
        message += " Skipped because not present in ZIP: " + ", ".join(skipped) + "."
    flash(message, "success" if restored_parts else "error")
    return redirect(url_for("backup_restore_page"))
