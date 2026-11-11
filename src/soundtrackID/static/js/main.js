// Starts the page, once every other script has run. Loaded last.

// Resize observer for debounced redraw + zoom-cap re-clamp
window.addEventListener('resize', () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    drawFilmstrip();
    if (!wfSamples) return;
    const max = effectiveMaxZoom();
    if (wfZoom > max) wfZoom = max;
    redrawAll();
  }, 100);
});


loadApiKeyState();
loadHistory();
