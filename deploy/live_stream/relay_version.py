#!/usr/bin/env python3
"""Which relay files this relay is running, for the hut's dashboard.

Writes /opt/relay/version.json: a SHA-256 of each file the repository supplies
for the relay, as installed here. The hut compares them with its own copy of
the same files from its release, so the dashboard can say "the relay matches
v1.020" or "relay_wind.py differs from v1.020". A version number written down
at install time would say the same thing until the first file was copied over
by hand -- which is how this relay is updated -- and then be wrong without
anyone knowing.

Run hourly by relay-version.timer, and by setup.sh after an install. Caddy
serves the result at /relay/version.json.

Deliberately not in it:

* the Caddyfile and startline_config.json, which carry local edits by design
  (a /stats password, a camera's own tuning), so they would always "differ";
* relay.env, which holds the tunnel and Access secrets;
* the versions of Caddy, MediaMTX, cloudflared and ffmpeg. The file is public,
  and a list of exact versions is a gift to anybody looking for a known hole.

Line endings are normalised before hashing: the hut's copies come out of a
release ZIP built on Windows and these from a Linux checkout, and a file that
differs only in CRLF is the same file.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

OUT = os.environ.get("RELAY_VERSION_FILE", "/opt/relay/version.json")

# The file's path in the repository's deploy/live_stream, and where it is installed.
FILES = {
    "mediamtx.yml": "/opt/relay/mediamtx.yml",
    "relay_branded_source.py": "/opt/relay/relay_branded_source.py",
    "relay_wind.py": "/opt/relay/relay_wind.py",
    "relay_startline.py": "/opt/relay/relay_startline.py",
    "odm_detector.py": "/opt/relay/odm_detector.py",
    "relay_version.py": "/opt/relay/relay_version.py",
    "site/index.html": "/opt/relay/site/index.html",
    "site/hut_offline.html": "/opt/relay/site/hut_offline.html",
    "lxc/systemd/mediamtx.service": "/etc/systemd/system/mediamtx.service",
    "lxc/systemd/cloudflared-tunnel.service": "/etc/systemd/system/cloudflared-tunnel.service",
    "lxc/systemd/cloudflared-camera.service": "/etc/systemd/system/cloudflared-camera.service",
    "lxc/systemd/caddy-relay-env.conf": "/etc/systemd/system/caddy.service.d/relay-env.conf",
    "lxc/systemd/relay-version.service": "/etc/systemd/system/relay-version.service",
    "lxc/systemd/relay-version.timer": "/etc/systemd/system/relay-version.timer",
}
# Only there when somebody set up the optional visitor report; absent is not a fault.
OPTIONAL = {
    "relay_stats.sh": "/opt/relay/relay_stats.sh",
}


def file_hash(path):
    """SHA-256 of a text file with its line endings made LF, or None if it is not there."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def inventory():
    files = {name: file_hash(path) for name, path in FILES.items()}
    for name, path in OPTIONAL.items():
        digest = file_hash(path)
        if digest is not None:
            files[name] = digest
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
    }


def main():
    body = json.dumps(inventory(), indent=2, sort_keys=True) + "\n"
    tmp = OUT + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(tmp, 0o644)              # Caddy reads it as its own user
    os.replace(tmp, OUT)
    print(f"relay_version: {len(json.loads(body)['files'])} files listed in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
