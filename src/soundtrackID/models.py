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
    provider: str = "shazam"
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
    enrichment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "provider": self.provider,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "enrichment": self.enrichment,
        }


@dataclass
class ResultsOutput:
    """Complete output for a processed video."""

    source: str
    segments: list[SegmentOutput]
    video_name: str | None = None   # run folder name, used in URLs
    identifications: list[IdentificationOutput] = field(default_factory=list)
    ID: str | None = None
    fiwi_id: str | None = None
    waveform: dict | None = None
    film: dict[str, Any] | None = None
    rate_limited: bool = False   # detection stopped early on a provider quota

    def _build_cues(self) -> list[dict[str, Any]]:
        ident_map = {id_.segment_id: id_ for id_ in self.identifications}
        cues = []
        for seg in self.segments:
            ident = ident_map.get(seg.id)
            cues.append({
                "segment_id": seg.id,
                "start": seg.start,
                "duration": seg.duration,
                "conditioning": seg.conditioning,
                "audio_file": seg.audio_file,
                "confidence": seg.confidence,
                "provider": ident.provider if ident else None,
                "title": ident.title if ident else None,
                "artist": ident.artist if ident else None,
                "album": ident.album if ident else None,
                "release_year": ident.metadata.get("release_year") if ident else None,
                "isrc": ident.metadata.get("isrc") if ident else None,
                "agreement": ident.metadata.get("agreement") if ident else None,
                "votes": ident.metadata.get("votes") if ident else None,
                "candidates": ident.metadata.get("candidates") if ident else None,
                "genre": ident.metadata.get("genre") if ident else None,
                "photo_url": ident.metadata.get("photo_url") if ident else None,
                "youtube_link": ident.metadata.get("youtube_link") if ident else None,
                "spotify_url": ident.metadata.get("spotify_url") if ident else None,
                "apple_music_url": ident.metadata.get("apple_music_url") if ident else None,
                "musicbrainz_recording_id": ident.metadata.get("musicbrainz_recording_id") if ident else None,
                "musicbrainz_url": ident.metadata.get("musicbrainz_url") if ident else None,
                "enrichment": ident.enrichment if ident else None,
            })
        return cues

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ID": self.ID,
            "fiwi_id": self.fiwi_id,
            "source": self.source,
            "video_name": self.video_name,
            "film": self.film,
            "cues": self._build_cues(),
            "waveform": self.waveform,
            "rate_limited": self.rate_limited,
        }
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
