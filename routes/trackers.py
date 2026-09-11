"""Trackers management page — assign GPS trackers to boats, add/remove devices.

Split out of app.py into the routes/ package. This page is available to race
officers (not admin-gated like Settings), since managing which tracker is on
which boat is a race-day task. The Traccar *connection* config stays in Settings.
Endpoint names + the ``/x`` + ``/admin/x`` decorators follow the package pattern.
"""
import time

from routes import app_module

_app = app_module()
app = _app.app
adopt_tracker = _app.adopt_tracker
command_templates_for = _app.command_templates_for
current_user_is_admin = _app.current_user_is_admin
jsonify = _app.jsonify
delete_tracker_type = _app.delete_tracker_type
list_tracker_types = _app.list_tracker_types
send_tracker_command = _app.send_tracker_command
tracker_command_results = _app.tracker_command_results
tracker_models_by_unique_id = _app.tracker_models_by_unique_id
tracker_protocols = _app.tracker_protocols
TRACKER_PROTOCOL_FAMILIES = _app.TRACKER_PROTOCOL_FAMILIES
upsert_tracker_type = _app.upsert_tracker_type
unregistered_traccar_devices = _app.unregistered_traccar_devices
appstate = _app.appstate
audit = _app.audit
course_chart_config = _app.course_chart_config
flash = _app.flash
hologram_account = _app.hologram_account
hologram_active = _app.hologram_active
hologram_invalidate = _app.hologram_invalidate
hologram_paused_sims = _app.hologram_paused_sims
hologram_sim_status = _app.hologram_sim_status
list_trackers = _app.list_trackers
redirect = _app.redirect
remove_tracker = _app.remove_tracker
render_template = _app.render_template
request = _app.request
search_boats = _app.search_boats
get_boat = _app.get_boat
track_runtime_status = _app.track_runtime_status
tracker_report_status = _app.tracker_report_status
upsert_tracker = _app.upsert_tracker
url_for = _app.url_for


@app.route("/trackers")
@app.route("/admin/trackers")
def trackers_page():
    """List trackers, their boat assignment and reporting status (RO-accessible)."""
    # Devices Traccar has seen but we have not adopted — pick one instead of
    # typing its IMEI. Empty (and harmless) when Traccar is not configured.
    available, available_error = unregistered_traccar_devices()
    registered = list_trackers()
    # What model each one is, resolved in a single pass: the type table and the
    # last-seen protocol are each read once rather than per row.
    models = tracker_models_by_unique_id(
        [t["unique_id"] for t in registered] + [d["unique_id"] for d in available])
    # What each device actually speaks, so an unknown type can be catalogued against the
    # protocol it was seen using rather than against every device sharing its type code.
    protocols = tracker_protocols()
    # What the SIM in each tracker is doing. One call for the whole fleet, cached,
    # and empty rather than an error when Hologram is off or unreachable — this is
    # a page opened on race morning and it must not wait on a third party.
    sims = hologram_sim_status([t["unique_id"] for t in registered])
    return render_template(
        "trackers.html",
        sim_status=sims["sims"],
        sim_error=sims["error"],
        sim_paused=hologram_paused_sims(sims),
        sim_age_s=sims["age_s"],
        sim_account=hologram_account() if hologram_active() else {},
        sim_lookup_enabled=hologram_active(),
        trackers=registered,
        tracker_models=models,
        tracker_protocols=protocols,
        known_protocols=sorted(set(protocols.values()) | set(TRACKER_PROTOCOL_FAMILIES)),
        tracker_types=list_tracker_types(),
        command_templates={t["unique_id"]: command_templates_for(t["unique_id"])
                           for t in registered} if current_user_is_admin() else {},
        can_send_commands=current_user_is_admin(),
        available_devices=available,
        available_error=available_error,
        track_boats=search_boats(limit=400),
        track_status=track_runtime_status(),
        report_status=tracker_report_status(),
        marks_data=appstate.MARKS,
        course_chart=course_chart_config(),
    )


@app.route("/trackers/sim-refresh", methods=["POST"])
@app.route("/admin/trackers/sim-refresh", methods=["POST"])
def trackers_sim_refresh():
    """Ask Hologram again now, rather than waiting for the cache to age out.

    Wanted at one specific moment: somebody has just changed a SIM in the
    Hologram dashboard and come here to see it. Blocking is right here — the
    person pressed a button and is waiting for the answer, which is the opposite
    of the page render this normally has to stay out of the way of.
    """
    hologram_invalidate()
    hologram_sim_status(blocking=True)
    return redirect(url_for("trackers_page"))


@app.route("/trackers/adopt", methods=["POST"])
@app.route("/admin/trackers/adopt", methods=["POST"])
def trackers_adopt():
    """Adopt a device Traccar already knows about (auto-registered when it connected)."""
    unique_id = request.form.get("unique_id", "").strip()
    name = request.form.get("label", "").strip()
    boat_id = request.form.get("boat_id", type=int)
    ok, message = adopt_tracker(unique_id, name=name, boat_id=boat_id)
    if ok:
        audit("tracker adopted", unique_id)
    flash(message, "success" if ok else "error")
    return redirect(url_for("trackers_page"))


@app.route("/trackers/save", methods=["POST"])
@app.route("/admin/trackers/save", methods=["POST"])
def trackers_save():
    """Re-assign / re-label existing trackers (boat_<uid>, label_<uid>)."""
    rows = [(uid.strip(), request.form.get(f"label_{uid.strip()}", "").strip(),
             request.form.get(f"boat_{uid.strip()}", type=int))
            for uid in request.form.getlist("tracker_uids") if (uid or "").strip()]
    # Unassignments first, then assignments. One boat has one tracker, so
    # moving a boat from one device to another is a clear and a set -- and this
    # page posts every row together, so doing them in form order would have the
    # set displace a device the very next row was about to free anyway. The
    # displacement is harmless but it would be reported, and a message about
    # something the operator did not do is worse than no message.
    for uid, label, boat_id in [r for r in rows if not r[2]]:
        upsert_tracker(uid, label=label, boat_id=None)
    # Collected per boat rather than as one flat list. Setting two trackers to
    # the same boat in one save makes the second displace the first, so a flat
    # list would name a tracker that ended up holding the boat after all --
    # which is what the first version of this message did, and it read as
    # nonsense next to a table showing that tracker assigned.
    took_over = {}
    for uid, label, boat_id in [r for r in rows if r[2]]:
        lost = upsert_tracker(uid, label=label, boat_id=boat_id)
        if lost:
            winner, previous = took_over.get(boat_id, (None, []))
            took_over[boat_id] = (uid, [u for u in previous + lost if u != uid])

    audit("trackers saved")
    flash("Tracker assignments saved.", "success")
    for boat_id, (winner, lost) in took_over.items():
        lost = [u for u in lost if u != winner]
        if not lost:
            continue
        boat = get_boat(boat_id)
        name = (boat["boat_name"] if boat else f"Boat {boat_id}")
        flash(f"{name} can only carry one tracker, so it now has {winner} and "
              + ", ".join(lost) + (" was" if len(lost) == 1 else " were")
              + " left unassigned. If that is the wrong way round, set the boat on the"
              " tracker you want and save again. Past fixes keep the boat they were"
              " recorded against either way.", "warning")
    return redirect(url_for("trackers_page"))


@app.route("/trackers/remove", methods=["POST"])
@app.route("/admin/trackers/remove", methods=["POST"])
def trackers_remove():
    """Remove a tracker (deletes it in Traccar too); the boat keeps its past track."""
    unique_id = request.form.get("unique_id", "").strip()
    ok, message = remove_tracker(unique_id)
    if ok:
        audit("tracker removed", unique_id)
    flash(message, "success" if ok else "error")
    return redirect(url_for("trackers_page"))


@app.route("/trackers/types/add", methods=["POST"])
@app.route("/admin/trackers/types/add", methods=["POST"])
def trackers_type_add():
    """Catalogue an IMEI type code as a model name.

    Not admin-gated: naming a device model records a fact about the kit, it does not
    reach the hardware. The command console next door is the part that does.
    """
    ok, message = upsert_tracker_type(
        request.form.get("tac", ""), request.form.get("model", ""),
        request.form.get("notes", ""), request.form.get("protocol", ""))
    if ok:
        audit("tracker type catalogued", f"{request.form.get('tac','')} = {request.form.get('model','')}")
    flash(message, "success" if ok else "error")
    return redirect(url_for("trackers_page"))


@app.route("/trackers/types/remove", methods=["POST"])
@app.route("/admin/trackers/types/remove", methods=["POST"])
def trackers_type_remove():
    """Forget a catalogued tracker type, or revert an overridden built-in to its default."""
    tac = request.form.get("tac", "").strip()
    ok, message = delete_tracker_type(tac)
    if ok:
        audit("tracker type removed", tac)
    flash(message, "success" if ok else "error")
    return redirect(url_for("trackers_page"))


@app.route("/trackers/command", methods=["POST"])
@app.route("/admin/trackers/command", methods=["POST"])
def trackers_command():
    """Send one raw protocol command to a tracker (administrators only).

    Answers JSON so the console can stay on the page. The command is *not* echoed into
    a flash: the reply arrives seconds to minutes later and belongs beside the command
    that caused it, which is what the console shows.
    """
    unique_id = (request.form.get("unique_id") or "").strip()
    command = (request.form.get("command") or "").strip()
    ok, message = send_tracker_command(unique_id, command)
    # Logged either way. A refused command is worth a line too: it records that
    # somebody tried to factory-reset a tracker from the race office.
    audit("tracker command sent" if ok else "tracker command refused",
          f"{unique_id}: {command[:120]}")
    return jsonify({"ok": ok, "message": message, "sent_at": time.time(), "command": command})


@app.route("/trackers/command/replies")
@app.route("/admin/trackers/command/replies")
def trackers_command_replies():
    """Replies a tracker has sent back since a given moment (administrators only)."""
    unique_id = (request.args.get("unique_id") or "").strip()
    try:
        since = float(request.args.get("since") or 0) or None
    except (TypeError, ValueError):
        since = None
    return jsonify({"ok": True, "replies": tracker_command_results(unique_id, since)})
