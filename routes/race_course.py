"""Race course selection and manual course builder routes.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and helpers are
read off the running app module (routes.app_module) so it works whether app.py
was started with ``python app.py`` (module ``__main__``) or imported.
"""
from core import track
from core.raceadmin import clear_custom_course, set_custom_course
from routes import app_module

_app = app_module()
app = _app.app
audit = _app.audit
analyse_course_with_wind = _app.analyse_course_with_wind
appstate = _app.appstate
course_chart_config = _app.course_chart_config
course_for_race = _app.course_for_race
current_actor = _app.current_actor
custom_course_from_race = _app.custom_course_from_race
datetime = _app.datetime
flash = _app.flash
get_db = _app.get_db
get_race = _app.get_race
json = _app.json
latest_weather_sample = _app.latest_weather_sample
list_polar_files = _app.list_polar_files
race_saved_polar = _app.race_saved_polar
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
resolve_polar_path = _app.resolve_polar_path
selectable_mark_names = _app.selectable_mark_names
url_for = _app.url_for
validate_course_sequence_json = _app.validate_course_sequence_json
weather_runtime_status = _app.weather_runtime_status


@app.route("/race/<int:race_id>/course/select", methods=["POST"])
@app.route("/admin/race/<int:race_id>/course/select", methods=["POST"])
def select_race_course(race_id: int):
    """Update the fixed course and/or polar selected for a race."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    course_no = request.form.get("course_no", type=int)
    polar_file = request.form.get("polar_file", "").strip()
    if course_no not in appstate.COURSE_BY_NO:
        flash("Please select a valid course number.", "error")
        return redirect(url_for("recommend", race_id=race_id))
    with get_db() as db:
        db.execute("UPDATE races SET course_no = ?, polar_file = COALESCE(NULLIF(?, ''), polar_file), custom_course_json = NULL, course_set = 1 WHERE id = ?", (course_no, polar_file, race_id))
        db.commit()
    audit("race course changed", f"#{race_id} to course {course_no}")
    flash(f"Course changed to {course_no}.", "success")
    if polar_file:
        return redirect(url_for("race_detail", race_id=race_id, polar_file=polar_file) + "#tab-course")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-course")


@app.route("/race/<int:race_id>/course/builder", methods=["GET", "POST"])
@app.route("/admin/race/<int:race_id>/course/builder", methods=["GET", "POST"])
def manual_course_builder(race_id: int):
    """Render and process the made-up-course builder."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    polar_files = list_polar_files()
    selected_polar = request.values.get("polar_file") or race_saved_polar(race, polar_files)
    polar_path = resolve_polar_path(selected_polar)
    if request.method == "POST":
        try:
            sequence = validate_course_sequence_json(request.form.get("course_marks_json", "[]"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("manual_course_builder", race_id=race_id, polar_file=polar_path.name))
        polar_file = request.form.get("polar_file", "").strip() or polar_path.name
        with get_db() as db:
            set_custom_course(db, race, sequence, actor=current_actor(), polar_file=polar_file)
        flash("Manual course saved to race.", "success")
        return redirect(url_for("race_detail", race_id=race_id, polar_file=polar_file) + "#tab-course")

    current_course = custom_course_from_race(race) or course_for_race(race)
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    twd = latest_wind.get("twd") if latest_wind else None
    tws = latest_wind.get("tws") if latest_wind else None
    analysis = analyse_course_with_wind(current_course, twd, tws, polar_path,
                                        marks=track.race_marks(race))
    return render_template(
        "course_builder.html",
        race=race,
        course=current_course,
        initial_marks=current_course.get("marks", []),
        mark_names=selectable_mark_names(),
        marks_data=track.race_marks(race),
        course_chart={**course_chart_config(), **track.race_chart_line(race)},
        polar_files=polar_files,
        selected_polar=polar_path.name,
        latest_wind=latest_wind,
        weather_status=weather_status,
        course_analysis=analysis,
        legs=analysis["legs_analysis"],
    )


@app.route("/race/<int:race_id>/course/clear_manual", methods=["POST"])
@app.route("/admin/race/<int:race_id>/course/clear_manual", methods=["POST"])
def clear_manual_course(race_id: int):
    """Clear a race's made-up course and return to fixed-course mode."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    with get_db() as db:
        clear_custom_course(db, race, actor=current_actor())
    flash("Manual course cleared; the fixed course number is in use again.", "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-course")
