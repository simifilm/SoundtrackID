"""Tests for chunk aggregator."""

import numpy as np

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.models import AudioChunk, ClassificationResult


def test_aggregate_single_music_chunk():
    """Test aggregation with a single music chunk."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)  # 30 seconds

    chunk = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    results = [ClassificationResult(chunk=chunk, is_music=True, confidence=0.9)]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 1
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 10.0
    assert len(segments[0].audio) == 10000
    assert segments[0].sample_rate == sample_rate


def test_aggregate_consecutive_chunks_merged():
    """Test that consecutive chunks within gap_tolerance are merged."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk1 = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    chunk2 = AudioChunk(
        data=full_audio[10000:20000],
        start_time=10.0,  # No gap
        end_time=20.0,
        sample_rate=sample_rate,
    )
    results = [
        ClassificationResult(chunk=chunk1, is_music=True, confidence=0.9),
        ClassificationResult(chunk=chunk2, is_music=True, confidence=0.8),
    ]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 1
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 20.0
    assert len(segments[0].audio) == 20000


def test_aggregate_gap_within_tolerance():
    """Test that chunks with gap within tolerance are merged."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk1 = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    chunk2 = AudioChunk(
        data=full_audio[11000:21000],
        start_time=11.0,  # 1 second gap (within tolerance)
        end_time=21.0,
        sample_rate=sample_rate,
    )
    results = [
        ClassificationResult(chunk=chunk1, is_music=True, confidence=0.9),
        ClassificationResult(chunk=chunk2, is_music=True, confidence=0.8),
    ]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 1
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 21.0


def test_aggregate_gap_exceeds_tolerance():
    """Test that chunks with gap exceeding tolerance create separate segments."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk1 = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    chunk2 = AudioChunk(
        data=full_audio[12000:22000],
        start_time=12.0,  # 2 second gap (exceeds tolerance)
        end_time=22.0,
        sample_rate=sample_rate,
    )
    results = [
        ClassificationResult(chunk=chunk1, is_music=True, confidence=0.9),
        ClassificationResult(chunk=chunk2, is_music=True, confidence=0.8),
    ]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 2
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 10.0
    assert segments[1].start_time == 12.0
    assert segments[1].end_time == 22.0


def test_aggregate_no_music_chunks():
    """Test aggregation when no chunks are classified as music."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    results = [ClassificationResult(chunk=chunk, is_music=False, confidence=0.9)]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 0


def test_aggregate_filters_non_music():
    """Test that non-music chunks are filtered out."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk1 = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    chunk2 = AudioChunk(
        data=full_audio[10000:20000],
        start_time=10.0,
        end_time=20.0,
        sample_rate=sample_rate,
    )
    chunk3 = AudioChunk(
        data=full_audio[20000:30000],
        start_time=20.0,
        end_time=30.0,
        sample_rate=sample_rate,
    )
    results = [
        ClassificationResult(chunk=chunk1, is_music=True, confidence=0.9),
        ClassificationResult(chunk=chunk2, is_music=False, confidence=0.8),  # Not music
        ClassificationResult(chunk=chunk3, is_music=True, confidence=0.85),
    ]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    # chunk1 and chunk3 have a 10s gap (20-10), so they are separate segments
    assert len(segments) == 2
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 10.0
    assert segments[1].start_time == 20.0
    assert segments[1].end_time == 30.0


def test_aggregate_unsorted_input():
    """Test that results are sorted by start_time before merging."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    chunk1 = AudioChunk(
        data=full_audio[10000:20000],
        start_time=10.0,
        end_time=20.0,
        sample_rate=sample_rate,
    )
    chunk2 = AudioChunk(
        data=full_audio[0:10000],
        start_time=0.0,
        end_time=10.0,
        sample_rate=sample_rate,
    )
    # Input in reverse order
    results = [
        ClassificationResult(chunk=chunk1, is_music=True, confidence=0.9),
        ClassificationResult(chunk=chunk2, is_music=True, confidence=0.8),
    ]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    # Should be merged into one segment despite reverse input order
    assert len(segments) == 1
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 20.0


def test_aggregate_audio_extraction():
    """Test that audio is correctly extracted from full_audio."""
    sample_rate = 1000
    # Create audio with distinct values at different positions
    full_audio = np.arange(30000, dtype=np.float32)

    chunk = AudioChunk(
        data=full_audio[5000:15000],
        start_time=5.0,
        end_time=15.0,
        sample_rate=sample_rate,
    )
    results = [ClassificationResult(chunk=chunk, is_music=True, confidence=0.9)]

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate(results, full_audio, sample_rate)

    assert len(segments) == 1
    # Verify audio values match expected slice
    np.testing.assert_array_equal(segments[0].audio, full_audio[5000:15000])


def test_aggregate_empty_results():
    """Test aggregation with empty results list."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    segments = aggregator.aggregate([], full_audio, sample_rate)

    assert len(segments) == 0
