"""Chunk aggregation."""

from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.io import wavfile

from fiwi_filmmusik.models import ClassificationResult, MusicSegment


class ChunkAggregator:
    def __init__(self, gap_tolerance: float = 1.0) -> None:
        self.gap_tolerance = gap_tolerance

    def stream_windows(
        self,
        results: Iterable[ClassificationResult],
        full_audio: NDArray[np.float32],
        sample_rate: int,
        window_duration: float | None = None,
    ) -> Iterator[MusicSegment]:
        """Yield music windows lazily, as soon as each is fully classified.

        Consumes ``results`` in chunk order and tracks the running music segment
        exactly as :meth:`stream`/:meth:`aggregate` (chunks separated by gaps
        <= ``gap_tolerance`` merge into one run). Within a run, if
        ``window_duration`` is set, each fixed-length window is emitted the moment
        classification has advanced past its end — so identification can overlap
        classification even for a single long, uninterrupted music cue. The
        trailing (partial) window is emitted when the run closes (a gap larger
        than ``gap_tolerance``) or when ``results`` is exhausted. With
        ``window_duration=None`` this degenerates to whole-segment streaming.

        Assumes music chunks arrive in ``start_time`` order, which the pipeline's
        sequential chunker guarantees.
        """
        run_start: float | None = None
        run_end: float | None = None
        emitted_until: float | None = None  # end of the last window emitted in this run

        def build(start: float, end: float) -> MusicSegment:
            return MusicSegment(
                audio=full_audio[int(start * sample_rate) : int(end * sample_rate)],
                start_time=start,
                end_time=end,
                sample_rate=sample_rate,
            )

        for result in results:
            if not result.is_music:
                continue
            chunk = result.chunk
            if run_start is None:
                run_start, run_end, emitted_until = chunk.start_time, chunk.end_time, chunk.start_time
            elif chunk.start_time - run_end <= self.gap_tolerance:
                run_end = chunk.end_time
            else:
                # Run closed by a gap: emit its trailing window, start a new run.
                if run_end > emitted_until:
                    yield build(emitted_until, run_end)
                run_start, run_end, emitted_until = chunk.start_time, chunk.end_time, chunk.start_time

            if window_duration:
                while run_end - emitted_until >= window_duration:
                    yield build(emitted_until, emitted_until + window_duration)
                    emitted_until += window_duration

        if run_start is not None and run_end > emitted_until:
            yield build(emitted_until, run_end)

    def stream(
        self,
        results: Iterable[ClassificationResult],
        full_audio: NDArray[np.float32],
        sample_rate: int,
    ) -> Iterator[MusicSegment]:
        """Merge consecutive music chunks into whole segments, yielding each as it
        finalizes (a gap larger than ``gap_tolerance``, or end of input)."""
        yield from self.stream_windows(results, full_audio, sample_rate, window_duration=None)

    def aggregate(
        self,
        results: list[ClassificationResult],
        full_audio: NDArray[np.float32],
        sample_rate: int,
    ) -> list[MusicSegment]:
        """Merge consecutive music chunks into segments."""
        # Sort music chunks by start time so streaming sees them in order, then
        # reuse the single-source merge logic in ``stream``.
        ordered = sorted(results, key=lambda r: r.chunk.start_time)
        return list(self.stream(ordered, full_audio, sample_rate))


def export_segments(
    segments: list[MusicSegment],
    output_dir: Path,
    prefix: str = "segment",
) -> list[Path]:
    """Export music segments as WAV files for verification.

    Args:
        segments: List of music segments to export.
        output_dir: Directory to save the WAV files.
        prefix: Filename prefix for exported files.

    Returns:
        List of paths to the exported files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []

    for i, seg in enumerate(segments):
        filename = f"{prefix}_{i:03d}_{seg.start_time:.1f}s-{seg.end_time:.1f}s.wav"
        filepath = output_dir / filename

        # Convert float32 [-1, 1] to int16 for WAV export
        audio_int16 = (seg.audio * 32767).astype(np.int16)
        wavfile.write(filepath, seg.sample_rate, audio_int16)
        exported.append(filepath)

    return exported


def export_non_music_chunks(
    results: list[ClassificationResult],
    output_dir: Path,
    prefix: str = "non_music",
) -> list[Path]:
    """Export non-music chunks as WAV files for double-checking.

    Args:
        results: List of classification results.
        output_dir: Directory to save the WAV files.
        prefix: Filename prefix for exported files.

    Returns:
        List of paths to the exported files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []

    non_music = [r for r in results if not r.is_music]

    for i, result in enumerate(non_music):
        chunk = result.chunk
        filename = f"{prefix}_{i:03d}_{chunk.start_time:.1f}s-{chunk.end_time:.1f}s.wav"
        filepath = output_dir / filename

        audio_int16 = (chunk.data * 32767).astype(np.int16)
        wavfile.write(filepath, chunk.sample_rate, audio_int16)
        exported.append(filepath)

    return exported


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from fiwi_filmmusik.chunker import AudioChunker
    from fiwi_filmmusik.classifiers import HuggingFaceClassifier
    from fiwi_filmmusik.loaders import VideoLoader

    path = sys.argv[1] if len(sys.argv) > 1 else "tests/test_video.mp4"

    loader = VideoLoader()
    chunker = AudioChunker(chunk_duration=10.0, overlap=2.0)
    classifier = HuggingFaceClassifier(music_labels=["Music"], threshold=0.2)
    aggregator = ChunkAggregator(gap_tolerance=1.0)

    print(f"Loading: {path}")
    audio, sample_rate = loader.load(Path(path))
    print(f"Duration: {len(audio) / sample_rate:.1f}s")

    print("Classifying chunks...")
    results = []
    for chunk in chunker.chunk(audio, sample_rate):
        result = classifier.classify(chunk)
        status = "MUSIC" if result.is_music else "-----"
        print(f"  {chunk.start_time:5.1f}s - {chunk.end_time:5.1f}s: {status} ({result.confidence*100:.1f}%)")
        results.append(result)

    print("Aggregating segments...")
    segments = aggregator.aggregate(results, audio, sample_rate)

    print(f"\nFound {len(segments)} music segment(s):")
    for seg in segments:
        print(f"  {seg.start_time:.1f}s - {seg.end_time:.1f}s ({seg.end_time - seg.start_time:.1f}s)")

    output_dir = Path("output/segments")
    exported = export_segments(segments, output_dir)
    print(f"\nExported {len(exported)} music segment(s) to {output_dir}/")
    for filepath in exported:
        print(f"  {filepath.name}")

    non_music_dir = Path("output/non_music")
    non_music_exported = export_non_music_chunks(results, non_music_dir)
    print(f"\nExported {len(non_music_exported)} non-music chunk(s) to {non_music_dir}/")
    for filepath in non_music_exported:
        print(f"  {filepath.name}")
