/* Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE. */
/*
 * The public competitor pages as a home-screen app: the offer to add them.
 *
 * Android (and desktop Chrome) tell the page when it can be installed, and let
 * the page ask at a moment of its own choosing: that is `beforeinstallprompt`.
 * An iPhone tells the page nothing and has no way to ask, so there the best
 * that can be done is to say where the button is, once.
 *
 * Either way it is one quiet line above the page, it goes away for good when
 * dismissed, and it never shows inside the installed app.
 */
(function () {
  'use strict';

  const DISMISS_KEY = 'roInstallHint';

  // Storage can be switched off (private browsing): then the offer simply shows
  // again next visit, which is better than the page breaking.
  function remembered() {
    try { return window.localStorage.getItem(DISMISS_KEY) === 'dismissed'; } catch (err) { return false; }
  }
  function remember() {
    try { window.localStorage.setItem(DISMISS_KEY, 'dismissed'); } catch (err) { /* shown again next time */ }
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

  let bar = null;
  let installEvent = null;

  function removeBar() {
    if (bar) bar.remove();
    bar = null;
  }

  function showBar(text, actionLabel, onAction) {
    if (bar || remembered() || standalone()) return;
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
    close.addEventListener('click', () => { remember(); removeBar(); });
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
        if (choice && choice.outcome === 'dismissed') remember();
      } catch (err) { /* the browser declined to ask; nothing to tidy up */ }
    });
  });

  window.addEventListener('appinstalled', () => { remember(); removeBar(); });

  function start() {
    document.documentElement.classList.toggle('app-standalone', standalone());
    if (isIos() && !standalone()) {
      showBar('Keep this on your home screen: tap the Share button, then “Add to Home Screen”.');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
