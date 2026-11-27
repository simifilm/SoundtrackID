// Choosing the files and confirming the films before the analysis: one, or
// several as a batch, a row each in the confirmation's table.

// The films to confirm, in order: each its file, its staged upload once there
// (stagingId; failed when it couldn't be), what is known about it (film, the
// full record read or looked up) and its row's elements.
let pendingFilms = [];
let choosingFor = null;   // the film whose choice among those found online is open

const filmConfirm = document.getElementById('film-confirm');
const fcFilms     = document.getElementById('fc-films');
const fcStatus    = document.getElementById('fc-status');
const fcStart     = document.getElementById('fc-start');
const FC_FIELDS = ['title', 'year', 'director', 'imdb_id', 'tmdb_id'];

// Step 1: a row per file, each filled in once its upload is staged; they are
// uploaded one after another. `skipped`: the files that are no film.
async function beginIdentify(files, skipped = []) {
  errorArea.style.display = 'none';
  document.getElementById('history-panel').style.display = 'none';
  dropZone.style.display = 'none';
  progressView.style.display = 'none';
  resultsView.style.display = 'none';
  fcFilms.querySelectorAll('.fc-film').forEach(row => row.remove());
  pendingFilms = files.map(addFilm);
  fcStatus.textContent = skipped.length
    ? `Not video or audio, left out: ${skipped.map(f => f.name).join(', ')}`
    : '';
  updateStart();
  filmConfirm.style.display = 'block';
  for (const entry of [...pendingFilms]) {
    if (pendingFilms.includes(entry)) await stage(entry);   // not removed meanwhile
  }
}

function addFilm(file) {
  const tbody = document.getElementById('fc-film-template').content.firstElementChild.cloneNode(true);
  const entry = {
    file,
    stagingId: null,
    failed: false,
    film: {},
    tbody,
    fields: Object.fromEntries(FC_FIELDS.map(k => [k, tbody.querySelector(`[data-field="${k}"]`)])),
    imdbLink: tbody.querySelector('[data-link="imdb_id"]'),
    tmdbLink: tbody.querySelector('[data-link="tmdb_id"]'),
    status: tbody.querySelector('.fc-row-status'),
  };
  tbody.querySelector('.fc-file').textContent = file.name;
  entry.fields.imdb_id.addEventListener('input', () => linkFilmIds(entry));
  entry.fields.tmdb_id.addEventListener('input', () => {
    // Typed, it may be another film's or series': whether it is a series is unknown.
    entry.film.media_type = null;
    linkFilmIds(entry);
  });
  entry.fields.year.addEventListener('input', () => markYear(entry));
  tbody.querySelector('.fc-lookup').addEventListener('click', () => lookUp(entry));
  tbody.querySelector('.fc-remove').addEventListener('click', () => removeFilm(entry));
  setEnabled(entry, false);
  entry.status.textContent = 'Waiting for the files before it…';
  fcFilms.append(tbody);
  return entry;
}

// Stage the film's file and prefill its row from the file's tags.
async function stage(entry) {
  entry.status.textContent = 'Uploading file and reading metadata…';
  const fd = new FormData();
  fd.append('file', entry.file);
  try {
    const res = await fetch('/identify', { method: 'POST', body: fd });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    entry.stagingId = data.staging_id;
    entry.film = data.film || {};
    fillFilmFields(entry, entry.film);
    setEnabled(entry, true);
    entry.status.textContent = entry.film.year
      ? 'Prefilled from file tags. Please review, especially the year.'
      : 'No year found in the file tags. Please add it or look it up online.';
  } catch (err) {
    entry.failed = true;
    entry.status.textContent = String(err).includes('404')
      ? 'Server endpoint /identify is missing. Running an older server version? Please restart the server.'
      : 'Could not prepare the file. Please remove it, or cancel and try again.';
  }
}

function setEnabled(entry, on) {
  for (const input of Object.values(entry.fields)) input.disabled = !on;
  entry.tbody.querySelector('.fc-lookup').disabled = !on;
}

function removeFilm(entry) {
  entry.tbody.remove();
  pendingFilms = pendingFilms.filter(e => e !== entry);
  if (!pendingFilms.length) { showStartPage(); return; }
  updateStart();
}

// One film is analyzed on its own, several as a batch.
function updateStart() {
  fcStart.textContent = pendingFilms.length > 1
    ? `Start batch (${pendingFilms.length} films)`
    : 'Start analysis';
}

function fillFilmFields(entry, film) {
  film = film || {};
  for (const k of FC_FIELDS) entry.fields[k].value = film[k] ?? '';
  linkFilmIds(entry);
  markYear(entry);
}

// The year flags music that came out after the film: a row without one shows.
function markYear(entry) {
  entry.tbody.classList.toggle('no-year', !entry.fields.year.value.trim());
}

// The IMDb and TMDb ID fields link to the pages of the IDs filled in.
function linkFilmIds(entry) {
  const imdb = encodeURIComponent(entry.fields.imdb_id.value.trim());
  const tmdb = encodeURIComponent(entry.fields.tmdb_id.value.trim());
  // A film and a series can have the same TMDb ID.
  const tmdbPage = entry.film.media_type === 'tv' ? 'tv' : 'movie';
  setHref(entry.imdbLink, imdb && `https://www.imdb.com/title/${imdb}/`);
  setHref(entry.tmdbLink, tmdb && `https://www.themoviedb.org/${tmdbPage}/${tmdb}`);
}

function setHref(link, url) {
  if (url) link.href = url;
  else link.removeAttribute('href');
}

function collectFilmFields(entry) {
  const f = entry.fields;
  const yearNum = parseInt(f.year.value, 10);
  return {
    title: f.title.value.trim() || null,
    year: Number.isFinite(yearNum) ? yearNum : null,
    director: f.director.value.trim() || null,
    imdb_id: f.imdb_id.value.trim() || null,
    tmdb_id: f.tmdb_id.value.trim() || null,
  };
}

// Optional: find the film online (TMDb, else Wikidata) without re-uploading, and
// let the user choose among the films found.
const filmChoose   = document.getElementById('film-choose');
const fcChooseTitle = document.getElementById('fc-choose-title');
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

function lookUp(entry) {
  entry.status.textContent = 'Looking up online…';
  postFilm('/film/search', collectFilmFields(entry))
    .then(data => {
      const candidates = data.candidates || [];
      if (!candidates.length) {
        entry.status.textContent = 'Nothing found online. Please fill in manually.';
        return;
      }
      entry.status.textContent = 'Choose the film or series.';
      showCandidates(entry, candidates);
    })
    .catch(err => {
      entry.status.textContent = String(err).includes('404')
        ? 'Endpoint /film/search is missing. The server is running an older version; please restart.'
        : 'Online lookup failed.';
    });
}

function showCandidates(entry, candidates) {
  choosingFor = entry;
  fcChooseTitle.textContent = `Choose the film or series of ${entry.file.name}`;
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
    choice.addEventListener('click', () => chooseFilm(entry, c));
    const item = document.createElement('li');
    item.append(choice);
    return item;
  }));
  filmChoose.showModal();
}

function chooseFilm(entry, c) {
  filmChoose.close();
  entry.status.textContent = 'Looking up the details…';
  // By its TMDb ID alone, so nothing typed in the form mixes another film in,
  // and whether it is a film's or a series'; a film found without TMDb comes
  // already looked up.
  const film = c.film
    ? Promise.resolve(c.film)
    : postFilm('/film/lookup', { tmdb_id: c.tmdb_id, media_type: c.media_type }).then(data => data.film || {});
  film.then(f => applyFilm(entry, f)).catch(() => { entry.status.textContent = 'Online lookup failed.'; });
}

// The chosen film replaces the row and the record, also where it has no value,
// so nothing of another film stays; the status says what changed.
function applyFilm(entry, film) {
  const before = collectFilmFields(entry);
  entry.film = film;
  fillFilmFields(entry, film);
  const after = collectFilmFields(entry);
  const shown = v => v == null ? '(empty)' : `"${v}"`;
  const changes = FC_FIELDS
    .filter(k => String(before[k] ?? '') !== String(after[k] ?? ''))
    .map(k => `${FC_FIELD_NAMES[k]} ${shown(before[k])} → ${shown(after[k])}`);
  entry.status.textContent = changes.length
    ? `Applied. Changed: ${changes.join('; ')}.`
    : 'Applied. Nothing changed.';
}

function cancelChoice() {
  if (choosingFor) choosingFor.status.textContent = 'Nothing chosen; the form is unchanged.';
}
document.getElementById('fc-choose-cancel').addEventListener('click', () => {
  filmChoose.close();
  cancelChoice();
});
filmChoose.addEventListener('cancel', cancelChoice);  // Escape

document.getElementById('fc-cancel').addEventListener('click', () => {
  pendingFilms = [];
  showStartPage();   // with the recent runs, hidden while confirming
});

fcStart.addEventListener('click', () => {
  if (pendingFilms.some(e => e.failed)) {
    fcStatus.textContent = 'Please remove the files that could not be prepared.';
    return;
  }
  if (pendingFilms.some(e => !e.stagingId)) {
    fcStatus.textContent = pendingFilms.length > 1
      ? 'Files not ready yet. Please wait a moment.'
      : 'File not ready yet. Please wait a moment.';
    return;
  }
  // Check before closing the table, so the user can fix settings and retry.
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
  const films = pendingFilms.map(e => ({
    stagingId: e.stagingId,
    film: { ...e.film, ...collectFilmFields(e) },
  }));
  pendingFilms = [];
  if (films.length === 1) startAnalysis(films[0].stagingId, films[0].film);
  else startBatch(films);
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

// The video and audio files of those chosen; the others are left out.
function takeFiles(fileList) {
  const files = Array.from(fileList);
  const media = files.filter(isMediaFile);
  if (!media.length) { rejectFile(); return false; }
  beginIdentify(media, files.filter(f => !isMediaFile(f)));
  return true;
}

dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) takeFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files.length && !takeFiles(fileInput.files)) fileInput.value = '';
});
