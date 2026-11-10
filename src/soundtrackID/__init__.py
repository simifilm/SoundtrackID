"""SoundtrackID - Film Music Analysis Pipeline."""

__version__ = "0.1.0"

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.classifiers import (
    BaseClassifier,
    HuggingFaceClassifier,
)
from soundtrackID.detection import (
    BaseMusicDetectionClient,
    ShazamDetectionClient,
)
from soundtrackID.isolators import BaseMusicIsolator, DummyIsolator
from soundtrackID.loaders import VideoLoader
from soundtrackID.models import (
    AudioChunk,
    ClassificationResult,
    DetectionResult,
    MusicSegment,
)
from soundtrackID.pipeline import Pipeline

__all__ = [
    "AudioChunk",
    "ClassificationResult",
    "MusicSegment",
    "DetectionResult",
    "VideoLoader",
    "AudioChunker",
    "BaseClassifier",
    "HuggingFaceClassifier",
    "BaseMusicIsolator",
    "DummyIsolator",
    "ChunkAggregator",
    "BaseMusicDetectionClient",
    "ShazamDetectionClient",
    "Pipeline",
]
