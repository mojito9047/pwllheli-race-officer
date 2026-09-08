// Copyright © 2026 CapeNet Ltd. All Rights Reserved.
// Shared countdown formatting so every timer reads the same way:
// D:HH:MM:SS, dropping the leading days and hours segments while they are zero.
// Minutes and seconds are always shown. Callers add any +/- sign or state text.
(function () {
  function pad2(n) { return String(n).padStart(2, '0'); }
  window.roCountdownBody = function (seconds) {
    var abs = Math.max(0, Math.floor(seconds));
    var d = Math.floor(abs / 86400); abs %= 86400;
    var h = Math.floor(abs / 3600); abs %= 3600;
    var m = Math.floor(abs / 60);
    var s = abs % 60;
    if (d > 0) return d + ':' + pad2(h) + ':' + pad2(m) + ':' + pad2(s);
    if (h > 0) return h + ':' + pad2(m) + ':' + pad2(s);
    return m + ':' + pad2(s);
  };
})();
