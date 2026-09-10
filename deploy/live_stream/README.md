# Pwllheli relay — front door, live camera and GPS tracking

> **There is a PDF version of this**, in the app's house style with an architecture
> diagram: `docs/Pwllheli_Relay_Guide.pdf`, also on the app's **Documentation** page.
> This README stays the working copy next to the config files; rebuild the PDF with
> `python scripts/build_relay_guide.py` after changing anything here.

An on-demand, branded, low-bandwidth live view of the hut camera at
`https://pro.pwllhelisailingclub.org/live`, fanned out by Cloudflare so the off-grid
hut only ever sends **one** stream, and only while someone is watching.

The same relay is also the club's **front door**: `pro.pwllhelisailingclub.org` proxies to
the hut race app, showing a holding page when the hut is offline. (This replaces the old
separate front-door PC and the standalone `live.` host.)

```
viewer ── https ──> Cloudflare (TLS + caches .ts segments)
                        │  tunnel: pro.pwllhelisailingclub.org -> Caddy:80
                        ▼
   The relay (VPS, or a home Proxmox LXC) — the club's single front door ───
     Caddy  ──/live─────────> watch page (site/index.html + hls.js)
            ──/hut/*─────────> MediaMTX  (HLS)      ▲ publishes on demand
            ──/  (everything else)─> hut race app (proxied)
                                     └─ holding page (site/hut_offline.html) if hut down
                               relay_branded_source.py  (ffmpeg: pull + burn logos)
                               │  rtsp, only while watched
     cloudflared access tcp ── localhost:18554 ── Cloudflare ──> hut camera
     reverse_proxy ─────────────────────────────── Cloudflare ──> hut-origin (the app)
   ──────────────────────────────────────────────────────────────────────
```

- **One pull from the hut** regardless of viewers; dropped ~20 s after the last one leaves.
- **Cloudflare caches the video segments**, so the relay's uplink stays flat as viewers grow.
- **Branding** (club logo top-left, sponsors rotating top-right) is burned in on the
  relay from the app's live branding manifest — change logos in the app, no relay edits.

Three machines: the **camera** (hut LAN), the **hut PC** (Windows, runs the app + its own
`hut-origin` cloudflared tunnel), and the **relay** (a Debian 12 VPS — see §5) — which is
now the stream relay, the public front door and the GPS tracking server.

> **Trade-off:** because the relay is the single front door, if the *relay* is down then
> `pro.` is unreachable entirely (the holding page only covers the *hut* being down). That's
> the cost of folding the separate front-door PC into the relay.

---

## Prerequisites

- The race app running on the hut PC, **v0.140 or later** (provides `/api/branding/live`
  and the "Public live stream URL" setting).
- A Cloudflare account managing `pwllhelisailingclub.org`, with the hut already reachable
  through a Cloudflare Tunnel.
- Hostnames under the zone (all proxied through Cloudflare):
  - `pro.pwllhelisailingclub.org` — the public front door; this guide points it at the relay.
  - `hut-origin.pwllhelisailingclub.org` — the hut PC's own tunnel (the app); the relay
    proxies to it.
  - `hut-cam.pwllhelisailingclub.org` — the camera's RTSP (added in step 2).

---

## 1. Camera (Hikvision DS-2CD3786G2T-IZSY)

In the camera web UI → Configuration → Video/Audio → **Sub-stream**:

- **Video encoding: H.264** (not H.265 — H.264 plays in every browser with no transcoding).
- Resolution ~640×480–704×576, bitrate ~512–1024 kbps, 12–15 fps.
- **I-frame interval = the frame rate** (e.g. 15). This is the biggest latency lever —
  HLS can only cut a segment on a keyframe, so the Hikvision default of 50–100 causes
  10 s+ latency.

Create a **least-privilege camera user** (live-view only) for the relay to use.

RTSP sub-stream URL: `rtsp://USER:PASS@CAM_IP:554/Streaming/Channels/102`

---

## 2. Hut PC (Windows) — expose the camera

The hut already runs `cloudflared` for the app. Add the camera as a **TCP** public
hostname on that tunnel.

- **Dashboard-managed tunnel** (token install): Cloudflare **Zero Trust** → Networks →
  Tunnels → your tunnel → **Public Hostname** → Add:
  - Subdomain `hut-cam`, domain `pwllhelisailingclub.org`
  - Type **TCP**, URL **`CAM_IP:554`**
- **Or config.yml tunnel**: add an ingress `- hostname: hut-cam.pwllhelisailingclub.org` /
  `service: tcp://CAM_IP:554`, then `cloudflared tunnel route dns … hut-cam.…` and restart
  the Windows `cloudflared` service.

Then **secure it** (so only the relay can pull the camera): Zero Trust → Access →
Applications → add a self-hosted app for `hut-cam.pwllhelisailingclub.org` with a
**service-token** policy. Note the token **Client ID** and **Client Secret** for step 4.

---

## 3. Hut PC — the app

In the app (Settings):

- **Public branding**: enable it and confirm the club + sponsor logos. (While this is off,
  `/api/branding/live` returns `enabled:false` and the stream is unbranded.)
- Leave **Public live stream URL** blank for now — set it in step 6 once the stream is live.

The relay reads branding from `https://pro.pwllhelisailingclub.org/api/branding/live`.

---

## 4. Cloudflare — point the front door at the relay + caching

The relay takes over `pro.pwllhelisailingclub.org` (retire the old separate front-door PC
and the standalone `live.` host).

- **Tunnel.** On the relay's dashboard-managed tunnel, add/point the **Public Hostname**
  `pro.pwllhelisailingclub.org` → Type **HTTP** → URL **`http://localhost:80`**, and copy
  that tunnel's **token** for step 5. Remove `pro.` from the old front-door PC.
- **Cache Rules** (Caching → Cache Rules) so the segments cache at the edge:
  - *Segments* — expression
    `(http.host eq "pro.pwllhelisailingclub.org" and (ends_with(http.request.uri.path, ".ts") or ends_with(http.request.uri.path, ".mp4") or ends_with(http.request.uri.path, ".m4s")))`
    → **Eligible for cache**, **Edge TTL: Override origin → 10 seconds**.
  - *Playlist* — expression
    `(http.host eq "pro.pwllhelisailingclub.org" and ends_with(http.request.uri.path, ".m3u8"))`
    → **Bypass cache**.
- Optional: Caching → Tiered Cache → **Smart Tiered Cache** (extra origin offload).
- Make sure **Development Mode is OFF** (it forces `cf-cache-status: BYPASS`).

> **Do not cache the app's HTML.** Keep every cache rule scoped to the video
> segment suffixes above (`.ts`/`.mp4`/`.m4s`), and never add a broad
> **"Cache Everything"** rule or Page Rule for `pro.pwllhelisailingclub.org`.
> The hut app serves its pages with `Cache-Control: no-store` (the login page in
> particular), and Caddy passes those headers straight through, so Cloudflare
> leaves them uncached by default. A "Cache Everything" rule with **Edge TTL:
> Override origin** ignores `no-store` and would serve a cached login page
> without its session cookie — which breaks sign-in with *"Bad request: CSRF
> token missing or invalid"* for the next visitor.

- **Access policy on `hut-origin`.** The relay proxies to
  `hut-origin.pwllhelisailingclub.org`, and that hostname is public: without a policy
  anyone can reach the whole app there, login page included, going round this relay and
  round every WAF or rate-limit rule scoped to `pro.`. A September 2026 external check
  found it open. Zero Trust -> Access -> Service Auth -> **Service Tokens**, create one
  named `relay-to-hut`; then Access -> Applications -> **Self-hosted**, domain
  `hut-origin.pwllhelisailingclub.org` with an **empty path** so it covers the whole
  host, and a single policy with action **Service Auth** including that token. One
  policy only: a second permissive one, or any policy with action Bypass, and the gate
  does nothing. Put the Client ID and Secret in `relay.env` as `HUT_ACCESS_ID` /
  `HUT_ACCESS_SECRET` (the Caddyfile already sends them) and **restart** Caddy -- a
  reload re-reads the Caddyfile but not the unit environment.

  > **Set the app's Public address first.** Settings -> Web server -> **Public address**
  > = `https://pro.pwllhelisailingclub.org`. Until that is set the app builds external
  > URLs against the tunnel's own hostname, so `/api/branding/live` hands out logo
  > addresses on `hut-origin`. Lock that host down before fixing this and
  > `relay_branded_source.py` quietly loses the logos: a failed download is swallowed and
  > the stream goes out unbranded. Set the address, check with
  > `curl -s https://pro.pwllhelisailingclub.org/api/branding/live`, then add the policy.

  Afterwards `curl -sI https://hut-origin.pwllhelisailingclub.org/admin/login` should no
  longer reach the app, while `pro.` still works.

---

## 5. The relay host

Anything running **Debian 12** with about 1 vCPU, 512 MB–1 GB RAM and 4 GB of disk will do.
There are two ways to host it, and they differ only in where the machine lives:

- **A VPS (recommended, and what the club runs).** A public IP of its own, so the trackers
  reach it directly and nothing depends on the home connection or router. Not sharing an
  uplink with a household is worth a lot on a race day.
- **A Debian 12 LXC on a home Proxmox server.** Fine to start with and free, but the
  trackers then need a port forwarded on the home router, and the whole public face of the
  club rides on the home broadband.

Everything below is identical either way — `lxc/setup.sh` is a plain Debian install script
despite the folder name, and the systemd units, Caddyfile and MediaMTX config are unchanged.

As root on the relay:

1. Get this `live_stream/` folder onto the machine (`git clone`; or on Proxmox, from the
   host: `pct push <ctid> -r /path/to/deploy/live_stream /root/live_stream`).
2. Run the installer:
   ```
   cd /root/live_stream && bash lxc/setup.sh
   ```
   It installs MediaMTX, Caddy, cloudflared, ffmpeg + python3, copies the config/site into
   `/opt/relay`, creates `/etc/relay/relay.env`, and installs the systemd services.
3. Edit **`/etc/relay/relay.env`**:
   ```
   CAMERA_URL=rtsp://USER:PASS@localhost:18554/Streaming/Channels/102
   MANIFEST_URL=https://pro.pwllhelisailingclub.org/api/branding/live
   CAMERA_FPS=15
   WIND_OVERLAY=1
   WIND_URL=https://pro.pwllhelisailingclub.org/api/weather/current
   TUNNEL_TOKEN=<the pro.… tunnel token from step 4>
   CAM_HOSTNAME=hut-cam.pwllhelisailingclub.org
   CAM_ACCESS_ID=<Access service token Client ID from step 2>
   CAM_ACCESS_SECRET=<Access service token Client Secret from step 2>
   ```
4. Start everything:
   ```
   systemctl restart mediamtx cloudflared-tunnel cloudflared-camera
   systemctl reload caddy
   ```

Services (all autostart on boot): `mediamtx`, `caddy`, `cloudflared-tunnel`,
`cloudflared-camera`. Logs: `journalctl -u <name> -f`.

---

## 5a. Updating the relay later

Nothing here is version-locked to the hut app: the relay is updated by copying
the file that changed and reloading the one service that reads it. Rerunning
`lxc/setup.sh` is for a fresh machine, not an update — it would overwrite
`/etc/relay/relay.env`.

**The Caddyfile** (`/etc/caddy/Caddyfile`) is the one that changes most:

```bash
# 1. See what you would be changing. The live file may carry local edits --
#    an uncommented /stats block with its password hash, a different
#    frame-ancestors list -- and a blind copy silently reverts them.
diff -u /etc/caddy/Caddyfile /root/live_stream/Caddyfile

# 2. Copy it in.
install -m 0644 /root/live_stream/Caddyfile /etc/caddy/Caddyfile

# 3. Check it parses BEFORE asking Caddy to use it.
caddy validate --config /etc/caddy/Caddyfile

# 4. Reload, not restart: reload swaps the config with no dropped connections,
#    and Caddy refuses to apply a broken one rather than stopping.
systemctl reload caddy
```

If the diff shows local edits worth keeping, apply the new block by hand instead
of copying the file — the change is usually one `handle` block.

**The other files** follow the same shape: `mediamtx.yml` →
`/opt/relay/mediamtx.yml` then `systemctl restart mediamtx`; the site pages and
`relay_branded_source.py` → `/opt/relay/` (the stream re-reads them when it next
starts, so close all viewers or `systemctl restart mediamtx`).

Check it worked from outside, not just from the relay:

```bash
curl -sS -o /dev/null -w "%{http_code} %{time_total}s
"      https://pro.pwllhelisailingclub.org/
```

---

## 6. Turn it on in the app + verify

- App → Settings → **Public live stream URL** = `https://pro.pwllhelisailingclub.org/live` →
  a **Watch live** link now appears on the competitor pages.
- Open `https://pro.pwllhelisailingclub.org/live` — branded live video in a few seconds.
  `https://pro.pwllhelisailingclub.org/` should show the race app, or the holding page if
  the hut is offline.

Verify the important behaviours:

| Check | How | Expect |
|---|---|---|
| On-demand | `journalctl -u mediamtx -f`, open the page | `runOnDemand command started`, then `stream is available and online` |
| One hut pull | open on 2–3 devices | still a single publish session in the log |
| Idle teardown | close all viewers | pull drops ~20 s later |
| Edge caching | DevTools → Network → a `.ts` → response headers | `cf-cache-status: HIT` (after the first `MISS`) |
| Branded | look at the video | club logo top-left, sponsors rotating top-right |

Once `.ts` requests show `HIT`, extra viewers are served by Cloudflare and the relay's
uplink stays flat.

---

## 7. GPS tracking (Traccar) — optional

Boats carry Queclink **GL521MG** LTE trackers. They report to a **Traccar** server on this
same relay; the hut app then *pulls* positions from Traccar's REST API over the existing
Cloudflare tunnel (outbound only — **no inbound port on the hut**). Traccar shows the fleet
live on the race course chart and can detect finish-line crossings (see the app's
`docs/TRACKING.md`).

1. **Traccar is installed by `lxc/setup.sh`** (it runs Traccar's own self-contained installer,
   which creates and enables `traccar.service`; skipped if `/opt/traccar` already exists). To
   install it by hand instead:
   ```bash
   cd /opt && curl -L -o traccar.zip https://www.traccar.org/download/traccar-linux-64-latest.zip
   unzip traccar.zip && ./traccar.run && systemctl enable --now traccar
   ```
   Traccar serves its web UI/API on **:8082**, and listens on a **separate port per device
   protocol** — one Traccar instance, many listeners. The port you need is the one for your
   device's protocol:

   | Device | Traccar protocol | Port |
   |---|---|---|
   | Queclink **GL521MG** (and the rest of the GL/GV series) | `gl200` | **5004/tcp** |
   | Teltonika **RUTX50** router, FMB/FMC series | `teltonika` | **5027/tcp** |
   | Traccar Client phone app, `simulate_trackers.py` | `osmand` | **5055/tcp** (HTTP) |

   > **5027 is Teltonika, not Queclink.** Earlier versions of this guide said to use 5027 for
   > the boat trackers — that is the port the club's RUTX50 test device uses, and it is wrong
   > for a GL521MG. Point a Queclink tracker at 5027 and it connects to Traccar's Teltonika
   > decoder, which cannot parse @Track messages: the tracker reports happily, nothing appears
   > in Traccar, and no error is logged anywhere. Use **5004** for the GL521MGs. Traccar's own
   > protocol reference is at `traccar.org/protocol/5004-gl200/` — the port is in the URL.

2. **Open the inbound port(s) for the trackers.** This is the only inbound access the whole
   system needs; everything else is outbound-only, and the hut PC still opens nothing.
   - **On a VPS:** allow **5004/tcp** in the provider's firewall/security group *and* in the
     host firewall (`ufw allow 5004/tcp`), then point `track.pwllhelisailingclub.org` at the
     VPS's IP as a **DNS-only** A record (grey cloud — Cloudflare cannot proxy a raw TCP
     socket).
   - **On a home Proxmox LXC:** port-forward **5004/tcp** on the router to the container, and
     point the same DNS-only record at your home public IP.

   The club currently has three open: **5004** for the Queclink trackers, **5027** for the
   Teltonika RUTX50 on the committee boat, and **5055** for `osmand`. Leave anything else
   closed.

   **Port 80 must be closed to the internet, and this is not theoretical.** Cloudflare does
   not connect to this machine from outside: `cloudflared` runs *on* it and reaches Caddy over
   `localhost:80`, so nothing on the internet ever needs that port. If the firewall lets it
   through, the catch-all `:80` site block below answers directly - and its last rule proxies
   anything it does not recognise to the hut app. The race office **login page is then served
   over plain HTTP on a raw IP address with none of Cloudflare's protection in front of it**:
   no WAF, no rate limiting, no Access.

   That is exactly what was found on the club's own relay in August 2026.
   `http://track.pwllhelisailingclub.org/` - the DNS-only tracker record, pointing at this
   machine - returned the competitor page, `/live/` the watch page, and `/admin/login` a real
   password form. The session cookie is `Secure`, so a login could not actually have stuck; but
   anyone typing a password into that form would have sent it across the internet in clear
   text, and `Strict-Transport-Security` does not help, because browsers ignore HSTS delivered
   over HTTP.

   ```bash
   sudo ufw status numbered     # find any rule allowing 80/tcp
   sudo ufw delete <number>     # and remove it -- also in the VPS provider's firewall
   ```

   Stronger still, bind Caddy to the loopback interface so a later firewall mistake cannot
   expose it again: change the `:80` site block to `127.0.0.1:80`. The tunnel still reaches it,
   because it connects over localhost.

   Check it from a machine outside the network - not from the relay itself, where localhost
   will always answer:

   ```bash
   curl -sS -m 10 -o /dev/null -w '%{http_code}
' http://track.pwllhelisailingclub.org/
   ```

   It should fail to connect. The trackers are unaffected: their ports are Traccar's, not
   Caddy's. The public site is unaffected: it arrives through Cloudflare.

   **Why 5055 is worth keeping open.** It is the OsmAnd protocol, and two useful things speak
   it. The first is the **Traccar Client phone app** (free, iOS and Android): install it, set
   the server to `http://track.pwllhelisailingclub.org:5055` and a device identifier, add that
   identifier on the app's Trackers page, and a phone in a pocket becomes a tracker. That is
   the cheapest way to put a real moving boat on the chart — a rescue RIB, the committee boat,
   or a volunteer's phone during a race — without waiting for GL521MG hardware or burning its
   battery. The second is `scripts/simulate_trackers.py`, which reports over the same protocol,
   so a whole simulated fleet can be sailed round the course through the real relay.

   It is plain HTTP with no authentication beyond the device identifier, so treat it the same
   way as the other two: Traccar only accepts identifiers it knows (unless
   `database.registerUnknown` is on), and nothing behind it is reachable from that port.
3. **Provision each tracker** to report to `track.pwllhelisailingclub.org:5004` using the
   Queclink @Track configuration commands for the model (`AT+GTQSS` / `AT+GTSRI` on this
   family — check the GL521MG protocol document that ships with the units). The "Main server
   IP/domain" field accepts a hostname.

   Set a **frequent reporting interval for race days**. The GL521MG is a battery asset
   tracker built for up-to-a-year standby, so its out-of-the-box schedule is far too coarse
   for finish order — but the reverse is also true: a race-day rate that is fast enough to
   time a line crossing will flatten the battery in a fraction of that. Plan on **charging
   between race days** (the GL521MG charges wirelessly, Qi), and keep the fast rate for
   racing rather than leaving it on permanently.

   You can then add each device from the app's **Trackers** page (it creates it in Traccar
   for you) instead of the Traccar UI — or, with `database.registerUnknown` enabled, just
   switch the tracker on and adopt it from the list that appears there.
4. **Expose the API to the hut app** — the `Caddyfile` already has a `handle_path /traccar/*`
   block that reverse-proxies to `localhost:8082` (prefix stripped). This path serves the
   **REST API only**, not Traccar's web UI (see the next step). In the Traccar UI create
   an **API token** (user settings), then in the hut app **Settings → GPS tracking** set:
   - Base URL: `https://pro.pwllhelisailingclub.org/traccar`
   - API token: the token from Traccar
   - tick **Enable GPS tracking**, save.
5. **Verify:** the app's Settings page should show *"N tracker(s) reporting"*, and each
   boat's tracker can be assigned there. Assign trackers to boats, then on a race sheet the
   fleet appears on the course chart. Arm **GPS auto-finish** per race when you want crossing
   detection.
5b. **Push positions to the app (recommended)** — see
   [`traccar-forward.xml`](traccar-forward.xml) for the entries to merge into
   `/opt/traccar/conf/traccar.xml`, and set the matching **Push ingest token** in the app's
   Settings → GPS tracking. Traccar then POSTs each fix to the app as it decodes it, and
   finish detection runs on receipt, instead of the app waiting up to a poll interval. That
   delay does not change a recorded finish *time* (crossings are interpolated between fix
   timestamps) but it does delay the **automatic horn** and what competitors are shown —
   which matters more the faster the trackers report. This is **not** "forwarder-only" mode:
   Traccar keeps its database, so it also keeps the device registry (needed to send commands
   to a tracker, e.g. raising its reporting rate near the line), the history the app
   back-fills from, and its web UI. Once push works, raise the app's poll interval to ~30 s —
   the poller stays as the safety net and fills any gap from Traccar's history.
6. **Reaching Traccar's own web UI** (only needed for Traccar-side admin — devices, users and
   tokens; day-to-day tracker work is done on the app's **Trackers** page). It does **not**
   work at `…/traccar/`: that page loads but spins for ever, because Traccar's UI is a
   single-page app that asks for `/assets/index-*.js`, `/styles.css` and `/api/socket` at the
   **site root**, and those requests fall through to the hut app instead of Traccar. Pick one:
   - **On the LAN or over SSH** (nothing extra published): `http://<lxc-ip>:8082`, or from a
     remote machine `ssh -L 8082:localhost:8082 root@<relay>` then
     `http://localhost:8082`. Simplest, and keeps the UI off the internet.
   - **Its own public hostname:** add `traccar.pwllhelisailingclub.org` as a second public
     hostname on the same Cloudflare tunnel → service `http://localhost:80`; the `Caddyfile`
     already has the matching `http://traccar.pwllhelisailingclub.org` site block, so
     `systemctl reload caddy` is all this end needs. Don't reuse
     `track.pwllhelisailingclub.org` — that is a DNS-only record for the trackers' 5004/tcp
     forward and a tunnel hostname must be a CNAME, so one name cannot be both.
     **This publishes a login page to the internet**: put Cloudflare Access in front of the
     hostname, or at least change Traccar's default admin password and switch Registration
     off in Traccar → Settings → Server.

No hardware yet? Tick **Simulate boats** in Settings to preview the map and auto-finish with
synthetic boats.

---

## The pieces (files in this folder)

- `mediamtx.yml` — MediaMTX: standard **mpegts HLS** (1 s segments), on-demand source
  runs `relay_branded_source.py`, drops the stream 20 s after the last viewer.
- `relay_branded_source.py` — pulls the camera and publishes into MediaMTX. Fetches the
  branding manifest and burns in the logos (matching the app's layout); falls back to a
  plain copy if branding is off or unreachable. Also composites the optional ODM
  start-line overlay (below) when enabled.
- `relay_startline.py` + `odm_detector.py` + `startline_config.json` — the **ODM
  start-line overlay** (prototype, see below).
- `Caddyfile` — the front door: watch page at `/live`, `/hut/*` → MediaMTX (with the
  caching/cookie handling), and everything else proxied to the hut app with a holding-page
  fallback when the hut is unreachable.
- `site/index.html` — the watch page, served at `/live` (hls.js bundled locally; embeddable).
  It also **posts its state to the parent window** when embedded —
  `{source: "psc-live", state: "connecting" | "playing" | "error"}` — which the hut app's
  camera panels use to keep showing snapshots until the stream is really playing and then
  swap to this player (see `static/live_camera.js` and the app's `docs/VIDEO_RECORDING.md`).
  **Copy this file to the relay's site root whenever it changes** (`/opt/relay/site/` by
  default); with an older copy that posts nothing, the app's panels stay on still pictures.
  From v0.187 it plays through **hls.js first**, using the browser's own HLS only where
  hls.js is unsupported (Safari/iOS). Chrome answers *"maybe"* to
  `canPlayType("application/vnd.apple.mpegurl")` on some builds and then cannot demux the
  playlist, which left the player **paused at 0:00 on a perfectly healthy stream** — the
  intermittent "sometimes it doesn't start" fault, on this page as well as embedded. It also
  asks for playback whenever new data arrives, watches for a loaded-but-paused player, and
  offers *"Tap to start the live view"* if autoplay is refused outright.
  The `Caddyfile`'s `frame-ancestors` list controls **who may embed this page** — it covers
  `pro.…` itself plus `http://localhost:5050` and `http://127.0.0.1:5050` for the app running
  directly on a PC (the hut machine's race sheet, or a test server). Anything not listed gets
  a *"refused to connect"* box, so after changing that line run `systemctl reload caddy`.
- `site/hut_offline.html` — the holding page shown when the hut app is down.

### How long the relay waits for the hut

The hut proxy gives the hut **3 seconds to connect and 5 seconds to start
answering** (`dial_timeout`, `response_header_timeout`). That is deliberately
tight: it is what makes the holding page appear promptly when the hut is off,
rather than leaving somebody looking at a spinner.

**The Virtual Race Officer's two command endpoints are the exception**, and have
their own `handle` block with `response_header_timeout 45s`. They wait on a
language model, which answers in 3 to 8 seconds — everything else the hut serves
is well under a second, so the 5-second limit was invisible until that page
existed. Then it cut off every command that ran long: the person on the water saw
*"the hut did not answer (504)"* within five seconds while the hut carried on and
**completed the command**, which its own slow-request log shows finishing 200 at
3.3–7.9 s. Identical commands succeeded or failed depending on which side of five
seconds the model landed.

45 s is chosen so the **app** is always the thing that gives up first: its own
model timeout is 30 s, after which it answers with the provider's reason. A relay
that times out first replaces that explanation with a bare gateway error.

If the model is ever configured to allow longer than 30 s, raise this to match.
- `lxc/` — `setup.sh` + systemd units for the native Debian install (this guide). The name
  is historical: it is a plain Debian script and runs the same on a VPS.
- `docker/` — an alternative Docker Compose stack (if you'd rather run it under Docker).

To change branding: edit it in the app (Settings → Public branding). The relay re-reads
the manifest each time the stream starts, so restart the stream (close viewers, or
`systemctl restart mediamtx`) to pick up new logos.

## ODM start-line overlay (prototype)

The relay can draw the **actual start line** on the stream — from the foreground pole
base to the orange Outer Distance Mark (ODM) buoy, which swings on its anchor. It's a
colour-based detector (`odm_detector.py`, PIL + numpy — no OpenCV/ML) tuned per camera in
`startline_config.json`.

Because the buoy moves slowly, detection runs **once when the stream starts** (on-demand),
and the line is fixed for that viewing session — no real-time tracking. It's fully
**best-effort**: if numpy/Pillow are missing, no frame can be grabbed, or no buoy is
confidently found, the stream simply runs without the line (never fails).

- **Enable/disable:** `relay_overlay_enabled` in `startline_config.json` (default `true`).
- **Appearance:** `line_color` (RGB) and `line_opacity` (0–1) in `startline_config.json`.
  The line is drawn solid then the whole layer's alpha is scaled, so it reads as painted
  on the water. The live relay uses `line_color` (green) as-is; the planned start/finish
  **recording** overlay will instead pass **red before the start signal and green after**
  (the renderer, `odm_detector.render_line_overlay`, already takes a `color` argument).
- **Deps:** `python3-numpy` + `python3-pil` (Debian) / `py3-numpy` + `py3-pillow` (Docker) —
  installed by `setup.sh` / the Dockerfile.
- **Tuning caveat:** it was tuned against the app's public branded frames. The relay pulls
  the camera **sub-stream**, which may differ in field of view/sharpness. If the line is
  off, grab one sub-stream frame, drop it into the project's `odm_frames/` folder and re-tune
  with the harness there (`python run_detect.py`), then copy the values into
  `startline_config.json` and restart the stream.
- The line is drawn **under** the corner logos. It only reflects the buoy position at the
  moment the stream started; a periodic refresh can be added later if drift matters.

> **Prototype status:** verified on stills (detection + overlay render + ffmpeg command
> build), but the live ffmpeg encode path has not yet been smoke-tested on the relay. First
> deploy: watch `journalctl -u mediamtx` for the `startline:` log line and confirm the line
> lands on the buoy before relying on it.

---

## Wind readout on the stream (TWD / TWS / gust)

The relay can burn the hut's wind across the **top centre** of the picture, so somebody
watching from the bar or the club website sees the same numbers the race officer is looking
at without a second screen.

- **Turn it on:** `WIND_OVERLAY=1` in the MediaMTX unit's environment. Off by default.
- **Where the numbers come from:** `WIND_URL`, default
  `https://hut-app.pwllhelisailingclub.org/api/weather/current`. That endpoint is already
  public — it is what the competitor page and the clubhouse display poll — so no credentials
  travel to the relay.
- **How often:** `WIND_POLL_SECONDS` (default 5).
- **When it goes blank:** `WIND_STALE_SECONDS` (default 120). See below.
- **Size:** `WIND_FONT_SCALE` (default `0.0225`, i.e. 24px on a 1080-line frame). It
  started at `0.045` and was halved on sight of the real stream, where it read as a banner
  across the picture rather than a readout on it.
- **Backing:** white text alone disappears against the pale overcast sky this camera mostly
  looks at, so it is outlined. `WIND_BOX=1` puts a solid panel behind it instead — legible
  either way, but the panel is a caption bar across the view.
- **Font:** any of DejaVu/Liberation. Debian has DejaVu already; on Alpine install
  `ttf-dejavu`. **No font, no readout** — the stream still runs.

It rides on the branding encode that is already happening, so it costs no extra transcode.
That also means it only exists on the **branded** path: when branding is unavailable the
relay falls back to `-c copy` deliberately, and a copy has no filters and so no readout.

### Why it blanks rather than freezes

The text is a file that ffmpeg re-reads while it runs (`drawtext ... reload=1`), and a child
process rewrites it every few seconds. That is what lets the numbers change without
restarting the encode — but it also means a file left alone would keep showing the last wind
it was given.

If the hut link drops, that would put a twenty-minute-old wind on live-looking footage with
nothing on screen to say so, and people sail on those numbers. So **a sample older than
`WIND_STALE_SECONDS` renders as nothing at all**, as does a sample with no direction or no
speed, or one stamped in the future (the clocks disagree, so its age cannot be reasoned
about either way). An empty file draws an empty string: the picture simply stops claiming to
know the wind. Gust is dropped on its own when the station does not report one.

The updater stops itself when the stream does — it watches for its parent going away, so
nothing is left polling the hut for a stream nobody is watching.

### Checking it

    sudo journalctl -u mediamtx --since "10 min ago" --no-pager | grep -i wind

Two traps in that one line, both of which cost real time the first time this was
deployed. **`sudo` is not optional** — MediaMTX runs as root, and without it journalctl
quietly shows you only your own user's messages and nothing else. It prints a *Hint*
saying so, which is easy to read past when you are looking for a grep match that was
never going to appear. And the source is **on demand**: nothing is logged until a viewer
opens the stream, so open it first, then look.

Every stream start says which it is, so **silence means the script never ran at all**:

| Line | Meaning |
|---|---|
| `wind: readout on, every 5s from … — first value 'TWD 245   TWS 12.4 kn'` | Working. Compare that value with the picture. |
| `wind: readout on … no wind yet` | Working, but nothing current came back. Usually genuine; if `curl` gets a good sample from the same host and this still says it, suspect the request rather than the station (see below). |
| `wind: readout off (set WIND_OVERLAY=1 …)` | The switch is not set. |
| `wind: no usable font on this host` | Install `fonts-dejavu-core`. |
| `wind overlay unavailable` | `relay_wind.py` is missing or did not import. |
| *(nothing)* | The branded encode did not run — check the logos are on the picture too. |

All of them leave the stream up.

If `WIND_OVERLAY` is set and nothing appears, check the value actually reached the
running service — an edit to `relay.env` needs a `systemctl restart mediamtx`, not a
reload. `relay.env` is `0600 root`, so both of these want `sudo`:

    sudo grep WIND /etc/relay/relay.env
    sudo tr '\0' '
' < /proc/$(pgrep -x mediamtx)/environ | grep -i WIND

The second is the one that settles it: it reads the environment the process actually
holds. Do **not** reach for `systemctl show mediamtx -p Environment` — that lists only
inline `Environment=` directives and says nothing about an `EnvironmentFile=`, so it
comes back empty whether the variable is set or not.

**If curl works and the readout does not**, the difference is the User-Agent: Cloudflare's
bot rules 403 the default python-urllib one, so `curl` succeeds from the relay while the
script gets nothing — and the failure is silent, indistinguishable from a station with no
wind. Both the manifest fetch and the wind fetch therefore send an ordinary User-Agent. The
same trap has now bitten three separate fetches in this project.

The formatting and the blanking rules are unit-tested in the app repo
(`tests/test_relay_wind_overlay.py`), and the filtergraph was verified against a real ffmpeg
— including that `reload=1` picks up a change mid-encode.

---

## Access logging (who is visiting)

Caddy here is the single public front door — the hut app, the competitor pages
(`/public/...`), the `/live` watch page and the `/hut` stream all pass through it —
so one Caddy access log records every visitor. It is enabled in the `Caddyfile`:

- A global `servers { trusted_proxies static private_ranges; client_ip_headers
  CF-Connecting-IP }` block makes each log line's `request>client_ip` the real
  visitor IP. Without it every request would log as `127.0.0.1` (the cloudflared
  peer on loopback).
- The site `log` block writes JSON to `/var/log/caddy/access.log`, rolled at
  20 MiB, keeping 14 files (~90 days via `roll_keep_for 2160h`).
- The high-volume HLS `.ts` segments are `log_skip`-ped to keep the log about
  page/stream visits.

Notes:

- **Video-viewer counts:** Cloudflare edge-caches the `.ts` segments, so most
  never reach the relay. The `.m3u8` playlists are `no-cache`, so an active
  viewer's periodic playlist refresh *does* hit Caddy — count distinct
  `client_ip`s on `/hut/*.m3u8` to gauge concurrent viewers. For headline totals
  and geography across everything (including cached traffic), use Cloudflare's
  own analytics / Logpush at the edge.
- **Privacy:** IP addresses are personal data under UK GDPR. Retention is bounded
  by `roll_keep_for`; keep `/var/log/caddy/` readable by root only, and add a
  short access-logging note to the public page.
- After editing the Caddyfile on the relay, create the log dir if needed
  (`sudo mkdir -p /var/log/caddy`) and reload: `sudo systemctl reload caddy`
  (in the Docker setup, `docker compose exec caddy caddy reload`).

### Serving the GoAccess report at /stats (optional, password-protected)

You can serve a GoAccess HTML report through this same Caddy at
`https://pro.pwllhelisailingclub.org/stats`. A commented `handle /stats*` block is
ready in the `Caddyfile`. Analytics data is private, so keep it behind auth.

**Packages** (not installed by `setup.sh` — the report is optional): you need
**GoAccess ≥ 1.9.2 built with GeoIP2**. Debian's packaged GoAccess is older, so
install from the official GoAccess apt repo (see goaccess.io/download); check with
`goaccess --version | grep -i geo`. Also useful:

```
sudo apt install jq geoipupdate   # jq: only for the pre-1.9.2 fallback path.
                                  # geoipupdate: keeps the GeoLite2 databases fresh.
```

To turn it on:

1. **Generate the report on a schedule** with `relay_stats.sh` (in this folder;
   copy it to the relay, e.g. `/opt/relay/relay_stats.sh`, and `chmod +x` it):
   ```
   */10 * * * * /opt/relay/relay_stats.sh 2>/dev/null
   ```
   The script reads the current + rolled `.gz` logs and auto-detects the GoAccess
   version: on **GoAccess ≥ 1.9.2** it uses the built-in `--log-format=CADDY`
   (which reads `client_ip` — the real visitor); on older GoAccess it falls back
   to reshaping each line with `jq` into Combined Log Format, because pre-1.9.2
   `--log-format=CADDY` reads `remote_ip`, which behind the tunnel is always the
   cloudflared loopback (`127.0.0.1`) and would map every visitor to localhost.
   Either way you get real visitor IPs. (The `jq` fallback needs `jq` installed.)
2. **(Optional) geolocation:** drop MaxMind GeoLite2 `.mmdb` files into
   `/opt/relay/geoip/` (`GeoLite2-City.mmdb` for location, `GeoLite2-ASN.mmdb` for
   networks — free MaxMind account, or `geoipupdate`). `relay_stats.sh` picks them
   up automatically and adds Country/City/ASN panels. Requires a GoAccess built
   with GeoIP2 (`goaccess --version | grep -i geo`).
3. **Make a password:** `caddy hash-password`, copy the bcrypt hash.
4. **Enable the block:** uncomment `handle /stats*` in the `Caddyfile`, paste the
   hash over `PASTE_BCRYPT_HASH_HERE` (directive is `basic_auth` on Caddy ≥ 2.8,
   `basicauth` on older), then `caddy validate` and `sudo systemctl reload caddy`.

The block is `log_skip`-ped so your own report views don't pollute the stats. Do
**not** leave `/stats` unauthenticated — it exposes visitor data. For a stronger
gate, put **Cloudflare Access** in front of the `/stats` path instead of, or as
well as, basic auth.

## Troubleshooting (things that bite, and why)

These are the non-obvious settings the working setup depends on — check here first.

- **No video, player stuck "Connecting…", HLS never plays** — must be **mpegts** HLS, not
  low-latency. Low-latency HLS uses chunked/blocking playlist delivery that Cloudflare
  doesn't proxy, so it stalls behind the CDN. (`hlsVariant: mpegts` in `mediamtx.yml`.)
- **Playlist 404s through the proxy / cookie-check redirect loops** — Caddy must proxy at
  `/hut/*` **without stripping the prefix**. MediaMTX redirects to `/hut/index.m3u8?…`; a
  stripped prefix (`handle_path /hls/*`) breaks it.
- **`Timestamps are unset` / `Non-monotonous DTS`, unplayable segments** — the camera RTSP
  has no usable PTS; `relay_branded_source.py` fixes it with
  `-use_wallclock_as_timestamps 1` on the input.
- **`RTP packets lost` / stream tears down every ~30 s / lots of buffering** — publish to
  MediaMTX over **TCP** (`-rtsp_transport tcp` on the ffmpeg output) and give the input a
  buffer (`-thread_queue_size 512`). Also: don't `-loop` the logo images at full frame
  rate — a single `-i` per image with `overlay …:eof_action=repeat` holds them cheaply.
- **`branding manifest fetch failed (403)`** — Cloudflare's bot/WAF blocks the default
  `Python-urllib` user-agent; the script sends a normal `User-Agent` (curl works, plain
  urllib doesn't). Also make sure Public branding is **enabled** in the app.
- **Branding wrong place / too small** — the overlay mirrors the app's
  `build_branding_overlay_filter_for_size` (club top-left, sponsors top-right rotating,
  ~15 % of frame height); it probes the camera resolution so proportions match.
- **`cf-cache-status: BYPASS` on segments** — Cloudflare won't cache a response that (a)
  has `Set-Cookie` or (b) says `Cache-Control: private/no-cache`. MediaMTX sends both, so
  Caddy strips `Set-Cookie` and replaces `Cache-Control` on segments (`header_down`), and
  the Cloudflare rule uses **Edge TTL: Override origin**. Also confirm **Development Mode
  is off** and no Page Rule "Bypass Cache on Cookie" matches (the requests carry the
  `cookieCheck`/`hlsSession` cookies).
- **Caddy won't start** — port 80 in a container is fine; if running Caddy by hand instead,
  `:2019 address already in use` = another Caddy/service already running, and
  `:80 permission denied` = run it as the packaged service (which has the privilege).
- Harmless: `hlsAllowOrigin is deprecated`, `RTP packets are too big … remuxing`,
  `deprecated pixel format` — ignore.
- **Stream won't start; `runOnDemand … timed out` in a loop, with ffmpeg `Invalid
  data found` / `KeyboardInterrupt`** — the branded source didn't publish within
  `runOnDemandStartTimeout`, so MediaMTX killed it mid-startup (those errors are the
  kill, not the cause). First confirm the camera itself is fine — a direct pull
  should stream instantly:
  `ffmpeg -rtsp_transport tcp -i "$CAMERA_URL" -t 3 -f null -`. If that works, the
  branded startup is just too slow: it makes two RTSP connects over the tunnel
  (probe + ODM frame grab) plus manifest/logo fetches. Raise
  `runOnDemandStartTimeout` (now 30s) and `systemctl restart mediamtx`, or set
  `relay_overlay_enabled: false` in `startline_config.json` to drop the overlay's
  extra frame grab. A `cloudflared`/`ffmpeg` upgrade nudging startup past a tight
  timeout is a known trigger.
- **`probe failed (… timed out)` / `Could not find codec parameters … unspecified
  size` / "Consider increasing … analyzeduration/probesize`** — a newer ffmpeg
  defaults `analyzeduration` to 0 and gives up before it has read the camera
  sub-stream's H.264 dimensions, so `ffprobe` burns its whole timeout and the encode
  never starts. The relay scripts now pass `-analyzeduration 10M -probesize 10M` on
  every RTSP read (`relay_branded_source.py` `PROBE_ARGS`/`INPUT` and
  `relay_startline.py` `_grab_frame`) to fix this — make sure `/opt/relay` has the
  current copies (`git pull` on the repo, then copy them over) and
  `systemctl restart mediamtx`.
