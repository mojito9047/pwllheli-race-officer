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

  // A reply as the interpreter wrote it: lines, numbered and bulleted lists, and
  // **bold**. Nothing else is read as formatting. Built from elements and text
  // nodes only -- never innerHTML -- so whatever a reply contains, it can only
  // ever be words on the page.
  //
  // Before this the whole reply was one paragraph: three suggested courses came
  // out as "...at O: 1. **9p Fp 10p Op** – 5.12 nm ... 2. **4p Fp..." on a phone,
  // asterisks and all.
  function inline(parent, text) {
    const parts = String(text).split(/\*\*(.+?)\*\*/);
    parts.forEach(function (part, i) {
      if (!part) return;
      // Every odd part was inside a pair of asterisks. A lone pair left over is
      // noise, not emphasis, and is dropped rather than shown.
      if (i % 2) parent.appendChild(el("strong", null, part));
      else parent.appendChild(document.createTextNode(part.replace(/\*\*/g, "")));
    });
    return parent;
  }

  function formatted(text) {
    const box = document.createDocumentFragment();
    let list = null;
    let listKind = "";
    let item = null;
    String(text || "").split(/\r?\n/).forEach(function (raw) {
      const line = raw.trim();
      // Indented under a list item, it belongs to that item on a line of its
      // own: a suggested course with its wind angles beneath it, "   TWA: O-F
      // 44° P, ...", reads as one option rather than an option and a stray line.
      if (line && item && /^\s{2,}/.test(raw)) {
        item.appendChild(el("br"));
        inline(item, line.replace(/^[-•*]\s+/, ""));
        return;
      }
      item = null;
      const numbered = line.match(/^(\d+)[.)]\s+(.*)$/);
      const bullet = line.match(/^[-•*]\s+(.*)$/);
      const kind = numbered ? "ol" : bullet ? "ul" : "";
      if (!line || !kind) list = null;
      if (!line) return;
      if (kind) {
        if (!list || listKind !== kind) {
          list = el(kind, "ow-list");
          if (numbered && numbered[1] !== "1") list.start = parseInt(numbered[1], 10);
          listKind = kind;
          box.appendChild(list);
        }
        item = inline(el("li"), numbered ? numbered[2] : bullet[1]);
        list.appendChild(item);
        return;
      }
      box.appendChild(inline(el("p"), line));
    });
    return box;
  }
  OnWater.formatted = formatted;

  // A course the app suggested, timed or is about to set, drawn rather than
  // described: the board as chips, its length and time, and a row a leg with the
  // wind angle and tack. The same angles in a sentence were "TWA: O-F 44° P, F-8
  // 157° S, 8-F 23°" wrapping mid-figure on a phone.
  // What to say to choose a card: the same words a race officer would type,
  // so choosing goes through the same read-back and Yes as typing it.
  function sayToChoose(card) {
    if (card.title) return "Use " + card.title.toLowerCase();
    const board = (card.board || []).map(function (m) { return m.mark + (m.rounding || "p"); });
    return "Set the made-up course " + board.join(" ");
  }

  // `number` is its place among the cards in one reply, 1, 2, 3 -- the same order
  // the options are given in the words. Three courses with nothing to call them
  // by left "which one?" unanswerable on a phone. `use`, when given, is what the
  // "Use this" button calls.
  function courseCard(card, number, use) {
    const box = el("div", "ow-card");
    const head = el("div", "ow-card-head");
    if (number) head.appendChild(el("span", "ow-card-number", String(number)));
    if (card.title) head.appendChild(el("span", "ow-card-title", card.title));
    const board = el("span", "ow-board");
    (card.board || []).forEach(function (m) {
      board.appendChild(el("span", "mark " + (m.rounding || "p"), m.mark + (m.rounding || "p")));
    });
    if (card.laps > 1) board.appendChild(el("span", "mark laps", card.laps + " laps"));
    head.appendChild(board);
    const sums = [];
    if (card.length_nm !== undefined && card.length_nm !== null) sums.push(card.length_nm + " nm");
    if (card.minutes !== undefined && card.minutes !== null) sums.push("about " + card.minutes + " min");
    head.appendChild(el("span", "ow-card-sum", sums.join(" · ")));
    box.appendChild(head);

    const table = el("table", "ow-legs");
    const top = el("tr");
    ["Leg", "TWA", "Sail", "nm", "min"].forEach(function (h) { top.appendChild(el("th", null, h)); });
    table.appendChild(el("thead")).appendChild(top);
    const body = el("tbody");
    (card.legs || []).forEach(function (leg) {
      const row = el("tr");
      row.appendChild(el("td", "ow-leg", leg.leg));
      const twa = el("td", "ow-twa");
      twa.appendChild(document.createTextNode(leg.twa === null || leg.twa === undefined ? "—" : leg.twa + "°"));
      if (leg.tack) twa.appendChild(el("span", "ow-tack ow-tack-" + leg.tack.toLowerCase(), leg.tack));
      twa.title = leg.point || "";
      row.appendChild(twa);
      row.appendChild(el("td", "ow-sail", leg.sail || "—"));
      row.appendChild(el("td", "ow-num", leg.nm === null || leg.nm === undefined ? "—" : String(leg.nm)));
      row.appendChild(el("td", "ow-num", leg.min === null || leg.min === undefined ? "—" : String(leg.min)));
      body.appendChild(row);
    });
    table.appendChild(body);
    box.appendChild(table);
    box.appendChild(el("p", "ow-card-foot",
      "In " + (card.wind || "the wind now") + (card.polar ? ", on the " + card.polar + " polar" : "")
      + ". P and S: port or starboard tack."));
    if (use && card.choosable !== false) {
      const choose = el("button", "ow-card-use", "Use this");
      choose.type = "button";
      choose.title = sayToChoose(card);
      choose.addEventListener("click", function () { use(sayToChoose(card)); });
      box.appendChild(choose);
    }
    return box;
  }

  // The cards go under the reply rather than inside it, the full width of the
  // thread, and two or more sit side by side to be swiped through. Stacked,
  // three courses were a thousand pixels of scrolling on a phone and the words
  // that compared them were off the top of the screen. `box` must already be
  // in the thread.
  function addCourses(box, courses, use) {
    const cards = courses || [];
    if (!cards.length) return null;
    const row = el("div", "ow-cards" + (cards.length > 1 ? "" : " ow-cards-one"));
    cards.forEach(function (card, i) {
      row.appendChild(courseCard(card, cards.length > 1 ? i + 1 : 0, use));
    });
    box.after(row);
    return row;
  }

  // The time a message was sent, on the phone's clock -- the hut's, for a
  // conversation reloaded from it.
  function stamp() {
    const now = new Date();
    return String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }
  OnWater.courseCard = courseCard;

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

    // A card's "Use this" says what the race officer would have typed, and sends
    // it: it is read back and waits for a Yes like anything else.
    function useCourse(words) {
      if (busy) return;
      input.value = words;
      form.requestSubmit();
    }

    // The conversation reloaded after a refresh arrives as the server wrote it,
    // one plain paragraph a turn; it is laid out exactly as it was first shown.
    thread.querySelectorAll(".ow-app p.ow-text").forEach(function (p) {
      const box = p.parentNode;
      p.replaceWith(formatted(p.textContent));
      let courses = [];
      try { courses = JSON.parse(box.dataset.courses || "[]"); } catch (err) { courses = []; }
      addCourses(box, courses, useCourse);
    });

    // The newest message at the bottom of the screen, as in any chat -- unless
    // it is taller than the screen, when its first line is the one to read:
    // scrolled to the end, a reply with cards under it showed the cards and not
    // the words saying which one to choose.
    const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function reveal(node) {
      const bottom = thread.scrollHeight - thread.clientHeight;
      const top = node.offsetTop - 12;
      thread.scrollTo({top: Math.max(0, Math.min(bottom, top)), behavior: still ? "auto" : "smooth"});
    }

    function show(kind, text, extraClass, courses) {
      const box = el("div", "ow-msg ow-new " + kind + (extraClass ? " " + extraClass : ""));
      if (kind === "ow-app") box.appendChild(formatted(text));
      else box.appendChild(el("p", null, text));
      box.appendChild(el("span", "ow-time", stamp()));
      thread.appendChild(box);
      addCourses(box, courses, useCourse);
      reveal(box);
      return box;
    }

    // Something to look at while the hut works. A sentence can take three calls
    // to the interpreter and twenty seconds, and the page used to show a greyed
    // button for all of it -- "you just have to wait and hope". This says what is
    // happening, asking the hut every second and a half, and counts the seconds
    // so a stall looks like a stall rather than like nothing.
    function working(commandId, firstWords) {
      const box = el("div", "ow-msg ow-new ow-app ow-working");
      const line = el("p");
      // The three dots every chat uses for "the other side is typing" -- here,
      // the hut is working -- with what it is doing beside them.
      const typing = el("span", "ow-typing");
      typing.setAttribute("aria-hidden", "true");
      typing.appendChild(el("i"));
      typing.appendChild(el("i"));
      typing.appendChild(el("i"));
      const doing = el("span", "ow-working-doing", firstWords);
      const dots = el("span", "ow-dots", "");
      const secs = el("span", "ow-working-secs", "");
      line.appendChild(typing);
      line.appendChild(doing);
      line.appendChild(dots);
      line.appendChild(secs);
      box.appendChild(line);
      thread.appendChild(box);
      reveal(box);
      const started = Date.now();
      let stopped = false;
      const counter = setInterval(function () {
        const s = Math.round((Date.now() - started) / 1000);
        if (s >= 2) secs.textContent = " " + s + " s";
        if (s >= 30 && !box.classList.contains("ow-slow")) {
          box.classList.add("ow-slow");
          line.appendChild(el("span", "ow-working-note",
            " — slower than usual; the interpreter may be busy."));
        }
      }, 1000);
      async function poll() {
        if (stopped || !commandId || !config.progressUrl) return;
        try {
          const resp = await fetch(config.progressUrl + "?id=" + encodeURIComponent(commandId),
                                   {cache: "no-store", credentials: "same-origin"});
          const data = await resp.json();
          if (!stopped && data && data.doing) doing.textContent = data.doing;
        } catch (err) {
          // A progress poll that fails says nothing about the command itself.
        }
        if (!stopped) setTimeout(poll, 1500);
      }
      setTimeout(poll, 700);
      return {
        done: function () {
          stopped = true;
          clearInterval(counter);
          box.remove();
        }
      };
    }

    function setBusy(state) {
      busy = state;
      btnSay.disabled = state;
      btnYes.disabled = state;
      btnNo.disabled = state;
      // The button is an arrow, so it says it is sending rather than changing
      // its words to "…".
      btnSay.setAttribute("aria-busy", state ? "true" : "false");
      btnSay.setAttribute("aria-label", state ? "Sending" : "Send");
    }

    // The Yes and No buttons, and what they are for. A proposal stays live on the
    // server until it is answered, superseded or lapses, and the buttons stay
    // with it: every reply says whether one is still waiting (pending_token),
    // so a question asked in between no longer takes the Yes button away while
    // the interpreter is telling somebody to press it.
    const answerWhat = document.getElementById("answerWhat");
    function awaitAnswer(token, what) {
      pendingToken = token;
      answer.hidden = !token;
      if (answerWhat) {
        // "Waiting for your yes" is the label above it, in the page.
        answerWhat.textContent = token && what ? what : "";
        answerWhat.hidden = !(token && what);
      }
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
      // Redrawn only when the course, or the wind by enough to turn the arrow,
      // has changed: the strip is now asked for every few seconds.
      const courseKey = JSON.stringify([header.race_id, header.course_board,
                                        header.course_marks, header.shortened]);
      const wind = Number(header.wind_direction);
      const windMoved = header.wind_direction !== undefined && header.wind_direction !== ""
        && (drawnWind === null || Math.abs(((wind - drawnWind + 540) % 360) - 180) >= 10);
      if (courseKey !== drawnCourse || windMoved) {
        const board = document.getElementById("owBoard");
        if (board && header.course_board) {
          board.textContent = "";
          header.course_board.forEach(function (m) {
            board.appendChild(el("span", "mark " + (m.rounding || ""), m.mark + (m.rounding || "")));
          });
        }
        const map = document.querySelector("#owChart .course-map");
        if (map && header.course_marks) {
          if (windMoved) map.dataset.windDirection = String(header.wind_direction);
          if (header.shortened !== undefined) map.dataset.shortened = header.shortened ? "1" : "0";
          // render() stores the new marks on the element, so a chart opened later
          // draws the course the race is on now rather than the one it started with.
          if (window.RaceCourseMap) window.RaceCourseMap.render(map, header.course_marks);
        }
        drawnCourse = courseKey;
        if (windMoved) drawnWind = wind;
      }
      tick();
    }
    // What the board and chart were last drawn from. The page's own render counts
    // as the first drawing, so the first answer from the hut redraws nothing
    // unless something has actually changed.
    let drawnCourse = null;
    let drawnWind = null;

    // A course changed, a start moved or a course shortened on the race sheet
    // has to reach this page as it reaches every other: the strip is asked for
    // every five seconds, and applied in place -- a reload would throw away
    // whatever was half typed. Not while the phone's screen is off, which is
    // most of the time on a boat and costs the hut's 4G for nothing.
    async function refreshHeader() {
      if (!config.headerUrl || document.hidden) return;
      try {
        const resp = await fetch(config.headerUrl, {cache: "no-store", credentials: "same-origin",
                                                    headers: {"Accept": "application/json"}});
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.header) applyHeader(data.header);
      } catch (err) {
        // A missed refresh is the next one's job; the page itself is unaffected.
      }
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
        show("ow-app", data.readback + (warnings ? " " + warnings : ""), "ow-ask", data.courses);
        awaitAnswer(data.pending_token, data.pending_readback || data.readback);
        return;
      }
      if (data.status === "answered") {
        // Words, not an action: there is nothing to confirm and nothing was done.
        show("ow-app", data.answer, null, data.courses);
        awaitAnswer(data.pending_token || null, data.pending_readback);
        return;
      }
      if (data.status === "needs_clarification" || data.status === "not_understood") {
        // A question with a choice in it is answered with one tap. The app
        // hears the reply either way, but "standard" is a lot to type with one
        // hand on a tiller.
        offerOptions(show("ow-app", data.question, "ow-ask"), data.options);
        awaitAnswer(data.pending_token || null, data.pending_readback);
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
      show("ow-app", data.message || "Done.", "ow-done", data.courses);
      awaitAnswer(data.pending_token || null, data.pending_readback);
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const text = (input.value || "").trim();
      if (!text || busy) return;
      show("ow-you", text);
      input.value = "";
      setBusy(true);
      const commandId = newCommandId();
      const wait = working(commandId, "Sending");
      try {
        const answer = await post(config.commandUrl, {text: text, client_command_id: commandId});
        wait.done();
        if (answer.unreachable) {
          // Interpreting changes nothing, so a request that never arrived
          // changed nothing either. That is worth saying: the alternative is
          // wondering whether half a race was created.
          answer.error += " Nothing was carried out — say it again.";
        }
        render(answer);
      } catch (err) {
        wait.done();
        // Said plainly: on the water, "did that land?" is the only question
        // that matters, and a silent failure is the worst possible answer.
        show("ow-app", "That did not reach the hut. Check signal and say it again.", "ow-problem");
      } finally {
        wait.done();
        setBusy(false);
      }
    });

    async function answerWith(confirm) {
      if (!pendingToken || busy) return;
      const token = pendingToken;
      awaitAnswer(null);
      show("ow-you", confirm ? "Yes" : "No");
      setBusy(true);
      const wait = working(null, confirm ? "Doing it" : "Leaving it");
      try {
        const answer = await post(config.confirmUrl, {pending_token: token, confirm: confirm});
        wait.done();
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
        wait.done();
        show("ow-app", "That did not reach the hut — it may or may not have been done. " +
                       "Say 'status' to check.", "ow-problem");
      } finally {
        wait.done();
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
    // The first answer sets what the page's own render drew, so it redraws
    // nothing unless the race has changed since the page was loaded.
    if (config.initialHeader) {
      drawnCourse = JSON.stringify([config.initialHeader.race_id, config.initialHeader.course_board,
                                    config.initialHeader.course_marks, config.initialHeader.shortened]);
      const initialWind = Number(config.initialHeader.wind_direction);
      drawnWind = config.initialHeader.wind_direction === "" || Number.isNaN(initialWind) ? null : initialWind;
    }
    setInterval(refreshHeader, 5000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refreshHeader();
    });
    // A proposal that was waiting when the page reloaded is still waiting: the
    // server never forgot it, and the buttons come back live rather than
    // leaving a command on the server that nothing on screen can agree to.
    if (config.pendingToken) awaitAnswer(config.pendingToken, config.pendingReadback);
    thread.scrollTop = thread.scrollHeight;
    input.focus();
  };

  window.OnWater = OnWater;
})();
