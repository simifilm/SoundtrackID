// Recent runs.

async function loadHistory() {
  const panel = document.getElementById('history-panel');
  const list = document.getElementById('history-list');
  try {
    const items = await fetch('/history').then(r => r.json());
    if (!items.length) { panel.style.display = 'none'; return; }
    list.innerHTML = '';
    items.forEach(item => {
      const d = new Date(item.timestamp * 1000);
      const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      const meta = `${dateStr}, ${timeStr} · ${item.total} seg${item.total !== 1 ? 's' : ''}` +
        (item.identified > 0 ? ` · ${item.identified} identified` : '');
      const div = document.createElement('div');
      div.className = 'history-item';
      div.innerHTML = `
        <span class="history-name">${fmt(item.video_name)}</span>
        <span class="history-meta">${meta}</span>
        <button class="history-delete-btn" title="Delete run"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="3,6 5,6 21,6"/><path d="M19,6l-1,14H6L5,6"/><path d="M10,11v6M14,11v6"/><path d="M9,6V4h6v2"/></svg></button>
        <span class="history-chevron">›</span>`;
      div.querySelector('.history-delete-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        try { await fetch('/history/' + encodeURIComponent(item.video_name), { method: 'DELETE' }); } catch (_) {}
        // The run on screen is gone: back to the start page.
        if (item.video_name === currentVideoName) showStartPage(); else loadHistory();
      });
      div.addEventListener('click', async () => {
        const res = await fetch('/results/' + encodeURIComponent(item.video_name));
        if (!res.ok) { alert(`Could not open this run (HTTP ${res.status})`); return; }
        const results = await res.json();
        const timeline = await loadTimeline(item.video_name);
        dropZone.style.display = 'none';
        panel.open = false;
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
      });
      list.appendChild(div);
    });
    panel.style.display = 'block';
  } catch (_) { panel.style.display = 'none'; }
}

document.getElementById('delete-all-runs').addEventListener('click', async () => {
  const n = document.querySelectorAll('.history-item').length;
  if (!confirm(`Delete ${n === 1 ? 'the 1 run' : `all ${n} runs`}? This can't be undone.`)) return;
  try { await fetch('/history', { method: 'DELETE' }); } catch (_) {}
  showStartPage();
});
