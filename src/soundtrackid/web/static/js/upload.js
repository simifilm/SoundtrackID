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
  linkFilmIds();
}

// The IMDb and TMDb ID fields link to the pages of the IDs filled in.
const fcImdbLink = document.getElementById('fc-imdb-link');
const fcTmdbLink = document.getElementById('fc-tmdb-link');

function linkFilmIds() {
  const imdb = encodeURIComponent(fcFields.imdb_id.value.trim());
  const tmdb = encodeURIComponent(fcFields.tmdb_id.value.trim());
  // A film and a series can have the same TMDb ID.
  const tmdbPage = pendingFilm.media_type === 'tv' ? 'tv' : 'movie';
  setHref(fcImdbLink, imdb && `https://www.imdb.com/title/${imdb}/`);
  setHref(fcTmdbLink, tmdb && `https://www.themoviedb.org/${tmdbPage}/${tmdb}`);
}

function setHref(link, url) {
  if (url) link.href = url;
  else link.removeAttribute('href');
}

fcFields.imdb_id.addEventListener('input', linkFilmIds);
fcFields.tmdb_id.addEventListener('input', () => {
  // Typed, it may be another film's or series': whether it is a series is unknown.
  pendingFilm.media_type = null;
  linkFilmIds();
});

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

// Optional: find the film online (TMDb, else Wikidata) without re-uploading, and
// let the user choose among the films found.
const filmChoose   = document.getElementById('film-choose');
const fcCandidates = document.getElementById('fc-candidates');
const FC_FIELD_NAMES = { title: 'Title', year: 'Year', director: 'Director', imdb_id: 'IMDb ID', tmdb_id: 'TMDb ID' };
const FC_MATCHES = { tmdb_id: 'Matches the TMDb ID', imdb_id: 'Matches the IMDb ID' };
const FC_KINDS = { movie: 'Film', tv: 'Series' };  // TMDb's media types

function postFilm(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(res => res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status)));
}

function fcElement(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function showCandidates(candidates) {
  fcCandidates.replaceChildren(...candidates.map(c => {
    const poster = c.poster_url
      ? Object.assign(fcElement('img', 'fc-poster'), { src: c.poster_url, alt: '' })
      : fcElement('span', 'fc-poster');
    const text = fcElement('span', 'fc-cand-text');
    text.append(fcElement('strong', '', c.year ? `${c.title} (${c.year})` : c.title));
    if (FC_KINDS[c.media_type]) text.append(fcElement('span', 'fc-cand-sub', FC_KINDS[c.media_type]));
    if (c.original_title && c.original_title !== c.title) {
      text.append(fcElement('span', 'fc-cand-sub', `Original title: ${c.original_title}`));
    }
    if (FC_MATCHES[c.match]) text.append(fcElement('span', 'fc-match', FC_MATCHES[c.match]));
    if (c.overview) text.append(fcElement('span', 'fc-overview', c.overview));
    const choice = fcElement('button', 'fc-candidate');
    choice.type = 'button';
    choice.append(poster, text);
    choice.addEventListener('click', () => chooseFilm(c));
    const item = document.createElement('li');
    item.append(choice);
    return item;
  }));
  filmChoose.showModal();
}

function chooseFilm(c) {
  filmChoose.close();
  fcStatus.textContent = 'Looking up the details…';
  // By its TMDb ID alone, so nothing typed in the form mixes another film in,
  // and whether it is a film's or a series'; a film found without TMDb comes
  // already looked up.
  const film = c.film
    ? Promise.resolve(c.film)
    : postFilm('/film/lookup', { tmdb_id: c.tmdb_id, media_type: c.media_type }).then(data => data.film || {});
  film.then(applyFilm).catch(() => { fcStatus.textContent = 'Online lookup failed.'; });
}

// The chosen film replaces the form and the record, also where it has no value,
// so nothing of another film stays; the status says what changed.
function applyFilm(film) {
  const before = collectFilmFields();
  pendingFilm = film;
  fillFilmFields(film);
  const after = collectFilmFields();
  const shown = v => v == null ? '(empty)' : `"${v}"`;
  const changes = Object.keys(fcFields)
    .filter(k => String(before[k] ?? '') !== String(after[k] ?? ''))
    .map(k => `${FC_FIELD_NAMES[k]} ${shown(before[k])} → ${shown(after[k])}`);
  fcStatus.textContent = changes.length
    ? `Applied. Changed: ${changes.join('; ')}.`
    : 'Applied. Nothing changed.';
}

document.getElementById('fc-lookup').addEventListener('click', () => {
  fcStatus.textContent = 'Looking up online…';
  postFilm('/film/search', collectFilmFields())
    .then(data => {
      const candidates = data.candidates || [];
      if (!candidates.length) {
        fcStatus.textContent = 'Nothing found online. Please fill in manually.';
        return;
      }
      fcStatus.textContent = 'Choose the film or series.';
      showCandidates(candidates);
    })
    .catch(err => {
      fcStatus.textContent = String(err).includes('404')
        ? 'Endpoint /film/search is missing. The server is running an older version; please restart.'
        : 'Online lookup failed.';
    });
});

function cancelChoice() {
  fcStatus.textContent = 'Nothing chosen; the form is unchanged.';
}
document.getElementById('fc-choose-cancel').addEventListener('click', () => {
  filmChoose.close();
  cancelChoice();
});
filmChoose.addEventListener('cancel', cancelChoice);  // Escape

document.getElementById('fc-cancel').addEventListener('click', () => {
  pendingStagingId = null;
  pendingFilm = {};
  showStartPage();   // with the recent runs, hidden while confirming
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
