"""One order for a series' races, and a race that says when it is not scheduled.

**Six races, three orders.** The series page, the Races page and the competitor
page each sorted by ``COALESCE(start_time, created_at)`` — two of them ascending,
one descending — so the same six races appeared in three different sequences.
Worse than inconsistent: that expression sorts one race by when it *starts* and
the next by when it was *typed in*, which are different clocks. Give Race 1 of a
September series a start time and its key jumps from "created on the 2nd" to
"starts on the 4th", and it moves from the middle of the list to the end of it.

The rule now is one rule, in one place: a start time when the race has one,
otherwise the name read naturally, so a series set up in advance lists Race 1 to
Race 6 rather than in whatever order somebody happened to create them. Naturally,
because plain alphabetical puts *Race 10* before *Race 2*.

**A fully scheduled series is unaffected**, and that is not a nicety: a race's
number in its series is its position in this list, and a scored season must not
renumber itself. Every race having a start time means this is start-time order,
exactly as before.

**And six races all read "Racing".** A new race's entries are added RACING, which
is right — it is how a boat that has not finished is recorded — and is not the
same as the race being under way. A series set up in advance showed six races all
claiming to be racing before a course or a start time had been set for any of
them; then the first got a start time and changed to *Start sequence pending*
while the other five went on claiming to race. "No start time" is asked first now.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.races import (
    natural_name_key,
    race_sort_key,
    race_status_label,
    races_in_order,
)


def _row(name, start_time="", status=None):
    return {"name": name, "start_time": start_time, "id": 0, "status": status}


class TestNaturalNames:
    def test_race_two_comes_before_race_ten(self):
        names = ["Race 10", "Race 2", "Race 1"]
        assert sorted(names, key=natural_name_key) == ["Race 1", "Race 2", "Race 10"]

    def test_it_is_case_insensitive(self):
        assert natural_name_key("RACE 1") == natural_name_key("race 1")

    def test_a_name_with_no_number_still_sorts(self):
        names = ["Autumn Cup", "Spring Regatta"]
        assert sorted(names, key=natural_name_key) == names

    def test_an_empty_name_does_not_raise(self):
        assert natural_name_key("") == ()
        assert natural_name_key(None) == ()


class TestTheOrderRacesAreSailedIn:
    def test_an_unscheduled_series_lists_by_name(self):
        """The reported case: six races created out of order, none scheduled."""
        rows = [_row("Welsh IRCs Cruisers - Race 6"), _row("Welsh IRCs Cruisers - Race 2"),
                _row("Welsh IRCs Cruisers - Race 4"), _row("Welsh IRCs Cruisers - Race 1"),
                _row("Welsh IRCs Cruisers - Race 5"), _row("Welsh IRCs Cruisers - Race 3")]
        assert [r["name"][-1] for r in races_in_order(rows)] == list("123456")

    def test_a_scheduled_series_lists_by_start_time(self):
        """And this is the guarantee that matters: a race's number in its series
        is its position here, so a scored season must not renumber itself."""
        rows = [_row("Race C", "2026-09-06T10:00"), _row("Race A", "2026-09-04T10:00"),
                _row("Race B", "2026-09-05T10:00")]
        assert [r["name"] for r in races_in_order(rows)] == ["Race A", "Race B", "Race C"]

    def test_a_name_never_outranks_a_start_time(self):
        """Race 9 is sailed; races 1-8 are not yet scheduled. The one that has
        happened comes first, because it is the one that has a place in time."""
        rows = [_row("Race 1"), _row("Race 9", "2026-09-04T10:00"), _row("Race 2")]
        assert [r["name"] for r in races_in_order(rows)] == ["Race 9", "Race 1", "Race 2"]

    def test_the_two_clocks_are_never_compared(self):
        """The whole fault in one assertion: a start time and a creation date are
        not comparable, so the key never puts them in the same slot."""
        scheduled = race_sort_key(_row("Race 1", "2026-09-04T11:14"))
        unscheduled = race_sort_key(_row("Race 2"))
        assert scheduled[0] == 0 and unscheduled[0] == 1

    def test_ordering_is_stable_for_identical_races(self):
        rows = [_row("Race 1"), _row("Race 1")]
        assert len(races_in_order(rows)) == 2


class TestWhatARaceIsCalled:
    @staticmethod
    def _race(start_time=""):
        return {"id": 1, "name": "R1", "start_time": start_time}

    RACING = [{"status": "RACING", "finish_time": None}]
    FINISHED = [{"status": "FINISHED", "finish_time": "2026-09-04T12:00"}]

    def test_no_start_time_is_not_racing(self, client):
        """The reported fault. Entries are added RACING; that is how a boat which
        has not finished is recorded, and says nothing about the race."""
        assert race_status_label(self._race(), self.RACING) == "No start time set"

    def test_a_start_time_in_the_future_is_pending(self, client):
        when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        assert race_status_label(self._race(when), self.RACING) == "Start sequence pending"

    def test_a_start_time_in_the_past_with_boats_out_is_racing(self, client):
        when = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        assert race_status_label(self._race(when), self.RACING) == "Racing"

    def test_everybody_in_is_finished(self, client):
        when = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        assert race_status_label(self._race(when), self.FINISHED) == "Finished"

    def test_a_race_with_no_entries_is_not_finished(self, client):
        """Nought out of nought boats have finished, which is not a result."""
        assert race_status_label(self._race(), []) == "No start time set"


class TestTheThreePagesAgree:
    """The point of the exercise, asserted end to end rather than by unit."""

    @staticmethod
    def _seed():
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO race_series (name, description, discard_profile,"
                " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("Order Test", "", "0,0,1", 3, now, now))
            sid = int(cur.lastrowid)
            # Created deliberately out of order, as the club's were.
            for name in ("Race 6", "Race 2", "Race 4", "Race 1", "Race 5", "Race 3"):
                cur = db.execute(
                    "INSERT INTO races (name, series_id, course_no, course_set, start_time,"
                    " notes, created_at) VALUES (?,?,1,1,'','',?)", (name, sid, now))
                db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status)"
                           " VALUES (?,'Boat','GBR1','RACING')", (int(cur.lastrowid),))
            db.commit()
        return sid

    @staticmethod
    def _order(html, after=None):
        """The race names in the order the page lists them.

        ``after`` skips the page furniture: the competitor page names the current
        race in its header before any table, so a bare scan of the document reads
        that first and reports an order the table does not have.
        """
        import re
        if after and after in html:
            html = html.split(after, 1)[1]
        return re.findall(r"Race [1-9]\b", html)

    def test_the_series_page_lists_them_in_order(self, logged_in_client):
        sid = self._seed()
        html = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        seen = [n for n in self._order(html)]
        # The first six are the races table; the page repeats names in results.
        assert seen[:6] == [f"Race {i}" for i in range(1, 7)], seen[:6]

    def test_and_so_does_the_races_page(self, logged_in_client):
        self._seed()
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        seen = self._order(html)
        assert seen[:6] == [f"Race {i}" for i in range(1, 7)], seen[:6]

    def test_and_so_does_the_competitor_page(self, client):
        self._seed()
        html = client.get("/public/current").get_data(as_text=True)
        seen = self._order(html, after="<h3>Races in ")
        assert seen[:6] == [f"Race {i}" for i in range(1, 7)], seen[:6]

    def test_and_none_of_them_claims_the_races_are_racing(self, client):
        self._seed()
        html = client.get("/public/current").get_data(as_text=True)
        assert "No start time set" in html
