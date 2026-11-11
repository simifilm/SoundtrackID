// How a cue shows: escaping, links, times, the film, dates, rights, grouping.

// Results
// Results hold text from providers, MusicBrainz/Wikidata (publicly editable),
// file tags and file names: escape it before it goes into HTML.
function fmt(s) { return s == null ? '' : String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`); }
// Only web pages become links, never e.g. a supplied "javascript:" URL.
function isWebUrl(url) { return /^https?:\/\//i.test(url || ''); }

const PLATFORM_ICONS = {
  youtube: {
    color: '#ef4444',
    title: 'Watch on YouTube',
    path: '<path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8zM9.8 15.5V8.5l6.2 3.5-6.2 3.5z"/>',
  },
  spotify: {
    color: '#1db954',
    title: 'Open in Spotify',
    path: '<path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.52 17.28c-.24.36-.66.48-1.02.24-2.82-1.74-6.36-2.1-10.56-1.14-.42.12-.78-.18-.9-.54-.12-.42.18-.78.54-.9 4.56-1.02 8.52-.6 11.64 1.32.42.18.48.66.3 1.02zm1.44-3.3c-.3.42-.84.6-1.26.3-3.24-1.98-8.16-2.58-11.94-1.38-.48.12-.99-.12-1.11-.6-.12-.48.12-.99.6-1.11 4.38-1.32 9.78-.66 13.5 1.62.36.18.54.78.21 1.17zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.3c-.6.18-1.2-.18-1.38-.78-.18-.6.18-1.2.78-1.38 4.26-1.26 11.28-1.02 15.72 1.62.54.3.72 1.02.42 1.56-.3.42-1.02.6-1.56.3z"/>',
  },
  apple: {
    color: '#fc3c44',
    title: 'Open in Apple Music',
    path: '<path d="M23.994 6.124a9.23 9.23 0 0 0-.24-2.19c-.317-1.31-1.062-2.31-2.18-3.043a5.022 5.022 0 0 0-1.769-.65c-.69-.12-1.385-.14-2.078-.13H6.272c-.692-.01-1.387.01-2.077.13-.692.12-1.356.35-1.769.65C1.308 1.624.563 2.624.246 3.934a9.23 9.23 0 0 0-.24 2.19C-.006 6.5 0 6.876 0 7.25v9.5c0 .374-.006.75.006 1.126a9.23 9.23 0 0 0 .24 2.19c.317 1.31 1.062 2.31 2.18 3.043.413.3 1.077.53 1.769.65.69.12 1.385.14 2.077.13h11.455c.692.01 1.387-.01 2.078-.13.691-.12 1.356-.35 1.769-.65 1.118-.733 1.863-1.733 2.18-3.043a9.23 9.23 0 0 0 .24-2.19c.012-.376.006-.752.006-1.126v-9.5c0-.374.006-.75-.006-1.126zM15.5 6.25H13v7.5a2.25 2.25 0 0 1-4.5 0 2.25 2.25 0 0 1 2.25-2.25c.402 0 .775.11 1.1.295V5h3.65v1.25z"/>',
  },
};

function cleanAppleMusicUrl(url) {
  if (!url) return url;
  try {
    const u = new URL(url);
    const i = u.searchParams.get('i');
    return (u.origin + u.pathname) + (i ? `?i=${i}` : '');
  } catch (_) { return url; }
}

function platformLinkIcons(cue) {
  const links = [
    { url: cue.youtube_link, platform: 'youtube' },
    { url: cue.spotify_url,  platform: 'spotify' },
    { url: cleanAppleMusicUrl(cue.apple_music_url), platform: 'apple' },
  ].filter(l => isWebUrl(l.url));
  return links.map(({ url, platform }) => {
    const { color, title, path } = PLATFORM_ICONS[platform];
    return `<a class="track-link" href="${fmt(url)}" target="_blank" rel="noopener noreferrer" title="${title}"><svg viewBox="0 0 24 24" fill="${color}">${path}</svg></a>`;
  }).join('');
}

function fmtTime(secs) {
  const m = Math.floor(secs / 60);
  const sec = (secs % 60).toFixed(1);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function renderFilmHeader(film) {
  const el = document.getElementById('film-metadata-header');
  if (!el) return;
  if (!film || (!film.title && !film.director && !film.imdb_id && !film.tmdb_id)) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  const yearPart = film.year ? ` (${fmt(film.year)})` : '';
  // The director's death year is not shown - PD status depends on the composer, not the director.
  const parts = [];
  if (film.director) parts.push(`Director: <a class="film-id-link" href="https://en.wikipedia.org/wiki/${encodeURIComponent(film.director.replace(/ /g, '_'))}" target="_blank" rel="noopener noreferrer">${fmt(film.director)}</a>`);
  if (film.imdb_id) parts.push(`IMDb: <a class="film-id-link" href="https://www.imdb.com/title/${fmt(film.imdb_id)}/" target="_blank" rel="noopener noreferrer">${fmt(film.imdb_id)}</a>`);
  if (film.tmdb_id) parts.push(`TMDb: <a class="film-id-link" href="https://www.themoviedb.org/movie/${fmt(film.tmdb_id)}" target="_blank" rel="noopener noreferrer">${fmt(film.tmdb_id)}</a>`);
  el.style.display = 'block';
  el.innerHTML = `
    <div class="film-title">${fmt(film.title || '')}${yearPart}</div>
    <div class="film-meta">${parts.join(' · ')}</div>`;
}

const PROVIDER_LABELS = { acrcloud: 'ACRCloud', shazam: 'Shazam', audd: 'AudD', ensemble: 'Ensemble' };

// Positive confidence signal from the ensemble: more providers agreeing = higher
// probability of correctness. A single-provider hit is valid, just uncorroborated
// (never styled as a warning). Empty for single-provider runs (no agreement data).
function confBadgeHTML(cue) {
  const ag = cue.agreement;
  if (!ag || !cue.title) return '';
  const { count, total } = ag;
  const cls = count >= 3 ? 'conf-high' : count === 2 ? 'conf-med' : 'conf-low';
  const dots = '●'.repeat(count) + '○'.repeat(Math.max(0, total - count));
  const names = (ag.providers || []).map(p => PROVIDER_LABELS[p] || p).join(', ');
  return `<span class="conf-badge ${cls}" title="${count} of ${total} providers agree${names ? ': ' + names : ''}">`
       + `<span class="conf-dots">${dots}</span> ${count}/${total}</span>`;
}

const ROLE_LABELS = { composer: 'Composer', lyricist: 'Lyricist', writer: 'Writer' };

// Rights status pill for the Rights column. Positive framing: "Public domain" is
// the confirmed-clear state; "Protected" is neutral (still in copyright);
// "Unverified" means we couldn't establish it. Clickable when there's detail.
function rightsPillHTML(enr) {
  let cls, label;
  if (enr.public_domain === true)      { cls = 'rights-pd';        label = 'Public domain'; }
  else if (enr.public_domain === false){ cls = 'rights-protected'; label = 'Protected'; }
  else if ((enr.authors || []).length || enr.composer) { cls = 'rights-unknown'; label = 'Unverified'; }
  else return '';
  const clickable = (enr.authors || []).length ? ' rights-clickable' : '';
  const title = clickable ? ' title="Show authorship &amp; copyright"' : '';
  return `<span class="rights-pill ${cls}${clickable}"${title}>${label}</span>`;
}

// Quiet "+N" after the primary composer when the work has co-authors (lyricists).
function authorsInlineHTML(enr) {
  const n = (enr.authors || []).length;
  return n > 1 ? ` <span class="authors-more" title="${n} authors, see Rights">+${n - 1}</span>` : '';
}

// One plain-language sentence explaining the copyright status.
function copyrightSentence(enr) {
  const yr = enr.latest_author_death_year;
  if (enr.public_domain === true)
    return `Public domain${yr ? ` since ${yr + 71}` : ''}. Every author has been deceased at least 70 years.`;
  if (enr.public_domain === false) {
    const last = (enr.authors || []).find(a => a.death_year === yr);
    return `In copyright ${enr.authors_partial ? `at least until ${yr + 71}` : `until ${yr + 71}`}` + (last ? `, 70 years after the last surviving author, ${fmt(last.name)} (d. ${yr}).` : '.');
  }
  if (enr.pd_presumption === true)
    return "The presumption rule applies, under which it can be assumed with reasonable certainty that the author has been dead for at least 70 years.";
  if (enr.pd_presumption === false)
    return "The presumption rule can't be applied, because it can't be assumed with reasonable certainty that the author has been dead for at least 70 years.";
  // Everyone we found is accounted for, but the set itself may be short a name -
  // so the gap is the missing people, not the missing dates.
  if (enr.authors_partial && (enr.authors || []).length && (enr.authors || []).every(a => a.death_year))
    return `Undetermined. Only the composer could be established for this work — an uncredited lyricist or co-writer may still hold rights.`;
  if ((enr.authors || []).length || enr.composer)
    return `Undetermined. A contributing author’s death year is missing, or they are still living.`;
  return '';
}

// Unified detail panel (one expandable row per cue): authorship, the copyright
// verdict, and - for ensemble runs - each provider's raw answer.
function detailPanelHTML(cue) {
  const enr = cue.enrichment || {};
  const sections = [];

  const authors = enr.authors || [];
  if (authors.length) {
    const lines = authors.map(a => {
      const roles = (a.roles || []).map(r => ROLE_LABELS[r] || r).join(' · ');
      const born = a.birth_year ? `b. ${a.birth_year}` : 'birth unknown';
      const dead = a.death_year ? `d. ${a.death_year}` : 'death unknown';
      const clock = enr.latest_author_death_year && a.death_year === enr.latest_author_death_year;
      // Linked to their Wikidata entity when MusicBrainz knows one; plain text otherwise.
      const name = isWebUrl(a.wikidata_url)
        ? `<a href="${fmt(a.wikidata_url)}" target="_blank" rel="noopener noreferrer" title="View on Wikidata">${fmt(a.name)}</a>`
        : fmt(a.name);
      return `<div class="author-line${clock ? ' author-clock' : ''}">`
           + `<span class="author-name">${name}</span>`
           + `<span class="author-role">${roles}</span>`
           + `<span class="author-birth">${born}</span>`
           + `<span class="author-death">${dead}</span></div>`;
    }).join('');
    sections.push(`<div class="detail-section detail-authors"><div class="detail-label">Authorship</div>${lines}</div>`);
  }

  const copyright = copyrightSentence(enr);
  if (copyright)
    sections.push(`<div class="detail-section detail-copyright-col"><div class="detail-label">Copyright</div><div class="detail-copyright">${copyright}</div></div>`);

  if (cue.votes && cue.votes.length > 1) {
    const lines = cue.votes.map(v => {
      const label = PROVIDER_LABELS[v.provider] || v.provider;
      const agree = (cue.agreement && (cue.agreement.providers || []).includes(v.provider));
      let answer;
      if (v.error) answer = `<span class="vote-error">error: ${fmt(v.error)}</span>`;
      else if (v.matched) answer = `${fmt(v.title)}${v.artist ? ' – ' + fmt(v.artist) : ''}`;
      else answer = '<span class="vote-none">no match</span>';
      return `<div class="vote-line${agree ? ' vote-agree' : ''}"><span class="vote-provider">${label}</span>${answer}</div>`;
    }).join('');
    sections.push(`<div class="detail-section detail-providers"><div class="detail-label">Identified by</div>${lines}</div>`);
  }

  return `<td colspan="9"><div class="cue-detail-inner">${sections.join('')}</div></td>`;
}

function cueHasDetail(cue) {
  return (cue.votes && cue.votes.length > 1) || ((cue.enrichment || {}).authors || []).length > 0;
}

// Group cues into unique pieces: which piece a cue is, the server works out
// (track_key), as it does whether it postdates the film (anachronistic).
// Recurrences of one work collapse into a single Tracks entry; the representative
// is the cue with the strongest ensemble agreement (best identification of it).
function groupCues(cues) {
  const map = new Map();
  cues.forEach(cue => {
    if (!cue.title) return;  // unidentified segments live only in the timeline
    if (!map.has(cue.track_key)) map.set(cue.track_key, []);
    map.get(cue.track_key).push(cue);
  });
  const agree = c => (c.agreement && c.agreement.count) || 0;
  return Array.from(map.values()).map(cs => ({
    rep: cs.reduce((b, c) => (agree(c) > agree(b) ? c : b), cs[0]),
    cues: cs,
    anachronistic: cs.every(c => c.anachronistic),
  }));
}
