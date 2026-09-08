#!/usr/bin/env bash
# Generate the GoAccess HTML report from Caddy's access logs on the relay.
#
# Caddy writes JSON access logs. What GoAccess needs is the REAL visitor IP
# (`client_ip`, populated from the CF-Connecting-IP header) — not `remote_ip`,
# which behind the Cloudflare Tunnel is always the cloudflared loopback
# (127.0.0.1). GoAccess handles this differently by version:
#
#   * >= 1.9.2  its built-in CADDY format reads `client_ip` directly, so we can
#               feed the JSON straight in with --log-format=CADDY.
#   * <  1.9.2  the CADDY format reads `remote_ip`, so we reshape each line with
#               jq into Combined Log Format using client_ip and use --log-format=COMBINED.
#
# This script auto-detects the version and picks the right path.
#
# GeoIP: drop MaxMind GeoLite2 .mmdb files into $GEOIP_DIR (City for location,
# ASN for networks) and they are picked up automatically. Needs a GoAccess built
# with GeoIP2 (goaccess --version | grep -i geo).
#
# Run from cron, e.g. every 10 minutes:
#   */10 * * * * /opt/relay/relay_stats.sh 2>/dev/null
#
# Overridable via environment:
#   RELAY_LOG_GLOB   default /var/log/caddy/access.log*   (current + rolled .gz)
#   RELAY_STATS_OUT  default /opt/relay/site/stats.html
#   RELAY_GEOIP_DIR  default /opt/relay/geoip
set -euo pipefail

LOG_GLOB="${RELAY_LOG_GLOB:-/var/log/caddy/access.log*}"
OUT="${RELAY_STATS_OUT:-/opt/relay/site/stats.html}"
GEOIP_DIR="${RELAY_GEOIP_DIR:-/opt/relay/geoip}"

geoip_args=()
for db in GeoLite2-City.mmdb GeoLite2-Country.mmdb GeoLite2-ASN.mmdb; do
	[ -f "$GEOIP_DIR/$db" ] && geoip_args+=(--geoip-database "$GEOIP_DIR/$db")
done

# GoAccess >= 1.9.2 reads client_ip in its CADDY format; older reads remote_ip.
ver="$(goaccess --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1)"
native_caddy=0
if [ -n "$ver" ] && printf '%s\n%s\n' "1.9.2" "$ver" | sort -V -C 2>/dev/null; then
	native_caddy=1
fi

# shellcheck disable=SC2086  # LOG_GLOB is intentionally a glob
if [ "$native_caddy" = 1 ]; then
	zcat -f $LOG_GLOB \
		| goaccess --log-format=CADDY "${geoip_args[@]}" -o "$OUT" -
else
	zcat -f $LOG_GLOB \
		| jq -r 'select(.request) |
			(.ts|gmtime|strftime("%d/%b/%Y:%H:%M:%S +0000")) as $t | .request as $r |
			"\($r.client_ip // "-") - - [\($t)] \"\($r.method) \($r.uri) \($r.proto)\" \(.status) \(.size // 0) \"\((($r.headers.Referer)//["-"])[0])\" \"\((($r.headers["User-Agent"])//["-"])[0])\""' \
		| goaccess --log-format=COMBINED "${geoip_args[@]}" -o "$OUT" -
fi
