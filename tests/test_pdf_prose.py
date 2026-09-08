"""What the guides say, checked against what the code does.

The PDFs drift, and the reason is structural: they restate facts that live in the
code, and until now nothing compared the two. `test_pdf_versions.py` checks the
cover stamp is generated rather than typed — the v0.254 failure — and nothing
checked a word of the prose.

Left alone, that produces contradictions rather than mere staleness. The reference
manual said "a mark counts as rounded **only if** the boat passes inside its
rounding radius" twenty-one lines above a note added in v0.258 saying three tests
decide it, any one of them enough. Both statements shipped in the same PDF. The
habit that caused it is correcting by appending a note instead of editing the
sentence that is wrong.

These read the built PDFs, not the builder scripts, so a guide that has not been
rebuilt since the code changed fails here. That is the point: the release process
rebuilds them, and this is what makes forgetting visible.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pypdf = pytest.importorskip("pypdf", reason="pypdf reads the built guides back")

from core import rounding, track

# The guides spell small numbers out, which reads better and would defeat a check
# that only looked for digits — so a number is matched in either form.
WORDS = {5: "five", 10: "ten", 15: "fifteen", 20: "twenty", 30: "thirty", 60: "sixty"}


def says_minutes(text: str, minutes: int) -> bool:
    return f"{minutes} minutes" in text or f"{WORDS.get(minutes, minutes)} minutes" in text


DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
GUIDES = {
    "competitor": "Pwllheli_Competitor_Guide.pdf",
    "reference": "Pwllheli_Race_Officer_Reference_Manual.pdf",
    "series": "Pwllheli_Race_Officer_Series_Guide.pdf",
    "relay": "Pwllheli_Relay_Guide.pdf",
}


def _text(name: str) -> str:
    path = DOCS / GUIDES[name]
    assert path.exists(), f"{path} is missing; the release process builds it"
    reader = pypdf.PdfReader(str(path))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


@pytest.fixture(scope="module")
def guides():
    return {name: _text(name) for name in GUIDES}


class TestTheGuidesWereBuiltFromThisCode:
    def test_every_guide_carries_this_version(self, guides):
        version = (DOCS.parent / "VERSION").read_text(encoding="utf-8").strip()
        for name, text in guides.items():
            assert f"Covers app version {version}" in text, \
                f"the {name} guide was not rebuilt for v{version}"


class TestNumbersInTheProseMatchTheCode:
    """A number written into a guide is a copy of one in the code."""

    def test_the_recent_pace_window_is_not_described_as_five_minutes(self, guides):
        """It was five, and is twenty. Five was short enough to produce a finish
        estimate of 32218 minutes when a boat momentarily stopped closing."""
        minutes = int(track.VMC_WINDOW_S // 60)
        for name, text in guides.items():
            stale = re.search(r"VMC[^.]{0,80}five minutes|averaged over five minutes", text)
            assert not stale, f"the {name} guide still says the window is five minutes"
        assert says_minutes(guides["competitor"], minutes), \
            f"the competitor guide should say the recent-pace window is {minutes} minutes"

    def test_the_gate_reach_quoted_is_the_default_the_code_uses(self, guides):
        reach = int(rounding.DEFAULT_GATE_REACH_M)
        assert f"{reach} m" in guides["reference"], \
            f"the reference manual should quote the {reach} m gate reach"

    def test_the_estimate_hold_quoted_matches_the_code(self, guides):
        minutes = int(track.ESTIMATE_AFTER_S // 60)
        word = {10: "ten", 15: "fifteen", 5: "five"}.get(minutes, str(minutes))
        assert f"{word} minutes" in guides["competitor"], \
            f"the competitor guide should say estimates start after {word} minutes"


class TestTheGuidesDescribeTodaysBehaviour:
    def test_rounding_is_not_described_as_the_radius_alone(self, guides):
        """The contradiction this file was written for.

        Two phrasings, because pinning the exact sentence I had just fixed
        caught nothing: the marks chapter said the same thing in its own words
        ("rounding is judged within a radius") a whole part earlier, and passed.
        """
        for name, text in guides.items():
            for phrasing in ("counts as rounded only if the boat passes inside",
                             "Rounding is judged within a radius"):
                assert phrasing not in text, \
                    f"the {name} guide still describes rounding as the radius alone"

    def test_the_wind_kept_for_a_race_is_not_described_as_pruned_after_a_day(self, guides):
        """A race's own wind is kept indefinitely; only ambient samples are
        trimmed. The replay gauge reads it back long after the day it blew."""
        assert "pruned after 24 hours) feeding" not in guides["reference"], \
            "the weather chapter still says every sample is pruned after 24 hours"
        assert "hour either side of a race is kept indefinitely" in guides["reference"], \
            "the weather chapter should say a race's own wind is kept"

    def test_the_install_chapter_names_this_release_folder(self, guides):
        """It said v0_131 until v0.262 — the first page a new installer reads."""
        version = (DOCS.parent / "VERSION").read_text(encoding="utf-8").strip()
        folder = "pwllheli_race_officer_v" + version.replace(".", "_")
        assert folder in guides["reference"], \
            f"the install chapter should unpack into {folder}"

    def test_the_reference_manual_states_all_three_rounding_tests(self, guides):
        text = guides["reference"]
        assert "Three tests decide whether a boat has rounded" in text
        for phrase in ("rounding radius", "gate", "Closest approach"):
            assert phrase in text, f"the rounding section never mentions {phrase!r}"

    def test_a_suspected_wrong_side_is_documented_as_never_refused(self, guides):
        """Because the alternative — stalling the boat — is what the race officer
        would otherwise have to undo by hand, and the app does not adjudicate."""
        assert "never refused" in guides["reference"]

    def test_the_competitor_guide_names_every_estimate_method_on_offer(self, guides):
        text = guides["competitor"]
        for phrase in ("Average pace since the start", "Recent pace", "Polar pace factor"):
            assert phrase in text, f"the competitor guide never offers {phrase!r}"

    def test_the_competitor_guide_says_one_method_drives_order_and_times(self, guides):
        """A board ranked by one estimator and timed by another contradicts itself."""
        assert "order and the times printed beside it" in guides["competitor"]

    def test_the_series_guide_offers_the_same_three_methods(self, guides):
        """It described the pre-v0.258 pair — polar or recent pace — and omitted
        the one the board actually opens on. Two guides, two answers."""
        text = guides["series"]
        for phrase in ("average pace since the start", "recent pace", "polar pace"):
            assert phrase in text.lower(), f"the series guide never offers {phrase!r}"

    def test_the_series_guide_states_the_rounding_gate(self, guides):
        """It described only the closest-approach test, so the race officer —
        the one person who fields 'why did that count?' — was not told the app
        knows a mark has a required side."""
        text = guides["series"]
        assert "three tests" in text.lower(), "the series guide does not say three tests decide a rounding"
        assert "required side" in text, "the series guide never mentions the required side"

    def test_the_competitor_guide_quotes_the_real_trail_length(self, guides):
        """It said a quarter of an hour, which is the clubhouse display's figure;
        the competitor chart draws ten minutes.

        Written first as `says_minutes(guide, 10)`, which passed against the
        wrong text: the guide says "ten minutes" elsewhere, about when estimates
        start. A number check has to be anchored to the sentence that carries it.
        """
        js = (DOCS.parent / "static" / "race_replay.js").read_text(encoding="utf-8")
        seconds = int(re.search(r"TRAIL_SECONDS\s*=\s*(\d+)", js).group(1))
        trail = re.search(r"trail of where it has been for the last ([\w\s]+?)[\.,]",
                          guides["competitor"])
        assert trail, "the competitor guide no longer describes the boat trail"
        assert says_minutes(trail.group(0), seconds // 60), \
            f"the guide says the trail is '{trail.group(1)}'; the chart draws {seconds // 60} minutes"


class TestTheGuidesNameControlsAsTheAppDoes:
    """A worked example that names a button which no longer exists reads as
    authoritative and sends the reader hunting for it."""

    def test_the_series_guide_numbers_the_race_tabs_as_the_app_does(self, guides):
        """`Shorten course` was inserted as tab 4 and the chapters after it were
        never renumbered, so the guide sent the reader to 4. Entries, 5. Results
        and 6. Remove Race — each one tab out."""
        race_html = (DOCS.parent / "templates" / "race.html").read_text(encoding="utf-8")
        tabs = dict(re.findall(r">(\d)\.\s([A-Za-z][A-Za-z &]+?)<", race_html))
        assert tabs, "could not read the tab numbering out of race.html"
        text = guides["series"]
        for number, label in tabs.items():
            plain = label.replace("&", "&")
            for wrong, name in re.findall(r"(\d)\.\s(" + re.escape(plain) + r")", text):
                assert wrong == number, \
                    f"the series guide calls {name!r} tab {wrong}; the app calls it tab {number}"

    def test_no_guide_offers_a_plain_download_csv_on_the_series_page(self, guides):
        """The exports became Sailwave import files in v0.243 and the buttons
        say so; the series guide still called it a spreadsheet download."""
        assert "Download CSV" not in guides["series"], \
            "the series guide still names a Download CSV button; the app has Sailwave CSV (IRC)/(YTC)"
