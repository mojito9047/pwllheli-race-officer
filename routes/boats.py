"""Boat database management routes (list, new/edit, delete, IRC/YTC import).

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
Dict = _app.Dict
List = _app.List
Optional = _app.Optional
audit = _app.audit
csv = _app.csv
datetime = _app.datetime
filter_irc_rows = _app.filter_irc_rows
filter_ytc_rows = _app.filter_ytc_rows
flash = _app.flash
boat_by_sail_no = _app.boat_by_sail_no
duplicate_sail_numbers = _app.duplicate_sail_numbers
get_boat = _app.get_boat
get_db = _app.get_db
google_sheet_to_csv_url = _app.google_sheet_to_csv_url
init_db = _app.init_db
listing_config = _app.listing_config
parse_float = _app.parse_float
rating_lookup_results = _app.rating_lookup_results
rating_listings_are_warm = _app.rating_listings_are_warm
read_irc_listing = _app.read_irc_listing
read_ytc_listing = _app.read_ytc_listing
warm_rating_listings = _app.warm_rating_listings
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
search_boats = _app.search_boats
upsert_boat_from_irc_row = _app.upsert_boat_from_irc_row
upsert_boat_from_ytc_row = _app.upsert_boat_from_ytc_row
url_for = _app.url_for
urllib = _app.urllib


@app.route("/boats")
@app.route("/admin/boats")
def boats_page():
    """Render the boat database page."""
    q = request.args.get("q", "").strip()
    include_inactive = request.args.get("include_inactive") == "1"
    boats = search_boats(q, include_inactive=include_inactive, limit=300)
    return render_template("boats.html", boats=boats, q=q, include_inactive=include_inactive,
                           # Records that predate the check on adding. Flagged so
                           # they can be found, not merged automatically: which
                           # one survives depends on which carries the results.
                           duplicate_sail_numbers=duplicate_sail_numbers())


@app.route("/boats/new", methods=["GET", "POST"])
@app.route("/admin/boats/new", methods=["GET", "POST"])
def boat_new():
    """Render the combined manual/IRC/YTC add-boat page."""
    init_db()
    if request.method == "POST":
        return save_boat_from_form(None)
    settings = listing_config()
    lookup_q = request.args.get("lookup_q", "").strip()
    if not lookup_q:
        # The race officer has to type a name before they can search, and that
        # gap covers the download. Only on the empty form: a search already
        # under way fetches what it needs, joining this one if it is running.
        warm_rating_listings(settings["irc_listing_url"], settings["ytc_listing_url"])
    lookup = rating_lookup_results(lookup_q)
    for err in lookup.get("errors", []):
        flash(f"Could not search {err}", "error")
    return render_template("boat_form.html", boat=None, lookup=lookup, settings=settings)


@app.route("/boats/<int:boat_id>/edit", methods=["GET", "POST"])
@app.route("/admin/boats/<int:boat_id>/edit", methods=["GET", "POST"])
def boat_edit(boat_id: int):
    """Render the edit page for one boat."""
    boat = get_boat(boat_id)
    if not boat:
        flash("Boat not found.", "error")
        return redirect(url_for("boats_page"))
    if request.method == "POST":
        return save_boat_from_form(boat_id)
    settings = listing_config()
    lookup_q = request.args.get("lookup_q", "").strip()
    if not lookup_q:
        # The race officer has to type a name before they can search, and that
        # gap covers the download. Only on the empty form: a search already
        # under way fetches what it needs, joining this one if it is running.
        warm_rating_listings(settings["irc_listing_url"], settings["ytc_listing_url"])
    lookup = rating_lookup_results(lookup_q)
    for err in lookup.get("errors", []):
        flash(f"Could not search {err}", "error")
    return render_template("boat_form.html", boat=boat, lookup=lookup, settings=settings)


def save_boat_from_form(boat_id: Optional[int]):
    """Create or update a boat from submitted form fields."""
    fields = {
        "boat_name": request.form.get("boat_name", "").strip(),
        "sail_no": request.form.get("sail_no", "").strip(),
        "owner": request.form.get("owner", "").strip(),
        "design": request.form.get("design", "").strip(),
        "club": request.form.get("club", "").strip(),
        "irc_rating": parse_float(request.form.get("irc_rating")),
        "irc_non_spinnaker_tcc": parse_float(request.form.get("irc_non_spinnaker_tcc")),
        "irc_cert_no": request.form.get("irc_cert_no", "").strip(),
        "irc_issue_date": request.form.get("irc_issue_date", "").strip(),
        "irc_cert_year": request.form.get("irc_cert_year", "").strip(),
        "ytc_rating": parse_float(request.form.get("ytc_rating")),
        "status": request.form.get("status", "ACTIVE").strip() or "ACTIVE",
        "notes": request.form.get("notes", "").strip(),
    }
    if not fields["boat_name"]:
        flash("Boat name is required.", "error")
        return redirect(request.referrer or url_for("boats_page"))
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        if boat_id is not None:
            # Editing a sail number onto one that is already taken. Refused
            # rather than merged: merging two boats that have both raced is not
            # something to do silently behind a Save button.
            clash = boat_by_sail_no(db, fields["sail_no"], exclude_id=boat_id)
            if clash is not None:
                flash(f"Sail number '{fields['sail_no']}' already belongs to "
                      f"{clash['boat_name']} (#{clash['id']}). Two boats cannot share one, "
                      "because results and GPS tracks are held against the boat.", "error")
                return redirect(url_for("boat_edit", boat_id=boat_id))

        # A boat added with a sail number already on file is that boat, not a new
        # one. A second record for it would split its results into two series
        # competitors and detach it from its own track — and the commonest way to
        # get here is deleting a boat and adding it back, which is exactly when
        # its history matters.
        existing = boat_by_sail_no(db, fields["sail_no"]) if boat_id is None else None
        if existing is not None:
            boat_id = int(existing["id"])
            entries = db.execute("SELECT COUNT(*) AS n FROM entries WHERE boat_id = ?",
                                 (boat_id,)).fetchone()["n"]
            was_inactive = str(existing["status"] or "").upper() != "ACTIVE"
            note = f"'{fields['sail_no']}' is already on file"
            if was_inactive:
                note += ", inactive"
            if entries:
                note += f" with {entries} race entr{'y' if entries == 1 else 'ies'}"
            flash(note + ". That record has been updated rather than a second one added, "
                         "so its results and track stay with it.", "success")
            audit("boat re-linked",
                  f"#{boat_id} '{fields['boat_name']}' {fields['sail_no']} entries={entries}")

        if boat_id is None:
            db.execute(
                """
                INSERT INTO boats (boat_name, sail_no, owner, design, club, irc_rating, irc_non_spinnaker_tcc,
                                   irc_cert_no, irc_issue_date, irc_cert_year, ytc_rating, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fields["boat_name"], fields["sail_no"], fields["owner"], fields["design"], fields["club"],
                 fields["irc_rating"], fields["irc_non_spinnaker_tcc"], fields["irc_cert_no"], fields["irc_issue_date"], fields["irc_cert_year"],
                 fields["ytc_rating"], fields["status"], fields["notes"], now, now),
            )
            flash("Boat added.", "success")
            audit("boat created", f"'{fields['boat_name']}' {fields['sail_no'] or ''}".strip())
        else:
            db.execute(
                """
                UPDATE boats
                SET boat_name=?, sail_no=?, owner=?, design=?, club=?, irc_rating=?, irc_non_spinnaker_tcc=?,
                    irc_cert_no=?, irc_issue_date=?, irc_cert_year=?, ytc_rating=?, status=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (fields["boat_name"], fields["sail_no"], fields["owner"], fields["design"], fields["club"],
                 fields["irc_rating"], fields["irc_non_spinnaker_tcc"], fields["irc_cert_no"], fields["irc_issue_date"], fields["irc_cert_year"],
                 fields["ytc_rating"], fields["status"], fields["notes"], now, boat_id),
            )
            if existing is None:            # a re-link has already said what happened
                flash("Boat updated.", "success")
                audit("boat updated", f"#{boat_id} '{fields['boat_name']}'")
        db.commit()
    return redirect(url_for("boats_page"))


@app.route("/boats/<int:boat_id>/delete", methods=["POST"])
@app.route("/admin/boats/<int:boat_id>/delete", methods=["POST"])
def boat_delete(boat_id: int):
    """Deactivate or delete a boat record."""
    with get_db() as db:
        used = db.execute("SELECT COUNT(*) AS n FROM entries WHERE boat_id = ?", (boat_id,)).fetchone()["n"]
        if used:
            db.execute("UPDATE boats SET status = 'INACTIVE', updated_at = ? WHERE id = ?", (datetime.now().isoformat(timespec="seconds"), boat_id))
            flash("Boat has race entries, so it has been marked inactive instead of deleted.", "success")
        else:
            db.execute("DELETE FROM boats WHERE id = ?", (boat_id,))
            flash("Boat deleted.", "success")
        db.commit()
    audit("boat deleted" if not used else "boat deactivated", f"#{boat_id}")
    return redirect(url_for("boats_page"))


@app.route("/boats/import_irc", methods=["GET", "POST"])
@app.route("/admin/boats/import_irc", methods=["GET", "POST"])
def boats_import_irc():
    """Bulk-import matching IRC listing rows into the boat database."""
    init_db()
    url = request.values.get("url", listing_config()["irc_listing_url"]).strip() or listing_config()["irc_listing_url"]
    q = request.values.get("q", "").strip()
    matches: List[Dict[str, str]] = []
    error = ""
    imported_id: Optional[int] = None
    if request.method == "POST" and request.form.get("action") == "import":
        row = {
            "Boat Name": request.form.get("boat_name", ""),
            "Sail No": request.form.get("sail_no", ""),
            "Cert No": request.form.get("cert_no", ""),
            "Issue Date": request.form.get("issue_date", ""),
            "Cert Year": request.form.get("cert_year", ""),
            "TCC": request.form.get("tcc", ""),
            "Non Spi TCC": request.form.get("non_spi_tcc", ""),
        }
        try:
            imported_id = upsert_boat_from_irc_row(row)
            audit("IRC rating imported",
                  f"'{row['Boat Name']}' {row['Sail No']} TCC={row['TCC']} cert={row['Cert No']}")
            flash(f"Imported/updated {row['Boat Name']}.", "success")
        except Exception as exc:
            flash(f"Could not import IRC row: {exc}", "error")
    if q:
        try:
            matches = filter_irc_rows(read_irc_listing(url), q, limit=75)
        except (urllib.error.URLError, TimeoutError, csv.Error, UnicodeDecodeError, ValueError) as exc:
            error = str(exc)
            flash(f"Could not read IRC listing: {error}", "error")
    return render_template("boats_import_irc.html", url=url, q=q, matches=matches, imported_id=imported_id, default_url=listing_config()["irc_listing_url"])


@app.route("/boats/import_ytc", methods=["GET", "POST"])
@app.route("/admin/boats/import_ytc", methods=["GET", "POST"])
def boats_import_ytc():
    """Bulk-import matching YTC listing rows into the boat database."""
    init_db()
    url = request.values.get("url", listing_config()["ytc_listing_url"]).strip() or listing_config()["ytc_listing_url"]
    q = request.values.get("q", "").strip()
    matches: List[Dict[str, str]] = []
    imported_id: Optional[int] = None
    if request.method == "POST" and request.form.get("action") == "import":
        row = {
            "boat_name": request.form.get("boat_name", ""),
            "sail_no": request.form.get("sail_no", ""),
            "rating": request.form.get("rating", ""),
            "owner": request.form.get("owner", ""),
            "design": request.form.get("design", ""),
            "club": request.form.get("club", ""),
            "issue_date": request.form.get("issue_date", ""),
        }
        try:
            imported_id = upsert_boat_from_ytc_row(row)
            audit("YTC rating imported",
                  f"'{row['boat_name']}' {row['sail_no']} YTC={row.get('ytc_rating', '')}")
            flash(f"Imported/updated YTC rating for {row['boat_name'] or row['sail_no']}.", "success")
        except Exception as exc:
            flash(f"Could not import YTC row: {exc}", "error")
    if q:
        try:
            matches = filter_ytc_rows(read_ytc_listing(url), q, limit=75)
        except (urllib.error.URLError, TimeoutError, csv.Error, UnicodeDecodeError, ValueError, OSError) as exc:
            flash(f"Could not read YTC sheet: {exc}", "error")
    return render_template("boats_import_ytc.html", url=url, q=q, matches=matches, imported_id=imported_id, default_url=listing_config()["ytc_listing_url"], csv_url=google_sheet_to_csv_url(url))
