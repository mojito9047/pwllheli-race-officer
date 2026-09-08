"""A boat with no tracker on it is not out on the water.

Reported from a race with **no trackers assigned to any boat in it**: the race
sheet and the competitor page both listed one boat at 96.73 nm to go, first on
the water, with a last fix "39832m ago" — twenty-seven days.

Two deliberate behaviours meeting, each right on its own:

* ``effective_tracker_for_entry`` falls back to ``last_tracker_seen_for_boat``,
  so a race sailed months ago still finds its track after the trackers were
  deleted from the Trackers page. Without it, deleting a simulated fleet took
  every race it had ever sailed blank with it.
* the leaderboard's display position reaches *outside* the race window on
  purpose, "otherwise a boat vanishes from the map before/just after the gun" —
  a tracker that last reported three minutes before the warning signal should
  still put the boat on the chart.

Together they say: any boat that ever carried a tracker is at wherever it last
was, for ever. The second had no bound, and an hour is the app's own definition
of a tracker that is not reporting — the red dot on the Trackers page. Past that
the display falls back to the last fix inside the race itself, which is what an
old race wants and is correctly empty for a race that has not started.

The distance-to-go went with it: it is derived from that display fix, so it was
"how far this boat would have to sail from where it was last month".
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import app as ro
import core.track as track

from tests.test_race_replay import _seed_race, _track


def _row(race_id, boat_name):
    return next(r for r in track.race_leaderboard(race_id) if r["boat_name"] == boat_name)


class TestAMonthOldFixIsNotAPosition:
    def test_the_boat_has_no_position(self, client):
        """The reported case, with the reported age."""
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)   # starts in an hour
        long_ago = time.time() - 27 * 24 * 3600
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(long_ago + i * 10, 52.88, -4.40) for i in range(5)])
        row = _row(race_id, "Alpha")
        assert row["lat"] is None and row["lon"] is None

    def test_and_no_distance_to_go_worked_out_from_it(self, client):
        """96.73 nm from where it was last month is not a useful number."""
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)
        long_ago = time.time() - 27 * 24 * 3600
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(long_ago + i * 10, 52.88, -4.40) for i in range(5)])
        assert _row(race_id, "Alpha")["dist_remaining_nm"] is None

    def test_and_no_fix_age_to_display(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)
        long_ago = time.time() - 27 * 24 * 3600
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(long_ago, 52.88, -4.40)])
        assert _row(race_id, "Alpha")["age"] is None


class TestARecentFixStillShows:
    """The behaviour the reach-outside-the-window exists for, which must survive."""

    def test_a_boat_that_reported_before_the_gun_is_on_the_chart(self, client):
        """Three minutes before the warning signal, race not started."""
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)
        recent = time.time() - 180
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(recent, 52.88, -4.40)])
        row = _row(race_id, "Alpha")
        assert row["lat"] is not None
        assert row["age"] is not None and row["age"] < 600

    def test_the_boundary_is_an_hour(self, client):
        """The same line the Trackers page draws between amber and red."""
        assert track.TRACK_RAG_AMBER_S == 3600
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 3500, 52.88, -4.40)])
        assert _row(race_id, "Alpha")["lat"] is not None

    def test_just_past_it_is_gone(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 3700, 52.88, -4.40)])
        assert _row(race_id, "Alpha")["lat"] is None


class TestAnOldRaceIsStillReadable:
    """The other behaviour that must survive: a race sailed months ago, whose
    trackers have since been deleted, still shows where its boats went."""

    def test_a_finished_race_keeps_its_positions(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        # Fixes inside the race window, all of them far older than an hour.
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 30, 52.88 + i * 1e-4, -4.40) for i in range(20)])
        row = _row(race_id, "Alpha")
        assert row["lat"] is not None, "an old race lost its track"

    def test_even_with_the_tracker_deleted(self, client):
        """last_tracker_seen_for_boat is what finds it, and still does."""
        from core.db import get_db
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 30, 52.88 + i * 1e-4, -4.40) for i in range(20)])
        with get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id = 'SIMR-Alpha'")
            db.commit()
        row = _row(race_id, "Alpha")
        assert row["tracked"] is True
        assert row["lat"] is not None


class TestTheFleetListSaysWhichKindOfNothing:
    """Two boats with no position looked different for no visible reason: one
    said "No tracker", the other showed "0/7 marks, next 1" beside an empty Fix.

    Bounding the display fix to an hour made the second the common case rather
    than a rarity, so the two now say which they are. Which they are turns on
    the tracker the boat has **now** -- ``tracker_assigned`` -- and not on
    ``tracked``, which stays true for any boat that ever carried one so an old
    race keeps its track. A boat whose tracker has been taken off it has not
    gone quiet, and calling it "Not reporting" would be a plain lie.
    """

    import pathlib as _pathlib
    _ROOT = _pathlib.Path(__file__).resolve().parent.parent
    COMP = (_ROOT / "templates" / "competitor_race.html").read_text(encoding="utf-8")
    RACE = (_ROOT / "templates" / "race.html").read_text(encoding="utf-8")

    def test_the_competitor_page_has_three_states(self):
        assert "'Not reporting' : 'No tracker'" in self.COMP

    def test_and_so_does_the_race_sheet(self):
        assert "'Not reporting' : 'No tracker'" in self.RACE

    def test_neither_page_decides_it_from_tracked(self):
        """`tracked` is true for a boat that once had a tracker, so branching on
        it calls a boat with no tracker on it "Not reporting"."""
        for tpl in (self.COMP, self.RACE):
            assert "b.tracker_assigned ? 'Not reporting'" in tpl
            assert "b.tracked ? 'Not reporting'" not in tpl

    def test_a_boat_with_no_position_shows_no_progress(self):
        """0/7 and "next 1" are true of a boat nobody can see, and read as live
        data. Both pages take the no-position branch before the live one."""
        for tpl in (self.COMP, self.RACE):
            assert "!b.lat && b.lat !== 0" in tpl


class TestWhichKindOfNothingTheServerReports:
    def _race(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=-60)   # starts in an hour
        return race_id, ids

    def test_a_quiet_tracker_is_still_assigned(self, client):
        """Nothing for twenty-seven days, but the tracker is on the boat: this
        is the boat that is genuinely not reporting."""
        race_id, ids = self._race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(time.time() - 27 * 24 * 3600, 52.88, -4.40)])
        row = _row(race_id, "Alpha")
        assert row["lat"] is None
        assert row["tracker_assigned"] is True

    def test_a_boat_whose_tracker_was_taken_off_it_is_not_quiet(self, client):
        """The reported case. `tracked` stays true -- there are fixes to draw --
        but there is no tracker on the boat, so the page must say "No tracker"."""
        from core.db import get_db
        race_id, ids = self._race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(time.time() - 27 * 24 * 3600, 52.88, -4.40)])
        with get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id = 'SIMR-Alpha'")
            db.commit()
        row = _row(race_id, "Alpha")
        assert row["tracked"] is True, "the track must still be findable"
        assert row["tracker_assigned"] is False

    def test_a_boat_that_never_had_one(self, client):
        from core.db import get_db
        race_id, ids = self._race(client)
        with get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id = 'SIMR-Bravo'")
            db.commit()
        row = _row(race_id, "Bravo")
        assert row["tracked"] is False and row["tracker_assigned"] is False

    def test_the_retired_per_entry_override_counts_as_assigned(self, client):
        """Nothing writes ``entries.tracker_unique_id`` any more, but an old row
        may carry one and the position still comes from it -- so it has to count
        here too, or that boat reads "No tracker" while showing a live fix."""
        from core.db import get_db
        race_id, ids = self._race(client)
        with get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id = 'SIMR-Bravo'")
            db.execute("UPDATE entries SET tracker_unique_id = 'LOANER-9' WHERE id = ?",
                       (ids["Bravo"]["entry_id"],))
            db.commit()
        assert _row(race_id, "Bravo")["tracker_assigned"] is True

    def test_every_row_carries_the_flag(self, client):
        """Both row shapes -- tracked and untracked -- or the page reads
        `undefined` and quietly picks one."""
        race_id, ids = self._race(client)
        from core.db import get_db
        with get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id = 'SIMR-Bravo'")
            db.commit()
        for row in track.race_leaderboard(race_id):
            assert "tracker_assigned" in row, row["boat_name"]
