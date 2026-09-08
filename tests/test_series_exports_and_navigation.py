"""Four small things that were each wrong in their own way.

**"Publish HTML" did not publish anything.** It opens the results page in a tab
to look at. Publishing is what the website export and the R2 upload do, and a
button that claims it on a page carrying a season's results is the wrong word to
hesitate over.

**The downloads were named after a row id.** ``series_1_results_publish.html``
and ``series_1_irc_sailwave.csv``, which say neither which series nor which run,
in a folder where they land more than once as a season is scored.

**Opening a race scrolled past the race.** ``history.replaceState`` does not
scroll, but the fragment it writes is still in the document's URL when the
browser runs its scroll-to-fragment step -- which it retries as the page settles,
finds ``#tab-course`` and jumps to it. Measured: a race opened at scrollY 254
with the race name, the course board, the flags and the countdown all above the
top of the screen, about 200ms after load. The *competitor* page hit this exact
thing and guarded it, with a comment saying so; the race sheet never got the
same fix.

**And Trackers sat below Hut power** in the side menu, which is the wrong way
round for how often each is opened on a race day.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime

from core.series import export_filename

_ROOT = pathlib.Path(__file__).resolve().parent.parent
RACE_TPL = (_ROOT / "templates" / "race.html").read_text(encoding="utf-8")
BASE_TPL = (_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
SERIES_TPL = (_ROOT / "templates" / "series_detail.html").read_text(encoding="utf-8")

WHEN = datetime(2026, 9, 1, 16, 42)


def _series(name="Autumn Series 2026"):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, discard_profile,"
            " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, "", "0,0,1", 3, now, now))
        db.commit()
        return int(cur.lastrowid)


class TestTheButtonSaysWhatItDoes:
    def test_it_offers_a_preview(self):
        assert ">Preview HTML<" in SERIES_TPL

    def test_and_no_longer_claims_to_publish(self):
        assert ">Publish HTML<" not in SERIES_TPL

    def test_the_download_beside_it_is_untouched(self):
        assert ">Download HTML<" in SERIES_TPL


class TestExportFilenames:
    def test_it_carries_the_series_and_the_moment(self):
        assert export_filename("Autumn Series 2026", "results", "html", WHEN) == \
            "Autumn-Series-2026-results-2026-09-01-1642.html"

    def test_a_welsh_name_loses_its_circumflex_in_the_ascii_form(self):
        """The header's `filename=` field has to be ASCII or it arrives as
        mojibake; the real name goes in `filename*` alongside it."""
        assert export_filename("Gŵyl Hwylio", "results", "html", WHEN) == \
            "Gwyl-Hwylio-results-2026-09-01-1642.html"

    def test_and_keeps_it_in_the_utf8_form(self):
        out = export_filename("Gŵyl Hwylio", "results", "html", WHEN, ascii_only=False)
        assert out.startswith("Gŵyl-Hwylio-results-")

    def test_punctuation_cannot_reach_the_filesystem(self):
        out = export_filename("A/B: test", "results", "html", WHEN)
        assert "/" not in out and ":" not in out

    def test_even_in_the_utf8_form(self):
        """That one is not slugged, only stripped of what a filesystem refuses."""
        out = export_filename("A/B: test", "results", "html", WHEN, ascii_only=False)
        for bad in '\\/:*?"<>|':
            assert bad not in out, bad

    def test_a_nameless_series_still_gets_a_filename(self):
        assert export_filename("", "results", "html", WHEN) == \
            "export-results-2026-09-01-1642.html"

    def test_the_two_forms_agree_on_a_plain_ascii_name(self):
        """They are slugged by ONE rule, differing only in whether the accents
        survived. Slugging them separately -- collapsing punctuation in the ASCII
        form and not in the other -- made them disagree for a plain name, so the
        header carried a `filename*` contradicting its `filename`, and the
        browser preferred the worse of the two: `Cruisers---Race-2`, from a " - "
        the other branch had already collapsed."""
        for name in ("Welsh IRCs Cruisers - Race 2", "R9 / Summer", "A  B"):
            assert export_filename(name, "x", "csv", WHEN) == \
                export_filename(name, "x", "csv", WHEN, ascii_only=False), name

    def test_no_run_of_hyphens_survives(self):
        out = export_filename("Welsh IRCs Cruisers - Race 2", "irc-sailwave", "csv", WHEN)
        assert "--" not in out, out

    def test_no_spaces_survive(self):
        """They would need quoting in the header, and quoting is one more thing
        to get wrong for no gain."""
        assert " " not in export_filename("Autumn Series 2026", "results", "html", WHEN)


class TestWhatTheDownloadIsCalled:
    def test_the_html_download_is_named_for_the_series(self, logged_in_client):
        sid = _series("Autumn Series 2026")
        resp = logged_in_client.get(f"/admin/series/{sid}/publish.html?download=1")
        cd = resp.headers["Content-Disposition"]
        assert "Autumn-Series-2026-results-" in cd
        assert "series_%d_results_publish" % sid not in cd

    def test_it_carries_the_date_and_time(self, logged_in_client):
        sid = _series()
        cd = logged_in_client.get(
            f"/admin/series/{sid}/publish.html?download=1").headers["Content-Disposition"]
        assert re.search(r"results-\d{4}-\d{2}-\d{2}-\d{4}\.html", cd), cd

    def test_the_preview_is_not_a_download(self, logged_in_client):
        """Same route, and only `download=1` should attach it."""
        sid = _series()
        resp = logged_in_client.get(f"/admin/series/{sid}/publish.html")
        assert "Content-Disposition" not in resp.headers

    def test_the_sailwave_csv_is_named_the_same_way(self, logged_in_client):
        """It sat in the same folder as series_1_irc_sailwave.csv."""
        sid = _series("Autumn Series 2026")
        cd = logged_in_client.get(
            f"/admin/series/{sid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert "Autumn-Series-2026-irc-sailwave-" in cd

    def test_a_welsh_name_gets_both_forms(self, logged_in_client):
        sid = _series("Gŵyl Hwylio")
        cd = logged_in_client.get(
            f"/admin/series/{sid}/publish.html?download=1").headers["Content-Disposition"]
        assert 'filename="Gwyl-Hwylio-results-' in cd
        assert "filename*=UTF-8''" in cd
        assert "G%C5%B5yl" in cd

    def test_a_plain_name_needs_only_one_form(self, logged_in_client):
        """The two would be identical, and a header repeating itself invites the
        question of which one is right."""
        sid = _series("Autumn Series 2026")
        cd = logged_in_client.get(
            f"/admin/series/{sid}/publish.html?download=1").headers["Content-Disposition"]
        assert "filename*" not in cd


class TestTheRaceSheetExportIsNamedToo:
    """`race_9_irc_sailwave.csv` names a row id, which is nobody's idea of a
    race. The race sheet's two Sailwave buttons sit in the same downloads folder
    as the series ones and were the odd pair out."""

    @staticmethod
    def _race(name, series_id=None):
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, series_id, course_no, course_set, start_time,"
                " notes, created_at) VALUES (?,?,1,1,'','',?)", (name, series_id, now))
            db.commit()
            return int(cur.lastrowid)

    def test_it_carries_the_race_name_and_the_moment(self, logged_in_client):
        rid = self._race("R9 Summer")
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert "R9-Summer-irc-sailwave-" in cd
        assert re.search(r"-\d{4}-\d{2}-\d{2}-\d{4}\.csv", cd), cd

    def test_and_no_longer_the_row_id(self, logged_in_client):
        rid = self._race("R9 Summer")
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert f"race_{rid}_irc_sailwave" not in cd

    def test_the_series_is_prefixed_when_it_adds_something(self, logged_in_client):
        """Half the club's races are called R1; on its own that names nothing."""
        sid = _series("Autumn Series 2026")
        rid = self._race("R1", series_id=sid)
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=ytc").headers["Content-Disposition"]
        assert "Autumn-Series-2026-R1-ytc-sailwave-" in cd

    def test_but_not_when_the_race_name_already_carries_it(self, logged_in_client):
        """The club really does name them this way, and the plain join gave
        `Welsh-IRCs-YTC-Cruisers-Welsh-IRCs-Cruisers-Race-2-...`."""
        sid = _series("Welsh IRCs Cruisers")
        rid = self._race("Welsh IRCs Cruisers - Race 2", series_id=sid)
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert cd.count("Welsh-IRCs-Cruisers") == 1, cd

    def test_a_shared_year_does_not_count_as_having_said_it(self, logged_in_client):
        """The overlap test is letters only, three or more. A series and a race
        that merely share "2026" have not named each other."""
        sid = _series("Summer 2026")
        rid = self._race("Race 3 2026", series_id=sid)
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert "Summer-2026-Race-3-2026-" in cd, cd

    def test_a_standalone_race_needs_no_prefix(self, logged_in_client):
        rid = self._race("Practice Race")
        cd = logged_in_client.get(
            f"/admin/race/{rid}/results.csv?rating=irc").headers["Content-Disposition"]
        assert 'filename="Practice-Race-irc-sailwave-' in cd


class TestOpeningARaceLandsAtTheTop:
    def test_the_fragment_is_not_written_on_load(self):
        assert "show((location.hash || '#tab-course').slice(1), false);" in RACE_TPL

    def test_but_clicking_a_tab_still_remembers_it(self):
        assert "show(b.dataset.tab, true)" in RACE_TPL

    def test_the_write_is_behind_the_flag(self):
        """Not merely passed: the parameter has to gate the replaceState, or the
        first load writes the fragment anyway."""
        body = RACE_TPL.split("function show(tabId, rememberInUrl)")[1][:400]
        assert "rememberInUrl && location.hash" in body, body

    def test_a_deep_link_still_opens_its_tab(self, logged_in_client):
        """The redirect after saving the Entries tab asks for #tab-admin and
        means it; only the fragment the page writes to itself was the problem."""
        assert "(location.hash || '#tab-course')" in RACE_TPL


class TestTheSideMenuOrder:
    def test_trackers_comes_before_hut_power(self):
        bottom = BASE_TPL.split('class="side-nav side-nav-bottom"')[1]
        assert bottom.index("Trackers") < bottom.index("Hut power")

    def test_and_both_are_still_there(self):
        bottom = BASE_TPL.split('class="side-nav side-nav-bottom"')[1]
        for item in ("Trackers", "Hut power", "Documentation", "Settings", "Backup / restore"):
            assert item in bottom, item
