# Remote access with Cloudflare Tunnel

This app can be made available from outside the start hut by putting a Cloudflare Tunnel in front of the Waitress service started by `python app.py`.

The recommended layout is:

```text
Cloudflare public hostname  ->  http://localhost:5050
```

Do **not** point the tunnel only at `/public`. The public page itself may load, but the browser will then request CSS, JavaScript, images and flags from `/static/...`. If only `/public` is forwarded, the page will look like plain text because those static files are blocked by the tunnel rule.

## Public and private areas

The tunnel may expose the whole app because the Flask routes enforce the split:

- The site root `/` is public and redirects to the default read-only competitor landing page.
- Race-office/admin pages live under `/admin` and require login.
- Public competitor pages under `/public/...` are read-only and do not require login.
- Static files under `/static/...` are allowed so the public page renders properly.

For example:

```text
https://pro.pwllhelisailingclub.org
```

redirects to the default public competitor landing page.

The direct competitor landing-page address also works:

```text
https://pro.pwllhelisailingclub.org/public/current
```

A specific race can also be opened with:

```text
https://pro.pwllhelisailingclub.org/public/race/12
```

There is no separate mobile address to hand out: since v0.178 one responsive page serves
phones, tablets and PCs, and the old `/public/mobile/...` links redirect to it.

The race-office dashboard is:

```text
https://pro.pwllhelisailingclub.org/admin
```

or, on the start-hut PC:

```text
http://localhost:5050/admin
```

## Race-office workflow

1. Start the app on the race-office PC, normally via the Windows Scheduled Task deployment.
2. Confirm `http://localhost:5050/admin` works on the hut PC.
3. Configure the Cloudflare Tunnel to forward the whole hostname to `http://localhost:5050`.
4. Give competitors the simple bare hostname, for example `https://pro.pwllhelisailingclub.org`.
5. Use `/admin` for race-office work and log in as normal.

The pop-out button on the race page uses the public read-only page automatically, so it still works for a screen in the start hut.


## Hut PC deployment

For a permanent race-hut installation, install the Windows Scheduled Task from `deploy/windows/install_startup_task.cmd`. That keeps the Waitress app running after the race-office user logs in, so the Cloudflare Tunnel has a local service to reach at `http://localhost:5050`.

See [`DEPLOYMENT_WINDOWS.md`](DEPLOYMENT_WINDOWS.md) for the full deployment process.

## The relay

Everything above assumes the front door is the **relay** — a small Debian 12 VPS that Cloudflare points `pro.pwllhelisailingclub.org` at (it can equally be a container on a home server; the guide covers both). It is no longer just a video relay: it proxies to the hut app (with a holding page when the hut is down), serves the live camera, runs the Traccar server the GPS tracking and automatic finishes depend on, and is the single place every visitor is logged.

Its setup, day-to-day operation and troubleshooting are in **[`docs/Pwllheli_Relay_Guide.pdf`](Pwllheli_Relay_Guide.pdf)** (also on the app's Documentation page), with the working copy of the procedures alongside the config files in [`deploy/live_stream/README.md`](../deploy/live_stream/README.md).

## Video bandwidth note

Cloudflare Tunnel does not remove load from the hut connection when the hut PC is the origin for large video files. For competitor finish videos, use the app's Cloudflare R2 public-video publishing option so viewers download the smaller public copy from R2/CDN rather than through the hut 4G link.

## Cloudflare Tunnel notes

For a tunnel from `pro.pwllhelisailingclub.org` to the race-office PC, configure the public hostname to send traffic to:

```text
http://localhost:5050
```

Leave the optional path blank.

## What is safe to expose

The public pages show the Pwllheli Sailing Club banner, current series race list, links to public race pages, hut wind, wind history, optional live camera preview, race status, flags, course information, course analysis, entries and provisional results. They do not provide buttons for horn control, finish-time entry, settings, boat management, rating import, user administration or database management.

The public live camera loads when the viewer opens the **Live camera** tab and stops when they leave it or background the page, so an idle viewer costs the hut nothing (the *Show live camera* checkbox was removed in v0.180). Public race video-clip links only work after a race has finished. If **Public live image → Upload latest JPEG to Cloudflare R2** is enabled, competitor browsers refresh the R2/custom-domain JPEG instead of repeatedly requesting `/public/video/live_frame.jpg` through the hut tunnel.

## Security hardening

The app is built for internet exposure: session cookies are `HttpOnly` + `SameSite=Lax` + `Secure`, baseline security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS) are sent, request bodies are size-capped, and failed logins are rate-limited per client IP + username. Because the app itself speaks plain http to the tunnel while the browser↔Cloudflare leg is HTTPS, `Secure` cookies work in production without extra config — only **local** http testing needs `RO_COOKIE_SECURE=0`.

Recommended Cloudflare-side additions for a public deployment:

- A **WAF / rate-limit rule** on `/admin*` (and optionally Cloudflare Access) as a second layer in front of the app's own login throttling.
- Leave Cloudflare's HSTS/TLS settings on; do not let it cache the login or `/admin` HTML (the app already sends `Cache-Control: no-store` on the login page).
- Set a strong `RO_SECRET_KEY` and, if the hut PC is otherwise reachable, restrict the weather-station poller with `RO_WEATHER_ALLOWED_HOSTS`.

## Access logging and visitor analytics

If the front door is the Caddy relay (see [`deploy/live_stream/README.md`](../deploy/live_stream/README.md)), it can log every visitor — the app, the competitor pages, the live-stream watch page and the HLS playlists — in one place, with the real client IP (via `CF-Connecting-IP`). That README's **Access logging** section covers enabling the Caddy access log, generating a GoAccess dashboard (`relay_stats.sh`) with optional geolocation, and serving it privately at `/stats`. Cloudflare's own edge analytics is the better source for totals and for the edge-cached video segments the relay never sees.

## GPS tracking (Traccar) inbound port

GPS yacht tracking is the one part that needs an **inbound** port, because the trackers open a
raw TCP socket that Cloudflare Tunnel cannot proxy (there is no tunnel client on the device).
The trackers connect to a **Traccar** server on the relay. Traccar listens on a different port
per device protocol, so the port to open is the one your devices use — **`5004/tcp`** for the
Queclink GL-series (`gl200`), `5027/tcp` for Teltonika. On the club's VPS relay that means
allowing the port in the provider's firewall and in `ufw`, with
`track.pwllhelisailingclub.org` as a **DNS-only** record pointing at the VPS (Cloudflare cannot
proxy a raw TCP socket). **No inbound
port is opened on the hut PC**: the app pulls positions from Traccar's REST API over the existing
Cloudflare tunnel (`/traccar/*` on the relay).

**That DNS-only name has no Cloudflare in front of it, so every port left open on the relay is
open to the internet under it — and only the tracker ports may be.** In August 2026 port 80 was
open there as well, and the relay's Caddy answered on it: `http://track.pwllhelisailingclub.org/`
served the competitor pages and `/admin/login` served the race office's password form, in clear
text, on a raw IP, with none of Cloudflare's protection in front. Nothing needs that port from
outside — the tunnel client runs on the relay and reaches Caddy over localhost. Close `80/tcp`
in `ufw` *and* in the VPS provider's firewall, and check it from somewhere outside the network
rather than from the relay itself. `deploy/live_stream/README.md` has the commands.

Since v0.189 traffic also flows the other way *through the tunnel* — Traccar's forwarder POSTs
each fix to `/api/track/ingest` on the public hostname so finish detection runs on receipt
rather than on the next poll. That is a public, CSRF-exempt endpoint, so it is worth knowing
what guards it: it stays closed (503) until a **Push ingest token** is set in Settings, and
anything arriving without that token is rejected (401). It accepts positions only — it cannot
read anything back. Still no listening port on the hut; the tunnel remains the only way in.

See [`deploy/live_stream/README.md`](../deploy/live_stream/README.md) section 7 and
[`TRACKING.md`](TRACKING.md). If an open inbound port is unacceptable, move the whole relay to
a VPS with a public IP — the configuration is unchanged.

## Race-office access

The public site root redirects competitors to the competitor landing page. Race officers should use `/admin` for the dashboard or `/admin/race/current` to jump directly to the current race sheet.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
