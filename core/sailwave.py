"""Sailwave-importable CSV export of race and series results.

The club scores in this app and keeps its published history in Sailwave, so the
CSV downloads exist to be imported there rather than read. Sailwave's importer
wants one flat table: a single header row of recognised field names, then **one
row per competitor per race**, with different races distinguished by ``RaceNo``.
(https://www.sailwave.com/how-do-i-import-a-list-of-race-results and
https://www.sailwave.com/field-names)

That is a different shape from what these exports used to be — stacked
human-readable tables, one per rating band, with title rows, blank separator rows
and bracketed discards. Readable, and impossible for Sailwave to parse. The
readable version of all of that is still the HTML publish.

Two things about the mapping are worth knowing before changing it:

* **One file per rating system.** A Sailwave series is scored under one system,
  but this app produces IRC *and* YTC results for the same race from the same
  finish times, and a competitor carries one ``Rating``. So IRC and YTC are
  separate downloads rather than one file that would need editing either way.
* **Times, not places.** ``Place`` is deliberately not exported. Sailwave scores
  from elapsed time and rating, and handing it a finishing order as well would
  mean importing this app's arithmetic and then asking Sailwave to redo it — with
  no way to tell which won if they ever disagreed. ``Elapsed`` *is* sent alongside
  ``Start`` and ``Finish`` because a race here can have per-class start times, so
  the elapsed time is the app's own and not always end-minus-start of the fleet.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.db import row_get
from core.timeutils import parse_dt, seconds_display

# The header, in this order. Every name here is one Sailwave recognises, so an
# import needs no column mapping. Anything it does not recognise would force the
# user through the mapping tab of the import wizard, which is the thing this
# ordering exists to avoid.
SAILWAVE_COLUMNS: Sequence[str] = (
    "RaceNo",
    "RaceName",
    "PublishedRaceDate",
    "PublishedRaceTime",
    "HelmName",
    "Boat",
    "SailNo",
    "Class",
    "Fleet",
    "Club",
    "Rating",
    "Start",
    "Finish",
    "Elapsed",
    "Code",
)

# App entry statuses to Sailwave scoring codes. The short codes are already the
# same words, which is why this is mostly identity — but it is written out rather
# than assumed, so a new status added to the app fails visibly here instead of
# being exported as something Sailwave silently ignores.
STATUS_TO_CODE: Dict[str, str] = {
    "FINISHED": "",          # a finisher is described by its time, not by a code
    "RACING": "",            # still on the water; exported as an unfinished row
    "DNF": "DNF",
    "DNS": "DNS",
    "DNC": "DNC",
    "RET": "RET",
    "RTD": "RET",            # older entries used RTD for retired
    "OCS": "OCS",
    "DSQ": "DSQ",
    "DNE": "DNE",
    "NSC": "NSC",
}


RATING_SYSTEMS: Sequence[str] = ("irc", "ytc")


def normalise_sailwave_rating_system(value: Any) -> str:
    """Return a supported rating system, defaulting to IRC."""
    text = str(value or "").strip().lower()
    return text if text in RATING_SYSTEMS else "irc"


def sailwave_code(status: Any) -> str:
    """Return the Sailwave scoring code for an app entry status."""
    text = str(status or "").strip().upper()
    if not text:
        return ""
    # An unknown status is passed through rather than dropped: Sailwave will show
    # it as an unrecognised code, which is visible, where a blank would quietly
    # score the boat as a finisher with no time.
    return STATUS_TO_CODE.get(text, text)


def sailwave_clock(value: Any) -> str:
    """Format a time of day as Sailwave expects it (24-hour, with seconds)."""
    moment = value if isinstance(value, datetime) else parse_dt(value)
    return moment.strftime("%H:%M:%S") if moment else ""


def sailwave_date(value: Any) -> str:
    """Format a race date. ISO, because it cannot be read the American way."""
    moment = value if isinstance(value, datetime) else parse_dt(value)
    return moment.strftime("%Y-%m-%d") if moment else ""


def sailwave_elapsed(seconds: Optional[float]) -> str:
    """Format an elapsed time as h:mm:ss, blank when the boat did not finish."""
    return seconds_display(seconds) if seconds is not None else ""


def sailwave_rating(rating: Optional[float], rating_system: str) -> str:
    """Format a rating the way its own system is written.

    IRC TCC is a coefficient near 1 and carries three decimals; a YTC number is a
    whole number (1000-ish). Getting this wrong does not fail the import — it
    silently rescores the fleet — so the two are formatted apart.
    """
    if rating is None:
        return ""
    value = float(rating)
    if str(rating_system).lower() == "irc":
        return f"{value:.3f}"
    return f"{value:.0f}" if value >= 10 else f"{value:.4f}"


def sailwave_class(candidate: Any, entry: Any, boat: Any, rating_system: str) -> str:
    """Return the boat class for the Class column.

    ``Class`` in Sailwave is the boat's class — Topper, J/109 — and ``Fleet``
    carries the rating system. The app's result tables label an unbanded or pursuit
    result with the rating system itself, and letting that through put "IRC" in
    both columns: no use as a class, and it would overwrite a real one on import.
    So a candidate that is only the rating system's name is discarded.
    """
    banned = {"irc", "ytc", "irc_tcc", ""}
    text = str(candidate or "").strip()
    if text.lower() not in banned:
        return text
    for fallback in (row_get(entry, "class_name"), row_get(boat, "class_name")):
        text = str(fallback or "").strip()
        if text and text.lower() not in banned:
            return text
    return ""


def sailwave_row(
    race: Any,
    result_row: Dict[str, Any],
    rating_system: str,
    race_no: int,
    result_class: str = "",
    boat: Any = None,
) -> Dict[str, str]:
    """Build one Sailwave result row for one competitor in one race."""
    entry = result_row.get("entry")
    # The result rows already carry the boat database row; the lookup is only a
    # fallback for callers that build rows without one.
    boat = result_row.get("boat") if result_row.get("boat") is not None else boat
    status = row_get(entry, "status")
    finished = str(status or "").strip().upper() in ("FINISHED", "RACING")
    return {
        "RaceNo": str(int(race_no)),
        "RaceName": str(row_get(race, "name") or "").strip(),
        "PublishedRaceDate": sailwave_date(row_get(race, "start_time")),
        "PublishedRaceTime": sailwave_clock(result_row.get("start_time")),
        # The app stores an owner against a boat, which is the nearest thing it has
        # to a helm. Left blank rather than guessed at when there is none.
        "HelmName": str(row_get(boat, "owner") or "").strip(),
        "Boat": str(row_get(entry, "boat_name") or "").strip(),
        "SailNo": str(row_get(entry, "sail_no") or "").strip(),
        # The rating-band class the race was scored in, or the boat's own class.
        "Class": sailwave_class(result_class, entry, boat, rating_system),
        "Fleet": str(rating_system).upper(),
        "Club": str(row_get(boat, "club") or "").strip(),
        "Rating": sailwave_rating(result_row.get("rating"), rating_system),
        "Start": sailwave_clock(result_row.get("start_time")),
        "Finish": sailwave_clock(row_get(entry, "finish_time")) if finished else "",
        "Elapsed": sailwave_elapsed(result_row.get("elapsed_seconds")) if finished else "",
        "Code": sailwave_code(status),
    }


def race_number_in_series(race: Any, series_races: Optional[Iterable[Any]]) -> int:
    """Return a race's 1-based position in its series, or 1 when it has none.

    Numbering a single-race export by its place in the series is what makes
    importing race by race through a season work: each import lands in its own
    Sailwave race column instead of overwriting race 1 every week.
    """
    if not series_races:
        return 1
    race_id = row_get(race, "id")
    for index, candidate in enumerate(series_races, start=1):
        if row_get(candidate, "id") == race_id:
            return index
    return 1


def sailwave_rows_for_race(
    race: Any,
    dual_results: Dict[str, Any],
    rating_system: str,
    race_no: int = 1,
    boats_by_id: Optional[Dict[Any, Any]] = None,
) -> List[Dict[str, str]]:
    """Build every Sailwave row for one race under one rating system.

    Boats the app excluded from this system's results — no IRC certificate when
    scoring IRC, say — are not exported here, because they are genuinely not part
    of this result and an unrated competitor would score oddly in Sailwave. They
    are listed as excluded on the race page and in the HTML publish.
    """
    boats_by_id = boats_by_id or {}
    group = dual_results.get(rating_system) or {}
    rows: List[Dict[str, str]] = []
    for table in group.get("tables", []):
        for result_row in table.get("rows", []):
            entry = result_row.get("entry")
            rows.append(sailwave_row(
                race,
                result_row,
                rating_system,
                race_no,
                result_class=table.get("class_name") or result_row.get("result_class") or "",
                boat=boats_by_id.get(row_get(entry, "boat_id")),
            ))
    return rows


def write_sailwave_csv(rows: Iterable[Dict[str, str]]) -> str:
    """Render rows as a Sailwave import CSV, header first.

    ``\\r\\n`` line endings and minimal quoting: standard CSV, which is what the
    Sailwave documentation asks for.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(SAILWAVE_COLUMNS),
                            extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in SAILWAVE_COLUMNS})
    return output.getvalue()
