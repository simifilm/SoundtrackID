"""Identifying a music clip: one module per provider (ACRCloud, Shazam, AudD),
and an ensemble that lets several of them vote.

build_detection_client is the way in: the app, the CLI and a resume all get
their client from it.
"""

from soundtrackID.detection.acrcloud import ACRCloudDetectionClient
from soundtrackID.detection.audd import AudDDetectionClient
from soundtrackID.detection.base import (
    BaseMusicDetectionClient,
    ProviderAuthError,
    RateLimitError,
)
from soundtrackID.detection.ensemble import PROVIDER_PRIORITY, EnsembleDetectionClient
from soundtrackID.detection.shazam import ShazamDetectionClient

__all__ = [
    "ACRCloudDetectionClient",
    "AudDDetectionClient",
    "BaseMusicDetectionClient",
    "EnsembleDetectionClient",
    "ProviderAuthError",
    "RateLimitError",
    "ShazamDetectionClient",
    "build_detection_client",
]


def _build_single_client(api: str) -> BaseMusicDetectionClient:
    if api == "acrcloud":
        return ACRCloudDetectionClient()
    if api == "shazam":
        return ShazamDetectionClient()
    if api == "audd":
        return AudDDetectionClient()
    raise ValueError(f"Unknown detection API: {api!r}")


def build_detection_client(api: str = "acrcloud") -> BaseMusicDetectionClient:
    """Instantiate a detection client from a provider name or a comma-separated
    list. One name → that single client; several → an EnsembleDetectionClient
    that majority-votes across them. Known names: acrcloud, shazam, audd."""
    names: list[str] = []
    for part in api.split(","):
        part = part.strip()
        if part and part not in names:
            names.append(part)
    if not names:
        raise ValueError("No detection provider specified")
    if len(names) == 1:
        return _build_single_client(names[0])
    ordered = sorted(
        names,
        key=lambda n: PROVIDER_PRIORITY.index(n) if n in PROVIDER_PRIORITY else 99,
    )
    return EnsembleDetectionClient([(n, _build_single_client(n)) for n in ordered])
