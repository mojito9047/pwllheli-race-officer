// Live-updates the dashboard "Hut power" card from /api/power/status.
// Mirrors dashboard_wind.js: poll every 5s, degrade quietly on error.
(function () {
  "use strict";
  const card = document.getElementById("powerCard");
  if (!card) return;

  const els = {
    message: document.getElementById("powerMessage"),
    soc: document.getElementById("powerSoc"),
    volts: document.getElementById("powerVolts"),
    amps: document.getElementById("powerAmps"),
    solar: document.getElementById("powerSolar"),
    load: document.getElementById("powerLoad"),
    charger: document.getElementById("powerCharger"),
  };

  function fmt(value, digits, suffix, opts) {
    opts = opts || {};
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    const sign = opts.sign && n >= 0 ? "+" : "";
    return sign + n.toFixed(digits) + (suffix || "");
  }

  function apply(data) {
    const battery = data.battery || {};
    const solar = data.solar || {};
    const charger = data.charger || {};
    if (els.message) els.message.textContent = data.message || "";
    if (els.soc) els.soc.textContent = fmt(battery.soc, 0, "%");
    if (els.volts) els.volts.textContent = fmt(battery.v, 2, " V");
    if (els.amps) els.amps.textContent = fmt(battery.i, 1, " A", {sign: true});
    if (els.solar) {
      const w = fmt(solar.pv_w, 0, " W");
      els.solar.textContent = w === "—" ? "—" : (solar.state ? `${w} (${solar.state})` : w);
    }
    if (els.load) els.load.textContent = fmt((data.load || {}).w, 0, " W");
    if (els.charger) els.charger.textContent = charger.state || "—";
  }

  async function refresh() {
    try {
      const res = await fetch("/api/power/status", {headers: {"Accept": "application/json"}, cache: "no-store"});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      apply(await res.json());
    } catch (err) {
      if (els.message) els.message.textContent = "Power update unavailable.";
    }
  }

  refresh();
  window.setInterval(refresh, 5000);
})();
