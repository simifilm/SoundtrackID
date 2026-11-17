"""ACRCloud: a fingerprint made locally (pyacrcloud), matched online."""

import json
import os
import tempfile
import threading
from typing import cast

from soundtrackid.detection.base import (
    CHECK_KEYS,
    BaseMusicDetectionClient,
    ProviderAuthError,
    RateLimitError,
)
from soundtrackid.models import DetectionResult, MusicSegment, parse_year

# ACRCloud's codes for wrong keys: 3001 missing / invalid access key, 3014
# invalid signature (a wrong access secret).
_ACRCLOUD_KEY_ERRORS = (3001, 3014)


def _parse_acr_candidate(m: dict) -> dict:
    """Parse one ACRCloud music candidate into a flat cue-shaped dict."""
    ext = m.get("external_metadata", {})
    spotify = ext.get("spotify", {}).get("track", {}).get("id")
    youtube = ext.get("youtube", {}).get("vid")
    mb = ext.get("musicbrainz")
    mb_id = mb[0].get("track", {}).get("id") if isinstance(mb, list) and mb else None
    genres = m.get("genres") or []
    release_year = parse_year(m.get("release_date")) or parse_year(
        m.get("album", {}).get("release_date")
    )
    return {
        "title": m.get("title"),
        "artist": ", ".join(a["name"] for a in m.get("artists", []) if a.get("name"))
        or None,
        "album": m.get("album", {}).get("name"),
        "release_year": release_year,
        "isrc": m.get("external_ids", {}).get("isrc"),
        "genre": genres[0].get("name") if genres else None,
        "acrcloud_id": m.get("acrid"),
        "score": float(m.get("score", 0.0)),
        "spotify_url": f"https://open.spotify.com/track/{spotify}" if spotify else None,
        "youtube_link": (
            f"https://www.youtube.com/watch?v={youtube}" if youtube else None
        ),
        "musicbrainz_recording_id": mb_id,
        "musicbrainz_url": (
            f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None
        ),
    }


class ACRCloudDetectionClient(BaseMusicDetectionClient):
    """Music detection via ACRCloud.

    Extracts an ACRCloud fingerprint from each segment locally (via the
    ``pyacrcloud`` SDK) and identifies it against ACRCloud's Music Recognition
    database. Unlike AcoustID, ACRCloud is Shazam-class: it recognises short,
    degraded, film-mixed audio, so it works on individual film cues.

    Requires the ``pyacrcloud`` SDK and a project connected to the ACRCloud
    Music database. Credentials come from the environment:
    ``ACRCLOUD_HOST``, ``ACRCLOUD_ACCESS_KEY``, ``ACRCLOUD_ACCESS_SECRET``.
    """

    def __init__(
        self,
        host: str | None = None,
        access_key: str | None = None,
        access_secret: str | None = None,
        min_score: float = 0.0,
    ):
        host = host or os.environ.get("ACRCLOUD_HOST")
        access_key = access_key or os.environ.get("ACRCLOUD_ACCESS_KEY")
        access_secret = access_secret or os.environ.get("ACRCLOUD_ACCESS_SECRET")
        if not (host and access_key and access_secret):
            raise RuntimeError(
                "ACRCloud credentials missing — set ACRCLOUD_HOST, "
                "ACRCLOUD_ACCESS_KEY and ACRCLOUD_ACCESS_SECRET (create a "
                "Music Recognition project at https://console.acrcloud.com)."
            )
        # Keep the score floor low by default: legitimate film cues can score
        # well below commercial tracks (a real Interstellar cue matched at 55%).
        self._min_score = min_score

        self._config = {
            "host": host,
            "access_key": access_key,
            "access_secret": access_secret,
            "timeout": 10,
        }
        # The pipeline identifies chunks concurrently across worker threads. Give
        # each thread its own ACRCloudRecognizer (construction only stores config,
        # no I/O) to sidestep any non-reentrancy in the native acrcloud_extr_tool.
        self._local = threading.local()
        # Prime the constructing thread's recognizer so a missing pyacrcloud SDK
        # fails here rather than on the first detect().
        _ = self._recognizer

    @property
    def _recognizer(self):
        recognizer = getattr(self._local, "recognizer", None)
        if recognizer is None:
            from acrcloud.recognizer import ACRCloudRecognizer

            recognizer = ACRCloudRecognizer(self._config)
            self._local.recognizer = recognizer
        return recognizer

    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in segment using ACRCloud."""
        empty = DetectionResult(
            segment=segment,
            title=None,
            artist=None,
            provider="acrcloud",
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            segment.write_wav(temp_path)

        duration = max(1, int(round(len(segment.audio) / segment.sample_rate)))
        try:
            raw = self._recognizer.recognize_by_file(temp_path, 0, duration)
        finally:
            os.unlink(temp_path)

        payload = json.loads(cast(str, raw))  # the SDK answers with JSON text
        code = payload.get("status", {}).get("code")
        if code != 0:
            # 1001 = no result; 2xxx = recording/fingerprint issues (tolerate,
            # treat as no match). Wrong keys stop the run; other 3xxx (quota,
            # QpS limit, HTTP error) stop the calls and allow a resume.
            msg = payload.get("status", {}).get("msg")
            if code in _ACRCLOUD_KEY_ERRORS:
                raise ProviderAuthError(
                    f"ACRCloud rejected its API keys (error {code}: {msg}): "
                    + CHECK_KEYS
                )
            if isinstance(code, int) and 3000 <= code < 4000:
                raise RateLimitError(f"ACRCloud error {code}: {msg}")
            return empty

        music = payload.get("metadata", {}).get("music", [])
        if not music:
            return empty

        # ACRCloud returns candidates highest-score first. Parse the top few so a
        # later temporal-rerank pass (which knows the film year) can prefer a
        # period-plausible candidate over an anachronistic top hit.
        candidates = [_parse_acr_candidate(m) for m in music[:5]]
        primary = candidates[0]
        if primary["score"] < self._min_score:
            return empty

        md = {k: v for k, v in primary.items() if k not in ("title", "artist", "score")}
        md["candidates"] = candidates
        return DetectionResult(
            segment=segment,
            title=primary["title"],
            artist=primary["artist"],
            provider="acrcloud",
            metadata=md,
        )
