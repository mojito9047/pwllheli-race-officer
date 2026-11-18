/* Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE. */
/*
 * The public competitor pages as a home-screen app: the offer to add them, and
 * the switch for notifications.
 *
 * ADDING TO THE HOME SCREEN
 *
 * Android (and desktop Chrome) tell the page when it can be installed, and let
 * the page ask at a moment of its own choosing: that is `beforeinstallprompt`.
 * An iPhone tells the page nothing and has no way to ask, so there the best
 * that can be done is to say where the button is, once.
 *
 * Either way it is one quiet line above the page, it goes away for good when
 * dismissed, and it never shows inside the installed app.
 *
 * NOTIFICATIONS
 *
 * When the club has switched them on, the page carries the key to subscribe
 * with (a <meta name="ro-push-key">) and a place for the switch
 * (#publicNotify). Pressing it asks the browser's permission, registers the
 * service worker (/public/sw.js, which is what shows a notification while the
 * page is closed) and sends the subscription to the hut with two choices: race
 * notices, and the favourite boats. Nothing is asked for but the say-so.
 *
 * An iPhone only allows this for a page that has been added to the home
 * screen, so in Safari the switch explains that instead of failing.
 */
(function () {
  'use strict';

  const DISMISS_KEY = 'roInstallHint';

  // Storage can be switched off (private browsing): then the offer simply shows
  // again next visit, which is better than the page breaking.
  function stored(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }
  function store(key, value) {
    try { window.localStorage.setItem(key, value); } catch (err) { /* this visit only */ }
  }

  function standalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
      || window.navigator.standalone === true;
  }

  // An iPad reports itself as a Mac; the touch points give it away.
  function isIos() {
    const ua = window.navigator.userAgent || '';
    return /iPhone|iPad|iPod/.test(ua)
      || (window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);
  }

  // --- Adding to the home screen -------------------------------------------

  let bar = null;
  let installEvent = null;

  function removeBar() {
    if (bar) bar.remove();
    bar = null;
  }

  function showBar(text, actionLabel, onAction) {
    if (bar || stored(DISMISS_KEY) === 'dismissed' || standalone()) return;
    const main = document.querySelector('main');
    if (!main) return;
    bar = document.createElement('div');
    bar.className = 'app-install';
    bar.setAttribute('role', 'note');
    const message = document.createElement('span');
    message.className = 'app-install-text';
    message.textContent = text;
    bar.appendChild(message);
    if (actionLabel) {
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'button small';
      action.textContent = actionLabel;
      action.addEventListener('click', onAction);
      bar.appendChild(action);
    }
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'button small secondary app-install-close';
    close.setAttribute('aria-label', 'Do not show this again');
    close.textContent = 'Not now';
    close.addEventListener('click', () => { store(DISMISS_KEY, 'dismissed'); removeBar(); });
    bar.appendChild(close);
    main.insertBefore(bar, main.firstChild);
  }

  window.addEventListener('beforeinstallprompt', ev => {
    // Hold the browser's own banner back and offer it in the page instead,
    // where it can be turned down once and stay turned down.
    ev.preventDefault();
    installEvent = ev;
    showBar('Keep this on your home screen, like an app.', 'Add to home screen', async () => {
      if (!installEvent) return;
      const prompt = installEvent;
      installEvent = null;
      removeBar();
      try {
        prompt.prompt();
        const choice = await prompt.userChoice;
        if (choice && choice.outcome === 'dismissed') store(DISMISS_KEY, 'dismissed');
      } catch (err) { /* the browser declined to ask; nothing to tidy up */ }
    });
  });

  window.addEventListener('appinstalled', () => { store(DISMISS_KEY, 'dismissed'); removeBar(); });

  // --- Notifications ---------------------------------------------------------

  const PREFS_KEY = 'roNotify';        // {notices, boats}: which news this phone wants
  const SYNC_KEY = 'roNotifySent';     // what the hut was last told, and when
  const RESEND_AFTER_MS = 24 * 3600 * 1000;

  let pushMeta = null;                 // the <meta> carrying the key and the addresses
  let line = null;                     // where the switch is drawn
  let busy = false;
  let note = '';                       // one line of explanation after something went wrong

  function pushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  function prefs() {
    let saved = {};
    try { saved = JSON.parse(stored(PREFS_KEY) || '{}') || {}; } catch (err) { saved = {}; }
    return {notices: saved.notices !== false, boats: saved.boats !== false};
  }

  function favouriteKeys() {
    return window.PublicFollow ? window.PublicFollow.list().map(f => f.key) : [];
  }

  // The key arrives as base64url text; the browser wants the bytes.
  function keyBytes(text) {
    const padded = (text + '='.repeat((4 - text.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    const raw = window.atob(padded);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  // Whether a subscription was made with the club's present key. If the hut's
  // key pair was ever lost and made again, an old subscription can no longer be
  // sent to, and has to be replaced.
  function madeWithOurKey(subscription) {
    const theirs = subscription.options && subscription.options.applicationServerKey;
    if (!theirs) return true;                    // the browser does not say: assume so
    const a = new Uint8Array(theirs), b = keyBytes(pushMeta.content);
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }

  async function worker() {
    return navigator.serviceWorker.register(pushMeta.dataset.worker, {scope: '/public/'});
  }

  async function currentSubscription() {
    const registration = await navigator.serviceWorker.getRegistration('/public/');
    return registration ? registration.pushManager.getSubscription() : null;
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify(body),
    });
    return response.ok;
  }

  // Tell the hut what this phone wants. Returns whether it was accepted.
  async function tellTheHut(subscription) {
    const choice = prefs();
    const body = {subscription: subscription.toJSON(), notices: choice.notices, boats: choice.boats,
                  boat_keys: favouriteKeys()};
    const ok = await post(pushMeta.dataset.subscribe, body);
    if (ok) {
      store(SYNC_KEY, JSON.stringify({
        at: Date.now(),
        what: JSON.stringify([subscription.endpoint, body.notices, body.boats, body.boat_keys]),
      }));
    }
    return ok;
  }

  // Sent again when what the phone wants has changed, and once a day regardless,
  // so the hut's list does not drift from what the phone believes.
  async function keepInStep(subscription) {
    const choice = prefs();
    const what = JSON.stringify([subscription.endpoint, choice.notices, choice.boats, favouriteKeys()]);
    let last = {};
    try { last = JSON.parse(stored(SYNC_KEY) || '{}') || {}; } catch (err) { last = {}; }
    if (last.what === what && Date.now() - (last.at || 0) < RESEND_AFTER_MS) return;
    try { await tellTheHut(subscription); } catch (err) { /* next page load will try again */ }
  }

  async function turnOn() {
    if (busy) return;
    busy = true;
    note = '';
    drawSwitch(null);
    try {
      const permission = await window.Notification.requestPermission();
      if (permission === 'granted') {
        const registration = await worker();
        await navigator.serviceWorker.ready;
        let subscription = await registration.pushManager.getSubscription();
        if (subscription && !madeWithOurKey(subscription)) {
          await subscription.unsubscribe();
          subscription = null;
        }
        if (!subscription) {
          subscription = await registration.pushManager.subscribe(
            {userVisibleOnly: true, applicationServerKey: keyBytes(pushMeta.content)});
        }
        if (!await tellTheHut(subscription)) {
          // The browser said yes and the hut did not take it: do not leave the
          // phone believing it is subscribed to something that will never arrive.
          await subscription.unsubscribe();
          note = 'Notifications could not be turned on just now. Try again in a moment.';
        }
      }
    } catch (err) {
      note = 'Notifications could not be turned on on this device.';
    }
    busy = false;
    refreshSwitch();
  }

  async function turnOff() {
    if (busy) return;
    busy = true;
    try {
      const subscription = await currentSubscription();
      if (subscription) {
        try { await post(pushMeta.dataset.unsubscribe, {endpoint: subscription.endpoint}); }
        catch (err) { /* the hut drops it anyway the first time a message bounces */ }
        await subscription.unsubscribe();
      }
    } catch (err) { /* nothing more to undo */ }
    busy = false;
    note = '';
    refreshSwitch();
  }

  function button(label, onClick, secondary) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'button small' + (secondary ? ' secondary' : '');
    b.textContent = label;
    b.disabled = busy;
    b.addEventListener('click', onClick);
    return b;
  }

  function tick(label, key) {
    const wrap = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = prefs()[key];
    box.addEventListener('change', async () => {
      const choice = prefs();
      choice[key] = box.checked;
      store(PREFS_KEY, JSON.stringify(choice));
      const subscription = await currentSubscription();
      if (subscription) keepInStep(subscription);
    });
    wrap.appendChild(box);
    wrap.appendChild(document.createTextNode(' ' + label));
    return wrap;
  }

  function say(text) {
    const span = document.createElement('span');
    span.className = 'notify-note';
    span.textContent = text;
    return span;
  }

  // subscription: the phone's present subscription, null for none, undefined
  // when that is not known yet.
  function drawSwitch(subscription) {
    if (!line) return;
    line.textContent = '';
    line.hidden = false;
    if (!pushSupported()) {
      if (isIos() && !standalone()) {
        line.appendChild(say('Notifications: on an iPhone, first add this page to your home screen '
          + '(Share, then “Add to Home Screen”), then open it from there and turn them on.'));
      } else {
        line.hidden = true;
      }
      return;
    }
    if (window.Notification.permission === 'denied') {
      line.appendChild(say('Notifications are blocked for this site in your browser’s settings.'));
      return;
    }
    if (subscription) {
      const on = document.createElement('strong');
      on.textContent = 'Notifications on';
      line.appendChild(on);
      line.appendChild(tick('Race notices', 'notices'));
      line.appendChild(tick('My favourite boats', 'boats'));
      line.appendChild(button('Turn off', turnOff, true));
    } else {
      line.appendChild(button(busy ? 'Turning on…' : 'Turn on notifications', turnOn, false));
      line.appendChild(say(note || 'Race starts, postponements and results, and your favourite boats finishing.'));
    }
  }

  async function refreshSwitch() {
    if (!line || !pushMeta) return;
    if (!pushSupported()) { drawSwitch(null); return; }
    let subscription = null;
    try {
      subscription = await currentSubscription();
      if (subscription && !madeWithOurKey(subscription)) {
        // The hut's key has changed. Permission still stands, so put it right
        // without troubling anybody.
        await subscription.unsubscribe();
        subscription = null;
        if (window.Notification.permission === 'granted') {
          const registration = await worker();
          await navigator.serviceWorker.ready;
          subscription = await registration.pushManager.subscribe(
            {userVisibleOnly: true, applicationServerKey: keyBytes(pushMeta.content)});
          if (!await tellTheHut(subscription)) { await subscription.unsubscribe(); subscription = null; }
        }
      } else if (subscription) {
        keepInStep(subscription);
      }
    } catch (err) {
      subscription = null;
    }
    drawSwitch(subscription);
  }

  // --- Start -----------------------------------------------------------------

  function start() {
    document.documentElement.classList.toggle('app-standalone', standalone());
    if (isIos() && !standalone()) {
      showBar('Keep this on your home screen: tap the Share button, then “Add to Home Screen”.');
    }
    pushMeta = document.querySelector('meta[name="ro-push-key"]');
    line = document.getElementById('publicNotify');
    if (pushMeta && pushMeta.content && line) {
      refreshSwitch();
      // Starring a boat changes what this phone should be told about.
      if (window.PublicFollow) {
        window.PublicFollow.onChange(async () => {
          if (!pushSupported()) return;
          try {
            const subscription = await currentSubscription();
            if (subscription) keepInStep(subscription);
          } catch (err) { /* the next page load will catch up */ }
        });
      }
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
