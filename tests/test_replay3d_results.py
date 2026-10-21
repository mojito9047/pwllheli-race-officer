"""The film's closing card has the same results as the results page.

The results page and the printed sheet score every standard race on both IRC and
YTC, whatever the race's rating_rule says. The replay chose by rating_rule
instead, and only knew 'DUAL'. A race left at the database default, 'IRC_TCC' --
Pwllheli's races 1 to 13, and a simulated Liverpool race made without picking
DUAL -- went to the scorer as a result type it does not have. Every boat came
back unscored, and the film closed on the finishing order: no elapsed times, the
finish clock time in the corrected column, and no IRC or YTC heading.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import app as ro
from core import replay3d
from core.races import get_race

GUN = datetime(2026, 10, 20, 14, 5, 0)
# name, IRC TCC, YTC, elapsed minutes
FLEET = [("SHAULA", 0.962, 894, 61.1), ("ONLY JUST", 0.993, 856, 59.8),
         ("ARCTURUS OF LYMINGTON", 1.014, 850, 58.6)]


def make_race(rating_rule=None) -> int:
    stamp = (GUN - timedelta(minutes=5)).isoformat(timespec="seconds")
    with ro.app.app_context():
        with ro.get_db() as db:
            if rating_rule is None:
                cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at)"
                                 " VALUES (1, 'River race', 1, ?, ?)", (stamp, stamp))
            else:
                cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at,"
                                 " rating_rule) VALUES (1, 'River race', 1, ?, ?, ?)",
                                 (stamp, stamp, rating_rule))
            race_id = int(cur.lastrowid)
            for name, irc, ytc, minutes in FLEET:
                finish = (GUN + timedelta(minutes=minutes)).isoformat(timespec="seconds")
                db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time,"
                           " manual_irc_rating, manual_ytc_rating) VALUES (?, ?, '', 'FINISHED', ?, ?, ?)",
                           (race_id, name, finish, irc, ytc))
            db.commit()
    return race_id


def results(race_id):
    with ro.app.app_context():
        return replay3d.race_results(get_race(race_id))


@pytest.mark.parametrize("rule", [None, "IRC_TCC", "DUAL", "YTC"])
def test_a_standard_race_is_scored_on_both_ratings(client, rule):
    tables = results(make_race(rule))
    assert [t["rating_label"] for t in tables] == ["IRC TCC", "YTC number"]
    assert all(t["title"] != "Finishing order" for t in tables)


def test_the_card_has_elapsed_and_corrected_times(client):
    irc, ytc = results(make_race("IRC_TCC"))
    winner = irc["rows"][0]
    assert winner["boat"] == "SHAULA"                   # third home, first on corrected
    assert winner["elapsed"] == "1:01:06" and winner["corrected"] == "0:58:47"   # 3666 s x 0.962
    assert [r["boat"] for r in ytc["rows"]] == ["SHAULA", "ARCTURUS OF LYMINGTON", "ONLY JUST"]


def test_it_agrees_with_the_results_page(client):
    from core.races import get_entries
    from core.series import compute_dual_results

    race_id = make_race("IRC_TCC")
    with ro.app.app_context():
        page = compute_dual_results(get_race(race_id), get_entries(race_id))
    card = results(race_id)
    for group, table in zip((page["irc"], page["ytc"]), card):
        page_rows = [r for r in group["tables"][0]["rows"] if r.get("corrected_text")]
        assert [(str(r["entry"]["boat_name"]), r["corrected_text"]) for r in page_rows] == \
               [(r["boat"], r["corrected"]) for r in table["rows"]]
