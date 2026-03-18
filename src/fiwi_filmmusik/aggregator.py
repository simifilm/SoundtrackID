"""Chunk aggregation."""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.io import wavfile

from fiwi_filmmusik.models import ClassificationResult, MusicSegment


class ChunkAggregator:
    def __init__(self, gap_tolerance: float = 1.0) -> None:
        self.gap_tolerance = gap_tolerance

    def aggregate(
        self,
        results: list[ClassificationResult],
        full_audio: NDArray[np.float32],
        sample_rate: int,
    ) -> list[MusicSegment]:
        """Merge consecutive music chunks into segments."""
        # Filter and sort music chunks
        music_chunks = sorted(
            [r.chunk for r in results if r.is_music],
            key=lambda c: c.start_time,
        )

        if not music_chunks:
            return []

        # Merge consecutive chunks
        segments: list[tuple[float, float]] = []
        current_start = music_chunks[0].start_time
        current_end = music_chunks[0].end_time

        for chunk in music_chunks[1:]:
            if chunk.start_time - current_end <= self.gap_tolerance:
                current_end = chunk.end_time
            else:
                segments.append((current_start, current_end))
                current_start = chunk.start_time
                current_end = chunk.end_time

        segments.append((current_start, current_end))

        # Extract audio for each segment
        return [
            MusicSegment(
                audio=full_audio[int(start * sample_rate) : int(end * sample_rate)],
                start_time=start,
                end_time=end,
                sample_rate=sample_rate,
            )
            for start, end in segments
        ]


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
