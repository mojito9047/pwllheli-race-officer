/* Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE. */
/*
 * Favourite boats on the public competitor pages.
 *
 * A competitor follows their own boat; the family ashore follow one or two. The
 * favourites are kept on the phone that chose them -- localStorage, nothing sent
 * to the club -- and shared by every public page, so a boat starred on one race
 * is still starred on the next.
 *
 * A favourite is remembered by the key the server hands out with each boat (its
 * sail number, tidied: see core/publicapp.py), never by an entry id, because a
 * boat is a different entry in every race. This file does not work the key out;
 * it only stores what it is given.
 *
 * Any row that carries data-boat-key gets a star (decorate), any star anywhere
 * on the page toggles (one delegated handler), and everything that shows a
 * favourite is brought up to date together (refresh).
 */
(function () {
  'use strict';

  const STORE_KEY = 'roFavourites';
  const MAX_FAVOURITES = 12;        // a phone's worth; the oldest makes way for a thirteenth
  const listeners = [];
  let held = null;

  function read() {
    if (held) return held;
    try {
      const raw = JSON.parse(window.localStorage.getItem(STORE_KEY) || '[]');
      held = (Array.isArray(raw) ? raw : [])
        .filter(f => f && typeof f.key === 'string' && f.key)
        .map(f => ({key: f.key, name: String(f.name || '')}))
        .slice(-MAX_FAVOURITES);
    } catch (err) {
      held = [];          // storage switched off: favourites last for this page only
    }
    return held;
  }

  function write(list) {
    held = list.slice(-MAX_FAVOURITES);
    try { window.localStorage.setItem(STORE_KEY, JSON.stringify(held)); } catch (err) { /* this page only */ }
    changed();
  }

  function changed() {
    refresh(document);
    listeners.forEach(fn => { try { fn(list()); } catch (err) { /* one listener must not stop the rest */ } });
  }

  function list() { return read().slice(); }
  function has(key) { return !!key && read().some(f => f.key === key); }
  function onChange(fn) { listeners.push(fn); }
  function clear() { write([]); }

  // Returns whether the boat is a favourite afterwards.
  function toggle(key, name) {
    if (!key) return false;
    const next = read().slice();
    const at = next.findIndex(f => f.key === key);
    if (at >= 0) next.splice(at, 1);
    else next.push({key: key, name: name || ''});
    write(next);
    return at < 0;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }

  function paint(button) {
    const on = has(button.dataset.favKey);
    const name = button.dataset.favName || 'this boat';
    button.textContent = on ? '★' : '☆';
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    button.title = on ? `${name} is a favourite. Tap to remove.` : `Make ${name} a favourite`;
    button.setAttribute('aria-label', button.title);
  }

  // The star for one boat, as markup, for a page that builds its rows in script.
  // Nothing for a boat with no key (no sail number and no name to go by).
  function starHtml(key, name) {
    if (!key) return '';
    const on = has(key);
    return `<button type="button" class="fav-star" data-fav-key="${escapeHtml(key)}"`
      + ` data-fav-name="${escapeHtml(name || '')}" aria-pressed="${on ? 'true' : 'false'}"`
      + ` aria-label="${on ? 'Remove from favourites' : 'Add to favourites'}">${on ? '★' : '☆'}</button>`;
  }

  // Give every server-rendered row that names a boat its star.
  function decorate(root) {
    (root || document).querySelectorAll('tr[data-boat-key]').forEach(tr => {
      const key = tr.dataset.boatKey;
      if (!key || tr.querySelector('.fav-star')) return;
      const cell = tr.querySelector('td[data-label="Boat"]') || tr.querySelector('td');
      if (!cell) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'fav-star';
      button.dataset.favKey = key;
      button.dataset.favName = tr.dataset.boatName || '';
      cell.appendChild(button);
      paint(button);
    });
  }

  function refresh(root) {
    const scope = root || document;
    scope.querySelectorAll('.fav-star[data-fav-key]').forEach(paint);
    scope.querySelectorAll('tr[data-boat-key]').forEach(tr => {
      tr.classList.toggle('is-favourite', has(tr.dataset.boatKey));
    });
    // The competitor home says who is being followed, and lets it be undone
    // without hunting for each boat's race.
    const line = scope.querySelector('#publicFollowLine');
    if (line) {
      const favourites = list();
      line.hidden = !favourites.length;
      const names = line.querySelector('.follow-names');
      if (names) names.textContent = favourites.map(f => f.name || f.key).join(', ');
    }
  }

  document.addEventListener('click', ev => {
    const star = ev.target.closest('.fav-star[data-fav-key]');
    if (star) {
      ev.preventDefault();
      toggle(star.dataset.favKey, star.dataset.favName || '');
      return;
    }
    if (ev.target.closest('.follow-clear')) {
      ev.preventDefault();
      clear();
    }
  });

  // Starred in another tab, or in the installed app while this page was open.
  window.addEventListener('storage', ev => {
    if (ev.key !== STORE_KEY) return;
    held = null;
    changed();
  });

  window.PublicFollow = {list, has, toggle, clear, onChange, starHtml, decorate, refresh};

  function start() { decorate(document); refresh(document); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
