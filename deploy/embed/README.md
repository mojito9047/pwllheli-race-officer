# Embedding the start-hut camera on the club website

`club_website_camera.html` is a self-contained page showing the view from the
start hut, for `https://pwllhelisailingclub.co.uk`. Upload it anywhere on the
club site and embed it:

```html
<iframe src="/club_website_camera.html" title="Start hut camera"
        style="width:100%;max-width:960px;aspect-ratio:16/9;border:0"
        loading="lazy" referrerpolicy="no-referrer"
        allow="autoplay; fullscreen"></iframe>
```

No libraries, fonts, cookies or trackers, and nothing loaded from any other
host.

## How it behaves, and why

It shows **the live stream**, with refreshing stills only while that stream is
starting. The stills are a waiting room, not the exhibit.

That split follows who pays for what:

| | Comes from | Cost of another viewer |
| --- | --- | --- |
| A still, 88 KB | the **hut**, over a domestic line also recording the racing | another 88 KB per refresh, off the hut |
| The stream | the **relay**, fanned out by Cloudflare | nothing — MediaMTX pulls the camera once however many are watching |

So the first person to look starts the stream and pays for a handful of stills
while MediaMTX spins up; everybody after that costs the hut nothing at all.
Measured on a stub of the relay: **one** snapshot request during startup, then
none for as long as the video plays.

It also stops entirely while the tab is in the background or scrolled out of
sight, which is how the relay learns nobody is watching and drops the stream —
and after fifteen minutes it stops and waits to be asked, so a page left open
on a kitchen tablet is not holding the camera open all afternoon.

### The three cases

| The relay… | What you see | What the hut is asked for |
| --- | --- | --- |
| plays | the video | one still during startup, then nothing |
| answers, still starting | refreshing stills | a still every 4 s until it plays |
| **says nothing** | **one still, and a caption saying the live view is unavailable** | **one still, then nothing** |

The third is the case worth understanding. Silence means the browser refused
the frame, which means this origin is not in the relay's `frame-ancestors` —
a misconfiguration, not a bad afternoon. Falling back to refreshing stills for
ever would hide that while billing the club's uplink for it, on every visitor,
indefinitely. So it takes one picture, says so, writes the diagnosis to the
browser console, and asks for nothing more until the page is reloaded.

Either way **the club site never shows an empty box** — there is always a
picture of the bay.

## The relay has to allow the club's domain

A browser refuses to frame the relay from an origin the relay does not list, and
shows nothing at all — so this is not a soft failure. The list is in
`deploy/live_stream/Caddyfile`:

```
header Content-Security-Policy "frame-ancestors 'self' https://*.pwllhelisailingclub.org https://pwllhelisailingclub.co.uk https://www.pwllhelisailingclub.co.uk http://localhost:5050 http://127.0.0.1:5050"
```

Both the bare domain and `www` are there because either may serve the page and
they are separate origins. To apply it on the relay:

```bash
cd /opt/relay-config && git pull
sudo install -m 0644 deploy/live_stream/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

(See `deploy/live_stream/README.md` §5a for the canonical version of those
steps.)

**If the video never appears and the stills carry on for ever, this is the first
thing to check.** In the browser console on the club site you will see a
`frame-ancestors` refusal.

## Still worth doing: publish the stills to R2

The stills come from the hut, and although only the first viewer needs them,
that is still the hut's uplink during the seconds when several people arrive at
once. The app can publish the same picture to Cloudflare R2 instead. It is
currently **switched off** — `/public/video/status` reports
`public_live_r2: enabled: false`.

Turning it on (Settings → Video, public live image → R2, with the credentials
already there for video publishing) and pointing `data-snapshot-src` at the R2
URL removes the hut from the picture entirely, in every state.

## Settings on the page

On the `<figure>`:

| Attribute | Default | What it does |
| --- | --- | --- |
| `data-stream-page` | the relay's `/live` | the video, iframed |
| `data-snapshot-src` | the hut's public frame | the stills shown while it starts |
| `data-snapshot-refresh-ms` | `4000` | brisk, because it only runs for a few seconds |
| `data-blocked-ms` | `8000` | silence this long means the frame was refused |
| `data-starting-ms` | `120000` | how long to let MediaMTX spin up before showing the player anyway |
| `data-idle-stop-ms` | `900000` | give up after this and wait to be asked |
