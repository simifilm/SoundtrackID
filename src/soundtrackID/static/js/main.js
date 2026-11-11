// Starts the page, once every other script has run. Loaded last.

// No browser menu on right-click (Save as, Save video as, …), except in text
// fields, where it holds Copy and Paste.
document.addEventListener('contextmenu', e => {
  if (!e.target.closest('input[type="text"], input[type="password"]')) e.preventDefault();
});

loadApiKeyState();
loadHistory();
