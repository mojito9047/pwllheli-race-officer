// Copyright © 2026 CapeNet Ltd. All Rights Reserved.
// Live camera panels: refreshing snapshots first, the relay's live stream once
// it is running.
//
// The relay's stream is on demand — MediaMTX only pulls the camera while
// somebody is watching, so the first viewer waits while it spins up. Rather
// than show a black player for those seconds, a panel keeps refreshing the
// branded snapshot (what it has always done) and swaps to the live stream when
// that stream reports it is playing.
//
// The stream itself is the relay's own watch page in an iframe: it brings its
// own hls.js and retry logic, so there is one player to keep working rather
// than two. That page posts its state to us (see deploy/live_stream/site/
// index.html), and ONLY a posted "playing" reveals it. Showing the frame
// without that signal would put a broken player on the page whenever it cannot
// load at all — the relay's frame-ancestors policy refuses origins it does not
// list, so a hut PC or test server on http://localhost:5050 gets a "refused to
// connect" box. No signal within the grace period means no live video here:
// the panel drops the frame, keeps the snapshots, says so, and retries later.
//
//   <div class="live-camera" data-snapshot-src="…" data-snapshot-refresh-ms="2000"
//        data-stream-page="https://…/live" data-stream-grace-ms="15000">
//     <img class="live-camera-snapshot" …>
//     <p class="live-camera-note">…</p>
//   </div>
//
// Panels are inert until start() is called, and stop() tears the iframe down so
// the relay can drop the stream when nobody is looking.
(function () {
  const DEFAULT_SNAPSHOT_MS = 2000;
  // How long to wait for the relay page to say anything at all. It posts
  // "connecting" the moment it loads, so silence means the browser refused the
  // frame (frame-ancestors) or the page is an older build.
  const DEFAULT_BLOCKED_MS = 8000;
  // Once it has said "connecting" the frame is working and the stream is simply
  // starting — MediaMTX has to pull the camera and cut the first HLS segments,
  // which takes appreciably longer than the old 15 s allowed. Wait properly.
  const DEFAULT_STARTING_MS = 120000;
  // ...but a frame that loaded is worth showing even if "playing" never reaches
  // us: the relay page displays its own "Connecting…"/buffering state and starts
  // by itself. Only a frame that said nothing at all is hidden away.
  const DEFAULT_REVEAL_MS = 25000;
  const RETRY_AFTER_ERROR_MS = 20000;

  function noteEl(container) {
    return container.querySelector('.live-camera-note');
  }

  function setNote(container, text) {
    const el = noteEl(container);
    if (el && text !== undefined) el.textContent = text;
  }

  // `wanted` is what the page asked for (tab open, panel expanded); `active` is
  // whether frames/stream are actually running. Backgrounding the browser tab
  // deactivates a panel without forgetting that it should come back.
  function state(container) {
    if (!container._liveCamera) container._liveCamera = {snapshotTimer: null, iframe: null, graceTimer: null,
                                                        retryTimer: null, revealTimer: null,
                                                        streaming: false, connecting: false,
                                                        wanted: false, active: false};
    return container._liveCamera;
  }

  function snapshot(container) {
    return container.querySelector('.live-camera-snapshot');
  }

  function refreshSnapshot(container) {
    const img = snapshot(container);
    const src = container.dataset.snapshotSrc || '';
    if (!img || !src) return;
    const joiner = src.indexOf('?') === -1 ? '?' : '&';
    img.src = src + joiner + 't=' + Date.now();
  }

  function startSnapshots(container) {
    const st = state(container);
    if (st.snapshotTimer) return;
    refreshSnapshot(container);
    const every = Math.max(500, Number(container.dataset.snapshotRefreshMs) || DEFAULT_SNAPSHOT_MS);
    st.snapshotTimer = setInterval(() => refreshSnapshot(container), every);
  }

  function stopSnapshots(container) {
    const st = state(container);
    if (st.snapshotTimer) { clearInterval(st.snapshotTimer); st.snapshotTimer = null; }
  }

  function showStream(container) {
    const st = state(container);
    if (!st.iframe || st.streaming) return;
    st.streaming = true;
    if (st.graceTimer) { clearTimeout(st.graceTimer); st.graceTimer = null; }
    if (st.revealTimer) { clearTimeout(st.revealTimer); st.revealTimer = null; }
    delete st.iframe.dataset.loading;
    const img = snapshot(container);
    if (img) img.hidden = true;
    stopSnapshots(container);            // the stream is the picture now
    container.classList.add('streaming');
    setNote(container, container.dataset.streamNote || 'Live video from the relay.');
  }

  function hideStream(container, note) {
    const st = state(container);
    st.streaming = false;
    st.connecting = false;
    if (st.iframe) { st.iframe.remove(); st.iframe = null; }
    if (st.graceTimer) { clearTimeout(st.graceTimer); st.graceTimer = null; }
    if (st.revealTimer) { clearTimeout(st.revealTimer); st.revealTimer = null; }
    const img = snapshot(container);
    if (img) img.hidden = false;
    container.classList.remove('streaming');
    if (st.active) startSnapshots(container);
    setNote(container, note);
  }

  function streamOrigin(container) {
    try {
      return new URL(container.dataset.streamPage, window.location.href).origin;
    } catch (err) {
      return null;
    }
  }

  function startStream(container) {
    const st = state(container);
    const page = container.dataset.streamPage || '';
    if (!page || st.iframe) return;
    const frame = document.createElement('iframe');
    frame.className = 'live-camera-stream';
    frame.src = page;
    frame.title = 'Live camera stream';
    // Not `hidden`: a display:none iframe may never start playing, so it would
    // never report "playing" and we would wait for ever. It is rendered at
    // opacity 0 behind the snapshot instead (see .live-camera-stream[data-loading]).
    frame.dataset.loading = '1';
    frame.setAttribute('allow', 'autoplay; fullscreen; picture-in-picture');
    frame.setAttribute('allowfullscreen', 'allowfullscreen');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    st.iframe = frame;
    container.insertBefore(frame, noteEl(container));
    // Nothing is shown until the relay page reports it is playing. Silence for
    // this long means the frame never loaded at all, so give up and stay on the
    // pictures; hearing "connecting" extends the wait (see handleMessage).
    st.connecting = false;
    const blocked = Math.max(2000, Number(container.dataset.streamBlockedMs) || DEFAULT_BLOCKED_MS);
    st.graceTimer = setTimeout(() => giveUpOnStream(container), blocked);
  }

  function giveUpOnStream(container) {
    const st = state(container);
    if (st.streaming) return;                  // already playing; nothing to do
    hideStream(container, container.dataset.streamUnavailableNote
      || 'Live video is not available here — showing still pictures.');
    if (!st.retryTimer) {
      st.retryTimer = setTimeout(() => {
        st.retryTimer = null;
        if (st.active) startStream(container);
      }, RETRY_AFTER_ERROR_MS);
    }
  }

  function handleMessage(event) {
    const data = event.data;
    if (!data || data.source !== 'psc-live') return;
    document.querySelectorAll('.live-camera').forEach(container => {
      const st = state(container);
      if (!st.active || !st.iframe || event.source !== st.iframe.contentWindow) return;
      if (event.origin !== streamOrigin(container)) return;
      if (data.state === 'playing') {
        showStream(container);
      } else if (data.state === 'connecting') {
        // The frame loaded, so it is not blocked — the stream is just starting.
        // Swap the short "did it load?" timeout for a patient one, once.
        if (st.connecting || st.streaming) return;
        st.connecting = true;
        if (st.graceTimer) clearTimeout(st.graceTimer);
        const starting = Math.max(5000, Number(container.dataset.streamStartingMs) || DEFAULT_STARTING_MS);
        st.graceTimer = setTimeout(() => giveUpOnStream(container), starting);
        // The frame works, so show it shortly even if "playing" never arrives —
        // the relay page has its own connecting/buffering display and will start
        // on its own. Waiting for a message we may never get is worse.
        const reveal = Math.max(3000, Number(container.dataset.streamRevealMs) || DEFAULT_REVEAL_MS);
        st.revealTimer = setTimeout(() => showStream(container), reveal);
        setNote(container, container.dataset.startingNote || container.dataset.snapshotNote || '');
      } else if (data.state === 'error') {
        giveUpOnStream(container);             // back to snapshots, retry later
      }
    });
  }

  function activate(container) {
    const st = state(container);
    if (st.active) return;
    st.active = true;
    startSnapshots(container);
    startStream(container);
  }

  function deactivate(container) {
    const st = state(container);
    st.active = false;
    stopSnapshots(container);
    if (st.retryTimer) { clearTimeout(st.retryTimer); st.retryTimer = null; }
    // Removing the iframe drops this viewer, so the relay can stop pulling the
    // camera when the last person looks away.
    hideStream(container, container.dataset.snapshotNote || '');
  }

  function start(container) {
    if (!container) return;
    state(container).wanted = true;
    if (!document.hidden) activate(container);
  }

  function stop(container) {
    if (!container) return;
    state(container).wanted = false;
    deactivate(container);
  }

  window.addEventListener('message', handleMessage);
  // Nothing should keep streaming or fetching frames while the browser tab is in
  // the background; panels the page still wants come back when it returns.
  document.addEventListener('visibilitychange', () => {
    document.querySelectorAll('.live-camera').forEach(container => {
      const st = state(container);
      if (!st.wanted) return;
      if (document.hidden) deactivate(container);
      else activate(container);
    });
  });

  window.PwllheliLiveCamera = {start: start, stop: stop};
})();
