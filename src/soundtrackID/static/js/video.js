// The video preview and its filmstrip, synced to the waveform.

let filmstripSprite   = null;   // loaded sprite Image
let filmstripMeta     = null;   // {count, thumb_width, thumb_height}
let hasVideo          = false;
let activeSegmentEnd  = null;   // when playing a cue, pause the video at this time

// Collapse / expand the video preview (chevron rotation handled in CSS).
function setVideoCollapsed(collapsed) {
  videoWrap.classList.toggle('collapsed', collapsed);
  videoToggle.setAttribute('aria-expanded', String(!collapsed));
}
videoToggle.addEventListener('click', () => setVideoCollapsed(!videoWrap.classList.contains('collapsed')));

// ── Video preview (shared player synced to the timeline) ──────────────────────
function setupVideo(results, videoName) {
  const v = results && results.video;
  if (v && videoName) {
    hasVideo = true;
    videoEl.src = `/output/${encodeURIComponent(videoName)}/video`;
    videoWrap.style.display = 'block';
    setVideoCollapsed(false);
    if (v.filmstrip) {
      filmstripMeta = v.filmstrip;
      filmstripSprite = new Image();
      filmstripSprite.onload = drawFilmstrip;
      filmstripSprite.src = `/output/${encodeURIComponent(videoName)}/filmstrip`;
      filmstripWrap.style.display = 'block';
    } else {
      filmstripMeta = null; filmstripSprite = null;
      filmstripWrap.style.display = 'none';
    }
  } else {
    hasVideo = false;
    videoWrap.style.display = 'none';
    // Stop playback (removing src alone doesn't halt in-progress audio) and
    // reset the element with load().
    videoEl.pause();
    videoEl.removeAttribute('src');
    videoEl.load();
    filmstripMeta = null; filmstripSprite = null;
    filmstripWrap.style.display = 'none';
    wfPlayhead.style.display = 'none';
    overviewPlayhead.style.display = 'none';
  }
}

function seekVideo(t, play = true) {
  if (!hasVideo || !isFinite(t)) return;
  activeSegmentEnd = null;   // a plain seek carries no stop bound
  try { videoEl.currentTime = Math.max(0, t); } catch (_) {}
  if (play) { const p = videoEl.play(); if (p && p.catch) p.catch(() => {}); }
  updatePlayhead();
}

// Play one detected cue in the shared video player: seek to its start, show the
// image track, and stop automatically at the segment end.
function playSegment(cue) {
  if (!hasVideo) return;
  setVideoCollapsed(false);
  seekVideo(cue.start, true);
  activeSegmentEnd = cue.start + cue.duration;
}

function updatePlayhead() {
  if (!hasVideo || !wfSamples || wfDuration === 0) {
    wfPlayhead.style.display = 'none';
    overviewPlayhead.style.display = 'none';
    return;
  }
  const frac = Math.min(videoEl.currentTime, wfDuration) / wfDuration;
  wfPlayhead.style.left = (frac * waveformCanvas.clientWidth) + 'px';
  wfPlayhead.style.display = 'block';
  // Overview playhead spans the minimap + filmstrip (both full-width = duration).
  if (filmstripWrap.style.display !== 'none') {
    overviewPlayhead.style.left = (frac * overviewWrap.clientWidth) + 'px';
    overviewPlayhead.style.display = 'block';
  } else {
    overviewPlayhead.style.display = 'none';
  }
}

// Full-width overview strip (like the minimap): a FIXED number of frames spanning
// the whole movie, drawn cover-cropped so nothing stretches. A longer movie just
// means coarser time-spacing, never smaller/squeezed thumbnails.
const FILMSTRIP_H = 54;
const FILMSTRIP_COUNT = 20;    // fixed number of thumbnails shown across the width
function drawFilmstrip() {
  if (!filmstripSprite || !filmstripMeta || filmstripWrap.style.display === 'none') return;
  const W = filmstripCanvas.clientWidth;
  if (W === 0) return;
  const dpr = window.devicePixelRatio || 1;
  filmstripCanvas.width = W * dpr;
  filmstripCanvas.height = FILMSTRIP_H * dpr;
  const ctx = filmstripCanvas.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, FILMSTRIP_H);
  const tw = filmstripMeta.thumb_width, th = filmstripMeta.thumb_height, count = filmstripMeta.count;
  const nCells = Math.max(1, Math.min(count, FILMSTRIP_COUNT));
  const cellW = W / nCells;
  for (let i = 0; i < nCells; i++) {
    const srcIdx = Math.min(count - 1, Math.floor((i + 0.5) / nCells * count));
    const sx = srcIdx * tw;
    const scale = Math.max(cellW / tw, FILMSTRIP_H / th);  // cover
    const dw = tw * scale, dh = th * scale;
    const dx = i * cellW + (cellW - dw) / 2, dy = (FILMSTRIP_H - dh) / 2;
    ctx.save();
    ctx.beginPath();
    ctx.rect(i * cellW, 0, cellW, FILMSTRIP_H);
    ctx.clip();
    ctx.drawImage(filmstripSprite, sx, 0, tw, th, dx, dy, dw, dh);
    ctx.restore();
  }
}

videoEl.addEventListener('timeupdate', () => {
  updatePlayhead();
  // Stop at the end of a cue that was launched via its Play button.
  if (activeSegmentEnd != null && videoEl.currentTime >= activeSegmentEnd) {
    videoEl.pause();
    activeSegmentEnd = null;
  }
  // While playing and zoomed, keep the waveform playhead within view.
  if (!videoEl.paused && wfZoom > 1 && wfDuration > 0) {
    const fullW = waveformCanvas.clientWidth;
    const scrollW = waveformScroll.clientWidth;
    const x = (videoEl.currentTime / wfDuration) * fullW;
    if (x < waveformScroll.scrollLeft + 40 || x > waveformScroll.scrollLeft + scrollW - 40) {
      waveformScroll.scrollLeft = Math.max(0, Math.min(Math.max(0, fullW - scrollW), x - scrollW / 2));
    }
  }
});

// Click the filmstrip → seek the video there.
filmstripCanvas.addEventListener('click', e => {
  if (!wfDuration) return;
  const rect = filmstripCanvas.getBoundingClientRect();
  const t = ((e.clientX - rect.left) / rect.width) * wfDuration;
  seekVideo(t);
});

// Space plays / pauses the video from anywhere on the page, except while typing
// in a field or when the player has focus (its own controls handle Space then).
document.addEventListener('keydown', e => {
  if (e.code !== 'Space' || !hasVideo) return;
  const el = e.target;
  if (el === videoEl || el.isContentEditable || el.closest('input, textarea, select')) return;
  e.preventDefault();   // no page scroll, no click on a focused button
  if (e.repeat) return;
  if (videoEl.paused) { const p = videoEl.play(); if (p && p.catch) p.catch(() => {}); }
  else videoEl.pause();
});
