"""The README should describe the app as it is now.

It had grown to 413 lines, 250 of which were 56 release notes going back to
v0.165 — a second copy of the changelog sitting above anything that said what the
thing was. Underneath that, the feature list still described the app of some
months ago: no pursuit races, no shortened courses, no hut power, no weather
station, no backup, no clubhouse display, and no sign of the chart replay or the
corrected-time leaderboards.

These check the parts that rot silently: the version, the length of the release
list, and whether every path it names actually exists.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (_ROOT / "README.md").read_text(encoding="utf-8")
VERSION = (_ROOT / "VERSION").read_text(encoding="utf-8").strip()


class TestItSaysWhichVersionItIs:
    def test_the_title_matches_the_version_file(self):
        assert README.splitlines()[0].strip() == f"# Pwllheli Race Officer v{VERSION}"

    def test_the_newest_release_note_is_this_version(self):
        first = re.search(r'^### v([\d.]+) update', README, re.M)
        assert first and first.group(1) == VERSION


class TestTheReleaseListStaysShort:
    """The changelog has its own file; the README carries the last few."""

    def test_it_holds_no_more_than_a_handful(self):
        entries = re.findall(r'^### v[\d.]+ update', README, re.M)
        assert 1 <= len(entries) <= 5, f"{len(entries)} release notes in the README"

    def test_it_points_at_the_changelog_for_the_rest(self):
        assert "docs/CHANGELOG.md" in README


class TestEverythingItNamesExists:
    def test_no_link_is_broken(self):
        broken = [t for t in re.findall(r'\]\(((?:docs|deploy|scripts)/[^)#]+)\)', README)
                  if not (_ROOT / t).exists()]
        assert not broken

    def test_the_folder_tree_is_real(self):
        block = README.split("## Important files and folders")[1].split("```")[1]
        missing = []
        for line in block.splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0].rstrip("/")
            if ("/" in parts[0] or name.endswith(".py")) and not (_ROOT / name).exists():
                missing.append(name)
        assert not missing


class TestTheFeatureListCoversWhatTheAppDoes:
    """Not exhaustive — just the things that were missing, so a reader forming a
    first impression is not told about half the app."""

    def test_it_mentions_the_features_it_used_to_omit(self):
        features = README.split("## Main features")[1].split("## Quick start")[0].lower()
        for word in ("pursuit", "shorten", "power", "weather station", "backup",
                     "/bar", "corrected", "activity log"):
            assert word in features, f"the feature list never mentions {word!r}"


class TestItExplainsTheRelay:
    """The app is one PC in a hut, but four of its features reach past that PC and
    all of them go through a second machine. The README described the app alone
    and mentioned the relay only as a link in the documentation list, so a reader
    had no way to know a second machine existed, let alone what it is for.
    """

    SECTION = "## What runs where"

    def _section(self):
        assert self.SECTION in README
        return README.split(self.SECTION)[1].split("\n## ")[0]

    def test_it_says_what_the_relay_does(self):
        s = self._section().lower()
        for job in ("front door", "camera", "traccar"):
            assert job in s, f"the relay section never mentions {job!r}"

    def test_it_says_the_hut_opens_nothing_inbound(self):
        """The single fact that explains why the arrangement is shaped this way."""
        assert "opens nothing inbound" in self._section()

    def test_it_says_what_still_works_without_one(self):
        """Otherwise a reader cannot tell whether a relay is a prerequisite."""
        assert "Without a relay" in self._section()

    def test_the_diagram_lines_up(self):
        """A box drawn with mismatched widths reads as carelessness.

        Only the box itself: the connector arrows below it legitimately start
        with the same characters and are not meant to be the same width.
        """
        block = self._section().split("```text\n")[1].split("```")[0]
        lines = block.splitlines()
        # A border is +---...---+ and nothing else; "+-- the hut camera" is a
        # connector, not a box edge.
        edges = [i for i, ln in enumerate(lines) if re.fullmatch(r'\+-+\+', ln.strip())]
        assert len(edges) == 2, "expected a top and a bottom border"
        box = lines[edges[0]:edges[1] + 1]
        widths = sorted(set(len(ln) for ln in box))
        assert len(widths) == 1, f"box line widths differ: {widths}"

    def test_it_points_at_both_the_guide_and_the_working_copy(self):
        s = self._section()
        assert "Pwllheli_Relay_Guide.pdf" in s and "deploy/live_stream/README.md" in s
