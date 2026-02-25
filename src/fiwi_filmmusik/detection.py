"""Music detection API."""

from abc import ABC, abstractmethod

from fiwi_filmmusik.models import DetectionResult, MusicSegment


class BaseMusicDetectionClient(ABC):
    @abstractmethod
    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in a segment."""
        ...


class DummyDetectionClient(BaseMusicDetectionClient):
    def detect(self, segment: MusicSegment) -> DetectionResult:
        return DetectionResult(segment=segment, title=None, artist=None, confidence=0.0)
