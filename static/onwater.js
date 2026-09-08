/* The on-the-water command page.
 *
 * Sends what was typed, shows what came back, and asks for a yes before
 * anything happens. It holds one piece of state -- the pending token from the
 * last read-back -- and forgets it the moment the command is answered.
 *
 * Two things it does for the connection rather than for the interface. Every
 * command carries a client_command_id, so a send that times out and is retried
 * is the same command rather than a second one; and the Send button stays
 * disabled until a reply arrives, because the failure mode on a boat is
 * pressing it again.
 */
(function () {
  "use strict";

  const OnWater = {};

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function newCommandId() {
    // Not security-sensitive: it only has to be different from the last one.
    return "ow-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
  }

  OnWater.start = function (config) {
    const thread = document.getElementById("thread");
    const answer = document.getElementById("answer");
    const form = document.getElementById("sayForm");
    const input = document.getElementById("sayText");
    const btnSay = document.getElementById("btnSay");
    const btnYes = document.getElementById("btnYes");
    const btnNo = document.getElementById("btnNo");
    let pendingToken = null;
    let busy = false;

    function show(kind, text, extraClass) {
      const box = el("div", "ow-msg " + kind + (extraClass ? " " + extraClass : ""));
      box.appendChild(el("p", null, text));
      thread.appendChild(box);
      box.scrollIntoView({block: "end", behavior: "smooth"});
      return box;
    }

    function setBusy(state) {
      busy = state;
      btnSay.disabled = state;
      btnYes.disabled = state;
      btnNo.disabled = state;
      btnSay.textContent = state ? "…" : "Send";
    }

    function awaitAnswer(token) {
      pendingToken = token;
      answer.hidden = !token;
    }

    async function post(url, body) {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Same protection as every other state-changing call in the app.
          "X-CSRF-Token": window.RO_CSRF_TOKEN || ""
        },
        body: JSON.stringify(body)
      });
      const data = await resp.json().catch(function () { return {}; });
      // 502/503/504 come from Cloudflare, not from the app: the request never
      // reached a handler. Saying "the hut did not answer" invited the reading
      // that it was thinking about it — a 504 arriving in five seconds, when the
      // edge waits a hundred, means the tunnel could not reach the hut at all.
      // Which half of the call it was decides what to do about it, so the caller
      // is told rather than guessing.
      if (!resp.ok) data.unreachable = resp.status >= 502 && resp.status <= 504;
      if (!resp.ok && !data.error) {
        data.error = data.unreachable
          ? "Could not reach the hut (" + resp.status + ")."
          : "The hut refused that (" + resp.status + ").";
      }
      return data;
    }

    // The strip at the top: how long to the gun, and what course the fleet is
    // on. Same formatting as every other countdown in the app (countdown.js),
    // because a race officer reading two of them at once should not have to
    // work out whether they mean the same thing.
    const clock = document.getElementById("owCountdown");
    const stateEl = document.getElementById("owState");

    function tick() {
      if (!clock) return;
      if (clock.dataset.raceFinished === "1") {
        clock.textContent = "Finished";
        if (stateEl) stateEl.textContent = "Race finished";
        return;
      }
      // Under AP the race keeps its scheduled time. Asked every tick rather
      // than once: the flag comes down at a minute the race officer chose, and
      // this page is not reloaded when it does.
      const postponed = window.SignalFlags
        ? SignalFlags.postponedNow(clock.dataset.postponed, clock.dataset.postponementEndsAt)
        : "";
      if (postponed) {
        clock.textContent = "--:--";
        clock.classList.remove("ow-imminent");
        if (stateEl) stateEl.textContent = SignalFlags.postponedLabel(postponed);
        return;
      }
      const start = new Date(clock.dataset.start || "");
      if (Number.isNaN(start.getTime())) {
        clock.textContent = "--:--";
        if (stateEl) stateEl.textContent = "No start time";
        return;
      }
      const diff = Math.round((start - new Date()) / 1000);
      clock.textContent = (diff < 0 ? "+" : "-") + window.roCountdownBody(Math.abs(diff));
      clock.classList.toggle("ow-imminent", diff > 0 && diff <= 300);
      if (stateEl) stateEl.textContent = diff > 0 ? "Counting down" : "Racing";
    }

    function applyHeader(header) {
      if (!header) return;
      const put = function (id, text) {
        const node = document.getElementById(id);
        if (node) node.textContent = text || "";
      };
      put("owRace", header.race_name);
      put("owCourse", header.course_text);
      put("owWind", header.wind_text);
      if (clock) {
        clock.dataset.start = header.first_gun || "";
        clock.dataset.raceFinished = header.finished ? "1" : "0";
        // Postponing from the water has to move this strip, for the same reason
        // setting a start time does: it is the reason the command was given.
        clock.dataset.postponed = header.postponed_flag || "";
        clock.dataset.postponementEndsAt = header.postponement_ends_at || "";
      }
      // The board and the chart are of a course, so they have to change when the
      // course does. The strip said "course 17" above a picture of course 16.
      const board = document.getElementById("owBoard");
      if (board && header.course_board) {
        board.textContent = "";
        header.course_board.forEach(function (m) {
          board.appendChild(el("span", "mark " + (m.rounding || ""), m.mark + (m.rounding || "")));
        });
      }
      const map = document.querySelector("#owChart .course-map");
      if (map && header.course_marks) {
        if (header.wind_direction !== undefined && header.wind_direction !== "") {
          map.dataset.windDirection = String(header.wind_direction);
        }
        // render() stores the new marks on the element, so a chart opened later
        // draws the course the race is on now rather than the one it started with.
        if (window.RaceCourseMap) window.RaceCourseMap.render(map, header.course_marks);
      }
      tick();
    }

    // Drawn when it is opened and not before: a closed <details> has no width,
    // so a chart rendered into it comes out the wrong size, and on 4G a chart
    // nobody asked for is bandwidth taken from the commands.
    const chart = document.getElementById("owChart");
    if (chart) {
      chart.addEventListener("toggle", function () {
        if (chart.open && window.RaceCourseMap) window.RaceCourseMap.initAll(chart);
      });
    }

    function offerOptions(box, options) {
      if (!options || !options.length) return;
      const row = el("div", "ow-options");
      options.forEach(function (option) {
        const button = el("button", null, option);
        button.type = "button";
        button.addEventListener("click", function () {
          if (busy) return;
          // Answered once. Tapping "standard" again half a minute later would
          // be a bare word with nothing left to answer.
          row.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
          input.value = option;
          form.requestSubmit();
        });
        row.appendChild(button);
      });
      box.appendChild(row);
    }

    function render(data) {
      // Whatever else came back, the top of the page is now out of date.
      applyHeader(data.header);
      if (data.error) {
        show("ow-app", data.error, "ow-problem");
        awaitAnswer(data.keepPending || null);
        return;
      }
      if (data.status === "needs_confirmation") {
        // Warnings belong on the read-back, not in the response nobody sees:
        // "that race has finished" is the whole reason to press No.
        const warnings = (data.warnings || []).join(" ");
        show("ow-app", data.readback + (warnings ? " " + warnings : ""), "ow-ask");
        awaitAnswer(data.pending_token);
        return;
      }
      if (data.status === "answered") {
        // Words, not an action: there is nothing to confirm and nothing was done.
        show("ow-app", data.answer);
        awaitAnswer(null);
        return;
      }
      if (data.status === "needs_clarification" || data.status === "not_understood") {
        // A question with a choice in it is answered with one tap. The app
        // hears the reply either way, but "standard" is a lot to type with one
        // hand on a tiller.
        offerOptions(show("ow-app", data.question, "ow-ask"), data.options);
        awaitAnswer(null);
        return;
      }
      if (data.status === "expired") {
        show("ow-app", data.question, "ow-problem");
        awaitAnswer(null);
        return;
      }
      if (data.status === "dismissed") {
        show("ow-app", "Left alone — nothing was changed.");
        awaitAnswer(null);
        return;
      }
      show("ow-app", data.message || "Done.", "ow-done");
      awaitAnswer(null);
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const text = (input.value || "").trim();
      if (!text || busy) return;
      show("ow-you", text);
      input.value = "";
      setBusy(true);
      try {
        const answer = await post(config.commandUrl, {text: text, client_command_id: newCommandId()});
        if (answer.unreachable) {
          // Interpreting changes nothing, so a request that never arrived
          // changed nothing either. That is worth saying: the alternative is
          // wondering whether half a race was created.
          answer.error += " Nothing was carried out — say it again.";
        }
        render(answer);
      } catch (err) {
        // Said plainly: on the water, "did that land?" is the only question
        // that matters, and a silent failure is the worst possible answer.
        show("ow-app", "That did not reach the hut. Check signal and say it again.", "ow-problem");
      } finally {
        setBusy(false);
      }
    });

    async function answerWith(confirm) {
      if (!pendingToken || busy) return;
      const token = pendingToken;
      awaitAnswer(null);
      show("ow-you", confirm ? "Yes" : "No");
      setBusy(true);
      try {
        const answer = await post(config.confirmUrl, {pending_token: token, confirm: confirm});
        if (answer.unreachable) {
          // The one thing worth knowing here: pressing Yes again is safe. The
          // command carries a token and a token is carried out once, so a retry
          // either does it or reports what it already did.
          answer.error += " It may or may not have been carried out — press Yes again, "
                        + "which cannot do it twice.";
          // Put the token back, or the button vanishes at exactly the moment
          // somebody is being told to press it.
          answer.keepPending = token;
        }
        render(answer);
      } catch (err) {
        show("ow-app", "That did not reach the hut — it may or may not have been done. " +
                       "Say 'status' to check.", "ow-problem");
      } finally {
        setBusy(false);
      }
    }

    btnYes.addEventListener("click", function () { answerWith(true); });
    btnNo.addEventListener("click", function () { answerWith(false); });

    document.querySelectorAll(".onwater-quick button").forEach(function (button) {
      button.addEventListener("click", function () {
        const said = button.getAttribute("data-say") || "";
        if (said.endsWith(" ")) {          // a half-typed command to finish
          input.value = said;
          input.focus();
        } else {
          input.value = said;
          form.requestSubmit();
        }
      });
    });

    tick();
    setInterval(tick, 1000);
    // A proposal that was waiting when the page reloaded is still waiting: the
    // server never forgot it, and the buttons come back live rather than
    // leaving a command on the server that nothing on screen can agree to.
    if (config.pendingToken) awaitAnswer(config.pendingToken);
    thread.scrollTop = thread.scrollHeight;
    input.focus();
  };

  window.OnWater = OnWater;
})();
