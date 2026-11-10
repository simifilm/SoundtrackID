"""Tests for video/audio loaders."""

import numpy as np
import pytest

from soundtrackID.loaders import VideoLoader
from tests.media import write_video

VIDEO_SECONDS = 10
TONE_HZ = 440


@pytest.fixture(scope="module")
def test_video_path(tmp_path_factory):
    """A small generated MP4 with a 440 Hz stereo tone at 48 kHz."""
    path = tmp_path_factory.mktemp("video") / "tone.mp4"
    return write_video(path, VIDEO_SECONDS, TONE_HZ)


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
    # AAC adds a few milliseconds of padding
    assert duration == pytest.approx(VIDEO_SECONDS, abs=0.05)


def test_loader_keeps_pitch(test_video_path):
    """Test that downmixing 48 kHz stereo to 16 kHz mono keeps the tone's pitch."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, sr = loader.load(test_video_path)

    spectrum = np.abs(np.fft.rfft(audio))
    peak_hz = np.argmax(spectrum) * sr / len(audio)
    assert peak_hz == pytest.approx(TONE_HZ, abs=1)
