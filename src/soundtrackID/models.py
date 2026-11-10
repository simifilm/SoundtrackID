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
    probs: NDArray[np.float32] | None = None


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
    skipped: bool = False  # not identified: the provider was rate-limiting


@dataclass
class SegmentOutput:
    """Output representation of a detected music segment."""

    id: str
    start: float
    duration: float
    conditioning: str
    audio_file: str
    confidence: float
    pending: bool = False  # skipped at a rate limit; a resume identifies it

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "duration": self.duration,
            "conditioning": self.conditioning,
            "audio_file": self.audio_file,
            "confidence": self.confidence,
        }


# Detection metadata copied onto every cue, besides provider/title/artist/album.
# Both new runs (ResultsOutput) and resumed ones (app._apply_detection_to_cue)
# build cues from this list, so a resumed cue gets the same fields.
CUE_METADATA_KEYS = (
    "release_year",
    "isrc",
    "agreement",
    "votes",
    "candidates",
    "genre",
    "photo_url",
    "youtube_link",
    "spotify_url",
    "apple_music_url",
    "musicbrainz_recording_id",
    "musicbrainz_url",
)


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
    video_name: str | None = None  # run folder name, used in URLs
    identifications: list[IdentificationOutput] = field(default_factory=list)
    ID: str | None = None
    fiwi_id: str | None = None
    waveform: dict | None = None
    film: dict[str, Any] | None = None
    rate_limited: bool = False  # detection stopped early on a provider quota
    classification: list[dict[str, Any]] | None = None
    classification_windows: list[dict[str, Any]] | None = None

    def _build_cues(self) -> list[dict[str, Any]]:
        ident_map = {id_.segment_id: id_ for id_ in self.identifications}
        cues = []
        for seg in self.segments:
            ident = ident_map.get(seg.id)
            metadata = ident.metadata if ident else {}
            cues.append(
                {
                    "segment_id": seg.id,
                    "start": seg.start,
                    "duration": seg.duration,
                    "conditioning": seg.conditioning,
                    "audio_file": seg.audio_file,
                    "confidence": seg.confidence,
                    "pending": seg.pending,
                    "provider": ident.provider if ident else None,
                    "title": ident.title if ident else None,
                    "artist": ident.artist if ident else None,
                    "album": ident.album if ident else None,
                    **{key: metadata.get(key) for key in CUE_METADATA_KEYS},
                    "enrichment": ident.enrichment if ident else None,
                }
            )
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
            "classification": self.classification,
            "classification_windows": self.classification_windows,
            "rate_limited": self.rate_limited,
        }
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
