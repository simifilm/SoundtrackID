"""Pipeline orchestrator."""

import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from fiwi_filmmusik.aggregator import ChunkAggregator
from fiwi_filmmusik.chunker import AudioChunker
from fiwi_filmmusik.classifiers import BaseClassifier
from fiwi_filmmusik.detection import BaseMusicDetectionClient, RateLimitError
from fiwi_filmmusik.isolators import BaseMusicIsolator, DummyIsolator
from fiwi_filmmusik.loaders import VideoLoader
from fiwi_filmmusik.models import (
    DetectionResult,
    IdentificationOutput,
    MusicSegment,
    ResultsOutput,
    SegmentOutput,
)


def _det_key(det: DetectionResult) -> tuple | None:
    # Album is deliberately excluded: the same piece often comes back tagged to
    # different albums across windows, and we still want to merge those adjacent
    # windows into one cue (a different *title* is what breaks a run).
    return (det.title, det.artist) if det.title else None


def _group_detections(
    results: list[tuple[MusicSegment, DetectionResult]],
    gap_tolerance_s: float = 30.0,
) -> list[list[tuple[MusicSegment, DetectionResult]]]:
    """Group detection results into cue-level blocks.

    Pass 1: consecutive same-key runs (None==None so unidentified also merge).
    Pass 2: bridge unidentified gaps <= gap_tolerance_s between identical tracks.
    """
    # Pass 1: consecutive same-key grouping
    groups: list[list[tuple[MusicSegment, DetectionResult]]] = []
    for item in results:
        key = _det_key(item[1])
        if groups and key == _det_key(groups[-1][-1][1]):
            groups[-1].append(item)
        else:
            groups.append([item])

    # Pass 2: bridge short unidentified gaps between the same identified track
    i = 1
    while i < len(groups) - 1:
        prev, curr, nxt = groups[i - 1], groups[i], groups[i + 1]
        gap_key = _det_key(curr[0][1])
        prev_key = _det_key(prev[0][1])
        if (
            gap_key is None
            and prev_key is not None
            and prev_key == _det_key(nxt[0][1])
            and sum(seg.end_time - seg.start_time for seg, _ in curr) <= gap_tolerance_s
        ):
            groups[i - 1] = prev + curr + nxt
            groups.pop(i + 1)
            groups.pop(i)
        else:
            i += 1

    return groups


class OutputWriter:
    """Handles writing detection results to disk."""

    def __init__(
        self,
        output_dir: Path,
        conditioning: str = "original",
        export_csv: bool = False,
        export_mava: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.conditioning = conditioning
        self.export_csv = export_csv
        self.export_mava = export_mava

    def write(
        self,
        video_path: Path,
        results: list[tuple[MusicSegment, DetectionResult]],
        waveform: dict | None = None,
        film: dict | None = None,
    ) -> ResultsOutput:
        """Write detection results to disk.

        Consecutive detections with the same identification are concatenated
        into a single WAV file. Creates:
        - output_dir/<video_name>/results.json
        - output_dir/<video_name>/segments/seg_001.wav
        """
        video_name = video_path.stem
        video_output_dir = self.output_dir / video_name
        segments_dir = video_output_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        groups = _group_detections(results)

        segment_outputs: list[SegmentOutput] = []
        identification_outputs: list[IdentificationOutput] = []

        for i, group in enumerate(groups):
            segment_id = f"seg_{i + 1:03d}"
            audio_filename = f"{segment_id}.wav"
            audio_path = segments_dir / audio_filename

            combined_audio = np.concatenate([seg.audio for seg, _ in group])
            sample_rate = group[0][0].sample_rate
            audio_int16 = (np.clip(combined_audio, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(audio_path, sample_rate, audio_int16)

            first_seg, last_seg = group[0][0], group[-1][0]
            identified = [d for _, d in group if d.title]
            confidence = sum(d.confidence for d in identified) / len(identified) if identified else 0.5

            segment_outputs.append(SegmentOutput(
                id=segment_id,
                start=first_seg.start_time,
                duration=last_seg.end_time - first_seg.start_time,
                conditioning=self.conditioning,
                audio_file=f"segments/{audio_filename}",
                confidence=confidence,
            ))

            first_detection = next((d for _, d in group if d.title), None)
            if first_detection:
                # Canonical album for the merged cue: the most frequent album name
                # among the group's identified windows (ties → first seen), so a
                # single piece doesn't inherit whichever album the first window
                # happened to carry.
                album_counts: dict[str, int] = {}
                for d in identified:
                    alb = d.metadata.get("album")
                    if alb:
                        album_counts[alb] = album_counts.get(alb, 0) + 1
                album = max(album_counts, key=album_counts.get) if album_counts else None
                identification_outputs.append(IdentificationOutput(
                    segment_id=segment_id,
                    provider=first_detection.provider,
                    title=first_detection.title,
                    artist=first_detection.artist,
                    album=album,
                    confidence=first_detection.confidence,
                    metadata={k: v for k, v in first_detection.metadata.items() if k != "album" and v is not None},
                ))

        results_output = ResultsOutput(
            source=str(video_path),
            segments=segment_outputs,
            identifications=identification_outputs,
            waveform=waveform,
            film=film,
        )

        results_path = video_output_dir / "results.json"
        results_path.write_text(results_output.to_json())
        print(f"  Results written to: {results_path}")

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
                        seg.id, seg.start, seg.duration, seg.conditioning, seg.confidence,
                        ident.provider if ident else "",
                        ident.title if ident else "",
                        ident.artist if ident else "",
                        ident.album if ident else "",
                    ])
            print(f"  CSV written to: {csv_path}")

        if self.export_mava:
            from fiwi_filmmusik.mava_export import write_mava_export
            tsv_path, mapping_path = write_mava_export(
                results_output.to_dict()["cues"], video_output_dir, video_name=video_name
            )
            print(f"  MAVA export written to: {tsv_path.name} + {mapping_path.name}")

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
        export_mava: bool = False,
        detection_concurrency: int = 4,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.classifier = classifier
        self.aggregator = aggregator
        self.isolator = isolator
        self.detection_client = detection_client
        self.output_dir = output_dir or Path("./output")
        # Number of identification calls dispatched concurrently. Kept modest by
        # default: identification providers are network-bound and rate-limited
        # (ACRCloud is metered).
        self.detection_concurrency = max(1, detection_concurrency)

        # Determine conditioning based on isolator type
        conditioning = "original" if isinstance(isolator, DummyIsolator) else "vocals_removed"
        self.output_writer = OutputWriter(
            self.output_dir, conditioning=conditioning, export_csv=export_csv, export_mava=export_mava
        )

    def run(self, video_path: Path, on_progress=None, max_segment_duration: float | None = None, container_tags: dict | None = None) -> ResultsOutput:
        """Run the pipeline on a video file.

        Args:
            video_path: Path to the video file.
            on_progress: Optional callable(step: str, detail: str) for progress updates.
                Steps: "loading", "waveform", "classifying", "identified", "detecting",
                "writing" (identification overlaps classification, so "classifying" and
                "identified" events interleave). "done" is emitted by the caller.

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

        WAVEFORM_HZ = 5
        WAVEFORM_MAX_PTS = 60_000
        duration_s = len(audio) / sample_rate
        n_pts = min(WAVEFORM_MAX_PTS, max(1, int(round(duration_s * WAVEFORM_HZ))))
        n_pts = min(n_pts, len(audio))
        block = max(1, len(audio) // n_pts)
        trimmed = (len(audio) // block) * block
        peaks = np.abs(audio[:trimmed].reshape(-1, block)).max(axis=1)
        max_p = float(peaks.max())
        waveform_data = {
            "samples": [round(float(x / max_p), 3) for x in peaks] if max_p > 0 else [0.0] * len(peaks),
            "duration": round(float(len(audio) / sample_rate), 2),
        }
        if on_progress:
            on_progress("waveform", json.dumps(waveform_data))

        progress("classifying", "Starting chunk classification...")

        # Detection runs concurrently with (and overlaps) classification: as the
        # incremental aggregator finalizes each music segment, its identification
        # chunks are submitted to a thread pool. Only the blocking detect() calls
        # run in workers; all progress is emitted from this driver thread.
        detection_results_by_idx: dict[int, tuple[MusicSegment, DetectionResult]] = {}
        pending: dict[Future, tuple[int, MusicSegment]] = {}
        total_chunks = 0
        reaped = 0
        classification_done = False

        executor = ThreadPoolExecutor(max_workers=self.detection_concurrency)

        # Debug-only: FIWI_DEBUG_DETECT_SLEEP=<seconds> makes each identification
        # call sleep, to exercise the frontend's stall watchdog. No effect unset.
        debug_sleep = float(os.environ.get("FIWI_DEBUG_DETECT_SLEEP") or 0)

        rate_limited = False

        def detect_one(seg: MusicSegment) -> DetectionResult:
            if debug_sleep:
                time.sleep(debug_sleep)
            return self.detection_client.detect(seg)

        def empty_result(seg: MusicSegment) -> DetectionResult:
            return DetectionResult(segment=seg, title=None, artist=None, confidence=0.0)

        def reap(fut, chunk_seg: MusicSegment) -> DetectionResult:
            """Return a detection, converting a provider rate-limit into a
            no-match (and flagging the run) instead of aborting everything."""
            nonlocal rate_limited
            try:
                return fut.result()
            except RateLimitError:
                rate_limited = True
                return empty_result(chunk_seg)

        def emit_reaped(idx: int, chunk_seg: MusicSegment, detection: DetectionResult, *, label: bool) -> None:
            # Always emit an "identified" progress event (title=null means the
            # window was processed but not matched). The frontend handles it
            # without touching the step indicator, so it advances the
            # identification progress bar per window and can detect a stalled
            # provider — including while classification is still running.
            # "detecting" step labels are emitted only once classification has
            # finished (label=True), to avoid thrashing the step transition.
            nonlocal reaped
            reaped += 1
            detection_results_by_idx[idx] = (chunk_seg, detection)
            progress("identified", json.dumps({
                "start": round(chunk_seg.start_time, 3),
                "end": round(chunk_seg.end_time, 3),
                "title": detection.title,
                "artist": detection.artist,
                "reaped": reaped,
                "total": total_chunks if classification_done else None,
            }))
            if label:
                if detection.title:
                    det_detail = f"[{reaped}/{total_chunks}] {detection.title} – {detection.artist}"
                else:
                    det_detail = f"[{reaped}/{total_chunks}] {chunk_seg.start_time:.0f}s–{chunk_seg.end_time:.0f}s: no match"
                print(f"  {det_detail}")
                progress("detecting", det_detail)

        def drain_finished() -> None:
            # Reap already-completed futures without blocking, so identification
            # keeps overlapping the still-running classification loop.
            for fut in [f for f in pending if f.done()]:
                idx, chunk_seg = pending.pop(fut)
                emit_reaped(idx, chunk_seg, reap(fut, chunk_seg), label=False)

        def classified_stream():
            for chunk in self.chunker.chunk(audio, sample_rate):
                result = self.classifier.classify(chunk)
                status = "MUSIC" if result.is_music else "-----"
                detail = f"{chunk.start_time:5.1f}s - {chunk.end_time:5.1f}s: {status}"
                print(f"  {detail}")
                progress("classifying", detail)
                yield result

        try:
            # Windows are emitted as soon as each is fully classified (even mid
            # cue), so identification of early windows overlaps classification of
            # later audio. Isolation stays on this driver thread (a no-op for the
            # default DummyIsolator; the optional GPU DemucsIsolator is per-window).
            for window in self.aggregator.stream_windows(
                classified_stream(), audio, sample_rate, window_duration=max_segment_duration
            ):
                iso_audio = self.isolator.isolate(window.audio, window.sample_rate)
                chunk_seg = MusicSegment(
                    audio=iso_audio,
                    start_time=window.start_time,
                    end_time=window.end_time,
                    sample_rate=window.sample_rate,
                )
                idx = total_chunks
                total_chunks += 1
                if rate_limited:
                    # Provider is throttled; don't burn more calls. Record the
                    # remaining windows as unidentified so they can be resumed.
                    emit_reaped(idx, chunk_seg, empty_result(chunk_seg), label=False)
                else:
                    pending[executor.submit(detect_one, chunk_seg)] = (idx, chunk_seg)
                drain_finished()

            # Classification is complete: the total chunk count is now known, so
            # switch to the "detecting" step and reap the remaining futures.
            classification_done = True
            progress("detecting", f"Identifying {total_chunks} chunk(s)...")
            # Reap with a timeout so we can emit a "waiting" heartbeat while blocked
            # on the identification provider (e.g. Shazam rate-limiting delays the
            # response) — otherwise the UI would sit silent with no feedback.
            waited = 0.0
            remaining = set(pending)
            while remaining:
                done_set, remaining = wait(remaining, timeout=5.0, return_when=FIRST_COMPLETED)
                if not done_set:
                    waited += 5.0
                    progress("ident_waiting", json.dumps({
                        "reaped": reaped,
                        "total": total_chunks,
                        "seconds": int(waited),
                    }))
                    continue
                waited = 0.0
                for fut in done_set:
                    idx, chunk_seg = pending.pop(fut)
                    emit_reaped(idx, chunk_seg, reap(fut, chunk_seg), label=True)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)

        detection_results: list[tuple[MusicSegment, DetectionResult]] = [
            detection_results_by_idx[i] for i in range(total_chunks)
        ]
        found = sum(1 for _, d in detection_results if d.title)
        progress("detecting", f"{found}/{total_chunks} chunks identified")

        progress("writing")
        output = self.output_writer.write(video_path, detection_results, waveform=waveform_data, film=container_tags or None)
        output.rate_limited = rate_limited
        return output


if __name__ == "__main__":
    import argparse

    from fiwi_filmmusik.detection import build_detection_client

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

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
        "--api",
        default="acrcloud",
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
             "list for ensemble majority voting (default: acrcloud)",
    )
    parser.add_argument(
        "--classifier",
        choices=("onnx", "hf"),
        default="onnx",
        help="Music classifier backend (default: onnx)",
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
        "--max-segment",
        type=float,
        default=None,
        help="Split segments into chunks of at most N seconds before detection",
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

    if args.classifier == "hf":
        from fiwi_filmmusik.classifiers import HuggingFaceClassifier
        classifier: BaseClassifier = HuggingFaceClassifier(music_labels=["Music"], threshold=args.threshold)
    else:
        from fiwi_filmmusik.classifiers import OnnxClassifier
        onnx_model_dir = Path(__file__).parents[2] / "assets" / "ast_model"
        classifier = OnnxClassifier(
            model_dir=str(onnx_model_dir), music_labels=["Music"], threshold=args.threshold
        )

    aggregator = ChunkAggregator(gap_tolerance=1.0)

    if args.no_isolation:
        isolator: BaseMusicIsolator = DummyIsolator()
    else:
        from fiwi_filmmusik.isolators import DemucsIsolator
        isolator = DemucsIsolator()

    detection_client = build_detection_client(args.api)

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

    results = pipeline.run(args.video_path, max_segment_duration=args.max_segment)

    print(f"\nPipeline complete!")
    print(f"  Segments: {len(results.segments)}")
    print(f"  Identifications: {len(results.identifications)}")
    print(f"  Output: {args.output_dir / args.video_path.stem}")
