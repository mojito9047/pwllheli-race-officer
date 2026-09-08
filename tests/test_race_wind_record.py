"""Average wind over a finished race — the "record of the conditions".

Once every boat has stopped racing, the public race page's chart and course
analysis switch from the live hut wind to the mean over the race window
(warning signal -> last finish). TWD has to be vector-averaged, otherwise a
race that oscillated either side of north would report a southerly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro
from core.horn import hardware_config, save_hardware_config
from core.weather import circular_mean_deg, summarise_wind_samples


class TestCircularMean:
    def test_averages_either_side_of_north(self):
        # 350 and 10 average to 0, not 180.
        assert circular_mean_deg([350, 10]) == 0.0

    def test_plain_average_case(self):
        assert round(circular_mean_deg([170, 190]), 6) == 180.0

    def test_ignores_missing_values(self):
        assert round(circular_mean_deg([None, 90, None]), 6) == 90.0

    def test_no_values_gives_none(self):
        assert circular_mean_deg([]) is None
        assert circular_mean_deg([None, None]) is None

    def test_exactly_opposed_directions_have_no_mean(self):
        assert circular_mean_deg([0, 180]) is None

    def test_non_numeric_values_are_skipped(self):
        assert round(circular_mean_deg(["nonsense", 45]), 6) == 45.0


class TestSummariseWindSamples:
    def test_summarises_direction_speed_and_gust(self):
        summary = summarise_wind_samples([
            {"twd": 200, "tws": 10.0, "gust": 14.0},
            {"twd": 220, "tws": 14.0, "gust": 19.5},
            {"twd": 210, "tws": 12.0, "gust": None},
        ])
        assert summary["count"] == 3
        assert 205 < summary["twd"] < 215
        assert round(summary["tws"], 2) == 12.0
        assert summary["tws_min"] == 10.0 and summary["tws_max"] == 14.0
        assert summary["gust"] == 19.5      # the peak, not the mean

    def test_empty_set(self):
        summary = summarise_wind_samples([])
        assert summary["count"] == 0
        assert summary["twd"] is None and summary["tws"] is None


def _finished_race_with_wind(twds, *, warning_offset_min=-120, finish_offset_min=-30):
    """A finished race plus wind samples spread across its window."""
    warning = datetime.now() + timedelta(minutes=warning_offset_min)
    finish = datetime.now() + timedelta(minutes=finish_offset_min)
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
            " VALUES ('Wind Record Race', 'IRC', ?, ?, 'IRC_TCC', '', ?)",
            (ro.appstate.COURSES[0]["course_no"], warning.isoformat(timespec="seconds"),
             warning.isoformat(timespec="seconds")),
        )
        race_id = int(cur.lastrowid)
        db.execute(
            "INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time)"
            " VALUES (?, 'Boat', 'GBR1', 'FINISHED', ?)",
            (race_id, finish.isoformat(timespec="seconds")),
        )
        span = (finish - warning).total_seconds()
        for index, twd in enumerate(twds):
            when = warning.timestamp() + span * (index + 1) / (len(twds) + 1)
            db.execute(
                "INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source, raw_json)"
                " VALUES (?, ?, ?, ?, ?, 'test', '{}')",
                (when, datetime.fromtimestamp(when).isoformat(timespec="seconds"), twd, 12.0, 16.0),
            )
        # A sample well outside the window must not be counted.
        outside = warning.timestamp() - 3600
        db.execute(
            "INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source, raw_json)"
            " VALUES (?, ?, 5, 40.0, 60.0, 'test', '{}')",
            (outside, datetime.fromtimestamp(outside).isoformat(timespec="seconds")),
        )
        db.commit()
    with ro.get_db() as db:
        race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    return race_id, race


class TestRaceWindRecord:
    def test_averages_only_samples_inside_the_race_window(self, client):
        race_id, race = _finished_race_with_wind([200, 210, 220])
        record = ro.race_wind_record(race, ro.get_entries(race_id))
        assert record is not None
        assert record["count"] == 3                     # the outside sample is excluded
        assert 205 < record["twd"] < 215
        assert round(record["tws"], 1) == 12.0
        assert record["gust"] == 16.0                   # not the 60 kt outside the window
        assert record["from_iso"] and record["to_iso"]

    def test_none_when_there_is_no_wind_history(self, client):
        race_id, race = _finished_race_with_wind([])
        assert ro.race_wind_record(race, ro.get_entries(race_id)) is None

    def test_none_without_a_warning_time(self, client):
        race_id, race = _finished_race_with_wind([200])
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = '' WHERE id = ?", (race_id,))
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        assert ro.race_wind_record(race, ro.get_entries(race_id)) is None


class TestFinishedRacePageUsesTheAverage:
    def test_page_shows_average_wind_chips_when_finished(self, client):
        race_id, _race = _finished_race_with_wind([200, 210, 220])
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "Average TWD" in html
        assert "Average TWS" in html
        assert "Peak gust" in html
        assert "from the warning signal to the last finish" in html
        # The chart's wind arrow uses the same averaged direction.
        assert 'data-wind-direction="2' in html

    def test_racing_page_still_shows_live_hut_wind(self, client):
        race_id, _race = _finished_race_with_wind([200, 210, 220])
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='RACING', finish_time=NULL WHERE race_id=?", (race_id,))
            db.commit()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "TWD at hut" in html
        assert "Average TWD" not in html


class TestFinishedRaceDropsLiveTrackingColumns:
    def _entries_pane(self, html):
        """Just the Entries tab.

        Scoped deliberately: the Replay tab (v0.199) has its own leaderboard with
        the same column headings, and it is *meant* to be there after the race —
        replaying is mostly something you do afterwards. Matching on the whole
        page would fail on that table instead of the one under test.
        """
        return html.split('id="ptab-entries"', 1)[1].split('id="ptab-chart"', 1)[0]

    def test_gps_columns_go_once_the_race_is_over(self, client):
        """Nothing polls positions after the finish, so the columns would be dashes."""
        save_hardware_config({**hardware_config(), "track_enabled": True})
        race_id, _race = _finished_race_with_wind([200])
        finished = self._entries_pane(client.get(f"/public/race/{race_id}").get_data(as_text=True))
        assert "<th>Marks</th>" not in finished
        assert "Boat positions come from GPS trackers" not in finished
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='RACING', finish_time=NULL WHERE race_id=?", (race_id,))
            db.commit()
        racing = self._entries_pane(client.get(f"/public/race/{race_id}").get_data(as_text=True))
        assert "<th>Marks</th>" in racing
