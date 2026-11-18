// Downloading a run's results: JSON, CSV and the VIAN and MAVA exports.

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

// A button opening a menu: a click on the button opens or closes it, any other
// click (also on an option, or on another menu's button) closes it.
function dropdownMenu(button, options) {
  document.addEventListener('click', e => {
    const open = button.contains(e.target) && options.hidden;
    options.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  });
}

// Download results as JSON or CSV. Both come from the run's saved results, so
// they hold the same data, also after edits and a resume.
const downloadOptions = document.getElementById('download-options');
dropdownMenu(downloadBtn, downloadOptions);
downloadOptions.addEventListener('click', async e => {
  const format = e.target.closest('[data-format]')?.dataset.format;
  if (!format || !currentVideoName) return;
  const name = encodeURIComponent(currentVideoName);
  const res = await fetch(format === 'csv' ? `/output/${name}/csv` : `/results/${name}`);
  if (!res.ok) { alert(`Download failed (HTTP ${res.status})`); return; }
  const content = format === 'csv' ? await res.blob() : JSON.stringify(await res.json(), null, 2);
  await triggerDownload(content, `${currentVideoName}_results.${format}`, 'application/json');
});

// The exports for VIAN (a project zip) and MAVA (a .mediapkg), in a menu too.
dropdownMenu(document.getElementById('export-btn'), document.getElementById('export-options'));
async function downloadExport(tool, path, suffix) {
  if (!currentVideoName) return;
  const res = await fetch(`/output/${encodeURIComponent(currentVideoName)}/${path}`);
  if (!res.ok) { alert(`${tool} export failed (HTTP ${res.status})`); return; }
  // Same name the server sends in Content-Disposition.
  await triggerDownload(await res.blob(), currentVideoName + suffix);
}
document.getElementById('vian-export-btn').addEventListener('click',
  () => downloadExport('VIAN', 'vian', '_vian_project.zip'));
document.getElementById('mava-export-btn').addEventListener('click',
  () => downloadExport('MAVA', 'mediapkg', '.mediapkg'));
