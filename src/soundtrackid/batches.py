"""A batch's folder: the films analyzed one after another as a batch
(analysis.analyze_batch), and the one place that knows how it is laid out.

    <runs>/batch_<date>_<time>/
        batch.json          when it started, what it analyzes with (settings),
                            and its films in order: each its file's name
                            (source) and, if the batch didn't take the file
                            over, where it is (path); what is known about the
                            film; and its status: queued, analyzing, done (and
                            its run), failed (and why) or cancelled
        queue/NN/<file>     a film the batch took over (an upload to the app),
                            until its run has it
        <run>/              each film's run (runs.Run), as a single run's
        .lock               held while the batch is analyzed or resumed
                            (locks.py)
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from soundtrackid.locks import FolderLock, is_locked
from soundtrackid.results import save_json
from soundtrackid.runs import Run

_QUEUE = "queue"


class Batch:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        films: list[tuple[Path, dict[str, Any] | None]],
        settings: dict[str, Any],
        take_files: bool = False,
    ) -> "Batch":
        """A new batch's folder in `runs_dir`, named after the time, with its
        films queued: each its file and what is known about the film.

        `settings` are what the batch analyzes with, also when continued: the
        classifier ("onnx" or "hf"), the providers (api), the pipeline's other
        settings (analysis.pipeline_for) and max_segment_duration. With
        `take_files`, the batch takes the files over, moved into its queue (the
        app's uploads), and each film's run keeps its file; else they stay
        where they are, and the batch records where.
        """
        started = datetime.now()
        batch = cls(runs_dir / f"batch_{started:%Y-%m-%d_%H-%M-%S}")
        batch.path.mkdir(parents=True)
        if take_files:
            for number, (file, _) in enumerate(films, start=1):
                dest = batch._queued(number, file.name)
                dest.parent.mkdir(parents=True)
                shutil.move(str(file), str(dest))
        batch.save(
            {
                "started": started.isoformat(timespec="seconds"),
                "settings": settings,
                "films": [
                    {
                        "source": file.name,
                        "path": None if take_files else str(file.resolve()),
                        "film": film,
                        "status": "queued",
                        "run": None,
                        "error": None,
                    }
                    for file, film in films
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

    def lock(self) -> FolderLock:
        """Hold the batch's lock, as its analysis or resume does until it
        ends; locks.Busy when another holds it."""
        return FolderLock(self.path).acquire()

    def under_way(self) -> bool:
        """Whether a process, this one too, is analyzing or resuming the batch."""
        return is_locked(self.path)

    def run(self, name: str) -> Run | None:
        """The batch's run of this name, if it has one."""
        path = self.path / name
        return Run(path) if name != _QUEUE and path.is_dir() else None

    def remove_unfinished_runs(self) -> None:
        """Remove the runs films left unfinished (e.g. the app closed during
        one), so they can be analyzed again."""
        for path in self.path.iterdir():
            run = self.run(path.name)
            if run is not None and not run.exists():
                run.remove()

    # ── The films' files ──────────────────────────────────────────────────

    @staticmethod
    def took_over(film: dict[str, Any]) -> bool:
        """Whether the batch took the film's file over: its run keeps it."""
        return film["path"] is None

    def film_file(self, number: int, film: dict[str, Any]) -> Path:
        """Where the batch's film number `number` (from 1) has its file until
        its run has it: in the queue when the batch took it over."""
        if self.took_over(film):
            return self._queued(number, film["source"])
        return Path(film["path"])

    def dequeue(self, number: int) -> None:
        """Remove film number `number`'s place in the queue, now that its run
        has its file, and the queue once no film waits in it."""
        shutil.rmtree(self.path / _QUEUE / f"{number:02d}", ignore_errors=True)
        try:
            (self.path / _QUEUE).rmdir()
        except OSError:
            pass  # other films still wait

    def _queued(self, number: int, name: str) -> Path:
        return self.path / _QUEUE / f"{number:02d}" / name
