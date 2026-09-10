"""Race sheet lifecycle routes (new, detail, update, delete, results CSV, current).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and helpers are
read off the running app module (routes.app_module) so it works whether app.py
was started with ``python app.py`` (module ``__main__``) or imported.
"""
import re

from core import track
from core.raceadmin import next_whole_minute
from core.series import export_attachment
from core.racesignals import LOWER_AP_MIN_LEAD_S
from core import barreplay
from core.races import postponement_flag, race_first_start_dt
from core.raceadmin import (
    RaceSettings,
    RaceSpec,
    RaceValidationError,
    create_race,
    update_race_settings,
)
from routes import app_module

_app = app_module()
app = _app.app
Any = _app.Any
CLASS_CONFIG_HELP = _app.CLASS_CONFIG_HELP
Dict = _app.Dict
Optional = _app.Optional
Response = _app.Response
START_PLAN_HELP = _app.START_PLAN_HELP
_render_pursuit_race_detail = _app._render_pursuit_race_detail
_update_pursuit_race = _app._update_pursuit_race
add_active_boats_to_race = _app.add_active_boats_to_race
add_active_boats_to_series_races = _app.add_active_boats_to_series_races
analyse_course_with_wind = _app.analyse_course_with_wind
apply_course_shortening = _app.apply_course_shortening
appstate = _app.appstate
assign_pursuit_start_times = _app.assign_pursuit_start_times
audit = _app.audit
class_config_text = _app.class_config_text
compute_dual_results = _app.compute_dual_results
compute_results = _app.compute_results
pending_proposals_for_race = _app.pending_proposals_for_race
list_trackers = _app.list_trackers
track_config = _app.track_config
course_announcement_text = _app.course_announcement_text
course_chart_config = _app.course_chart_config
course_for_race = _app.course_for_race
course_shorten_options = _app.course_shorten_options
csv = _app.csv
current_actor = _app.current_actor
custom_course_from_race = _app.custom_course_from_race
datetime = _app.datetime
delete_race_and_related_data = _app.delete_race_and_related_data
dt_full_display = _app.dt_full_display
ensure_start_video_scheduled = _app.ensure_start_video_scheduled
entry_class_labels_map = _app.entry_class_labels_map
flash = _app.flash
get_boats_by_id = _app.get_boats_by_id
normalise_sailwave_rating_system = _app.normalise_sailwave_rating_system
race_number_in_series = _app.race_number_in_series
race_series_row = _app.race_series_row
get_series_races = _app.get_series_races
sailwave_rows_for_race = _app.sailwave_rows_for_race
write_sailwave_csv = _app.write_sailwave_csv
get_current_competitor_race = _app.get_current_competitor_race
get_db = _app.get_db
get_entries = _app.get_entries
get_events = _app.get_events
get_race = _app.get_race
get_series = _app.get_series
hardware_config = _app.hardware_config
init_db = _app.init_db
io = _app.io
is_pursuit_race = _app.is_pursuit_race
json = _app.json
latest_weather_sample = _app.latest_weather_sample
list_polar_files = _app.list_polar_files
list_series = _app.list_series
normalise_start_time_value = _app.normalise_start_time_value
parse_dt = _app.parse_dt
parse_start_plan_text = _app.parse_start_plan_text
race_class_config = _app.race_class_config
race_console_config = _app.race_console_config
race_delete_summary = _app.race_delete_summary
race_first_start_time = _app.race_first_start_time
race_saved_polar = _app.race_saved_polar
race_start_plan = _app.race_start_plan
race_start_schedule = _app.race_start_schedule
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
reset_start_sequence_state_for_race = _app.reset_start_sequence_state_for_race
resolve_polar_path = _app.resolve_polar_path
row_get = _app.row_get
safe_json_loads = _app.safe_json_loads
search_boats = _app.search_boats
selectable_mark_names = _app.selectable_mark_names
series_class_config = _app.series_class_config
signal_panel_schedule = _app.signal_panel_schedule
start_plan_from_grid_form = _app.start_plan_from_grid_form
start_plan_grid_from_plan = _app.start_plan_grid_from_plan
start_plan_text = _app.start_plan_text
start_signal_plan_rows = _app.start_signal_plan_rows
sync_race_entries_to_series = _app.sync_race_entries_to_series
sync_series_entries_into_race = _app.sync_series_entries_into_race
time = _app.time
url_for = _app.url_for
video_clip_link_text = _app.video_clip_link_text
video_clip_maps = _app.video_clip_maps
video_runtime_status = _app.video_runtime_status
weather_runtime_status = _app.weather_runtime_status


@app.route("/admin/race/current")
def current_race_sheet():
    """Redirect the race officer to the current race sheet."""
    race = get_current_competitor_race()
    if not race:
        flash("No race sheets have been created yet.", "error")
        return redirect(url_for("new_race"))
    return redirect(url_for("race_detail", race_id=int(race["id"])))


@app.route("/race/new", methods=["GET", "POST"])
@app.route("/admin/race/new", methods=["GET", "POST"])
def new_race():
    """Create a new race shell before course/start setup."""
    init_db()
    selected_series = request.args.get("series_id", type=int)
    if request.method == "POST":
        # The form is read here; what a race is, and the order things happen in,
        # belongs to core.raceadmin so a caller that is not a browser gets the
        # same rules rather than a second copy of them.
        spec = RaceSpec(
            name=request.form.get("name", ""),
            series_id=request.form.get("series_id", type=int),
            class_name=request.form.get("class_name", ""),
            notes=request.form.get("notes", ""),
            race_type=request.form.get("race_type", ""),
            pursuit_rating=request.form.get("pursuit_rating", ""),
            pursuit_duration_min=request.form.get("pursuit_duration_min", type=float),
            add_all_active=request.form.get("add_all_active") == "1",
        )
        try:
            with get_db() as db:
                created = create_race(db, spec, actor=current_actor())
        except RaceValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("new_race"))
        tail = "Set the course and first warning-signal time on the race sheet."
        if spec.series_id:
            flash(f"Race created and {created.entries_added} existing/selected series competitor(s) added. {tail}", "success")
        elif spec.add_all_active:
            flash(f"Race created and {created.entries_added} active boat(s) added. {tail}", "success")
        else:
            flash(f"Race created. {tail}", "success")
        return redirect(url_for("race_detail", race_id=created.race_id))
    series_list = list_series()
    return render_template("new_race.html", series_list=series_list, selected_series=selected_series)


@app.route("/race/<int:race_id>")
@app.route("/admin/race/<int:race_id>")
def race_detail(race_id: int):
    """Render the main race workflow page."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    if is_pursuit_race(race):
        return _render_pursuit_race_detail(race)
    ensure_start_video_scheduled(race)
    course = course_for_race(race)
    shorten_options = course_shorten_options(course)
    shorten_index = row_get(race, "shortened_at_index", None)
    course_shortened = shorten_index is not None and str(shorten_index) != ""
    if course_shortened:
        course = apply_course_shortening(course, shorten_index)
    entries = get_entries(race_id)
    entry_boats = get_boats_by_id(e["boat_id"] for e in entries)
    results = compute_results(race, entries)
    results = sorted(results, key=lambda r: (r["rank"] is None, r["rank"] or 9999, r["entry"]["boat_name"]))
    dual_results = compute_dual_results(race, entries)
    polar_files = list_polar_files()
    selected_polar = request.args.get("polar_file") or race_saved_polar(race, polar_files)
    polar_path = resolve_polar_path(selected_polar)
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    twd = latest_wind.get("twd") if latest_wind else None
    tws = latest_wind.get("tws") if latest_wind else None
    course_analysis = analyse_course_with_wind(course, twd, tws, polar_path,
                                               marks=track.race_marks(race))
    legs = course_analysis["legs_analysis"]
    events = get_events(race_id, limit=None)
    _replay_plan = barreplay.replay_plan(race)
    boats = search_boats(limit=400)
    all_active_count = len(boats)
    fleets = sorted({(b["class_name"] or "") for b in boats if b["class_name"]})
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    race_finished = bool(entries) and racing_count == 0
    series_list = list_series()
    current_series = get_series(race["series_id"]) if "series_id" in race.keys() and race["series_id"] else None
    video_maps = video_clip_maps(race_id)
    effective_class_config = race_class_config(race)
    effective_start_plan = race_start_plan(race)
    race_start_override = safe_json_loads(row_get(race, "start_plan_json", ""), {"starts": []})
    race_start_editor_grid = start_plan_grid_from_plan(effective_start_plan, effective_class_config)
    delete_summary = race_delete_summary(race, entries, get_events(race_id, limit=100000), video_maps["clips"])
    return render_template(
        "race.html", race=race, course=course, courses=appstate.COURSES, entries=entries, results=results, dual_results=dual_results,
        legs=legs, course_analysis=course_analysis, start_finish=appstate.START_FINISH, events=events, boats=boats, entry_boats=entry_boats, fleets=fleets,
        all_active_count=all_active_count, finished_count=finished_count, racing_count=racing_count, race_finished=race_finished,
        polar_files=polar_files, selected_polar=polar_path.name, weather_status=weather_status, latest_wind=latest_wind,
        course_chart={**course_chart_config(), **track.race_chart_line(race)},
        config=hardware_config(), course_announcement=course_announcement_text(race, course),
        is_custom_course=bool(custom_course_from_race(race)), course_set=bool(row_get(race, "course_set", 1)),
        marks_data=track.race_marks(race), mark_names=selectable_mark_names(),
        finish_lines=track.finish_lines(), race_finish_line_key=track.race_finish_line_key(race),
        race_finish_line=track.finish_line_by_key(track.race_finish_line_key(race)),
        series_list=series_list, current_series=current_series,
        video_status=video_runtime_status(), video_clips=video_maps["clips"], start_video_clip=video_maps["start_clip"],
        finish_video_by_entry=video_maps["by_entry"], video_clip_link_text=video_clip_link_text, now_ts=int(time.time()),
        race_console=race_console_config(),
        shorten_options=shorten_options, course_shortened=course_shortened,
        # Replaying this race in the bar. The running time is worked out here
        # rather than in the template because it is the same arithmetic the
        # display does: six times life except during the clips.
        bar_replay_race_id=barreplay.current_replay().get("race_id"),
        replay_possible=_replay_plan is not None,
        replay_minutes=(round(((max(0.0, (_replay_plan["to_ts"] - _replay_plan["from_ts"])
                                    - sum(c["seconds"] for c in _replay_plan["clips"]))
                               / _replay_plan["speed"])
                              + sum(c["seconds"] for c in _replay_plan["clips"])) / 60)
                        if _replay_plan else 0),
        postponed_flag=postponement_flag(race), postponed_at=row_get(race, 'postponed_at', ''),
        # AP postpones races that have not started. The service layer refuses
        # after the first gun; the page should not offer it either, rather than
        # taking the press and answering with an error.
        race_has_started=bool(race_first_start_dt(race)
                              and _app.datetime.now() >= race_first_start_dt(race)),
        postponement_ends_at=row_get(race, 'postponement_ends_at', ''),
        # The default in the Lower AP box: the next whole minute, so the
        # commonest case is one press and every derived time is round.
        next_whole_minute=next_whole_minute(at_least_seconds=LOWER_AP_MIN_LEAD_S),
        shorten_mark=row_get(race, "shortened_at_mark", "") or "",
        shorten_time=row_get(race, "shortened_at_time", "") or "",
        entry_class_labels=entry_class_labels_map(effective_class_config, entries),
        class_config=effective_class_config, start_plan=effective_start_plan, start_schedule=race_start_schedule(race),
        signal_panel_schedule=signal_panel_schedule(race),
        first_warning_time=str(row_get(race, "start_time", "") or ""), first_start_time=race_first_start_time(race),
        signal_plan_rows=start_signal_plan_rows(race),
        class_config_text=class_config_text(effective_class_config), start_plan_text=start_plan_text(effective_start_plan),
        start_plan_grid=race_start_editor_grid,
        race_has_start_override=bool(race_start_override.get("starts")),
        race_delete_summary=delete_summary,
        gps_tracking_enabled=track_config()["enabled"],
        gps_finish_enabled=bool(row_get(race, "gps_finish_enabled", 0)),
        gps_auto_confirm=bool(row_get(race, "gps_auto_confirm", 0)),
        gps_pending_proposals=pending_proposals_for_race(race_id),
        trackers=list_trackers(),
        class_config_help=CLASS_CONFIG_HELP, start_plan_help=START_PLAN_HELP
    )


@app.route("/race/<int:race_id>/update", methods=["POST"])
@app.route("/admin/race/<int:race_id>/update", methods=["POST"])
def update_race(race_id: int):
    """Save Course & start edits without accidentally clearing made-up courses."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    if is_pursuit_race(race):
        return _update_pursuit_race(race)
    back = url_for("race_detail", race_id=race_id) + "#tab-course"
    series_id = request.form.get("series_id", type=int) or None
    # The start plan is the one genuinely form-shaped part: the grid arrives as
    # a spread of named inputs. Parse it here, hand core.raceadmin the plan.
    start_plan: Optional[Dict[str, Any]] = None
    if not (series_id and request.form.get("use_series_start_plan") == "1"):
        with get_db() as db:
            selected_series = db.execute("SELECT * FROM race_series WHERE id = ?", (series_id,)).fetchone() if series_id else None
        start_class_cfg = series_class_config(selected_series) if selected_series else race_class_config(race)
        if request.form.get("start_plan_mode") == "grid":
            start_plan = start_plan_from_grid_form(request.form, start_class_cfg)
        else:
            start_plan = parse_start_plan_text(request.form.get("start_plan_text", ""))

    # The course picker offers "Course not set" until somebody picks one, so this
    # can arrive empty. Empty means "I saved the name and the time, I have not
    # chosen a course": keep the fallback number the race already carries, and do
    # not let the save count as choosing. Refusing the save instead would make
    # setting a start time impossible before the course was known, and defaulting
    # to the fallback is exactly the confusion this is fixing.
    course_choice = request.form.get("course_no", type=int)
    settings = RaceSettings(
        name=request.form.get("name", ""),
        # No default: the Course & start tab no longer posts this, and None
        # means "leave the fleet label alone" rather than "set it to empty".
        class_name=request.form.get("class_name"),
        start_time=request.form.get("start_time", ""),
        series_id=series_id,
        notes=request.form.get("notes", ""),
        polar_file=request.form.get("polar_file", ""),
        finish_line_key=request.form.get("finish_line_key", ""),
        course_no=course_choice or int(race["course_no"] or 0) or None,
        choosing_course=bool(course_choice),
        start_plan=start_plan,
    )
    try:
        with get_db() as db:
            result = update_race_settings(db, race, settings, actor=current_actor())
    except RaceValidationError as exc:
        flash(exc.message, "error")
        return redirect(back)
    if result.warnings:
        flash("Race saved, but start plan has warnings: " + "; ".join(result.warnings), "error")
    else:
        flash("Race course/first-warning details updated and start-sequence automation resynchronised.", "success")
    return redirect(back)


@app.route("/race/<int:race_id>/delete", methods=["POST"])
@app.route("/admin/race/<int:race_id>/delete", methods=["POST"])
def delete_race(race_id: int):
    """Delete a race sheet and all attached race data after confirmation."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("races_page"))
    if request.form.get("confirm_delete") != "1":
        flash("Tick the confirmation box before deleting the race.", "error")
        return redirect(url_for("race_detail", race_id=race_id) + "#tab-remove")
    series_id = row_get(race, "series_id")
    series_name = row_get(race_series_row(race), "name", "")
    race_name = race["name"]
    counts = delete_race_and_related_data(race_id)
    audit("race deleted", f"#{race_id} '{race_name}'")
    parts = [f"{counts['entries']} entr{'y' if counts['entries'] == 1 else 'ies'}", f"{counts['events']} log event(s)", f"{counts['video_clips']} video clip record(s)"]
    if counts.get("video_files"):
        parts.append(f"{counts['video_files']} video file(s)")
    if series_name:
        parts.append(f"removed from series {series_name}")
    flash(f"Deleted race {race_name}: " + ", ".join(parts) + ".", "success")
    if series_id and get_series(int(series_id)):
        return redirect(url_for("series_detail", series_id=int(series_id)))
    return redirect(url_for("races_page"))


@app.route("/race/<int:race_id>/results.csv")
@app.route("/admin/race/<int:race_id>/results.csv")
def results_csv(race_id: int):
    """Export one race's results as a Sailwave import CSV.

    ``?rating=irc`` (the default) or ``?rating=ytc``: one file per rating system,
    because a Sailwave series is scored under one system while this app produces
    both from the same finish times. See core/sailwave.py for the mapping.

    The race is numbered by its position in its series, so importing week by week
    through a season lands each race in its own Sailwave race rather than
    overwriting race 1 every time.
    """
    race = get_race(race_id)
    if not race:
        return Response("Race not found", status=404)
    rating_system = normalise_sailwave_rating_system(request.args.get("rating"))
    entries = get_entries(race_id)
    dual_results = compute_dual_results(race, entries)
    boats_by_id = get_boats_by_id([row_get(e, "boat_id") for e in entries])
    series = race_series_row(race)
    race_no = race_number_in_series(race, get_series_races(int(series["id"])) if series else None)
    body = write_sailwave_csv(sailwave_rows_for_race(
        race, dual_results, rating_system, race_no=race_no, boats_by_id=boats_by_id))
    # Named the way the series exports are: what it holds and when it was taken.
    # "race_9_irc_sailwave.csv" identifies the row, not the race.
    #
    # The series is prefixed only when it adds something. Half the club's races
    # are called R1, which on its own names nothing -- and the other half are
    # called "Welsh IRCs Cruisers - Race 2" inside a series called "Welsh IRCs
    # YTC Cruisers", where prefixing puts the club's name in twice. Sharing a
    # real word is the test: an exact-substring check said those two names
    # differ, which is true and beside the point. Letters only, three or more,
    # so a shared "2026" does not count as having said it already.
    race_name = str(race["name"] or "")
    series_name = str(series["name"] or "") if series else ""
    def _words(text):
        return set(re.findall(r"[a-z]{3,}", text.lower()))
    label = race_name
    if series_name and not (_words(series_name) & _words(race_name)):
        label = f"{series_name} {race_name}"
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": export_attachment(label, f"{rating_system}-sailwave", "csv")})
