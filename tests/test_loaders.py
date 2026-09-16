"""Tests for video/audio loaders."""

from pathlib import Path

import numpy as np
import pytest

from soundtrackID.loaders import VideoLoader


@pytest.fixture
def test_video_path():
    """Path to test video file."""
    path = Path(__file__).parent / "test_video.mp4"
    if not path.exists():
        pytest.skip("test_video.mp4 not found")
    return path


def test_loader_returns_audio_and_sample_rate(test_video_path):
    """Test that loader returns audio array and sample rate."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, sr = loader.load(test_video_path)

    assert sr == 16000
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert len(audio) > 0


def test_loader_mono_output(test_video_path):
    """Test that mono=True produces 1D array."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, _ = loader.load(test_video_path)

    assert audio.ndim == 1


def test_loader_audio_normalized(test_video_path):
    """Test that audio is normalized to [-1, 1] range."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, _ = loader.load(test_video_path)

    assert audio.min() >= -1.0
    assert audio.max() <= 1.0


def test_loader_duration(test_video_path):
    """Test that loaded audio has expected duration."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, sr = loader.load(test_video_path)

    duration = len(audio) / sr
    # Test video is ~263 seconds
    assert 260 < duration < 270
