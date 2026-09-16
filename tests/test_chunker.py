"""Tests for audio chunker."""

import numpy as np

from soundtrackID.chunker import AudioChunker


def test_chunk_basic():
    """Test basic chunking with known values."""
    sample_rate = 1000  # 1000 samples/sec for easy math
    duration = 26.0  # 26 seconds of audio
    audio = np.zeros(int(duration * sample_rate), dtype=np.float32)

    chunker = AudioChunker(chunk_duration=10.0, overlap=2.0)
    chunks = list(chunker.chunk(audio, sample_rate))

    # With 26s audio, 10s chunks, 8s step: chunks at 0s, 8s, 16s
    # Chunk at 16s ends at 26s which exactly fits
    assert len(chunks) == 3

    assert chunks[0].start_time == 0.0
    assert chunks[0].end_time == 10.0

    assert chunks[1].start_time == 8.0
    assert chunks[1].end_time == 18.0

    assert chunks[2].start_time == 16.0
    assert chunks[2].end_time == 26.0


def test_chunk_data_size():
    """Test that chunk data has correct size."""
    sample_rate = 44100
    audio = np.ones(sample_rate * 30, dtype=np.float32)  # 30 seconds

    chunker = AudioChunker(chunk_duration=10.0, overlap=2.0)
    chunks = list(chunker.chunk(audio, sample_rate))

    expected_samples = int(10.0 * sample_rate)
    for chunk in chunks:
        assert len(chunk.data) == expected_samples
        assert chunk.sample_rate == sample_rate


def test_chunk_short_audio():
    """Test that audio shorter than chunk_duration returns empty."""
    sample_rate = 1000
    audio = np.zeros(5000, dtype=np.float32)  # 5 seconds

    chunker = AudioChunker(chunk_duration=10.0, overlap=2.0)
    chunks = list(chunker.chunk(audio, sample_rate))

    assert len(chunks) == 0


def test_chunk_exact_duration():
    """Test audio exactly equal to chunk_duration."""
    sample_rate = 1000
    audio = np.zeros(10000, dtype=np.float32)  # exactly 10 seconds

    chunker = AudioChunker(chunk_duration=10.0, overlap=2.0)
    chunks = list(chunker.chunk(audio, sample_rate))

    assert len(chunks) == 1
    assert chunks[0].start_time == 0.0
    assert chunks[0].end_time == 10.0
