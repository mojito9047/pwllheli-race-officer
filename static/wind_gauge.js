// Copyright © 2026 CapeNet Ltd. All Rights Reserved.
// Analog wind-speed and direction instrument, shared by the race-office
// dashboard and the public competitor home page.
//
// Every element with class .wind-gauge is driven. Needles and the readouts
// inside the dial are found by class within the gauge, so more than one gauge
// can live on a page. Optional companion text elements (a status line, a source
// line, plain-text TWD/TWS) are named by element id in data attributes:
//
//   <div class="wind-gauge" data-twd="212" data-tws="11.4"
//        data-source="Weather station polling"
//        data-status-target="..." data-source-target="..."
//        data-twd-target="..." data-tws-target="...">
//
// One poller serves every gauge: /api/weather/current every 5 seconds (a public,
// read-only endpoint). The server-rendered data-twd/data-tws are applied first,
// so the needles are never blank while that first fetch is in flight.
(function () {
  const roots = Array.from(document.querySelectorAll('.wind-gauge'));
  if (!roots.length) return;

  const NS = 'http://www.w3.org/2000/svg';
  const CX = 120;
  const CY = 120;
  const MAX_SPEED = 30;
  const REFRESH_MS = 5000;

  function n(tag, attrs, text) {
    const el = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => el.setAttribute(key, String(value)));
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function polar(radius, bearingDeg) {
    const rad = bearingDeg * Math.PI / 180;
    return {
      x: CX + radius * Math.sin(rad),
      y: CY - radius * Math.cos(rad)
    };
  }

  function speedAngle(knots) {
    const k = Math.max(0, Math.min(MAX_SPEED, Number(knots) || 0));
    return -140 + (k / MAX_SPEED) * 280;
  }

  function arcPath(radius, startBearing, endBearing) {
    const a = polar(radius, startBearing);
    const b = polar(radius, endBearing);
    const diff = Math.abs(endBearing - startBearing);
    const large = diff > 180 ? 1 : 0;
    const sweep = endBearing > startBearing ? 1 : 0;
    return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} A ${radius} ${radius} 0 ${large} ${sweep} ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
  }

  function drawScale(svg) {
    const directionLayer = svg.querySelector('.direction-ticks');
    const speedLayer = svg.querySelector('.speed-ticks');
    const bandLayer = svg.querySelector('.speed-band');
    if (!directionLayer || !speedLayer || !bandLayer) return;
    directionLayer.innerHTML = '';
    speedLayer.innerHTML = '';
    bandLayer.innerHTML = '';

    [
      [-140, -56, 'speed-band-low'],
      [-56, 56, 'speed-band-mid'],
      [56, 140, 'speed-band-high']
    ].forEach(([from, to, cls]) => {
      bandLayer.appendChild(n('path', {d: arcPath(103, from, to), class: cls}));
    });

    // Cardinal letters, not twelve three-digit numbers.
    //
    // The numbers were the reason the middle of this dial was unreadable: at
    // radius 56 the 150/180/210 labels land exactly where the readouts sit, so
    // "108°" and "2.5 kt" were rendered through "210", "180" and "150" — the
    // reported "2102.5 kt150". Four letters free the whole centre, and read far
    // better from across a bar than a ring of digits. The exact bearing has never
    // been on the ring anyway; it is the big readout underneath.
    const CARDINALS = {0: 'N', 90: 'E', 180: 'S', 270: 'W'};
    for (let deg = 0; deg < 360; deg += 10) {
      const major = deg % 30 === 0;
      const p1 = polar(81, deg);
      // Ticks kept short (81 down to 73/77) so the cardinal letters can sit
      // outside the centre rather than inside it. That is what buys the room
      // for the two readouts, which is where the crowding was.
      const p2 = polar(major ? 73 : 77, deg);
      directionLayer.appendChild(n('line', {
        x1: p1.x.toFixed(2), y1: p1.y.toFixed(2),
        x2: p2.x.toFixed(2), y2: p2.y.toFixed(2),
        class: major ? 'direction-major-tick' : 'direction-minor-tick'
      }));
      if (CARDINALS[deg]) {
        const label = polar(64, deg);
        directionLayer.appendChild(n('text', {
          x: label.x.toFixed(2), y: label.y.toFixed(2),
          class: 'direction-label',
          'text-anchor': 'middle',
          'dominant-baseline': 'middle'
        }, CARDINALS[deg]));
      }
    }

    for (let kt = 0; kt <= MAX_SPEED; kt += 1.5) {
      const major = Math.abs(kt % 3) < 0.01;
      const bearing = speedAngle(kt);
      const p1 = polar(104, bearing);
      const p2 = polar(major ? 95 : 99, bearing);
      speedLayer.appendChild(n('line', {
        x1: p1.x.toFixed(2), y1: p1.y.toFixed(2),
        x2: p2.x.toFixed(2), y2: p2.y.toFixed(2),
        class: major ? 'speed-major-tick' : 'speed-minor-tick'
      }));
      if (major) {
        const label = polar(114, bearing);
        speedLayer.appendChild(n('text', {
          x: label.x.toFixed(2), y: label.y.toFixed(2),
          class: 'speed-label',
          'text-anchor': 'middle',
          'dominant-baseline': 'middle'
        }, String(kt)));
      }
    }
  }

  function normaliseBearing(value) {
    if (!Number.isFinite(value)) return null;
    return ((value % 360) + 360) % 360;
  }

  function byId(id) { return id ? document.getElementById(id) : null; }

  // One gauge: the SVG parts it owns, plus whichever companion text it names.
  function prepare(root) {
    const svg = root.querySelector('svg.wind-dial');
    if (!svg) return null;
    const gauge = {
      root: root,
      svg: svg,
      directionNeedle: svg.querySelector('.direction-needle'),
      speedNeedle: svg.querySelector('.speed-needle'),
      twdValue: svg.querySelector('.wind-readout.twd'),
      twsValue: svg.querySelector('.wind-readout.tws'),
      gustMarker: svg.querySelector('.gust-marker'),
      twdText: byId(root.dataset.twdTarget),
      twsText: byId(root.dataset.twsTarget),
      statusText: byId(root.dataset.statusTarget),
      sourceText: byId(root.dataset.sourceTarget)
    };
    if (!gauge.directionNeedle || !gauge.speedNeedle) return null;
    drawScale(svg);
    return gauge;
  }

  function setInstrument(gauge, twd, tws, message, source, gust) {
    const dir = normaliseBearing(Number(twd));
    const speed = Number(tws);
    const hasDir = dir !== null;
    const hasSpeed = Number.isFinite(speed);

    gauge.directionNeedle.style.display = hasDir ? '' : 'none';
    gauge.directionNeedle.setAttribute('transform', hasDir ? `rotate(${dir.toFixed(1)} ${CX} ${CY})` : '');
    gauge.speedNeedle.style.display = hasSpeed ? '' : 'none';
    gauge.speedNeedle.setAttribute('transform', hasSpeed ? `rotate(${speedAngle(speed).toFixed(1)} ${CX} ${CY})` : '');

    const dirLabel = hasDir ? `${Math.round(dir)}°` : '—°';
    const speedLabel = hasSpeed ? `${speed.toFixed(1)} kt` : '— kt';
    if (gauge.twdValue) gauge.twdValue.textContent = dirLabel;
    if (gauge.twsValue) gauge.twsValue.textContent = speedLabel;
    // The gust rides the speed scale as a marker rather than a caption: it is a
    // reading of the same quantity as the speed needle, so it belongs on the same
    // scale. Shown only when it is actually gusting — a gust equal to the mean is
    // not a gust, and a marker sitting under the needle says nothing.
    if (gauge.gustMarker) {
      const g = Number(gust);
      const worth = Number.isFinite(g) && hasSpeed && g > speed + 0.5;
      gauge.gustMarker.style.display = worth ? '' : 'none';
      if (worth) {
        gauge.gustMarker.setAttribute('transform', `rotate(${speedAngle(g).toFixed(1)} ${CX} ${CY})`);
      }
    }
    if (gauge.twdText) gauge.twdText.textContent = hasDir ? `${Math.round(dir)}°T` : '—';
    if (gauge.twsText) gauge.twsText.textContent = hasSpeed ? `${speed.toFixed(1)} kt` : '—';
    if (gauge.statusText && message) gauge.statusText.textContent = message;
    if (gauge.sourceText && source) gauge.sourceText.textContent = source;
    gauge.svg.setAttribute('aria-label', hasDir || hasSpeed
      ? `Wind ${hasDir ? Math.round(dir) + ' degrees true' : 'direction unknown'}, ${hasSpeed ? speed.toFixed(1) + ' knots' : 'speed unknown'}`
        + (Number.isFinite(Number(gust)) && hasSpeed && Number(gust) > speed + 0.5
           ? `, gusting ${Number(gust).toFixed(0)} knots` : '')
      : 'Wind unavailable');
  }

  // Beyond this a stored reading is not the wind at the moment being shown.
  const WIND_STALE_S = 600;

  // The wind at a moment from a recorded series ([t, twd, tws, gust] rows): the
  // last reading at or before it. Not interpolated -- wind is a measurement
  // every few seconds, not a track, and averaging two readings across a shift
  // would invent a direction nobody recorded. Shared, so a replay on the bar
  // television and one on the race page cannot disagree about what the wind was
  // doing at the same moment of the same race.
  function readingAt(series, t) {
    if (!series || !series.length) return null;
    if (series[0][0] > t) return null;
    let lo = 0, hi = series.length - 1, best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (series[mid][0] <= t) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    if (best < 0) return null;
    // A reading far older than the moment shown is not the wind at that moment.
    if (t - series[best][0] > WIND_STALE_S) return null;
    return series[best];
  }

  const all = roots.map(prepare).filter(Boolean);
  // A gauge driven by something else — the chart's replay clock — must not be
  // overwritten by the live poll, or a replay of last month's race would show
  // this afternoon's wind a few seconds after you scrubbed to it.
  const gauges = all.filter(g => g.root.dataset.manual !== '1');
  window.WindGauge = {prepare: prepare, setInstrument: setInstrument,
                     readingAt: readingAt};
  if (!gauges.length) return;

  function applyApiPayload(data) {
    const current = data && data.current ? data.current : {};
    const config = data && data.config ? data.config : {};
    const source = config.source === 'manual' ? 'Manual input' : 'Weather station polling';
    gauges.forEach(gauge => setInstrument(gauge, current.twd, current.tws, data && data.message, source));
  }

  async function refresh() {
    try {
      const res = await fetch('/api/weather/current', {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      applyApiPayload(await res.json());
    } catch (err) {
      gauges.forEach(gauge => {
        if (gauge.statusText) gauge.statusText.textContent = 'Wind update unavailable.';
      });
    }
  }

  gauges.forEach(gauge => setInstrument(
    gauge,
    gauge.root.dataset.twd,
    gauge.root.dataset.tws,
    gauge.statusText ? gauge.statusText.textContent : '',
    gauge.root.dataset.source || ''
  ));
  refresh();
  window.setInterval(refresh, REFRESH_MS);
})();
