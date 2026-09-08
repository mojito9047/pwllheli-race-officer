"""Race entry and finish routes (add entries, finish, update/delete entry, pursuit positions).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and helpers are
read off the running app module (routes.app_module) so it works whether app.py
was started with ``python app.py`` (module ``__main__``) or imported.
"""
from core.raceadmin import EntryScope, RaceValidationError, add_entries
from routes import app_module

_app = app_module()
app = _app.app
PURSUIT_STATUS_CHOICES = _app.PURSUIT_STATUS_CHOICES
add_active_boats_to_race = _app.add_active_boats_to_race
add_active_boats_to_series_races = _app.add_active_boats_to_series_races
add_boat_database_entry_to_race = _app.add_boat_database_entry_to_race
add_boat_database_entry_to_series_races = _app.add_boat_database_entry_to_series_races
audit = _app.audit
current_actor = _app.current_actor
datetime = _app.datetime
dt_display = _app.dt_display
fire_horn = _app.fire_horn
flash = _app.flash
get_boat = _app.get_boat
get_db = _app.get_db
get_race = _app.get_race
hardware_config = _app.hardware_config
is_finish_assignable_event = _app.is_finish_assignable_event
is_pursuit_race = _app.is_pursuit_race
legacy_entry_rating_value = _app.legacy_entry_rating_value
log_event = _app.log_event
parse_dt = _app.parse_dt
parse_float = _app.parse_float
pursuit_finish_dt = _app.pursuit_finish_dt
recompute_pursuit_start_times = _app.recompute_pursuit_start_times
redirect = _app.redirect
request = _app.request
row_get = _app.row_get
schedule_video_clip = _app.schedule_video_clip
confirm_proposal = _app.confirm_proposal
dismiss_proposal = _app.dismiss_proposal
course_rounding_sequence = _app.course_rounding_sequence
jsonify = _app.jsonify
race_leaderboard = _app.race_leaderboard
set_next_mark_override = _app.set_next_mark_override
url_for = _app.url_for


@app.route("/race/<int:race_id>/event/<int:event_id>/assign_finish", methods=["POST"])
@app.route("/admin/race/<int:race_id>/event/<int:event_id>/assign_finish", methods=["POST"])
def assign_event_as_finish(race_id: int, event_id: int):
    """Assign a manual horn event time to a selected boat's finish."""
    entry_id = request.form.get("entry_id", type=int)
    if not entry_id:
        flash("Choose a boat to assign this horn time to.", "error")
        return redirect(url_for("race_detail", race_id=race_id) + "#tab-start")
    with get_db() as db:
        ev = db.execute("SELECT * FROM race_events WHERE id = ? AND race_id = ?", (event_id, race_id)).fetchone()
        entry = db.execute("SELECT * FROM entries WHERE id = ? AND race_id = ?", (entry_id, race_id)).fetchone()
        if not ev or not entry:
            flash("Could not find that horn event or entry.", "error")
            return redirect(url_for("race_detail", race_id=race_id) + "#tab-start")
        if not is_finish_assignable_event(ev):
            flash("Only manual horn activations can be assigned as finish times.", "error")
            return redirect(url_for("race_detail", race_id=race_id) + "#tab-start")
        db.execute(
            "UPDATE entries SET finish_time = ?, finish_source = ?, status = 'FINISHED' WHERE id = ? AND race_id = ?",
            (ev["event_time"], f"race-log-event-{event_id}", entry_id, race_id),
        )
        db.execute("UPDATE video_clips SET entry_id = ?, updated_at = ? WHERE event_id = ? AND race_id = ?", (entry_id, datetime.now().isoformat(timespec="seconds"), event_id, race_id))
        db.commit()
    label = f"Assigned horn log time {dt_display(ev['event_time'])} as finish for {entry['boat_name']}"
    log_event(race_id, "finish", label, "race-log", {"entry_id": entry_id, "event_id": event_id, "finish_time": ev["event_time"]})
    audit("horn event assigned as finish",
          f"race #{race_id} · {entry['boat_name']} · {dt_display(ev['event_time'])} (event {event_id})")
    flash(label, "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/entry/add_boat", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/add_boat", methods=["POST"])
def add_boat_entry(race_id: int):
    """Add a boat-database record to a race."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    scope = EntryScope(
        kind="boat",
        boat_id=request.form.get("boat_id", type=int),
        class_name=request.form.get("class_name", ""),
        rating_source=request.form.get("rating_source", "AUTO"),
    )
    try:
        with get_db() as db:
            result = add_entries(db, race, scope, actor=current_actor())
    except RaceValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("race_detail", race_id=race_id))
    if result.across_series:
        flash(f"Added {result.boat_name} to {result.added} race(s) in the series with race-entry IRC/YTC rating snapshots.",
              "success" if result.added else "error")
    else:
        flash(f"Added {result.boat_name} with race-entry IRC/YTC rating snapshots." if result.added
              else f"{result.boat_name} is already entered in this race.",
              "success" if result.added else "error")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-entries")


@app.route("/race/<int:race_id>/entry/add_all_active", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/add_all_active", methods=["POST"])
def add_all_active_entries(race_id: int):
    """Add all active boats to a race."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    with get_db() as db:
        result = add_entries(db, race, EntryScope(kind="all_active"), actor=current_actor())
    flash(f"Added {result.added} active boat/race entry record(s) across the series."
          if result.across_series else f"Added {result.added} active boat(s) to the race.",
          "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-entries")


@app.route("/race/<int:race_id>/entry/<int:entry_id>/finish_now", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/<int:entry_id>/finish_now", methods=["POST"])
def finish_now(race_id: int, entry_id: int):
    # In production this machine should use GPS/NTP disciplined time.
    """Record an immediate finish, sound the horn and schedule a finish clip."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        entry = db.execute("SELECT * FROM entries WHERE id = ? AND race_id = ?", (entry_id, race_id)).fetchone()
        if not entry:
            flash("Entry not found.", "error")
            return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")
        db.execute(
            "UPDATE entries SET finish_time = ?, finish_source = 'manual-now', status = 'FINISHED' WHERE id = ? AND race_id = ?",
            (now, entry_id, race_id),
        )
        db.commit()
    label_parts = [entry["boat_name"]]
    if entry["sail_no"]:
        label_parts.append(str(entry["sail_no"]))
    finish_label = f"Finish now + horn: {' — '.join(label_parts)} at {dt_display(now)}"
    horn_result = fire_horn(hardware_config()["horn_duration_ms"])
    log_event(race_id, "finish", finish_label, "finish-now", {"entry_id": entry_id, "finish_time": now, "horn": horn_result})
    schedule_video_clip(race_id, "finish", now, entry_id=entry_id, label=finish_label)
    audit("finish recorded", f"race #{race_id} · {entry['boat_name']}")
    flash(f"Recorded finish for {entry['boat_name']} and fired horn: {horn_result['message']}", "success" if horn_result.get("ok") else "error")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/entry/<int:entry_id>/update", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/<int:entry_id>/update", methods=["POST"])
def update_entry(race_id: int, entry_id: int):
    # Race-page admin owns finish/status plus the per-race rating snapshot.
    # Boat identity remains managed on the Boats page.
    """Update finish time, status, notes and race-specific ratings for an entry."""
    finish_time = request.form.get("finish_time", "").strip() or None
    status = request.form.get("status", "RACING").strip() or "RACING"
    notes = request.form.get("notes", "").strip()
    irc_rating = parse_float(request.form.get("manual_irc_rating"))
    ytc_rating = parse_float(request.form.get("manual_ytc_rating"))
    legacy_rating, legacy_source = legacy_entry_rating_value(irc_rating, ytc_rating)
    if finish_time and not parse_dt(finish_time):
        flash("Invalid finish time format. Use YYYY-MM-DDTHH:MM:SS or the browser date/time picker.", "error")
        return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")
    finish_source = "manual" if finish_time else None
    with get_db() as db:
        # Read the row first, purely so the activity log can say what changed. This
        # is the only action that can rewrite a finish time, a status or a rating on
        # a result that may already be published, so "it was X before" is the whole
        # value of the entry.
        before = db.execute("SELECT * FROM entries WHERE id = ? AND race_id = ?", (entry_id, race_id)).fetchone()
        db.execute(
            """
            UPDATE entries
            SET finish_time = ?, finish_source = ?, status = ?, notes = ?,
                manual_irc_rating = ?, manual_ytc_rating = ?, rating = ?, rating_source = ?
            WHERE id = ? AND race_id = ?
            """,
            (finish_time, finish_source, status, notes, irc_rating, ytc_rating, legacy_rating, legacy_source, entry_id, race_id),
        )
        db.commit()
    changed = []
    for field, new_value in (("finish_time", finish_time), ("status", status),
                             ("manual_irc_rating", irc_rating), ("manual_ytc_rating", ytc_rating),
                             ("notes", notes)):
        old_value = row_get(before, field)
        if str(old_value if old_value is not None else "") != str(new_value if new_value is not None else ""):
            changed.append(f"{field}: {old_value if old_value not in (None, '') else '(blank)'}"
                           f" -> {new_value if new_value not in (None, '') else '(blank)'}")
    if changed:
        audit("entry edited",
              f"race #{race_id} · {row_get(before, 'boat_name', entry_id)} · " + "; ".join(changed))
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/gps_finish", methods=["POST"])
@app.route("/admin/race/<int:race_id>/gps_finish", methods=["POST"])
def set_gps_finish(race_id: int):
    """Arm/disarm GPS auto-finish detection for a race (RO opt-in)."""
    enabled = 1 if request.form.get("gps_finish_enabled") == "1" else 0
    auto = 1 if request.form.get("gps_auto_confirm") == "1" else 0
    with get_db() as db:
        db.execute("UPDATE races SET gps_finish_enabled = ?, gps_auto_confirm = ? WHERE id = ?", (enabled, auto, race_id))
        db.commit()
    audit("gps finish setting changed", f"race #{race_id} · enabled={enabled} auto={auto}")
    flash("GPS auto-finish " + ("armed" if enabled else "disarmed") + (" (auto-confirm on)" if enabled and auto else ""), "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/finish_proposal/<int:proposal_id>/confirm", methods=["POST"])
@app.route("/admin/race/<int:race_id>/finish_proposal/<int:proposal_id>/confirm", methods=["POST"])
def confirm_finish_proposal(race_id: int, proposal_id: int):
    """Confirm a pending GPS finish proposal → record the finish."""
    if confirm_proposal(proposal_id):
        audit("gps finish confirmed", f"race #{race_id} · proposal {proposal_id}")
        flash("GPS finish confirmed.", "success")
    else:
        flash("That finish proposal was already handled.", "error")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/finish_proposal/<int:proposal_id>/dismiss", methods=["POST"])
@app.route("/admin/race/<int:race_id>/finish_proposal/<int:proposal_id>/dismiss", methods=["POST"])
def dismiss_finish_proposal(race_id: int, proposal_id: int):
    """Dismiss a pending GPS finish proposal (no finish recorded)."""
    dismiss_proposal(proposal_id)
    audit("gps finish dismissed", f"race #{race_id} · proposal {proposal_id}")
    flash("GPS finish proposal dismissed.", "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/entry/<int:entry_id>/next_mark", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/<int:entry_id>/next_mark", methods=["POST"])
def entry_next_mark(race_id: int, entry_id: int):
    """Step a boat on to the next mark, or back to the previous one.

    For the wide rounding the app did not see. A mark counts as rounded only if the
    boat passes inside its rounding radius, and the walk through the course is
    sequential — so one mark given a generous berth leaves the boat showing three
    marks behind where it is for the rest of the race, and because a finish is only
    looked for once every earlier mark is rounded, its GPS finish never arrives
    either. The race officer can see plainly what happened; this is how they say so.

    Answers JSON: the caller is the fleet table on the race sheet, which refreshes
    itself, so there is nothing to redirect to.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "message": "Race not found."}), 404
    with get_db() as db:
        entry = db.execute("SELECT * FROM entries WHERE id = ? AND race_id = ?",
                           (entry_id, race_id)).fetchone()
    if not entry:
        return jsonify({"ok": False, "message": "Entry not found."}), 404
    if str(row_get(entry, "status", "") or "").upper() == "FINISHED":
        # Nothing left to sail to, and the finish time is the thing that matters
        # now — which is edited on the entry itself, with the change recorded.
        return jsonify({"ok": False,
                        "message": f"{entry['boat_name']} has finished. "
                                   "Edit the entry to change its finish."}), 400
    seq = course_rounding_sequence(race)
    if not seq:
        return jsonify({"ok": False,
                        "message": "This race has no course marks to step through."}), 400

    direction = str((request.get_json(silent=True) or {}).get("direction", "")).lower()
    if direction not in ("forward", "back"):
        return jsonify({"ok": False, "message": "Direction must be forward or back."}), 400

    # Where the boat is *according to the table the race officer is looking at*, so
    # the arrow always steps from what they can see rather than from a second
    # opinion computed a different way.
    row = next((r for r in race_leaderboard(race_id) if r["entry_id"] == entry_id), None)
    current = row.get("rounded") if row else None
    if current is None:
        current = 0
    step = 1 if direction == "forward" else -1
    # The arrow steps between *marks*, which is what the race officer is looking at,
    # while the override is an index into the walk's own sequence — and the two are
    # not the same once a course carries waypoints, since a waypoint sits in the
    # sequence but is not a mark and is not counted. Step in marks, then translate.
    mark_idx = [i for i, s in enumerate(seq) if not s.get("via")]
    if not mark_idx:
        return jsonify({"ok": False,
                        "message": "This race has no course marks to step through."}), 400
    # The last leg of the sequence is the finish line: "sailing to the line" is a
    # real state and the top of the range. Past it is finishing, which is the finish
    # button's job — not a silent side effect of an arrow.
    ordinal = max(0, min(int(current) + step, len(mark_idx) - 1))
    if ordinal == int(current):
        edge = "already sailing to the finish" if step > 0 else "already sailing to the first mark"
        return jsonify({"ok": False, "message": f"{entry['boat_name']} is {edge}."}), 400

    target = mark_idx[ordinal]
    set_next_mark_override(race_id, entry_id, target)
    mark = seq[target]["code"]
    was = seq[mark_idx[int(current)]]["code"] if int(current) < len(mark_idx) else "?"
    audit("next mark adjusted",
          f"race #{race_id} · {entry['boat_name']} · {was} -> {mark} ({direction})")
    # In the race log too, not only the activity log: the race sheet is where
    # somebody asks afterwards why a boat's progress jumped a mark.
    log_event(race_id, "note", f"{entry['boat_name']} now sailing to {mark}", "manual")
    return jsonify({"ok": True, "next_mark": mark, "rounded": target, "total": len(seq),
                    "message": f"{entry['boat_name']} is now sailing to {mark}."})


@app.route("/race/<int:race_id>/entry/<int:entry_id>/delete", methods=["POST"])
@app.route("/admin/race/<int:race_id>/entry/<int:entry_id>/delete", methods=["POST"])
def delete_entry(race_id: int, entry_id: int):
    """Remove an entry from a race."""
    race = get_race(race_id)
    with get_db() as db:
        db.execute("DELETE FROM entries WHERE id = ? AND race_id = ?", (entry_id, race_id))
        db.commit()
    audit("entry removed from race", f"race #{race_id} · entry {entry_id}")
    if race and is_pursuit_race(race):
        recompute_pursuit_start_times(race_id)
        return redirect(url_for("race_detail", race_id=race_id) + "#tab-entries")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-admin")


@app.route("/race/<int:race_id>/pursuit/positions", methods=["POST"])
@app.route("/admin/race/<int:race_id>/pursuit/positions", methods=["POST"])
def save_pursuit_positions(race_id: int):
    """Record pursuit-race finishing positions (on-the-water order) and statuses."""
    race = get_race(race_id)
    if not race or not is_pursuit_race(race):
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    finish_dt = pursuit_finish_dt(race)
    finish_iso = finish_dt.isoformat(timespec="seconds") if finish_dt else None
    placed = 0
    with get_db() as db:
        entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (race_id,)).fetchall()
        for entry in entries:
            eid = int(entry["id"])
            pos = request.form.get(f"position_{eid}", type=int)
            status = (request.form.get(f"status_{eid}", "") or "").strip().upper()
            if pos and pos > 0:
                db.execute(
                    "UPDATE entries SET pursuit_position = ?, status = 'FINISHED', finish_time = ? WHERE id = ?",
                    (pos, finish_iso, eid),
                )
                placed += 1
            else:
                new_status = status if status in PURSUIT_STATUS_CHOICES else entry["status"]
                db.execute(
                    "UPDATE entries SET pursuit_position = NULL, status = ?, finish_time = NULL WHERE id = ?",
                    (new_status, eid),
                )
        db.commit()
    audit("pursuit positions saved", f"race #{race_id} · {placed} placed")
    flash(f"Saved finishing positions ({placed} placed).", "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-positions")
