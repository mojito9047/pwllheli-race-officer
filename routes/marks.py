"""Marks admin pages (list, add, edit, delete) and the on-the-water ping page.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from routes import app_module

_app = app_module()
app = _app.app
appstate = _app.appstate
audit = _app.audit
core_marks = _app.core_marks
current_user_is_admin = _app.current_user_is_admin
current_user_can_set_marks = _app.current_user_can_set_marks
current_user_is_mark_layer = _app.current_user_is_mark_layer
session = _app.session
jsonify = _app.jsonify
track_config = _app.track_config
flash = _app.flash
redirect = _app.redirect
reload_course_mark_data = _app.reload_course_mark_data
render_template = _app.render_template
request = _app.request
url_for = _app.url_for


@app.route("/marks")
@app.route("/admin/marks")
def marks_page():
    """Render the mark list page (admins can add/remove simple marks)."""
    delete_blocks = {code: core_marks.mark_delete_block(code) for code in appstate.MARKS}
    edit_blocks = {code: core_marks.mark_edit_block(code) for code in appstate.MARKS}
    return render_template("marks.html", marks=appstate.MARKS,
                           mark_delete_blocks=delete_blocks,
                           mark_edit_blocks=edit_blocks,
                           # Editing an existing mark follows the Set marks
                           # permission; adding and deleting stay with admins,
                           # because those change what every course refers to.
                           can_edit_marks=current_user_can_set_marks(),
                           can_manage_marks=current_user_is_admin(),
                           can_set_marks=current_user_can_set_marks(),
                           # So a blank per-mark radius can say what it inherits
                           # rather than leaving the reader to go and look.
                           default_radius_m=track_config()["rounding_radius_m"])


@app.route("/marks/add", methods=["POST"])
@app.route("/admin/marks/add", methods=["POST"])
def marks_add():
    """Add a new simple racing mark to data/marks.json (admin only)."""
    ok, result = core_marks.add_mark(
        request.form.get("code", ""),
        request.form.get("name", ""),
        request.form.get("lat", ""),
        request.form.get("lon", ""),
        request.form.get("buoy", ""),
        request.form.get("top_mark", ""),
        request.form.get("rounding_radius_m", ""),
        waypoint=bool(request.form.get("waypoint")),
    )
    if ok:
        reload_course_mark_data()
        kind = "waypoint" if request.form.get("waypoint") else "mark"
        audit(f"{kind} added", f"code={result}")
        flash(f"{kind.capitalize()} '{result}' added.", "success")
    else:
        flash(result, "error")
    return redirect(url_for("marks_page"))


@app.route("/marks/<code>/delete", methods=["POST"])
@app.route("/admin/marks/<code>/delete", methods=["POST"])
def mark_delete(code: str):
    """Delete a racing mark when it is not in use (admin only)."""
    ok, result = core_marks.delete_mark(code)
    if ok:
        reload_course_mark_data()
        audit("mark deleted", f"code={code}")
        flash(f"Mark '{code}' deleted.", "success")
    else:
        flash(f"Cannot delete mark '{code}': {result}", "error")
    return redirect(url_for("marks_page"))


@app.route("/marks/<code>/edit", methods=["POST"])
@app.route("/admin/marks/<code>/edit", methods=["POST"])
def mark_edit(code: str):
    """Change an existing mark's name, position or description.

    Needs the **Set marks** permission rather than administrator access. The
    permission already lets somebody stand next to a mark and set it to where they
    are; typing a correction to the same mark's name, buoy description or rounding
    radius is the lesser act, and the person who has just re-laid it is the one who
    knows. Adding and deleting marks stay admin-only (see ADMIN_ONLY_ENDPOINTS).
    """
    if not current_user_can_set_marks():
        flash("You do not have permission to edit marks.", "error")
        return redirect(url_for("marks_page"))
    ok, result = core_marks.update_mark(
        code,
        request.form.get("name", ""),
        request.form.get("lat", ""),
        request.form.get("lon", ""),
        request.form.get("buoy", ""),
        request.form.get("top_mark", ""),
        by=session.get("username", ""),
        source="manual",
        rounding_radius_m=request.form.get("rounding_radius_m", ""),
    )
    if ok:
        reload_course_mark_data()
        audit("mark edited", f"code={code}")
        flash(f"Mark '{code}' updated.", "success")
    else:
        flash(f"Cannot update mark '{code}': {result}", "error")
    return redirect(url_for("marks_page"))


@app.route("/marks/ping")
@app.route("/admin/marks/ping")
def marks_ping_page():
    """The page taken out in the RIB: pick a mark, then set it to where you are.

    A page of its own rather than a corner of the marks admin page. It is used
    one-handed, on a phone, in a small boat, by somebody who is not necessarily
    an administrator — so it carries nothing but the job in hand.
    """
    if not current_user_can_set_marks():
        flash("You do not have permission to set mark positions.", "error")
        return redirect(url_for("marks_page"))
    marks = {code: md for code, md in appstate.MARKS.items()
             if core_marks.mark_edit_block(code) is None}
    # A mark layer cannot reach the marks page, so offering them a link back to
    # it would just bounce them here again — a button that appears to do nothing.
    return render_template("marks_ping.html", marks=marks,
                           show_back=not current_user_is_mark_layer())


@app.route("/marks/<code>/position", methods=["POST"])
@app.route("/admin/marks/<code>/position", methods=["POST"])
def mark_set_position(code: str):
    """Set a mark to a measured position. Answers JSON: the caller is a phone."""
    if not current_user_can_set_marks():
        return jsonify({"ok": False, "message": "You do not have permission to set mark positions."}), 403
    payload = request.get_json(silent=True) or {}
    ok, message = core_marks.set_mark_position(
        code,
        payload.get("lat"),
        payload.get("lon"),
        by=session.get("username", ""),
        accuracy_m=payload.get("accuracy_m"),
        source="phone",
        confirm_large_move=bool(payload.get("confirm")),
    )
    if ok:
        reload_course_mark_data()
        audit("mark position set",
              f"code={code} lat={payload.get('lat')} lon={payload.get('lon')} "
              f"accuracy={payload.get('accuracy_m')}")
        md = appstate.MARKS.get(code) or {}
        return jsonify({"ok": True, "message": message,
                        "lat_text": md.get("lat_text"), "lon_text": md.get("lon_text")})
    return jsonify({"ok": False, "message": message}), 400
