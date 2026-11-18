/* Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE. */
/*
 * The service worker for the public competitor pages. Served at /public/sw.js.
 *
 * It does two things and nothing else: shows a notification when the hut sends
 * one, and opens the race when the notification is tapped.
 *
 * There is deliberately no `fetch` handler and no cache. A service worker that
 * answers page requests from a cache is how a web app comes to show yesterday's
 * race with nothing on the screen to say so, and on these pages being current is
 * the whole point. Every page and every reading still comes from the hut.
 *
 * What arrives is JSON from core/notify.py: {title, body, url, tag}.
 */

self.addEventListener('install', () => {
  // Nothing to prepare, so take over as soon as it is installed rather than
  // waiting for every open tab to close.
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', event => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch (err) {
    // Not JSON. Shown as a plain notice rather than dropped: a browser that is
    // woken for a push and shows nothing counts it against the site.
    message = {body: event.data ? event.data.text() : ''};
  }
  const title = message.title || 'Race notice';
  event.waitUntil(self.registration.showNotification(title, {
    body: message.body || '',
    // Same tag, same slot on the phone: a later notice of the same kind for the
    // same race replaces the earlier one instead of stacking under it.
    tag: message.tag || undefined,
    icon: '/public/app-icon/192.png',
    badge: '/public/app-icon/192.png',
    data: {url: message.url || '/public/current'},
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || '/public/current',
                         self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window'});
    // A competitor page that is already open is brought forward and taken to
    // the race, rather than a second copy being opened beside it. Only a
    // competitor page: the race office lives at the same address, and a
    // notification must never take a race officer's screen away from them.
    for (const client of windows) {
      const where = new URL(client.url);
      if (where.origin !== self.location.origin || !where.pathname.startsWith('/public/')) continue;
      try {
        if (client.navigate) await client.navigate(target);
        return client.focus();
      } catch (err) { /* that window would not be moved: open a new one */ }
    }
    return self.clients.openWindow(target);
  })());
});
