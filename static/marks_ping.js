/* Copyright © 2026 CapeNet Ltd. All Rights Reserved. */
/*
 * Setting a mark's position from a phone on the water.
 *
 * `watchPosition`, not `getCurrentPosition`: the first fix a phone offers is
 * often the worst one it has — a cached network position, or a cold GPS still
 * converging — and the accuracy improves over the following seconds. Watching
 * lets the reading settle while the driver holds station, and lets the page show
 * how good the fix is *before* anything is written.
 *
 * Nothing is sent until the button is pressed, and the button says how far the
 * mark is about to move. In a small boat in a seaway that matters more than
 * saving a tap.
 */
(function () {
  'use strict';

  // Below this the fix is good enough to write over a surveyed position. Kept in
  // step with MAX_ACCURACY_M in core/marks.py, which enforces it server-side —
  // this copy only decides what the button looks like.
  const GOOD_ACCURACY_M = 25;
  const CONFIRM_MOVE_M = 2000;      // matches IMPLAUSIBLE_MOVE_M server-side

  // A phone remembers a "Don't Allow" tapped once, months ago, and never asks
  // again — so the page has to say where the setting is rather than just that
  // there is one. Which setting, though, depends on the browser, and getting it
  // wrong sends somebody in a RIB to a screen that does not exist.
  //
  // The one that catches people: on an iPhone every browser needs the *phone's*
  // permission separately, under its own name in Location Services. Allowing it
  // for Safari does nothing for Chrome, so the page can work in one browser and
  // be dead in the other on the same handset, which reads as a broken app.
  const UA = navigator.userAgent;
  const IOS = /iPhone|iPad|iPod/.test(UA)
              // iPadOS reports itself as a Mac; a touch screen gives it away.
              || (/Macintosh/.test(UA) && navigator.maxTouchPoints > 1);
  const IOS_CHROME = IOS && /CriOS/.test(UA);
  const IOS_FIREFOX = IOS && /FxiOS/.test(UA);
  const IOS_EDGE = IOS && /EdgiOS/.test(UA);

  // What the browser is called in Settings → Privacy & Security → Location
  // Services. Safari's entry is "Safari Websites"; the others use their own name.
  const IOS_APP_NAME = IOS_CHROME ? 'Chrome'
                     : IOS_FIREFOX ? 'Firefox'
                     : IOS_EDGE ? 'Edge'
                     : 'Safari Websites';

  function blockedAdvice() {
    if (!IOS) {
      return 'Location is blocked for this site. Allow it for this site in the '
             + 'browser settings, check the device has location switched on, '
             + 'then tap Try again.';
    }
    const settings = 'Check Settings → Privacy & Security → Location Services is '
                     + 'on, and that ' + IOS_APP_NAME + ' in that list is set to '
                     + '"While Using the App" — on an iPhone each browser is asked '
                     + 'separately, so allowing it for one does not cover another.';
    if (IOS_CHROME || IOS_FIREFOX || IOS_EDGE) {
      // Deliberately no in-browser menu path here: these move between versions,
      // and once the phone-level permission is on, the browser asks by itself.
      return settings + ' Then reload this page and allow location when '
             + (IOS_CHROME ? 'Chrome' : IOS_FIREFOX ? 'Firefox' : 'Edge')
             + ' asks for it.';
    }
    return 'Location is blocked for this site. Tap the page settings icon at the '
           + 'left of the address bar, then Website Settings → Location → Allow, '
           + 'and tap Try again. If that already says Allow: ' + settings;
  }

  const BLOCKED_ADVICE = blockedAdvice();

  function metresBetween(aLat, aLon, bLat, bLon) {
    const mLat = 111320;
    const mLon = 111320 * Math.cos((aLat + bLat) / 2 * Math.PI / 180);
    return Math.hypot((aLat - bLat) * mLat, (aLon - bLon) * mLon);
  }

  function init(root) {
    const sel = root.querySelector('.ping-mark');
    const btn = root.querySelector('.ping-set');
    const fixValue = root.querySelector('.ping-fix-value');
    const fixAcc = root.querySelector('.ping-fix-accuracy');
    const current = root.querySelector('.ping-current');
    const currentValue = root.querySelector('.ping-current-value');
    const distance = root.querySelector('.ping-distance');
    const result = root.querySelector('.ping-result');
    const warning = root.querySelector('.ping-warning');
    const warnDetail = root.querySelector('.ping-warning-detail');
    const startBtn = root.querySelector('.ping-start');

    let fix = null;          // the latest position the phone has given us
    let busy = false;

    function say(text, kind) {
      result.textContent = text || '';
      result.className = 'ping-result' + (kind ? ' ' + kind : '');
    }

    function refuse(detail) {
      warning.hidden = false;
      warnDetail.textContent = detail;
      btn.disabled = true;
    }

    function describeFix() {
      if (!fix) {
        fixValue.textContent = 'waiting for GPS…';
        fixAcc.textContent = '';
        return;
      }
      fixValue.textContent = fix.lat.toFixed(5) + ', ' + fix.lon.toFixed(5);
      const good = fix.accuracy <= GOOD_ACCURACY_M;
      fixAcc.textContent = '±' + Math.round(fix.accuracy) + ' m'
        + (good ? '' : ' — too vague to set a mark by, waiting…');
      fixAcc.className = 'ping-fix-accuracy' + (good ? ' good' : ' poor');
    }

    function refresh() {
      const opt = sel.selectedOptions[0];
      const code = sel.value;
      describeFix();
      if (!code) {
        current.hidden = true;
        btn.disabled = true;
        btn.textContent = 'Set mark to my position';
        return;
      }
      const markLat = parseFloat(opt.dataset.lat);
      const markLon = parseFloat(opt.dataset.lon);
      current.hidden = false;
      currentValue.textContent = markLat.toFixed(5) + ', ' + markLon.toFixed(5);

      if (!fix) {
        distance.textContent = '';
        btn.disabled = true;
        btn.textContent = 'Waiting for a position…';
        return;
      }
      const moved = metresBetween(fix.lat, fix.lon, markLat, markLon);
      distance.textContent = 'You are ' + (moved < 1000
        ? Math.round(moved) + ' m' : (moved / 1000).toFixed(1) + ' km') + ' from it.';
      distance.className = 'ping-distance' + (moved > CONFIRM_MOVE_M ? ' warn' : '');

      const usable = fix.accuracy <= GOOD_ACCURACY_M;
      btn.disabled = busy || !usable;
      btn.textContent = usable
        ? 'Move ' + code + ' ' + (moved < 1000 ? Math.round(moved) + ' m' : (moved / 1000).toFixed(1) + ' km') + ' to here'
        : 'Waiting for a better fix…';
    }

    // --- getting a position ----------------------------------------------
    // Two things learned the hard way, both of which left the page sitting on
    // "waiting for GPS…" with nothing to say for itself:
    //
    //  * several mobile browsers only put up the location prompt in response to
    //    a **tap**. Called on page load, the request is ignored — no permission
    //    dialog, no success callback, and no error callback either. So there is
    //    an explicit button, and the automatic attempt is a bonus rather than
    //    the only route.
    //  * a phone has no console. Anything the page cannot do, it has to say on
    //    screen, including which of these branches it took.
    let watchId = null;
    let waitTimer = null;

    function status(text) {
      const el = root.querySelector('.ping-status');
      if (el) el.textContent = text || '';
    }

    function stopWaitTimer() {
      if (waitTimer) { window.clearTimeout(waitTimer); waitTimer = null; }
    }

    function onFix(pos) {
      stopWaitTimer();
      fix = {lat: pos.coords.latitude, lon: pos.coords.longitude,
             accuracy: pos.coords.accuracy};
      warning.hidden = true;
      status('');
      startBtn.hidden = true;
      refresh();
    }

    function onFixError(err) {
      stopWaitTimer();
      startBtn.hidden = false;
      startBtn.textContent = 'Try again';
      if (err && err.code === err.PERMISSION_DENIED) {
        refuse(BLOCKED_ADVICE);
      } else if (err && err.code === err.TIMEOUT) {
        refuse('No position yet. Under cover or below decks a phone can take a while '
               + '— go outside, wait a moment, then tap Try again.');
      } else {
        refuse('The phone could not give a position'
               + (err && err.message ? ' (' + err.message + ')' : '') + '.');
      }
    }

    function requestPosition() {
      if (!navigator.geolocation) {
        refuse('This browser has no location support.');
        return;
      }
      if (!window.isSecureContext) {
        // Browsers withhold geolocation from insecure origins entirely. Say so
        // with the address in it, because the difference between the club's
        // https address and a plain http one on the LAN is not obvious on a
        // phone where the address bar is half hidden.
        refuse('This page is open at ' + window.location.origin + ', which is not a '
               + 'secure address, and a browser will not give a position to one. Open '
               + 'it at the https address for the club instead.');
        return;
      }
      warning.hidden = true;
      status('Asking for your position…');
      startBtn.textContent = 'Getting position…';
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      watchId = navigator.geolocation.watchPosition(
        onFix, onFixError,
        {enableHighAccuracy: true, maximumAge: 0, timeout: 30000});
      // Belt and braces: some browsers neither succeed nor call the error
      // handler. Never leave the page silent.
      //
      // This is the branch Chrome on iOS lands in. Its permissions API answers
      // for the WebKit engine underneath rather than for Chrome's own state, so
      // a phone-level block is not reported as "denied" and no error callback
      // arrives either — the timer is the only thing left that can speak, so it
      // has to give the full advice rather than a shrug.
      stopWaitTimer();
      waitTimer = window.setTimeout(() => {
        if (!fix) {
          startBtn.hidden = false;
          startBtn.textContent = 'Try again';
          status('No answer from the phone after 12 seconds, and no permission '
                 + 'prompt appeared.');
          refuse(BLOCKED_ADVICE);
        }
      }, 12000);
    }

    startBtn.addEventListener('click', requestPosition);

    // Report the permission state where it can be read, since it explains most
    // of the ways this goes wrong and cannot be seen any other way on a phone.
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({name: 'geolocation'}).then(p => {
        if (p.state === 'denied') {
          refuse(BLOCKED_ADVICE);
          startBtn.hidden = false;
          startBtn.textContent = 'Try again';
          // The watchdog is already running from the automatic attempt, and
          // there is nothing left for it to say: "still waiting" underneath a
          // box explaining that it will never arrive reads as a contradiction.
          stopWaitTimer();
          status('');
        }
      }).catch(() => {});
    }

    // A phone has no console, so the facts needed to diagnose this go on the page.
    // The browser is named because the same handset can work in one and not in
    // another, and because which browser it is decides which advice is right.
    const diag = root.querySelector('.ping-diag');
    if (diag) {
      const browser = IOS_CHROME ? 'Chrome/iOS'
                    : IOS_FIREFOX ? 'Firefox/iOS'
                    : IOS_EDGE ? 'Edge/iOS'
                    : IOS ? 'Safari/iOS' : 'other browser';
      diag.textContent = window.location.origin
        + (window.isSecureContext ? ' · secure' : ' · NOT secure')
        + ' · ' + browser
        + (navigator.geolocation ? '' : ' · no geolocation support');
    }

    requestPosition();      // works where a browser allows it without a tap

    sel.addEventListener('change', () => { say(''); refresh(); });

    btn.addEventListener('click', async () => {
      const code = sel.value;
      if (!code || !fix || busy) return;
      const opt = sel.selectedOptions[0];
      const moved = metresBetween(fix.lat, fix.lon,
                                  parseFloat(opt.dataset.lat), parseFloat(opt.dataset.lon));
      if (!window.confirm('Move ' + code + ' to your position?\n\n'
                          + 'It moves ' + Math.round(moved) + ' m.\n'
                          + 'This changes every course that uses this mark.')) return;

      busy = true;
      btn.disabled = true;
      say('Setting…');
      try {
        const res = await fetch('/marks/' + encodeURIComponent(code) + '/position', {
          method: 'POST',
          // The app accepts a CSRF token from this header as well as from a
          // form field, which is what makes a JSON post from a page possible.
          headers: {'Content-Type': 'application/json',
                    'X-CSRFToken': root.dataset.csrf || ''},
          body: JSON.stringify({lat: fix.lat, lon: fix.lon, accuracy_m: fix.accuracy,
                                confirm: moved > CONFIRM_MOVE_M}),
        });
        const body = await res.json().catch(() => ({}));
        if (res.ok && body.ok) {
          say(body.message || 'Set.', 'good');
          // The option carries the position the page compares against, so it has
          // to move too, or the distance would keep reading from the old one.
          opt.dataset.lat = String(fix.lat);
          opt.dataset.lon = String(fix.lon);
        } else {
          say(body.message || 'Could not set the position.', 'bad');
        }
      } catch (err) {
        say('No answer from the app — check the signal and try again.', 'bad');
      }
      busy = false;
      refresh();
    });

    refresh();
  }

  document.querySelectorAll('[data-ping]').forEach(init);
})();
