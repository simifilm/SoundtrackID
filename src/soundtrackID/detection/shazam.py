"""Shazam: Apple's ShazamKit through a native helper, else shazamio."""

import json
import logging
import os
import tempfile
from pathlib import Path

from soundtrackID import paths
from soundtrackID.detection.base import BaseMusicDetectionClient, RateLimitError
from soundtrackID.models import DetectionResult, MusicSegment, parse_year

log = logging.getLogger(__name__)


def _find_shazamkit_helper() -> str | None:
    """Locate the compiled ShazamKit helper binary (macOS only).

    The helper ships as a minimal .app bundle (shazamkit-match.app) so it can
    carry its own provisioning profile, which ShazamKit requires; the bare
    binary is only a fallback (it always gets ShazamKit error 202).

    Order: SHAZAMKIT_HELPER env override → bundle, then bare binary, in
    sys._MEIPASS / next to the bundled binary (frozen app) or in the repo's
    shazamkit/ dir (dev build)."""
    in_bundle = Path("shazamkit-match.app") / "Contents" / "MacOS" / "shazamkit-match"
    override = os.environ.get("SHAZAMKIT_HELPER")
    candidates = [Path(override)] if override else []
    if paths.FROZEN:
        bases = [paths.BUNDLE_DIR, paths.SERVER_DIR]
        candidates += [b / in_bundle for b in bases] + [
            b / "shazamkit-match" for b in bases
        ]
    else:
        shazamkit_dir = paths.SOURCE_ROOT / "shazamkit"
        candidates.append(shazamkit_dir / "build" / in_bundle)
        candidates.append(shazamkit_dir / "shazamkit-match")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _shazamio_http_client():
    """shazamio's default HTTP client, except that Shazam still throttling (HTTP
    429) or failing (5xx) after all its retries, or not reachable, raises
    RateLimitError: shazamio returns such a response, or raises, and either
    reads as "no match"."""
    import asyncio

    import aiohttp
    from aiohttp_retry import ExponentialRetry, RetryClient
    from shazamio.client import HTTPClient
    from shazamio.utils import validate_json

    class Client(HTTPClient):
        async def request(self, method, url, *args, **kwargs):
            try:
                async with RetryClient(
                    retry_options=self.retry_options,
                    raise_for_status=False,
                    trace_configs=[self.trace_config],
                ) as client:
                    async with client.request(method, url, **kwargs) as resp:
                        if resp.status == 429:
                            raise RateLimitError("Shazam rate limit (HTTP 429)")
                        if resp.status >= 500:
                            raise RateLimitError(f"Shazam error: HTTP {resp.status}")
                        return await validate_json(resp, *args)
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                raise RateLimitError(f"Shazam unreachable: {exc!r}") from exc

    # The retries of shazamio's default client: about 12 minutes on a 429.
    return Client(
        retry_options=ExponentialRetry(
            attempts=20, max_timeout=60, statuses={500, 502, 503, 504, 429}
        )
    )


class ShazamDetectionClient(BaseMusicDetectionClient):
    """Music detection via Shazam.

    Prefers Apple's official **ShazamKit** through a small native Swift helper
    (``shazamkit-match.app``). On macOS, ShazamKit only authorizes a caller that
    carries an embedded provisioning profile for an App ID with the ShazamKit App
    Service, so the helper ships as its own small .app with its own profile; an
    unauthorized helper (e.g. an unsigned dev build) gets error 202. On 202 or any
    other error (or off macOS / no helper), this client **falls back to
    shazamio**. A genuine ShazamKit "no match" is trusted and does not fall back.
    """

    _warned = False

    def __init__(self, helper_path: str | None = None, language: str = "en-US"):
        self._helper = helper_path or _find_shazamkit_helper()
        self._language = language
        if not self._helper:
            # Then shazamio is the only way to ask Shazam: fail here rather than
            # with a "no match" for every segment.
            try:
                import shazamio  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    "Shazam without the ShazamKit helper requires the 'shazam' "
                    "extras. Install with: pip install soundtrackID[shazam]"
                ) from exc

    def detect(self, segment: MusicSegment) -> DetectionResult:
        empty = DetectionResult(
            segment=segment, title=None, artist=None, provider="shazam"
        )
        # Try ShazamKit first (best source when available).
        if self._helper:
            result, available = self._detect_shazamkit(self._helper, segment)
            if result is not None:
                return result  # a real match
            if available:
                return empty  # ShazamKit worked and said "no match" — trust it
            # else: ShazamKit unavailable (202 / broke) -> fall through to shazamio
        # Fallback: shazamio.
        return self._detect_shazamio(segment) or empty

    def _detect_shazamkit(self, helper: str, segment: MusicSegment):
        """Return (DetectionResult|None, available). available=False means ShazamKit
        couldn't run (missing entitlement/helper error) so the caller should fall back.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        import subprocess

        try:
            proc = subprocess.run(
                [helper, temp_path], capture_output=True, text=True, timeout=30
            )
        except (subprocess.TimeoutExpired, OSError):
            return None, False
        finally:
            os.unlink(temp_path)

        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None, False

        if data.get("title"):
            return (
                DetectionResult(
                    segment=segment,
                    title=data.get("title"),
                    artist=data.get("artist"),
                    provider="shazam",
                    metadata={
                        "isrc": data.get("isrc"),
                        "apple_music_url": data.get("appleMusicURL"),
                        "shazam_id": data.get("shazamID"),
                    },
                ),
                True,
            )
        # 202 = missing entitlement -> unavailable (fall back). result:null -> a real
        # ShazamKit no-match (available, trust it). Other errors -> unavailable.
        if data.get("code") == 202 or data.get("error"):
            if data.get("code") == 202 and not ShazamDetectionClient._warned:
                ShazamDetectionClient._warned = True
                log.warning(
                    "ShazamKit unavailable (202: no entitlement/profile yet) "
                    "— falling back to shazamio until it is activated."
                )
            return None, False
        return None, True  # {"result": null}: ShazamKit ran, no match

    def _detect_shazamio(self, segment: MusicSegment) -> DetectionResult | None:
        try:
            import asyncio

            from shazamio import Shazam
        except ImportError:
            return None

        async def _run() -> dict:
            shazam = Shazam(
                language=self._language,
                http_client=_shazamio_http_client(),
                segment_duration_seconds=12,
            )
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
                segment.write_wav(path)
            try:
                return await shazam.recognize(path)
            finally:
                os.unlink(path)

        try:
            result = asyncio.run(_run())
        except RateLimitError:
            raise
        except Exception:
            return None

        track = result.get("track")
        if not track:
            return DetectionResult(
                segment=segment,
                title=None,
                artist=None,
                provider="shazam",
            )

        album = release_year = None
        sections = track.get("sections", [])
        if sections:
            meta = sections[0].get("metadata", [])
            if meta:
                album = meta[0].get("text")
            for item in meta:
                if str(item.get("title", "")).strip().lower() == "released":
                    release_year = parse_year(item.get("text"))
                    break
        youtube_link = next(
            (s.get("youtubeurl") for s in sections if s.get("type") == "VIDEO"), None
        )
        hub = track.get("hub", {})
        providers = hub.get("providers", [])
        spotify_url = (
            providers[0]["actions"][0]["uri"]
            if providers and providers[0].get("actions")
            else None
        )
        options = hub.get("options", [])
        apple_music_url = (
            options[0]["actions"][0]["uri"]
            if options and options[0].get("actions")
            else None
        )
        return DetectionResult(
            segment=segment,
            title=track.get("title"),
            artist=track.get("subtitle"),
            provider="shazam",
            metadata={
                "album": album,
                "release_year": release_year,
                "genre": track.get("genres", {}).get("primary"),
                "isrc": track.get("isrc"),
                "photo_url": track.get("images", {}).get("coverart"),
                "youtube_link": youtube_link,
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
            },
        )
