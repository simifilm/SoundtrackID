"""Fakes for the pipeline's components: the loader, the classifier and the
providers, which the pipeline, analysis, CLI and app tests share."""

import numpy as np

from soundtrackid.detection import ProviderAuthError, RateLimitError
from soundtrackid.models import DetectionResult

SR = 1000
LABELS = ["Speech", "Music", "Applause", "Piano"]


class FakeLoader:
    def load(self, path):
        return np.linspace(-0.5, 0.5, 30 * SR, dtype=np.float32), SR


class FakeClassifier:
    """Only the windows starting at `music_starts` are music: they score 0.6,
    the others 0.05."""

    labels = LABELS
    window_duration = 10.0

    def __init__(self, music_starts=(10.0,)):
        self.music_starts = music_starts

    def classify(self, chunk):
        is_music = chunk.start_time in self.music_starts
        probs = np.array(
            [0.3, 0.6, 0.06, 0.04] if is_music else [0.9, 0.05, 0.03, 0.02],
            dtype=np.float32,
        )
        return float(probs[1]), probs


class NoMatchClient:
    def detect(self, segment):
        return DetectionResult(segment=segment, title=None, artist=None)


class ThrottledClient:
    """Answers "no match" to the first `answers` calls, then refuses every call
    as rate-limited; counts the calls."""

    def __init__(self, answers=0):
        self.answers = answers
        self.calls = 0

    def detect(self, segment):
        self.calls += 1
        if self.calls > self.answers:
            raise RateLimitError("quota exceeded")
        return DetectionResult(segment=segment, title=None, artist=None)


class FailingClient:
    """Fails with an unexpected error on clips starting at `fail_at`, as on a
    provider's malformed answer; matches "X" otherwise."""

    def __init__(self, fail_at):
        self.fail_at = fail_at

    def detect(self, segment):
        if segment.start_time in self.fail_at:
            raise ValueError("malformed answer")
        return DetectionResult(segment=segment, title="X", artist=None)


class RejectingClient:
    """Rejects its keys, as a provider does with a wrong one."""

    def detect(self, segment):
        raise ProviderAuthError("ACRCloud rejected the API keys (error 3014)")


def fake_components(monkeypatch, client=None, classifier=None):
    """Make build_pipeline load audio with FakeLoader and identify with `client`
    (default: no match); returns a stand-in for the app's get_classifier."""
    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", FakeLoader)
    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client",
        lambda api: client or NoMatchClient(),
    )

    return lambda: classifier or FakeClassifier()
