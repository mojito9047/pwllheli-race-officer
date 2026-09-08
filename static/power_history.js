// Hand-rolled Canvas2D chart for the hut power history page (no external
// libraries -- the app's CSP forbids CDNs). Three stacked panels sharing one
// time axis: battery (SOC + voltage), solar watts, and battery current
// (charge/discharge). Polls /api/power/history for a live refresh.
(function () {
  "use strict";
  const canvas = document.getElementById("powerHistoryCanvas");
  if (!canvas || !canvas.getContext) return;
  const screenCtx = canvas.getContext("2d");
  // The chart is drawn once into an offscreen canvas and then blitted, so
  // moving the cursor costs one drawImage instead of re-plotting every sample.
  // At the 30-day range that is 74,000 points per panel: measured at 590 ms a
  // redraw, which made the cursor lag hopelessly behind the pointer.
  const baseCanvas = document.createElement("canvas");
  const baseCtx = baseCanvas.getContext("2d");
  let ctx = screenCtx;              // whichever surface is being drawn on now
  let lastFit = {w: 960, h: 540, dpr: 1};
  const rangeSel = document.getElementById("powerHistoryRange");
  const statusEl = document.getElementById("powerHistoryStatus");
  const emptyEl = document.getElementById("powerHistoryEmpty");

  let minutes = rangeSel ? Number(rangeSel.value) : 360;
  let samples = [];
  let serverNow = Date.now() / 1000;
  let cursorX = null;          // where the pointer is, in CSS pixels
  let scales = {};             // panel geometry + axis ranges, filled by draw()

  const COLORS = {
    grid: "#e3e7ef",
    axis: "#7b8496",
    text: "#4a5262",
    soc: "#1f9d55",
    volts: "#2563eb",
    solar: "#f59e0b",
    solarFill: "rgba(245,158,11,0.18)",
    load: "#7c3aed",
    charge: "#1f9d55",
    discharge: "#dc2626",
    zero: "#aeb6c4",
  };

  function fit() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 960;
    const h = canvas.clientHeight || 540;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    baseCanvas.width = canvas.width;
    baseCanvas.height = canvas.height;
    lastFit = {w, h, dpr};
    return lastFit;
  }

  function niceMax(v) {
    if (!isFinite(v) || v <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * pow;
  }

  // Round numbers a person reads off an axis without doing arithmetic: 5, 10,
  // 25, 50 -- never 13.7 or 41.66. Returns the ticks that fall inside the range.
  function niceTicks(lo, hi, target) {
    if (!(hi > lo)) return [lo];
    const raw = (hi - lo) / (target || 5);
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / pow;
    const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * pow;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) {
      out.push(Math.abs(v) < step * 1e-6 ? 0 : v);
    }
    return out;
  }

  function decimalsFor(step) {
    if (!isFinite(step) || step <= 0) return 0;
    return step >= 1 ? 0 : Math.min(2, Math.ceil(-Math.log10(step)));
  }

  // One horizontal line per tick, labelled on the left. This is most of what
  // made the old chart hard to read: three lines per panel and no numbers
  // between them, so every value had to be estimated against the frame.
  function gridY(x, y, w, h, lo, hi, opts) {
    const o = opts || {};
    const ticks = niceTicks(lo, hi, o.count || 4);
    const dp = decimalsFor(ticks.length > 1 ? ticks[1] - ticks[0] : hi - lo);
    ctx.save();
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const v of ticks) {
      const py = y + h - ((v - lo) / (hi - lo || 1)) * h;
      if (py < y - 0.5 || py > y + h + 0.5) continue;
      ctx.strokeStyle = (o.zeroLine && Math.abs(v) < 1e-9) ? COLORS.zero : COLORS.grid;
      ctx.beginPath();
      ctx.moveTo(x, Math.round(py) + 0.5);
      ctx.lineTo(x + w, Math.round(py) + 0.5);
      ctx.stroke();
      ctx.fillStyle = o.color || COLORS.axis;
      if (o.side === "right") {
        ctx.textAlign = "left";
        ctx.fillText(v.toFixed(dp) + (o.suffix || ""), x + w + 6, py);
      } else {
        ctx.textAlign = "right";
        ctx.fillText(v.toFixed(dp) + (o.suffix || ""), x - 6, py);
      }
    }
    ctx.restore();
    return ticks;
  }

  function timeTicks(tMin, tMax) {
    // Round times, not arbitrary fifths of the window: on the hour, the
    // quarter hour, or the day, depending on how much is on screen.
    const span = tMax - tMin;
    const steps = [60, 300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
                   172800, 604800];
    let step = steps[steps.length - 1];
    for (const candidate of steps) {
      if (span / candidate <= 8) { step = candidate; break; }
    }
    const out = [];
    const offset = new Date().getTimezoneOffset() * 60;   // align to local clock
    let first = Math.ceil((tMin - offset) / step) * step + offset;
    for (let t = first; t <= tMax; t += step) out.push(t);
    return out;
  }

  function values(key) {
    const out = [];
    for (const s of samples) {
      const v = s[key];
      if (v !== null && v !== undefined && !Number.isNaN(Number(v))) out.push(Number(v));
    }
    return out;
  }

  function fmtTimeLabel(t) {
    const d = new Date(t * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (minutes > 1440) {
      return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} ${hh}:${mm}`;
    }
    return `${hh}:${mm}`;
  }

  function drawFrame(x, y, w, h, title) {
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);
    ctx.fillStyle = COLORS.text;
    ctx.font = "600 13px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(title, x + 6, y + 6);
  }

  function xFor(t, tMin, tMax, x, w) {
    if (tMax <= tMin) return x;
    return x + ((t - tMin) / (tMax - tMin)) * w;
  }

  function drawTimeAxis(x, y, w, tMin, tMax) {
    ctx.fillStyle = COLORS.axis;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const t of timeTicks(tMin, tMax)) {
      ctx.fillText(fmtTimeLabel(t), xFor(t, tMin, tMax, x, w), y + 4);
    }
  }

  // The same times, ruled down every panel, so a moment can be followed from
  // the battery to the solar to the current without guessing across the gaps.
  function gridX(x, y, w, h, tMin, tMax) {
    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    for (const t of timeTicks(tMin, tMax)) {
      const px = Math.round(xFor(t, tMin, tMax, x, w)) + 0.5;
      if (px <= x || px >= x + w) continue;
      ctx.beginPath();
      ctx.moveTo(px, y);
      ctx.lineTo(px, y + h);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawLine(key, color, x, y, w, h, tMin, tMax, vMin, vMax, dashed) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if (dashed) ctx.setLineDash([5, 4]);
    let started = false;
    for (const s of samples) {
      const v = s[key];
      if (v === null || v === undefined || Number.isNaN(Number(v))) { started = false; continue; }
      const px = xFor(s.t, tMin, tMax, x, w);
      const py = y + h - ((Number(v) - vMin) / (vMax - vMin || 1)) * h;
      if (!started) { ctx.moveTo(px, py); started = true; } else { ctx.lineTo(px, py); }
    }
    ctx.stroke();
    ctx.restore();
  }

  function yLabel(text, x, y, color) {
    ctx.fillStyle = color;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, y);
  }

  // Nearest sample to a time, by binary search: a month at 35 s is 74,000
  // points and this runs on every mouse move.
  function sampleNear(t) {
    if (!samples.length) return null;
    let lo = 0, hi = samples.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (samples[mid].t < t) lo = mid + 1; else hi = mid;
    }
    const a = samples[Math.max(0, lo - 1)], b = samples[lo];
    return (Math.abs(a.t - t) <= Math.abs(b.t - t)) ? a : b;
  }

  function fmtValue(v, dp, suffix) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(dp) + suffix;
  }

  function panelTop(i) { return scales.top + (scales.panelH + scales.gap) * i; }

  function dot(px, py, color) {
    ctx.beginPath();
    ctx.arc(px, py, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // A line down all three panels at the pointer, a dot on each series where it
  // crosses, and one box listing what every trace reads at that instant. The
  // panels share a time axis, so the whole point is being able to read them
  // together rather than eyeball three separate charts.
  function drawCursor() {
    if (cursorX === null || !samples.length || !scales.w) return;
    const x = scales.x, w = scales.w, ph = scales.panelH;
    if (cursorX < x || cursorX > x + w) return;
    const t = scales.tMin + ((cursorX - x) / w) * (scales.tMax - scales.tMin);
    const s0 = sampleNear(t);
    if (!s0) return;
    const px = Math.round(xFor(s0.t, scales.tMin, scales.tMax, x, w)) + 0.5;
    const bottom = panelTop(2) + ph;

    ctx.save();
    ctx.strokeStyle = COLORS.axis;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(px, scales.top);
    ctx.lineTo(px, bottom);
    ctx.stroke();
    ctx.setLineDash([]);

    const at = (lo, hi, top, v) =>
      top + ph - ((Number(v) - lo) / ((hi - lo) || 1)) * ph;
    if (s0.battery_soc !== null && s0.battery_soc !== undefined) {
      dot(px, at(0, 100, panelTop(0), s0.battery_soc), COLORS.soc);
    }
    if (scales.volts && s0.battery_v !== null && s0.battery_v !== undefined) {
      dot(px, at(scales.volts.lo, scales.volts.hi, panelTop(0), s0.battery_v), COLORS.volts);
    }
    if (scales.watts) {
      if (s0.solar_pv_w !== null && s0.solar_pv_w !== undefined) {
        dot(px, at(scales.watts.lo, scales.watts.hi, panelTop(1), s0.solar_pv_w), COLORS.solar);
      }
      if (s0.load_w !== null && s0.load_w !== undefined) {
        dot(px, at(scales.watts.lo, scales.watts.hi, panelTop(1), s0.load_w), COLORS.load);
      }
    }
    if (scales.amps && s0.battery_i !== null && s0.battery_i !== undefined) {
      dot(px, at(scales.amps.lo, scales.amps.hi, panelTop(2), s0.battery_i),
          Number(s0.battery_i) >= 0 ? COLORS.charge : COLORS.discharge);
    }

    const when = new Date(s0.t * 1000);
    const stamp = String(when.getDate()).padStart(2, "0") + " " +
      ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][when.getMonth()] +
      " " + String(when.getHours()).padStart(2, "0") + ":" +
      String(when.getMinutes()).padStart(2, "0") + ":" +
      String(when.getSeconds()).padStart(2, "0");
    const rows = [
      ["Battery", fmtValue(s0.battery_soc, 1, "%") + "   " + fmtValue(s0.battery_v, 2, " V"), COLORS.soc],
      ["Current", fmtValue(s0.battery_i, 2, " A"), Number(s0.battery_i) >= 0 ? COLORS.charge : COLORS.discharge],
      ["Solar", fmtValue(s0.solar_pv_w, 0, " W"), COLORS.solar],
      ["Load", fmtValue(s0.load_w, 0, " W"), COLORS.load],
    ];
    if (s0.solar_state) rows.push(["State", String(s0.solar_state), COLORS.text]);

    ctx.font = "11px system-ui, sans-serif";
    let boxW = ctx.measureText(stamp).width;
    for (const r of rows) {
      boxW = Math.max(boxW, ctx.measureText(r[0]).width + 12 + ctx.measureText(r[1]).width);
    }
    boxW += 20;
    const boxH = 18 + rows.length * 15 + 8;
    // Flip to the other side of the line rather than run off the canvas.
    let bx = px + 12;
    if (bx + boxW > x + w) bx = px - 12 - boxW;
    const by = Math.min(scales.top + 4, bottom - boxH);

    ctx.fillStyle = "rgba(255,255,255,0.96)";
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(Math.round(bx) + 0.5, Math.round(by) + 0.5, boxW, boxH);
    ctx.fill();
    ctx.stroke();

    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    ctx.fillStyle = COLORS.text;
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.fillText(stamp, bx + 10, by + 6);
    ctx.font = "11px system-ui, sans-serif";
    rows.forEach(function (r, i) {
      const ry = by + 21 + i * 15;
      ctx.fillStyle = COLORS.axis;
      ctx.textAlign = "left";
      ctx.fillText(r[0], bx + 10, ry);
      ctx.fillStyle = r[2];
      ctx.textAlign = "right";
      ctx.fillText(r[1], bx + boxW - 10, ry);
    });
    ctx.restore();
  }

  // Blit the cached chart and put the cursor on top. This is what a pointer
  // move runs, and it does not care how many samples are on screen.
  function paint() {
    const {w, h, dpr} = lastFit;
    screenCtx.setTransform(1, 0, 0, 1, 0, 0);
    screenCtx.clearRect(0, 0, canvas.width, canvas.height);
    screenCtx.drawImage(baseCanvas, 0, 0);
    screenCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx = screenCtx;
    drawCursor();
  }

  function draw() {
    const {w, h, dpr} = fit();
    ctx = baseCtx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const padL = 44, padR = 56, padT = 8, gap = 34;
    const plotW = w - padL - padR;
    const panelH = (h - padT - gap * 2 - 22) / 3;
    const tMax = serverNow;
    const tMin = tMax - minutes * 60;
    // Kept for the crosshair, which has to know where the panels ended up.
    scales = {x: padL, w: plotW, panelH: panelH, top: padT, gap: gap,
              tMin: tMin, tMax: tMax};

    // Panel 1: battery SOC (0-100, left) + voltage (auto, right)
    let y = padT;
    drawFrame(padL, y, plotW, panelH, "Battery — SOC % (green) · Voltage V (blue)");
    gridX(padL, y, plotW, panelH, tMin, tMax);
    gridY(padL, y, plotW, panelH, 0, 100, {count: 5, suffix: "%", color: COLORS.soc});
    drawLine("battery_soc", COLORS.soc, padL, y, plotW, panelH, tMin, tMax, 0, 100, false);
    const vv = values("battery_v");
    if (vv.length) {
      let vMin = Math.min.apply(null, vv), vMax = Math.max.apply(null, vv);
      const padv = Math.max(0.2, (vMax - vMin) * 0.2);
      vMin -= padv; vMax += padv;
      // Ticks rather than just the two extremes: a battery lives in a narrow
      // band and the interesting question is always "how far off 13.8 is it".
      gridY(padL, y, plotW, panelH, vMin, vMax,
            {count: 4, side: "right", suffix: "V", color: COLORS.volts});
      drawLine("battery_v", COLORS.volts, padL, y, plotW, panelH, tMin, tMax, vMin, vMax, true);
      scales.volts = {lo: vMin, hi: vMax};
    }

    // Panel 2: solar watts (filled area) with hut consumption overlaid
    y = padT + panelH + gap;
    drawFrame(padL, y, plotW, panelH, "Power (W) — solar in (gold) · hut load (purple)");
    const sv = values("solar_pv_w");
    const lv = values("load_w");
    const sMax = niceMax(Math.max(sv.length ? Math.max.apply(null, sv) : 1, lv.length ? Math.max.apply(null, lv) : 1));
    gridX(padL, y, plotW, panelH, tMin, tMax);
    gridY(padL, y, plotW, panelH, 0, sMax, {count: 4, suffix: " W"});
    scales.watts = {lo: 0, hi: sMax};
    // filled area under the solar line
    ctx.save();
    ctx.beginPath(); ctx.rect(padL, y, plotW, panelH); ctx.clip();
    ctx.beginPath();
    let started = false, lastX = padL;
    for (const s of samples) {
      const v = s.solar_pv_w;
      if (v === null || v === undefined || Number.isNaN(Number(v))) continue;
      const px = xFor(s.t, tMin, tMax, padL, plotW);
      const py = y + panelH - (Number(v) / sMax) * panelH;
      if (!started) { ctx.moveTo(px, y + panelH); ctx.lineTo(px, py); started = true; }
      else ctx.lineTo(px, py);
      lastX = px;
    }
    if (started) {
      ctx.lineTo(lastX, y + panelH);
      ctx.closePath();
      ctx.fillStyle = COLORS.solarFill;
      ctx.fill();
    }
    ctx.restore();
    drawLine("solar_pv_w", COLORS.solar, padL, y, plotW, panelH, tMin, tMax, 0, sMax, false);
    drawLine("load_w", COLORS.load, padL, y, plotW, panelH, tMin, tMax, 0, sMax, false);

    // Panel 3: battery current, symmetric around zero (charge green / discharge red)
    y = padT + (panelH + gap) * 2;
    drawFrame(padL, y, plotW, panelH, "Battery current A  (+ charging / − discharging)");
    const iv = values("battery_i");
    const iMax = niceMax(iv.length ? Math.max.apply(null, iv.map(Math.abs)) : 1);
    gridX(padL, y, plotW, panelH, tMin, tMax);
    gridY(padL, y, plotW, panelH, -iMax, iMax, {count: 4, suffix: " A", zeroLine: true});
    scales.amps = {lo: -iMax, hi: iMax};
    const zeroY = y + panelH / 2;
    // per-segment colouring by sign
    ctx.save();
    ctx.beginPath(); ctx.rect(padL, y, plotW, panelH); ctx.clip();
    ctx.lineWidth = 2;
    let prev = null;
    for (const s of samples) {
      const v = s.battery_i;
      if (v === null || v === undefined || Number.isNaN(Number(v))) { prev = null; continue; }
      const px = xFor(s.t, tMin, tMax, padL, plotW);
      const py = zeroY - (Number(v) / iMax) * (panelH / 2);
      if (prev) {
        ctx.strokeStyle = Number(v) >= 0 ? COLORS.charge : COLORS.discharge;
        ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(px, py); ctx.stroke();
      }
      prev = {x: px, y: py};
    }
    ctx.restore();

    // shared time axis at the very bottom
    drawTimeAxis(padL, padT + (panelH + gap) * 2 + panelH + 2, plotW, tMin, tMax);
    ctx = screenCtx;
    paint();

    if (emptyEl) emptyEl.hidden = samples.length > 0;
  }

  async function refresh() {
    try {
      const res = await fetch(`/api/power/history?minutes=${minutes}`, {headers: {"Accept": "application/json"}, cache: "no-store"});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      samples = Array.isArray(data.samples) ? data.samples : [];
      if (data.server_now) serverNow = data.server_now;
      draw();
    } catch (err) {
      if (statusEl) statusEl.textContent = "Power history unavailable.";
    }
  }

  if (rangeSel) {
    rangeSel.addEventListener("change", function () {
      minutes = Number(rangeSel.value) || 360;
      refresh();
    });
  }
  // Pointer events rather than mouse, so a finger on a tablet works too: the
  // hut PC is a desktop but the race office is not the only place this is read.
  function pointerAt(ev) {
    const rect = canvas.getBoundingClientRect();
    cursorX = ev.clientX - rect.left;
    paint();                       // not draw(): the chart underneath is unchanged
  }
  canvas.addEventListener("pointermove", pointerAt);
  canvas.addEventListener("pointerdown", pointerAt);
  canvas.addEventListener("pointerleave", function () { cursorX = null; paint(); });
  canvas.style.cursor = "crosshair";

  window.addEventListener("resize", draw);
  refresh();
  window.setInterval(refresh, 10000);
})();
