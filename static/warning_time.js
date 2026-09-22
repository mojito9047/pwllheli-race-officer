// Copyright © 2026 CapeNet Ltd. All Rights Reserved.
//
// The first-warning-signal field suggests a time when you open it, and only
// then.
//
// A new race is created with no warning time at all -- "Not set" is a real
// state, and the start sequence does not run until somebody chooses one. So the
// field cannot simply be pre-filled with a suggestion: the race officer opens
// the race to set a course, presses Save, and has silently been given a warning
// signal time, which is a fleet being counted down to a race nobody started.
//
// The field therefore shows exactly what is stored, and the suggestion is put in
// when the field is opened -- which is when the race officer is choosing one.
// A stored time that has already gone is replaced the same way, because a field
// offering a moment that has passed has to be retyped every time.
//
// Without JavaScript the field is simply what is stored, the picker opens at the
// current time as it always did, and nothing is written that was not chosen.
(function () {
  function parseLocal(value) {
    // "2026-09-22T14:30" from a datetime-local input, read as local time.
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(value || ''));
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0);
  }

  function suggestOnOpen(input) {
    var fill = function () {
      var suggested = input.dataset.suggested || '';
      if (!suggested) return;
      var at = parseLocal(input.value);
      // Empty, unreadable, or already gone. A time still ahead of us was chosen
      // by somebody and is not ours to move, however close it is.
      if (!at || at.getTime() <= Date.now()) input.value = suggested;
    };
    input.addEventListener('focus', fill);
    input.addEventListener('click', fill);
  }

  function arm() {
    document.querySelectorAll('input[data-suggested]').forEach(suggestOnOpen);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', arm);
  } else {
    arm();
  }
})();
