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

    id: str
    start: float
    duration: float
    conditioning: str
    audio_file: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "duration": self.duration,
            "conditioning": self.conditioning,
            "audio_file": self.audio_file,
            "confidence": self.confidence,
        }


@dataclass
class IdentificationOutput:
    """Output representation of a music identification result."""

    segment_id: str
    provider: str
    title: str | None
    artist: str | None
    album: str | None
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "provider": self.provider,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class ResultsOutput:
    """Complete output for a processed video."""

    source: str
    segments: list[SegmentOutput]
    identifications: list[IdentificationOutput] = field(default_factory=list)
    ID: str | None = None
    fiwi_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ID": self.ID,
            "fiwi_id": self.fiwi_id,
            "source": self.source,
            "segments": [seg.to_dict() for seg in self.segments],
            "identifications": [ident.to_dict() for ident in self.identifications],
        }
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
