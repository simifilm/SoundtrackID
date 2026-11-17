// The analysis settings and the API keys the providers need.

// Settings wiring
document.getElementById('opt-threshold').addEventListener('input', function() {
  document.getElementById('opt-threshold-val').textContent = this.value + '%';
});

// ── API keys settings ─────────────────────────────────────────────────────
// Key fields each provider needs (Shazam needs none).
const PROVIDER_KEYS = {
  acrcloud: ['acrcloud_host', 'acrcloud_access_key', 'acrcloud_access_secret'],
  audd: ['audd_api_token'],
};
let apiKeyState = null;  // last /settings response

// Labels of providers with a missing key, e.g. ['ACRCloud'].
function providersMissingKeys(providers = selectedProviders()) {
  if (!apiKeyState) return [];
  return providers
    .filter(p => (PROVIDER_KEYS[p] || []).some(k => !apiKeyState[k]?.configured))
    .map(p => PROVIDER_LABELS[p]);
}

function updateProviderKeyWarning() {
  const missing = providersMissingKeys();
  const el = document.getElementById('provider-key-warning');
  el.hidden = missing.length === 0;
  el.textContent = `API keys missing for ${missing.join(' and ')}. Add them under API keys below.`;
}
document.querySelectorAll('.opt-provider').forEach(cb =>
  cb.addEventListener('change', updateProviderKeyWarning));

async function loadApiKeyState() {
  try {
    const r = await fetch('/settings');
    if (!r.ok) return;
    const state = await r.json();
    apiKeyState = state;
    document.querySelectorAll('#api-keys input[data-key]').forEach(inp => {
      const s = state[inp.dataset.key];
      inp.value = '';
      inp.placeholder = (s && s.configured) ? s.preview : 'not set';
    });
    updateProviderKeyWarning();
  } catch (_) { /* offline is fine */ }
}


document.getElementById('save-keys').addEventListener('click', async () => {
  const status = document.getElementById('keys-status');
  // Send only fields the user typed into; blank fields keep their saved value.
  const payload = {};
  document.querySelectorAll('#api-keys input[data-key]').forEach(inp => {
    if (inp.value.trim() !== '') payload[inp.dataset.key] = inp.value.trim();
  });
  if (Object.keys(payload).length === 0) { status.textContent = 'Nothing to save.'; return; }
  status.textContent = 'Saving...';
  try {
    const r = await fetch('/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error('server returned ' + r.status);
    status.textContent = 'Saved. New analyses will use these keys.';
    await loadApiKeyState();
  } catch (err) {
    status.textContent = 'Could not save: ' + err.message;
  }
});
