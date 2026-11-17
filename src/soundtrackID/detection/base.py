"""What every detection client is, and the errors that stop asking one."""

from abc import ABC, abstractmethod

from soundtrackID.models import DetectionResult, MusicSegment


class RateLimitError(RuntimeError):
    """A provider can't answer for now: it refused the request at its quota or
    rate limit, failed (HTTP 5xx), or couldn't be reached.

    Raised so the pipeline can stop calling that provider, keep whatever it has
    already identified, and offer a resume rather than losing the whole run.
    """


class ProviderAuthError(RuntimeError):
    """A provider rejected its API keys.

    Waiting doesn't fix that, so unlike a RateLimitError it stops the run (or
    resume) at once and says so, instead of leaving cues for a resume that
    would fail the same way.
    """


CHECK_KEYS = "check them under Settings → API keys (the CLI reads .env.dev)"


class BaseMusicDetectionClient(ABC):
    @abstractmethod
    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in a segment."""
        ...
