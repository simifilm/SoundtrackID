"""Pipeline orchestrator."""

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from fiwi_filmmusik.aggregator import ChunkAggregator
from fiwi_filmmusik.chunker import AudioChunker
from fiwi_filmmusik.classifiers import BaseClassifier
from fiwi_filmmusik.detection import BaseMusicDetectionClient
from fiwi_filmmusik.isolators import BaseMusicIsolator, DummyIsolator
from fiwi_filmmusik.loaders import VideoLoader
from fiwi_filmmusik.models import (
    DetectionResult,
    IdentificationOutput,
    MusicSegment,
    ResultsOutput,
    SegmentOutput,
)


class OutputWriter:
    """Handles writing detection results to disk."""

    def __init__(self, output_dir: Path, conditioning: str = "original", export_csv: bool = False) -> None:
        self.output_dir = output_dir
        self.conditioning = conditioning
        self.export_csv = export_csv

    def write(
        self,
        video_path: Path,
        results: list[tuple[MusicSegment, DetectionResult]],
    ) -> ResultsOutput:
        """Write detection results to disk.

        Creates:
        - output_dir/<video_name>/results.json
        - output_dir/<video_name>/segments/seg_001.wav
        """
        video_name = video_path.stem
        video_output_dir = self.output_dir / video_name
        segments_dir = video_output_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        segment_outputs: list[SegmentOutput] = []
        identification_outputs: list[IdentificationOutput] = []

        for i, (segment, detection) in enumerate(results):
            segment_id = f"seg_{i + 1:03d}"
            audio_filename = f"{segment_id}.wav"
            audio_path = segments_dir / audio_filename

            # Write segment audio to WAV
            audio_int16 = (np.clip(segment.audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(audio_path, segment.sample_rate, audio_int16)

            # Calculate confidence from classification (average would need to be passed through)
            # For now, use detection confidence or 1.0 if detected
            confidence = detection.confidence if detection.title else 0.5

            segment_output = SegmentOutput(
                id=segment_id,
                start=segment.start_time,
                duration=segment.end_time - segment.start_time,
                conditioning=self.conditioning,
                audio_file=f"segments/{audio_filename}",
                confidence=confidence,
            )
            segment_outputs.append(segment_output)

            # Add identification if music was detected
            if detection.title:
                identification = IdentificationOutput(
                    segment_id=segment_id,
                    provider="shazam",
                    title=detection.title,
                    artist=detection.artist,
                    album=detection.metadata.get("album"),
                    confidence=detection.confidence,
                    metadata={
                        k: v
                        for k, v in detection.metadata.items()
                        if k != "album" and v is not None
                    },
                )
                identification_outputs.append(identification)

        results_output = ResultsOutput(
            source=str(video_path),
            segments=segment_outputs,
            identifications=identification_outputs,
        )

        # Write results.json
        results_path = video_output_dir / "results.json"
        results_path.write_text(results_output.to_json())
        print(f"  Results written to: {results_path}")

        # Optionally write results.csv
        if self.export_csv:
            import csv
            ident_map = {id_.segment_id: id_ for id_ in identification_outputs}
            csv_path = video_output_dir / "results.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["segment_id", "start", "duration", "conditioning", "confidence", "provider", "title", "artist", "album"])
                for seg in segment_outputs:
                    ident = ident_map.get(seg.id)
                    writer.writerow([
                        seg.id,
                        seg.start,
                        seg.duration,
                        seg.conditioning,
                        seg.confidence,
                        ident.provider if ident else "",
                        ident.title if ident else "",
                        ident.artist if ident else "",
                        ident.album if ident else "",
                    ])
            print(f"  CSV written to: {csv_path}")

        return results_output


class Pipeline:
    def __init__(
        self,
        loader: VideoLoader,
        chunker: AudioChunker,
        classifier: BaseClassifier,
        aggregator: ChunkAggregator,
        isolator: BaseMusicIsolator,
        detection_client: BaseMusicDetectionClient,
        output_dir: Path | None = None,
        export_csv: bool = False,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.classifier = classifier
        self.aggregator = aggregator
        self.isolator = isolator
        self.detection_client = detection_client
        self.output_dir = output_dir or Path("./output")

        # Determine conditioning based on isolator type
        conditioning = "original" if isinstance(isolator, DummyIsolator) else "vocals_removed"
        self.output_writer = OutputWriter(self.output_dir, conditioning=conditioning, export_csv=export_csv)

    def run(self, video_path: Path, on_progress=None, max_segment_duration: float | None = None) -> ResultsOutput:
        """Run the pipeline on a video file.

        Args:
            video_path: Path to the video file.
            on_progress: Optional callable(step: str, detail: str) for progress updates.
                Steps: "loading", "classifying", "aggregating", "detecting", "writing", "done"

        Returns:
            ResultsOutput containing all detected segments and their metadata.
        """
        def progress(step: str, detail: str = "") -> None:
            print(f"[{step}] {detail}" if detail else f"[{step}]")
            if on_progress:
                on_progress(step, detail)

        progress("loading", f"Loading audio from: {video_path}")
        audio, sample_rate = self.loader.load(video_path)
        progress("loading", f"Duration: {len(audio) / sample_rate:.1f}s")

        if on_progress:
            n_pts = min(1000, len(audio))
            block = max(1, len(audio) // n_pts)
            trimmed = (len(audio) // block) * block
            peaks = np.abs(audio[:trimmed].reshape(-1, block)).max(axis=1)
            max_p = float(peaks.max())
            norm = [round(float(x / max_p), 3) for x in peaks] if max_p > 0 else [0.0]*len(peaks)
            on_progress("waveform", json.dumps({"samples": norm, "duration": round(float(len(audio)/sample_rate), 2)}))

        progress("classifying", "Starting chunk classification...")
        classification_results = []
        for chunk in self.chunker.chunk(audio, sample_rate):
            result = self.classifier.classify(chunk)
            status = "MUSIC" if result.is_music else "-----"
            detail = f"{chunk.start_time:5.1f}s - {chunk.end_time:5.1f}s: {status} ({result.confidence * 100:.1f}%)"
            print(f"  {detail}")
            progress("classifying", detail)
            classification_results.append(result)

        progress("aggregating", "Aggregating music segments...")
        segments = self.aggregator.aggregate(classification_results, audio, sample_rate)
        progress("aggregating", f"Found {len(segments)} music segment(s)")

        progress("detecting", "Isolating and detecting music...")
        detection_results: list[tuple[MusicSegment, DetectionResult]] = []
        for i, segment in enumerate(segments):
            seg_detail = f"Segment {i + 1}/{len(segments)}: {segment.start_time:.1f}s - {segment.end_time:.1f}s"
            print(f"  {seg_detail}")
            progress("detecting", seg_detail)

            # Isolate music (remove vocals if using DemucsIsolator)
            seg_audio = self.isolator.isolate(segment.audio, segment.sample_rate)

            # Split into chunks for API detection (or treat whole segment as one chunk)
            if max_segment_duration:
                max_samples = int(max_segment_duration * segment.sample_rate)
                chunk_audios = [
                    seg_audio[s:s + max_samples]
                    for s in range(0, len(seg_audio), max_samples)
                ]
            else:
                chunk_audios = [seg_audio]

            for ci, chunk_audio in enumerate(chunk_audios):
                chunk_start = segment.start_time + ci * (max_segment_duration or 0)
                chunk_end = chunk_start + len(chunk_audio) / segment.sample_rate
                chunk_seg = MusicSegment(
                    audio=chunk_audio,
                    start_time=chunk_start,
                    end_time=chunk_end,
                    sample_rate=segment.sample_rate,
                )
                detection = self.detection_client.detect(chunk_seg)
                if detection.title:
                    det_detail = f"Detected: {detection.title} - {detection.artist}"
                    print(f"    {det_detail}")
                    progress("detecting", det_detail)
                else:
                    progress("detecting", "No match found")
                detection_results.append((chunk_seg, detection))

        found = sum(1 for _, d in detection_results if d.title)
        progress("detecting", f"{found}/{len(segments)} identified")

        progress("writing")
        return self.output_writer.write(video_path, detection_results)


if __name__ == "__main__":
    import argparse

    from fiwi_filmmusik.classifiers import HuggingFaceClassifier
    from fiwi_filmmusik.detection import ShazamDetectionClient
    from fiwi_filmmusik.isolators import DemucsIsolator

    parser = argparse.ArgumentParser(
        description="Run the film music detection pipeline on a video file."
    )
    parser.add_argument("video_path", type=Path, help="Path to the video file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./output"),
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--no-isolation",
        action="store_true",
        help="Skip vocal isolation (use DummyIsolator)",
    )
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=10.0,
        help="Chunk duration in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.2,
        help="Music classification threshold (default: 0.2)",
    )

    args = parser.parse_args()

    # Create components
    loader = VideoLoader()
    chunker = AudioChunker(chunk_duration=args.chunk_duration, overlap=2.0)
    classifier = HuggingFaceClassifier(music_labels=["Music"], threshold=args.threshold)
    aggregator = ChunkAggregator(gap_tolerance=1.0)

    if args.no_isolation:
        isolator: BaseMusicIsolator = DummyIsolator()
    else:
        isolator = DemucsIsolator()

    detection_client = ShazamDetectionClient()

    # Create and run pipeline
    pipeline = Pipeline(
        loader=loader,
        chunker=chunker,
        classifier=classifier,
        aggregator=aggregator,
        isolator=isolator,
        detection_client=detection_client,
        output_dir=args.output_dir,
    )

    results = pipeline.run(args.video_path)

    print(f"\nPipeline complete!")
    print(f"  Segments: {len(results.segments)}")
    print(f"  Identifications: {len(results.identifications)}")
    print(f"  Output: {args.output_dir / args.video_path.stem}")
