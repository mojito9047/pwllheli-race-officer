"""The competitor pages, looked at as an app on a phone (November 2026).

Tried at iPhone size, the pages spent most of the screen on things that were not
the race. What came out of that, and what these tests hold:

- The race page opens on the Chart, and the chart looks after its own size: one
  built inside a closed tab used to come out wrong when the tab was opened.
- Notifications, the favourites and the home-screen offer are behind one cog on
  the competitor home, not lines on every page.
- The explanatory sentences under each heading are gone.
- The 3D replay button is on the results tab, beside the start video.
- Once no boat is racing, Provisional results takes the place of Entries.
- One footer, two lines, the same on both pages.
- A phone gets a slim header, a short summary and cards that read as a line.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime

import app as ro
from tests.test_public_responsive import _race

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def _read(path):
    return path.read_text(encoding="utf-8")


def _finish(race_id):
    with ro.get_db() as db:
        db.execute("UPDATE entries SET status='FINISHED', finish_time=?, manual_irc_rating=1.0 WHERE race_id=?",
                   (datetime.now().isoformat(timespec="seconds"), race_id))
        db.commit()


def _phone_css():
    """The phone block of the tightening pass, which is the last thing in style.css."""
    css = _read(STATIC / "style.css")
    block = css.split("The public pages, tightened", 1)[1]
    return block, block.split("@media (max-width: 700px) {", 1)[1]


class TestTheRacePageOpensOnTheChart:
    def test_before_the_race(self, client):
        html = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        assert 'data-default-tab="ptab-chart"' in html
        assert 'class="tab-pane active" id="ptab-chart"' in html
        assert 'class="tab-button active" data-tab="ptab-chart"' in html

    def test_and_after_it_when_there_are_results(self, client):
        race_id = _race()
        _finish(race_id)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'data-tab="ptab-results"' in html
        assert 'class="tab-pane active" id="ptab-chart"' in html
        assert 'class="tab-pane" id="ptab-results"' in html

    def test_only_one_tab_is_marked_open(self, client):
        race_id = _race()
        _finish(race_id)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert html.count('class="tab-pane active"') == 1
        assert len(re.findall(r'class="tab-button active"', html)) == 1


class TestTheChartLooksAfterItsOwnSize:
    """Leaflet measures its container once. In a closed tab that is 0 x 0."""

    def _js(self):
        return _read(STATIC / "course_map.js")

    def test_it_watches_its_own_box(self):
        js = self._js()
        assert "new window.ResizeObserver(() => settle(container))" in js
        assert "function settle(container)" in js

    def test_a_chart_with_no_size_is_left_alone(self):
        """A phone fires `resize` whenever its address bar slides away. Measuring
        a chart in a closed tab then set it to 0 x 0, and it stayed that way."""
        body = self._js().split("function settle(container)")[1].split("\n  }\n")[0]
        assert "!hasSize(container)" in body
        assert body.index("!hasSize(container)") < body.index("invalidateSize")

    def test_every_way_of_remeasuring_goes_through_it(self):
        js = self._js()
        outside = js.replace(js.split("function settle(container)")[1].split("\n  }\n")[0], "")
        assert "invalidateSize" not in outside, "a second place that measures can measure a hidden chart"
        assert "document.querySelectorAll('.course-map').forEach(settle)" in js

    def test_the_course_is_fitted_again_the_first_time_it_can_be_seen(self):
        js = self._js()
        body = js.split("function settle(container)")[1].split("\n  }\n")[0]
        assert "state.sized = true" in body and "state.home()" in body
        assert "'coursemap:shown'" in body
        assert "sized: hasSize(container)" in js, "a chart built on screen is not fitted twice"

    def test_the_replay_puts_its_boat_back_when_the_chart_appears(self):
        js = _read(STATIC / "race_replay.js")
        handler = js.split("addEventListener('coursemap:shown'")[1][:200]
        assert "revealSelected()" in handler

    def test_the_page_no_longer_tries_to_do_it_once_on_a_timer(self):
        tpl = _read(TEMPLATES / "competitor_race.html")
        assert "mapsReady" not in tpl and "fixChart" not in tpl
        assert "window.RaceCourseMap.invalidate(pane)" in tpl


class TestAFavouriteIsPickedOutWithoutItsCard:
    """On a phone the card lies across the lower half of the chart, so a chart
    that opened with it up looked as though it had not drawn properly."""

    def _js(self):
        return _read(STATIC / "race_replay.js")

    def test_the_card_needs_more_than_a_selection(self):
        assert "if (!boat || !cardOpen) { card.hidden = true;" in self._js()

    def test_a_shared_link_opens_on_the_card_and_a_favourite_does_not(self):
        body = self._js().split("function restoreChoices()")[1].split("\n    }\n")[0]
        assert "cardOpen = want !== undefined;" in body
        assert body.index("cardOpen = want !== undefined;") < body.index("favs.list()")

    def test_a_reload_puts_the_card_back_as_it_was(self):
        js = self._js()
        assert "{sel: selected, card: cardOpen, follow: following, speed: SPEEDS[speedIx]}" in js
        assert "cardOpen = selected !== null && saved.card === true;" in js

    def test_tapping_the_picked_out_boat_opens_its_card_rather_than_letting_it_go(self):
        assert "selected = (o.toggle && selected === want && cardOpen) ? null : want;" in self._js()


class TestSettingsAreBehindACogOnTheHomePage:
    def test_the_home_page_has_the_cog_and_the_dialog(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'class="public-settings-open" data-public-settings="publicSettings"' in html
        assert '<dialog id="publicSettings"' in html
        dialog = html.split('<dialog id="publicSettings"')[1].split("</dialog>")[0]
        for part in ('id="publicNotify"', 'id="publicFollowLine"', 'id="publicInstallPart"'):
            assert part in dialog, part

    def test_nothing_of_it_is_left_in_the_page_itself(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        page = html.split("<main>")[1]
        for gone in ("publicNotify", "publicFollowLine", "follow-clear"):
            assert gone not in page, gone

    def test_the_race_page_has_neither(self, client):
        html = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        for gone in ("publicNotify", "public-settings-open", "publicSettings", "publicFollowLine"):
            assert gone not in html, gone

    def test_the_script_opens_it_and_fills_it(self):
        js = _read(STATIC / "public_app.js")
        assert "ev.target.closest('[data-public-settings]')" in js
        assert "dialog.showModal()" in js
        assert "function drawInstall()" in js and "getElementById('publicInstallPart')" in js

    def test_a_page_with_no_switch_still_keeps_the_hut_in_step(self):
        """Starring a boat on a race page changes what the phone should be told."""
        js = _read(STATIC / "public_app.js")
        start = js.split("function start()")[1]
        assert "if (pushMeta) {" in start
        assert "if (pushMeta && pushMeta.content && line)" not in js

    def test_the_cog_is_drawn_not_typed(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        button = html.split('class="public-settings-open"')[1].split("</button>")[0]
        assert "<svg" in button and 'aria-label="Settings"' in button


class TestTheWayInToTheCurrentRaceIsUnderTheInstruments:
    """What the race is, how it stands (the wind and the clock), then the
    button: not the button between the race and its instruments."""

    def _summary(self, client):
        from tests.test_competitor_follows_the_current_race import _race as racing
        racing("Race being sailed")
        html = client.get("/public/current").get_data(as_text=True)
        return html.split('class="race-header race-summary public-summary"')[1].split("</section>")[0]

    def test_it_is_the_last_thing_in_the_summary(self, client):
        summary = self._summary(client)
        assert summary.index("public-wind-instrument") < summary.index("public-action-row")
        assert summary.index('id="publicHomeCountdown"') < summary.index("public-action-row")
        assert summary.rstrip().endswith("Go to current race</a></p>")

    def test_and_not_inside_the_text_block_any_more(self, client):
        summary = self._summary(client)
        text_block = summary.split('class="race-summary-main"')[1].split('class="public-wind-instrument"')[0]
        assert "Go to current race" not in text_block

    def test_with_no_race_there_is_no_button(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert "Go to current race" not in html and "public-action-row" not in html

    def test_it_has_a_row_to_itself_at_every_width(self):
        block, phone = _phone_css()
        assert ".public-summary > .public-action-row { flex: 1 1 100%; margin: 0; }" in block
        assert ".public-summary > .public-action-row," in phone, "and the full width of the phone's grid"


class TestTheDocumentationNamesNoOtherTracker:
    """Asked for outright (November 2026): the documentation does not name
    the tracking service competitors used before. The mark corners YA and YB
    are another matter, and are everywhere."""

    NAMES = ("YB Tracking", "YB Races", "YB convention", "YB app", "YB's", "yb.tl", "the YB ")

    def test_nothing_that_ships_names_it(self):
        found = []
        places = [ROOT / "docs", ROOT / "scripts", ROOT / "templates", ROOT / "static", ROOT / "core",
                  ROOT / "routes"]
        for place in places:
            for path in place.rglob("*"):
                if path.suffix.lower() not in (".md", ".py", ".html", ".js", ".css", ".txt"):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                found += [f"{path.relative_to(ROOT)}: {name}" for name in self.NAMES if name in text]
        assert not found, found


class TestTheExplanationsAreGone:
    GONE_FROM_THE_RACE_PAGE = (
        "All entered boats are no longer racing",
        "Boats are drawn between their reported positions",
        "A record of the conditions",
        "Averaged over the race, from the warning signal",
        "Taken by the hut camera at the start signal",
        "This is one particular race",
        "may differ from the wind on the race course",
    )
    GONE_FROM_THE_HOME_PAGE = (
        "Series are rolled up",
        "may differ from the wind on the race course",
        "The view from the start hut, while this tab is open",
    )

    def test_the_race_template_says_none_of_it(self):
        tpl = _read(TEMPLATES / "competitor_race.html")
        for text in self.GONE_FROM_THE_RACE_PAGE:
            assert text not in tpl, text
        assert "replay-footnote" not in tpl

    def test_the_home_template_says_none_of_it_either(self):
        tpl = _read(TEMPLATES / "competitors_home.html")
        for text in self.GONE_FROM_THE_HOME_PAGE:
            assert text not in tpl, text

    def test_a_race_that_is_not_the_current_one_just_links_to_it(self, client):
        first = _race("Earlier race")
        _finish(first)
        _race("Race being sailed")
        html = client.get(f"/public/race/{first}").get_data(as_text=True)
        assert '<p class="small current-race-link"><a href="/public/race">Go to the current race</a></p>' in html

    def test_the_wind_still_says_where_it_was_measured(self, client):
        """The sentence warning that hut wind is not course wind went too. What
        is left of the warning is on every figure, which is the better place:
        "at hut", and "Start hut wind" over the plot. The guide says the rest."""
        race = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        for chip in ("TWD at hut", "TWS at hut", "Gust at hut"):
            assert chip in race, chip
        home = client.get("/public/current").get_data(as_text=True)
        assert "<h3>Start hut wind</h3>" in home and "TWD at hut" in home
        guide = _read(ROOT / "scripts" / "build_competitor_guide.py")
        assert "<b>Hut wind, not course wind.</b>" in guide


class TestProvisionalResultsTakeThePlaceOfEntries:
    """Every boat on the entries list is in the results, with its class, its
    rating and its status. After the finish the list was the same boats again
    with less to say, in a tab of its own."""

    def test_while_boats_are_racing_there_are_entries_and_no_results(self, client):
        html = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        assert 'data-tab="ptab-entries">Entries</button>' in html
        assert 'id="publicEntriesPanel"' in html
        assert "ptab-results" not in html.split("<script>")[0]
        assert "Provisional results" not in html

    def test_once_none_are_the_results_are_there_instead(self, client):
        race_id = _race()
        _finish(race_id)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'data-tab="ptab-results">Provisional results</button>' in html
        assert 'data-tab="ptab-entries"' not in html
        assert 'id="ptab-entries"' not in html and "publicEntriesPanel" not in html

    def test_the_tab_is_not_called_leader_board_any_more(self, client):
        """The live order during a race is the Leaderboard under the chart. Two
        things with one name on one page was one too many."""
        race_id = _race()
        _finish(race_id)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "Leader board" not in html
        assert "<h3>Provisional results</h3>" in html

    def test_a_pursuit_race_keeps_its_start_times(self):
        """They are not in its results: those are a finishing order."""
        tpl = _read(TEMPLATES / "competitor_race.html")
        assert "{% set entries_tab = is_pursuit or not race_finished %}" in tpl
        assert tpl.count("{% if entries_tab %}") == 2, "the tab and its pane, together or not at all"

    def test_somebody_on_entries_when_the_last_boat_finishes_lands_on_the_results(self):
        """The page reloads itself carrying the tab they were on, and that tab
        is no longer there. An old link to either tab goes the same way."""
        tpl = _read(TEMPLATES / "competitor_race.html")
        assert "if (tabId === 'ptab-entries' && !document.getElementById(tabId)) tabId = 'ptab-results';" in tpl
        assert "if (tabId === 'ptab-results' && !document.getElementById(tabId)) tabId = 'ptab-entries';" in tpl


class TestTheFilmButtonIsOnTheResultsTab:
    FILM = "https://films.example.org/race.mp4"

    def _page(self, client, monkeypatch, finished=True):
        monkeypatch.setattr(ro.replay3d, "film_url_for", lambda race_id: self.FILM)
        race_id = _race()
        if finished:
            _finish(race_id)
        return client.get(f"/public/race/{race_id}").get_data(as_text=True)

    def test_it_is_in_the_results_pane(self, client, monkeypatch):
        html = self._page(client, monkeypatch)
        pane = html.split('id="ptab-results"')[1].split('id="ptab-chart"')[0]
        assert f'href="{self.FILM}"' in pane and ">Watch the 3D replay<" in pane

    def test_and_nowhere_else_on_the_page(self, client, monkeypatch):
        html = self._page(client, monkeypatch)
        assert html.count(">Watch the 3D replay<") == 1
        header = html.split('class="race-header')[1].split('class="race-tabs')[0]
        assert "3D replay" not in header and "film-link" not in html

    def test_it_shares_a_line_with_the_start_video(self):
        tpl = _read(TEMPLATES / "competitor_race.html")
        line = tpl.split('<p class="small result-links">')[2].split("</p>")[0]
        assert "View start video" in line and "film_button()" in line

    def test_a_pursuit_race_has_it_too(self):
        """Its videos are per boat, so it has no start-video line to share."""
        tpl = _read(TEMPLATES / "competitor_race.html")
        pursuit = tpl.split("<h3>Provisional results (finishing order)</h3>")[1].split("{% else %}")[0]
        assert "film_button()" in pursuit


class TestOneFooterForBothPages:
    def _footer(self, html):
        return re.sub(r"\s+", " ", html.split('<footer class="public-footer">')[1].split("</footer>")[0]).strip()

    def test_they_are_the_same(self, client):
        ro.save_app_settings({"public_live_stream_url": "https://live.example.org"})
        home = client.get("/public/current").get_data(as_text=True)
        race = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        assert self._footer(home) == self._footer(race)
        assert "Watch live" in self._footer(home)

    def test_it_is_one_partial(self):
        for name in ("competitors_home.html", "competitor_race.html"):
            tpl = _read(TEMPLATES / name)
            assert "{% include 'partials/public_footer.html' %}" in tpl
            assert '<footer class="public-footer">' not in tpl

    def test_it_still_says_what_the_page_is_not(self, client):
        footer = self._footer(client.get("/public/current").get_data(as_text=True))
        assert "Provisional information" in footer
        assert "Race officer decisions and published results are authoritative" in footer
        assert f"v{ro.APP_VERSION}" in footer and "/documentation/competitor-guide" in footer

    def test_it_is_two_paragraphs_not_a_row_of_flex_items(self, client):
        """The race-office footer is a flex row, which made every link an item
        of its own: five lines and 140px on a phone."""
        footer = self._footer(client.get("/public/current").get_data(as_text=True))
        assert footer.count("<p>") == 2
        block, _phone = _phone_css()
        rule = block.split(".public-page .public-footer {")[1].split("}")[0]
        assert "display: block" in rule


class TestAPhoneGetsTheRaceSooner:
    def test_one_slim_row_across_the_top(self):
        _block, phone = _phone_css()
        assert "--public-header-h: 48px" in phone
        header = phone.split(".competitor-race-page > header {")[0].rsplit("}", 1)[1] \
            + phone.split(".competitor-race-page > header {")[1].split("}")[0]
        assert "flex-direction: row" in header and "height: var(--public-header-h)" in header

    def test_the_home_link_has_a_short_name_for_it(self, client):
        html = client.get(f"/public/race/{_race()}").get_data(as_text=True)
        assert '<span class="nav-long">Competitor home</span><span class="nav-short" aria-hidden="true">Home</span>' in html
        block, phone = _phone_css()
        assert ".nav-short { display: none; }" in block
        assert ".nav-long { display: none; }" in phone and ".nav-short { display: inline; }" in phone

    def test_the_tabs_stick_under_the_header_not_behind_it(self):
        _block, phone = _phone_css()
        assert ".public-page .race-tabs .tab-nav { top: var(--public-header-h); }" in phone

    def test_an_empty_flag_board_is_not_shown(self):
        _block, phone = _phone_css()
        assert ".public-page .race-header-flags.no-flags-up { display: none; }" in phone

    def test_the_clock_is_one_line_and_says_finished_once(self):
        _block, phone = _phone_css()
        clock = phone.split(".competitor-race-page .countdown {")[1].split("}")[0]
        assert "display: flex" in clock
        assert '.competitor-race-page .countdown[data-race-finished="1"] .state { display: none; }' in phone

    def test_a_card_reads_as_a_line_of_facts(self):
        """Label hard left and value hard right was eight rows a boat."""
        _block, phone = _phone_css()
        row = phone.split(".public-page .stack-table:not(.replay-board) tr {")[1].split("}")[0]
        assert "display: flex" in row and "flex-wrap: wrap" in row
        cell = phone.split(".public-page .stack-table:not(.replay-board) td {")[1].split("}")[0]
        assert "justify-content: flex-start" in cell

    def test_the_leaderboard_under_the_chart_keeps_its_own_layout(self):
        _block, phone = _phone_css()
        for line in phone.splitlines():
            if ".stack-table" in line and "{" in line and "#htab-races" not in line:
                assert ":not(.replay-board)" in line, line

    def test_the_times_come_first_on_a_result(self):
        _block, phone = _phone_css()
        assert 'td[data-label="Corrected"] { order: 1; }' in phone
        assert 'td[data-label="Corrected"] { font-weight: 700; }' in phone

    def test_only_the_phone_layout_is_touched(self):
        """Everything above is inside the one media query; a wide screen keeps
        its tables, its flag board and its clock."""
        block, phone = _phone_css()
        before = block.split("@media (max-width: 700px) {", 1)[0]
        for phone_only in (".stack-table", ".countdown", ".no-flags-up", ".tab-nav"):
            assert phone_only not in before, phone_only
        assert phone.rstrip().endswith("}")
