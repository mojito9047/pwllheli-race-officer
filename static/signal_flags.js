/* The RRS 26 start sequence: which flags are hoisted right now, the markup for them,
 * and which phase of the countdown a race is in.
 *
 * Lifted out of templates/competitor_race.html when the clubhouse display needed the
 * same flags beside its countdown. Two copies of a rule is how the live course walk and
 * the replay's walk came to disagree, and how a finish came to be recorded on the wrong
 * line crossing — so the rule lives in one place and both pages call it.
 *
 * The flag *image* paths come from the caller because they are built with url_for in a
 * template; everything else is here.
 */
window.SignalFlags = (function () {
  // A class flag goes up at its warning signal, five minutes before that class starts,
  // and comes down as it starts. P is up from the preparatory signal (four minutes) to
  // one minute. Both windows are RRS 26.
  var WARNING_S = 300;
  var PREP_UP_S = 240;
  var PREP_DOWN_S = 60;

  function escapeAttr(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/[&<>"']/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
      });
  }

  /* The flags up at `now`, in hoist order, for a signal_panel_schedule.
   *
   * Several classes can have a flag up at once, and two classes can share one flag, so
   * duplicates are dropped on kind+numeral+label rather than counted twice.
   */
  function liveFlags(schedule, now) {
    var flags = [];
    var seen = {};
    var prepUp = false;
    (schedule || []).forEach(function (startItem) {
      var start = new Date(startItem.time || '');
      if (isNaN(start.getTime())) return;
      var diff = Math.round((start - now) / 1000);
      if (diff <= WARNING_S && diff > 0) {
        (startItem.flags || []).forEach(function (flag) {
          var key = (flag.kind || '') + ':' + (flag.numeral || '') + ':' + (flag.label || '');
          if (!seen[key]) { flags.push(flag); seen[key] = true; }
        });
      }
      if (diff <= PREP_UP_S && diff > PREP_DOWN_S) prepUp = true;
    });
    if (prepUp) flags.push({kind: 'prep-p', label: 'Preparatory flag P'});
    return flags;
  }

  /* Whether AP is up *at this moment*, from the two things the page was rendered
   * with: the flag, and the minute the race officer said it comes down.
   *
   * The page is rendered once and the flag comes down by itself, so a page that
   * only knows "postponed: AP" shows AP for ever and never starts its countdown --
   * which is what happened: the race officer watched the moment pass with the flag
   * still up and the clock still stopped until they reloaded. No polling is needed
   * to fix it, because both times are already in the page, and the start time it
   * counts to is the one that follows the flag coming down.
   *
   * Call it every tick, not once on load: the answer changes with the clock.
   */
  function postponedNow(flagName, endsAtIso, now) {
    if (!flagName) return '';
    var ends = endsAtIso ? new Date(endsAtIso) : null;
    if (!ends || isNaN(ends.getTime())) return flagName;   // up until further notice
    return (now || new Date()) < ends ? flagName : '';
  }

  /* The flags flying for a postponement, from the stored flag name.
   *
   * A postponement replaces the sequence rather than joining it: while AP is up no
   * warning, preparatory or starting signal is made, so no class flag and no P is
   * flying either. Returning only these is what the panel should show.
   *
   * "AP over H" is two flags, one above the other on the halyard. Side by side is the
   * best a row of boxes can do, so the label carries the meaning -- which is the part
   * a race officer actually needs to read back.
   */
  function postponementFlags(flagName) {
    var name = String(flagName || '').trim();
    if (!name) return [];
    if (/over\s*_?H/i.test(name)) {
      return [{kind: 'ap', label: 'AP over H'},
              {kind: 'code-h', label: 'H (further signals ashore)'}];
    }
    if (/over\s*_?A/i.test(name)) {
      return [{kind: 'ap', label: 'AP over A'},
              {kind: 'code-a', label: 'A (no more racing today)'}];
    }
    return [{kind: 'ap', label: 'AP (postponed)'}];
  }

  /* One flag as markup. `srcs` is {numeralBase, prep, codeS} of image paths.
   *
   * The letter in the trailing span is not decoration: signal flags are CSS-drawn as a
   * fallback and Code flag S rendered as a white box until v0.163, so the letter is
   * what makes a missing image survivable.
   */
  function flagMarkup(flag, srcs) {
    if (flag.kind === 'code-s') {
      return '<span class="signal-flag code-s flag-up" title="Code flag S — shortened course"'
        + ' aria-label="Code flag S — shortened course"><img src="' + srcs.codeS
        + '" alt="" aria-hidden="true"><span aria-hidden="true">S</span></span>';
    }
    // AP, H and A are drawn entirely in CSS: there are no images for them, and the
    // letters in the span are what make them readable if the CSS is ever missed too.
    if (flag.kind === 'ap' || flag.kind === 'code-h' || flag.kind === 'code-a') {
      var letter = flag.kind === 'ap' ? 'AP' : (flag.kind === 'code-h' ? 'H' : 'A');
      var apTitle = escapeAttr(flag.label || letter);
      return '<span class="signal-flag ' + flag.kind + ' flag-up" title="' + apTitle
        + '" aria-label="' + apTitle + '"><span aria-hidden="true">' + letter + '</span></span>';
    }
    if (flag.kind === 'prep-p') {
      return '<span class="signal-flag prep-p flag-up" title="Preparatory flag P"'
        + ' aria-label="Preparatory flag P"><img src="' + srcs.prep
        + '" alt="" aria-hidden="true"><span aria-hidden="true">P</span></span>';
    }
    var numeral = escapeAttr(flag.numeral || '0');
    var title = escapeAttr(flag.label || ('Numeral ' + numeral));
    return '<span class="signal-flag numeral-flag numeral-' + numeral + ' flag-up"'
      + ' data-numeral="' + numeral + '" title="' + title + '" aria-label="' + title
      + '"><img src="' + srcs.numeralBase + numeral + '.png" alt="" aria-hidden="true">'
      + '<span aria-hidden="true">' + numeral + '</span></span>';
  }

  function markup(flags, srcs) {
    return (flags || []).map(function (flag) { return flagMarkup(flag, srcs); }).join('');
  }

  /* Where the countdown is in the sequence: "Preparatory period", "One minute".
   *
   * The same RRS 26 windows as the flags, and it used to be a `stateFor` in both admin
   * race sheets with the numbers written out again. Naming the phase and deciding what
   * is flying are different questions, but they are answered from one set of timings, so
   * a change to the sequence cannot now move one and leave the other.
   *
   * `diff` is seconds until the start: positive before it, negative after.
   */
  function phaseFor(diff) {
    if (diff <= WARNING_S && diff > PREP_UP_S) return 'Warning signal window';
    if (diff <= PREP_UP_S && diff > PREP_DOWN_S) return 'Preparatory period';
    if (diff <= PREP_DOWN_S && diff > 0) return 'One minute';
    if (diff === 0) return 'START';
    if (diff < 0) return 'Race started';
    return 'Waiting for sequence';
  }

  /* "Flags: numeral 1 + preparatory flag P up" — the line under the flags. */
  function summary(flags, opts) {
    opts = opts || {};
    if (opts.raceFinished) return 'Flags: race finished';
    // Before anything about the sequence: while AP is up there is no sequence, and
    // saying "first warning not set" for a postponed race would be both true and
    // exactly the wrong thing to read on the water.
    if (opts.postponed) {
      if (/over\s*_?A/i.test(opts.postponed)) return 'Flags: AP over A up — no more racing today';
      if (/over\s*_?H/i.test(opts.postponed)) return 'Flags: AP over H up — further signals ashore';
      return 'Flags: AP up — warning signal one minute after AP is lowered';
    }
    if (!opts.hasSchedule && !opts.shortened) return 'Flags: first warning not set';
    if (!flags || !flags.length) return 'Flags: none up';
    return 'Flags: ' + flags.map(function (f) { return f.label || 'Flag'; }).join(' + ') + ' up';
  }

  /* What the countdown should say instead of counting. A race postponed keeps its old
   * start time in the database until AP comes down, so a countdown left running would
   * tick towards a gun that is not going to be fired -- which is the display version of
   * the fault this whole feature exists to fix.
   */
  function postponedLabel(flagName) {
    if (/over\s*_?A/i.test(String(flagName || ''))) return 'No more racing today';
    if (/over\s*_?H/i.test(String(flagName || ''))) return 'Postponed — ashore';
    return 'Postponed';
  }

  return {liveFlags: liveFlags, markup: markup, summary: summary,
          postponementFlags: postponementFlags, postponedLabel: postponedLabel,
          postponedNow: postponedNow,
          phaseFor: phaseFor, escapeAttr: escapeAttr,
          WARNING_S: WARNING_S, PREP_UP_S: PREP_UP_S, PREP_DOWN_S: PREP_DOWN_S};
})();
