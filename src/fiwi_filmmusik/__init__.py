"""FIWI Filmmusik - Film Music Analysis Pipeline."""

__version__ = "0.1.0"

from fiwi_filmmusik.aggregator import ChunkAggregator
from fiwi_filmmusik.chunker import AudioChunker
from fiwi_filmmusik.classifiers import BaseClassifier, DummyClassifier, HuggingFaceClassifier
from fiwi_filmmusik.config import Config, load_config
from fiwi_filmmusik.detection import (
    BaseMusicDetectionClient,
    ShazamDetectionClient,
)
from fiwi_filmmusik.isolators import BaseMusicIsolator, DummyIsolator
from fiwi_filmmusik.loaders import VideoLoader
from fiwi_filmmusik.models import AudioChunk, ClassificationResult, DetectionResult, MusicSegment
from fiwi_filmmusik.pipeline import Pipeline

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
