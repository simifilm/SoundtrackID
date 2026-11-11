// Showing a run's results; resuming, re-running and leaving it.

function showResults(results) {
  if (!results) {
    errorArea.textContent = 'ERROR: server sent done event without results data';
    errorArea.style.display = 'block';
    return;
  }
  currentResults = results;
  resultsView.style.display = 'block';

  const cues = results.cues || [];
  const videoName = results.video_name;
  currentVideoName = videoName;

  setupVideo(results, videoName);
  renderFilmHeader(results.film);
  // A re-run needs the video the server stored for this run.
  document.getElementById('rerun-btn').style.display = results.video ? '' : 'none';

  const tabs = document.getElementById('results-tabs');
  const tracksView = document.getElementById('tracks-view');
  const timelineView = document.getElementById('timeline-view');

  if (cues.length === 0) {
    tabs.style.display = 'none';
    tracksView.style.display = 'none';
    timelineView.style.display = 'none';
    document.getElementById('classes-view').style.display = 'none';
    emptyState.style.display = 'block';
  } else {
    emptyState.style.display = 'none';
    tabs.style.display = 'flex';
    renderTracks(groupCues(cues), videoName);
    renderTimeline(cues, videoName);
    switchResultsView(currentResultsView);
  }

  updateAnachronismToolbar(cues);
  updateRateLimitBanner(results);
}

// Show a resume prompt while cues are pending: a provider hit its limit,
// couldn't be reached or failed on them (wrong keys fail the run instead).
function updateRateLimitBanner(results) {
  const banner = document.getElementById('ratelimit-banner');
  if (!banner) return;
  const cues = results.cues || [];
  const identified = cues.filter(c => c.title).length;
  const pending = cues.filter(c => c.pending).length;
  if (!pending) { banner.style.display = 'none'; return; }
  document.getElementById('ratelimit-text').textContent =
    `A provider hit its limit, couldn't be reached or failed. ${identified} of ${cues.length} identified; ${pending} still pending.`;
  banner.style.display = 'flex';
  document.getElementById('resume-btn').disabled = false;
}

function setResumeRunning(on) {
  resumeRunning = on;
  document.getElementById('tracks-body').classList.toggle('locked', on);
}

function resumeDetection() {
  if (!currentVideoName) return;
  const btn = document.getElementById('resume-btn');
  const text = document.getElementById('ratelimit-text');
  // Continue with the run's own providers; older runs didn't save them.
  const providers = currentResults?.providers || selectedProviders();
  const missing = providersMissingKeys(providers);
  if (missing.length) {
    alert(`API keys missing for ${missing.join(' and ')}. Add them under Settings → API keys.`);
    document.getElementById('settings-panel').open = true;
    document.getElementById('api-keys').open = true;
    return;
  }
  btn.disabled = true;
  setResumeRunning(true);
  fetch(`/resume/${encodeURIComponent(currentVideoName)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api: providers.join(',') || 'acrcloud' }),
  }).then(async res => {
    if (!res.ok) {
      // E.g. the run was deleted: a JSON error, not an event stream.
      const { detail } = await res.json().catch(() => ({}));
      text.textContent = 'Resume failed: ' + (typeof detail === 'string' ? detail : `HTTP ${res.status}`);
      btn.disabled = false;
      setResumeRunning(false);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', finished = false;
    (function pump() {
      reader.read().then(({ done, value }) => {
        if (done) {
          // Ended without "done"/"error": the server dropped the connection.
          if (!finished) {
            text.textContent = 'Resume failed: the server closed the connection before finishing.';
            btn.disabled = false;
            setResumeRunning(false);
          }
          return;
        }
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n'); buf = parts.pop();
        parts.forEach(part => {
          if (!part.startsWith('data: ')) return;
          let ev; try { ev = JSON.parse(part.slice(6)); } catch (_) { return; }
          if (ev.step === 'resuming') text.textContent = `Resuming identification... ${ev.detail}`;
          else if (ev.step === 'enriching') text.textContent = `Identifying composers... ${ev.detail}`;
          else if (ev.step === 'done') {
            finished = true;
            setResumeRunning(false);
            currentResults = ev.results;
            showResults(ev.results);   // re-renders + refreshes the banner
          } else if (ev.step === 'error') {
            finished = true;
            text.textContent = 'Resume failed: ' + (ev.detail || 'unknown error');
            btn.disabled = false;
            setResumeRunning(false);
          }
        });
        pump();
      }).catch(err => { text.textContent = 'Resume connection lost: ' + err.message; btn.disabled = false; setResumeRunning(false); });
    })();
  }).catch(err => { text.textContent = 'Resume failed: ' + err.message; btn.disabled = false; setResumeRunning(false); });
}
document.getElementById('resume-btn').addEventListener('click', resumeDetection);

function updateAnachronismToolbar(cues) {
  const toolbar = document.getElementById('results-toolbar');
  const label = document.getElementById('anachronism-toggle-label');
  if (!toolbar) return;
  const n = cues.filter(c => c.anachronistic).length;
  if (n === 0) { toolbar.style.display = 'none'; return; }
  toolbar.style.display = 'flex';
  label.textContent = `Hide ${n} temporally implausible match${n === 1 ? '' : 'es'}`;
}

document.getElementById('opt-hide-anachronism').addEventListener('change', function() {
  resultsBody.classList.toggle('hide-anachronism', this.checked);
  document.getElementById('tracks-body').classList.toggle('hide-anachronism', this.checked);
});

// Analyze the shown run's stored video again with the current settings.
document.getElementById('rerun-btn').addEventListener('click', () => {
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
  startAnalysis(null, null, currentVideoName);
});

resetBtn.addEventListener('click', () => {
  filmConfirm.style.display = 'none';
  dropZone.style.display = 'block';
  progressView.style.display = 'none';
  resultsView.style.display = 'none';
  waveformContainer.style.display = 'none';
  setupVideo(null, null);
  fileInput.value = '';
  loadHistory();
});
