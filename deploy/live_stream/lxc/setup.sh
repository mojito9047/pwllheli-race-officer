#!/usr/bin/env bash
# Turnkey installer for the hut live-stream relay in a native Debian/Ubuntu LXC
# (no Docker). Run as root inside a fresh container:
#     bash lxc/setup.sh
# Installs MediaMTX, Caddy, cloudflared, ffmpeg + python3, Traccar (GPS
# tracking), drops in the shared config/site files, and installs the systemd
# services. Reuses the same
# Caddyfile / mediamtx.yml / relay_branded_source.py / site as the other deploy
# methods. Idempotent-ish: safe to re-run.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run as root"; exit 1; fi

RELAY_DIR=/opt/relay
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # deploy/live_stream

echo "== packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ffmpeg python3 python3-numpy python3-pil curl gnupg debian-keyring debian-archive-keyring apt-transport-https tar unzip

echo "== Caddy =="
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

echo "== cloudflared =="
if ! command -v cloudflared >/dev/null 2>&1; then
  ARCH="$(dpkg --print-architecture)"
  curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" -o /tmp/cloudflared.deb
  apt-get install -y /tmp/cloudflared.deb
fi

echo "== MediaMTX =="
mkdir -p "$RELAY_DIR"
if [ ! -x "$RELAY_DIR/mediamtx" ]; then
  case "$(uname -m)" in
    x86_64)  MTX_ARCH=amd64 ;;
    aarch64) MTX_ARCH=arm64v8 ;;
    armv7l)  MTX_ARCH=armv7 ;;
    *) echo "unsupported arch $(uname -m)"; exit 1 ;;
  esac
  VER="$(curl -sL https://api.github.com/repos/bluenviron/mediamtx/releases/latest | grep -oP '"tag_name":\s*"\K[^"]+')"
  curl -L "https://github.com/bluenviron/mediamtx/releases/download/${VER}/mediamtx_${VER}_linux_${MTX_ARCH}.tar.gz" -o /tmp/mediamtx.tar.gz
  tar -xzf /tmp/mediamtx.tar.gz -C "$RELAY_DIR" mediamtx
  chmod +x "$RELAY_DIR/mediamtx"
fi

echo "== Traccar (GPS tracking) =="
# Traccar ships its own self-contained installer (bundles a JRE) that creates and
# enables the traccar.service unit. Non-fatal: a failure here must not abort the
# live-stream relay setup. Skipped if /opt/traccar already exists.
if [ ! -d /opt/traccar ]; then
  case "$(uname -m)" in
    x86_64)  TRACCAR_ZIP=traccar-linux-64-latest.zip ;;
    aarch64) TRACCAR_ZIP=traccar-linux-arm-64-latest.zip ;;
    *)       TRACCAR_ZIP="" ;;
  esac
  if [ -n "$TRACCAR_ZIP" ]; then
    if curl -fsSL -o /tmp/traccar.zip "https://www.traccar.org/download/$TRACCAR_ZIP" \
       && unzip -o -q /tmp/traccar.zip -d /tmp/traccar-install \
       && ( cd /tmp/traccar-install && ./traccar.run ); then
      echo "Traccar installed to /opt/traccar."
    else
      echo "WARNING: Traccar install failed; install it manually (README section 7)." >&2
    fi
    rm -rf /tmp/traccar-install /tmp/traccar.zip
  else
    echo "WARNING: no Traccar build for $(uname -m); install it manually (README section 7)." >&2
  fi
fi
# Ensure it is enabled + running (idempotent; the installer normally does this).
systemctl enable --now traccar >/dev/null 2>&1 || true

echo "== relay files =="
install -m 0644 "$SRC_DIR/mediamtx.yml"            "$RELAY_DIR/mediamtx.yml"
install -m 0755 "$SRC_DIR/relay_branded_source.py" "$RELAY_DIR/relay_branded_source.py"
# ODM start-line overlay (optional; tune/disable via startline_config.json)
install -m 0644 "$SRC_DIR/relay_startline.py"      "$RELAY_DIR/relay_startline.py"
install -m 0644 "$SRC_DIR/odm_detector.py"         "$RELAY_DIR/odm_detector.py"
install -m 0644 "$SRC_DIR/startline_config.json"   "$RELAY_DIR/startline_config.json"
mkdir -p "$RELAY_DIR/site"
install -m 0644 "$SRC_DIR/site/index.html"         "$RELAY_DIR/site/index.html"
install -m 0644 "$SRC_DIR/site/hut_offline.html"   "$RELAY_DIR/site/hut_offline.html"
[ -f "$RELAY_DIR/site/hls.min.js" ] || curl -L https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js -o "$RELAY_DIR/site/hls.min.js"
install -m 0644 "$SRC_DIR/Caddyfile"               /etc/caddy/Caddyfile

echo "== env file =="
mkdir -p /etc/relay
if [ ! -f /etc/relay/relay.env ]; then
  install -m 0600 "$SRC_DIR/lxc/relay.env.example" /etc/relay/relay.env
  NEED_ENV=1
fi

echo "== systemd units =="
install -m 0644 "$SRC_DIR/lxc/systemd/mediamtx.service"            /etc/systemd/system/mediamtx.service
install -m 0644 "$SRC_DIR/lxc/systemd/cloudflared-tunnel.service"  /etc/systemd/system/cloudflared-tunnel.service
install -m 0644 "$SRC_DIR/lxc/systemd/cloudflared-camera.service"  /etc/systemd/system/cloudflared-camera.service
systemctl daemon-reload
systemctl enable mediamtx cloudflared-tunnel cloudflared-camera >/dev/null
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo
echo "== installed =="
if [ "${NEED_ENV:-0}" = "1" ]; then
  echo "EDIT /etc/relay/relay.env  (camera URL/creds, MANIFEST_URL, tunnel token, camera Access token+hostname)"
fi
cat <<'EOF'
Then start the services:
    systemctl restart mediamtx cloudflared-tunnel cloudflared-camera

Cloudflare: point the tunnel's Public Hostname pro.pwllhelisailingclub.org -> http://localhost:80
            and add the cache rules from README section 4.
In the app: enable Public branding + set the Public live stream URL.

Verify:
    curl -I http://localhost/         # 200
    journalctl -u mediamtx -f         # watch the on-demand encode start when a viewer connects
EOF
