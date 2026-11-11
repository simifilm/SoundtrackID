// The waveform: drawing, tooltip, zoom, panning and the minimap.

// Waveform tooltip
const wfTooltip = document.getElementById('wf-tooltip');

function showWfTooltip(x, y, text) {
  wfTooltip.textContent = text;
  wfTooltip.style.display = 'block';
  const tw = wfTooltip.offsetWidth;
  wfTooltip.style.left = Math.min(x + 14, window.innerWidth - tw - 8) + 'px';
  wfTooltip.style.top = (y - 44) + 'px';
}
function hideWfTooltip() { wfTooltip.style.display = 'none'; }

waveformCanvas.addEventListener('mousemove', e => {
  if (!wfSamples || wfDuration === 0) return;
  const rect = waveformCanvas.getBoundingClientRect();
  const t = ((e.clientX - rect.left) / rect.width) * wfDuration;
  let text = null;

  if (wfCues.length > 0) {
    const cue = wfCues.find(c => t >= c.start && t < c.end);
    text = cue
      ? (cue.title ? `${cue.artist}, ${cue.title}` : 'Unidentified music')
      : 'No music';
  } else if (wfChunks.length > 0) {
    // Live (during-run) tooltip: name tracks that have already been identified,
    // otherwise fall back to "Music detected" for regions still being processed.
    const ident = wfIdentified.find(r => r.title && t >= r.start && t < r.end);
    if (ident) {
      text = ident.artist ? `${ident.artist}, ${ident.title}` : ident.title;
    } else {
      const merged = [];
      for (const c of [...wfChunks].filter(c => c.isMusic).sort((a, b) => a.start - b.start)) {
        const last = merged[merged.length - 1];
        if (last && c.start <= last.end) { last.end = Math.max(last.end, c.end); }
        else { merged.push({ start: c.start, end: c.end }); }
      }
      text = merged.some(r => t >= r.start && t < r.end) ? 'Music detected' : 'No music';
    }
  }

  text ? showWfTooltip(e.clientX, e.clientY, text) : hideWfTooltip();
});
waveformCanvas.addEventListener('mouseleave', hideWfTooltip);

// Waveform. Its state is this file's: the other scripts set it through the
// functions below and only read it.
let wfSamples = null, wfDuration = 0, wfChunks = [], wfIdentified = [], wfCues = [];

// Zoom & pan state
let wfZoom = 1;
const WF_ZOOM_MIN = 1;
const WF_ZOOM_MAX_HARD = 50;
let cachedMergedMusic = [];   // recomputed inside redrawAll
let markerTimer = null;
let suppressNextClick = false;
let resizeTimer = null;

// Empty, for a new analysis: its waveform comes with its first events.
function resetWaveform() {
  wfSamples = null; wfDuration = 0; wfChunks = []; wfIdentified = []; wfCues = [];
  resetWaveformView();
}

// A run's waveform, shown whole.
function showWaveform(samples, duration) {
  wfSamples = samples;
  wfDuration = duration;
  resetWaveformView();
  waveformContainer.style.display = 'block';
  applyZoomVisibility();
  redrawAll();
}

// One classified window of the analysis under way.
function addClassifiedWindow(start, end, isMusic) {
  wfChunks.push({ start, end, isMusic });
  if (wfSamples) redrawAll();
}

// One window identified during the analysis; they can arrive out of order.
function addIdentifiedWindow(start, end, title, artist) {
  wfIdentified = coalesceIdentified([...wfIdentified, { start, end, title, artist }]);
}

// The run's cues, in place of the windows seen during the analysis: one
// contiguous band per cue, rather than the per-window classification's pieces.
function showCuesOnWaveform(cues) {
  wfCues = cues.map(c => ({
    segment_id: c.segment_id,
    start: c.start,
    end: c.start + c.duration,
    duration: c.duration,
    title: c.title || null,
    artist: c.artist || null,
  }));
  wfIdentified = wfCues.filter(c => c.title).map(c => ({ start: c.start, end: c.end }));
  wfChunks = cues.map(c => ({ start: c.start, end: c.start + c.duration, isMusic: true }));
  if (wfSamples) redrawAll();
}

function resetWaveformView() {
  wfZoom = 1;
  if (waveformScroll) waveformScroll.scrollLeft = 0;
  if (wfMarker) wfMarker.classList.remove('active');
  if (markerTimer) { clearTimeout(markerTimer); markerTimer = null; }
}

// Resize observer for debounced redraw + zoom-cap re-clamp
window.addEventListener('resize', () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    drawFilmstrip();
    if (!wfSamples) return;
    const max = effectiveMaxZoom();
    if (wfZoom > max) wfZoom = max;
    redrawAll();
  }, 100);
});

function rebuildMergedMusic() {
  cachedMergedMusic = [];
  for (const c of [...wfChunks].filter(c => c.isMusic).sort((a, b) => a.start - b.start)) {
    const last = cachedMergedMusic[cachedMergedMusic.length - 1];
    if (last && c.start <= last.end) { last.end = Math.max(last.end, c.end); }
    else { cachedMergedMusic.push({ start: c.start, end: c.end }); }
  }
}

// Merge live identification results (which may arrive out of order under
// concurrent identification) into ordered, same-title runs.
function coalesceIdentified(list) {
  const GAP_TOLERANCE = 30;
  const out = [];
  for (const c of [...list].sort((a, b) => a.start - b.start)) {
    const last = out[out.length - 1];
    if (last && last.title === c.title && (c.start - last.end) <= GAP_TOLERANCE) {
      last.end = Math.max(last.end, c.end);
    } else {
      out.push({ start: c.start, end: c.end, title: c.title, artist: c.artist || null });
    }
  }
  return out;
}

function colorAt(t) {
  for (const r of wfIdentified) {
    if (t >= r.start && t < r.end) return '#16a34a';
  }
  for (const r of cachedMergedMusic) {
    if (t >= r.start && t < r.end) return '#7c3aed';
  }
  for (const c of wfChunks) {
    if (!c.isMusic && t >= c.start && t < c.end) return '#ddd6fe';
  }
  return '#ede9fe';
}

function effectiveMaxZoom() {
  const scrollW = waveformScroll.clientWidth || 1;
  const dpr = window.devicePixelRatio || 1;
  const pxLimit = 16000;
  const cap = pxLimit / (scrollW * dpr);
  return Math.max(WF_ZOOM_MIN, Math.min(WF_ZOOM_MAX_HARD, cap));
}

function redrawWaveform(canvas) {
  const scrollW = waveformScroll.clientWidth || canvas.parentElement.clientWidth || 1;
  canvas.style.width = (scrollW * wfZoom) + 'px';
  updatePlayhead();

  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const n = wfSamples.length;
  const barW = W / n;

  ctx.fillStyle = '#d1d5db';
  for (let t = 30; t < wfDuration; t += 30) {
    const x = Math.round((t / wfDuration) * W);
    ctx.fillRect(x, 0, 1, H);
  }

  for (let i = 0; i < n; i++) {
    const t = (i / n) * wfDuration;
    const barH = Math.max(1, wfSamples[i] * H);
    const x = i * barW;
    ctx.fillStyle = colorAt(t);
    ctx.fillRect(x, (H - barH) / 2, Math.max(1, barW - 0.5), barH);
  }

  ctx.fillStyle = '#a09cb8';
  ctx.font = '9px sans-serif';
  ctx.textAlign = 'center';
  for (let t = 30; t < wfDuration; t += 30) {
    const x = (t / wfDuration) * W;
    ctx.fillRect(x - 0.5, H - 8, 1, 8);
    const label = t >= 60 ? `${Math.floor(t/60)}m${t%60?String(t%60).padStart(2,'0')+'s':''}` : `${t}s`;
    ctx.fillText(label, x, H - 10);
  }
}

function redrawMinimap() {
  if (!wfSamples || wfDuration === 0) return;
  const ctx = minimapCanvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = minimapCanvas.clientWidth;
  const H = minimapCanvas.clientHeight;
  if (W === 0 || H === 0) return;
  minimapCanvas.width = W * dpr;
  minimapCanvas.height = H * dpr;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const n = wfSamples.length;
  const barW = W / n;
  for (let i = 0; i < n; i++) {
    const t = (i / n) * wfDuration;
    const barH = Math.max(1, wfSamples[i] * H);
    ctx.fillStyle = colorAt(t);
    ctx.fillRect(i * barW, (H - barH) / 2, Math.max(1, barW), barH);
  }

  // Viewport rectangle
  const fullW = waveformCanvas.clientWidth || (W * wfZoom);
  if (fullW > 0) {
    const scrollW = waveformScroll.clientWidth;
    const vx = (waveformScroll.scrollLeft / fullW) * W;
    const vw = Math.max(2, (scrollW / fullW) * W);
    const atFull = wfZoom <= 1.001;
    ctx.fillStyle = atFull ? 'rgba(124,58,237,0.05)' : 'rgba(124,58,237,0.12)';
    ctx.fillRect(vx, 0, vw, H);
    ctx.strokeStyle = atFull ? 'rgba(124,58,237,0.35)' : 'rgba(124,58,237,0.9)';
    ctx.lineWidth = atFull ? 1 : 1.5;
    ctx.strokeRect(vx + 0.5, 0.5, vw - 1, H - 1);
  }
}

function redrawAll() {
  if (!wfSamples) return;
  rebuildMergedMusic();
  redrawWaveform(waveformCanvas);
  redrawMinimap();
  updateZoomUI();
}

function updateZoomUI() {
  zoomLabel.textContent = wfZoom.toFixed(1) + '×';
  const max = effectiveMaxZoom();
  zoomInBtn.disabled = wfZoom >= max - 0.01;
  zoomOutBtn.disabled = wfZoom <= WF_ZOOM_MIN + 0.01;
  zoomResetBtn.disabled = wfZoom <= WF_ZOOM_MIN + 0.01;
}

// setZoom: newZoom = target zoom; anchorFraction = 0..1 position on full timeline
// that should remain at cursorOffset px from left edge of viewport (defaults to centered).
function setZoom(newZoom, anchorFraction, cursorOffset) {
  if (!wfSamples) return;
  const max = effectiveMaxZoom();
  newZoom = Math.max(WF_ZOOM_MIN, Math.min(max, newZoom));
  const scrollW = waveformScroll.clientWidth;
  if (anchorFraction == null) anchorFraction = 0.5;
  if (cursorOffset == null) cursorOffset = scrollW / 2;
  wfZoom = newZoom;
  redrawWaveform(waveformCanvas);
  const fullW = waveformCanvas.clientWidth;
  const target = anchorFraction * fullW - cursorOffset;
  waveformScroll.scrollLeft = Math.max(0, Math.min(Math.max(0, fullW - scrollW), target));
  redrawMinimap();
  updateZoomUI();
}

function applyZoomVisibility() {
  // Show/hide zoom controls + minimap based on whether zooming is meaningful.
  const showZoom = !!wfSamples && wfDuration > 30 && wfSamples.length > 80;
  zoomControls.style.display = showZoom ? 'flex' : 'none';
  minimapCanvas.style.display = showZoom ? 'block' : 'none';
  if (!showZoom && wfZoom !== 1) {
    wfZoom = 1;
    if (wfSamples) redrawWaveform(waveformCanvas);
  }
}

// Zoom controls
zoomInBtn.addEventListener('click', () => setZoom(wfZoom * 1.5, 0.5, waveformScroll.clientWidth / 2));
zoomOutBtn.addEventListener('click', () => setZoom(wfZoom / 1.5, 0.5, waveformScroll.clientWidth / 2));
zoomResetBtn.addEventListener('click', () => setZoom(1, 0, 0));

waveformScroll.addEventListener('wheel', e => {
  if (!(e.ctrlKey || e.metaKey)) return;
  if (!wfSamples) return;
  e.preventDefault();
  const fullW = waveformCanvas.clientWidth;
  if (fullW === 0) return;
  const rect = waveformScroll.getBoundingClientRect();
  const cursorOffset = e.clientX - rect.left;
  const fraction = (waveformScroll.scrollLeft + cursorOffset) / fullW;
  const factor = Math.exp(-e.deltaY * 0.0015);
  setZoom(wfZoom * factor, fraction, cursorOffset);
}, { passive: false });

waveformScroll.addEventListener('scroll', () => {
  if (!wfSamples) return;
  redrawMinimap();
});

// Drag-pan on main waveform
let panState = null;  // {startX, startScroll, dragged}
waveformScroll.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  if (!wfSamples) return;
  panState = { startX: e.clientX, startScroll: waveformScroll.scrollLeft, dragged: false };
  suppressNextClick = false;
});
window.addEventListener('mousemove', e => {
  if (!panState) return;
  const dx = e.clientX - panState.startX;
  if (Math.abs(dx) > 4) {
    if (!panState.dragged) {
      panState.dragged = true;
      waveformScroll.classList.add('grabbing');
      hideWfTooltip();
    }
    waveformScroll.scrollLeft = panState.startScroll - dx;
  }
});
window.addEventListener('mouseup', () => {
  if (!panState) return;
  if (panState.dragged) {
    suppressNextClick = true;
    waveformScroll.classList.remove('grabbing');
  }
  panState = null;
});

// Minimap drag/click to recenter
function minimapJump(clientX) {
  if (!wfSamples) return;
  const rect = minimapCanvas.getBoundingClientRect();
  const fraction = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  const fullW = waveformCanvas.clientWidth;
  const scrollW = waveformScroll.clientWidth;
  const target = fraction * fullW - scrollW / 2;
  waveformScroll.scrollLeft = Math.max(0, Math.min(Math.max(0, fullW - scrollW), target));
}

let miniDragging = false;
minimapCanvas.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  miniDragging = true;
  minimapJump(e.clientX);
});
window.addEventListener('mousemove', e => {
  if (!miniDragging) return;
  minimapJump(e.clientX);
});
window.addEventListener('mouseup', () => { miniDragging = false; });

// Click on waveform → scroll to and flash result row
waveformCanvas.addEventListener('click', e => {
  if (suppressNextClick) { suppressNextClick = false; return; }
  if (!wfSamples || wfDuration === 0) return;
  const rect = waveformCanvas.getBoundingClientRect();
  const t = ((e.clientX - rect.left) / rect.width) * wfDuration;
  seekVideo(t);
  if (wfCues.length === 0) return;
  const cue = wfCues.find(c => t >= c.start && t < c.end);
  if (!cue || !cue.segment_id) return;
  const row = resultsBody.querySelector(`tr[data-segment-id="${cue.segment_id}"]`);
  if (!row) return;
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  row.classList.remove('cue-flash');
  void row.offsetWidth;
  row.classList.add('cue-flash');
});

// Focus a cue on the timeline: zoom out enough to see it, pan to center, show marker.
function focusCueOnTimeline(cue) {
  if (!wfSamples || wfDuration === 0) return;
  const midT = cue.start + (cue.duration || (cue.end - cue.start)) / 2;
  const dur = (cue.duration || (cue.end - cue.start)) || 1;
  const fraction = midT / wfDuration;
  // Keep the user's current zoom level; just pan to centre the cue (a no-op at
  // 1×, where the whole timeline is already visible) and highlight its band.
  setZoom(wfZoom, fraction, waveformScroll.clientWidth / 2);

  const fullW = waveformCanvas.clientWidth;
  if (fullW > 0) {
    const left = (cue.start / wfDuration) * fullW;
    const width = Math.max(3, (dur / wfDuration) * fullW);
    wfMarker.style.left = left + 'px';
    wfMarker.style.width = width + 'px';
    wfMarker.classList.remove('active');
    void wfMarker.offsetWidth;
    wfMarker.classList.add('active');
    if (markerTimer) clearTimeout(markerTimer);
    markerTimer = setTimeout(() => { wfMarker.classList.remove('active'); markerTimer = null; }, 2500);
  }
  seekVideo(cue.start);
}
