"""Pure race-scoring and series tie-break primitives.

Extracted from app.py. These are the framework-independent scoring rules --
corrected-time calculation, whole-second rounding for ties, RRS A7 low-point
race ranking, RRS A2.1 discard selection and RRS A8 series tie-break keys --
plus the discard-profile parser. They operate on plain dicts/lists and take all
data as arguments, so they can be unit-tested in isolation. The DB-heavy result
and series builders that call these stay in app.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.timeutils import normalise_key

DEFAULT_DISCARD_PROFILE = "0,0,1,1,1,1,2,2,2"
DEFAULT_MIN_RACES_TO_CONSTITUTE = 3
NON_EXCLUDABLE_STATUS_CODES = {"DNE", "DGM"}


def parse_discard_profile(text: Any) -> Tuple[List[int], List[str]]:
    """Parse a comma-separated discard profile into non-negative integers.

    Position 1 is used when one race has been completed, position 2 when two
    races have been completed, and so on.  If more races are completed than the
    profile contains, the final value is reused.
    """
    raw = str(text if text is not None else "").strip()
    if not raw:
        raw = DEFAULT_DISCARD_PROFILE
    values: List[int] = []
    errors: List[str] = []
    for idx, part in enumerate(raw.split(","), start=1):
        item = part.strip()
        if not item:
            errors.append(f"Discard profile item {idx} is blank.")
            continue
        try:
            value = int(item)
        except ValueError:
            errors.append(f"Discard profile item {idx} must be a whole number.")
            continue
        if value < 0:
            errors.append(f"Discard profile item {idx} cannot be negative.")
            continue
        values.append(value)
    if not values:
        values = [0]
    return values, errors


def normalise_discard_profile_text(text: Any) -> str:
    """Return the stored form of a discard profile, falling back to the default."""
    values, _errors = parse_discard_profile(text)
    return ",".join(str(v) for v in values)


def corrected_seconds_for_result(elapsed_seconds: Optional[float], rating: Optional[float], result_type: str) -> Optional[float]:
    """Calculate corrected time for an entry under IRC or YTC."""
    if elapsed_seconds is None or rating is None:
        return None
    if result_type.upper() == "YTC":
        # RYA YTC formula: Corrected Time = Elapsed Time * 1000 / YTC.
        return elapsed_seconds * 1000.0 / float(rating)
    return elapsed_seconds * float(rating)


def rating_label_for_type(result_type: str) -> str:
    """Return the display label for a rating type."""
    return "IRC TCC" if result_type.upper() == "IRC" else "YTC number"


def rating_formula_for_type(result_type: str) -> str:
    """Return the corrected-time formula label for a rating type."""
    return "Corrected = elapsed × IRC TCC" if result_type.upper() == "IRC" else "Corrected = elapsed × 1000 ÷ YTC"


def rounded_seconds_for_scoring(seconds: Optional[float]) -> Optional[int]:
    """Return the whole-second corrected time used for race places and ties.

    Results are displayed to whole seconds, so RRS A7 ties must be detected at
    that same precision.  Without this, two boats can show the same corrected
    time in the published table but receive different places because their raw
    floating-point corrected seconds differ by a fraction of a second.
    """
    if seconds is None:
        return None
    return int(round(float(seconds)))


def assign_low_point_race_ranks(rows: List[Dict[str, Any]]) -> None:
    """Assign race places and Appendix A7 low-point scores to corrected-time rows."""
    finishers = [r for r in rows if r["corrected_seconds"] is not None]
    finishers.sort(key=lambda r: (
        rounded_seconds_for_scoring(r["corrected_seconds"]),
        normalise_key(r["entry"]["boat_name"] or ""),
    ))
    idx = 0
    while idx < len(finishers):
        corrected_key = rounded_seconds_for_scoring(finishers[idx]["corrected_seconds"])
        end = idx + 1
        # Equal displayed corrected times are race ties under RRS A7.  The app
        # publishes corrected times to whole-second precision, so the tie test
        # uses the same rounded value as the table rather than invisible
        # fractional seconds from the rating calculation.
        while end < len(finishers) and rounded_seconds_for_scoring(finishers[end]["corrected_seconds"]) == corrected_key:
            end += 1
        first_place = idx + 1
        tied_places = list(range(first_place, end + 1))
        points = sum(tied_places) / len(tied_places)
        for row in finishers[idx:end]:
            row["rank"] = first_place
            row["points"] = float(points)
            row["rank_text"] = str(first_place)
            row["tie"] = len(tied_places) > 1
        idx = end


def is_non_excludable_score(score: Dict[str, Any]) -> bool:
    """Return True when a score must not be discarded under RRS 90.3(b)."""
    return str(score.get("code") or "").upper() in NON_EXCLUDABLE_STATUS_CODES


def choose_discard_indexes(scores: List[Dict[str, Any]], discard_count: int) -> set[int]:
    """Choose discard indexes using RRS A2.1 worst-score and earliest-equal rules."""
    eligible = [i for i, score in enumerate(scores) if not is_non_excludable_score(score)]
    if discard_count <= 0 or not eligible:
        return set()
    # Keep at least one score in the total.  Non-excludable scores such as DNE
    # and DGM already satisfy that requirement, but they themselves must never
    # be selected as discards.
    count = min(discard_count, max(0, len(scores) - 1), len(eligible))
    if count <= 0:
        return set()
    # Worst scores are highest points.  If two worst scores are equal, A2.1 says
    # the score(s) for the race(s) sailed earliest in the series are excluded.
    ordered = sorted(eligible, key=lambda i: (-float(scores[i]["points"]), i))
    return set(ordered[:count])


def series_rank_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    """Return the RRS Appendix A8 series tie-break key for a row."""
    return (
        round(float(row["total"]), 6),
        tuple(round(float(p), 6) for p in row.get("tie_break_a81", [])),
        tuple(round(float(p), 6) for p in row.get("tie_break_a82", [])),
    )


def ordinal_text(value: Any) -> str:
    """Return a simple ordinal label, for example 1st, 2nd, 3rd."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    suffix = "th" if 10 <= (n % 100) <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
