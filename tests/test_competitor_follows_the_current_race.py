"""A competitor on the water is not left on the race they have just sailed.

The journey, as reported: open the competitor home on a phone, press **Go to
current race**, sail the race, finish. The race officer finishes the fleet and
moves on to the next one — and the competitor sits on the race that is over,
because that button handed them ``/public/race/<id>`` and pinned them to it. The
only way forward was to know to go back to the home page and press the same
button again, which is not something to work out one-handed on a wet phone.

The distinction the clubhouse television already draws: ``/bar`` follows the day
and ``/bar/<id>`` pins to one race. ``/public/race`` and ``/public/race/<id>`` now
do the same. Pinning is still what a link shared to somebody needs, and what
looking back at last week's race needs, so both exist and both say which they
are.

No new client code was needed for the following part. The page already polls a
state signature every two seconds and reloads when it changes; that signature is
computed over a dict that includes ``race_id``, so pointing an unpinned page at
the *current race* state endpoint makes the race officer moving on look exactly
like a course change — something the page already knows how to pick up.

The positions and track endpoints stay pinned to the race being rendered. They
are fetched between reloads, and following the current race there would draw one
race's boats over another's course for as long as it took the page to notice.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro


def _race(name, *, racing=True, hours_ago=1):
    """A race with a boat still RACING is what makes it the current one."""
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    when = (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, course_no, course_set, start_time, notes, created_at)"
            " VALUES (?,1,1,?,'',?)", (name, when, now))
        rid = int(cur.lastrowid)
        db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status) VALUES (?,?,?,?)",
                   (rid, name + " boat", "GBR" + str(rid), "RACING" if racing else "FINISHED"))
        db.commit()
        return rid


def _finish_the_fleet(race_id):
    from core.db import get_db
    when = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE race_id=?",
                   (when, race_id))
        db.commit()


class TestTheFollowingPage:
    def test_it_renders_the_current_race(self, client):
        rid = _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert "Race One" in html

    def test_it_moves_on_when_the_race_officer_does(self, client):
        """The whole report, end to end."""
        first = _race("Race One")
        assert "Race One" in client.get("/public/race").get_data(as_text=True)
        _finish_the_fleet(first)
        _race("Race Two", hours_ago=0)
        html = client.get("/public/race").get_data(as_text=True)
        assert "Race Two" in html
        assert ">Race One<" not in html

    def test_it_says_that_it_follows(self, client):
        """A page that changes race under somebody is startling unless it said so.

        Behind a `?` rather than on the page: three lines of explanation across a
        390px phone pushed the race name, the course board, the flags and the
        clock down the screen, and it is read once and then in the way."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert 'data-help="followHelp"' in html
        assert '<dialog id="followHelp"' in html

    def test_the_explanation_is_not_on_the_page_itself(self, client):
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        header = html.split('<section class="race-tabs')[0]
        assert "follows whichever race is current" not in header

    def test_the_popout_opens_on_this_standalone_page(self, client):
        """base.html gives every race-office page the delegated `?` handler and
        this page is not one of them: it builds its own <head>, so a `?` with no
        handler beside it would simply do nothing."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert "closest('[data-help]')" in html

    def test_it_offers_a_way_to_stay_put(self, client):
        rid = _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert f"/public/race/{rid}" in html

    def test_no_races_at_all_is_not_a_crash(self, client):
        assert client.get("/public/race").status_code == 404


class TestThePinnedPageStillPins:
    def test_a_shared_link_stays_on_its_race(self, client):
        first = _race("Race One")
        _finish_the_fleet(first)
        _race("Race Two", hours_ago=0)
        html = client.get(f"/public/race/{first}").get_data(as_text=True)
        assert "Race One" in html

    def test_and_says_the_racing_has_moved_on(self, client):
        """Otherwise a bookmark leaves somebody watching a finished race with
        nothing on the page to say where everyone went."""
        first = _race("Race One")
        _finish_the_fleet(first)
        _race("Race Two", hours_ago=0)
        html = client.get(f"/public/race/{first}").get_data(as_text=True)
        assert "not the one being sailed now" in html

    def test_the_current_race_pinned_says_no_such_thing(self, client):
        rid = _race("Race One")
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "not the one being sailed now" not in html
        assert "follows whichever race is current" not in html


class TestWhatEachPagePolls:
    def test_the_following_page_polls_the_current_race_state(self, client):
        """This is what makes it follow: the signature covers the race id, so the
        page's existing two-second reload does the rest."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert '"/public/race/state"' in html

    def test_the_pinned_page_polls_its_own_race(self, client):
        rid = _race("Race One")
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert f'"/public/race/{rid}/state"' in html

    def test_positions_stay_pinned_on_the_following_page(self, client):
        """Following there would draw one race's boats over another's course for
        as long as it took the page to notice.

        Asked of the URL builder rather than the rendered page: the positions and
        track URLs are only written into the HTML when GPS tracking is switched
        on, and this rule holds whether it is or not."""
        rid = _race("Race One")
        with ro.app.test_request_context("/public/race"):
            race = ro.get_race(rid)
            urls = ro.public_context_urls(race, pinned=False)
        assert urls["positions"] == f"/public/race/{rid}/positions"
        assert urls["track"] == f"/public/race/{rid}/track"
        assert urls["state"] == "/public/race/state"

    def test_and_the_pinned_page_pins_all_three(self, client):
        rid = _race("Race One")
        with ro.app.test_request_context(f"/public/race/{rid}"):
            urls = ro.public_context_urls(ro.get_race(rid), pinned=True)
        assert urls["state"] == f"/public/race/{rid}/state"


class TestWhereTheReloadLands:
    """Every reload landed at the tabs, wherever the viewer actually was.

    The URL carries the open tab as a `#fragment`, and a browser honours a
    fragment before any of this page's script runs. Look at the chart, scroll
    back up to watch the countdown, and the moment the race officer changes the
    course you are thrown back down to the chart — never seeing the course board
    or the clock change. The reload was carrying the viewer away from the very
    thing it was delivering.

    Scoping the fix to race changes, as the first attempt did, left exactly that
    case broken. So no state reload leaves an anchor to jump to, and the page
    then lands somewhere chosen: a different race at the top, because the header
    is the news; the same race exactly where the viewer was.

    The scrolling itself is the browser's and cannot be asserted from here. Both
    halves were driven through a 390px viewport: a course change with the viewer
    scrolled up to the countdown came back at the same scrollY with the timer in
    view and the new course on the board, and a race change came back at scrollY
    0 with the race name in view. What these hold is the mechanism.
    """

    def test_the_page_knows_which_race_it_was_rendered_for(self, client):
        rid = _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert f"const renderedRaceId = {rid};" in html

    def test_no_reload_leaves_an_anchor_to_jump_to(self, client):
        """Unconditional, and that is the point: scoping it to race changes left
        the reported fault in place for a course change."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        js = html.split("async function checkRaceState")[1].split("setInterval")[0]
        before_hop = js.split("next.hash = '';")[0]
        assert "next.searchParams.set('tab', tab)" in js
        assert "data.race_id !== renderedRaceId" not in before_hop, \
            "the tab hop must not sit inside the race-change branch"

    def test_the_scroll_position_is_kept_for_the_same_race(self, client):
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert "sessionStorage.setItem(SCROLL_KEY" in html

    def test_and_cleared_when_the_race_changes(self, client):
        """Otherwise the next race would open at the last one's scroll position,
        with its own header pushed off the top -- the first fault again."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert "data.race_id !== renderedRaceId" in html
        assert "sessionStorage.removeItem(SCROLL_KEY);" in html

    def test_the_key_is_per_race(self, client):
        """A remembered position must not be applied to a different race."""
        rid = _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert f"'roScroll:{rid}'" in html

    def test_storage_being_unavailable_is_not_an_error(self, client):
        """A private window must still get the race, just at the top."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert html.count("catch (err)") >= 2

    def test_the_hash_still_wins_when_both_are_present(self, client):
        """A state reload strips the hash, so the parameter only decides when it
        is the newer of the two. The other way round would send somebody who had
        since changed tab back to the one they were on two reloads ago."""
        _race("Race One")
        html = client.get("/public/race").get_data(as_text=True)
        assert "show(location.hash ? location.hash.slice(1) : (queryTab || defaultTab), false);" in html


class TestTheStateEndpoint:
    def test_it_answers_for_the_current_race(self, client):
        rid = _race("Race One")
        data = client.get("/public/race/state").get_json()
        assert data["ok"] is True and data["race_id"] == rid

    def test_its_signature_changes_when_the_race_does(self, client):
        first = _race("Race One")
        before = client.get("/public/race/state").get_json()["signature"]
        _finish_the_fleet(first)
        _race("Race Two", hours_ago=0)
        after = client.get("/public/race/state").get_json()["signature"]
        assert before != after

    def test_it_matches_the_pinned_endpoint_for_the_same_race(self, client):
        """Two ways of asking the same question must not give two answers, or an
        unpinned page would reload itself in a loop."""
        rid = _race("Race One")
        following = client.get("/public/race/state").get_json()["signature"]
        pinned = client.get(f"/public/race/{rid}/state").get_json()["signature"]
        assert following == pinned

    def test_no_current_race_is_a_404_not_a_500(self, client):
        assert client.get("/public/race/state").status_code == 404


class TestTheHomePageSendsThemThere:
    def test_go_to_current_race_is_the_following_page(self, client):
        _race("Race One")
        html = client.get("/public/current").get_data(as_text=True)
        assert 'href="/public/race"' in html


class TestTheStateUrlIsNotAmbiguous:
    def test_state_is_not_read_as_a_race_id(self, client):
        """/public/race/state and /public/race/<int:race_id> share a prefix; the
        int converter is what keeps them apart, and that is worth a test."""
        _race("Race One")
        assert client.get("/public/race/state").get_json()["ok"] is True
