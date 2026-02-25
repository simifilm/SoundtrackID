"""Data models for the film music analysis pipeline."""

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray


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


@dataclass
class MusicSegment:
    audio: NDArray[np.float32]
    start_time: float
    end_time: float
    sample_rate: int


@dataclass
class DetectionResult:
    segment: MusicSegment
    title: str | None
    artist: str | None
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SegmentOutput:
    """Output representation of a detected music segment."""

    start: float
    end: float
    audio_file: str
    title: str | None
    artist: str | None
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "audio_file": self.audio_file,
            "title": self.title,
            "artist": self.artist,
            "confidence": self.confidence,
        }


@dataclass
class ResultsOutput:
    """Complete output for a processed video."""

    source: str
    segments: list[SegmentOutput]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "segments": [seg.to_dict() for seg in self.segments],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
