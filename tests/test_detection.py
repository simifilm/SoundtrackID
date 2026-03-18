"""Tests for detection clients."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from fiwi_filmmusik.detection import ShazamDetectionClient
from fiwi_filmmusik.models import MusicSegment


class TestShazamDetectionClient:
    """Tests for ShazamDetectionClient with mocked dependencies."""

    @pytest.fixture
    def mock_shazam(self):
        """Create a ShazamDetectionClient with mocked shazamio."""
        with patch("fiwi_filmmusik.detection.asyncio.run") as mock_run:
            with patch.dict("sys.modules", {"shazamio": MagicMock()}):
                from fiwi_filmmusik.detection import ShazamDetectionClient

                client = ShazamDetectionClient.__new__(ShazamDetectionClient)
                client._shazam = MagicMock()

                yield client, mock_run

    def test_detect_successful_match(self, mock_shazam):
        """Test detect returns track info when Shazam finds a match."""
        client, mock_run = mock_shazam

        # Mock the async detect result
        mock_run.return_value = MagicMock(
            segment=MagicMock(),
            title="Test Song",
            artist="Test Artist",
            confidence=1.0,
            metadata={"shazam_key": "123", "album": "Test Album", "genre": "Pop", "isrc": "US123"},
        )

        segment = MusicSegment(
            audio=np.zeros(16000, dtype=np.float32),
            start_time=0.0,
            end_time=1.0,
            sample_rate=16000,
        )

        result = client.detect(segment)

        assert result.title == "Test Song"
        assert result.artist == "Test Artist"
        assert result.confidence == 1.0
        mock_run.assert_called_once()

    def test_detect_no_match(self, mock_shazam):
        """Test detect returns no match when Shazam doesn't find anything."""
        client, mock_run = mock_shazam

        # Mock no match result
        mock_run.return_value = MagicMock(
            segment=MagicMock(),
            title=None,
            artist=None,
            confidence=0.0,
            metadata={},
        )

        segment = MusicSegment(
            audio=np.zeros(16000, dtype=np.float32),
            start_time=0.0,
            end_time=1.0,
            sample_rate=16000,
        )

        result = client.detect(segment)

        assert result.title is None
        assert result.artist is None
        assert result.confidence == 0.0


class TestShazamDetectionClientAsync:
    """Tests for the async internals of ShazamDetectionClient using asyncio.run."""

    @pytest.fixture
    def segment(self):
        """Create a test segment."""
        return MusicSegment(
            audio=np.random.randn(16000).astype(np.float32) * 0.5,
            start_time=0.0,
            end_time=1.0,
            sample_rate=16000,
        )

    def test_detect_async_successful_match(self, segment):
        """Test _detect_async returns track info when Shazam finds a match."""
        import asyncio

        with patch.dict("sys.modules", {"shazamio": MagicMock()}):
            from fiwi_filmmusik.detection import ShazamDetectionClient

            client = ShazamDetectionClient.__new__(ShazamDetectionClient)
            client._shazam = MagicMock()
            client._shazam.recognize = AsyncMock(
                return_value={
                    "track": {
                        "title": "Test Song",
                        "subtitle": "Test Artist",
                        "key": "12345",
                        "genres": {"primary": "Rock"},
                        "isrc": "US123456789",
                        "sections": [{"metadata": [{"text": "Test Album"}]}],
                    }
                }
            )

            result = asyncio.run(client._detect_async(segment))

            assert result.title == "Test Song"
            assert result.artist == "Test Artist"
            assert result.confidence == 1.0
            assert result.metadata["shazam_key"] == "12345"
            assert result.metadata["genre"] == "Rock"
            assert result.metadata["album"] == "Test Album"
            assert result.metadata["isrc"] == "US123456789"

    def test_detect_async_no_match(self, segment):
        """Test _detect_async returns no match when track is not found."""
        import asyncio

        with patch.dict("sys.modules", {"shazamio": MagicMock()}):
            from fiwi_filmmusik.detection import ShazamDetectionClient

            client = ShazamDetectionClient.__new__(ShazamDetectionClient)
            client._shazam = MagicMock()
            client._shazam.recognize = AsyncMock(return_value={})

            result = asyncio.run(client._detect_async(segment))

            assert result.title is None
            assert result.artist is None
            assert result.confidence == 0.0

    def test_detect_async_partial_metadata(self, segment):
        """Test _detect_async handles missing metadata gracefully."""
        import asyncio

        with patch.dict("sys.modules", {"shazamio": MagicMock()}):
            from fiwi_filmmusik.detection import ShazamDetectionClient

            client = ShazamDetectionClient.__new__(ShazamDetectionClient)
            client._shazam = MagicMock()
            client._shazam.recognize = AsyncMock(
                return_value={
                    "track": {
                        "title": "Test Song",
                        "subtitle": "Test Artist",
                        # No other fields
                    }
                }
            )

            result = asyncio.run(client._detect_async(segment))

            assert result.title == "Test Song"
            assert result.artist == "Test Artist"
            assert result.confidence == 1.0
            assert result.metadata["shazam_key"] is None
            assert result.metadata["genre"] is None
            assert result.metadata["album"] is None
            assert result.metadata["isrc"] is None


def _shazamio_available():
    """Check if shazamio is available."""
    try:
        import shazamio  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _shazamio_available(), reason="shazamio not installed")
class TestShazamDetectionClientIntegration:
    """Integration tests that require actual shazamio."""

    def test_client_creation(self):
        """Test ShazamDetectionClient can be created."""
        from fiwi_filmmusik.detection import ShazamDetectionClient

        client = ShazamDetectionClient()
        assert client._shazam is not None

    def test_client_with_language(self):
        """Test ShazamDetectionClient accepts language parameter."""
        from fiwi_filmmusik.detection import ShazamDetectionClient

        client = ShazamDetectionClient(language="de-DE")
        assert client._shazam is not None
