// Downloading a run's results: JSON, CSV and the MAVA export.

// Download helpers
// `content` is a string, or a Blob (e.g. a zip fetched from the server).
async function triggerDownload(content, filename, type) {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  // In the desktop app, ask where to save (the webview would save straight to
  // Downloads). In a plain browser, download as usual.
  if (window.__TAURI__) {
    try {
      const contents = Array.from(new Uint8Array(await blob.arrayBuffer()));
      await window.__TAURI__.core.invoke('save_file', { filename, contents });
    } catch (err) {
      alert(`Could not save ${filename}: ${err}`);
    }
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

// Download results as JSON or CSV. Both come from the run's saved results, so
// they hold the same data, also after edits and a resume.
const downloadOptions = document.getElementById('download-options');
function showDownloadOptions(show) {
  downloadOptions.hidden = !show;
  downloadBtn.setAttribute('aria-expanded', String(show));
}
downloadBtn.addEventListener('click', e => {
  e.stopPropagation();  // the document's click handler would close it again
  showDownloadOptions(downloadOptions.hidden);
});
document.addEventListener('click', () => showDownloadOptions(false));
downloadOptions.addEventListener('click', async e => {
  const format = e.target.closest('[data-format]')?.dataset.format;
  if (!format || !currentVideoName) return;
  const name = encodeURIComponent(currentVideoName);
  const res = await fetch(format === 'csv' ? `/output/${name}/csv` : `/results/${name}`);
  if (!res.ok) { alert(`Download failed (HTTP ${res.status})`); return; }
  const content = format === 'csv' ? await res.blob() : JSON.stringify(await res.json(), null, 2);
  await triggerDownload(content, `${currentVideoName}_results.${format}`, 'application/json');
});

document.getElementById('mava-export-btn').addEventListener('click', async () => {
  if (!currentVideoName) return;
  const res = await fetch(`/output/${encodeURIComponent(currentVideoName)}/mava`);
  if (!res.ok) { alert(`MAVA export failed (HTTP ${res.status})`); return; }
  // Same name the server sends in Content-Disposition.
  await triggerDownload(await res.blob(), currentVideoName + '_mava_export.zip');
});
