"""Pipeline orchestrator."""

import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.classifiers import BaseClassifier
from soundtrackID.detection import BaseMusicDetectionClient, RateLimitError
from soundtrackID.isolators import BaseMusicIsolator, DummyIsolator
from soundtrackID.loaders import VideoLoader
from soundtrackID.models import (
    ClassificationResult,
    DetectionResult,
    IdentificationOutput,
    MusicSegment,
    ResultsOutput,
    SegmentOutput,
)
from soundtrackID.slots import merge_spans, stream_slots, top_classes

log = logging.getLogger(__name__)

# Seconds between the starts of overlapping classification windows.
CLASSIFICATION_HOP_S = 5.0


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

    Pass 1: consecutive same-key runs (None==None so unidentified also merge),
    as long as a clip starts <= gap_tolerance_s after the previous one ended.
    Clips the provider didn't answer (a rate limit or an error) stay alone, so
    a resume identifies each one.
    Pass 2: bridge unidentified groups between identical tracks when the later
    track starts <= gap_tolerance_s after the earlier one ended.
    """
    # Pass 1: consecutive same-key grouping, not across longer non-music gaps
    groups: list[list[tuple[MusicSegment, DetectionResult]]] = []
    for item in results:
        key = _det_key(item[1])
        if (
            groups
            and key == _det_key(groups[-1][-1][1])
            and item[0].start_time - groups[-1][-1][0].end_time <= gap_tolerance_s
            and not item[1].skipped
            and not groups[-1][-1][1].skipped
        ):
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
            and nxt[0][0].start_time - prev[-1][0].end_time <= gap_tolerance_s
        ):
            groups[i - 1] = prev + curr + nxt
            groups.pop(i + 1)
            groups.pop(i)
        else:
            i += 1

    return groups


class OutputWriter:
    """Turns detections into cues and writes the audio of the pending ones."""

    def __init__(
        self,
        output_dir: Path,
        bridge_gap: float = 2.0,
    ) -> None:
        self.output_dir = output_dir
        self.bridge_gap = bridge_gap

    def write(
        self,
        video_path: Path,
        results: list[tuple[MusicSegment, DetectionResult]],
        waveform: dict | None = None,
        film: dict | None = None,
        classification: list[dict] | None = None,
        classification_windows: list[dict] | None = None,
        run_name: str | None = None,
    ) -> ResultsOutput:
        """Merge consecutive detections with the same identification into cues.

        Writes output_dir/<video_name>/segments/seg_001.wav, the audio of each
        pending cue, which a resume sends again; video_name is `run_name`, by
        default the video's file name. results.json is the caller's to save
        (analysis.analyze), once the run is complete.
        """
        video_name = run_name or video_path.stem
        video_output_dir = self.output_dir / video_name
        segments_dir = video_output_dir / "segments"

        groups = _group_detections(results, gap_tolerance_s=self.bridge_gap)

        segment_outputs: list[SegmentOutput] = []
        identification_outputs: list[IdentificationOutput] = []

        for i, group in enumerate(groups):
            segment_id = f"seg_{i + 1:03d}"
            pending = all(d.skipped for _, d in group)
            audio_file = None
            if pending:
                # A pending cue is a single clip: skipped clips are never joined.
                seg = group[0][0]
                audio_file = f"segments/{segment_id}.wav"
                segments_dir.mkdir(parents=True, exist_ok=True)
                audio_int16 = (np.clip(seg.audio, -1.0, 1.0) * 32767).astype(np.int16)
                wavfile.write(
                    video_output_dir / audio_file, seg.sample_rate, audio_int16
                )

            first_seg, last_seg = group[0][0], group[-1][0]
            identified = [d for _, d in group if d.title]
            confidence = (
                sum(d.confidence for d in identified) / len(identified)
                if identified
                else None
            )

            segment_outputs.append(
                SegmentOutput(
                    id=segment_id,
                    start=first_seg.start_time,
                    duration=last_seg.end_time - first_seg.start_time,
                    audio_file=audio_file,
                    confidence=confidence,
                    pending=pending,
                )
            )

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
                album = (
                    max(album_counts, key=album_counts.get) if album_counts else None
                )
                identification_outputs.append(
                    IdentificationOutput(
                        segment_id=segment_id,
                        provider=first_detection.provider,
                        title=first_detection.title,
                        artist=first_detection.artist,
                        album=album,
                        confidence=first_detection.confidence,
                        metadata={
                            k: v
                            for k, v in first_detection.metadata.items()
                            if k != "album" and v is not None
                        },
                    )
                )

        results_output = ResultsOutput(
            source=video_path.name,
            video_name=video_name,
            segments=segment_outputs,
            identifications=identification_outputs,
            waveform=waveform,
            film=film,
            classification=classification,
            classification_windows=classification_windows,
        )

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
        detection_concurrency: int = 4,
        bridge_gap: float = 2.0,
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

        self.output_writer = OutputWriter(self.output_dir, bridge_gap=bridge_gap)

    @property
    def settings(self) -> dict:
        """What this pipeline analyzes with, as saved with a run's results."""
        return {
            "classifier": type(self.classifier).__name__,
            "threshold": self.classifier.threshold,
            "window": self.chunker.chunk_duration,
            "hop": self.chunker.chunk_duration - self.chunker.overlap,
            "bridge_gap": self.output_writer.bridge_gap,
            "isolation": not isinstance(self.isolator, DummyIsolator),
        }

    def run(
        self,
        video_path: Path,
        on_progress=None,
        max_segment_duration: float | None = None,
        container_tags: dict | None = None,
        run_name: str | None = None,
    ) -> ResultsOutput:
        """Run the pipeline on a video file.

        Args:
            video_path: Path to the video file.
            run_name: Name of the run's output folder; by default the video's
                file name.
            on_progress: Optional callable(step: str, **fields) for progress updates.
                Steps: "loading", "waveform", "classifying", "identified", "detecting",
                "writing" (identification overlaps classification, so "classifying" and
                "identified" events interleave). "done" is emitted by the caller.
                Every step except "waveform" passes `detail`, a message for people.
                Data comes as fields, never inside `detail`:
                  waveform:        samples, duration
                  classifying:     start, end, is_music (once per window)
                  identified:      start, end, title, artist, reaped, total
                  ident_waiting:   reaped, total, seconds
                  detecting:       total, title, artist (per window, once
                                   classification is done)

        Returns:
            ResultsOutput containing all detected segments and their metadata.
        """

        def progress(step: str, detail: str = "", **fields) -> None:
            text = detail or (json.dumps(fields) if fields else "")
            log.info("[%s] %s", step, text)
            if on_progress:
                on_progress(step, detail=detail, **fields)

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
            "samples": [round(float(x / max_p), 3) for x in peaks]
            if max_p > 0
            else [0.0] * len(peaks),
            "duration": round(float(len(audio) / sample_rate), 2),
        }
        if on_progress:
            on_progress("waveform", **waveform_data)

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
            # Once the provider throttles, windows already queued skip their call
            # too (they stay unidentified for a resume); calls under way finish.
            nonlocal rate_limited
            if rate_limited:
                return skipped_result(seg)
            if debug_sleep:
                time.sleep(debug_sleep)
            try:
                return self.detection_client.detect(seg)
            except RateLimitError:
                rate_limited = True
                raise

        def skipped_result(seg: MusicSegment) -> DetectionResult:
            return DetectionResult(
                segment=seg, title=None, artist=None, confidence=0.0, skipped=True
            )

        def reap(fut, chunk_seg: MusicSegment) -> DetectionResult:
            """Return a detection, converting a provider rate-limit (which also
            flags the run) or error into a skipped window instead of aborting
            everything: a resume asks for it again."""
            nonlocal rate_limited
            try:
                return fut.result()
            except RateLimitError:
                rate_limited = True
                return skipped_result(chunk_seg)
            except Exception:
                log.exception(
                    "Identifying %.1f-%.1f s failed",
                    chunk_seg.start_time,
                    chunk_seg.end_time,
                )
                return skipped_result(chunk_seg)

        def emit_reaped(
            idx: int,
            chunk_seg: MusicSegment,
            detection: DetectionResult,
            *,
            label: bool,
        ) -> None:
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
            progress(
                "identified",
                start=round(chunk_seg.start_time, 3),
                end=round(chunk_seg.end_time, 3),
                title=detection.title,
                artist=detection.artist,
                reaped=reaped,
                total=total_chunks if classification_done else None,
            )
            if label:
                prefix = f"[{reaped}/{total_chunks}]"
                if detection.title:
                    det_detail = f"{prefix} {detection.title} - {detection.artist}"
                else:
                    start, end = chunk_seg.start_time, chunk_seg.end_time
                    det_detail = f"{prefix} {start:.0f}s-{end:.0f}s: no match"
                log.info("  %s", det_detail)
                progress(
                    "detecting",
                    det_detail,
                    total=total_chunks,
                    title=detection.title,
                    artist=detection.artist,
                )

        def drain_finished() -> None:
            # Reap already-completed futures without blocking, so identification
            # keeps overlapping the still-running classification loop.
            for fut in [f for f in pending if f.done()]:
                idx, chunk_seg = pending.pop(fut)
                emit_reaped(idx, chunk_seg, reap(fut, chunk_seg), label=False)

        # Every window's top classes, so a wrong music verdict can be traced.
        windows: list[dict] = []

        def classified_stream():
            for chunk in self.chunker.chunk(audio, sample_rate):
                result = self.classifier.classify(chunk)
                windows.append(
                    {
                        "start": chunk.start_time,
                        "end": chunk.end_time,
                        "music": round(result.confidence, 4),
                        "is_music": result.is_music,
                        "classes": top_classes(result.probs, self.classifier.labels, 3),
                    }
                )
                status = "MUSIC" if result.is_music else "-----"
                detail = f"{chunk.start_time:5.1f}s - {chunk.end_time:5.1f}s: {status}"
                log.info("  %s", detail)
                progress(
                    "classifying",
                    detail,
                    start=chunk.start_time,
                    end=chunk.end_time,
                    is_music=result.is_music,
                )
                yield result

        # The aggregator gets gap-free hop-sized slots instead of the overlapping
        # windows; the slots are also kept for the classification spans.
        slots: list[ClassificationResult] = []

        def slot_stream():
            hop = self.chunker.chunk_duration - self.chunker.overlap
            for slot in stream_slots(classified_stream(), audio, sample_rate, hop):
                slots.append(slot)
                yield slot

        try:
            # Windows are emitted as soon as each is fully classified (even mid
            # cue), so identification of early windows overlaps classification of
            # later audio. Isolation stays on this driver thread (a no-op for the
            # default DummyIsolator; the optional GPU DemucsIsolator is per-window).
            for window in self.aggregator.stream_windows(
                slot_stream(), audio, sample_rate, window_duration=max_segment_duration
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
                    # remaining windows as skipped so they can be resumed.
                    emit_reaped(idx, chunk_seg, skipped_result(chunk_seg), label=False)
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
                done_set, remaining = wait(
                    remaining, timeout=5.0, return_when=FIRST_COMPLETED
                )
                if not done_set:
                    waited += 5.0
                    progress(
                        "ident_waiting",
                        reaped=reaped,
                        total=total_chunks,
                        seconds=int(waited),
                    )
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
        output = self.output_writer.write(
            video_path,
            detection_results,
            waveform=waveform_data,
            film=container_tags or None,
            classification=merge_spans(slots, self.classifier.labels),
            classification_windows=windows,
            run_name=run_name,
        )
        return output


def build_pipeline(
    classifier: BaseClassifier,
    api: str,
    output_dir: Path,
    hop: float = CLASSIFICATION_HOP_S,
    window: float | None = None,
    bridge_gap: float = 2.0,
    isolation: bool = False,
) -> Pipeline:
    """The pipeline the app and the CLI analyze with.

    `api` names the detection provider(s), comma-separated to let them vote.
    `window` is the length of a classified window, by default the classifier's
    own (no padding); `isolation` removes the vocals before identification
    (Demucs).
    """
    # Imported here, so tests can replace the provider.
    from soundtrackID.detection import build_detection_client

    window = window or classifier.window_duration
    if isolation:
        from soundtrackID.isolators import DemucsIsolator

        isolator: BaseMusicIsolator = DemucsIsolator()
    else:
        isolator = DummyIsolator()
    return Pipeline(
        loader=VideoLoader(),
        chunker=AudioChunker(chunk_duration=window, overlap=window - hop),
        classifier=classifier,
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=isolator,
        detection_client=build_detection_client(api),
        output_dir=output_dir,
        bridge_gap=bridge_gap,
    )
