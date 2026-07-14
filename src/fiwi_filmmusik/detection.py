"""Music detection API."""

import asyncio
import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod

import numpy as np
from scipy.io import wavfile

from fiwi_filmmusik.models import DetectionResult, MusicSegment


class BaseMusicDetectionClient(ABC):
    @abstractmethod
    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in a segment."""
        ...


class ShazamDetectionClient(BaseMusicDetectionClient):
    """Music detection using Shazam via shazamio library."""

    def __init__(self, language: str = "en-US"):
        self._language = language

    def detect(self, segment: MusicSegment) -> DetectionResult:
        """Identify music in segment using Shazam."""
        return asyncio.run(self._detect_async(segment))

    async def _detect_async(self, segment: MusicSegment) -> DetectionResult:
        """Async implementation of music detection."""
        from shazamio import Shazam

        # Fresh instance per call; shazamio's aiohttp session is bound to the
        # current event loop; reusing an instance across asyncio.run() calls
        # causes silent failures on the second and subsequent detections.
        shazam = Shazam(language=self._language)

        # Write segment to temp WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(temp_path, segment.sample_rate, audio_int16)

        try:
            # Query Shazam
            result = await shazam.recognize(temp_path)
        finally:
            # Clean up temp file
            os.unlink(temp_path)

        # Parse response
        if not result.get("track"):
            return DetectionResult(
                segment=segment, title=None, artist=None, confidence=0.0
            )

        track = result["track"]

        # Extract album from metadata if available
        album = None
        sections = track.get("sections", [])
        if sections:
            metadata = sections[0].get("metadata", [])
            if metadata:
                album = metadata[0].get("text")

        # Extract track link: YouTube > Spotify > Apple Music
        youtube_link = None
        for section in sections:
            if section.get("type") == "VIDEO":
                youtube_link = section.get("youtubeurl")
                break

        hub = track.get("hub", {})
        providers = hub.get("providers", [])
        spotify_url = providers[0]["actions"][0]["uri"] if providers and providers[0].get("actions") else None

        options = hub.get("options", [])
        apple_music_url = options[0]["actions"][0]["uri"] if options and options[0].get("actions") else None

        return DetectionResult(
            segment=segment,
            title=track.get("title"),
            artist=track.get("subtitle"),  # Artist is in "subtitle" field
            confidence=1.0,  # Shazam doesn't provide confidence scores
            metadata={
                "shazam_key": track.get("key"),
                "album": album,
                "genre": track.get("genres", {}).get("primary"),
                "isrc": track.get("isrc"),
                "photo_url": track.get("images", {}).get("coverart"),
                "youtube_link": youtube_link,
                "spotify_url": spotify_url,
                "apple_music_url": apple_music_url,
            },
        )


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
            segment=segment, title=None, artist=None, confidence=0.0, provider="acrcloud"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(temp_path, segment.sample_rate, audio_int16)

        duration = max(1, int(round(len(segment.audio) / segment.sample_rate)))
        try:
            raw = self._recognizer.recognize_by_file(temp_path, 0, duration)
        finally:
            os.unlink(temp_path)

        payload = json.loads(raw)
        code = payload.get("status", {}).get("code")
        if code != 0:
            # 1001 = no result; 2xxx = recording/fingerprint issues (tolerate,
            # treat as no match). 3xxx = auth/quota misconfiguration — surface it.
            if isinstance(code, int) and 3000 <= code < 4000:
                raise RuntimeError(
                    f"ACRCloud error {code}: {payload.get('status', {}).get('msg')}"
                )
            return empty

        music = payload.get("metadata", {}).get("music", [])
        if not music:
            return empty

        m = music[0]  # ACRCloud returns candidates highest-score first
        score = float(m.get("score", 0.0))
        if score < self._min_score:
            return empty

        title = m.get("title")
        artist = ", ".join(a["name"] for a in m.get("artists", []) if a.get("name")) or None
        album = m.get("album", {}).get("name")

        ext = m.get("external_metadata", {})
        spotify = ext.get("spotify", {}).get("track", {}).get("id")
        youtube = ext.get("youtube", {}).get("vid")
        mb = ext.get("musicbrainz")
        mb_id = mb[0].get("track", {}).get("id") if isinstance(mb, list) and mb else None
        genres = m.get("genres") or []

        return DetectionResult(
            segment=segment,
            title=title,
            artist=artist,
            confidence=score / 100.0,  # ACRCloud score is 0-100
            provider="acrcloud",
            metadata={
                "album": album,
                "isrc": m.get("external_ids", {}).get("isrc"),
                "genre": genres[0].get("name") if genres else None,
                "acrcloud_id": m.get("acrid"),
                "spotify_url": f"https://open.spotify.com/track/{spotify}" if spotify else None,
                "youtube_link": f"https://www.youtube.com/watch?v={youtube}" if youtube else None,
                "musicbrainz_recording_id": mb_id,
                "musicbrainz_url": f"https://musicbrainz.org/recording/{mb_id}" if mb_id else None,
            },
        )


def build_detection_client(api: str = "shazam") -> BaseMusicDetectionClient:
    """Instantiate a detection client by name ("shazam" or "acrcloud")."""
    if api == "acrcloud":
        return ACRCloudDetectionClient()
    if api == "shazam":
        return ShazamDetectionClient()
    raise ValueError(f"Unknown detection API: {api!r}")


if __name__ == "__main__":
    import argparse

    from scipy.io import wavfile as scipy_wavfile

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Identify music in one or more WAV files via Shazam or ACRCloud."
    )
    parser.add_argument("wav_files", nargs="+", help="WAV file(s) to identify")
    parser.add_argument(
        "--api",
        choices=("shazam", "acrcloud"),
        default="shazam",
        help="Detection provider (default: shazam)",
    )
    args = parser.parse_args()

    client = build_detection_client(args.api)
    print(f"Using provider: {args.api}")

    for wav_path in args.wav_files:
        print(f"\nProcessing: {wav_path}")

        # Load WAV file
        sample_rate, audio_data = scipy_wavfile.read(wav_path)

        # Convert to float32 if needed
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32767.0
        elif audio_data.dtype == np.int32:
            audio_data = audio_data.astype(np.float32) / 2147483647.0

        # Handle stereo by taking mean
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)

        segment = MusicSegment(
            audio=audio_data,
            start_time=0.0,
            end_time=len(audio_data) / sample_rate,
            sample_rate=sample_rate,
        )

        result = client.detect(segment)

        if result.title:
            print(f"  Title:      {result.title}")
            print(f"  Artist:     {result.artist}")
            print(f"  Confidence: {result.confidence}")
            if result.metadata.get("album"):
                print(f"  Album:      {result.metadata['album']}")
            if result.metadata.get("genre"):
                print(f"  Genre:      {result.metadata['genre']}")
            if result.metadata.get("musicbrainz_url"):
                print(f"  MusicBrainz: {result.metadata['musicbrainz_url']}")
        else:
            print("  No match found")
