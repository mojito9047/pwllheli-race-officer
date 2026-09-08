/* Copyright © 2026 CapeNet Ltd. All Rights Reserved. */
(function () {
  'use strict';

  const DEFAULT_OPTIONS = {
    defaultWidth: 900,
    defaultHeight: 300,
    minWidth: 360,
    minHeight: 240,
    background: '#020617',
    grid: '#1e293b',
    axis: '#334155',
    text: '#f8fafc',
    muted: '#cbd5e1',
    subtext: '#94a3b8',
    twdLine: '#f8fafc',
    twsLine: '#93c5fd',
    twdScale: '#e2e8f0',
    twsScale: '#bfdbfe',
    // Seven labels give six vertical divisions for each half of the chart.
    scaleTickCount: 7,
    // TWS is drawn as horizontal bars from 0 kt to match common marine displays.
    twsRenderMode: 'bars',
    emptyMessage: 'Waiting for wind history…',
    twdPaddingDeg: 10,
    twsPaddingKt: 1,
    // Keep the TWS scale anchored at zero for race-officer and competitor wind plots.
    twsStartAtZero: true,
    cssHeight: null
  };

  function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function toFiniteNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function norm360(value) {
    const n = toFiniteNumber(value);
    if (n === null) return 0;
    return ((n % 360) + 360) % 360;
  }

  function signedAngleDiff(value, reference) {
    return ((norm360(value) - norm360(reference) + 540) % 360) - 180;
  }

  function formatBearing(value, digits) {
    const n = toFiniteNumber(value);
    if (n === null) return '—';
    return `${norm360(n).toFixed(digits || 0)}°`;
  }

  function normaliseSamples(samples) {
    return (samples || [])
      .map((sample) => ({
        t: toFiniteNumber(sample.t),
        twd: toFiniteNumber(sample.twd),
        tws: toFiniteNumber(sample.tws),
        gust: toFiniteNumber(sample.gust)
      }))
      .filter((sample) => isFiniteNumber(sample.t) && isFiniteNumber(sample.twd) && isFiniteNumber(sample.tws));
  }

  function unwrapTwd(samples) {
    if (!samples.length) return [];
    const reference = norm360(samples[samples.length - 1].twd);
    return samples.map((sample) => ({
      ...sample,
      twdPlot: reference + signedAngleDiff(sample.twd, reference)
    }));
  }

  function roundDown(value, step) {
    return Math.floor(value / step) * step;
  }

  function roundUp(value, step) {
    return Math.ceil(value / step) * step;
  }

  function drawWindHistory(canvas, samples, serverNow, minutes, options) {
    if (!canvas) return;
    const opts = {...DEFAULT_OPTIONS, ...(options || {})};

    // The canvas backing-store size is deliberately separate from its CSS
    // layout size.  Without forcing the CSS height first, some browsers use
    // the backing-store height as the layout height; each redraw then makes the
    // element taller, and the next redraw scales it up again.
    const fixedCssHeight = toFiniteNumber(opts.cssHeight) || opts.defaultHeight;
    canvas.style.display = 'block';
    canvas.style.width = '100%';
    canvas.style.height = `${fixedCssHeight}px`;
    canvas.style.maxHeight = `${fixedCssHeight}px`;
    canvas.style.boxSizing = 'border-box';

    const rect = canvas.getBoundingClientRect();
    const cssWidth = rect.width || canvas.clientWidth || opts.defaultWidth;
    const cssHeight = fixedCssHeight;
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const pixelWidth = Math.max(opts.minWidth, Math.floor(cssWidth * dpr));
    const pixelHeight = Math.max(opts.minHeight, Math.floor(cssHeight * dpr));
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = opts.background;
    ctx.fillRect(0, 0, w, h);

    const top = 32;
    const bottom = 30;
    const left = 38;
    const mid = w / 2;
    const right = w - 18;
    const plotH = Math.max(1, h - top - bottom);
    const now = Number(serverNow || Date.now() / 1000);
    const windowMinutes = Math.max(1, Number(minutes || 10));
    const t0 = now - windowMinutes * 60;
    const visible = normaliseSamples(samples)
      .filter((sample) => sample.t >= t0 && sample.t <= now)
      .sort((a, b) => a.t - b.t);

    ctx.strokeStyle = opts.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i += 1) {
      const y = top + (plotH * i / 5);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
    ctx.strokeStyle = opts.axis;
    ctx.beginPath();
    ctx.moveTo(mid, top);
    ctx.lineTo(mid, h - bottom);
    ctx.stroke();

    ctx.fillStyle = opts.text;
    ctx.font = '14px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('TWD', mid / 2, 20);
    ctx.fillText('TWS kt', mid + (right - mid) / 2, 20);
    ctx.fillStyle = opts.muted;
    ctx.font = '12px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('now', 4, top + 12);
    ctx.fillText(`-${windowMinutes}m`, 4, h - bottom - 2);

    if (!visible.length) {
      ctx.fillStyle = opts.muted;
      ctx.textAlign = 'center';
      ctx.fillText(opts.emptyMessage, w / 2, h / 2);
      return;
    }

    const unwrapped = unwrapTwd(visible);
    const twdValues = unwrapped.map((sample) => sample.twdPlot);
    const twsValues = visible.map((sample) => sample.tws);
    let twdMin = roundDown(Math.min(...twdValues) - opts.twdPaddingDeg, 10);
    let twdMax = roundUp(Math.max(...twdValues) + opts.twdPaddingDeg, 10);
    if (twdMax <= twdMin) twdMax = twdMin + 20;
    let twsMin = opts.twsStartAtZero ? 0 : Math.max(0, Math.floor(Math.min(...twsValues) - opts.twsPaddingKt));
    let twsMax = Math.max(twsMin + 1, Math.ceil(Math.max(...twsValues) + opts.twsPaddingKt));
    if (twsMax <= twsMin) twsMax = twsMin + 1;

    const yFor = (t) => top + ((now - t) / (now - t0 || 1)) * plotH;
    const xTwd = (v) => left + ((v - twdMin) / (twdMax - twdMin || 1)) * (mid - left - 6);
    const xTws = (v) => mid + 8 + ((v - twsMin) / (twsMax - twsMin || 1)) * (right - mid - 8);

    function makeTicks(min, max, count) {
      const tickCount = Math.max(3, Math.floor(Number(count) || 7));
      if (tickCount <= 1) return [min, max];
      const span = max - min;
      if (!Number.isFinite(span) || span === 0) return [min, max];
      const ticks = [];
      for (let i = 0; i < tickCount; i += 1) {
        ticks.push(min + (span * i / (tickCount - 1)));
      }
      return ticks;
    }

    function drawScale(ticks, xFn, labelFn, y, colour) {
      ctx.save();
      ctx.strokeStyle = opts.axis;
      ctx.fillStyle = colour || opts.muted;
      ctx.font = '11px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ticks.forEach((tick) => {
        const x = xFn(tick);
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, h - bottom);
        ctx.stroke();
        ctx.fillText(labelFn(tick), x, y);
      });
      ctx.restore();
    }

    const tickCount = opts.scaleTickCount;
    drawScale(makeTicks(twdMin, twdMax, tickCount), xTwd, (tick) => formatBearing(tick, 0), h - 8, opts.twdScale);
    drawScale(makeTicks(twsMin, twsMax, tickCount), xTws, (tick) => `${Math.round(tick)}`, h - 8, opts.twsScale);

    ctx.fillStyle = opts.subtext;
    ctx.font = '11px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Direction scale', mid / 2, h - 20);
    ctx.fillText('Speed scale', mid + (right - mid) / 2, h - 20);

    function plotLine(items, xFn, key, colour) {
      if (!items.length) return;
      ctx.save();
      ctx.strokeStyle = colour;
      ctx.fillStyle = colour;
      ctx.lineWidth = 2;
      ctx.beginPath();
      items.forEach((sample, index) => {
        const x = xFn(sample[key]);
        const y = yFor(sample.t);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      const last = items[items.length - 1];
      ctx.beginPath();
      ctx.arc(xFn(last[key]), yFor(last.t), 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    function plotHorizontalBars(items, xFn, key, colour, baselineValue) {
      if (!items.length) return;
      const baselineX = xFn(baselineValue);
      const barHeight = Math.max(1, Math.min(6, (plotH / Math.max(items.length, 1)) * 0.9));
      ctx.save();
      ctx.fillStyle = colour;
      items.forEach((sample) => {
        const x = xFn(sample[key]);
        const y = yFor(sample.t);
        const leftX = Math.min(baselineX, x);
        const widthX = Math.max(1, Math.abs(x - baselineX));
        ctx.fillRect(leftX, y - (barHeight / 2), widthX, barHeight);
      });
      ctx.restore();
    }

    plotLine(unwrapped, xTwd, 'twdPlot', opts.twdLine);
    if (opts.twsRenderMode === 'bars') {
      plotHorizontalBars(visible, xTws, 'tws', opts.twsLine, twsMin);
    } else {
      plotLine(visible, xTws, 'tws', opts.twsLine);
    }

    const last = visible[visible.length - 1];
    ctx.fillStyle = opts.text;
    ctx.textAlign = 'center';
    ctx.font = '12px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.fillText(formatBearing(last.twd, 0), mid / 2, 30);
    ctx.fillText(`${last.tws.toFixed(1)} kt`, mid + (right - mid) / 2, 30);
  }

  window.PwllheliWindHistory = {
    draw: drawWindHistory,
    norm360,
    signedAngleDiff,
    formatBearing
  };
})();
