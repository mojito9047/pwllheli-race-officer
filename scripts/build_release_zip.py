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
EXCLUDE_DIRS = {".venv", ".claude", "runtime", "__pycache__", ".pytest_cache", ".git", "video_clips", "odm_frames", "scripts"}
# docs/hardware holds the start-hut vendor PDF manuals (~84 MB). They are reference
# material for physically installing the hut, not needed to run the app, and dominate
# the download — keep them version-controlled in the repo but out of the release ZIP.
EXCLUDE_REL_DIRS = {os.path.join("docs", "hardware"),
                    # docs/Trackers is the same kind of thing: vendor protocol and
                    # command manuals for the GL521M and LL301 trackers (47 MB).
                    # Reference material for configuring the units, not needed to run
                    # the app, and it would more than quadruple the hut's download.
                    os.path.join("docs", "Trackers")}
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
EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".zip")
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
        for d in pruned:
            excluded_dirs_hit.add(str(rel_root / d))

        for f in files:
            rel_path = rel_root / f if str(rel_root) != "." else Path(f)
            rel_str = str(rel_path)
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
