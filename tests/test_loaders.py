"""Tests for video/audio loaders."""

from fractions import Fraction

import av
import numpy as np
import pytest

from soundtrackID.loaders import VideoLoader

VIDEO_SECONDS = 10
TONE_HZ = 440


@pytest.fixture(scope="module")
def test_video_path(tmp_path_factory):
    """A small MP4: tiny video plus a 440 Hz stereo tone at 48 kHz.

    Generated instead of checked in, so the tests need no film footage.
    """
    path = tmp_path_factory.mktemp("video") / "tone.mp4"
    sr = 48000
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = container.add_stream("aac", rate=sr)
        audio.layout = "stereo"

        for i in range(VIDEO_SECONDS * 10):
            pixels = np.full((48, 64, 3), i, np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            container.mux(video.encode(frame))

        t = np.arange(VIDEO_SECONDS * sr) / sr
        tone = (0.5 * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.float32)
        stereo = np.stack([tone, tone])
        for start in range(0, stereo.shape[1], 1024):
            chunk = np.ascontiguousarray(stereo[:, start : start + 1024])
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate = sr
            frame.pts = start
            frame.time_base = Fraction(1, sr)
            container.mux(audio.encode(frame))

        container.mux(video.encode())
        container.mux(audio.encode())
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
    # AAC adds a few milliseconds of padding
    assert duration == pytest.approx(VIDEO_SECONDS, abs=0.05)


def test_loader_keeps_pitch(test_video_path):
    """Test that downmixing 48 kHz stereo to 16 kHz mono keeps the tone's pitch."""
    loader = VideoLoader(target_sample_rate=16000, mono=True)
    audio, sr = loader.load(test_video_path)

    spectrum = np.abs(np.fft.rfft(audio))
    peak_hz = np.argmax(spectrum) * sr / len(audio)
    assert peak_hz == pytest.approx(TONE_HZ, abs=1)
