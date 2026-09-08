"""What the markdown guides say, checked against what the code does.

`test_pdf_prose.py` does this for the four built PDFs. It found nothing here,
because the twenty-odd `docs/*.md` files are a separate body of prose with the
same failure mode and nothing checking it either. A complete read of them turned
up: the pre-v0.258 rounding rule still stated as fact in two documents, a setting
(`RO_TRACK_GATE_REACH_M`) that existed in the code and in no document at all, six
`core/` modules missing from the developer map, and a doc that contradicted
itself about whether competitors can see GPS tracking.

Two kinds of check live here:

* **Numbers and rules** copied out of the code, which go stale silently.
* **Coverage**, which is the one that caught the most: a new module or a new
  setting is exactly the sort of thing that ships without a line of documentation,
  and nobody notices because nothing was wrong — something was simply absent.

`docs/CHANGELOG.md` is deliberately exempt. It is a record of what was true at
each release, so a superseded statement in it is correct history, not drift.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from core import rounding, track

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

# The CHANGELOG records what was true when each version shipped; a rule that has
# since changed is history there rather than an error.
FROZEN = {"CHANGELOG.md"}


def _live_docs() -> dict[str, str]:
    out = {}
    for p in sorted(DOCS.glob("*.md")):
        if p.name not in FROZEN:
            out[f"docs/{p.name}"] = p.read_text(encoding="utf-8")
    for extra in ("README.md", "scripts/README.md", "deploy/windows/README.md",
                  "deploy/live_stream/README.md"):
        p = REPO / extra
        if p.exists():
            out[extra] = p.read_text(encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def docs():
    return _live_docs()


class TestTheRoundingRuleIsStatedOnce:
    """One rule, described the same way everywhere it is described at all."""

    def test_no_live_doc_describes_rounding_as_the_radius_alone(self, docs):
        """This survived in TROUBLESHOOTING and TRACKING for four releases after
        the gate went in — including as the headline diagnosis of a
        troubleshooting entry, which sends a volunteer out in a RIB to re-measure
        a mark that is where it should be."""
        for name, text in docs.items():
            assert "judged within a radius" not in text, \
                f"{name} still describes rounding as the radius alone"

    def test_the_gate_reach_quoted_matches_the_code(self, docs):
        reach = int(rounding.DEFAULT_GATE_REACH_M)
        quoted = {name for name, text in docs.items() if re.search(r"\b750\s*m\b", text)}
        assert quoted, f"no document quotes the {reach} m gate reach"
        for name, text in docs.items():
            for wrong in re.findall(r"gate reach[^.]{0,40}?(\d{3,4})\s*m", text):
                assert int(wrong) == reach, f"{name} quotes a gate reach of {wrong} m"


class TestNumbersCopiedOutOfTheCode:
    def test_the_recent_pace_window_is_not_still_five_minutes(self, docs):
        """It was five, and is twenty. `scripts/README.md` still said five while
        explaining why a compressed simulated race would not exercise it."""
        minutes = int(track.VMC_WINDOW_S // 60)
        assert minutes == 20, "update the wording below if the window changes"
        for name, text in docs.items():
            assert not re.search(r"VMC over five|averag\w+ VMC over five", text), \
                f"{name} still says the VMC window is five minutes"

    def test_the_estimate_hold_is_quoted_as_ten_minutes(self, docs):
        minutes = int(track.ESTIMATE_AFTER_S // 60)
        word = {5: "five", 10: "ten", 15: "fifteen", 20: "twenty"}.get(minutes, str(minutes))
        text = docs["docs/PUBLIC_COMPETITOR_PAGE.md"]
        assert f"first {word} minutes of racing" in text, \
            f"the competitor-page doc should say estimates start after {word} minutes"


class TestEverythingInTheCodeIsDescribedSomewhere:
    """Absence is the failure mode nothing else catches."""

    def test_every_core_module_is_in_the_developer_map(self, docs):
        notes = docs["docs/DEVELOPER_NOTES.md"]
        missing = [p.name for p in sorted((REPO / "core").glob("*.py"))
                   if p.name != "__init__.py" and f"core/{p.name}" not in notes]
        assert not missing, f"core modules missing from DEVELOPER_NOTES.md: {missing}"

    def test_every_environment_variable_is_documented(self, docs):
        """RO_TRACK_GATE_REACH_M shipped in v0.258 and appeared in no document —
        neither the installation guide's list nor the manual's reference table."""
        code = set()
        for folder in ("core", "routes"):
            for p in (REPO / folder).glob("*.py"):
                code |= set(re.findall(r"RO_[A-Z0-9_]+", p.read_text(encoding="utf-8")))
        code |= set(re.findall(r"RO_[A-Z0-9_]+", (REPO / "app.py").read_text(encoding="utf-8")))
        described = set()
        for text in docs.values():
            described |= set(re.findall(r"RO_[A-Z0-9_]+", text))
        # The reference manual's table is built from a script, not a doc file.
        builder = (REPO / "scripts" / "build_reference_manual.py").read_text(encoding="utf-8")
        described |= set(re.findall(r"RO_[A-Z0-9_]+", builder))
        assert not (code - described), f"environment variables documented nowhere: {sorted(code - described)}"

    def test_every_pdf_builder_is_named_in_the_release_pipeline(self, docs):
        """A builder left out of the checklist is a guide that stops being
        rebuilt, which is how one drifts."""
        notes = docs["docs/DEVELOPER_NOTES.md"]
        for builder in sorted((REPO / "scripts").glob("build_*guide.py")):
            assert builder.name in notes, f"{builder.name} is not in the release pipeline notes"
        assert "build_reference_manual.py" in notes


class TestTheWorkedExamplesAreCurrent:
    def test_the_windows_guides_name_this_release_folder(self, docs):
        """The beginner guide walks someone through unpacking a release and told
        them to expect `..._v0_193` while the app was at 0.262. It does say to use
        your own folder name, but a first-time installer reading a number that
        does not match what they just extracted has no way to know which is wrong.

        If this fails after a version bump, it is a one-line fix:
            python - <<'X'
            import pathlib, re
            v = pathlib.Path("VERSION").read_text().strip().replace(".", "_")
            for f in ("docs/GETTING_STARTED_WINDOWS.md", "docs/DEPLOYMENT_WINDOWS.md"):
                p = pathlib.Path(f)
                p.write_text(re.sub(r"pwllheli_[a-z_]+_v[\\d_]+", f"pwllheli_race_officer_v{v}",
                                    p.read_text(encoding="utf-8")), encoding="utf-8")
            X
        """
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        folder = "pwllheli_race_officer_v" + version.replace(".", "_")
        for name in ("docs/GETTING_STARTED_WINDOWS.md", "docs/DEPLOYMENT_WINDOWS.md"):
            stale = {m for m in re.findall(r"pwllheli_(?:ro_mvp|race_officer)_v[\d_]+", docs[name]) if m != folder}
            assert not stale, f"{name} names {sorted(stale)}; this release unpacks into {folder}"


class TestTheDocsDoNotContradictEachOther:
    def test_no_doc_claims_tracking_is_race_office_only(self, docs):
        """Competitors have seen the fleet since v0.177. RACE_OFFICER_WORKFLOW.md
        said 'race-office only — it is not shown on the public competitor pages'
        seven lines above a bullet describing what competitors see."""
        for name, text in docs.items():
            assert "not shown on the public competitor pages" not in text, \
                f"{name} still says tracking is hidden from competitors"

    def test_no_doc_offers_a_per_race_tracker_dropdown(self, docs):
        """Removed in v0.207, leaving the Trackers page as the only place a
        pairing is set — but TRACKING.md still gave instructions for using it,
        contradicting its own paragraph saying it was gone."""
        for name, text in docs.items():
            assert "auto (boat's)" not in text, \
                f"{name} still describes the per-race loaner tracker dropdown"

    def test_settings_sections_are_named_as_the_app_names_them(self, docs):
        """'Settings → Video & camera' is a section that does not exist; the
        app calls it Video Recording. A wrong path reads as authoritative and
        sends the reader hunting."""
        for name, text in docs.items():
            assert "Video &amp; camera" not in text and "Video & camera" not in text, \
                f"{name} names a Settings section that does not exist"
