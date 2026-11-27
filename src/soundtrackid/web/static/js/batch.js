// A batch's page: its films and what became of each, each one done to open,
// with the progress of the one under way below them while the batch runs. Once
// it ended, what else it offers: its results to download, its films not done
// to continue, its runs' pending cues to resume.

const batchQueue    = document.getElementById('batch-queue');
const batchHeading  = document.getElementById('batch-heading');
const batchFilms    = document.getElementById('batch-films');
const batchResume   = document.getElementById('batch-resume');
const batchContinue = document.getElementById('batch-continue');
const batchDownload = document.getElementById('batch-download');
const batchBack     = document.getElementById('batch-back');   // a run's "Back to the batch"
let shownBatch      = null;       // the batch whose page or run shows, by name
let batchProviders  = [];         // its providers, whose keys continuing or resuming takes
let batchFilmShown  = -1;         // the film whose analysis shows, by its place
let resuming        = null;       // the run whose pending cues are resumed: {run, text, status}
let batchLoads      = 0;          // loadBatch's latest: an earlier one's answer is dropped
const runCounts     = new Map();  // a done film's run → its counts, once read

// Analyze several staged films one after another as a batch: `films` are
// each {stagingId, film}.
function startBatch(films) {
  showProgress(true);
  shownBatch = null;   // named by its first event
  const formData = analysisSettings();
  formData.append('films', JSON.stringify(films.map(f => ({ staging_id: f.stagingId, film: f.film }))));
  startJob('/batches', formData, handleBatchEvent);
}

// Show batch `name`'s page: following its job under way, else as it is.
async function showBatch(name) {
  const jobs = await fetch('/jobs').then(r => r.ok ? r.json() : []).catch(() => []);
  const job = jobs.find(j => (j.kind === 'batch' || j.kind === 'batch_resume') && j.name === name);
  if (job) { followBatch(job); return; }
  showProgress(true);
  shownBatch = name;
  batchEnded();
}

// Show the progress of a batch's job (as GET /jobs has it), from its start.
function followBatch(job) {
  showProgress(true);
  shownBatch = job.name;
  if (job.kind === 'batch_resume') {
    stepsList.hidden = true;   // no film is analyzed
    cancelBtn.textContent = 'Cancel resume';
    followAnalysis(job.id, handleBatchResumeEvent);
  } else {
    followAnalysis(job.id, handleBatchEvent);
  }
}

// The page's batch parts, empty; shown with `batch` (showProgress).
function resetBatchPage(batch) {
  batchQueue.hidden = !batch;
  batchFilms.replaceChildren();
  batchHeading.textContent = '';
  batchFilmShown = -1;
  resuming = null;
  batchLoads++;
  runCounts.clear();
  for (const el of [batchResume, batchContinue, batchDownload, batchBack]) el.hidden = true;
}

// A batch job's events: the batch's record as it starts and as each film
// starts and ends, each film's progress in between, and the batch's end.
function handleBatchEvent(ev) {
  if (ev.step === 'batch') {
    lastEventAt = performance.now();
    shownBatch = ev.name;
    const analyzing = ev.films.findIndex(f => f.status === 'analyzing');
    if (analyzing !== -1 && analyzing !== batchFilmShown) {
      batchFilmShown = analyzing;
      resetProgress();   // for the next film's
    }
    renderBatch(ev.films.map(f => ({ ...f, counts: null })));
    return;
  }
  if (ev.step === 'done' || ev.step === 'cancelled' || ev.step === 'error') {
    batchEnded(ev.step === 'error' ? 'ERROR: ' + ev.detail : null);
    return;
  }
  handleEvent(ev);   // the film's progress
}

// A batch resume's events: each run as its resume starts, its progress, and
// the end.
function handleBatchResumeEvent(ev) {
  lastEventAt = performance.now();
  if (ev.step === 'batch_resume') {
    resuming = { run: ev.run, text: `Resuming… (run ${ev.number} of ${ev.total})`, status: null };
    loadBatch(false);   // with the counts of the runs resumed before
  } else if (ev.step === 'resuming' || ev.step === 'enriching') {
    if (!resuming) return;
    resuming.text = (ev.step === 'resuming' ? 'Resuming identification… ' : 'Identifying composers… ') + ev.detail;
    if (resuming.status) resuming.status.textContent = resuming.text;
  } else if (ev.step === 'done' || ev.step === 'cancelled' || ev.step === 'error') {
    resuming = null;
    batchEnded(ev.step === 'error' ? 'ERROR: ' + ev.detail : null);
  }
}

// The batch's page once its job ended, or with none under way: its films as
// they are now, and what the batch offers. `error`: why it stopped.
function batchEnded(error = null) {
  finishRun();
  if (error) {
    errorArea.textContent = error;
    errorArea.style.display = 'block';
  }
  stepsList.hidden = true;
  waveformContainer.style.display = 'none';
  setupVideo(null, null);
  loadBatch(true);
}

// Show the batch as GET /batches has it now; once `ended`, with what it offers.
async function loadBatch(ended) {
  const load = ++batchLoads;
  let batch;
  try {
    const res = await fetch('/batches/' + encodeURIComponent(shownBatch));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    batch = await res.json();
  } catch (err) {
    if (load === batchLoads) runEnded('Could not load the batch: ' + err.message);
    return;
  }
  if (load !== batchLoads) return;   // the page moved on
  batchProviders = batch.providers;
  const films = batch.films.map(f => ({ ...f, run: f.run && f.run.video_name, counts: f.run }));
  renderBatch(films);
  if (!ended) return;
  // No job of this page's server: another process has it.
  if (batch.under_way) batchHeading.textContent += ' · under way in another process, e.g. the command line';
  const pending = films.reduce((n, f) => n + (f.counts ? f.counts.pending : 0), 0);
  batchResume.textContent = `Resume pending cues (${pending})`;
  batchResume.hidden = !pending || batch.under_way;
  const n = batch.to_continue;
  batchContinue.textContent = `Continue the batch (${n} film${n === 1 ? '' : 's'})`;
  batchContinue.hidden = !n;
  batchDownload.hidden = !films.some(f => f.run);
  showRunEnd();
}

// `films`: each {source, status, error, run (its name), counts (as GET
// /history has a run's), or null when to read}.
function renderBatch(films) {
  const done = films.filter(f => f.status === 'done').length;
  batchHeading.textContent = `Batch: ${done} of ${films.length} films analyzed`;
  batchFilms.replaceChildren(...films.map(batchFilm));
}

function batchFilm(film) {
  const status = Object.assign(document.createElement('span'), { className: 'batch-film-status' });
  const item = document.createElement('li');
  item.className = `batch-film ${film.status}`;
  item.append(Object.assign(document.createElement('span'), { className: 'batch-film-name', textContent: film.source }), status);
  if (resuming && film.run === resuming.run) {
    item.className = 'batch-film analyzing';
    status.textContent = resuming.text;
    resuming.status = status;
  } else if (film.status === 'done') {
    if (film.counts) {
      status.textContent = countsText(film.counts);
    } else {
      status.textContent = 'Done';
      countsOf(film.run).then(text => { status.textContent = text; });
    }
    const open = Object.assign(document.createElement('button'), { type: 'button', className: 'reset-btn', textContent: 'Open' });
    open.addEventListener('click', () => openBatchRun(film.run));
    item.append(open);
  } else if (film.status === 'failed') {
    status.textContent = `Failed: ${film.error}`;
  } else {
    status.textContent = FILM_STATUS[film.status] || film.status;
  }
  return item;
}

// "12 of 15 cues identified · 3 pending"
function countsText(run) {
  return `${run.identified} of ${run.total} cues identified` + (run.pending ? ` · ${run.pending} pending` : '');
}

// The counts of a done film's run, once its results are read.
function countsOf(run) {
  if (!runCounts.has(run)) {
    runCounts.set(run, fetch('/results/' + encodeURIComponent(run))
      .then(res => res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status)))
      .then(results => {
        const cues = results.cues || [];
        return countsText({
          identified: cues.filter(c => c.title).length,
          total: cues.length,
          pending: cues.filter(c => c.pending).length,
        });
      })
      .catch(() => 'Done'));
  }
  return runCounts.get(run);
}

// Open a film's run, also while the batch runs on; "Back to the batch" returns.
async function openBatchRun(run) {
  const batch = shownBatch;
  stopFollowing();
  batchLoads++;
  if (!await openRun(run, batch)) showBatch(batch);
}

batchBack.addEventListener('click', () => showBatch(shownBatch));

// Continue the batch shown, or resume its runs' pending cues: its job `kind`.
async function startBatchJob(action, kind) {
  const missing = providersMissingKeys(batchProviders);
  if (missing.length) {
    alert(`API keys missing for ${missing.join(' and ')}. Add them under Settings → API keys.`);
    document.getElementById('settings-panel').open = true;
    document.getElementById('api-keys').open = true;
    return;
  }
  const name = shownBatch;
  try {
    const res = await fetch(`/batches/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`);
    followBatch({ id: body.job_id, kind, name });
  } catch (err) {
    errorArea.textContent = 'ERROR: ' + err.message;
    errorArea.style.display = 'block';
  }
}
batchContinue.addEventListener('click', () => startBatchJob('continue', 'batch'));
batchResume.addEventListener('click', () => startBatchJob('resume', 'batch_resume'));

// Download all results: each run's, as JSON, CSV, a VIAN project or a MAVA
// .mediapkg, in a zip.
const batchDownloadOptions = document.getElementById('batch-download-options');
dropdownMenu(document.getElementById('batch-download-btn'), batchDownloadOptions);
batchDownloadOptions.addEventListener('click', async e => {
  const format = e.target.closest('[data-format]')?.dataset.format;
  if (!format || !shownBatch) return;
  const res = await fetch(`/batches/${encodeURIComponent(shownBatch)}/results/${format}`);
  if (!res.ok) {
    const { detail } = await res.json().catch(() => ({}));
    alert(`Download failed: ${typeof detail === 'string' ? detail : `HTTP ${res.status}`}`);
    return;
  }
  // Same name the server sends in Content-Disposition.
  await triggerDownload(await res.blob(), `${shownBatch}_results_${format}.zip`);
});
