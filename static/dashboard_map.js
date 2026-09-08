/* Copyright © 2026 CapeNet Ltd. All Rights Reserved. */
/*
 * The dashboard map: every mark, plus whoever is actually on the water.
 *
 * Not a race chart — there is no course and no leaderboard here. It answers the
 * question the dashboard could not: is anything out there, and where. A race has
 * its own chart with the course drawn on it.
 *
 * The zoom fits the marks *and* the trackers together. Fitting to the trackers
 * alone (which is what the Trackers page does) would fill the screen with one
 * boat when only one is out; fitting to the marks alone would push a boat that
 * has wandered off the course area off the edge.
 */
(function () {
  'use strict';

  const REFRESH_MS = 15000;
  // Never zoom closer than this, or a single tracker sitting on the pontoon
  // fills the screen with fifty metres of car park.
  const MIN_SPAN_M = 800;
  // How far out a mark still counts as "the racing area" for the zoom.
  //
  // A pair of temporary marks for a cross-Irish-Sea passage race, 98 and 115 km
  // out, once put 88 km of water on screen and shrank Pwllheli bay to a smudge.
  // Those two are gone, but marks like them get laid again, and the map should
  // not need weeding to stay useful. The regular marks reach the Gwylan Islands
  // and the Causeway at about 23 km, so 40 km keeps all of them with room to
  // spare while still excluding anything genuinely across the sea.
  //
  // Only the *zoom* ignores a distant mark — it is still drawn, so zooming out
  // finds it. And trackers are never filtered: if a boat really is out there,
  // the map should follow it.
  const HOME_RADIUS_KM = 40;
  // With boats out, the zoom follows them and takes the marks around them. The
  // outlying marks — the Gwylan Islands, the Causeway, Madog — are 14 to 23 km
  // out, and fitting all of them put the whole Llyn peninsula on screen with the
  // bay as a smudge and the boat too small to find. The point of this map is to
  // see what is happening, so when something is happening it wins.
  const NEAR_ACTIVITY_KM = 6;

  const el = document.getElementById('dashboardMap');
  if (!el || !window.L || !window.RaceCourseMap) return;

  const scope = el.closest('section') || document;
  const statusEl = document.getElementById('dashboardMapStatus');
  const url = el.dataset.trackersUrl;
  const trackingEnabled = el.dataset.trackingEnabled === '1';

  const homeLat = parseFloat(el.dataset.bridgeLat);
  const homeLon = parseFloat(el.dataset.bridgeLon);

  function kmFromHome(lat, lon) {
    if (!isFinite(homeLat) || !isFinite(homeLon)) return 0;   // no anchor: keep it
    const dy = (lat - homeLat) * 111.32;
    const dx = (lon - homeLon) * 111.32 * Math.cos(homeLat * Math.PI / 180);
    return Math.hypot(dx, dy);
  }

  function markPoints() {
    let marks;
    try {
      marks = JSON.parse(el.dataset.allMarks || '{}');
    } catch (err) {
      return [];
    }
    // Compound marks carry no position of their own; they expand to components
    // which are ordinary marks and are in here too.
    return Object.keys(marks || {}).map(k => marks[k]).filter(Boolean)
      .filter(m => typeof m.lat === 'number' && typeof m.lon === 'number')
      .filter(m => kmFromHome(m.lat, m.lon) <= HOME_RADIUS_KM)
      .map(m => [m.lat, m.lon]);
  }

  const marks = markPoints();

  function kmBetween(a, b) {
    const dy = (a[0] - b[0]) * 111.32;
    const dx = (a[1] - b[1]) * 111.32 * Math.cos(a[0] * Math.PI / 180);
    return Math.hypot(dx, dy);
  }

  // What the zoom should cover: the boats and the marks they are among, or the
  // whole racing area when nobody is out.
  function fitPoints(markers) {
    const boats = (markers || [])
      .filter(m => m.lat !== null && m.lat !== undefined
                   && m.lon !== null && m.lon !== undefined)
      .map(m => [m.lat, m.lon]);
    if (!boats.length) return marks;
    const near = marks.filter(p => boats.some(b => kmBetween(p, b) <= NEAR_ACTIVITY_KM));
    return boats.concat(near);
  }

  function say(text) {
    if (statusEl) statusEl.textContent = text || '';
  }

  function boatCount(n) {
    return n === 1 ? '1 tracker reporting' : n + ' trackers reporting';
  }

  if (!trackingEnabled) {
    // The template already says so; still fit the marks so the map is useful.
    window.RaceCourseMap.fitTo(scope, marks, {minSpanM: MIN_SPAN_M});
    return;
  }

  async function refresh() {
    let markers = [];
    try {
      const res = await fetch(url, {headers: {'Accept': 'application/json'},
                                    cache: 'no-store'});
      if (!res.ok) return;                 // keep the last render
      const data = await res.json();
      markers = data.markers || [];
    } catch (err) {
      return;                              // a transient failure is not worth a blank map
    }
    window.RaceCourseMap.invalidate(scope);
    window.RaceCourseMap.updateBoats(scope, markers);
    const points = fitPoints(markers);
    // fitTo leaves a map alone once it is showing what it should, so this does
    // not fight somebody who has panned or zoomed to look at something.
    if (points.length) {
      window.RaceCourseMap.fitTo(scope, points, {minSpanM: MIN_SPAN_M});
    }
    say(markers.length ? boatCount(markers.length) + ' in the last hour.'
                       : 'No tracker has reported in the last hour.');
  }

  // Leaflet measures the container when it initialises and caches a pixel
  // origin from it. This map is capped shorter than the default square, so by
  // the time that cap applies the map has already measured the taller box — and
  // every mark was then drawn about 3000px below the visible map. invalidateSize
  // re-reads the box; it has to happen before the first fit, and again on a
  // resize, or the same skew comes back at the new size.
  function settle() {
    window.RaceCourseMap.invalidate(scope);
    if (marks.length) window.RaceCourseMap.fitTo(scope, marks, {minSpanM: MIN_SPAN_M});
  }

  settle();
  // Belt and braces: web fonts and the sidebar can still shift the layout after
  // load, and a map that measured mid-shift is wrong in exactly the same way.
  window.addEventListener('load', settle);
  window.addEventListener('resize', () => window.RaceCourseMap.invalidate(scope));

  refresh();
  window.setInterval(refresh, REFRESH_MS);
})();
