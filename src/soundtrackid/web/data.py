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
from soundtrackid.runs import Run

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
    when there is none.

    Guards against path traversal via video_name: it must be a folder's name,
    so anything else (the cache itself via ".", a sibling such as "../cache2")
    is rejected. A batch's folder or its queue is no run.
    """
    cache = runs_dir().resolve()
    path = (cache / video_name).resolve()
    if path.parent != cache:
        raise HTTPException(status_code=400, detail="Invalid path")
    if path.is_dir() and not Batch(path).exists():
        return Run(path)
    for folder in cache.iterdir() if cache.is_dir() else ():
        batch = Batch(folder)
        if batch.exists() and (run := batch.run(path.name)) is not None:
            return run
    raise HTTPException(status_code=404, detail="Run not found")


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
