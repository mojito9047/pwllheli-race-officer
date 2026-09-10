// Live-updates the dashboard "3D replay" card from /api/replay3d/status.
// Mirrors dashboard_power.js: poll every 5s, degrade quietly on error.
//
// The thing this card is for is not progress. It is whether there is a render
// machine at all. A job queued against a box that is switched off looks exactly
// like one being worked on, and without this you find out hours later when the
// film never appears. The film itself is reached from the race that made it.
(function () {
  "use strict";
  const card = document.getElementById("replay3dCard");
  if (!card) return;

  const els = {
    pill: document.getElementById("replay3dPill"),
    message: document.getElementById("replay3dMessage"),
    renderer: document.getElementById("replay3dRenderer"),
    film: document.getElementById("replay3dFilm"),
  };

  function apply(data) {
    // Nothing configured means the club is not using this at all, so the card
    // has nothing to say and should not take up room on a race day.
    card.hidden = !data.configured;
    if (!data.configured) return;

    if (els.message) els.message.textContent = data.message || "";
    if (els.pill) els.pill.hidden = !!data.ok;

    // The link carries a version token, because the film's key is only the
    // race id and it is served with a day of cache: without the token a
    // re-render sits behind the old copy and this link hands out yesterday's.
    if (els.film) {
      const url = data.status && data.status.film_url;
      els.film.hidden = !url;
      if (url) els.film.href = url;
    }

    // Only when it adds something. When there is no renderer the message
    // above already says so, and saying it twice reads as a stutter.
    if (els.renderer) {
      const busy = data.heartbeat && data.heartbeat.busy_with;
      els.renderer.textContent = !data.renderer_up ? ""
        : busy ? "Render machine working on race " + busy + "."
        : "Render machine ready.";
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/replay3d/status", {
        headers: {"Accept": "application/json"},
        cache: "no-store",
      });
      if (!res.ok) return;
      apply(await res.json());
    } catch (err) {
      // A dashboard that goes blank because the bucket hiccuped is worse than
      // one showing a value a few seconds old. Leave what is on screen.
    }
  }

  refresh();
  window.setInterval(refresh, 5000);
})();
