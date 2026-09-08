/* Copyright © 2026 CapeNet Ltd. All Rights Reserved. */
/*
 * The clubhouse TV.
 *
 * A stripped-down cousin of race_replay.js: no timeline, no controls, nothing to
 * click. It shows the fleet where it is now, keeps up on its own, and cuts to the
 * start-hut camera when there is something to watch.
 *
 * Boats are interpolated between fixes for the same reason the competitor chart
 * does it — ten-second reporting looks like a slideshow otherwise — and the order
 * beside the map is the server's, not a second implementation here.
 *
 * Two things matter because this runs unattended for hours on a screen nobody
 * touches: it only ever asks for the fixes it does not already have, and when the
 * club's *current* race moves on to the next one, the page follows it.
 */
(function () {
  'use strict';

  const TRACK_POLL_MS = 10000;      // new fixes
  const STATE_POLL_MS = 5000;       // camera window / has the race moved on
  // How long a clip may sit on screen without the picture moving before the
  // replay gives up on it and carries on at six times life. Long enough for a
  // slow start over the CDN, short enough that a television in a bar is not
  // left on a still frame.
  const CLIP_STALL_S = 10;
  const TRAIL_SECONDS = 900;
  const BOARD_CYCLE_MS = 15000;     // how long each leaderboard stays up
  const FIT_MS = 6000;              // how often to reconsider what the map shows
  const FIT_MIN_SPAN_M = 700;       // never zoom closer than this across
  const FIT_PADDING = [40, 40];      // breathing room inside the visible part
  const COLOURS = ['#2563eb', '#db2777', '#0891b2', '#7c3aed', '#c026d3',
                   '#1e3a8a', '#9d174d', '#155e75', '#4338ca', '#6b21a8'];

  function pad(n) { return String(n).padStart(2, '0'); }

  function hms(seconds) {
    const s = Math.max(0, Math.round(Math.abs(seconds)));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return (h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`);
  }

  function escapeHtml(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;',
                                  '"': '&quot;', "'": '&#39;'}[c]));
  }

  // Where a boat was at time t: the fix before, nudged towards the fix after.
  function positionAt(fixes, t) {
    if (!fixes || !fixes.length || t < fixes[0][0]) return null;
    let lo = 0, hi = fixes.length - 1;
    if (t >= fixes[hi][0]) {
      const f = fixes[hi];
      return {lat: f[1], lon: f[2], sog: f[3], cog: f[4], age: t - f[0]};
    }
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (fixes[mid][0] <= t) lo = mid; else hi = mid;
    }
    const a = fixes[lo], b = fixes[hi];
    const span = b[0] - a[0];
    const r = span > 0 ? (t - a[0]) / span : 0;
    return {lat: a[1] + (b[1] - a[1]) * r, lon: a[2] + (b[2] - a[2]) * r,
            sog: a[3], cog: a[4], age: t - a[0]};
  }

  function trailPoints(fixes, t, seconds) {
    const from = t - seconds, pts = [];
    for (let i = 0; i < fixes.length; i++) {
      const f = fixes[i];
      if (f[0] > t) break;
      if (f[0] >= from) pts.push([f[1], f[2]]);
    }
    const here = positionAt(fixes, t);
    if (here) pts.push([here.lat, here.lon]);
    return pts;
  }

  function init(stage) {
    const mapRoot = stage.querySelector('.bar-map');
    const boardBody = stage.querySelector('.bar-board tbody');
    const boardNote = stage.querySelector('.bar-board-note');
    const boardHead = stage.querySelector('.bar-board h2');
    const boardLast = stage.querySelector('.bar-board thead th:last-child');
    const boardDots = stage.querySelector('.bar-board-dots');
    const clockTime = stage.querySelector('.bar-clock-time');
    const clockState = stage.querySelector('.bar-clock-state');
    const videoPanel = stage.querySelector('.bar-video');
    const videoCaption = stage.querySelector('.bar-video-caption');
    const cameraWrap = stage.querySelector('.live-camera');
    const statusEl = stage.querySelector('.bar-status');
    const trackUrl = stage.dataset.trackUrl;
    const stateUrl = stage.dataset.stateUrl;
    const pinned = stage.dataset.pinned === '1';
    const raceId = parseInt(stage.dataset.raceId, 10) || 0;
    const startTs = stage.dataset.start ? Date.parse(stage.dataset.start) / 1000 : null;

    let data = null;
    let colourOf = {};
    let boatById = {};
    let videoOn = false;
    let raceFinished = false;
    let lastFinish = null;      // epoch of the last boat across the line
    let boardModes = [{key: 'line', title: 'Order on the water'}];
    let boardIx = 0;
    // A replay of a race that has been sailed, rather than the live view. The
    // whole plan -- both ends of the race and every clip -- is written into the
    // page, so there is no request per frame and no second source of truth.
    const replayPlan = (function () {
      try { return stage.dataset.replay ? JSON.parse(stage.dataset.replay) : null; }
      catch (e) { return null; }
    })();
    let replay = null;
    const allMarks = (function () {
      const el = stage.querySelector('.course-map');
      try { return JSON.parse((el && el.dataset.allMarks) || '{}'); } catch (e) { return {}; }
    })();

    // Every part of this display asks what time it is, and in a replay the
    // answer is not the wall clock. One function, so the chart, the clock, the
    // board and the map fit cannot disagree about which moment is being shown.
    function nowTs() { return replay ? replay.at() : Date.now() / 1000; }

    // The wind the race was sailed in. Live, the shared gauge polls the weather
    // station itself and this does nothing. Replaying, that would put this
    // afternoon's wind over Sunday's race -- and, being this afternoon's, it
    // would sit there not changing while the fleet beat up a shifting course.
    // The readings come down with the track, so the replay reads them off at its
    // own clock using the same rule as the race page.
    const windWrap = stage.querySelector('.bar-wind');
    const windGaugeEl = stage.querySelector('.bar-wind-gauge');
    const windGauge = (replayPlan && windGaugeEl && window.WindGauge)
      ? window.WindGauge.prepare(windGaugeEl) : null;

    function drawWind() {
      if (!windGauge) return;
      const w = window.WindGauge.readingAt(data && data.wind, nowTs());
      if (windWrap) windWrap.hidden = !w;
      windGaugeEl.hidden = !w;
      if (w) window.WindGauge.setInstrument(windGauge, w[1], w[2], '', '', w[3]);
    }

    function setStatus(text) { if (statusEl) statusEl.textContent = text || ''; }

    // --- replaying a race ------------------------------------------------
    // Six times life, except while a clip plays, when the clock follows the
    // video instead. It follows rather than merely matching speed: the replay
    // time IS the clip's own footage time, so buffering, a slow decode or a
    // television throttling the tab cannot let the boats drift away from the
    // picture. When a two-minute clip ends the replay has advanced exactly two
    // minutes and the fleet is where the video left it.
    function makeReplay(plan) {
      const video = stage.querySelector('.bar-replay-video');
      let at = plan.from_ts;            // where the replay has got to, in race time
      let last = performance.now();     // to measure real elapsed between frames
      let playing = null;               // the clip on screen, if any
      let clipCt = null;                // last playback position read off it
      let clipMoved = 0;                // when its picture last actually advanced
      let clipShown = false;            // has any of it really played?
      let done = false;
      let doneAt = 0;

      function clipDue(t) {
        for (let i = 0; i < plan.clips.length; i++) {
          const c = plan.clips[i];
          if (c.played) continue;
          if (t >= c.ends_at) { c.played = true; continue; }   // wholly behind us
          if (t >= c.starts_at) return c;
        }
        return null;
      }

      function endClip() {
        if (!playing) return;
        // Believe the plan, not the player: a clip that stalls or ends early
        // must still leave the replay where the footage ended, or the chart and
        // the video disagree from here on.
        //
        // A clip that never played at all is the other case, and it cost two
        // finishes. It showed nothing, so moving the clock to the end of footage
        // nobody saw skips whatever happened in it -- and on a race's last clip
        // it carries the replay past the last finish, which ends the replay then
        // and there. Leave the clock where it was and carry on at six times life.
        if (clipShown) at = playing.ends_at;
        playing.played = true;
        playing = null;
        clipShown = false;
        video.hidden = true;
        video.removeAttribute('src');
        video.load();
        setVideo(false, '');
      }

      function startClip(clip) {
        playing = clip;
        video.hidden = false;
        video.src = clip.url;
        // Start where the replay has already got to, not at the beginning of
        // the file. Clips overlap -- they carry a minute either side of their
        // moment, and boats finish close together -- so a clip can begin before
        // the previous one ended. Playing it from zero sent the clock
        // *backwards*: +0:52 to +0:02, boats sliding back up the course.
        // Seeking past the footage already shown keeps time moving one way and
        // skips the repeat.
        const seenUpTo = Math.max(0, at - clip.starts_at);
        video.currentTime = seenUpTo;
        clipCt = null;
        clipMoved = performance.now();
        clipShown = false;
        const caption = clip.also_covers && clip.also_covers.length
          ? clip.label + ' + ' + clip.also_covers.length + ' more'
          : (clip.label || (clip.kind === 'start' ? 'The start' : 'A finish'));
        setVideo(true, caption);
        askToPlay();
      }

      // A refused play() is not proof the television will not show the clip.
      // Asking while the new source is still loading is rejected out of hand --
      // AbortError, measured on the bar page itself -- and the element then sits
      // at canplay, ready and paused, waiting to be asked again. Treating that
      // first refusal as final is what cut the second finish to a couple of
      // seconds and skipped the third altogether. So ask, ignore the refusal, and
      // let tick() ask again; what decides whether a clip is playable is whether
      // its picture actually moves, not what a promise said.
      function askToPlay() {
        const p = video.play();
        if (p && p.catch) p.catch(() => {});
      }

      video.addEventListener('ended', endClip);
      video.addEventListener('error', endClip);

      function tick() {
        const nowReal = performance.now();
        const elapsed = Math.max(0, (nowReal - last) / 1000);
        last = nowReal;
        if (done) return;
        if (playing) {
          const ct = video.currentTime || 0;
          if (clipCt === null) clipCt = ct;              // the seek's landing place
          else if (ct > clipCt + 0.05) {
            clipCt = ct; clipMoved = nowReal; clipShown = true;
          }
          // The clip's own timeline is the clock -- once it is really running.
          // Until the first frame plays the clock is held where the handover left
          // it, rather than snapping back to the head of a clip joined in
          // progress.
          if (clipShown) {
            at = playing.starts_at + ct;
            if (video.ended || at >= playing.ends_at) { endClip(); return; }
          }
          if (video.paused && !video.ended) askToPlay();
          // Nothing moving for this long: a television refusing autoplay, a dead
          // URL, a stalled download. Give up on the clip rather than hold the
          // whole replay on a still frame.
          //
          // Only while the page is actually on screen, though. A hidden tab has
          // its video suspended by the browser, so the picture stops for a reason
          // that says nothing about whether the clip is playable, and judging it
          // then throws away footage that was fine. The bar television is never
          // hidden; a screen that has blanked, or a tab someone left behind, is.
          if (document.hidden) clipMoved = nowReal;
          else if ((nowReal - clipMoved) / 1000 > CLIP_STALL_S) endClip();
          return;
        }
        at += elapsed * plan.speed;
        const due = clipDue(at);
        if (due) {
          // Never rewind: if the clip began before now, it is joined in progress.
          at = Math.max(at, due.starts_at);
          startClip(due);
          return;
        }
        if (at >= plan.to_ts) {
          // The last boat is in. Hold on the finishing order long enough to
          // read it, then hand the television back to the live view.
          at = plan.to_ts;
          done = true;
          doneAt = nowReal;
          raceFinished = true;
          setStatus('Replay of ' + plan.name + ' — finished');
          window.setTimeout(() => {
            fetch('/bar/replay/finished/' + plan.race_id, {method: 'POST'})
              .catch(() => {})
              .then(() => { window.location.href = '/bar'; });
          }, plan.results_hold_s * 1000);
        }
      }

      window.setInterval(tick, 200);   // finer than the 1 s redraw, so the
                                       // handover to a clip lands on time
      return {
        at: function () { return at; },
        isPlayingClip: function () { return !!playing; }
      };
    }

    // --- the chart -------------------------------------------------------
    function draw() {
      if (!data) return;
      const now = nowTs();
      const boats = [], trails = [];
      (data.boats || []).forEach(b => {
        if (!b.fixes || !b.fixes.length) return;
        const p = positionAt(b.fixes, now);
        if (!p) return;
        boats.push({lat: p.lat, lon: p.lon, cog: p.cog, sog: p.sog,
                    sail_no: b.sail_no, boat_name: b.boat_name,
                    colour: colourOf[b.entry_id], stale: p.age > 180});
        trails.push({points: trailPoints(b.fixes, now, TRAIL_SECONDS),
                     colour: colourOf[b.entry_id]});
      });
      window.RaceCourseMap.updateTrails(mapRoot, trails);
      window.RaceCourseMap.updateBoats(mapRoot, boats);
    }

    // --- what the map should be showing ----------------------------------
    // A whole-course view wastes most of a television on empty sea once the fleet
    // has strung out down one leg. This follows the boats that are still racing:
    // each of them, and the mark each is sailing to, so a viewer can see the leg
    // rather than just the boats on it.
    //
    // Boats that have finished are left out on purpose — they are parked by the
    // line and would hold the view open across the whole course for the sake of
    // somebody who is already in the bar.
    function markLatLon(code) {
      const m = allMarks[String(code || '').trim().toUpperCase()];
      return (m && typeof m.lat === 'number' && typeof m.lon === 'number')
        ? [m.lat, m.lon] : null;
    }

    // The order on the water at the moment on screen. Live that is the newest
    // the server has sent, which is why this was simply the last row -- but in a
    // replay the last row is the finishing order, so every boat showed
    // "Finished" a minute before its own start. The boards come down with the
    // track as a series, so a replay picks the one for its own clock.
    function boardRows() {
      const b = data && data.boards;
      if (!b || !b.rows || !b.rows.length) return null;
      const times = b.times || [];
      if (!replay || times.length !== b.rows.length) return b.rows[b.rows.length - 1];
      const t = nowTs();
      if (t <= times[0]) return b.rows[0];
      let lo = 0, hi = times.length - 1;
      if (t >= times[hi]) return b.rows[hi];
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= t) lo = mid; else hi = mid;
      }
      return b.rows[lo];
    }

    function fitPoints() {
      const pts = [];
      if (data && data.boards && data.boards.rows.length) {
        const rows = boardRows();
        const now = nowTs();
        (rows || []).forEach(r => {
          const [entryId, position, rounded, nextMark, toGo, finished] = r;
          if (finished) return;
          const boat = boatById[entryId];
          const p = boat && boat.fixes && boat.fixes.length ? positionAt(boat.fixes, now) : null;
          if (p) pts.push([p.lat, p.lon]);
          const mark = markLatLon(nextMark);
          if (mark) pts.push(mark);
        });
      }
      // Nobody racing — before the start, or once everyone is in — so show the
      // course itself rather than holding whatever the last boat left behind.
      if (!pts.length) {
        const el = stage.querySelector('.course-map');
        let course = [];
        try { course = JSON.parse((el && el.dataset.courseMarks) || '[]'); } catch (e) { course = []; }
        course.forEach(m => {
          const at = markLatLon(typeof m === 'string' ? m : (m && m.mark));
          if (at) pts.push(at);
        });
        const line = markLatLon(el && el.dataset.startFinishMark);
        if (line) pts.push(line);
      }
      return pts;
    }

    function overlayInsets() {
      // Measured rather than assumed: the header wraps differently on a 4K screen
      // and the board is a proportion of the width.
      const box = stage.getBoundingClientRect();
      const head = stage.querySelector('.bar-top');
      const board = stage.querySelector('.bar-board');
      const hb = head ? head.getBoundingClientRect() : null;
      const bb = board ? board.getBoundingClientRect() : null;
      return {
        top: hb ? Math.max(0, hb.bottom - box.top) : 0,
        right: bb ? Math.max(0, box.right - bb.left) : 0,
        bottom: 0,
        left: 0,
      };
    }

    function refit() {
      const pts = fitPoints();
      if (!pts.length) return;
      window.RaceCourseMap.fitTo(mapRoot, pts, {
        minSpanM: FIT_MIN_SPAN_M, padding: FIT_PADDING, maxZoom: 15,
        insets: overlayInsets(),
      });
    }

    // --- the order beside the map ---------------------------------------
    // Nobody is going to walk up to the television and change the leaderboard, so
    // it shows each one in turn instead: the order on the water, then every
    // rating system the fleet is actually rated in. Overall only — a bar screen
    // is not the place to work through the classes one at a time.
    function fitBoardModes() {
      const boats = data.boats || [];
      const modes = [{key: 'line', title: 'Order on the water'}];
      if (boats.some(b => b.irc_factor)) {
        modes.push({key: 'irc', title: 'IRC corrected', factor: 'irc_factor'});
      }
      if (boats.some(b => b.ytc_factor)) {
        modes.push({key: 'ytc', title: 'YTC corrected', factor: 'ytc_factor'});
      }
      boardModes = modes;
      if (boardIx >= modes.length) boardIx = 0;
      if (boardHead) boardHead.textContent = modes[boardIx].title;
    }

    // A corrected board with nothing on it yet is worse than not showing it: the
    // estimate is withheld for the first ten minutes of racing, and a column of
    // dashes on a television for ten minutes tells the room nothing. Skip past
    // any board that has no times to give, and come back to it when it has.
    function boardHasContent(mode) {
      if (mode.key === 'line') return true;
      if (!data || !data.boards || !data.boards.rows.length) return false;
      const rows = boardRows() || [];
      return rows.some(r => {
        const boat = boatById[r[0]] || {};
        return boat[mode.factor] && r[EST_START];
      });
    }

    function cycleBoard() {
      if (boardModes.length < 2) return;
      for (let step = 1; step <= boardModes.length; step++) {
        const next = (boardIx + step) % boardModes.length;
        if (boardHasContent(boardModes[next])) { boardIx = next; break; }
      }
      drawBoard();
    }

    // Average pace since the start, the same default the competitor page ranks by.
    // It was `r[7] || r[8]` - the polar estimate with a VMC fallback - which meant a
    // board could rank one boat by the polar and the boat under it by VMC, whenever
    // the first had no polar estimate that snapshot. Two boats on the same board
    // measured different ways cannot be compared, which is the whole job of a board.
    const EST_START = 8;

    function correctedRows(rows, mode) {
      // A boat already finished shows its real time; one still racing shows the
      // projection, made the same way for every boat.
      const out = [];
      (rows || []).forEach(r => {
        const boat = boatById[r[0]] || {};
        const factor = boat[mode.factor];
        const est = r[EST_START];
        out.push({row: r, boat: boat,
                  corrected: (factor && est) ? est * factor : null});
      });
      out.sort((a, b) => {
        if (a.corrected === null && b.corrected === null) return a.row[1] - b.row[1];
        if (a.corrected === null) return 1;
        if (b.corrected === null) return -1;
        return a.corrected - b.corrected;
      });
      return out;
    }

    function drawBoard() {
      if (!data || !data.boards || !data.boards.rows.length) return;
      const rows = boardRows();
      const total = data.boards.total;
      let mode = boardModes[boardIx] || boardModes[0];
      if (!boardHasContent(mode)) { mode = boardModes[0]; boardIx = 0; }
      if (boardHead) boardHead.textContent = mode.title;
      if (boardLast) boardLast.textContent = mode.key === 'line' ? 'To go' : 'Corrected';
      boardBody.innerHTML = '';

      const items = mode.key === 'line'
        ? (rows || []).map(r => ({row: r, boat: boatById[r[0]] || {}, corrected: undefined}))
        : correctedRows(rows, mode);
      let rank = 0;
      items.forEach(item => {
        const [entryId, position, rounded, nextMark, toGo, finished] = item.row;
        const boat = item.boat;
        const tr = document.createElement('tr');
        if (finished) tr.className = 'finished';
        const swatch = colourOf[entryId]
          ? `<span class="bar-swatch" style="background:${colourOf[entryId]}"></span>` : '';
        let place, last;
        if (mode.key === 'line') {
          place = position;
          last = finished ? 'Finished'
               : (toGo === null || toGo === undefined ? '—' : Number(toGo).toFixed(2) + ' nm');
        } else {
          if (item.corrected !== null) rank += 1;
          place = item.corrected === null ? '—' : rank;
          last = item.corrected === null ? '—' : hms(item.corrected);
        }
        // Speed over the ground, from the same interpolated position the chart
        // draws the boat at, so the number and the hull agree. A finished boat's
        // last fix is not news, hence the dash.
        const at = (!finished && boat.fixes && boat.fixes.length)
          ? positionAt(boat.fixes, nowTs()) : null;
        const sog = (at && at.sog !== null && at.sog !== undefined)
          ? Number(at.sog).toFixed(1) + ' kn' : '—';
        tr.innerHTML =
          `<td>${place}</td>` +
          `<td>${swatch}${escapeHtml(boat.boat_name)}` +
          `<span class="bar-sail">${escapeHtml(boat.sail_no)}</span></td>` +
          `<td>${rounded === null || rounded === undefined ? '—' : rounded + '/' + total}</td>` +
          `<td>${sog}</td>` +
          `<td>${last}</td>`;
        boardBody.appendChild(tr);
      });
      if (boardNote) {
        const n = (data.boats || []).filter(b => b.fixes && b.fixes.length).length;
        boardNote.textContent = mode.key === 'line'
          ? `${n} boat${n === 1 ? '' : 's'} tracked`
          : 'Estimated while boats are still racing — not a result.';
      }
      if (boardDots) {
        boardDots.innerHTML = boardModes.length < 2 ? '' : boardModes
          .map((m, i) => `<span class="bar-dot${i === boardIx ? ' on' : ''}"></span>`).join('');
      }
    }

    // --- the race clock --------------------------------------------------
    function drawClock() {
      if (!clockTime || startTs === null || !isFinite(startTs)) return;
      // Once the last boat is in, the clock stops at the race's elapsed time.
      // It used to carry on counting, so a race that finished at lunchtime was
      // still ticking up on the bar screen hours later.
      if (raceFinished) {
        clockTime.textContent = lastFinish
          ? hms(lastFinish - startTs) : clockTime.textContent;
        clockState.textContent = 'Finished';
        stage.classList.remove('bar-prestart');
        stage.classList.add('bar-finished');
        return;
      }
      stage.classList.remove('bar-finished');
      // A postponed race keeps its scheduled time until AP comes down, so a clock
      // left counting would show the whole clubhouse a start that is not coming.
      // Queried here rather than closed over: `flagBox` is declared further down
      // this scope, and a clock that ran before it would hit the temporal dead zone.
      const apBox = stage.querySelector('.bar-flags');
      const postponed = apBox && window.SignalFlags && SignalFlags.postponedNow(
        apBox.dataset.postponed, apBox.dataset.postponementEndsAt,
        new Date(nowTs() * 1000));
      if (postponed) {
        clockTime.textContent = '--:--';
        clockState.textContent = window.SignalFlags
          ? SignalFlags.postponedLabel(postponed) : 'Postponed';
        stage.classList.remove('bar-prestart');
        return;
      }
      const delta = nowTs() - startTs;
      clockTime.textContent = (delta < 0 ? '−' : '+') + hms(delta);
      clockState.textContent = delta < 0 ? 'Counting down' : 'Racing';
      stage.classList.toggle('bar-prestart', delta < 0);
    }

    // --- the hoisted flags -----------------------------------------------
    // Which flags are up is the shared rule in static/signal_flags.js, the same one
    // the competitor page calls: the start sequence is not something to have two
    // opinions about. Only the placement is particular to the bar.
    const renderSignature = stage.dataset.renderSignature || '';
    const flagBox = stage.querySelector('.bar-flags');
    const flagSchedule = (function () {
      if (!flagBox) return [];
      try { return JSON.parse(flagBox.dataset.schedule || '[]'); } catch (e) { return []; }
    })();
    const flagSrcs = flagBox ? {
      numeralBase: (flagBox.dataset.numeralBase || '').replace('numeral_0.png', 'numeral_'),
      prep: flagBox.dataset.prepSrc || '',
      codeS: flagBox.dataset.codeSSrc || ''
    } : null;
    let flagMarkupShown = null;

    function drawFlags() {
      if (!flagBox || !window.SignalFlags) return;
      // Both of these must be answers about the moment on screen. A replayed
      // race is over in life -- that is why it can be replayed -- so the
      // server-rendered "finished" and the wall clock between them left this
      // panel empty for the whole replay: the class flag went up and the
      // preparatory flew and came down with nothing on the screen to show it.
      const shownAt = new Date(nowTs() * 1000);
      const finished = replay ? nowTs() >= replayPlan.to_ts
                              : (raceFinished || flagBox.dataset.raceFinished === '1');
      const postponed = finished ? '' : SignalFlags.postponedNow(
        flagBox.dataset.postponed, flagBox.dataset.postponementEndsAt, shownAt);
      let flags = [];
      if (postponed) {
        // AP replaces the sequence rather than joining it: while it is up no warning,
        // preparatory or starting signal is made, so no class flag and no P is flying.
        flags = SignalFlags.postponementFlags(postponed);
      } else if (!finished) {
        flags = SignalFlags.liveFlags(flagSchedule, shownAt);
        if (flagBox.dataset.shortened === '1') {
          flags.push({kind: 'code-s', label: 'Code flag S (shortened course)'});
        }
      }
      const html = SignalFlags.markup(flags, flagSrcs);
      // Only touch the DOM when it changes: this runs every second on a screen that
      // is left on all afternoon, and re-writing identical markup would restart the
      // flag images' decode on some televisions.
      if (html !== flagMarkupShown) {
        flagBox.innerHTML = html;
        flagMarkupShown = html;
      }
      flagBox.classList.toggle('bar-flags-empty', flags.length === 0);
      flagBox.classList.toggle('postponed', !!postponed);
    }

    // --- the camera ------------------------------------------------------
    // Started and stopped rather than left running, so the relay only serves the
    // stream while it is actually on a screen. That is the whole design of the
    // on-demand relay and this page would otherwise hold it open all afternoon.
    function setVideo(on, caption) {
      if (videoCaption) videoCaption.textContent = caption || '';
      if (on === videoOn) return;
      videoOn = on;
      videoPanel.hidden = !on;
      stage.classList.toggle('bar-video-on', on);
      if (!window.PwllheliLiveCamera || !cameraWrap) return;
      if (on) window.PwllheliLiveCamera.start(cameraWrap);
      else window.PwllheliLiveCamera.stop(cameraWrap);
    }

    // --- keeping up ------------------------------------------------------
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

    function adopt(payload) {
      data = payload;
      if (!data.boards) data.boards = {times: [], rows: [], total: 0};
      (data.boats || []).forEach((b, i) => {
        colourOf[b.entry_id] = COLOURS[i % COLOURS.length];
        boatById[b.entry_id] = b;
      });
      fitBoardModes();
      draw();
      drawBoard();
      refit();
    }

    function merge(payload) {
      if (!payload.incremental) { adopt(payload); return; }
      data.end = payload.end;
      (payload.boats || []).forEach(nb => {
        const b = boatById[nb.entry_id];
        if (!b) return;
        if (nb.fixes && nb.fixes.length) {
          const have = b.fixes.length ? b.fixes[b.fixes.length - 1][0] : -Infinity;
          b.fixes = b.fixes.concat(nb.fixes.filter(f => f[0] > have));
        }
      });
      const nb = payload.boards;
      if (nb && nb.times && nb.times.length) {
        data.boards.times = data.boards.times.concat(nb.times);
        data.boards.rows = data.boards.rows.concat(nb.rows);
        data.boards.total = nb.total || data.boards.total;
      }
      draw();
      drawBoard();
    }

    async function pollTrack() {
      try {
        const since = data ? lastFixTime() : null;
        const url = trackUrl + (since !== null ? `?since=${encodeURIComponent(since)}` : '');
        const res = await fetch(url, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
        if (!res.ok) return;
        const payload = await res.json();
        if (!payload || !payload.ok) return;
        if (payload.enabled === false) { setStatus('GPS tracking is switched off.'); return; }
        if (!data) adopt(payload); else merge(payload);
        setStatus('');
      } catch (err) {
        // A dropped poll on a screen nobody is watching is not worth a message;
        // the next one is ten seconds away.
      }
    }

    let recovering = false;

    async function pollState() {
      try {
        const res = await fetch(stateUrl, {headers: {'Accept': 'application/json'},
                                           cache: 'no-store'});
        // The race this page was rendered for has been deleted. Every poll from here
        // on is a 404, and treating that as a transient error left the television
        // showing a race that no longer exists until somebody found a keyboard —
        // reloading picks up whatever race is current now, or says there is none.
        // Once only: if the reload lands on the same 404 there is nothing to gain by
        // asking again every five seconds.
        if (res.status === 404) {
          if (!recovering) { recovering = true; window.location.reload(); }
          return;
        }
        if (!res.ok) return;
        const s = await res.json();
        if (!s || !s.ok) return;

        // A replay owns the screen while it runs. The live camera window, the
        // live finished flag and the follow-the-current-race reload are all
        // statements about *now*, and applying them here would fight the engine
        // -- the camera would cut in over a clip, and the display would jump to
        // today's race halfway through last Saturday's.
        if (replay) {
          if (!s.replay || s.replay.race_id !== raceId) {
            window.location.href = '/bar';       // somebody stopped it
          }
          return;
        }
        // Not replaying: has somebody asked for one?
        if (s.replay && s.replay.race_id) {
          window.location.href = '/bar/' + s.replay.race_id + '?replay=1';
          return;
        }

        setVideo(!!(s.video && s.video.show), s.video && s.video.caption);
        raceFinished = !!s.finished;
        lastFinish = s.last_finish || lastFinish;
        drawClock();
        // Left running for a season: when the club moves on to the next race,
        // follow it rather than showing yesterday's finish for ever.
        if (!pinned && s.current_race_id && s.current_race_id !== s.race_id) {
          window.location.reload();
          return;
        }
        // The course, the start time the clock counts from and the flag schedule are
        // written into this page once, by the server. This is how the television finds
        // out they changed — it used to sit on "Waiting" with the old course until
        // somebody went and reloaded it by hand.
        if (renderSignature && s.render_signature && s.render_signature !== renderSignature) {
          window.location.reload();
        }
      } catch (err) { /* try again in five seconds */ }
    }

    if (replayPlan) {
      replay = makeReplay(replayPlan);
      setStatus('Replay of ' + replayPlan.name);
    }

    pollTrack();
    pollState();
    window.setInterval(pollTrack, TRACK_POLL_MS);
    window.setInterval(cycleBoard, BOARD_CYCLE_MS);
    window.setInterval(refit, FIT_MS);
    window.setInterval(pollState, STATE_POLL_MS);
    // Redraw between polls so the boats move rather than stepping every ten
    // seconds. One second is plenty across a room.
    window.setInterval(() => { draw(); drawClock(); drawFlags(); drawWind(); }, 1000);
    drawClock();
    drawFlags();
    drawWind();
  }

  function start() {
    document.querySelectorAll('[data-bar]').forEach(init);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
