"""Data models for the film music analysis pipeline."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.io import wavfile


@dataclass
class AudioChunk:
    data: NDArray[np.float32]
    start_time: float
    end_time: float
    sample_rate: int


@dataclass
class ClassificationResult:
    chunk: AudioChunk
    is_music: bool
    confidence: float
    probs: NDArray[np.float32] | None = None


@dataclass
class MusicSegment:
    audio: NDArray[np.float32]
    start_time: float
    end_time: float
    sample_rate: int

    def write_wav(self, path: str | Path) -> None:
        """The clip as a 16-bit WAV file, as the providers get it and a run
        keeps it for a resume; louder than full scale is clipped."""
        pcm = (np.clip(self.audio, -1.0, 1.0) * 32767).astype(np.int16)
        wavfile.write(path, self.sample_rate, pcm)

    @classmethod
    def read_wav(cls, path: str | Path) -> "MusicSegment":
        """A WAV file as a mono clip starting at 0 s, e.g. one write_wav wrote."""
        sample_rate, audio = wavfile.read(path)
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32767.0
        elif audio.dtype == np.int32:
            audio = audio.astype(np.float32) / 2147483647.0
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return cls(
            audio=audio,
            start_time=0.0,
            end_time=len(audio) / sample_rate,
            sample_rate=sample_rate,
        )


@dataclass
class DetectionResult:
    segment: MusicSegment
    title: str | None
    artist: str | None
    provider: str = "shazam"
    metadata: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False  # not identified: the provider didn't answer (limit, error)


def parse_year(value: Any) -> int | None:
    """The year of a date or a year, as the providers, TMDb, Wikidata,
    MusicBrainz and MP4 tags give it (e.g. "1950-08-04" or 1950): a number
    as it is, else the first four digits of a string."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    m = re.search(r"\d{4}", value)
    return int(m.group()) if m else None
