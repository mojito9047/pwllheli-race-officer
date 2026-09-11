import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
import os
import zipfile
from pathlib import Path

SRC = Path(_REPO)
VERSION = (SRC / "VERSION").read_text(encoding="utf-8").strip()
TOP_FOLDER = "pwllheli_race_officer_v" + VERSION.replace(".", "_")
OUT = SRC / f"{TOP_FOLDER}.zip"

# deploy/ ships in the release (deploy/windows install scripts + deploy/live_stream
# relay setup). Only the usual runtime/build junk is excluded.
EXCLUDE_DIRS = {".venv", ".claude", "runtime", "__pycache__", ".pytest_cache", ".git", "video_clips", "odm_frames"}
# scripts/ is developer tooling -- PDF builders, screenshot capture, one-off
# analysis -- and does not ship. With one exception: scripts/replay3d is the 3D
# replay renderer, and a render machine is built from a release ZIP like
# everything else at this club rather than from a git checkout. Nobody setting
# up a spare desktop in a clubhouse should need git first.
#
# Two layers rather than one set, because the junk rules above must still win
# inside a kept folder: __pycache__ under scripts/replay3d is still junk.
PARTLY_EXCLUDED_DIRS = {"scripts"}
KEEP_REL_DIRS = {os.path.join("scripts", "replay3d")}


def _within(rel, roots):
    """Is this relative path one of ``roots``, or inside one?"""
    return any(rel == root or rel.startswith(root + os.sep) for root in roots)


def _leads_to_kept(rel):
    """Would pruning here also prune something we mean to keep?"""
    return any(keep == rel or keep.startswith(rel + os.sep) for keep in KEEP_REL_DIRS)
# docs/hardware holds the start-hut vendor PDF manuals (~84 MB). They are reference
# material for physically installing the hut, not needed to run the app, and dominate
# the download — keep them version-controlled in the repo but out of the release ZIP.
EXCLUDE_REL_DIRS = {os.path.join("docs", "hardware"),
                    # docs/Trackers is the same kind of thing: vendor protocol and
                    # command manuals for the GL521M and LL301 trackers (47 MB).
                    # Reference material for configuring the units, not needed to run
                    # the app, and it would more than quadruple the hut's download.
                    os.path.join("docs", "Trackers"),
                    # data/dem is scratch, not data: GeoTIFFs pulled by hand while
                    # the 3D replay was being built (a 9.6 MB elevation tile and two
                    # imagery mosaics, 30 MB together). It is untracked, so a clean
                    # clone has none of it and a developer who once ran those
                    # fetch scripts has all of it -- which is how a release built on
                    # the wrong machine goes from 14 MB to 44 MB over the hut's 4G
                    # link. Nothing installed reads it: the renderer keeps its tiles
                    # under runtime/replay3d/ and fetches what it is missing.
                    os.path.join("data", "dem")}
EXCLUDE_FILES = {
    str(Path("data") / "race_officer.db"),
}
# Naming a database by its suffix has now failed three times. ".db" did not catch
# "race_officer.db-wal", and a developer whose database had been in WAL mode shipped
# the sidecar while the database itself was correctly excluded -- a stale WAL beside
# a fresh install is replayed over it, the failure documented in core/db.py. Adding
# "-wal" and friends then did not catch "race_officer.db.devcopy", which
# scripts/use_hut_data.py parks beside the hut's data; and none of them caught
# "race_officer.db.before-v0248-finish-fix", a whole club database that has sat in
# data/ under a name nobody thought to list.
#
# So the rule is the thing itself rather than the spelling: a file with ".db"
# anywhere in its name is a database, whatever has been appended to it, and no
# database ships. Every such file in this repo is one.
EXCLUDE_NAME_CONTAINS = (".db",)
# ".env" is here because a filled-in one shipped in the v1.002 release: the
# render machine's renderer.env, with live R2 keys and a Mapbox token in it.
# It was in .gitignore, which was the mistake -- this script walks the working
# tree and has never consulted git, so "not committed" and "not published" are
# different questions and only the rules in this file answer the second.
# ".env.example" is deliberately not caught: it ends ".example" and is meant to
# ship. Anything holding real credentials must end ".env".
EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".zip", ".env")
EXCLUDE_FILENAMES = {".DS_Store", "Thumbs.db"}

included = []
excluded_dirs_hit = set()
excluded_files_hit = []

if OUT.exists():
    OUT.unlink()

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(SRC):
        rel_root = Path(root).relative_to(SRC)
        # prune excluded directories in-place so os.walk doesn't descend into them
        pruned = []
        for d in list(dirs):
            rel_dir = str(rel_root / d) if str(rel_root) != "." else d
            if d in EXCLUDE_DIRS or rel_dir in EXCLUDE_REL_DIRS:
                pruned.append(d)
                dirs.remove(d)
            elif _within(rel_dir, PARTLY_EXCLUDED_DIRS) and not _leads_to_kept(rel_dir):
                pruned.append(d)
                dirs.remove(d)
        for d in pruned:
            excluded_dirs_hit.add(str(rel_root / d))

        for f in files:
            rel_path = rel_root / f if str(rel_root) != "." else Path(f)
            rel_str = str(rel_path)
            # We now walk into scripts/ for the renderer, so the files sitting
            # directly in it have to be turned away by name rather than by
            # never being reached.
            if (_within(str(rel_root), PARTLY_EXCLUDED_DIRS)
                    and not _within(str(rel_root), KEEP_REL_DIRS)):
                excluded_files_hit.append(rel_str)
                continue
            if rel_str in EXCLUDE_FILES:
                excluded_files_hit.append(rel_str)
                continue
            if (f in EXCLUDE_FILENAMES or f.endswith(EXCLUDE_FILE_SUFFIXES)
                    or any(part in f for part in EXCLUDE_NAME_CONTAINS)):
                excluded_files_hit.append(rel_str)
                continue
            abs_path = SRC / rel_path
            arcname = str(Path(TOP_FOLDER) / rel_path)
            zf.write(abs_path, arcname)
            included.append(rel_str)

print(f"VERSION: {VERSION}")
print(f"Top folder: {TOP_FOLDER}")
print(f"Files included: {len(included)}")
print(f"Directories excluded: {sorted(excluded_dirs_hit)}")
print(f"Files excluded: {excluded_files_hit}")
print(f"Output zip: {OUT} ({OUT.stat().st_size / 1024 / 1024:.2f} MB)")
