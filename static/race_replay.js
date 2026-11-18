/* The race chart: live while the race is on, and a replay of it at any time.
 *
 * One view, not two. It opens at the latest positions — so during a race it is
 * simply the live chart — and winds back through everything recorded so far.
 * While you are at the live edge it keeps up as new fixes arrive; the moment you
 * scrub back or play from earlier it stops dragging you forward, and the Live
 * button returns you.
 *
 * The whole race arrives in one fetch (/public/race/<id>/track), so scrubbing
 * is local and instant — no request per frame. Boat positions are interpolated
 * between fixes client-side, which is what makes 10-second reporting look like
 * motion rather than a slideshow. Be honest about that with viewers: the line
 * between two fixes is drawn, not observed.
 *
 * The order on the water is NOT recomputed here — mark rounding, distance to go
 * and finish detection are the app's own course walk and a second version in the
 * browser could quietly disagree with the live view. Instead the server sends
 * that order pre-computed for every step of the race, along with the track, so
 * showing it is a local lookup: no request per frame, and no lag behind the
 * boats on the chart.
 */
(function () {
  'use strict';

  // 60x is there for a passage race: seven hours takes 42 minutes at 10x.
  const SPEEDS = [1, 2, 5, 10, 30, 60];
  const DEFAULT_SPEED = 5;
  const LIVE_POLL_MS = 10000;         // how often to ask for new fixes while live
  const LIVE_EDGE_S = 15;             // within this of the end counts as 'at the live edge'
  const TRAIL_SECONDS = 600;          // how much track to draw behind each boat
  const MAX_FRAME_S = 0.25;           // biggest step one frame may advance the clock
  const SAILED_MIN_STEP_M = 10;       // a move smaller than this is GPS jitter, not sailing
  const VMC_WINDOW_S = 1200;          // VMC's window when the server names none: its own twenty minutes
  const CARD_REFRESH_MS = 500;        // how often the boat card is redrawn during playback
  const FOLLOW_EDGE = 0.22;           // share of the chart, each side, a followed boat is kept out of
  const FOLLOW_SETTLE_MS = 900;       // let one pan finish before judging whether to pan again
  // Boat colours deliberately avoid the hues the course is drawn in: red is a
  // port rounding, green a starboard one, black the finish, and the marks are
  // yellow. A red boat on a red leg is genuinely hard to pick out on a phone —
  // so the fleet lives in blues, violets and pinks, where nothing on the chart
  // competes with it. Distinguishing boats from each other is the sail number's
  // job; distinguishing a boat from the course is the colour's.
  const COLOURS = ['#2563eb', '#db2777', '#0891b2', '#7c3aed', '#c026d3',
                   '#1e3a8a', '#9d174d', '#155e75', '#4338ca', '#6b21a8'];

  function pad(n) { return String(n).padStart(2, '0'); }

  function clockText(epoch) {
    if (!epoch) return '--:--:--';
    const d = new Date(epoch * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function elapsedText(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return (h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`);
  }

  // What the wind was doing at a moment -- the rule lives with the gauge, so
  // this page and the bar television cannot disagree about it.
  function windAt(series, t) {
    return window.WindGauge ? window.WindGauge.readingAt(series, t) : null;
  }

  // Where a boat was at time t: the fix before, nudged towards the fix after.
  // Returns null before its first fix so a boat does not appear until it has
  // actually reported — better than parking it on its first known position.
  function positionAt(fixes, t) {
    if (!fixes || !fixes.length || t < fixes[0][0]) return null;
    let lo = 0, hi = fixes.length - 1;
    if (t >= fixes[hi][0]) {
      const f = fixes[hi];
      return {lat: f[1], lon: f[2], sog: f[3], cog: f[4], age: t - f[0]};
    }
    while (hi - lo > 1) {                    // binary search: called every frame
      const mid = (lo + hi) >> 1;
      if (fixes[mid][0] <= t) lo = mid; else hi = mid;
    }
    const a = fixes[lo], b = fixes[hi];
    const span = b[0] - a[0];
    const r = span > 0 ? (t - a[0]) / span : 0;
    return {
      lat: a[1] + (b[1] - a[1]) * r,
      lon: a[2] + (b[2] - a[2]) * r,
      sog: a[3], cog: a[4],
      age: t - a[0],
    };
  }

  // Great-circle distance between two positions, in metres.
  function metresBetween(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2
            + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  // How far a boat had sailed by each of its fixes, in metres from its first.
  //
  // Adding up every step between fixes over-reads badly on a tracker reporting
  // every two seconds: each fix is a few metres off, and a few metres of error
  // 11,000 times is miles the boat never sailed. So a step only counts once the
  // boat is clear of the last point counted, by more than the error on a fix.
  function sailedSeries(fixes) {
    const out = new Array(fixes.length);
    let total = 0, fromLat = null, fromLon = null;
    for (let i = 0; i < fixes.length; i++) {
      const f = fixes[i];
      if (fromLat === null) {
        fromLat = f[1]; fromLon = f[2];
      } else {
        const step = metresBetween(fromLat, fromLon, f[1], f[2]);
        if (step >= SAILED_MIN_STEP_M) { total += step; fromLat = f[1]; fromLon = f[2]; }
      }
      out[i] = total;
    }
    return out;
  }

  // The last fix at or before t, as an index; -1 when t is before the first.
  function fixIndexAt(fixes, t) {
    if (!fixes.length || t < fixes[0][0]) return -1;
    let lo = 0, hi = fixes.length - 1;
    if (t >= fixes[hi][0]) return hi;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (fixes[mid][0] <= t) lo = mid; else hi = mid;
    }
    return lo;
  }

  function trailPoints(fixes, t, seconds) {
    const from = t - seconds;
    const pts = [];
    for (let i = 0; i < fixes.length; i++) {
      const f = fixes[i];
      if (f[0] > t) break;
      if (f[0] >= from) pts.push([f[1], f[2]]);
    }
    const here = positionAt(fixes, t);
    if (here) pts.push([here.lat, here.lon]);
    return pts;
  }

  function init(root) {
    const panel = root.querySelector('[data-replay]');
    if (!panel) return;
    const trackUrl = panel.dataset.trackUrl;
    const mapRoot = panel.querySelector('.replay-map');
    const slider = panel.querySelector('.replay-slider');
    const playBtn = panel.querySelector('.replay-play');
    const speedSel = panel.querySelector('.replay-speed');
    const viewSel = panel.querySelector('.replay-view');
    const clockEl = panel.querySelector('.replay-clock');
    const elapsedEl = panel.querySelector('.replay-elapsed');
    const statusEl = panel.querySelector('.replay-status');
    const boardEl = panel.querySelector('.replay-board tbody');
    const liveBtn = panel.querySelector('.replay-live');
    const boardAtEl = panel.querySelector('.replay-board-at');
    const windGaugeEl = panel.querySelector('.chart-wind-gauge');
    const windCaptionEl = panel.querySelector('.chart-wind-caption');
    const windGauge = (windGaugeEl && window.WindGauge)
      ? window.WindGauge.prepare(windGaugeEl) : null;
    const boardHeadEl = panel.querySelector('.replay-board thead tr');
    const boardNoteEl = panel.querySelector('.replay-board-note');
    const modeSel = panel.querySelector('.replay-board-mode');
    const classSel = panel.querySelector('.replay-board-class');
    const methodSel = panel.querySelector('.replay-board-method');
    const classWrap = panel.querySelector('.replay-class-wrap');
    const methodWrap = panel.querySelector('.replay-method-wrap');

    let data = null;            // {start, end, boats:[{entry_id, boat_name, sail_no, fixes}]}
    let clock = 0;              // current replay time (epoch seconds)
    let playing = false;
    let speedIx = SPEEDS.indexOf(DEFAULT_SPEED);
    let selected = null;        // entry_id of the boat the card and the highlight are on, or null
    let following = false;      // the chart keeps the selected boat in view
    let finishAt = {};          // entry id -> time of the first snapshot showing it finished
    let lastFrame = null;       // performance.now() of the previous animation frame
    let shownBoardIx = -1;      // which pre-computed snapshot is on screen
    let boatById = {};
    let atLive = true;          // following the latest positions rather than replaying
    let livePoll = null;
    let raceOver = false;
    // Favourites (static/public_follow.js) and what the page was told about each
    // boat (core/publicapp.py). Both are optional: without them the chart works
    // exactly as before, with no stars and a card that has less to say.
    const favs = window.PublicFollow || null;
    const boatInfo = (function () {
      const el = document.getElementById('publicBoatInfo');
      try { return el ? (JSON.parse(el.textContent || '{}') || {}) : {}; } catch (err) { return {}; }
    })();
    const STATE_KEY = 'roChart:' + (panel.dataset.raceId || '');
    let stateRestored = false;
    let colourOf = {};
    let boardMode = 'line';     // 'line' | 'irc' | 'ytc'

    const LINE_HEAD = ['#', 'Boat', 'Marks', 'Next', 'To go', 'SOG', 'VMC', 'Sailed'];
    const CORRECTED_HEAD = ['#', 'Boat', 'Marks', 'To go', 'Est. elapsed', 'Corrected'];
    const LINE_NOTE = 'Order <strong>on the water</strong> at the moment shown — not corrected for handicap.';

    function setStatus(text, kind) {
      if (!statusEl) return;
      statusEl.textContent = text || '';
      statusEl.className = 'replay-status small' + (kind ? ' ' + kind : '');
    }

    // Elapsed time in a race is time since your gun, so that is what the clock
    // counts. The replay window opens at the warning signal five minutes earlier,
    // and counting from there read 15:00 when the race was ten minutes old - the
    // very moment the board starts estimating, so the estimates looked late.
    // Before the gun it counts down.
    //
    // It lives in here rather than at module scope because it reads `data`, which
    // is a closure variable of init(). Declared outside, it threw a ReferenceError
    // on every frame - and it is called two lines into drawFrame, so it killed the
    // slider, the leaderboard and everything else drawn after the clock. That
    // shipped in v0.261: the clock was right and the board below it was empty.
    function raceElapsedText(at) {
      const gun = (data && data.first_start) || null;
      if (!gun) return elapsedText(at - data.start);
      const since = at - gun;
      return since < 0 ? `-${elapsedText(-since)}` : elapsedText(since);
    }

    function drawFrame() {
      if (!data) return;
      const boats = [], trails = [];
      let selectedAt = null;
      data.boats.forEach(b => {
        if (!b.fixes || !b.fixes.length) return;
        const p = positionAt(b.fixes, clock);
        if (!p) return;
        const dim = selected !== null && b.entry_id !== selected;
        if (b.entry_id === selected) selectedAt = p;
        boats.push({
          entry_id: b.entry_id,
          lat: p.lat, lon: p.lon, cog: p.cog, sog: p.sog,
          sail_no: b.sail_no, boat_name: b.boat_name,
          colour: colourOf[b.entry_id],     // same colour as this boat's trail
          stale: p.age > 120,
          selected: b.entry_id === selected,
          favourite: isFavourite(b.entry_id),
        });
        trails.push({points: trailPoints(b.fixes, clock, TRAIL_SECONDS),
                     colour: colourOf[b.entry_id], dim});
      });
      window.RaceCourseMap.updateTrails(mapRoot, trails);
      window.RaceCourseMap.updateBoats(mapRoot, boats, {onBoatClick: id => selectBoat(id)});
      if (following && selectedAt) keepInView(selectedAt, false);
      // Every frame while somebody is scrubbing, twice a second during playback:
      // the numbers on the card do not change faster than they can be read.
      renderCard(!playing);
      if (clockEl) clockEl.textContent = clockText(clock);
      if (elapsedEl) elapsedEl.textContent = raceElapsedText(clock);
      if (windGauge) {
        const w = windAt(data.wind, clock);
        // Nothing rather than a stale reading dressed as current, which is how the
        // board already treats a boat whose tracker has gone quiet.
        windGaugeEl.hidden = !w;
        if (windCaptionEl) windCaptionEl.hidden = !w;
        if (w) window.WindGauge.setInstrument(windGauge, w[1], w[2], '', '', w[3]);
      }
      if (slider) slider.value = String(Math.round(clock));
      // Exact replay time, for scripts/verify_replay.py. The displayed clock and
      // the slider are both whole seconds, which is far too coarse to tell
      // whether playback ever runs backwards between frames.
      panel.dataset.clock = clock.toFixed(3);
    }

    // The order on the water is pre-computed by the server for every step of the
    // race and arrives with the track, so showing it is a local lookup — no
    // request per frame, and no lag behind the boats on the chart. Keeping the
    // computation server-side means the mark-rounding and finish logic is still
    // the same code the live view uses, rather than a second version in here
    // that could disagree with it.
    function boardIndexFor(t) {
      const times = data.boards.times;
      if (!times.length) return -1;
      let lo = 0, hi = times.length - 1;
      if (t <= times[0]) return 0;
      if (t >= times[hi]) return hi;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= t) lo = mid; else hi = mid;
      }
      return lo;
    }

    function refreshBoard(force) {
      if (!data || !data.boards || !data.boards.times.length) return;
      const ix = boardIndexFor(clock);
      if (ix < 0) return;
      if (ix === shownBoardIx && !force) return;      // same snapshot: nothing to redo
      shownBoardIx = ix;
      renderBoard(data.boards.rows[ix], data.boards.times[ix]);
      if (boardAtEl) {
        boardAtEl.textContent = `order at ${clockText(data.boards.times[ix])}`;
      }
    }

    // Which column of a snapshot each estimating method reads. One method drives
    // the ranking AND the times shown beside it: a board where the order came from
    // one estimator and the finish times from another would be indefensible to a
    // competitor reading down it.
    const EST_INDEX = {pace: 7, start: 8, recent: 9};

    // A corrected board is worked out here rather than on the server because the
    // competitor changes rating system, class and estimating method as they watch:
    // every snapshot already carries all three estimates and every boat its two
    // rating factors, so switching is a re-sort of what is on hand, not a request.
    function correctedRows(rows, at) {
      const estIdx = EST_INDEX[(methodSel && methodSel.value) || 'start'] || 8;
      const key = boardMode === 'irc' ? 'irc_factor' : 'ytc_factor';
      const cls = classSel ? classSel.value : '';
      const kept = [];
      (rows || []).forEach(r => {
        const boat = boatById[r[0]] || {};
        if (cls && (boat.class_label || '') !== cls) return;
        const factor = boat[key];
        const est = r[estIdx];
        // No rating, or too early to guess: still listed, but below the ranked boats.
        const corrected = (factor && est) ? est * factor : null;
        kept.push({row: r, corrected: corrected, est: est});
      });
      kept.sort((a, b) => {
        if (a.corrected === null && b.corrected === null) return a.row[1] - b.row[1];
        if (a.corrected === null) return 1;
        if (b.corrected === null) return -1;
        return a.corrected - b.corrected;
      });
      // The server withholds an estimate for the first ten minutes of racing, so
      // "nobody has one" is how the viewer knows it is still too early to rank.
      return {rows: kept, early: !kept.some(k => k.est)};
    }

    function renderBoard(rows, at) {
      boardEl.innerHTML = '';
      const total = data.boards.total;
      if (boardMode === 'line') { renderLineBoard(rows, total, at); return; }
      const out = correctedRows(rows, at);
      let rank = 0;
      out.rows.forEach(item => {
        const [entryId, position, rounded, nextMark, toGoNm, finished] = item.row;
        const boat = boatById[entryId] || {};
        const tr = newRow(entryId, finished);
        const marks = (rounded === null || rounded === undefined) ? '—' : `${rounded}/${total}`;
        const toGo = (toGoNm === null || toGoNm === undefined) ? '—' : `${Number(toGoNm).toFixed(2)} nm`;
        if (item.corrected !== null) rank += 1;
        tr.innerHTML =
          `<td data-label="Pos">${item.corrected === null ? '—' : rank}</td>` +
          boatCell(entryId, boat) +
          `<td data-label="Marks">${marks}</td><td data-label="To go">${toGo}</td>` +
          `<td data-label="Est. elapsed">${item.est ? elapsedText(item.est) : '—'}</td>` +
          `<td data-label="Corrected">${item.corrected === null ? '—' : elapsedText(item.corrected)}</td>`;
        boardEl.appendChild(tr);
      });
      if (boardNoteEl) boardNoteEl.innerHTML = boardNote(out.early);
    }

    function boardNote(early) {
      const name = boardMode === 'irc' ? 'IRC' : 'YTC';
      if (early) {
        const mins = Math.round((data.boards.estimate_after_s || 600) / 60);
        return `<strong>Estimated ${name} order</strong> — nothing to show yet. ` +
               `Estimates start after the first ${mins} minutes of racing, once a boat has ` +
               `sailed enough of the course to say anything useful about the rest of it.`;
      }
      const method = (methodSel && methodSel.value) || 'start';
      const mins = Math.round((data.boards.vmc_window_s || 1200) / 60);
      const how = method === 'recent'
        ? `how fast each boat has been closing on the finish <strong>over the last ${mins} minutes</strong>, ` +
          `carried over the distance it has left. This one notices a boat parking, and is the jumpiest`
        : method === 'pace'
        ? `each boat's <strong>pace against the polar</strong> — how its time so far compares with the ` +
          `target time for the legs it has sailed, applied to the legs it has not. This one needs the ` +
          `wind, and the only wind the club measures is at the hut`
        : `each boat's <strong>average pace since the start</strong> — how much of the course it has ` +
          `covered, and how long that has taken. No wind and no polar come into it`;
      return `<strong>Estimated ${name} corrected order</strong>, not a result. ` +
             `The finish time is projected from ${how}, then the boat's rating is applied. ` +
             `A boat already finished shows its real elapsed time.`;
    }

    function renderLineBoard(rows, total, at) {
      const ix = shownBoardIx;
      (rows || []).forEach(r => {
        const [entryId, position, rounded, nextMark, toGoNm, finished, sog] = r;
        const boat = boatById[entryId] || {};
        const tr = newRow(entryId, finished);
        const marks = (rounded === null || rounded === undefined) ? '—' : `${rounded}/${total}`;
        const next = finished ? 'Finished' : (nextMark || '—');
        const toGo = (toGoNm === null || toGoNm === undefined) ? '—' : `${Number(toGoNm).toFixed(2)} nm`;
        const sogText = (sog === null || sog === undefined) ? '—' : `${Number(sog).toFixed(1)} kn`;
        tr.innerHTML =
          `<td data-label="Pos">${position}</td>` + boatCell(entryId, boat) +
          `<td data-label="Marks">${marks}</td><td data-label="Next">${escapeHtml(String(next))}</td>` +
          `<td data-label="To go">${toGo}</td><td data-label="SOG">${sogText}</td>` +
          `<td data-label="VMC">${knotsText(vmcAt(entryId, ix))}</td>` +
          `<td data-label="Sailed">${milesText(sailedNm(boat, at))}</td>`;
        boardEl.appendChild(tr);
      });
    }

    function newRow(entryId, finished) {
      const tr = document.createElement('tr');
      tr.className = (entryId === selected ? 'selected' : '') + (finished ? ' finished' : '')
                   + (isFavourite(entryId) ? ' is-favourite' : '');
      tr.dataset.entryId = String(entryId);
      return tr;
    }

    // data-label drives the one-card-per-boat layout on a phone.
    function boatCell(entryId, boat) {
      const swatch = colourOf[entryId]
        ? `<span class="replay-swatch" style="background:${colourOf[entryId]}"></span>` : '';
      const star = favs ? favs.starHtml(keyOf(entryId), boat.boat_name || '') : '';
      return `<td class="stack-wide" data-label="Boat">${swatch}${escapeHtml(boat.boat_name || '')}${star}<br>` +
             `<span class="small muted">${escapeHtml(boat.sail_no || '')}</span></td>`;
    }

    function knotsText(v) { return (v === null || v === undefined) ? '—' : `${v.toFixed(1)} kn`; }
    function milesText(v) { return (v === null || v === undefined) ? '—' : `${v.toFixed(1)} nm`; }

    // --- Which boat ------------------------------------------------------
    // One boat can be picked out: tapped on the chart, or its row tapped in the
    // board. Its track stands out, its marker is ringed and the card shows what
    // is known about it at the moment on the clock.

    function keyOf(entryId) { return (boatInfo[String(entryId)] || {}).key || ''; }
    function isFavourite(entryId) { return !!favs && favs.has(keyOf(entryId)); }

    // The viewer's choices survive the page reloading itself, which it does
    // whenever the race officer changes something. Kept per race and per tab.
    function loadState() {
      try { return JSON.parse(window.sessionStorage.getItem(STATE_KEY) || 'null') || {}; }
      catch (err) { return {}; }
    }
    function saveState() {
      try {
        window.sessionStorage.setItem(STATE_KEY, JSON.stringify(
          {sel: selected, follow: following, speed: SPEEDS[speedIx]}));
      } catch (err) { /* no storage: the choices last until the next reload */ }
    }

    function selectBoat(id, opts) {
      const o = opts || {};
      const want = (id === null || id === undefined) ? null : Number(id);
      selected = (o.toggle && selected === want) ? null : want;
      if (selected !== null && !boatById[selected]) selected = null;
      if (selected === null) following = false;
      saveState();
      drawFrame();           // redraws the card as well
      refreshBoard(true);
      revealSelected();
    }

    // A boat just picked should be somewhere it can be seen: on a phone the
    // card that opens for it may be lying right across it.
    function revealSelected() {
      const boat = selected !== null ? boatById[selected] : null;
      const p = boat ? positionAt(boat.fixes || [], clock) : null;
      if (p) keepInView(p, false);
    }

    function rowFor(ix, entryId) {
      const rows = (data && data.boards.rows[ix]) || [];
      for (let i = 0; i < rows.length; i++) if (rows[i][0] === entryId) return rows[i];
      return null;
    }

    // When a boat finished, to the nearest board snapshot; null while it races.
    // Its tracker keeps reporting all the way back to the mooring, and none of
    // that is the race.
    function finishTime(entryId) {
      if (finishAt[entryId] !== undefined) return finishAt[entryId];
      const times = data.boards.times;
      const last = times.length - 1;
      const lastRow = last >= 0 ? rowFor(last, entryId) : null;
      if (!lastRow || !lastRow[5]) return null;        // still racing: ask again later
      let lo = 0, hi = last;
      while (lo < hi) {                                // the first snapshot with it finished
        const mid = (lo + hi) >> 1;
        const r = rowFor(mid, entryId);
        if (r && r[5]) hi = mid; else lo = mid + 1;
      }
      finishAt[entryId] = times[lo];
      return times[lo];
    }

    // Distance sailed since the start signal, in nautical miles, by time t.
    // Measured along the track, so it is more than the course length by however
    // much the boat tacked, gybed and went the long way round.
    function sailedNm(boat, t) {
      const fixes = (boat && boat.fixes) || [];
      if (!fixes.length) return null;
      if (!boat.sailed || boat.sailed.length !== fixes.length) boat.sailed = sailedSeries(fixes);
      const gun = data.first_start || data.start;
      const done = finishTime(boat.entry_id);
      const until = (done !== null && done < t) ? done : t;
      if (until <= gun) return 0;
      const a = fixIndexAt(fixes, gun), b = fixIndexAt(fixes, until);
      if (b < 0) return 0;
      return (boat.sailed[b] - (a >= 0 ? boat.sailed[a] : 0)) / 1852;
    }

    // Velocity made good on the course: how fast the distance left to sail has
    // been falling, in knots. Read off the server's own distance-to-go at two
    // snapshots, so it agrees with the To go column beside it, and over the
    // server's own window (twenty minutes), so "recent" means one thing on this
    // page: it is the pace the Recent pace estimate carries forward. Early in a
    // race it is the pace so far. Negative when a boat is sailing away from its
    // next mark. It jumps if the course is shortened, for as long as the two
    // snapshots straddle the change.
    function vmcAt(entryId, ix) {
      if (!data || ix < 0) return null;
      const times = data.boards.times;
      const now = rowFor(ix, entryId);
      if (!now || now[5] || now[4] === null || now[4] === undefined) return null;
      const from = boardIndexFor(times[ix] - (data.boards.vmc_window_s || VMC_WINDOW_S));
      if (from < 0 || from >= ix) return null;
      const span = times[ix] - times[from];
      if (span < 60) return null;                      // too short a run to mean anything
      const then = rowFor(from, entryId);
      if (!then || then[4] === null || then[4] === undefined) return null;
      return (then[4] - now[4]) / (span / 3600);
    }

    // --- The boat card ---------------------------------------------------
    const card = panel.querySelector('.boat-card');
    const cardEl = name => (card ? card.querySelector('.boat-card-' + name) : null);
    let cardDrawnAt = 0;

    function fact(label, value) {
      return `<div><dt>${label}</dt><dd>${value}</dd></div>`;
    }

    function renderCard(force) {
      if (!card) return;
      const boat = (selected !== null && data) ? boatById[selected] : null;
      if (!boat) { card.hidden = true; panel.classList.remove('boat-card-open'); return; }
      const nowMs = window.performance.now();
      if (!force && nowMs - cardDrawnAt < CARD_REFRESH_MS) return;
      cardDrawnAt = nowMs;
      const info = boatInfo[String(selected)] || {};
      card.hidden = false;
      panel.classList.add('boat-card-open');

      cardEl('name').textContent = boat.boat_name || '';
      cardEl('sail').textContent = boat.sail_no || '';
      cardEl('swatch').style.background = colourOf[selected] || 'transparent';
      const about = [info.design, info.club, info.class,
                     info.irc ? `IRC ${info.irc}` : '', info.ytc ? `YTC ${info.ytc}` : '']
        .filter(Boolean).join(' · ');
      cardEl('about').textContent = about;
      cardEl('about').hidden = !about;

      const p = positionAt(boat.fixes || [], clock);
      const ix = boardIndexFor(clock);
      const row = ix >= 0 ? rowFor(ix, selected) : null;
      const finished = !!(row && row[5]);
      const facts = [];
      // The first four are what a glance is for, and all a phone shows until
      // More is pressed: the card lies over the chart there, and the chart is
      // what the viewer came to look at.
      const sog = p ? p.sog : null, cog = p ? p.cog : null;
      if (row) {
        facts.push(fact('Place', `${row[1]} of ${data.boards.rows[ix].length}`));
        facts.push(fact('Next mark', finished ? 'Finished' : escapeHtml(String(row[3] || '—'))));
        facts.push(fact('To go', (finished || row[4] === null || row[4] === undefined)
          ? '—' : `${Number(row[4]).toFixed(2)} nm`));
      }
      facts.push(fact('Speed', (sog === null || sog === undefined) ? '—' : `${Number(sog).toFixed(1)} kn`));
      if (row) {
        facts.push(fact('Marks', (row[2] === null || row[2] === undefined)
          ? '—' : `${row[2]}/${data.boards.total}`));
      }
      facts.push(fact('Heading', (cog === null || cog === undefined) ? '—' : `${Math.round(cog)}°`));
      facts.push(fact('VMC', knotsText(vmcAt(selected, ix))));
      facts.push(fact('Sailed', milesText(sailedNm(boat, clock))));
      if (row) {
        const method = (methodSel && methodSel.value) || 'start';
        const est = row[EST_INDEX[method] || 8];
        facts.push(fact(finished ? 'Elapsed' : 'Est. elapsed', est ? elapsedText(est) : '—'));
        // Corrected on the system the board is showing; IRC where it is showing
        // neither and the boat has a rating.
        const system = boardMode === 'ytc' ? 'ytc' : 'irc';
        const factor = boat[system + '_factor'] || boat.irc_factor || boat.ytc_factor;
        const systemName = boat[system + '_factor'] ? system.toUpperCase()
          : (boat.irc_factor ? 'IRC' : 'YTC');
        if (factor) {
          facts.push(fact(`${finished ? '' : 'Est. '}${systemName} corrected`,
                          est ? elapsedText(est * factor) : '—'));
        }
      }
      // Only said when it matters: a boat whose tracker has gone quiet is being
      // shown where it was, and the viewer should know how long ago that is.
      if (p && p.age > 90) {
        facts.push(fact('Last report', `${Math.round(p.age / 60)} min before`));
      }
      cardEl('facts').innerHTML = facts.join('');

      // The two boats nearest it on the water, each a way to jump to that boat.
      const near = [];
      if (p) {
        data.boats.forEach(other => {
          if (other.entry_id === selected) return;
          const q = positionAt(other.fixes || [], clock);
          if (!q) return;
          near.push({id: other.entry_id, name: other.boat_name || other.sail_no || '',
                     nm: metresBetween(p.lat, p.lon, q.lat, q.lon) / 1852});
        });
        near.sort((a, b) => a.nm - b.nm);
      }
      const nearEl = cardEl('near');
      nearEl.hidden = !near.length;
      nearEl.innerHTML = near.length ? 'Nearest: ' + near.slice(0, 2).map(n =>
        `<button type="button" class="link boat-card-jump" data-entry-id="${n.id}">${escapeHtml(n.name)}</button>` +
        ` ${n.nm < 10 ? n.nm.toFixed(2) : n.nm.toFixed(1)} nm`).join(' · ') : '';

      const fav = cardEl('fav');
      fav.hidden = !(favs && info.key);
      if (favs && info.key) {
        fav.dataset.favKey = info.key;
        fav.dataset.favName = boat.boat_name || '';
        favs.refresh(card);
      }
      const followBtn = cardEl('follow');
      followBtn.setAttribute('aria-pressed', following ? 'true' : 'false');
      followBtn.textContent = following ? 'Following' : 'Follow';
      followBtn.title = following
        ? 'The chart is keeping this boat in view. Tap to stop, or drag the chart.'
        : 'Keep this boat in view as it moves';
      const video = cardEl('video');
      video.hidden = !info.video;
      if (info.video) video.href = info.video;
      cardEl('share').hidden = !info.key;
    }

    // Keep the followed boat on the chart. It moves the chart only when the
    // boat nears an edge -- a chart that re-centres on every frame cannot be
    // read -- and never fights the viewer: dragging the chart stops following.
    let followHoldUntil = 0;
    function keepInView(p, force) {
      const map = window.RaceCourseMap.mapOf(mapRoot);
      if (!map || !p) return;
      const nowMs = window.performance.now();
      if (!force && nowMs < followHoldUntil) return;
      const size = map.getSize();
      if (!size.x || !size.y) return;          // the Chart tab is not open: nothing to move
      const at = map.latLngToContainerPoint([p.lat, p.lon]);
      const mx = size.x * FOLLOW_EDGE, my = size.y * FOLLOW_EDGE;
      // On a phone the card lies across the bottom of the chart, so the boat
      // has to be kept above it, not merely on the map.
      const covered = (card && !card.hidden && window.matchMedia('(max-width: 700px)').matches)
        ? card.offsetHeight + 40 : 0;
      const bottom = Math.max(my, covered + my / 2);
      if (force || at.x < mx || at.x > size.x - mx || at.y < my || at.y > size.y - bottom) {
        followHoldUntil = nowMs + FOLLOW_SETTLE_MS;
        // Centred in the part of the chart that can be seen, which on a phone
        // is the part above the card.
        const target = covered ? map.containerPointToLatLng(at.add([0, covered / 2])) : [p.lat, p.lon];
        map.panTo(target, {animate: true, duration: 0.6});
      }
    }

    function setFollowing(on) {
      following = !!on && selected !== null;
      saveState();
      const boat = following ? boatById[selected] : null;
      const p = boat ? positionAt(boat.fixes || [], clock) : null;
      if (p) keepInView(p, true);
      renderCard(true);
    }

    let mapBound = false;
    function bindMap() {
      if (mapBound) return;
      const map = window.RaceCourseMap.mapOf(mapRoot);
      if (!map) return;
      mapBound = true;
      map.on('dragstart', () => { if (following) setFollowing(false); });
    }

    async function shareBoat() {
      const info = boatInfo[String(selected)] || {};
      if (!info.key) return;
      // The pinned page for this race: the one that stays on it, which is what
      // a link sent to somebody should do.
      const url = new URL(panel.dataset.shareUrl || window.location.pathname, window.location.href);
      url.search = '';
      url.searchParams.set('boat', info.key);
      url.hash = 'ptab-chart';
      const button = cardEl('share');
      try {
        if (window.navigator.share) {
          await window.navigator.share({title: `${info.name} · ${document.title}`, url: url.toString()});
          return;
        }
        await window.navigator.clipboard.writeText(url.toString());
        button.textContent = 'Link copied';
        window.setTimeout(() => { button.textContent = 'Share'; }, 2000);
      } catch (err) {
        if (err && err.name === 'AbortError') return;     // the share sheet was closed
        window.prompt('Copy this link to share it', url.toString());
      }
    }

    if (card) {
      cardEl('close').addEventListener('click', () => selectBoat(null));
      cardEl('more').addEventListener('click', () => {
        const open = !card.classList.contains('expanded');
        card.classList.toggle('expanded', open);
        cardEl('more').setAttribute('aria-expanded', open ? 'true' : 'false');
        cardEl('more').textContent = open ? 'Less' : 'More';
      });
      cardEl('follow').addEventListener('click', () => setFollowing(!following));
      cardEl('share').addEventListener('click', shareBoat);
      cardEl('near').addEventListener('click', ev => {
        const jump = ev.target.closest('.boat-card-jump');
        if (jump) selectBoat(Number(jump.dataset.entryId));
      });
    }
    // A star pressed anywhere -- the card, the board, the tables above -- changes
    // which boats are marked on the chart and offered as a view.
    if (favs) {
      favs.onChange(() => {
        if (!data) return;
        fitViewPicker();
        drawFrame();
        refreshBoard(true);
      });
    }

    // --- Views -------------------------------------------------------------
    // Somewhere to look, chosen from a list: the start line, the finish, the
    // whole course, or wherever the boats are now.
    function showView(which) {
      const views = window.RaceCourseMap.views(mapRoot) || {};
      let points = null, minSpanM = 0;
      if (which === 'start') { points = views.start; minSpanM = 500; }
      else if (which === 'finish') { points = views.finish; minSpanM = 500; }
      else if (which === 'course') { points = views.course; }
      else if (which === 'fleet' || which === 'favourites') {
        points = [];
        ((data && data.boats) || []).forEach(b => {
          if (which === 'favourites' && !isFavourite(b.entry_id)) return;
          const p = positionAt(b.fixes || [], clock);
          if (p) points.push([p.lat, p.lon]);
        });
        minSpanM = 400;
      }
      if (!points || !points.length) return;
      if (following) setFollowing(false);      // a view chosen by hand replaces following
      window.RaceCourseMap.fitTo(mapRoot, points,
        {minSpanM: minSpanM, slack: 1.02, padding: [36, 36], maxZoom: 17, duration: 0.8});
    }

    // "Favourites" is only offered when one of them is in this race. Added and
    // removed rather than hidden, because an iPhone shows a hidden <option>.
    function fitViewPicker() {
      if (!viewSel) return;
      const have = viewSel.querySelector('option[value="favourites"]');
      const want = !!data && data.boats.some(b => isFavourite(b.entry_id));
      if (want && !have) {
        const option = document.createElement('option');
        option.value = 'favourites';
        option.textContent = 'Favourites';
        viewSel.appendChild(option);
      } else if (!want && have) {
        have.remove();
      }
    }

    if (viewSel) {
      viewSel.addEventListener('change', () => {
        const which = viewSel.value;
        viewSel.value = '';          // a list of places to go, not a setting that sticks
        if (which) showView(which);
      });
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c =>
        ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    }

    function tick(now) {
      if (playing && data) {
        if (lastFrame !== null) {
          // Clamp the frame gap. Browsers stop firing requestAnimationFrame in a
          // background tab, so without this the first frame after switching back
          // would carry the whole time-away and jump the replay to the end.
          // Clamped, a backgrounded replay simply pauses and resumes in place.
          const dt = Math.min((now - lastFrame) / 1000, MAX_FRAME_S);
          clock += dt * SPEEDS[speedIx];
          if (clock >= data.end) {          // caught up with the live edge
            clock = data.end;
            setPlaying(false);
            atLive = true;
            describe();
          }
        }
        lastFrame = now;
        drawFrame();
        refreshBoard();
      }
      window.requestAnimationFrame(tick);
    }

    function setPlaying(on) {
      playing = on;
      if (on && data && clock < data.end - LIVE_EDGE_S) atLive = false;
      lastFrame = null;
      if (playBtn) {
        playBtn.textContent = on ? '❚❚ Pause' : '▶ Play';
        playBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
      if (!on) refreshBoard();
    }

    if (playBtn) playBtn.addEventListener('click', () => setPlaying(!playing));
    function setSpeed(value) {
      const ix = SPEEDS.indexOf(Number(value));
      speedIx = ix >= 0 ? ix : SPEEDS.indexOf(DEFAULT_SPEED);
      if (speedSel) speedSel.value = String(SPEEDS[speedIx]);
    }
    if (speedSel) {
      speedSel.innerHTML = SPEEDS.map(s => `<option value="${s}">${s}×</option>`).join('');
      setSpeed(loadState().speed || DEFAULT_SPEED);
      speedSel.addEventListener('change', () => { setSpeed(speedSel.value); saveState(); });
    }
    if (slider) {
      slider.addEventListener('input', () => {
        clock = Number(slider.value);
        atLive = (data && clock >= data.end - LIVE_EDGE_S);
        drawFrame();
        refreshBoard();
        describe();
      });
    }
    if (liveBtn) liveBtn.addEventListener('click', () => { if (data) goLive(); });

    // --- Full screen -----------------------------------------------------
    // On a phone the chart is a postage stamp with a leaderboard under it. Full
    // screen gives the chart the whole display and floats the board over it,
    // rolled away until it is wanted.
    const fullBtn = panel.querySelector('.replay-full');
    const sheet = panel.querySelector('.replay-sheet');
    const sheetTab = panel.querySelector('.replay-sheet-tab');

    // Rolled away to start with, on every screen: opening the Chart tab is a
    // request to see the chart, and the board is one tap away when it is wanted.
    function defaultRolled() {
      return '1';
    }

    const exitBtn = panel.querySelector('.replay-exit');

    // The sheet is anchored above the control bar, whose height depends on how
    // its buttons have wrapped — so it is measured rather than assumed, on load
    // and again whenever the layout could have changed.
    function measureControls() {
      const bar = panel.querySelector('.replay-controls');
      if (!bar) return;
      panel.style.setProperty('--replay-controls-h',
                              Math.round(bar.getBoundingClientRect().height) + 'px');
    }

    function setFullscreen(on) {
      panel.classList.toggle('replay-fullscreen', on);
      document.body.classList.toggle('replay-fullscreen-open', on);
      if (exitBtn) exitBtn.hidden = !on;
      // Going full screen is a request to look at the chart, so the board gets
      // out of the way; leaving restores whatever suits the screen.
      if (sheet) sheet.dataset.rolled = on ? '1' : defaultRolled();
      panel.classList.toggle('replay-sheet-open', !!sheet && sheet.dataset.rolled === '0');
      measureControls();
      if (fullBtn) {
        fullBtn.textContent = on ? '⛶ Exit full screen' : '⛶ Full screen';
        fullBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
      // Nothing fired a resize — the map moved because a class changed.
      window.RaceCourseMap.invalidate(panel);
      // Leaflet needs the new size to have been laid out before it re-centres.
      setTimeout(() => { window.RaceCourseMap.invalidate(panel); drawFrame(); }, 60);
    }

    function inFullscreen() { return panel.classList.contains('replay-fullscreen'); }

    if (fullBtn) {
      fullBtn.addEventListener('click', () => {
        const on = !inFullscreen();
        setFullscreen(on);
        // Ask for real fullscreen too where it exists, to lose the browser
        // chrome as well. iOS Safari has none of this on a plain element, which
        // is why the CSS above does the actual work.
        try {
          if (on && panel.requestFullscreen) panel.requestFullscreen().catch(() => {});
          else if (!on && document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
        } catch (err) { /* the class-based layout stands on its own */ }
      });
    }
    if (exitBtn) {
      exitBtn.addEventListener('click', () => {
        setFullscreen(false);
        try {
          if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
        } catch (err) { /* the class-based layout stands on its own */ }
      });
    }
    if (sheetTab && sheet) {
      sheet.dataset.rolled = defaultRolled();
      sheetTab.setAttribute('aria-expanded', sheet.dataset.rolled === '1' ? 'false' : 'true');
      sheetTab.addEventListener('click', () => {
        const rolled = sheet.dataset.rolled === '1';
        sheet.dataset.rolled = rolled ? '0' : '1';
        sheetTab.setAttribute('aria-expanded', rolled ? 'true' : 'false');
        panel.classList.toggle('replay-sheet-open', rolled);
      });
    }
    // The control bar rewraps as the window changes, moving the sheet's anchor.
    // A ResizeObserver rather than a load-time measurement: the Chart tab starts
    // hidden, so at init the bar measures zero and the sheet would be anchored to
    // the bottom of the panel — straight over the controls it is meant to clear.
    const bar = panel.querySelector('.replay-controls');
    if (bar && window.ResizeObserver) {
      new window.ResizeObserver(measureControls).observe(bar);
    } else {
      window.addEventListener('resize', measureControls);
    }
    measureControls();
    // Leaving fullscreen by the browser's own control (Esc, or the system UI)
    // must put the page back, or the panel is left pinned over everything.
    document.addEventListener('fullscreenchange', () => {
      if (!document.fullscreenElement && inFullscreen()) setFullscreen(false);
    });
    // Escape always leaves, rather than deferring to the browser when the real
    // Fullscreen API happened to be granted: if that key never reaches us the
    // panel is left pinned over the page with no way back except reloading.
    document.addEventListener('keydown', ev => {
      if (ev.key !== 'Escape' || !inFullscreen()) return;
      setFullscreen(false);
      try {
        if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
      } catch (err) { /* already out */ }
    });

    function applyBoardMode() {
      boardMode = modeSel ? modeSel.value : 'line';
      const corrected = boardMode !== 'line';
      if (classWrap) classWrap.hidden = !corrected || classSel.options.length < 2;
      if (methodWrap) methodWrap.hidden = !corrected;
      if (boardHeadEl) {
        boardHeadEl.innerHTML = (corrected ? CORRECTED_HEAD : LINE_HEAD)
          .map(h => `<th>${h}</th>`).join('');
      }
      if (!corrected && boardNoteEl) boardNoteEl.innerHTML = LINE_NOTE;
      refreshBoard(true);
    }
    [modeSel, classSel, methodSel].forEach(sel => {
      if (sel) sel.addEventListener('change', applyBoardMode);
    });

    // Which rating systems and classes this race actually has. A race scored on
    // one system should not offer the other, and a fleet with no classes has
    // nothing to filter by.
    function fitBoardPicker() {
      const boats = data.boats || [];
      const has = key => boats.some(b => b[key]);
      if (modeSel) {
        Array.from(modeSel.options).forEach(o => {
          if (o.value === 'irc') o.hidden = !has('irc_factor');
          if (o.value === 'ytc') o.hidden = !has('ytc_factor');
        });
        if (modeSel.selectedOptions[0] && modeSel.selectedOptions[0].hidden) modeSel.value = 'line';
      }
      if (classSel) {
        const classes = Array.from(new Set(boats.map(b => b.class_label).filter(Boolean))).sort();
        const want = classSel.value;
        classSel.innerHTML = '<option value="">Overall</option>' +
          classes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
        if (classes.indexOf(want) >= 0) classSel.value = want;
      }
      // Without a polar for these boats there is no pace factor to work from.
      if (methodSel && data.boards && data.boards.polar_available === false) {
        const pace = methodSel.querySelector('option[value="pace"]');
        if (pace) pace.hidden = true;
        methodSel.value = 'start';
      }
      applyBoardMode();
    }
    if (boardEl) {
      boardEl.addEventListener('click', ev => {
        if (ev.target.closest('.fav-star')) return;   // the star is its own control
        const tr = ev.target.closest('tr');
        if (!tr || !tr.dataset.entryId) return;
        selectBoat(Number(tr.dataset.entryId), {toggle: true});   // tap again to unselect
      });
    }

    // Only what is new comes back, so a long race is not re-sent every poll.
    function mergeUpdate(payload) {
      if (!payload.incremental) {
        // The whole race again, mid-race: fixes arrived that belong behind what is
        // on screen (a tracker's saved fixes after a spell out of signal). Someone
        // who had wound back stays where they were rather than being thrown to
        // the live edge.
        const keep = atLive ? null : clock;
        adopt(payload);
        if (keep !== null) {
          clock = Math.min(Math.max(keep, data.start), data.end);
          atLive = false;
          drawFrame();
          refreshBoard();
          describe();
        }
        return;
      }
      data.end = payload.end;
      if (payload.stored !== undefined && payload.stored !== null) data.stored = payload.stored;
      (payload.boats || []).forEach(nb => {
        const b = boatById[nb.entry_id];
        if (!b) return;
        if (nb.fixes && nb.fixes.length) {
          // Belt and braces against a boundary fix arriving twice.
          const have = b.fixes.length ? b.fixes[b.fixes.length - 1][0] : -Infinity;
          b.fixes = b.fixes.concat(nb.fixes.filter(f => f[0] > have));
        }
        b.tracked = nb.tracked;
      });
      const nb = payload.boards;
      if (nb && nb.times && nb.times.length) {
        // The server cuts the board at the cursor this page sends; this only
        // guards against a snapshot arriving twice.
        const last = lastBoardTime();
        const keep = nb.times.map((t, i) => i).filter(i => last === null || nb.times[i] > last);
        data.boards.times = data.boards.times.concat(keep.map(i => nb.times[i]));
        data.boards.rows = data.boards.rows.concat(keep.map(i => nb.rows[i]));
        data.boards.total = nb.total || data.boards.total;
      }
      // New wind readings are appended, so the gauge keeps up with a live race
      // rather than going blank once the reading it loaded with is ten minutes old.
      if (payload.wind && payload.wind.length) {
        const have = lastWindTime();
        data.wind = (data.wind || []).concat(payload.wind.filter(w => have === null || w[0] > have));
      }
      applyWindow();
    }

    function lastBoardTime() {
      const t = data && data.boards && data.boards.times;
      return t && t.length ? t[t.length - 1] : null;
    }

    function lastWindTime() {
      const w = data && data.wind;
      return w && w.length ? w[w.length - 1][0] : null;
    }

    // The newest board snapshot and wind reading this page holds, so a reply
    // carries only what comes after each of them.
    function cursorParams() {
      const b = lastBoardTime(), w = lastWindTime();
      return (b !== null ? `&board=${encodeURIComponent(b)}` : '')
        + (w !== null ? `&wind=${encodeURIComponent(w)}` : '');
    }

    function applyWindow() {
      if (!slider) return;
      slider.min = String(Math.floor(data.start));
      slider.max = String(Math.ceil(data.end));
      slider.disabled = false;
      if (playBtn) playBtn.disabled = false;
    }

    function lastFixTime() {
      let latest = null;
      (data.boats || []).forEach(b => {
        if (b.fixes && b.fixes.length) {
          const t = b.fixes[b.fixes.length - 1][0];
          if (latest === null || t > latest) latest = t;
        }
      });
      return latest;
    }

    function describe() {
      const tracked = (data.boats || []).filter(b => b.fixes && b.fixes.length);
      const fixCount = tracked.reduce((n, b) => n + b.fixes.length, 0);
      // Asked of the page rather than of raceOver, which is not set until after
      // the first of these is written: a finished race used to open saying "live".
      const over = raceOver || panel.dataset.raceFinished === '1';
      const where = atLive && !over ? 'live — ' : '';
      // Published for the page around us. A reload throws away where somebody had
      // scrubbed to, so the competitor page holds one back while the chart is open —
      // but only wound back into the past is worth protecting. Sitting at the live
      // edge, a reload costs nothing, and refusing it is how a race officer's start
      // time or shortened course failed to reach the very people watching the race.
      panel.dataset.atLive = (atLive && !raceOver) ? '1' : '0';
      setStatus(`${where}${tracked.length} boat${tracked.length === 1 ? '' : 's'}, `
                + `${fixCount.toLocaleString()} fixes over ${elapsedText(data.end - data.start)}.`);
    }

    function goLive() {
      clock = data.end;
      atLive = true;
      setPlaying(false);
      drawFrame();
      refreshBoard();
      describe();
    }

    async function pollForNewFixes() {
      if (!data) return;
      const since = lastFixTime();
      try {
        // `stored` hands back the server's cursor, so it can tell when fixes have
        // landed behind `since` and send the whole race instead.
        const url = trackUrl + (since !== null ? `?since=${encodeURIComponent(since)}` : '')
          + (since !== null && data.stored != null ? `&stored=${encodeURIComponent(data.stored)}` : '')
          + (since !== null ? cursorParams() : '');
        const res = await fetch(url, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
        if (!res.ok) return;
        const payload = await res.json();
        if (!payload || !payload.ok || payload.enabled === false) return;
        const grew = payload.end > data.end + 0.001;
        mergeUpdate(payload);
        if (atLive) {
          clock = data.end;          // stay pinned to the latest positions
          drawFrame();
          refreshBoard();
        }
        if (grew) describe();
      } catch (err) {
        // A dropped poll is not worth disturbing the view for; try again next tick.
      }
    }

    setStatus('Loading the track…');
    function adopt(payload) {
      data = payload;
      if (!data.boards) data.boards = {times: [], rows: [], total: 0};
      boatById = {};
      finishAt = {};
      data.boats.forEach((b, i) => {
        colourOf[b.entry_id] = COLOURS[i % COLOURS.length];
        boatById[b.entry_id] = b;
      });
      restoreChoices();
      fitViewPicker();
      bindMap();
      applyWindow();
      // Open at the latest positions: during a race that is simply the live
      // chart, and afterwards it is the finish — either way the moment people
      // most want to see first. Winding back is one drag away.
      clock = data.end;
      atLive = true;
      shownBoardIx = -1;
      drawFrame();
      fitBoardPicker();      // ends in refreshBoard()
      describe();
    }

    // Which boat to open on. What the viewer had chosen before a reload comes
    // first, including having chosen none; then a boat named in the address (a
    // shared link); then their first favourite sailing in this race.
    function restoreChoices() {
      if (selected !== null && !boatById[selected]) selected = null;
      if (stateRestored) return;
      stateRestored = true;
      const saved = loadState();
      if ('sel' in saved) {
        selected = (saved.sel !== null && boatById[saved.sel]) ? Number(saved.sel) : null;
        following = !!saved.follow && selected !== null;
        return;
      }
      const asked = new URLSearchParams(window.location.search).get('boat') || '';
      const byKey = key => (data.boats.find(b => key && keyOf(b.entry_id) === key) || {}).entry_id;
      let want = byKey(asked);
      if (want === undefined && favs) {
        favs.list().some(f => { want = byKey(f.key); return want !== undefined; });
      }
      selected = (want === undefined) ? null : want;
    }

    fetch(trackUrl, {headers: {'Accept': 'application/json'}})
      .then(r => r.ok ? r.json() : null)
      .then(payload => {
        if (!payload || !payload.ok) { setStatus('Could not load the track for this race.', 'warn'); return; }
        if (payload.enabled === false) { setStatus('GPS tracking is switched off.', 'warn'); return; }
        const tracked = (payload.boats || []).filter(b => b.fixes && b.fixes.length);
        if (!payload.start || !tracked.length) {
          setStatus('No GPS track has been recorded for this race yet.', 'warn');
          return;
        }
        adopt(payload);
        // Keep up while the race is on. A race whose last boat has finished has a
        // fixed window, so there is nothing to poll for.
        raceOver = panel.dataset.raceFinished === '1';
        if (!raceOver) livePoll = setInterval(pollForNewFixes, LIVE_POLL_MS);
      })
      .catch(() => setStatus('Could not load the track for this race.', 'warn'));

    window.requestAnimationFrame(tick);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init(document));
  } else {
    init(document);
  }
})();
