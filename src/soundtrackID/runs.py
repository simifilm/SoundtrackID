"""A run's folder: what an analysis keeps of a film, and the one place that
knows how it is laid out.

    <runs>/<name>/
        results.json            the cues and what is known about the film
        timeline.json           the waveform and the music classification
        source.<ext>            the film, for the app's preview and a re-run
        filmstrip.jpg           thumbnails of the film
        segments/seg_NNN.wav    the audio of a cue still pending, for a resume
"""

import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile

from soundtrackID.models import TIMELINE_KEYS, MusicSegment, load_results, save_json

# One lock per run folder, so the saves of a run in this process (an edit, the
# end of a resume) never read what the other is about to overwrite.
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


class Run:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, path: Path) -> "Run":
        """A new run's folder. Not one that exists: a failed run removes its
        folder, so it must not be another run's."""
        path.mkdir(parents=True)
        return cls(path)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def results_path(self) -> Path:
        return self.path / "results.json"

    def exists(self) -> bool:
        """Whether the run is complete: its results are saved only then."""
        return self.results_path.is_file()

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    # ── Results and timeline ──────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """The run's results, in the current format; reading changes nothing."""
        return load_results(self.results_path)

    def save(self, results: dict[str, Any]) -> None:
        with self._lock():
            self._save(results)

    def update(self, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Load the results, `change` them and save them, with no other save of
        this run in this process in between; return them. A `change` that
        raises leaves the saved results as they were."""
        with self._lock():
            results = self.load()
            change(results)
            self._save(results)
            return results

    def timeline(self) -> dict[str, Any]:
        """The waveform and the classification; in results.json for a run saved
        before they had a file of their own."""
        path = self.path / "timeline.json"
        source = path if path.exists() else self.results_path
        data = json.loads(source.read_text())
        return {k: data[k] for k in TIMELINE_KEYS if k in data}

    def save_timeline(self, timeline: dict[str, Any]) -> None:
        save_json(self.path / "timeline.json", timeline)

    def _save(self, results: dict[str, Any]) -> None:
        timeline_path = self.path / "timeline.json"
        if not timeline_path.exists() and self.results_path.exists():
            # Saved before the timeline had a file of its own: moved there now,
            # as the results saved now leave it out.
            timeline = self.timeline()
            if timeline:
                save_json(timeline_path, timeline)
        save_json(self.results_path, results)

    def _lock(self) -> threading.Lock:
        key = self.path.resolve()
        with _locks_guard:
            return _locks.setdefault(key, threading.Lock())

    # ── The film ──────────────────────────────────────────────────────────

    @property
    def video(self) -> Path | None:
        sources = sorted(self.path.glob("source.*")) if self.path.is_dir() else []
        return sources[0] if sources else None

    def keep_video(self, video: Path) -> Path:
        """Move the film into the run and return where it is now."""
        dest = self.path / f"source{video.suffix or '.mp4'}"
        shutil.move(str(video), str(dest))
        return dest

    @property
    def filmstrip_path(self) -> Path:
        return self.path / "filmstrip.jpg"

    # ── Audio of the pending cues ─────────────────────────────────────────

    def write_audio(self, segment_id: str, clip: MusicSegment) -> str:
        """Keep the audio of a pending cue for a resume; return its audio_file."""
        audio_file = f"segments/{segment_id}.wav"
        path = self.path / audio_file
        path.parent.mkdir(exist_ok=True)
        wavfile.write(path, clip.sample_rate, _to_int16(clip.audio))
        return audio_file

    def read_audio(self, audio_file: str) -> MusicSegment | None:
        """The audio a cue's audio_file names, starting at 0 s; None when it is
        gone, or outside the run's segments folder."""
        path = self._audio_path(audio_file)
        if path is None or not path.is_file():
            return None
        sample_rate, audio = wavfile.read(path)
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32767.0
        elif audio.dtype == np.int32:
            audio = audio.astype(np.float32) / 2147483647.0
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return MusicSegment(
            audio=audio,
            start_time=0.0,
            end_time=len(audio) / sample_rate,
            sample_rate=sample_rate,
        )

    def remove_audio(self, audio_file: str) -> None:
        path = self._audio_path(audio_file)
        if path is not None:
            path.unlink(missing_ok=True)

    def _audio_path(self, audio_file: str) -> Path | None:
        segments = (self.path / "segments").resolve()
        path = (self.path / audio_file).resolve()
        return path if path.parent == segments else None


def _to_int16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
