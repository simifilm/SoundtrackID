"""Music detection API."""

import asyncio
import os
import tempfile
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


if __name__ == "__main__":
    import sys

    from scipy.io import wavfile as scipy_wavfile

    if len(sys.argv) < 2:
        print("Usage: python -m fiwi_filmmusik.detection <wav_file> [wav_file2 ...]")
        sys.exit(1)

    client = ShazamDetectionClient()

    for wav_path in sys.argv[1:]:
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
            print(f"  Title: {result.title}")
            print(f"  Artist: {result.artist}")
            if result.metadata.get("album"):
                print(f"  Album: {result.metadata['album']}")
            if result.metadata.get("genre"):
                print(f"  Genre: {result.metadata['genre']}")
        else:
            print("  No match found")
