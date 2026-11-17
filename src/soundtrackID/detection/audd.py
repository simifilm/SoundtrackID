"""AudD: the clip sent to its API, which answers with the match."""

import os
import tempfile

from soundtrackID.detection.base import (
    CHECK_KEYS,
    BaseMusicDetectionClient,
    ProviderAuthError,
    RateLimitError,
)
from soundtrackID.models import DetectionResult, MusicSegment, parse_year


class AudDDetectionClient(BaseMusicDetectionClient):
    """Music detection via AudD (https://audd.io).

    AudD is a catalog-backed, Shazam-class recognizer: a single multipart POST
    of the audio to ``https://api.audd.io/`` returns the matched track with
    Apple Music / Spotify / MusicBrainz metadata (including ISRC, which feeds
    the composer-enrichment chain). No SDK is required.

    The API token comes from the environment: ``AUDD_API_TOKEN``.
    """

    ENDPOINT = "https://api.audd.io/"

    def __init__(self, api_token: str | None = None):
        api_token = api_token or os.environ.get("AUDD_API_TOKEN")
        if not api_token:
            raise RuntimeError(
                "AudD API token missing — set AUDD_API_TOKEN (get a token at "
                "https://dashboard.audd.io)."
            )
        self._token = api_token

    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in segment using AudD."""
        import httpx

        empty = DetectionResult(
            segment=segment, title=None, artist=None, provider="audd"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        try:
            with open(temp_path, "rb") as fh:
                # httpx.Client is created per call — network-bound and safe under
                # the pipeline's concurrent detection threads.
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        self.ENDPOINT,
                        data={
                            "api_token": self._token,
                            "return": "apple_music,spotify,musicbrainz",
                        },
                        files={"file": fh},
                    )
        except httpx.HTTPError as exc:
            # Nobody listened to this segment: stop and allow resume, as
            # ACRCloud does with its code 3000, instead of failing the run.
            raise RateLimitError(f"AudD unreachable: {exc}") from exc
        finally:
            os.unlink(temp_path)

        try:
            payload = resp.json()
        except ValueError as exc:
            # A server error page instead of an answer: also try again later.
            raise RateLimitError(f"AudD error: HTTP {resp.status_code}") from exc
        status = payload.get("status")
        if status == "error":
            err = payload.get("error", {}) or {}
            code = err.get("error_code")
            # 900 = no/invalid api_token: stop the run. 901 = request limit
            # reached: stop the calls and allow resume (as ACRCloud's 3003).
            if code == 900:
                raise ProviderAuthError(
                    f"AudD rejected its API token (error 900: "
                    f"{err.get('error_message')}): " + CHECK_KEYS
                )
            if code == 901:
                raise RateLimitError(f"AudD error {code}: {err.get('error_message')}")
            return empty

        result = payload.get("result")
        if not result:
            return empty

        apple = result.get("apple_music") or {}
        spotify = result.get("spotify") or {}
        mb = result.get("musicbrainz") or []

        mb_first = mb[0] if isinstance(mb, list) and mb else {}
        # Apple Music / Spotify blocks can be missing even when requested; the
        # MusicBrainz recording usually carries the ISRC too.
        mb_isrcs = mb_first.get("isrcs") or []
        isrc = (
            apple.get("isrc")
            or (spotify.get("external_ids") or {}).get("isrc")
            or (mb_isrcs[0] if mb_isrcs else None)
        )
        spotify_url = (spotify.get("external_urls") or {}).get("spotify")
        apple_music_url = apple.get("url")
        mb_id = mb_first.get("id")

        return DetectionResult(
            segment=segment,
            title=result.get("title"),
            artist=result.get("artist"),
            provider="audd",
            metadata={
                "album": result.get("album"),
                "release_year": parse_year(result.get("release_date")),
                "isrc": isrc,
                "genre": None,
                "song_link": result.get("song_link"),
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
                "youtube_link": None,
                "musicbrainz_recording_id": mb_id,
                "musicbrainz_url": (
                    f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None
                ),
            },
        )
