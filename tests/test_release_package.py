"""What the release ZIP must not contain.

The packaging script excludes files by suffix, and ".db" does not catch a SQLite
sidecar: "race_officer.db-wal" does not end in ".db". So a developer whose local
database had been in WAL mode shipped data/race_officer.db-wal and -shm in the
v0.241 candidate while the database itself was correctly excluded.

That is worse than untidy. A stale WAL beside a database is replayed over it on
the next open — the same mechanism recorded in core/db.py — so an install could
come up with somebody else's pages laid over a fresh database.

These check the exclusion rules rather than building a 14 MB ZIP.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "build_release_zip.py"

# Literal exclusion constants, read with ast rather than by importing the script:
# importing it would run the packaging and write a 14 MB ZIP.
_LITERAL_RULES = ("EXCLUDE_FILE_SUFFIXES", "EXCLUDE_NAME_CONTAINS", "EXCLUDE_DIRS",
                  "EXCLUDE_FILENAMES")


def _rules():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in _LITERAL_RULES:
                found[target.id] = ast.literal_eval(node.value)
    missing = set(_LITERAL_RULES) - set(found)
    assert not missing, f"the packaging script no longer defines {missing}"
    return found


def _excluded(name: str) -> bool:
    rules = _rules()
    return (name.endswith(tuple(rules["EXCLUDE_FILE_SUFFIXES"]))
            or any(part in name for part in rules["EXCLUDE_NAME_CONTAINS"])
            or name in rules["EXCLUDE_FILENAMES"])


class TestNoDatabaseEverShips:
    @pytest.mark.parametrize("name", [
        "race_officer.db", "track_positions.db", "power_history.db",
    ])
    def test_the_databases_are_excluded(self, name):
        assert _excluded(name)

    @pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
    def test_and_so_are_their_sidecars(self, suffix):
        """The bug: ".db" does not match "race_officer.db-wal"."""
        assert _excluded(f"race_officer.db{suffix}"), \
            f"a stale {suffix} file would be replayed over a fresh install's database"

    @pytest.mark.parametrize("name", [
        "race_officer.db.devcopy", "track_positions.db.devcopy", "power_history.db.devcopy",
    ])
    def test_and_so_are_the_developer_copies_kept_beside_them(self, name):
        """scripts/use_hut_data.py parks the developer's own databases as
        race_officer.db.devcopy while the hut's data is in place. That ends in
        ".devcopy", so ".db" misses it exactly as it missed "-wal" -- and a
        release cut on that machine would ship a whole club database."""
        assert _excluded(name)

    @pytest.mark.parametrize("name", [
        "race_officer.db.before-v0248-finish-fix",   # sitting in data/ right now
        "race_officer.db.bak", "track_positions.db.2026-08-18",
    ])
    def test_and_so_is_a_database_saved_under_any_name_at_all(self, name):
        """Three suffix rules in a row missed a database somebody had renamed, so
        the rule is now the thing rather than the spelling: ".db" anywhere in the
        name means it is one, and no database ships."""
        assert _excluded(name)

    def test_the_runtime_folder_is_excluded_so_no_secret_key_or_password_ships(self):
        """runtime/ holds secret_key.txt and initial_admin_password.txt."""
        assert "runtime" in _rules()["EXCLUDE_DIRS"]

    def test_saved_video_clips_do_not_ship(self):
        assert "video_clips" in _rules()["EXCLUDE_DIRS"]

    def test_a_nested_zip_does_not_ship(self):
        """The repo root holds every previous release ZIP."""
        assert _excluded("pwllheli_ro_mvp_v0_240.zip")


class TestVendorManualsDoNotShip:
    """Two directories of manufacturer PDFs live in the repo as reference material —
    84 MB for the start-hut hardware and 47 MB of tracker protocol manuals. Neither is
    needed to run the app, and together they would be six times the size of everything
    else in the download, over the hut's 4G link.

    EXCLUDE_REL_DIRS is built with os.path.join, so it cannot be read with
    ast.literal_eval like the other rules; assert on the assignment's source instead.
    """

    def _exclude_rel_dirs_source(self):
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "EXCLUDE_REL_DIRS" in source, "the packaging script no longer defines it"
        after = source.split("EXCLUDE_REL_DIRS", 1)[1]
        return after.split("EXCLUDE_FILES", 1)[0]

    @pytest.mark.parametrize("directory", ["hardware", "Trackers"])
    def test_it_is_excluded(self, directory):
        assert f'"{directory}"' in self._exclude_rel_dirs_source()

    def test_the_directories_are_where_the_rule_expects(self):
        """A renamed folder would leave the rule matching nothing, silently."""
        for directory in ("hardware", "Trackers"):
            path = _ROOT / "docs" / directory
            if not path.exists():
                pytest.skip(f"docs/{directory} is not in this checkout")
            assert path.is_dir()

    def test_the_rule_is_applied_to_directories_as_it_walks(self):
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "rel_dir in EXCLUDE_REL_DIRS" in source, (
            "the exclusion list is defined but no longer consulted")
