// Recent runs: the runs, and the batches, each showing its films under it and
// opening its page (batch.js).

const DELETE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="3,6 5,6 21,6"/><path d="M19,6l-1,14H6L5,6"/><path d="M10,11v6M14,11v6"/><path d="M9,6V4h6v2"/></svg>';
// What became of a batch's film that has no run; "failed" shows why.
const FILM_STATUS = {
  queued: 'Queued',
  analyzing: 'Analyzing…',
  cancelled: 'Cancelled',
  not_analyzed: 'Not analyzed',
  deleted: 'Run deleted',
};
const openBatches = new Set();   // the batches showing their films, by name

async function loadHistory() {
  const panel = document.getElementById('history-panel');
  const list = document.getElementById('history-list');
  try {
    const entries = await fetch('/history').then(r => r.json());
    if (!entries.length) { panel.style.display = 'none'; return; }
    list.replaceChildren(...entries.flatMap(historyRows));
    panel.style.display = 'block';
    showHistorySize();
  } catch (_) { panel.style.display = 'none'; }
}

// How much the runs take on disk, next to "Delete all runs".
async function showHistorySize() {
  const label = document.getElementById('history-size');
  try {
    const size = await fetch('/history/size').then(r => r.ok ? r.json() : Promise.reject());
    label.textContent = `${formatSize(size.bytes)} on disk`;
    label.title = `In ${size.folder}`;
  } catch (_) { label.textContent = ''; }
}

// "12.3 GB", counted in 1024s, as Windows Explorer does.
function formatSize(bytes) {
  const units = ['KB', 'MB', 'GB', 'TB'];
  if (bytes < 1024) return `${bytes} bytes`;
  let i = -1;
  do { bytes /= 1024; i++; } while (bytes >= 1024 && i < units.length - 1);
  return `${bytes.toFixed(1)} ${units[i]}`;
}

// The rows of an entry of GET /history.
function historyRows(entry) {
  if (entry.kind === 'batch') return batchRows(entry);
  if (entry.kind === 'interrupted') return [interruptedRow(entry)];
  if (entry.kind === 'analyzing') return [analyzingRow(entry)];
  return [runRow(entry)];
}

// A row of the list. With `onDelete`, it is one to click, and deletes with
// its button, titled `deleteTitle`.
function historyRow(name, meta, deleteTitle = null, onDelete = null) {
  const row = document.createElement('div');
  row.className = 'history-item';
  row.append(
    Object.assign(document.createElement('span'), { className: 'history-name', textContent: name, title: name }),
    Object.assign(document.createElement('span'), { className: 'history-meta', textContent: meta, title: meta }),
  );
  if (onDelete) {
    const button = Object.assign(document.createElement('button'), { className: 'history-delete-btn', title: deleteTitle, innerHTML: DELETE_ICON });
    button.addEventListener('click', e => { e.stopPropagation(); onDelete(); });
    row.append(button, Object.assign(document.createElement('span'), { className: 'history-chevron', textContent: '›' }));
  }
  return row;
}

function historyTime(timestamp) {
  const d = new Date(timestamp * 1000);
  const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return `${dateStr}, ${timeStr}`;
}

// A run's row, named `name`: it opens the run, of `batch` if one's.
function runRow(run, name = run.video_name, batch = null) {
  const meta = `${historyTime(run.timestamp)} · ${run.total} seg${run.total !== 1 ? 's' : ''}` +
    (run.identified > 0 ? ` · ${run.identified} identified` : '');
  const row = historyRow(name, meta, 'Delete run', () => deleteRun(run.video_name));
  row.classList.add('history-run');
  row.addEventListener('click', () => openRun(run.video_name, batch));
  return row;
}

// A run stopped before it was saved (e.g. the app closed during it): only to
// delete.
function interruptedRow(run) {
  const row = historyRow(run.video_name, `${historyTime(run.timestamp)} · Interrupted`, 'Delete run', () => deleteRun(run.video_name));
  row.classList.add('history-run', 'history-interrupted');
  row.querySelector('.history-chevron').remove();
  return row;
}

// A run not saved yet, being analyzed (maybe by the command line): nothing to
// do with it yet.
function analyzingRow(run) {
  const row = historyRow(run.video_name, `${historyTime(run.timestamp)} · Being analyzed`);
  row.classList.add('history-analyzing');
  return row;
}

async function deleteRun(videoName) {
  try { await fetch('/history/' + encodeURIComponent(videoName), { method: 'DELETE' }); } catch (_) {}
  // The run on screen is gone: back to the start page.
  if (videoName === currentVideoName) showStartPage(); else loadHistory();
}

// A batch's row, which shows or hides the rows of its films under it.
function batchRows(batch) {
  const runs = batch.films.filter(f => f.run).map(f => f.run);
  const identified = runs.reduce((n, run) => n + run.identified, 0);
  const meta = `${historyTime(batch.timestamp)} · ${runs.length} of ${batch.films.length} films` +
    (identified > 0 ? ` · ${identified} identified` : '');
  const name = 'Batch: ' + batch.films.map(f => f.source).join(', ');
  const row = historyRow(name, meta, 'Delete batch', () => deleteBatch(batch.name, runs));
  row.classList.add('history-batch');
  const open = Object.assign(document.createElement('button'), { type: 'button', className: 'history-open-btn', textContent: 'Open', title: "Open the batch's page" });
  open.addEventListener('click', e => { e.stopPropagation(); showBatch(batch.name); });
  row.insertBefore(open, row.querySelector('.history-delete-btn'));
  const films = document.createElement('div');
  films.className = 'history-batch-films';
  films.append(...batch.films.map(film => filmRow(film, batch.name)));
  const show = () => {
    films.hidden = !openBatches.has(batch.name);
    row.classList.toggle('open', !films.hidden);
  };
  row.addEventListener('click', () => {
    if (!openBatches.delete(batch.name)) openBatches.add(batch.name);
    show();
  });
  show();
  return [row, films];
}

// A film's row in its batch: its run's, once done, else what became of it.
function filmRow(film, batch) {
  if (film.run) return runRow(film.run, film.source, batch);
  const status = film.status === 'failed' ? `Failed: ${film.error}` : FILM_STATUS[film.status] || film.status;
  const row = historyRow(film.source, status);
  row.classList.add('history-film');
  return row;
}

async function deleteBatch(name, runs) {
  const what = runs.length === 0 ? '' : runs.length === 1 ? ' and its run' : ` and its ${runs.length} runs`;
  if (!confirm(`Delete this batch${what}? This can't be undone.`)) return;
  const res = await fetch('/batches/' + encodeURIComponent(name), { method: 'DELETE' }).catch(() => null);
  if (res && !res.ok) {
    const { detail } = await res.json().catch(() => ({}));
    alert(typeof detail === 'string' ? detail : `Could not delete the batch (HTTP ${res.status})`);
    return;
  }
  // A run on screen is gone: back to the start page.
  if (runs.some(run => run.video_name === currentVideoName)) showStartPage(); else loadHistory();
}

// Show a saved run: its results, waveform and classes; with "Back to the
// batch" when it is one of `batch`'s. Returns whether it opened.
async function openRun(videoName, batch = null) {
  const res = await fetch('/results/' + encodeURIComponent(videoName));
  if (!res.ok) { alert(`Could not open this run (HTTP ${res.status})`); return false; }
  const results = await res.json();
  const timeline = await loadTimeline(videoName);
  dropZone.style.display = 'none';
  document.getElementById('history-panel').open = false;
  progressView.style.display = 'none';
  resultsView.style.display = 'block';
  const wf = timeline.waveform;
  if (wf) {
    progBars.style.display = 'none';
    showWaveform(wf.samples, wf.duration);
    showCuesOnWaveform(results.cues || []);
  } else {
    waveformContainer.style.display = 'none';   // not the previous run's
  }
  showResults(results);
  renderClasses(timeline.classification_windows);
  shownBatch = batch;
  batchBack.hidden = !batch;
  return true;
}

document.getElementById('delete-all-runs').addEventListener('click', async () => {
  const n = document.querySelectorAll('.history-run').length;
  if (!confirm(`Delete ${n === 1 ? 'the 1 run' : `all ${n} runs`}? This can't be undone.`)) return;
  try { await fetch('/history', { method: 'DELETE' }); } catch (_) {}
  showStartPage();
});
