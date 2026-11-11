// Choosing a file and confirming the film before the analysis.

let pendingStagingId = null;   // staged upload awaiting confirmation → analysis
let pendingFilm = {};          // full film metadata read/looked-up in the confirm step

// ── Pre-analysis film confirmation ────────────────────────────────────────────
const filmConfirm = document.getElementById('film-confirm');
const fcStatus     = document.getElementById('fc-status');
const fcFields = {
  title: document.getElementById('fc-title'),
  year: document.getElementById('fc-year'),
  director: document.getElementById('fc-director'),
  imdb_id: document.getElementById('fc-imdb'),
  tmdb_id: document.getElementById('fc-tmdb'),
};

function fillFilmFields(film) {
  film = film || {};
  fcFields.title.value    = film.title ?? '';
  fcFields.year.value     = film.year ?? '';
  fcFields.director.value = film.director ?? '';
  fcFields.imdb_id.value  = film.imdb_id ?? '';
  fcFields.tmdb_id.value  = film.tmdb_id ?? '';
}

function collectFilmFields() {
  const yearNum = parseInt(fcFields.year.value, 10);
  return {
    title: fcFields.title.value.trim() || null,
    year: Number.isFinite(yearNum) ? yearNum : null,
    director: fcFields.director.value.trim() || null,
    imdb_id: fcFields.imdb_id.value.trim() || null,
    tmdb_id: fcFields.tmdb_id.value.trim() || null,
  };
}

// Step 1: stage the file and prefill the film form for confirmation.
function beginIdentify(file) {
  errorArea.style.display = 'none';
  document.getElementById('history-panel').style.display = 'none';
  dropZone.style.display = 'none';
  progressView.style.display = 'none';
  resultsView.style.display = 'none';
  pendingStagingId = null;
  pendingFilm = {};
  fillFilmFields(null);
  fcStatus.textContent = 'Uploading file and reading metadata…';
  filmConfirm.style.display = 'block';

  const fd = new FormData();
  fd.append('file', file);
  fetch('/identify', { method: 'POST', body: fd })
    .then(res => {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(data => {
      pendingStagingId = data.staging_id;
      pendingFilm = data.film || {};
      fillFilmFields(pendingFilm);
      fcStatus.textContent = pendingFilm.year
        ? 'Prefilled from file tags. Please review, especially the year.'
        : 'No year found in the file tags. Please add it or look it up online.';
    })
    .catch(err => {
      const stale = String(err).includes('404');
      fcStatus.textContent = stale
        ? 'Server endpoint /identify is missing. Running an older server version? Please restart the server.'
        : 'Could not prepare the file. Please try again.';
    });
}

// Optional: resolve missing fields online (TMDb/Wikidata) without re-uploading.
document.getElementById('fc-lookup').addEventListener('click', () => {
  fcStatus.textContent = 'Looking up online…';
  fetch('/film/lookup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(collectFilmFields()),
  })
    .then(res => res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status)))
    .then(data => {
      const f = data.film || {};
      // Keep the full looked-up record (incl. director_death_year, sources) …
      pendingFilm = { ...pendingFilm, ...f };
      // … but only fill *empty* form fields, keeping anything the user typed.
      for (const [k, el] of Object.entries(fcFields)) {
        if (!el.value && f[k] != null) el.value = f[k];
      }
      fcStatus.textContent = (f.title || f.year)
        ? 'Online data applied (empty fields filled).'
        : 'Nothing found online. Please fill in manually.';
    })
    .catch(err => {
      fcStatus.textContent = String(err).includes('404')
        ? 'Endpoint /film/lookup is missing. The server is running an older version; please restart.'
        : 'Online lookup failed.';
    });
});

document.getElementById('fc-cancel').addEventListener('click', () => {
  pendingStagingId = null;
  pendingFilm = {};
  filmConfirm.style.display = 'none';
  dropZone.style.display = 'block';
  fileInput.value = '';
});

document.getElementById('fc-start').addEventListener('click', () => {
  if (!pendingStagingId) { fcStatus.textContent = 'File not ready yet. Please wait a moment.'; return; }
  // Check before closing the dialog, so the user can fix settings and retry.
  if (selectedProviders().length === 0) {
    alert('Select at least one detection provider in Settings.');
    return;
  }
  const missing = providersMissingKeys();
  if (missing.length) {
    alert(`API keys missing for ${missing.join(' and ')}. Add them under Settings → API keys, or unselect the provider.`);
    document.getElementById('settings-panel').open = true;
    document.getElementById('api-keys').open = true;
    return;
  }
  filmConfirm.style.display = 'none';
  // Merge the edited fields over the full retrieved record so nothing extra
  // (e.g. director_death_year) is lost.
  const film = { ...pendingFilm };
  for (const [k, v] of Object.entries(collectFilmFields())) film[k] = v;
  startAnalysis(pendingStagingId, film);
});

// Drag & Drop
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
const MEDIA_EXTS = new Set(['.mp4','.mov','.avi','.mkv','.webm','.m4v','.mpg','.mpeg','.wmv','.flv','.mp3','.wav']);
function isMediaFile(file) {
  if (file.type.startsWith('video/') || file.type.startsWith('audio/')) return true;
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  return MEDIA_EXTS.has(ext);
}
function rejectFile() {
  errorArea.textContent = 'Only video or audio files are supported (.mp4, .mov, .mkv, .mp3, .wav …)';
  errorArea.style.display = 'block';
}

dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (!file) return;
  if (!isMediaFile(file)) { rejectFile(); return; }
  beginIdentify(file);
});
fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!isMediaFile(file)) { rejectFile(); fileInput.value = ''; return; }
  beginIdentify(file);
});
