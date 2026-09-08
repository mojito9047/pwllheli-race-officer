"""The race and series CSV downloads are Sailwave import files.

The club scores here and keeps its published history in Sailwave, so these
downloads exist to be imported rather than read. Sailwave's importer wants one
flat table: a single header row of field names it recognises, then one row per
competitor per race, with races told apart by ``RaceNo``.

Before this they were stacked human-readable tables — a title row and a header
row per rating band, blank separator rows between them, bracketed discards — which
Sailwave cannot parse at all. The readable version of all that is the HTML publish.

The two things most worth pinning:

* **Every column name is one Sailwave recognises.** A name it does not know forces
  the user through the mapping tab of the import wizard on every import, which is
  the whole thing this format exists to avoid.
* **IRC and YTC are separate files.** A Sailwave series is scored under one system
  and a competitor carries one ``Rating``, but this app produces both from the same
  finish times. Mixing them would silently rescore a fleet.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pytest

import app as ro
from core import sailwave

# Every field name Sailwave documents, from https://www.sailwave.com/field-names
# and the race-results import page. Copied here so a column added to the export
# without checking it against the documentation fails.
SAILWAVE_KNOWN_FIELDS = {
    # competitor
    "Total", "Nett", "Boat", "Rank", "SailNo", "AltSailNo", "Class", "Club", "Fleet",
    "Division", "Nat", "Tally", "Flight", "Notes", "Exclude", "Alias", "Rating",
    "NewRating", "WindRats", "CarriedFwd", "Penalties", "Seeding", "Team", "Sponsor",
    "Formula", "Squad", "District", "Area", "Region", "Status", "Paid", "Fee", "Group",
    "BowNumber", "Medalist", "PrivateNotes", "Rig", "Sailmaker", "Platform", "Foils",
    "HelmName", "HelmId", "CrewName", "CrewId", "SkipperName", "OwnerName",
    # race / result
    "RaceNo", "Elapsed", "Start", "Finish", "Laps", "Code", "Place", "RaceRating",
    "RaceName", "PublishedRaceDate", "PublishedRaceTime",
}


def seed_scored_race(name="Saturday Points", series_id=None, minutes_ago=90):
    """A finished race with a finisher, a slower finisher, a DNF and a retirement."""
    warning = datetime.now() - timedelta(minutes=minutes_ago)
    start = warning + timedelta(minutes=5)
    now_iso = datetime.now().isoformat(timespec="seconds")
    boats = [
        ("Kite", "GBR 1234", 1.021, 1015, "FINISHED", 42),
        ("Halcyon", "GBR 5678", 0.987, 1042, "FINISHED", 51),
        ("Petrel", "GBR 9012", 1.005, 1000, "DNF", None),
        ("Osprey", "GBR 3456", 0.950, 1080, "RET", None),
    ]
    with ro.get_db() as db:
        race_id = int(db.execute(
            "INSERT INTO races (name, course_no, start_time, rating_rule, series_id, created_at)"
            " VALUES (?, 1, ?, 'DUAL', ?, ?)",
            (name, warning.isoformat(timespec="seconds"), series_id, now_iso)).lastrowid)
        for boat_name, sail, irc, ytc, status, minutes in boats:
            boat_id = int(db.execute(
                "INSERT INTO boats (boat_name, sail_no, owner, club, irc_rating, ytc_rating,"
                " status, created_at, updated_at) VALUES (?, ?, ?, 'Pwllheli SC', ?, ?, 'ACTIVE', ?, ?)",
                (boat_name, sail, f"{boat_name} Owner", irc, ytc, now_iso, now_iso)).lastrowid)
            finish = (start + timedelta(minutes=minutes)).isoformat(timespec="seconds") if minutes else None
            db.execute(
                "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, rating, status, finish_time)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (race_id, boat_id, boat_name, sail, irc, status, finish))
        db.commit()
    return race_id


def read_csv(body):
    return list(csv.DictReader(io.StringIO(body)))


class TestItIsAFileSailwaveCanRead:
    def test_every_column_is_a_field_sailwave_knows(self):
        """An unknown name means the mapping tab of the import wizard, every time."""
        unknown = set(sailwave.SAILWAVE_COLUMNS) - SAILWAVE_KNOWN_FIELDS
        assert not unknown, f"not Sailwave field names: {sorted(unknown)}"

    def test_it_is_one_header_row_then_one_row_per_result(self, logged_in_client):
        race_id = seed_scored_race()
        body = logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True)
        lines = [line for line in body.splitlines() if line.strip()]
        assert lines[0].split(",")[0] == "RaceNo"
        assert len(lines) == 5, "expected a header and one row for each of the four boats"

    def test_there_are_no_blank_or_title_rows(self, logged_in_client):
        """What made the old export unreadable to Sailwave."""
        race_id = seed_scored_race()
        body = logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True)
        assert "\r\n\r\n" not in body.strip()
        rows = read_csv(body)
        assert all(row["Boat"] for row in rows), "every row must be a competitor result"

    def test_a_finisher_carries_its_times(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        kite = next(row for row in rows if row["Boat"] == "Kite")
        assert kite["Start"] and kite["Finish"]
        assert kite["Elapsed"] == "0:42:00"
        assert kite["Code"] == "", "a finisher is described by its time, not a code"

    def test_times_are_clock_times_not_full_timestamps(self, logged_in_client):
        """Sailwave's examples are times of day; a date in the field is not parsed."""
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        kite = next(row for row in rows if row["Boat"] == "Kite")
        assert len(kite["Finish"].split(":")) == 3
        assert "-" not in kite["Finish"], "that would be a date"
        assert kite["PublishedRaceDate"].count("-") == 2, "the date belongs in its own field"

    def test_a_non_finisher_carries_a_code_and_no_finish_time(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        by_boat = {row["Boat"]: row for row in rows}
        assert by_boat["Petrel"]["Code"] == "DNF"
        assert by_boat["Osprey"]["Code"] == "RET"
        for boat in ("Petrel", "Osprey"):
            assert by_boat[boat]["Finish"] == ""
            assert by_boat[boat]["Elapsed"] == ""

    def test_the_place_is_not_exported(self):
        """Sailwave scores from elapsed time and rating. Sending a finishing order
        as well would import this app's arithmetic and then ask Sailwave to redo
        it, with nothing to say which wins if they disagree."""
        assert "Place" not in sailwave.SAILWAVE_COLUMNS
        assert "Rank" not in sailwave.SAILWAVE_COLUMNS

    def test_the_competitor_can_be_identified(self, logged_in_client):
        """Sailwave matches an existing competitor on the identifying fields given."""
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        kite = next(row for row in rows if row["Boat"] == "Kite")
        assert kite["SailNo"] == "GBR 1234"
        assert kite["HelmName"] == "Kite Owner"
        assert kite["Club"] == "Pwllheli SC"


class TestIrcAndYtcAreSeparateFiles:
    def test_irc_is_the_default(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        assert rows[0]["Fleet"] == "IRC"

    def test_the_irc_rating_is_the_tcc_coefficient(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating=irc").get_data(as_text=True))
        kite = next(row for row in rows if row["Boat"] == "Kite")
        assert kite["Rating"] == "1.021"

    def test_the_ytc_rating_is_a_whole_number(self, logged_in_client):
        """Formatting these the same way would silently rescore the fleet."""
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating=ytc").get_data(as_text=True))
        kite = next(row for row in rows if row["Boat"] == "Kite")
        assert kite["Rating"] == "1015"
        assert rows[0]["Fleet"] == "YTC"

    def test_the_two_files_differ(self, logged_in_client):
        race_id = seed_scored_race()
        irc = logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating=irc").get_data(as_text=True)
        ytc = logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating=ytc").get_data(as_text=True)
        assert irc != ytc

    def test_the_filename_says_which(self, logged_in_client):
        """Still the point, and still true: the two files land in one folder and
        have to be told apart. The shape changed in v0.279 -- these were named
        `race_9_irc_sailwave.csv`, after a row id, and are now named after the
        race with the date and time (tests/test_series_exports_and_navigation.py
        holds that). Only the separator and the prefix moved."""
        race_id = seed_scored_race()
        for system in ("irc", "ytc"):
            resp = logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating={system}")
            assert f"-{system}-sailwave-" in resp.headers["Content-Disposition"]

    def test_and_the_two_filenames_differ(self, logged_in_client):
        """The rating is the only thing telling them apart, so it has to survive
        whatever else goes into the name."""
        race_id = seed_scored_race()
        names = {
            logged_in_client.get(
                f"/admin/race/{race_id}/results.csv?rating={s}").headers["Content-Disposition"]
            for s in ("irc", "ytc")}
        assert len(names) == 2

    @pytest.mark.parametrize("value", ["", "nonsense", "IRC ", None])
    def test_a_bad_rating_parameter_falls_back_to_irc(self, value):
        assert sailwave.normalise_sailwave_rating_system(value) == "irc"


class TestRaceNumbering:
    def test_a_race_in_a_series_is_numbered_by_its_place_in_it(self, logged_in_client):
        """So importing week by week lands each race in its own Sailwave race
        instead of overwriting race 1 every time."""
        with ro.get_db() as db:
            now = datetime.now().isoformat(timespec="seconds")
            series_id = int(db.execute(
                "INSERT INTO race_series (name, created_at, updated_at) VALUES ('Summer', ?, ?)",
                (now, now)).lastrowid)
            db.commit()
        seed_scored_race("Race One", series_id=series_id, minutes_ago=300)
        second = seed_scored_race("Race Two", series_id=series_id, minutes_ago=120)

        rows = read_csv(logged_in_client.get(f"/admin/race/{second}/results.csv").get_data(as_text=True))

        assert {row["RaceNo"] for row in rows} == {"2"}
        assert {row["RaceName"] for row in rows} == {"Race Two"}

    def test_a_race_with_no_series_is_race_one(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        assert {row["RaceNo"] for row in rows} == {"1"}


class TestTheSeriesExport:
    def _series_with_two_races(self):
        with ro.get_db() as db:
            now = datetime.now().isoformat(timespec="seconds")
            series_id = int(db.execute(
                "INSERT INTO race_series (name, created_at, updated_at) VALUES ('Summer', ?, ?)",
                (now, now)).lastrowid)
            db.commit()
        seed_scored_race("Race One", series_id=series_id, minutes_ago=300)
        seed_scored_race("Race Two", series_id=series_id, minutes_ago=120)
        return series_id

    def test_every_race_is_in_one_file_numbered_in_order(self, logged_in_client):
        series_id = self._series_with_two_races()
        rows = read_csv(logged_in_client.get(f"/admin/series/{series_id}/results.csv").get_data(as_text=True))
        assert sorted({row["RaceNo"] for row in rows}) == ["1", "2"]
        assert len(rows) == 8, "four boats in each of two races"

    def test_each_boat_appears_once_per_race(self, logged_in_client):
        series_id = self._series_with_two_races()
        rows = read_csv(logged_in_client.get(f"/admin/series/{series_id}/results.csv").get_data(as_text=True))
        seen = [(row["RaceNo"], row["Boat"]) for row in rows]
        assert len(seen) == len(set(seen))

    def test_the_standings_are_not_exported(self, logged_in_client):
        """Sailwave works out its own totals and discards from the race results,
        which is the reason for importing them."""
        series_id = self._series_with_two_races()
        body = logged_in_client.get(f"/admin/series/{series_id}/results.csv").get_data(as_text=True)
        assert "Total" not in body.splitlines()[0]
        assert "[" not in body, "bracketed discards were a report thing"

    def test_it_is_the_same_header_as_the_race_export(self, logged_in_client):
        series_id = self._series_with_two_races()
        race_header = logged_in_client.get("/admin/series/%d/results.csv" % series_id).get_data(as_text=True).splitlines()[0]
        assert race_header.split(",") == list(sailwave.SAILWAVE_COLUMNS)


class TestTheFormattingHelpers:
    def test_elapsed_is_hours_minutes_seconds(self):
        assert sailwave.sailwave_elapsed(3723) == "1:02:03"
        assert sailwave.sailwave_elapsed(None) == ""

    def test_an_unknown_status_is_passed_through_rather_than_dropped(self):
        """A blank would score the boat as a finisher with no time, which is worse
        than a code Sailwave flags as unrecognised."""
        assert sailwave.sailwave_code("SOMETHING_NEW") == "SOMETHING_NEW"

    def test_a_finisher_has_no_code(self):
        assert sailwave.sailwave_code("FINISHED") == ""

    def test_retired_spellings_both_map_to_ret(self):
        assert sailwave.sailwave_code("RET") == "RET"
        assert sailwave.sailwave_code("RTD") == "RET"

    def test_a_missing_rating_is_blank_not_zero(self):
        """Zero would be a rating; blank is the absence of one."""
        assert sailwave.sailwave_rating(None, "irc") == ""

    def test_the_csv_is_standard_with_crlf_line_endings(self):
        body = sailwave.write_sailwave_csv([{"Boat": "Kite", "RaceNo": "1"}])
        assert body.endswith("\r\n")
        assert body.splitlines()[0].split(",") == list(sailwave.SAILWAVE_COLUMNS)

    def test_a_field_with_a_comma_is_quoted(self):
        body = sailwave.write_sailwave_csv([{"Boat": "Kite, of Pwllheli"}])
        assert '"Kite, of Pwllheli"' in body


class TestTheClassColumnIsABoatClass:
    """`Class` in Sailwave is the boat class; `Fleet` carries the rating system.

    The app labels an unbanded or pursuit result with the rating system itself, and
    letting that through put "IRC" in both columns — useless as a class, and it
    would overwrite a real one on import.
    """

    def test_the_rating_system_never_lands_in_class(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        for row in rows:
            assert row["Class"].upper() not in ("IRC", "YTC"), row

    def test_the_boat_class_is_used_when_there_is_one(self, logged_in_client):
        race_id = seed_scored_race()
        with ro.get_db() as db:
            db.execute("UPDATE entries SET class_name = 'J/109' WHERE race_id = ?", (race_id,))
            db.commit()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv").get_data(as_text=True))
        assert {row["Class"] for row in rows} == {"J/109"}

    def test_the_fleet_still_says_which_rating_system(self, logged_in_client):
        race_id = seed_scored_race()
        rows = read_csv(logged_in_client.get(f"/admin/race/{race_id}/results.csv?rating=ytc").get_data(as_text=True))
        assert {row["Fleet"] for row in rows} == {"YTC"}

    def test_helper_discards_a_rating_system_candidate(self):
        assert sailwave.sailwave_class("IRC", None, None, "irc") == ""
        assert sailwave.sailwave_class("Fast", None, None, "irc") == "Fast"
