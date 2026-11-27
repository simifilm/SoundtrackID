"""The server's data folder: its runs and batches of runs (cache/), the API keys
entered in the app (settings.json), the uploads awaiting confirmation (staging/)
and its log.

Installed, that is the user's application data folder; from source the
project's output/, so the app lists the CLI's runs too (paths.SOURCE_RUNS_DIR).
Each is found from `root` when it is needed, so a test moves them all at once.
"""

from pathlib import Path

from fastapi import HTTPException

from soundtrackid import paths
from soundtrackid.batches import Batch
from soundtrackid.locks import is_locked
from soundtrackid.runs import Run
from soundtrackid.web import jobs

root = paths.app_data_dir() if paths.FROZEN else paths.SOURCE_RUNS_DIR.parent


def runs_dir() -> Path:
    return root / "cache"


def settings_path() -> Path:
    return root / "settings.json"


def staging_dir() -> Path:
    return root / "staging"


def log_path() -> Path:
    return root / "soundtrackid.log"


def find_run(video_name: str) -> Run:
    """A run by its name: in the cache, or in the folder of a batch there; 404
    when there is none. A batch's folder or its queue is no run."""
    path = _in_cache(video_name)
    if path.is_dir() and not Batch(path).exists():
        return Run(path)
    cache = path.parent
    for folder in cache.iterdir() if cache.is_dir() else ():
        batch = Batch(folder)
        if batch.exists() and (run := batch.run(path.name)) is not None:
            return run
    raise HTTPException(status_code=404, detail="Run not found")


def find_batch(name: str) -> Batch:
    """A batch by its name; 404 when there is none."""
    batch = Batch(_in_cache(name))
    if not batch.exists():
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def under_way(folder: Path) -> bool:
    """Whether a run or batch of the cache is analyzed or resumed now: by a job
    of this server, or by another process (the command line, the app opened
    twice), which holds its lock (locks.py)."""
    return folder.name in jobs.under_way("batch", "batch_resume", "resume") or (
        is_locked(folder)
    )


def _in_cache(name: str) -> Path:
    """The cache's folder of this name, guarding against path traversal: it must
    be a folder's name, so anything else (the cache itself via ".", a sibling
    such as "../cache2") is rejected."""
    cache = runs_dir().resolve()
    path = (cache / name).resolve()
    if path.parent != cache:
        raise HTTPException(status_code=400, detail="Invalid path")
    return path


def read_results(run: Run) -> dict:
    """A run's results, or the error saying why there are none."""
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        return run.load()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read results.json: {exc}"
        )
