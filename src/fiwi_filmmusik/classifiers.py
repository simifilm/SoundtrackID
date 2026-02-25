"""Music classification."""

from abc import ABC, abstractmethod

from fiwi_filmmusik.models import AudioChunk, ClassificationResult


class BaseClassifier(ABC):
    @abstractmethod
    def classify(self, chunk: AudioChunk) -> ClassificationResult:
        """Classify an audio chunk for music presence."""
        ...


class DummyClassifier(BaseClassifier):
    def classify(self, chunk: AudioChunk) -> ClassificationResult:
        return ClassificationResult(chunk=chunk, is_music=True, confidence=1.0)
