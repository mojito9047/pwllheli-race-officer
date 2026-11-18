# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""What the public competitor pages know about a boat, beyond its place in a race.

Two things, both small and both shared:

* ``boat_follow_key`` -- the name a favourite is remembered by. A phone keeps its
  owner's favourite boats in its own storage, and an entry's id is no use for
  that: MOJITO is a different entry in every race. The sail number is what stays
  the same, so that is the key, tidied so "GBR 4822R" and "gbr4822r" are one
  boat. The page never works this out for itself; it is handed the key with each
  boat, so the browser and the server cannot come to disagree about it.

* ``public_boat_info`` -- the details the boat card on the chart shows: design,
  club, class, ratings and the finish video once there is one.

Nothing here reads the database or the request. The caller passes in what it has
already loaded, which is what lets the tests build a boat in two lines.

**No owner's name.** The boats table holds one, and the race office needs it,
but a public page is not the place for a private person's name beside where
their boat is. A club that wants it shown can say so; until then it is left out
here, in the one function that decides what the card may know.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

_NOT_KEY_CHARS = re.compile(r"[^A-Z0-9]")


def _field(row: Any, name: str) -> Any:
    """One column of a sqlite Row or a dict; None when it is not there."""
    if row is None:
        return None
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def boat_follow_key(sail_no: Any, boat_name: Any = "") -> str:
    """The key a favourite boat is remembered by: its sail number, tidied.

    A boat entered without a sail number falls back to its name, marked so it
    can never collide with somebody's sail number. Empty when there is neither.
    """
    sail = _NOT_KEY_CHARS.sub("", str(sail_no or "").upper())
    if sail:
        return sail
    name = _NOT_KEY_CHARS.sub("", str(boat_name or "").upper())
    return f"NAME{name}" if name else ""


def _rating(value: Any, digits: int) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.{digits}f}"


def public_boat_info(
    entries: Iterable[Any],
    boats_by_id: Optional[Mapping[int, Any]] = None,
    class_labels: Optional[Mapping[Any, str]] = None,
    finish_video_href: Optional[Callable[[Any], str]] = None,
) -> Dict[str, Dict[str, str]]:
    """Per entry id (as a string, since it becomes a JSON object key): what the card shows.

    ``finish_video_href`` is given the entry and answers the address of its
    finish video, or "" while there is none that can be watched.
    """
    boats_by_id = boats_by_id or {}
    class_labels = class_labels or {}
    info: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        entry_id = _field(entry, "id")
        if entry_id is None:
            continue
        boat = boats_by_id.get(_field(entry, "boat_id")) if _field(entry, "boat_id") is not None else None
        ytc = _field(entry, "manual_ytc_rating")
        try:
            # YTC is published two ways: a time-on-time factor near 1, or the
            # three-figure number it is worked out from. Shown as it was entered.
            ytc_text = _rating(ytc, 0 if float(ytc) >= 10 else 3) if ytc is not None else ""
        except (TypeError, ValueError):
            ytc_text = ""
        video = ""
        if finish_video_href is not None:
            try:
                video = str(finish_video_href(entry) or "")
            except Exception:
                video = ""        # a clip that cannot be linked is no reason to lose the card
        info[str(int(entry_id))] = {
            "key": boat_follow_key(_field(entry, "sail_no"), _field(entry, "boat_name")),
            "name": str(_field(entry, "boat_name") or ""),
            "sail": str(_field(entry, "sail_no") or ""),
            "class": str(class_labels.get(entry_id) or _field(entry, "class_name") or ""),
            "design": str(_field(boat, "design") or ""),
            "club": str(_field(boat, "club") or ""),
            "irc": _rating(_field(entry, "manual_irc_rating"), 3),
            "ytc": ytc_text,
            "video": video,
        }
    return info
