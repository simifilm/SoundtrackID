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


def _music_results(start, end, full_audio, sample_rate, step=5.0):
    """Consecutive music results covering [start, end) in `step`-second slots."""
    results = []
    t = start
    while t < end:
        e = min(t + step, end)
        chunk = AudioChunk(
            data=full_audio[int(t * sample_rate) : int(e * sample_rate)],
            start_time=t,
            end_time=e,
            sample_rate=sample_rate,
        )
        results.append(ClassificationResult(chunk=chunk, is_music=True, confidence=0.9))
        t = e
    return results


def test_stream_windows_anchors_last_window_to_run_end():
    """Each run's last window keeps the full length by overlapping the previous one."""
    sample_rate = 1000
    full_audio = np.arange(60000, dtype=np.float32)
    # Two runs (5-25 s and 40-60 s): one closed by a gap, one by end of input.
    results = _music_results(5.0, 25.0, full_audio, sample_rate) + _music_results(
        40.0, 60.0, full_audio, sample_rate
    )

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    windows = list(
        aggregator.stream_windows(
            results, full_audio, sample_rate, window_duration=15.0
        )
    )

    assert [(w.start_time, w.end_time) for w in windows] == [
        (5.0, 20.0),
        (10.0, 25.0),
        (40.0, 55.0),
        (45.0, 60.0),
    ]
    assert all(len(w.audio) == 15000 for w in windows)


def test_stream_windows_keeps_short_run_short():
    """A run shorter than the window is sent as it is."""
    sample_rate = 1000
    full_audio = np.arange(30000, dtype=np.float32)
    results = _music_results(5.0, 15.0, full_audio, sample_rate)

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    windows = list(
        aggregator.stream_windows(
            results, full_audio, sample_rate, window_duration=15.0
        )
    )

    assert [(w.start_time, w.end_time) for w in windows] == [(5.0, 15.0)]


def test_stream_windows_run_on_window_multiple_needs_no_extra_window():
    """A run that is an exact multiple of the window gets no overlapping window."""
    sample_rate = 1000
    full_audio = np.arange(40000, dtype=np.float32)
    results = _music_results(5.0, 35.0, full_audio, sample_rate)

    aggregator = ChunkAggregator(gap_tolerance=1.0)
    windows = list(
        aggregator.stream_windows(
            results, full_audio, sample_rate, window_duration=15.0
        )
    )

    assert [(w.start_time, w.end_time) for w in windows] == [(5.0, 20.0), (20.0, 35.0)]
