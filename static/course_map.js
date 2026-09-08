/* Copyright © 2026 CapeNet Ltd. All Rights Reserved. */
/*
 * Course chart renderer used by race, recommendation, manual-builder and
 * public competitor pages. It renders the same data payload either with
 * Leaflet map tiles or, if Leaflet is unavailable, a simple SVG fallback.
 */
(function () {
  'use strict';

  const MARK_START = 'O';
  const BASE_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
  const SEAMARK_TILE_URL = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png';

  function parseJson(value, fallback) {
    try { return JSON.parse(value || ''); } catch (e) { return fallback; }
  }

  function parseNumber(value) {
    if (value === null || typeof value === 'undefined' || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function norm360(v) { return ((v % 360) + 360) % 360; }

  function normaliseMark(item) {
    if (!item) return null;
    if (typeof item === 'string') return {mark: item.toUpperCase(), rounding: 'port'};
    return {
      mark: String(item.mark || '').trim().toUpperCase(),
      rounding: String(item.rounding || 'port').trim().toLowerCase()
    };
  }

  function validLatLon(markData) {
    return markData && typeof markData.lat === 'number' && typeof markData.lon === 'number' && isFinite(markData.lat) && isFinite(markData.lon);
  }

  function markDisplayCode(allMarks, code) {
    const key = String(code || '').trim().toUpperCase();
    const md = allMarks && allMarks[key];
    return md && md.display ? String(md.display) : key;
  }

  function compoundComponents(allMarks, mark, rounding) {
    const code = String(mark || '').trim().toUpperCase();
    const md = allMarks && allMarks[code];
    const roundingKey = String(rounding || '').toLowerCase().startsWith('s') ? 'starboard' : 'port';
    if (md && md.rounding_order && Array.isArray(md.rounding_order[roundingKey])) {
      return md.rounding_order[roundingKey].map(m => String(m || '').trim().toUpperCase()).filter(m => allMarks && allMarks[m]);
    }
    if (md && Array.isArray(md.components)) {
      const components = md.components.map(m => String(m || '').trim().toUpperCase()).filter(m => allMarks && allMarks[m]);
      return roundingKey === 'starboard' ? components.slice().reverse() : components;
    }
    return [code];
  }

  // Build the plotted route. Every course starts at ODM O, matching the
  // app's simplified start/finish reference for course geometry. Compound
  // marks such as Y/A are expanded into their physical corner points so the
  // chart matches the leg analysis while course-board text remains compact.
  function routePoints(allMarks, courseMarks) {
    const route = [];
    const start = allMarks[MARK_START];
    if (validLatLon(start)) route.push({mark: MARK_START, label: markDisplayCode(allMarks, MARK_START), rounding: 'start', lat: start.lat, lon: start.lon, meta: start});
    (courseMarks || []).map(normaliseMark).filter(Boolean).forEach((m) => {
      // A waypoint bends the leg and is not rounded. Taken from the mark record
      // rather than from the saved rounding, so a course stored before the mark
      // was made a waypoint still draws correctly — and so this cannot disagree
      // with the server, which decides the same way.
      const md0 = allMarks[m.mark];
      if (md0 && md0.waypoint) {
        if (validLatLon(md0)) {
          route.push({
            mark: m.mark,
            label: markDisplayCode(allMarks, m.mark),
            parentMark: m.mark,
            parentLabel: markDisplayCode(allMarks, m.mark),
            rounding: 'via',
            waypoint: true,
            lat: md0.lat,
            lon: md0.lon,
            meta: md0
          });
        }
        return;
      }
      compoundComponents(allMarks, m.mark, m.rounding).forEach((component) => {
        const md = allMarks[component];
        if (validLatLon(md)) {
          route.push({
            mark: component,
            label: markDisplayCode(allMarks, component),
            parentMark: m.mark,
            parentLabel: markDisplayCode(allMarks, m.mark),
            rounding: m.rounding,
            lat: md.lat,
            lon: md.lon,
            meta: md
          });
        }
      });
    });
    return route;
  }

  // The faint background layer of every other mark, so the race officer can see
  // what else is out there. Waypoints are left out of it: there is no buoy at a
  // turning point, and drawing one among the real marks would say there is.
  function allKnownPoints(allMarks) {
    return Object.keys(allMarks || {})
      .filter(k => validLatLon(allMarks[k]) && !(allMarks[k] || {}).waypoint)
      .map(k => ({mark: k, label: markDisplayCode(allMarks, k), lat: allMarks[k].lat, lon: allMarks[k].lon, meta: allMarks[k]}));
  }

  // The **start** line: the CHPSC Bridge window to the ODM. Always this one.
  // Races start here whatever line they finish on, so this must not be relabelled
  // when a race finishes elsewhere.
  function startFinishPoints(container, allMarks) {
    const d = container.dataset;
    const bridgeLat = parseNumber(d.bridgeLat);
    const bridgeLon = parseNumber(d.bridgeLon);
    const markName = String(d.startFinishMark || MARK_START).trim().toUpperCase();
    const odm = allMarks && allMarks[markName];
    if (bridgeLat === null || bridgeLon === null || !validLatLon(odm)) return null;
    return {
      shore: {mark: 'Bridge', lat: bridgeLat, lon: bridgeLon, meta: {name: 'CHPSC Bridge window'}},
      seaward: {mark: markName, lat: odm.lat, lon: odm.lon, meta: odm}
    };
  }

  // A **separate finish line**, when the race is not finishing on the start line.
  // Both ends come from the server outright: an ISORA passage race finishes on
  // the transit between the Fairway Buoy and the bridge at Plas Heli, and the
  // shore end of that is a building rather than anything on the course. Null when
  // the race finishes where it started, which is the usual case.
  function finishLinePoints(container) {
    const d = container.dataset;
    const seawardLat = parseNumber(d.lineSeawardLat);
    const seawardLon = parseNumber(d.lineSeawardLon);
    const shoreLat = parseNumber(d.lineShoreLat);
    const shoreLon = parseNumber(d.lineShoreLon);
    if (seawardLat === null || seawardLon === null || shoreLat === null || shoreLon === null) {
      return null;
    }
    return {
      shore: {mark: d.lineShoreLabel || 'Finish', lat: shoreLat, lon: shoreLon,
              meta: {name: d.lineShoreLabel || 'Shore end'}},
      seaward: {mark: d.lineSeawardLabel || 'Finish', lat: seawardLat, lon: seawardLon,
                meta: {name: d.lineSeawardLabel || 'Seaward end'}}
    };
  }

  function segmentClass(rounding) {
    const r = String(rounding || '').toLowerCase();
    if (r.startsWith('s')) return 'starboard';
    if (r.startsWith('p')) return 'port';
    return 'finish';
  }

  function segmentColour(rounding) {
    // A leg *into* a waypoint is neither a port nor a starboard rounding, so it is
    // drawn neutrally: colouring it red or green would say something about a
    // rounding that is not being asked for.
    if (String(rounding || '').toLowerCase() === 'via') return '#6b7280';
    const cls = segmentClass(rounding);
    if (cls === 'starboard') return '#15803d';
    if (cls === 'port') return '#dc2626';
    return '#111827';
  }

  // Arrows and boats are drawn as inline SVG rather than CSS shapes inside a
  // flex box. Leaflet sets `display: block` on .leaflet-marker-icon at the same
  // specificity as our own class and later in the cascade, so the flex centring
  // these used to rely on silently never applied: the leg arrows sat 6.5px right
  // and 8px below their anchor — up to 10px off the line they belong to — and
  // the boat label landed beside the hull instead of under it. An SVG centred on
  // its own viewBox cannot drift, whatever the host stylesheet does.

  // A chevron, not a filled wedge: it reads as direction without weighing down
  // the leg it sits on. Points north at 0, so the rotation is the bearing.
  function legArrowSvg(bearing, colour) {
    return `<svg width="20" height="20" viewBox="-10 -10 20 20" aria-hidden="true">` +
           `<g transform="rotate(${bearing.toFixed(1)})">` +
           `<path d="M -4 3.2 L 0 -3.2 L 4 3.2" fill="none" stroke="${colour}"` +
           ` stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>` +
           `</g></svg>`;
    }

  // A hull in plan view — fine bow, maximum beam just aft of amidships, transom
  // across the stern — so a glance gives heading as well as position. With no
  // COG there is no heading to show, and a disc is honest about that.
  // The flat transom is what makes this read as a boat rather than a leaf at
  // 20-odd pixels, so the stern is a straight edge and the bow a fine point.
  const BOAT_HULL = 'M 0 -9.8 C 3.4 -6 5 -2.2 5 1.6 L 4.7 7.2 '
                  + 'L -4.7 7.2 L -5 1.6 C -5 -2.2 -3.4 -6 0 -9.8 Z';

  function boatSvg(cog, fill) {
    const body = (cog === null)
      ? `<circle cx="0" cy="0" r="4.4" fill="${fill}" fill-opacity="0.85"` +
        ` stroke="#1e293b" stroke-width="0.9"/>`
      : `<g transform="rotate(${cog.toFixed(1)})"><path d="${BOAT_HULL}" fill="${fill}"` +
        ` fill-opacity="0.9" stroke="#1e293b" stroke-width="0.9" stroke-linejoin="round"/></g>`;
    return `<svg width="22" height="22" viewBox="-11 -11 22 22" class="course-map-boat-hull"` +
           ` aria-hidden="true">${body}</svg>`;
  }

  function popupHtml(p, routeIndex) {
    const name = p.meta && p.meta.name ? p.meta.name : '';
    const label = p.label || p.mark;
    const parent = p.parentMark && p.parentMark !== p.mark ? `<br>Part of compound mark ${escapeHtml(p.parentLabel || p.parentMark)}` : '';
    const rounding = p.rounding && p.rounding !== 'start' ? `<br>Round to ${p.rounding}` : '';
    const seq = typeof routeIndex === 'number' ? `<br>Course point ${routeIndex + 1}` : '';
    return `<strong>${escapeHtml(label)}</strong>${name ? ' — ' + escapeHtml(name) : ''}${parent}${rounding}${seq}`;
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  }

  function bearingDegrees(a, b) {
    const toRad = x => x * Math.PI / 180;
    const toDeg = x => x * 180 / Math.PI;
    const lat1 = toRad(a.lat), lat2 = toRad(b.lat);
    const dLon = toRad(b.lon - a.lon);
    const y = Math.sin(dLon) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function pointAlong(a, b, ratio) {
    return {lat: a.lat + (b.lat - a.lat) * ratio, lon: a.lon + (b.lon - a.lon) * ratio};
  }

  // Overlay the latest hut wind direction. The arrow points downwind while
  // the label states the direction the wind is coming from.
  function addWindOverlay(container) {
    // A chart carrying the wind gauge shows the wind there instead; two wind
    // readings on one chart, one of them live and one following the replay
    // clock, would disagree the moment a viewer wound back.
    if (container.closest('.course-map-wrap')
        && container.closest('.course-map-wrap').querySelector('.chart-wind-gauge')) return;
    const twd = parseNumber(container.dataset.windDirection);
    let overlay = container.querySelector('.course-map-wind-overlay');
    if (twd === null) {
      if (overlay) overlay.remove();
      return;
    }
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'course-map-wind-overlay';
      container.appendChild(overlay);
    }
    const downwind = norm360(twd + 180);
    overlay.innerHTML = `<div class="course-map-wind-label">Wind from ${norm360(twd).toFixed(0)}°T</div><div class="course-map-wind-arrow" style="transform: rotate(${downwind}deg)">↑</div>`;
  }

  // Main Leaflet renderer. It reuses an existing map instance when possible
  // so live wind/course refreshes do not recreate all map state.
  function makeLeafletMap(container, allMarks, courseMarks) {
    const L = window.L;
    const baseTileUrl = (container.dataset.baseTileUrl || BASE_TILE_URL).trim();
    const overlayTileUrl = (container.dataset.overlayTileUrl || SEAMARK_TILE_URL).trim();
    const tileHash = `${baseTileUrl}|${overlayTileUrl}`;
    if (!container._courseMap || container._courseMap.tileHash !== tileHash) {
      if (container._courseMap && container._courseMap.map) {
        container._courseMap.map.remove();
      }
      const map = L.map(container, {scrollWheelZoom: false, preferCanvas: true});
      if (baseTileUrl) {
        L.tileLayer(baseTileUrl, {
          maxZoom: 18,
          attribution: '&copy; map contributors'
        }).addTo(map);
      }
      if (overlayTileUrl) {
        L.tileLayer(overlayTileUrl, {
          maxZoom: 18,
          opacity: 0.95,
          attribution: '&copy; seamark/chart contributors'
        }).addTo(map);
      }
      container._courseMap = {map, layers: [], tileHash};
      setTimeout(() => map.invalidateSize(), 0);
    }
    const state = container._courseMap;
    state.layers.forEach(layer => state.map.removeLayer(layer));
    state.layers = [];

    const route = routePoints(allMarks, courseMarks);
    const known = allKnownPoints(allMarks);
    const startFinish = startFinishPoints(container, allMarks);
    const separateFinish = finishLinePoints(container);

    known.forEach((p) => {
      const icon = L.divIcon({
        className: 'course-map-mark course-map-mark-background',
        html: `<span>${escapeHtml(p.label || p.mark)}</span>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      });
      const marker = L.marker([p.lat, p.lon], {icon, interactive: true}).bindPopup(popupHtml(p));
      marker.addTo(state.map); state.layers.push(marker);
    });

    if (startFinish) {
      const pts = [[startFinish.shore.lat, startFinish.shore.lon], [startFinish.seaward.lat, startFinish.seaward.lon]];
      const casing = L.polyline(pts, {color: '#ffffff', weight: 5, opacity: 0.7}).addTo(state.map);
      const line = L.polyline(pts, {color: '#111827', weight: 1.8, opacity: 0.8, dashArray: '9 7'}).addTo(state.map);
      state.layers.push(casing, line);
      const mid = pointAlong(startFinish.shore, startFinish.seaward, 0.52);
      const label = L.marker([mid.lat, mid.lon], {
        interactive: false,
        icon: L.divIcon({
          className: 'course-map-line-label',
          html: separateFinish ? '<span>Start line</span>'
                              : '<span>Start / finish line</span>',
          iconSize: [130, 24],
          iconAnchor: [65, 12]
        })
      }).addTo(state.map);
      state.layers.push(label);
      const bridgeIcon = L.divIcon({
        className: 'course-map-bridge-marker',
        html: '<span>Bridge</span>',
        iconSize: [54, 22],
        iconAnchor: [27, 11]
      });
      const bridgeMarker = L.marker([startFinish.shore.lat, startFinish.shore.lon], {icon: bridgeIcon, interactive: true}).bindPopup('<strong>CHPSC Bridge window</strong><br>Shore end of start/finish line');
      bridgeMarker.addTo(state.map); state.layers.push(bridgeMarker);
    }

    // The finish line, when it is not the start line. Drawn in its own colour
    // so the two are never mistaken for each other on the water or on the TV.
    if (separateFinish) {
      const pts = [[separateFinish.shore.lat, separateFinish.shore.lon],
                   [separateFinish.seaward.lat, separateFinish.seaward.lon]];
      const casing = L.polyline(pts, {color: '#ffffff', weight: 5, opacity: 0.7}).addTo(state.map);
      const line = L.polyline(pts, {color: '#7c3aed', weight: 2.2, opacity: 0.9,
                                    dashArray: '9 7'}).addTo(state.map);
      state.layers.push(casing, line);
      const mid = pointAlong(separateFinish.shore, separateFinish.seaward, 0.52);
      const label = L.marker([mid.lat, mid.lon], {
        interactive: false,
        icon: L.divIcon({
          className: 'course-map-line-label course-map-finish-label',
          html: '<span>Finish line</span>',
          iconSize: [110, 24],
          iconAnchor: [55, 12]
        })
      }).addTo(state.map);
      state.layers.push(label);
      const shoreIcon = L.divIcon({
        className: 'course-map-bridge-marker',
        html: `<span>${escapeHtml(separateFinish.shore.mark)}</span>`,
        iconSize: [96, 22],
        iconAnchor: [48, 11]
      });
      const shoreMarker = L.marker([separateFinish.shore.lat, separateFinish.shore.lon],
        {icon: shoreIcon, interactive: true})
        .bindPopup(`<strong>${escapeHtml(separateFinish.shore.mark)}</strong><br>Shore end of the finish line`);
      shoreMarker.addTo(state.map); state.layers.push(shoreMarker);
    }

    route.forEach((p, idx) => {
      // A waypoint exists to bend the leg round a headland. It is not a mark, it is
      // not on the course board, and drawing a labelled circle for it would put a
      // mark on the chart that nobody is being asked to round. The bend speaks for
      // itself.
      if (p.waypoint) return;
      const cls = segmentClass(p.rounding);
      const icon = L.divIcon({
        className: `course-map-mark course-map-mark-route ${cls}`,
        html: `<span>${escapeHtml(p.label || p.mark)}</span>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15]
      });
      const marker = L.marker([p.lat, p.lon], {icon, interactive: true, zIndexOffset: 1000}).bindPopup(popupHtml(p, idx));
      marker.addTo(state.map); state.layers.push(marker);
    });

    for (let i = 0; i < route.length - 1; i++) {
      const a = route[i], b = route[i + 1];
      const colour = segmentColour(b.rounding);
      const line = L.polyline([[a.lat, a.lon], [b.lat, b.lon]], {color: colour, weight: 2.2, opacity: 0.7}).addTo(state.map);
      state.layers.push(line);
      const pos = pointAlong(a, b, 0.56);
      const bearing = bearingDegrees(a, b);
      const arrow = L.marker([pos.lat, pos.lon], {
        interactive: false,
        icon: L.divIcon({
          className: 'course-map-arrow',
          html: legArrowSvg(bearing, colour),
          iconSize: [20, 20],
          iconAnchor: [10, 10]
        })
      }).addTo(state.map);
      state.layers.push(arrow);
    }

    // When the course has been shortened, the route stops at the shorten mark
    // and boats proceed directly to the finish line. Draw that final leg as a
    // dashed line to the middle of the start/finish line so the chart matches
    // the "→ Finish" shown in the course sequence.
    if (container.dataset.shortened === '1' && startFinish && route.length) {
      const last = route[route.length - 1];
      const target = pointAlong(startFinish.shore, startFinish.seaward, 0.5);
      const line = L.polyline([[last.lat, last.lon], [target.lat, target.lon]], {color: '#111827', weight: 2.2, opacity: 0.7, dashArray: '7 5'}).addTo(state.map);
      state.layers.push(line);
      const pos = pointAlong(last, target, 0.56);
      const bearing = bearingDegrees(last, target);
      const arrow = L.marker([pos.lat, pos.lon], {
        interactive: false,
        icon: L.divIcon({
          className: 'course-map-arrow',
          html: legArrowSvg(bearing, '#111827'),
          iconSize: [20, 20],
          iconAnchor: [10, 10]
        })
      }).addTo(state.map);
      state.layers.push(arrow);
    }

    addWindOverlay(container);

    const fit = route.length ? route.map(p => [p.lat, p.lon]) : known.map(p => [p.lat, p.lon]);
    if (startFinish) fit.push([startFinish.shore.lat, startFinish.shore.lon], [startFinish.seaward.lat, startFinish.seaward.lon]);
    if (fit.length) {
      state.map.fitBounds(L.latLngBounds(fit), {padding: [28, 28], maxZoom: 14});
    } else {
      state.map.setView([52.87, -4.40], 13);
    }
    const missing = (courseMarks || []).map(normaliseMark).filter(m => m && !validLatLon(allMarks[m.mark])).map(m => m.mark);
    const msg = container.closest('.course-map-wrap')?.querySelector('.course-map-message');
    if (msg) msg.textContent = missing.length ? `Chart note: no coordinates are available for ${missing.join(', ')}.` : '';
    setTimeout(() => state.map.invalidateSize(), 100);
  }

  function projectPoint(p, bounds, width, height, pad) {
    const latRange = Math.max(0.00001, bounds.maxLat - bounds.minLat);
    const lonRange = Math.max(0.00001, bounds.maxLon - bounds.minLon);
    const x = pad + ((p.lon - bounds.minLon) / lonRange) * (width - pad * 2);
    const y = pad + ((bounds.maxLat - p.lat) / latRange) * (height - pad * 2);
    return {x, y};
  }

  // SVG fallback for offline/test environments without Leaflet or web tiles.
  function makeSvgMap(container, allMarks, courseMarks) {
    const route = routePoints(allMarks, courseMarks);
    const known = allKnownPoints(allMarks);
    const startFinish = startFinishPoints(container, allMarks);
    const fit = route.length ? route.slice() : known.slice();
    if (startFinish) fit.push(startFinish.shore, startFinish.seaward);
    const width = Math.max(520, container.clientWidth || 720);
    const height = Math.max(320, Math.floor(width * 0.52));
    const pad = 34;
    if (!fit.length) {
      container.innerHTML = '<div class="course-map-fallback">No chart coordinates are available.</div>';
      return;
    }
    const bounds = fit.reduce((acc, p) => ({
      minLat: Math.min(acc.minLat, p.lat), maxLat: Math.max(acc.maxLat, p.lat),
      minLon: Math.min(acc.minLon, p.lon), maxLon: Math.max(acc.maxLon, p.lon)
    }), {minLat: Infinity, maxLat: -Infinity, minLon: Infinity, maxLon: -Infinity});
    const latPad = Math.max(0.003, (bounds.maxLat - bounds.minLat) * 0.22);
    const lonPad = Math.max(0.003, (bounds.maxLon - bounds.minLon) * 0.22);
    bounds.minLat -= latPad; bounds.maxLat += latPad; bounds.minLon -= lonPad; bounds.maxLon += lonPad;

    const parts = [`<svg class="course-map-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Course chart">`,
      `<rect width="${width}" height="${height}" fill="#dbeafe"/>`,
      // Classed so a page that chose this drawing on purpose can hide the note.
      // On the water it is the design -- no tiles, no Leaflet, no network --
      // and "chart tiles unavailable" there reads as something being broken.
      `<text class="course-map-fallback-note" x="14" y="24" fill="#475569" font-size="13">Chart tiles unavailable — showing plotted mark positions</text>`];
    known.forEach(p => {
      const q = projectPoint(p, bounds, width, height, pad);
      parts.push(`<circle cx="${q.x}" cy="${q.y}" r="8" fill="#fde68a" stroke="#334155" stroke-width="2"/>`);
      parts.push(`<text x="${q.x + 10}" y="${q.y + 4}" fill="#0f172a" font-size="13" font-weight="700">${escapeHtml(p.label || p.mark)}</text>`);
    });
    if (startFinish) {
      const a = projectPoint(startFinish.shore, bounds, width, height, pad);
      const b = projectPoint(startFinish.seaward, bounds, width, height, pad);
      parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#ffffff" stroke-width="8" stroke-linecap="round"/>`);
      parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#111827" stroke-width="3" stroke-dasharray="10 8" stroke-linecap="round"/>`);
      parts.push(`<text x="${(a.x+b.x)/2 + 8}" y="${(a.y+b.y)/2 - 8}" fill="#111827" font-size="12" font-weight="700">Start / finish</text>`);
    }
    for (let i = 0; i < route.length - 1; i++) {
      const a = projectPoint(route[i], bounds, width, height, pad);
      const b = projectPoint(route[i + 1], bounds, width, height, pad);
      const bearing = bearingDegrees(route[i], route[i + 1]);
      const colour = segmentColour(route[i + 1].rounding);
      const mx = a.x + (b.x - a.x) * 0.56;
      const my = a.y + (b.y - a.y) * 0.56;
      parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${colour}" stroke-width="4" stroke-linecap="round"/>`);
      parts.push(`<polygon points="0,-6 14,0 0,6" fill="#111827" transform="translate(${mx},${my}) rotate(${bearing - 90})"/>`);
    }
    // Shortened-course final leg: dashed line from the shorten mark to the
    // middle of the start/finish line (see the Leaflet renderer above).
    if (container.dataset.shortened === '1' && startFinish && route.length) {
      const last = route[route.length - 1];
      const target = pointAlong(startFinish.shore, startFinish.seaward, 0.5);
      const a = projectPoint(last, bounds, width, height, pad);
      const b = projectPoint(target, bounds, width, height, pad);
      const bearing = bearingDegrees(last, target);
      const mx = a.x + (b.x - a.x) * 0.56;
      const my = a.y + (b.y - a.y) * 0.56;
      parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#111827" stroke-width="4" stroke-dasharray="8 6" stroke-linecap="round"/>`);
      parts.push(`<polygon points="0,-6 14,0 0,6" fill="#111827" transform="translate(${mx},${my}) rotate(${bearing - 90})"/>`);
    }
    const twd = parseNumber(container.dataset.windDirection);
    if (twd !== null) {
      const downwind = norm360(twd + 180);
      parts.push(`<g transform="translate(${width - 82},58) rotate(${downwind})"><line x1="0" y1="-28" x2="0" y2="28" stroke="#7c2d12" stroke-width="5" stroke-linecap="round"/><polygon points="0,38 -10,20 10,20" fill="#7c2d12"/></g>`);
      parts.push(`<text x="${width - 150}" y="22" fill="#7c2d12" font-size="13" font-weight="700">Wind from ${norm360(twd).toFixed(0)}°T</text>`);
    }
    route.forEach(p => {
      const q = projectPoint(p, bounds, width, height, pad);
      parts.push(`<circle cx="${q.x}" cy="${q.y}" r="11" fill="#fde047" stroke="${segmentColour(p.rounding)}" stroke-width="3"/>`);
      parts.push(`<text x="${q.x}" y="${q.y + 4}" text-anchor="middle" fill="#0f172a" font-size="13" font-weight="800">${escapeHtml(p.label || p.mark)}</text>`);
    });
    parts.push('</svg>');
    container.innerHTML = parts.join('');
  }

  function render(container, newCourseMarks) {
    if (!container) return;
    const allMarks = parseJson(container.dataset.allMarks, {});
    const courseMarks = newCourseMarks || parseJson(container.dataset.courseMarks, []);
    if (newCourseMarks) container.dataset.courseMarks = JSON.stringify(newCourseMarks);
    if (window.L) makeLeafletMap(container, allMarks, courseMarks);
    else makeSvgMap(container, allMarks, courseMarks);
  }

  function setWind(container, twd) {
    if (!container) return;
    const n = parseNumber(twd);
    if (n === null) container.dataset.windDirection = '';
    else container.dataset.windDirection = String(norm360(n));
    // With Leaflet already initialised, just update the overlay so the map does
    // not repeatedly refit/jump as the live weather station sample changes.
    if (window.L && container._courseMap) addWindOverlay(container);
    else render(container);
  }

  function updateAllWind(root, twd) {
    (root || document).querySelectorAll('.course-map').forEach(el => setWind(el, twd));
  }

  function initAll(root) {
    (root || document).querySelectorAll('.course-map').forEach(el => render(el));
  }

  // Overlay live boat positions on an already-initialised Leaflet map. Boat
  // markers live on their own layer array (state.boatLayers), separate from the
  // course geometry layers, so refreshing positions never refits or rebuilds the
  // course — mirroring how the wind overlay is updated independently.
  // opts.fit: also zoom/pan so every boat is visible — used by the Trackers map,
  // where a tracker may be nowhere near the race area. It only refits when the
  // markers would otherwise be off-screen (or on the first fix), so a map the
  // user has panned/zoomed is not yanked back on every refresh.
  function updateBoats(root, boats, opts) {
    if (!window.L) return;
    const options = opts || {};
    (root || document).querySelectorAll('.course-map').forEach(container => {
      const state = container._courseMap;
      if (!state || !state.map) return;
      if (!state.boatLayers) state.boatLayers = [];
      state.boatLayers.forEach(l => state.map.removeLayer(l));
      state.boatLayers = [];
      const points = [];
      (boats || []).forEach(b => {
        if (b.lat === null || b.lon === null || b.lat === undefined || b.lon === undefined) return;
        const cog = (b.cog === null || b.cog === undefined) ? null : norm360(b.cog);
        const label = escapeHtml(b.sail_no || b.boat_name || '');
        const finished = b.status && b.status !== 'RACING';
        const cls = 'course-map-boat' + (b.stale ? ' stale' : '') + (finished ? ' finished' : '');
        // Each boat in its own colour where the caller has one (the replay gives
        // every boat a colour for its trail), so hull and trail read as one boat.
        const fill = finished ? '#15803d' : (b.colour || '#1d4ed8');
        const icon = window.L.divIcon({
          className: cls,
          // The label is outlined in the boat's own colour, so the sail number
          // and the hull read as one object at a glance on a crowded start.
          html: boatSvg(cog, fill)
                + `<span class="course-map-boat-label" style="border-color:${fill}">${label}</span>`,
          iconSize: [22, 22], iconAnchor: [11, 11]
        });
        const sog = (b.sog === null || b.sog === undefined) ? '' : ` · ${Number(b.sog).toFixed(1)} kn`;
        const marker = window.L.marker([b.lat, b.lon], {icon, zIndexOffset: 2000, title: `${b.boat_name || ''}${sog}`});
        marker.addTo(state.map);
        state.boatLayers.push(marker);
        points.push([b.lat, b.lon]);
      });
      if (options.fit && points.length) {
        const bounds = window.L.latLngBounds(points);
        const needsFit = !state.boatsFitted || !state.map.getBounds().contains(bounds);
        if (needsFit) {
          state.map.fitBounds(bounds, {padding: [40, 40], maxZoom: 15});
          state.boatsFitted = true;
        }
      }
    });
  }

  // Draw each boat's track so far, for the replay viewer. Trails live on their
  // own layer array for the same reason boats do: redrawing them every frame
  // must not touch the course geometry or refit the map.
  //   trails: [{points: [[lat, lon], ...], colour: '#rrggbb', dim: bool}, ...]
  function updateTrails(root, trails) {
    if (!window.L) return;
    (root || document).querySelectorAll('.course-map').forEach(container => {
      const state = container._courseMap;
      if (!state || !state.map) return;
      if (!state.trailLayers) state.trailLayers = [];
      state.trailLayers.forEach(l => state.map.removeLayer(l));
      state.trailLayers = [];
      (trails || []).forEach(t => {
        if (!t || !t.points || t.points.length < 2) return;
        const line = window.L.polyline(t.points, {
          color: t.colour || '#0f314e',
          weight: t.dim ? 1.5 : 2.5,
          opacity: t.dim ? 0.35 : 0.8,
          interactive: false,
        });
        line.addTo(state.map);
        state.trailLayers.push(line);
      });
    });
  }

  // The chart is capped by viewport height as well as width, so resizing the
  // window — or rotating a phone — changes the container and Leaflet has to be
  // told, or it keeps drawing tiles for the size it was built at. Debounced,
  // because a drag-resize fires this continuously.
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      document.querySelectorAll('.course-map').forEach(container => {
        const state = container._courseMap;
        if (state && state.map) state.map.invalidateSize();
      });
    }, 150);
  });

  // Zoom to a set of points — the clubhouse display uses this to follow the part
  // of the course the fleet is actually on.
  //
  // The hysteresis is the whole of it. This is called every few seconds for an
  // hour on a screen nobody is watching closely, and a map that re-fits whenever
  // a boat moves a length is a map permanently in motion. So it only moves when
  // the view no longer holds what it should, or when it is holding far more than
  // it needs and could usefully close in.
  function fitTo(root, points, options) {
    if (!window.L || !points || !points.length) return false;
    const opts = options || {};
    const minSpanM = opts.minSpanM || 0;
    const slack = opts.slack || 1.9;         // how much bigger than needed is tolerated
    let moved = false;
    (root || document).querySelectorAll('.course-map').forEach(container => {
      const state = container._courseMap;
      if (!state || !state.map) return;
      let target = window.L.latLngBounds(points);
      if (minSpanM > 0) {
        // Never zoom past this, or a fleet in close company fills the screen with
        // one boat length of sea.
        const c = target.getCenter();
        const dLat = minSpanM / 2 / 111320;
        const dLon = dLat / Math.max(0.2, Math.cos(c.lat * Math.PI / 180));
        target = target.extend(window.L.latLngBounds(
          [c.lat - dLat, c.lng - dLon], [c.lat + dLat, c.lng + dLon]));
      }
      // Insets are the parts of the map covered by something else — on the
      // clubhouse display the header across the top and the leaderboard down the
      // right. Both the fit and the "is it still good enough" test work on the
      // part that can actually be seen, or the fleet ends up neatly centred
      // underneath the leaderboard.
      const inset = opts.insets || {};
      const size = state.map.getSize();
      const left = inset.left || 0, top = inset.top || 0;
      const right = inset.right || 0, bottom = inset.bottom || 0;
      const visible = (left || top || right || bottom)
        ? window.L.latLngBounds(
            state.map.containerPointToLatLng([left, top]),
            state.map.containerPointToLatLng([Math.max(left + 1, size.x - right),
                                              Math.max(top + 1, size.y - bottom)]))
        : state.map.getBounds();
      const span = b => Math.max(1e-9, (b.getNorth() - b.getSouth()))
                      * Math.max(1e-9, (b.getEast() - b.getWest()));
      const holds = visible.contains(target);
      const roomy = span(visible) > span(target) * slack;
      if (holds && !roomy) return;           // good enough; leave it alone
      const pad = opts.padding || [60, 60];
      state.map.flyToBounds(target, {
        paddingTopLeft: [left + pad[0], top + pad[1]],
        paddingBottomRight: [right + pad[0], bottom + pad[1]],
        maxZoom: opts.maxZoom || 15,
        duration: opts.duration || 1.6,
      });
      moved = true;
    });
    return moved;
  }

  // Re-measure after the container changes size for a reason the window does not
  // know about — going full screen moves the map with CSS, which fires no resize
  // event, and Leaflet would carry on drawing at the old size.
  function invalidate(root) {
    if (!window.L) return;
    (root || document).querySelectorAll('.course-map').forEach(container => {
      const state = container._courseMap;
      if (state && state.map) state.map.invalidateSize();
    });
  }

  window.RaceCourseMap = {initAll, render, setWind, updateAllWind, updateBoats,
                          updateTrails, invalidate, fitTo};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initAll(document));
  else initAll(document);
})();
