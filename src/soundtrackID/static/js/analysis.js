// Running an analysis: its steps, its progress events, a stall, cancelling.

let currentRunId      = null;    // POST /cancel/{currentRunId} cancels the run
let streaming         = false;   // until done, error or cancelled ends the run
let identFarthestEnd  = 0;       // farthest processed window end (identification frontier)
let identReaped       = 0;       // identification windows processed so far
let identTotal        = 0;       // total identification windows (known once classification finishes)
let lastEventAt       = 0;       // timestamp of the last SSE event (stall detection)
let stallTimer        = null;

// Steps helper
const STEP_ORDER = ['loading', 'classifying', 'detecting', 'writing', 'enriching'];
let currentStep = null;

function activateStep(stepName) {
  if (currentStep === stepName) return;
  if (currentStep) {
    const prev = document.querySelector(`.step[data-step="${currentStep}"]`);
    if (prev) { prev.classList.remove('active'); prev.classList.add('done'); prev.querySelector('.step-icon').textContent = '✓'; const d = prev.querySelector('.step-detail'); if (d) d.textContent = ''; }
  }
  currentStep = stepName;
  const el = document.querySelector(`.step[data-step="${stepName}"]`);
  if (el) el.classList.add('active');
}

function markAllDone() {
  document.querySelectorAll('.step').forEach(el => {
    el.classList.remove('active');
    el.classList.add('done');
    el.querySelector('.step-icon').textContent = '✓';
  });
}

function updateStepDetail(stepName, detail) {
  const el = document.querySelector(`.step[data-step="${stepName}"] .step-detail`);
  if (el) el.textContent = detail;
}

// Analysis
function selectedProviders() {
  return Array.from(document.querySelectorAll('.opt-provider:checked')).map(c => c.value);
}

// rerunVideo: name of a run whose stored video is analyzed again (no upload).
function startAnalysis(stagingId, film, rerunVideo = null) {
  resetWaveform();
  setupVideo(null, null);
  identFarthestEnd = 0; identReaped = 0; identTotal = 0;
  classFill.style.width = '0%'; identFill.style.width = '0%';
  classFill.classList.remove('waiting'); identFill.classList.remove('waiting');
  progBars.style.display = '';
  clearStall();
  startStallWatch();
  waveformContainer.style.display = 'none';
  errorArea.style.display = 'none';
  errorArea.textContent = '';
  resultsBody.innerHTML = '';
  currentStep = null;
  document.querySelectorAll('.step').forEach(el => {
    el.classList.remove('active', 'done');
    el.querySelector('.step-icon').textContent = el.closest('[data-step]')
      ? (STEP_ORDER.indexOf(el.closest('[data-step]').dataset.step) + 1).toString()
      : '';
  });
  STEP_ORDER.forEach((s, i) => {
    const icon = document.querySelector(`.step[data-step="${s}"] .step-icon`);
    if (icon) icon.textContent = i + 1;
    const detail = document.querySelector(`.step[data-step="${s}"] .step-detail`);
    if (detail) detail.textContent = '';
  });

  currentRunId = null;
  cancelBtn.disabled = false;
  cancelBtn.textContent = 'Cancel analysis';
  cancelBtn.style.display = 'block';

  document.getElementById('history-panel').style.display = 'none';
  dropZone.style.display = 'none';
  progressView.style.display = 'block';
  resultsView.style.display  = 'none';

  const formData = new FormData();
  if (!rerunVideo) {
    formData.append('staging_id', stagingId);
    formData.append('film', JSON.stringify(film || {}));
  }
  formData.append('api', selectedProviders().join(','));
  formData.append('threshold', document.getElementById('opt-threshold').value / 100);
  // The bridge gap is chosen in hops; the backend takes seconds.
  formData.append('bridge_gap', document.getElementById('opt-bridge').value * document.getElementById('opt-hop').value);
  formData.append('hop', document.getElementById('opt-hop').value);
  formData.append('max_segment', document.getElementById('opt-chunk').value);

  const url = rerunVideo ? `/rerun/${encodeURIComponent(rerunVideo)}` : '/analyze';
  fetch(url, { method: 'POST', body: formData })
    .then(async res => {
      if (!res.ok) {
        // Refused before the run started (e.g. an expired staged upload): the
        // body is a JSON error, not an event stream.
        const { detail } = await res.json().catch(() => ({}));
        finishRun();
        errorArea.textContent = 'ERROR: ' + (typeof detail === 'string' ? detail : `HTTP ${res.status}`);
        errorArea.style.display = 'block';
        return;
      }
      streaming = true;
      try {
        await readEvents(res, handleEvent, (err, text) => {
          // A bug handling one event: keep reading the stream, but show it
          // (also after "done", which hides the progress view).
          console.error('Could not handle server event', text, err);
          errorArea.textContent = 'Could not display an update from the server: ' + err.message;
          errorArea.style.display = 'block';
          progressView.style.display = 'block';
        });
        // Stream ended. If no terminal event (done/error/cancelled) ended the
        // run, the server dropped the connection mid-analysis.
        if (streaming) {
          finishRun();
          errorArea.textContent = 'The server closed the connection before finishing. It may have crashed. Please try again.';
          errorArea.style.display = 'block';
        }
      } catch (err) {
        // Abrupt drop mid-stream (server killed, network offline). Surface it
        // instead of hanging.
        if (streaming) {
          finishRun();
          errorArea.textContent = 'Lost connection to the server during analysis (' + err.message + '). Please try again.';
          errorArea.style.display = 'block';
        }
      }
    })
    .catch(err => { finishRun(); errorArea.textContent = 'Connection error: ' + err.message; errorArea.style.display = 'block'; });
}

const STALL_WARN_MS = 12000;   // no SSE event for this long during a run → warn
const STALL_HARD_MS = 40000;   // still nothing → stronger warning

function startStallWatch() {
  lastEventAt = performance.now();
  clearInterval(stallTimer);
  stallTimer = setInterval(checkStall, 2000);
}
function clearStall() {
  clearInterval(stallTimer); stallTimer = null;
  stallNotice.style.display = 'none';
  classFill.classList.remove('waiting');
  identFill.classList.remove('waiting');
}
function checkStall() {
  const gap = performance.now() - lastEventAt;
  if (gap < STALL_WARN_MS) return;
  const secs = Math.round(gap / 1000);
  // Which phase is stalled? Identification once it has started, else classification.
  const waitingIdent = currentStep === 'detecting' || identReaped > 0 || wfIdentified.length > 0;
  (waitingIdent ? identFill : classFill).classList.add('waiting');
  stallNotice.textContent = gap >= STALL_HARD_MS
    ? `Still waiting (${secs}s). The ${waitingIdent ? 'identification service (Shazam/ACRCloud)' : 'analysis'} isn’t responding. It may be rate-limited or down. Keep waiting or cancel.`
    : `Working… no response for ${secs}s. The ${waitingIdent ? 'identification service' : 'server'} is taking longer than usual.`;
  stallNotice.style.display = 'block';
}

function finishRun() {
  cancelBtn.style.display = 'none';
  currentRunId = null;
  streaming = false;
  clearStall();
}

function handleEvent(ev) {
  const { step, detail, results } = ev;

  // Any event means the pipeline is alive - reset the stall watchdog.
  lastEventAt = performance.now();
  if (stallNotice.style.display !== 'none') {
    stallNotice.style.display = 'none';
    classFill.classList.remove('waiting');
    identFill.classList.remove('waiting');
  }

  if (step === 'run_id') {
    currentRunId = ev.run_id;
    return;
  }

  if (step === 'cancelled') {
    finishRun();
    filmConfirm.style.display = 'none';
    dropZone.style.display = 'block';
    progressView.style.display = 'none';
    waveformContainer.style.display = 'none';
    setupVideo(null, null);
    fileInput.value = '';
    loadHistory();
    return;
  }

  if (step === 'waveform') {
    showWaveform(ev.samples, ev.duration);
    return;
  }

  if (step === 'identified') {
    // Emitted once per processed identification window (title === null means the
    // window was checked but not matched). Drives the identification bar per
    // window and the live green bands / tooltips for matches.
    identReaped = ev.reaped || identReaped + 1;
    if (ev.total) identTotal = ev.total;
    identFarthestEnd = Math.max(identFarthestEnd, ev.end);
    if (ev.title) addIdentifiedWindow(ev.start, ev.end, ev.title, ev.artist || null);
    // Identification bar: exact reaped/total once the total is known (after
    // classification), otherwise the processed-window frontier over the audio.
    const frac = identTotal
      ? identReaped / identTotal
      : (wfDuration > 0 ? identFarthestEnd / wfDuration : 0);
    identFill.style.width = Math.min(100, frac * 100) + '%';
    // Identification overlaps classification: surface the detecting step as
    // active (alongside classifying) with a live count, so progress is visible
    // during the run instead of appearing all at once at the end.
    const detStep = document.querySelector('.step[data-step="detecting"]');
    if (detStep && !detStep.classList.contains('done')) {
      detStep.classList.add('active');
      updateStepDetail('detecting', `${wfIdentified.length} identified…`);
    }
    if (wfSamples) redrawAll();
    return;
  }

  if (step === 'ident_waiting') {
    // Server is blocked waiting on the identification provider (e.g. Shazam
    // rate-limiting). Explicit heartbeat - show it rather than sit silent.
    identFill.classList.add('waiting');
    const done = ev.total ? ` (${ev.reaped}/${ev.total})` : '';
    stallNotice.textContent = `Waiting for the identification service…${done} ${ev.seconds}s`;
    stallNotice.style.display = 'block';
    return;
  }

  if (step === 'done') {
    finishRun();
    showCuesOnWaveform(results.cues || []);
    classFill.style.width = '100%'; identFill.style.width = '100%';
    setTimeout(() => { progBars.style.display = 'none'; }, 400);
    markAllDone();
    // Analysis finished - hide the step checklist; results + waveform remain.
    progressView.style.display = 'none';
    showResults(results);
    loadTimeline(results.video_name).then(t => renderClasses(t.classification_windows));
    return;
  }

  if (step === 'error') {
    finishRun();
    errorArea.textContent = 'ERROR: ' + detail;
    errorArea.style.display = 'block';
    return;
  }

  activateStep(step);
  if (step === 'detecting') {
    // Identification overlaps classification and its bar is driven by the
    // "identified" events, so here we only update the step label (and capture
    // the now-known window total for an exact identification-bar fraction).
    if ('title' in ev) {
      // One identification window's result.
      identTotal = ev.total;
      updateStepDetail(step, ev.title ? [ev.title, ev.artist].filter(Boolean).join(' – ') : 'No match');
    } else if (detail) {
      updateStepDetail(step, detail.trim());
    }
  } else if (step !== 'classifying') {
    updateStepDetail(step, detail ? detail.trim() : '');
  }

  if (step === 'classifying' && 'start' in ev) {
    addClassifiedWindow(ev.start, ev.end, ev.is_music);
    updateStepDetail(step, ev.is_music ? 'Music' : 'No music');
    // Classification bar tracks how far through the audio classification has run.
    if (wfDuration > 0) classFill.style.width = (ev.end / wfDuration * 100) + '%';
  }
}

cancelBtn.addEventListener('click', async () => {
  if (!currentRunId) return;
  cancelBtn.disabled = true;
  cancelBtn.textContent = 'Cancelling…';
  try { await fetch('/cancel/' + currentRunId, { method: 'POST' }); } catch (_) {}
});
