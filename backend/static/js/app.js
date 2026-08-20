/* DocCipher Breaker -- front-end interactions.

   Flow: drop/browse -> processing modal -> result modal -> activity log refresh.

   Processing itself takes ~50ms, which is too fast to read, so the step
   checklist below is paced over a minimum window while the real request is in
   flight. The step labels name what the server actually does (it copies to a
   temp workspace; it never renames your original), and the outcome, timing and
   error text always come from the server -- none of it is invented here. */

(function () {
  'use strict';

  const dropZone  = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const browseBtn = document.getElementById('browse-btn');
  const output    = document.getElementById('output');

  // Processing modal
  const crackModal = document.getElementById('crack-modal');
  const cmFill     = document.getElementById('cm-fill');
  const cmPct      = document.getElementById('cm-pct');
  const cmSteps    = document.getElementById('cm-steps');
  const cmStatus   = document.getElementById('cm-status');
  const cmTimer    = document.getElementById('cm-timer');
  const cmTarget   = document.getElementById('cm-target');
  const cmCancel   = document.getElementById('cm-cancel');
  const cmFormat   = document.getElementById('cm-format');
  const cmTitle    = document.getElementById('crack-modal-title');

  // Success modal
  const successModal = document.getElementById('success-modal');
  const successBody  = document.getElementById('success-body');

  /* The eight steps the server actually performs. These deliberately do NOT
     say "rename .docx to .zip" -- the app copies into a temp workspace and
     leaves the original untouched, so claiming otherwise would be inaccurate. */
  const FORMATS = {
    docx: {
      label: 'DOCX',
      title: 'Document',
      steps: [
        'Analyzing file structure',
        'Creating working copy (original untouched)',
        'Extracting document contents',
        'Locating word/settings.xml',
        'Removing editing restrictions',
        'Validating document integrity',
        'Rebuilding document',
        'Writing unlocked file'
      ],
      status: [
        'Analyzing document...',
        'Creating working copy...',
        'Extracting contents...',
        'Locating settings...',
        'Removing protections...',
        'Validating changes...',
        'Rebuilding document...',
        'Finalizing...'
      ]
    },
    pdf: {
      label: 'PDF',
      title: 'PDF',
      steps: [
        'Analyzing PDF security',
        'Detecting restriction type',
        'Removing restrictions',
        'Rebuilding document',
        'Verifying integrity',
        'Writing unlocked file'
      ],
      status: [
        'Analyzing PDF security...',
        'Detecting restrictions...',
        'Removing restrictions...',
        'Rebuilding document...',
        'Verifying integrity...',
        'Finalizing...'
      ]
    }
  };

  function formatOf(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    return FORMATS[ext] ? ext : null;
  }

  // Set per run by openCrackModal().
  let STEPS = FORMATS.docx.steps;
  let STATUS_LINES = FORMATS.docx.status;

  const MIN_SHOW_MS = 2000;   // floor so the sequence stays readable

  // Inline SVG only -- no emoji, so glyphs inherit the theme colour and render
  // identically on every platform.
  const SVG = (body, sw) =>
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' +
    (sw || 2) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    body + '</svg>';

  const ICONS = {
    pending:  '<circle cx="12" cy="12" r="8" stroke-dasharray="2 3" opacity=".6"/>',
    active:   '<path d="M12 3a9 9 0 1 0 9 9"/>',
    done:     '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    failed:   '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    download: '<path d="M12 3v12"/><path d="m7 12 5 5 5-5"/><path d="M4 21h16"/>',
    folder:   '<path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    bolt:     '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    refresh:  '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 4v4h-4"/>' +
              '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 20v-4h4"/>',
    check:    '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>'
  };

  let busy = false;
  let cancelled = false;
  let controller = null;      // AbortController for the in-flight upload
  let timerId = null;
  let tickerId = null;

  /* ---------------- sound ---------------- */

  let audioCtx = null;

  function beep(freq, ms, type) {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type || 'square';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.04, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + ms / 1000);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + ms / 1000);
    } catch (e) { /* audio blocked before first gesture -- ignore */ }
  }

  const tick   = () => beep(880, 35);
  const chime  = () => { beep(660, 90); setTimeout(() => beep(990, 170), 90); };
  const buzzer = () => beep(150, 320, 'sawtooth');

  /* ---------------- processing modal ---------------- */

  function buildSteps() {
    cmSteps.innerHTML = '';
    STEPS.forEach((text, i) => {
      const row = document.createElement('div');
      row.className = 'cm-step';
      row.id = 'cm-step-' + i;
      row.innerHTML =
        '<span class="box">' + SVG(ICONS.pending) + '</span>' +
        '<span>[' + (i + 1) + '/' + STEPS.length + '] ' + text + '</span>';
      cmSteps.appendChild(row);
    });
  }

  function markStep(i, state) {
    const row = document.getElementById('cm-step-' + i);
    if (!row) return;
    row.className = 'cm-step ' + state;
    const box = row.querySelector('.box');
    if (box) box.innerHTML = SVG(ICONS[state] || ICONS.pending);
    if (state === 'active') row.scrollIntoView({ block: 'nearest' });
  }

  function startTimer() {
    const t0 = Date.now();
    cmTimer.textContent = '00:00';
    timerId = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      cmTimer.textContent =
        String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    }, 250);
  }

  function setPct(n) {
    cmFill.style.width = n + '%';
    cmPct.textContent = n + '%';
  }

  function stopTimers() {
    clearInterval(timerId);
    clearInterval(tickerId);
    timerId = tickerId = null;
  }

  function openCrackModal(fileName, fmt) {
    cancelled = false;
    const spec = FORMATS[fmt] || FORMATS.docx;
    STEPS = spec.steps;
    STATUS_LINES = spec.status;
    if (cmFormat) cmFormat.textContent = spec.label + ' document';
    if (cmTitle) cmTitle.textContent = 'Processing ' + spec.title + '...';
    cmTarget.innerHTML = 'File: <b>' + escapeHtml(fileName) + '</b>';
    cmFill.style.width = '0%';
    cmPct.textContent = '0%';
    cmStatus.textContent = STATUS_LINES[0];
    cmCancel.disabled = false;
    buildSteps();
    crackModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    startTimer();

    let step = 0;
    markStep(0, 'active');
    tick();

    tickerId = setInterval(() => {
      markStep(step, 'done');
      step++;
      if (step >= STEPS.length) { clearInterval(tickerId); tickerId = null; return; }
      markStep(step, 'active');
      cmStatus.textContent = STATUS_LINES[step];
      setPct(Math.round((step / STEPS.length) * 100));
      tick();
    }, MIN_SHOW_MS / STEPS.length);
  }

  function closeCrackModal() {
    stopTimers();
    crackModal.classList.add('hidden');
    document.body.style.overflow = '';
  }

  /* ---------------- result modal ---------------- */

  function showSuccess(data) {
    const ok = data.status === 'success';
    successBody.className = 'success-modal' + (ok ? '' : ' failure');

    const stats = ok
      ? '<div class="sm-grid">' +
          cell('FILE', data.input_name) +
          cell('FORMAT', (data.format || 'docx').toUpperCase()) +
          cell('SIZE', humanSize(data.size_after)) +
          cell('TIME', (data.duration || 0) + 's') +
        '</div>' +
        '<div class="sm-path" title="' + escapeHtml(data.output_path || '') + '">' +
          escapeHtml(data.output_path || '') +
        '</div>'
      : '<div class="sm-grid">' +
          cell('FILE', data.input_name || '--') +
          cell('FORMAT', (data.format || '--').toUpperCase()) +
          cell('STATUS', 'Failed', true) +
          cell('TIME', (data.duration || 0) + 's') +
        '</div>' +
        '<div class="sm-error">' + escapeHtml(data.error || 'Unknown failure') + '</div>';

    const actions = ok
      ? '<div class="actions center">' +
          '<a class="btn primary" href="/download/' + encodeURIComponent(data.download_token) +
            '" download>' + SVG(ICONS.download) + ' DOWNLOAD</a>' +
          '<button class="btn" onclick="revealFile(' + Number(data.history_id) + ')">' +
            SVG(ICONS.folder) + ' OPEN FILE</button>' +
          '<button class="btn" onclick="anotherOne()">' + SVG(ICONS.bolt) + ' NEW</button>' +
        '</div>'
      : '<div class="actions center">' +
          '<button class="btn" onclick="anotherOne()">' + SVG(ICONS.refresh) + ' RETRY</button>' +
        '</div>';

    successBody.innerHTML =
      '<div class="sm-mark">' + SVG(ok ? ICONS.check : ICONS.failed, 1.6) + '</div>' +
      '<h2 class="sm-title" id="success-modal-title">' +
        (ok ? 'Unlocked Successfully!' : 'Processing Failed') + '</h2>' +
      '<div class="sm-rule"></div>' +
      stats + actions +
      '<div class="sm-foot">DocCipher Breaker v1.0.0 &nbsp;|&nbsp; Created by Achu Vijayakumar ' +
      '&nbsp;|&nbsp; <span class="edu">FOR EDUCATIONAL PURPOSES ONLY</span></div>';

    successModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    ok ? chime() : buzzer();

    const first = successBody.querySelector('.btn');
    if (first) first.focus();
  }

  function cell(k, v, bad) {
    const text = escapeHtml(String(v));
    return '<div class="sm-cell"><span class="k">' + k + '</span>' +
           '<span class="v' + (bad ? ' bad' : '') + '" title="' + text + '">' + text +
           '</span></div>';
  }

  window.closeSuccess = function () {
    successModal.classList.add('hidden');
    document.body.style.overflow = '';
  };

  window.anotherOne = function () {
    closeSuccess();
    if (output) output.innerHTML = '';
    dropZone.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  /* ---------------- upload ---------------- */

  async function crackFile(file) {
    const form = new FormData();
    form.append('file', file);

    controller = new AbortController();
    openCrackModal(file.name, formatOf(file.name) || 'docx');
    const started = Date.now();

    let data;
    try {
      const res = await fetch('/upload?format=json', {
        method: 'POST', body: form, signal: controller.signal
      });
      data = await res.json();
    } catch (err) {
      if (cancelled || (err && err.name === 'AbortError')) {
        closeCrackModal();
        return;
      }
      data = {
        status: 'failed',
        input_name: file.name,
        error: 'Connection to the local server was lost: ' + String(err)
      };
    } finally {
      controller = null;
    }

    // Hold the animation open long enough for the sequence to be readable.
    await sleep(Math.max(0, MIN_SHOW_MS - (Date.now() - started)));
    if (cancelled) { closeCrackModal(); return; }

    stopTimers();
    const failIdx = data.status === 'success' ? -1 : (data.failed_step || 1) - 1;
    STEPS.forEach((_, i) => {
      if (failIdx >= 0 && i === failIdx) markStep(i, 'failed');
      else if (failIdx >= 0 && i > failIdx) markStep(i, 'pending');
      else markStep(i, 'done');
    });
    setPct(100);
    cmStatus.textContent =
      data.status === 'success' ? 'Processing complete' : 'Processing failed';

    await sleep(420);
    closeCrackModal();
    showSuccess(data);
    refreshSidePanels();
  }

  async function crackQueue(files) {
    if (busy) return;
    const docs = Array.from(files).filter((f) => formatOf(f.name) !== null);

    if (!docs.length) {
      showSuccess({
        status: 'failed',
        input_name: '--',
        error: 'No supported files in selection. Drop a .docx or .pdf file.'
      });
      return;
    }

    busy = true;
    dropZone.classList.remove('dragover');
    try {
      for (const file of docs) {
        await crackFile(file);
        if (cancelled) break;
        // With several files queued, wait for the user to dismiss each result.
        if (docs.length > 1) await waitForSuccessDismissed();
      }
    } finally {
      busy = false;
    }
  }

  function waitForSuccessDismissed() {
    return new Promise((resolve) => {
      if (successModal.classList.contains('hidden')) return resolve();
      const observer = new MutationObserver(() => {
        if (successModal.classList.contains('hidden')) { observer.disconnect(); resolve(); }
      });
      observer.observe(successModal, { attributes: true, attributeFilter: ['class'] });
    });
  }

  /* ---------------- cancel ---------------- */

  cmCancel.addEventListener('click', () => {
    cancelled = true;
    cmCancel.disabled = true;
    cmStatus.textContent = 'Cancelling...';
    // Abort the request in flight. The server writes its output atomically at
    // the last step, so a cancelled run leaves nothing half-written behind.
    if (controller) controller.abort();
    stopTimers();
    setTimeout(closeCrackModal, 220);
  });

  /* ---------------- events ---------------- */

  browseBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) crackQueue(fileInput.files);
    fileInput.value = '';
  });

  ['dragenter', 'dragover'].forEach((type) =>
    dropZone.addEventListener(type, (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    })
  );

  ['dragleave', 'drop'].forEach((type) =>
    dropZone.addEventListener(type, (e) => {
      e.preventDefault();
      if (type === 'dragleave' && dropZone.contains(e.relatedTarget)) return;
      dropZone.classList.remove('dragover');
    })
  );

  dropZone.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) crackQueue(e.dataTransfer.files);
  });

  // Dropping anywhere else must not make the browser navigate to the file.
  ['dragover', 'drop'].forEach((type) =>
    window.addEventListener(type, (e) => {
      if (!dropZone.contains(e.target)) e.preventDefault();
    })
  );

  /* ---------------- about modal ---------------- */

  const aboutModal = document.getElementById('modal');

  window.openAbout = function () {
    aboutModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    const close = aboutModal.querySelector('.modal-close');
    if (close) close.focus();
  };

  window.closeAbout = function () {
    aboutModal.classList.add('hidden');
    document.body.style.overflow = '';
  };

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!aboutModal.classList.contains('hidden')) closeAbout();
    else if (!successModal.classList.contains('hidden')) closeSuccess();
    else if (!crackModal.classList.contains('hidden')) cmCancel.click();
  });

  /* ---------------- helpers ---------------- */

  window.revealFile = async function (historyId) {
    try {
      await fetch('/reveal/' + historyId, { method: 'POST' });
    } catch (e) { /* non-fatal */ }
  };

  window.refreshSidePanels = function () {
    if (!window.htmx) return;
    const table = document.getElementById('history-table');
    const stats = document.getElementById('stats');
    if (table) htmx.ajax('GET', '/history', { target: '#history-table', source: table });
    if (stats) htmx.ajax('GET', '/stats', { target: '#stats' });
  };

  // Kept for any server-rendered fragment that still calls it.
  window.resetZone = window.anotherOne;

  /* ---------------- theme ---------------- */

  // The stored theme is applied inline in <head> before first paint; this only
  // handles the toggle itself. Light is the default when nothing is stored.
  (function themeToggle() {
    const KEY = 'doccipher-theme';
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;

    const root = document.documentElement;

    function apply(theme) {
      root.setAttribute('data-theme', theme);
      btn.setAttribute('aria-label',
        theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
      try { localStorage.setItem(KEY, theme); } catch (e) { /* private mode */ }
    }

    btn.addEventListener('click', () => {
      apply(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    });

    apply(root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light');
  })();

  /* ---------------- watermark ---------------- */

  // Built in JS rather than markup so the notice does not bloat the HTML
  // source or get read out as page text by screen readers.
  (function buildWatermark() {
    const wm = document.getElementById('watermark');
    if (!wm) return;
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 120; i++) {
      const s = document.createElement('span');
      s.textContent = 'FOR EDUCATIONAL PURPOSES ONLY';
      frag.appendChild(s);
    }
    wm.appendChild(frag);
  })();

  function humanSize(bytes) {
    let v = Number(bytes) || 0;
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
})();
