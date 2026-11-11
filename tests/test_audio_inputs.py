"""Tests for audio-only uploads (mp3, wav) instead of video files."""

import io
from pathlib import Path

import av
import numpy as np
import pytest

from soundtrackID.loaders import VideoLoader
from soundtrackID.thumbnails import extract_filmstrip

SR = 44100
DURATION = 3.0


def _write_tone(path: Path, codec: str) -> Path:
    """Write a 440 Hz stereo tone with PyAV."""
    t = np.arange(int(SR * DURATION)) / SR
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([tone, tone])
    if codec == "pcm_s16le":
        fmt, data = "s16", (stereo.T.reshape(1, -1) * 32767).astype(np.int16)
    else:
        fmt, data = "fltp", stereo

    container = av.open(str(path), "w")
    stream = container.add_stream(codec, rate=SR)
    stream.layout = "stereo"
    frame = av.AudioFrame.from_ndarray(data, format=fmt, layout="stereo")
    frame.sample_rate = SR
    for packet in [*stream.encode(frame), *stream.encode(None)]:
        container.mux(packet)
    container.close()
    return path


@pytest.mark.parametrize(
    ("filename", "codec"), [("a.wav", "pcm_s16le"), ("a.mp3", "mp3")]
)
def test_loader_reads_audio_files(tmp_path, filename, codec):
    """Audio-only files load like the audio track of a video."""
    path = _write_tone(tmp_path / filename, codec)
    audio, sr = VideoLoader(target_sample_rate=16000, mono=True).load(path)

    assert sr == 16000
    assert audio.ndim == 1
    assert audio.dtype == np.float32
    assert abs(len(audio) / sr - DURATION) < 0.1


def test_filmstrip_none_for_audio_only(tmp_path):
    path = _write_tone(tmp_path / "a.wav", "pcm_s16le")
    assert extract_filmstrip(path, tmp_path / "filmstrip.jpg") is None


def test_filmstrip_ignores_mp3_cover_art(tmp_path):
    """Embedded cover art is exposed as a video stream but is not a video."""
    pytest.importorskip("PIL")
    mutagen_id3 = pytest.importorskip("mutagen.id3")
    from PIL import Image

    path = _write_tone(tmp_path / "a.mp3", "mp3")
    cover = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 50, 50)).save(cover, format="JPEG")
    tags = mutagen_id3.ID3()
    tags.add(
        mutagen_id3.APIC(encoding=3, mime="image/jpeg", type=3, data=cover.getvalue())
    )
    tags.save(str(path))

    with av.open(str(path)) as container:
        assert container.streams.video  # precondition: cover shows up as video
    assert extract_filmstrip(path, tmp_path / "filmstrip.jpg") is None
