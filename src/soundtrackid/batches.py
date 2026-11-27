"""A batch's folder: the films analyzed one after another as a batch
(analysis.analyze_batch), and the one place that knows how it is laid out.

    <runs>/batch_<date>_<time>/
        batch.json          when it started, and its films in order, each with
                            its status: queued, analyzing, done (and its run),
                            failed (and why) or cancelled
        queue/NN/<file>     a film the batch took over (an upload to the app),
                            until its turn
        <run>/              each film's run (runs.Run), as a single run's
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from soundtrackid.results import save_json
from soundtrackid.runs import Run

_QUEUE = "queue"


class Batch:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(
        cls, runs_dir: Path, films: list[tuple[str, dict[str, Any] | None]]
    ) -> "Batch":
        """A new batch's folder in `runs_dir`, named after the time, with its
        films queued: each its file's name and what is known about the film."""
        started = datetime.now()
        batch = cls(runs_dir / f"batch_{started:%Y-%m-%d_%H-%M-%S}")
        batch.path.mkdir(parents=True)
        batch.save(
            {
                "started": started.isoformat(timespec="seconds"),
                "films": [
                    {
                        "source": source,
                        "film": film,
                        "status": "queued",
                        "run": None,
                        "error": None,
                    }
                    for source, film in films
                ],
            }
        )
        return batch

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def record_path(self) -> Path:
        return self.path / "batch.json"

    def exists(self) -> bool:
        return self.record_path.is_file()

    def load(self) -> dict[str, Any]:
        return json.loads(self.record_path.read_text())

    def save(self, record: dict[str, Any]) -> None:
        save_json(self.record_path, record)

    def run(self, name: str) -> Run | None:
        """The batch's run of this name, if it has one."""
        path = self.path / name
        return Run(path) if name != _QUEUE and path.is_dir() else None

    # ── The films taken over ──────────────────────────────────────────────

    def queue(self, number: int, video: Path) -> Path:
        """Move the batch's film number `number` into its queue, until its turn,
        and return where it is now: named as before, as its run will be."""
        dest = self.path / _QUEUE / f"{number:02d}" / video.name
        dest.parent.mkdir(parents=True)
        shutil.move(str(video), str(dest))
        return dest

    def clear_queue(self) -> None:
        shutil.rmtree(self.path / _QUEUE, ignore_errors=True)
