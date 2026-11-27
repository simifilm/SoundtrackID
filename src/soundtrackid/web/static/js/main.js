// Starts the page, once every other script has run. Loaded last.

// No browser menu on right-click (Save as, Save video as, …), except in text
// fields, where it holds Copy and Paste.
document.addEventListener('contextmenu', e => {
  if (!e.target.closest('input[type="text"], input[type="password"]')) e.preventDefault();
});

// Back on the page while the server analyzes a film or resumes a run (e.g. the
// page reloaded): show it again, where it is.
async function followJobUnderWay() {
  const jobs = await fetch('/jobs').then(r => r.ok ? r.json() : []).catch(() => []);
  const job = jobs[jobs.length - 1];   // the latest
  if (!job) return;
  if (job.kind === 'resume') {
    if (await openRun(job.name)) followResume(job.id);
  } else if (job.kind === 'batch') {
    showProgress(true);
    followAnalysis(job.id, handleBatchEvent);
  } else {
    showProgress();
    followAnalysis(job.id);
  }
}

loadApiKeyState();
// Then a job under way, whose progress hides the recent runs again.
loadHistory().then(followJobUnderWay);
