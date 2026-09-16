"""SoundtrackID - Film Music Analysis Pipeline."""

__version__ = "0.1.0"

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.classifiers import BaseClassifier, DummyClassifier, HuggingFaceClassifier
from soundtrackID.config import Config, load_config
from soundtrackID.detection import (
    BaseMusicDetectionClient,
    ShazamDetectionClient,
)
from soundtrackID.isolators import BaseMusicIsolator, DummyIsolator
from soundtrackID.loaders import VideoLoader
from soundtrackID.models import AudioChunk, ClassificationResult, DetectionResult, MusicSegment
from soundtrackID.pipeline import Pipeline

__all__ = [
    "Config",
    "load_config",
    "AudioChunk",
    "ClassificationResult",
    "MusicSegment",
    "DetectionResult",
    "VideoLoader",
    "AudioChunker",
    "BaseClassifier",
    "DummyClassifier",
    "HuggingFaceClassifier",
    "BaseMusicIsolator",
    "DummyIsolator",
    "ChunkAggregator",
    "BaseMusicDetectionClient",
    "ShazamDetectionClient",
    "Pipeline",
]
