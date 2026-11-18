// The Tracks, Timeline and Classes views, and editing the tracks.

// ── Tracks report (grouped): one row per unique piece with full metadata + rights.
function buildTrackRowHTML(group) {
  const cue = group.rep;
  const enr = cue.enrichment || {};
  const cover = cue.photo_url ? `<img class="cover-thumb" src="${fmt(cue.photo_url)}" alt="">` : '';
  const uncertain = enr.composer_match && enr.composer_match !== 'isrc';
  const cls = uncertain ? ' class="composer-uncertain"' : '';
  // Date column: the work's real original year (Wikidata) when known, else the
  // provider's release year in grey - a reissue date, so only a hint.
  let dateYear = '', dateCls = '';
  if (enr.original_year != null) {
    dateYear = enr.original_year;
  } else if (cue.release_year != null) {
    dateYear = cue.release_year;
    dateCls = ' style="color:#9ca3af"';
  }
  const expander = cueHasDetail(cue)
    ? '<button type="button" class="vote-toggle" title="Show details">▸</button>'
    : '';
  const composerCls = 'editable' + (uncertain ? ' composer-uncertain' : '');
  const temporalBadge = cue.temporal_pick
    ? '<span class="temporal-badge" title="Re-ranked to a period-plausible match: the top result postdated the film, so an earlier candidate was chosen.">↻</span>'
    : '';
  const links = platformLinkIcons(cue);
  return `
    <td class="expand-cell">${expander}</td>
    <td class="editable" data-field="title" title="Double-click to edit"><div class="title-cell">${cover}<span class="title-text">${fmt(cue.title)}${temporalBadge}</span>${links ? `<span class="track-links">${links}</span>` : ''}</div></td>
    <td class="editable" data-field="artist" title="Double-click to edit">${fmt(cue.artist)}</td>
    <td class="${composerCls}" data-field="composer" title="Double-click to edit">${fmt(enr.composer)}${authorsInlineHTML(enr)}</td>
    <td class="editable" data-field="date" title="Double-click to edit"${dateCls}>${fmt(dateYear)}</td>
    <td class="editable col-album" data-field="album" title="${fmt(cue.album)}">${fmt(cue.album)}</td>
    <td class="conf-cell">${confBadgeHTML(cue)}</td>
    <td class="rights-cell">${rightsPillHTML(enr)}</td>
    <td class="row-actions"><button type="button" class="track-remove" title="Remove track">×</button></td>`;
}

// Read a track field's underlying value (for prefilling the inline editor).
function trackFieldValue(cue, field) {
  const enr = cue.enrichment || {};
  if (field === 'title') return cue.title || '';
  if (field === 'artist') return cue.artist || '';
  if (field === 'album') return cue.album || '';
  if (field === 'composer') return enr.composer || '';
  if (field === 'date') return enr.original_year != null ? enr.original_year : '';
  return '';
}

// Write an edited field to every cue of the group, then persist + re-render.
// What follows from the edit (a hand-edited composer's author entry and the
// rights verdict, the track, whether it postdates the film) the server works out
// when it saves; saveCues shows its answer.
function applyTrackEdit(group, field, raw) {
  const val = String(raw).trim();
  group.cues.forEach(cue => {
    if (field === 'title') cue.title = val || null;
    else if (field === 'artist') cue.artist = val || null;
    else if (field === 'album') cue.album = val || null;
    else if (field === 'composer') { cue.enrichment = cue.enrichment || {}; cue.enrichment.composer = val || null; }
    else if (field === 'date') { cue.enrichment = cue.enrichment || {}; cue.enrichment.original_year = val ? (parseInt(val, 10) || null) : null; }
  });
  persistAndRerender();
}

function removeTrack(group) {
  if (resumeRunning) return;
  const ids = new Set(group.cues.map(c => c.segment_id));
  currentResults.cues = (currentResults.cues || []).filter(c => !ids.has(c.segment_id));
  persistAndRerender();
}

// Turn a cell into an inline text editor; Enter/blur commits, Esc cancels.
function startCellEdit(td, group) {
  if (resumeRunning || td.querySelector('input')) return;
  const field = td.dataset.field;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'cell-edit';
  td.innerHTML = '';
  td.appendChild(input);
  input.value = trackFieldValue(group.rep, field);
  input.focus();
  input.select();
  let done = false;
  const commit = () => { if (done) return; done = true; applyTrackEdit(group, field, input.value); };
  const cancel = () => { if (done) return; done = true; rerenderResults(); };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
  });
  input.addEventListener('blur', commit);
}

function attachTrackRow(tr, group) {
  // Clicking a track row expands its authorship/copyright/providers detail panel.
  function toggleDetail() {
    const det = tr.nextElementSibling;
    if (!det || !det.classList.contains('cue-detail')) return;
    det.hidden = !det.hidden;
    tr.classList.toggle('detail-open', !det.hidden);
    const vt = tr.querySelector('.vote-toggle');
    if (vt) { vt.textContent = det.hidden ? '▸' : '▾'; vt.classList.toggle('open', !det.hidden); }
  }
  tr.addEventListener('click', e => {
    // Editable cells (edit on double-click), the remove control, and links handle
    // their own interaction; a plain click elsewhere toggles the detail panel.
    if (e.target.closest('a, .editable, .track-remove, input')) return;
    toggleDetail();
  });
  tr.querySelectorAll('.editable').forEach(td => {
    td.addEventListener('dblclick', e => { e.stopPropagation(); startCellEdit(td, group); });
  });
  const rm = tr.querySelector('.track-remove');
  if (rm) rm.addEventListener('click', e => { e.stopPropagation(); removeTrack(group); });
}

// Persist the current cues to disk and re-render both tabs from currentResults.
function persistAndRerender() {
  rerenderResults();
  saveCues();
}

function rerenderResults() {
  const cues = (currentResults && currentResults.cues) || [];
  renderTracks(groupCues(cues), currentVideoName);
  renderTimeline(cues, currentVideoName);
  switchResultsView(currentResultsView);
  updateAnachronismToolbar(cues);
}

let saveSeq = 0;  // the latest save: only its answer is shown

async function saveCues() {
  if (!currentVideoName) return;
  const seq = ++saveSeq;
  try {
    const r = await fetch(`/results/${encodeURIComponent(currentVideoName)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cues: currentResults.cues || [] }),
    });
    if (!r.ok) throw new Error('server returned ' + r.status);
    const { results } = await r.json();
    // The cues as the server saved them, with what follows from the edit. Copied
    // onto the cues shown (an open cell editor holds them), unless another edit
    // has been saved since; re-rendered once no cell is being edited.
    if (seq !== saveSeq || !results) return;
    const saved = new Map(results.cues.map(c => [c.segment_id, c]));
    (currentResults.cues || []).forEach(c => Object.assign(c, saved.get(c.segment_id)));
    if (!document.querySelector('input.cell-edit')) rerenderResults();
  } catch (err) {
    errorArea.textContent = 'Could not save changes: ' + err.message;
    errorArea.style.display = 'block';
  }
}

function renderTracks(groups, videoName) {
  const body = document.getElementById('tracks-body');
  body.innerHTML = '';
  if (groups.length === 0) {
    body.innerHTML = '<tr><td colspan="9" class="tracks-empty">No tracks identified.</td></tr>';
    return;
  }
  groups.forEach(group => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.innerHTML = buildTrackRowHTML(group);
    tr.classList.toggle('anachronistic', group.anachronistic);
    attachTrackRow(tr, group);
    body.appendChild(tr);
    if (cueHasDetail(group.rep)) {
      const det = document.createElement('tr');
      det.className = 'cue-detail';
      det.hidden = true;
      det.innerHTML = detailPanelHTML(group.rep);
      body.appendChild(det);
    }
  });
}

// Timeline (chronological): the raw detection sequence, minimal, for oversight.
function buildTimelineRowHTML(cue, videoName) {
  const anaBadge = cue.anachronistic
    ? '<span class="anachronism-badge" title="Later than the film; likely a later work, not the original score.">⚠</span>'
    : '';
  const links = platformLinkIcons(cue);
  const choose = cue.title
    ? '<button type="button" class="sc-button song-choose-btn" title="Every song the providers found in this segment, to choose from">Alternatives</button>'
    : '';
  return `
    <td><span class="badge music">${+cue.segment_id.replace('seg_', '')}</span></td>
    <td>${fmtTime(cue.start)}</td>
    <td>${fmtTime(cue.duration)}</td>
    <td><div class="title-cell"><span class="title-text">${fmt(cue.title)}${anaBadge}</span>${links ? `<span class="track-links">${links}</span>` : ''}</div></td>
    <td>${fmt(cue.artist)}</td>
    <td>${choose}</td>`;
}

// Clicking a timeline row plays that segment in the shared player (stops at its end).
function attachTimelineRow(tr, cue) {
  tr.addEventListener('click', e => {
    if (e.target.closest('a')) return;
    if (e.target.closest('.song-choose-btn')) { openSongChoose(cue); return; }
    const wfCue = wfCues.find(c => c.segment_id === cue.segment_id);
    focusCueOnTimeline(wfCue || { start: cue.start, duration: cue.duration, end: cue.start + cue.duration });
    playSegment(cue);
  });
}

function renderTimeline(cues, videoName) {
  resultsBody.innerHTML = '';
  cues.forEach(cue => {
    const tr = document.createElement('tr');
    tr.dataset.segmentId = cue.segment_id;
    tr.style.cursor = 'pointer';
    tr.innerHTML = buildTimelineRowHTML(cue, videoName);
    tr.classList.toggle('anachronistic', Boolean(cue.anachronistic));
    attachTimelineRow(tr, cue);
    resultsBody.appendChild(tr);
  });
}

// Choosing a segment's song among those the providers found in it (its
// alternatives), or typing one in. Only that segment changes; the server looks
// its composer up again and answers with the results as saved.
const songChoose = document.getElementById('song-choose');
const scSongs = document.getElementById('sc-songs');
const scTyped = document.getElementById('sc-typed');
const scStatus = document.getElementById('sc-status');
const scPlay = document.getElementById('sc-play');
let songChooseCue = null;

// The Play button pauses while the video runs, however it was started.
function updateScPlay() { scPlay.textContent = videoEl.paused ? '▶ Play' : '❚❚ Pause'; }
videoEl.addEventListener('play', updateScPlay);
videoEl.addEventListener('pause', updateScPlay);

function isCueSong(cue, song) {
  return song.title === cue.title && song.artist === cue.artist && (song.isrc || null) === (cue.isrc || null);
}

function songChoiceHTML(cue, song, index) {
  const current = isCueSong(cue, song);
  const details = [
    song.album,
    song.release_year,
    song.isrc && `ISRC ${song.isrc}`,
    song.score != null && `score ${song.score}`,
    song.windows > 1 && `in ${song.windows} windows`,
  ].filter(Boolean).map(fmt).join(' · ');
  const later = song.anachronistic
    ? '<span class="anachronism-badge" title="Released well after the film: likely a later recording.">⚠ later</span>'
    : '';
  const action = current
    ? '<span class="sc-current">Current</span>'
    : `<button type="button" class="sc-button sc-use" data-index="${index}">Use</button>`;
  return `<li class="sc-song${current ? ' current' : ''}">`
       + `<span class="sc-text"><span><strong>${fmt(song.title)}</strong>${song.artist ? ' – ' + fmt(song.artist) : ''}${later}</span>`
       + `<span class="sc-details">${details}</span></span>`
       + `<span class="track-links">${platformLinkIcons(song)}</span>${action}</li>`;
}

// The songs by provider, each in the order the provider named them.
function renderSongChoices(cue) {
  const songs = (cue.alternatives || []).map((song, index) => ({ song, index }));
  if (!songs.length) {
    scSongs.innerHTML = '<p class="sc-none">This run kept no answers of the providers for this segment. Analyze the film again to list them.</p>';
    return;
  }
  const providers = [...new Set(songs.map(s => s.song.provider))];
  scSongs.innerHTML = providers.map(p => {
    const items = songs.filter(s => s.song.provider === p).map(s => songChoiceHTML(cue, s.song, s.index));
    return `<h4>${fmt(PROVIDER_LABELS[p] || p || 'Provider')}</h4><ul class="sc-list">${items.join('')}</ul>`;
  }).join('');
}

function openSongChoose(cue) {
  if (resumeRunning) return;
  songChooseCue = cue;
  document.getElementById('sc-segment').textContent =
    `Segment ${+cue.segment_id.replace('seg_', '')} · ${fmtTime(cue.start)} – ${fmtTime(cue.start + cue.duration)}`;
  scPlay.hidden = !hasVideo;
  updateScPlay();
  renderSongChoices(cue);
  scTyped.reset();
  scStatus.textContent = '';
  songChoose.showModal();
}

async function chooseSong(choice) {
  const cue = songChooseCue;
  const controls = songChoose.querySelectorAll('button, input');
  controls.forEach(el => { el.disabled = true; });
  scStatus.textContent = 'Saving, and looking up the composer…';
  try {
    const r = await fetch(`/results/${encodeURIComponent(currentVideoName)}/cues/${encodeURIComponent(cue.segment_id)}/song`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(choice),
    });
    if (!r.ok) throw new Error('server returned ' + r.status);
    const { results } = await r.json();
    Object.assign(cue, results.cues.find(c => c.segment_id === cue.segment_id));
    songChoose.close();
    rerenderResults();
  } catch (err) {
    scStatus.textContent = 'Could not change the song: ' + err.message;
  } finally {
    controls.forEach(el => { el.disabled = false; });
  }
}

scSongs.addEventListener('click', e => {
  const use = e.target.closest('.sc-use');
  if (use) chooseSong({ alternative: Number(use.dataset.index) });
});
scTyped.addEventListener('submit', e => {
  e.preventDefault();
  chooseSong({
    title: document.getElementById('sc-title').value,
    artist: document.getElementById('sc-artist').value,
  });
});
// Pause; or play the segment: on from where it was paused in it, else from its
// start, stopping at its end either way.
scPlay.addEventListener('click', () => {
  if (!videoEl.paused) { videoEl.pause(); return; }
  const cue = songChooseCue, end = cue.start + cue.duration;
  if (videoEl.currentTime >= cue.start && videoEl.currentTime < end) {
    activeSegmentEnd = end;
    const p = videoEl.play(); if (p && p.catch) p.catch(() => {});
  } else {
    playSegment(cue);
  }
});
document.getElementById('sc-close').addEventListener('click', () => songChoose.close());

// Classes: each classification window's music score and top 3 classes, to trace
// where a wrong music / no-music verdict comes from. Music windows are badged.
// A run's timeline (waveform, music classification) is a file of its own.
async function loadTimeline(videoName) {
  try {
    const res = await fetch(`/output/${encodeURIComponent(videoName)}/timeline`);
    return res.ok ? await res.json() : {};
  } catch (_) {
    return {};
  }
}

function renderClasses(windows) {
  const body = document.getElementById('classes-body');
  if (!windows) {
    body.innerHTML = '<tr><td colspan="4" class="tracks-empty">No class data for this run.</td></tr>';
    return;
  }
  const pct = p => Math.round(p * 100) + '%';
  body.innerHTML = '';
  windows.forEach(w => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.innerHTML = `
      <td>${fmtTime(w.start)}</td>
      <td>${fmtTime(w.end)}</td>
      <td>${w.is_music ? `<span class="badge music">${pct(w.music)}</span>` : pct(w.music)}</td>
      <td>${Object.entries(w.classes).map(([name, p]) => `${fmt(name)} ${pct(p)}`).join(' · ')}</td>`;
    // Like a timeline row: show the window on the timeline and play it, then
    // scroll up to the player so its picture is in view.
    tr.addEventListener('click', () => {
      const span = { start: w.start, end: w.end, duration: w.end - w.start };
      focusCueOnTimeline(span);
      playSegment(span);
      videoWrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    body.appendChild(tr);
  });
}

let currentResultsView = 'tracks';
function switchResultsView(view) {
  currentResultsView = view;
  document.querySelectorAll('.results-tab').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  document.getElementById('tracks-view').style.display = view === 'tracks' ? '' : 'none';
  document.getElementById('timeline-view').style.display = view === 'timeline' ? '' : 'none';
  document.getElementById('classes-view').style.display = view === 'classes' ? '' : 'none';
}
document.getElementById('results-tabs').addEventListener('click', e => {
  const btn = e.target.closest('.results-tab');
  if (btn) switchResultsView(btn.dataset.view);
});

// Click the Album header to hide/show that (often long) column.
document.getElementById('album-header').addEventListener('click', () => {
  document.getElementById('tracks-table').classList.toggle('hide-album');
});
