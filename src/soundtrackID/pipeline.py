"""Pipeline orchestrator."""

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.classifiers import BaseClassifier
from soundtrackID.defaults import BRIDGE_GAP_S, CLASSIFICATION_HOP_S, MUSIC_THRESHOLD
from soundtrackID.detection import (
    BaseMusicDetectionClient,
    ProviderAuthError,
    RateLimitError,
)
from soundtrackID.isolators import BaseMusicIsolator, DummyIsolator
from soundtrackID.loaders import VideoLoader
from soundtrackID.models import (
    ClassificationResult,
    Cue,
    DetectionResult,
    MusicSegment,
    ResultsOutput,
    match_fields,
)
from soundtrackID.slots import merge_spans, stream_slots, top_classes

log = logging.getLogger(__name__)

# The run's progress callback: progress(step, detail="", **fields).
Progress = Callable[..., None]


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


def build_cues(
    results: list[tuple[MusicSegment, DetectionResult]], bridge_gap: float
) -> tuple[list[Cue], dict[str, MusicSegment]]:
    """Merge consecutive detections with the same identification into cues.

    Returns the cues and the audio of each pending one, by its segment_id, which
    the run keeps for a resume to send again (runs.Run.write_audio); a pending
    cue's audio_file is the run's to set.
    """
    groups = _group_detections(results, gap_tolerance_s=bridge_gap)

    cues: list[Cue] = []
    pending_audio: dict[str, MusicSegment] = {}
    for i, group in enumerate(groups):
        segment_id = f"seg_{i + 1:03d}"
        pending = all(d.skipped for _, d in group)
        if pending:
            # A pending cue is a single clip: skipped clips are never joined.
            pending_audio[segment_id] = group[0][0]

        first_seg, last_seg = group[0][0], group[-1][0]
        identified = [d for _, d in group if d.title]
        cue: Cue = {
            "segment_id": segment_id,
            "start": first_seg.start_time,
            "duration": last_seg.end_time - first_seg.start_time,
            "audio_file": None,
            "pending": pending,
            **match_fields(identified[0] if identified else None),
            "enrichment": None,
        }
        if identified:
            # Canonical album for the merged cue: the most frequent album name
            # among the group's identified windows (ties → first seen), so a
            # single piece doesn't inherit whichever album the first window
            # happened to carry.
            album_counts: dict[str, int] = {}
            for d in identified:
                alb = d.metadata.get("album")
                if alb:
                    album_counts[alb] = album_counts.get(alb, 0) + 1
            cue["album"] = (
                max(album_counts, key=album_counts.get) if album_counts else None
            )
        cues.append(cue)

    return cues, pending_audio


# The waveform the app draws: 5 points a second, at most 60,000 (3 h 20 min).
WAVEFORM_HZ = 5
WAVEFORM_MAX_PTS = 60_000


def waveform_peaks(audio: np.ndarray, sample_rate: int) -> dict:
    """The audio's waveform for the app: the loudest sample of each stretch,
    scaled so that the loudest of all is 1, and the duration in seconds."""
    duration_s = len(audio) / sample_rate
    n_pts = min(WAVEFORM_MAX_PTS, max(1, int(round(duration_s * WAVEFORM_HZ))))
    n_pts = min(n_pts, len(audio))
    block = max(1, len(audio) // n_pts)
    trimmed = (len(audio) // block) * block
    peaks = np.abs(audio[:trimmed].reshape(-1, block)).max(axis=1)
    max_p = float(peaks.max())
    return {
        "samples": [round(float(x / max_p), 3) for x in peaks]
        if max_p > 0
        else [0.0] * len(peaks),
        "duration": round(float(len(audio) / sample_rate), 2),
    }


class _Classification:
    """A run's audio, classified window by window as the aggregator asks for it.

    A window is music if the classifier scores it above the run's threshold.
    Keeps what the results show of it: every window's top classes, so a wrong
    music verdict can be traced, and the gap-free hop-sized slots the aggregator
    gets instead of the overlapping windows, which make the classification's
    spans.
    """

    def __init__(
        self,
        chunker: AudioChunker,
        classifier: BaseClassifier,
        threshold: float,
        progress: Progress,
    ) -> None:
        self._chunker = chunker
        self._classifier = classifier
        self._threshold = threshold
        self._progress = progress
        self.windows: list[dict] = []
        self.slots: list[ClassificationResult] = []

    def slots_of(
        self, audio: np.ndarray, sample_rate: int
    ) -> Iterator[ClassificationResult]:
        hop = self._chunker.chunk_duration - self._chunker.overlap
        for slot in stream_slots(
            self._windows_of(audio, sample_rate), audio, sample_rate, hop
        ):
            self.slots.append(slot)
            yield slot

    def _windows_of(
        self, audio: np.ndarray, sample_rate: int
    ) -> Iterator[ClassificationResult]:
        for chunk in self._chunker.chunk(audio, sample_rate):
            music, probs = self._classifier.classify(chunk)
            result = ClassificationResult(
                chunk=chunk,
                is_music=music > self._threshold,
                confidence=music,
                probs=probs,
            )
            self.windows.append(
                {
                    "start": chunk.start_time,
                    "end": chunk.end_time,
                    "music": round(music, 4),
                    "is_music": result.is_music,
                    "classes": top_classes(probs, self._classifier.labels, 3),
                }
            )
            status = "MUSIC" if result.is_music else "-----"
            detail = f"{chunk.start_time:5.1f}s - {chunk.end_time:5.1f}s: {status}"
            log.info("  %s", detail)
            self._progress(
                "classifying",
                detail,
                start=chunk.start_time,
                end=chunk.end_time,
                is_music=result.is_music,
            )
            yield result


class _Identification:
    """Identifies a run's music windows while the run is still being classified.

    Each window goes to a thread pool once the aggregator has it, and the
    answers are taken as they come in, so identifying early windows overlaps
    classifying later audio. Only the provider calls run in the pool; progress
    is reported from the calling thread.

    A window the provider didn't answer is skipped, which the run keeps pending
    for a resume: at a rate limit, which also skips the windows not asked yet
    rather than burn more calls, or on an error. Rejected keys stop the run.
    """

    def __init__(
        self,
        client: BaseMusicDetectionClient,
        concurrency: int,
        progress: Progress,
    ) -> None:
        self._client = client
        self._progress = progress
        self._executor = ThreadPoolExecutor(max_workers=concurrency)
        self._asked: dict[Future, tuple[int, MusicSegment]] = {}
        self._answers: dict[int, tuple[MusicSegment, DetectionResult]] = {}
        self._rate_limited = threading.Event()  # set by any thread, read by all
        self._total = 0  # windows submitted
        self._reaped = 0  # answers taken
        self._all_submitted = False
        # Debug-only: FIWI_DEBUG_DETECT_SLEEP=<seconds> makes each identification
        # call sleep, to exercise the frontend's stall watchdog. No effect unset.
        self._debug_sleep = float(os.environ.get("FIWI_DEBUG_DETECT_SLEEP") or 0)

    def submit(self, window: MusicSegment) -> None:
        """Ask about `window`, and take the answers already in, without waiting,
        so identification keeps overlapping the classification."""
        idx = self._total
        self._total += 1
        if self._rate_limited.is_set():
            # Provider is throttled; don't burn more calls. Record the
            # remaining windows as skipped so they can be resumed.
            self._take(idx, window, _skipped(window), label=False)
        else:
            self._asked[self._executor.submit(self._detect, window)] = (idx, window)
        for fut in [f for f in self._asked if f.done()]:
            idx, window = self._asked.pop(fut)
            self._take(idx, window, self._answer(fut, window), label=False)

    def finish(self) -> list[tuple[MusicSegment, DetectionResult]]:
        """Once every window is submitted: wait for the remaining answers, and
        return all of them in the windows' order."""
        self._all_submitted = True
        self._progress("detecting", f"Identifying {self._total} chunk(s)...")
        # Wait with a timeout so we can emit a "waiting" heartbeat while blocked
        # on the identification provider (e.g. Shazam rate-limiting delays the
        # response) — otherwise the UI would sit silent with no feedback.
        waited = 0.0
        remaining = set(self._asked)
        while remaining:
            done, remaining = wait(remaining, timeout=5.0, return_when=FIRST_COMPLETED)
            if not done:
                waited += 5.0
                self._progress(
                    "ident_waiting",
                    reaped=self._reaped,
                    total=self._total,
                    seconds=int(waited),
                )
                continue
            waited = 0.0
            for fut in done:
                idx, window = self._asked.pop(fut)
                self._take(idx, window, self._answer(fut, window), label=True)
        self._executor.shutdown(wait=True)
        return [self._answers[i] for i in range(self._total)]

    def abort(self) -> None:
        """Drop the calls not started yet; those under way finish."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _detect(self, window: MusicSegment) -> DetectionResult:
        """One provider call, in a worker thread."""
        # Once the provider throttles, windows already queued skip their call
        # too (they stay unidentified for a resume); calls under way finish.
        if self._rate_limited.is_set():
            return _skipped(window)
        if self._debug_sleep:
            time.sleep(self._debug_sleep)
        try:
            return self._client.detect(window)
        except RateLimitError:
            self._rate_limited.set()
            raise

    def _answer(self, fut: Future, window: MusicSegment) -> DetectionResult:
        """The provider's answer; a skipped window where there is none (a rate
        limit, which also stops the calls, or an error) instead of aborting
        everything: a resume asks for it again. Rejected keys abort: no resume
        gets past them."""
        try:
            return fut.result()
        except RateLimitError:
            self._rate_limited.set()
            return _skipped(window)
        except ProviderAuthError:
            raise
        except Exception:
            log.exception(
                "Identifying %.1f-%.1f s failed", window.start_time, window.end_time
            )
            return _skipped(window)

    def _take(
        self,
        idx: int,
        window: MusicSegment,
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
        self._reaped += 1
        self._answers[idx] = (window, detection)
        self._progress(
            "identified",
            start=round(window.start_time, 3),
            end=round(window.end_time, 3),
            title=detection.title,
            artist=detection.artist,
            reaped=self._reaped,
            total=self._total if self._all_submitted else None,
        )
        if label:
            prefix = f"[{self._reaped}/{self._total}]"
            if detection.title:
                det_detail = f"{prefix} {detection.title} - {detection.artist}"
            else:
                start, end = window.start_time, window.end_time
                det_detail = f"{prefix} {start:.0f}s-{end:.0f}s: no match"
            log.info("  %s", det_detail)
            self._progress(
                "detecting",
                det_detail,
                total=self._total,
                title=detection.title,
                artist=detection.artist,
            )


def _skipped(window: MusicSegment) -> DetectionResult:
    """A window the provider didn't answer, pending for a resume."""
    return DetectionResult(segment=window, title=None, artist=None, skipped=True)


class Pipeline:
    def __init__(
        self,
        loader: VideoLoader,
        chunker: AudioChunker,
        classifier: BaseClassifier,
        aggregator: ChunkAggregator,
        isolator: BaseMusicIsolator,
        detection_client: BaseMusicDetectionClient,
        detection_concurrency: int = 4,
        bridge_gap: float = BRIDGE_GAP_S,
        threshold: float = MUSIC_THRESHOLD,
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.classifier = classifier
        self.aggregator = aggregator
        self.isolator = isolator
        self.detection_client = detection_client
        # Number of identification calls dispatched concurrently. Kept modest by
        # default: identification providers are network-bound and rate-limited
        # (ACRCloud is metered).
        self.detection_concurrency = max(1, detection_concurrency)
        self.bridge_gap = bridge_gap
        # How likely a window must be music, by the classifier, to count as music.
        self.threshold = threshold

    @property
    def settings(self) -> dict:
        """What this pipeline analyzes with, as saved with a run's results."""
        return {
            "classifier": type(self.classifier).__name__,
            "threshold": self.threshold,
            "window": self.chunker.chunk_duration,
            "hop": self.chunker.chunk_duration - self.chunker.overlap,
            "bridge_gap": self.bridge_gap,
            "isolation": not isinstance(self.isolator, DummyIsolator),
        }

    def run(
        self,
        video_path: Path,
        on_progress=None,
        max_segment_duration: float | None = None,
        container_tags: dict | None = None,
    ) -> ResultsOutput:
        """Run the pipeline on a video file. Writes nothing: what to keep of the
        results is the caller's (analysis.analyze).

        Args:
            video_path: Path to the video file.
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

        waveform = waveform_peaks(audio, sample_rate)
        if on_progress:
            on_progress("waveform", **waveform)

        progress("classifying", "Starting chunk classification...")
        classification = _Classification(
            self.chunker, self.classifier, self.threshold, progress
        )
        identification = _Identification(
            self.detection_client, self.detection_concurrency, progress
        )
        try:
            # Windows are emitted as soon as each is fully classified (even mid
            # cue), so identification of early windows overlaps classification of
            # later audio. Isolation stays on this driver thread (a no-op for the
            # default DummyIsolator; the optional GPU DemucsIsolator is per-window).
            for window in self.aggregator.stream_windows(
                classification.slots_of(audio, sample_rate),
                audio,
                sample_rate,
                window_duration=max_segment_duration,
            ):
                identification.submit(
                    MusicSegment(
                        audio=self.isolator.isolate(window.audio, window.sample_rate),
                        start_time=window.start_time,
                        end_time=window.end_time,
                        sample_rate=window.sample_rate,
                    )
                )
            detection_results = identification.finish()
        except BaseException:
            identification.abort()
            raise

        found = sum(1 for _, d in detection_results if d.title)
        progress("detecting", f"{found}/{len(detection_results)} chunks identified")

        progress("writing")
        cues, pending_audio = build_cues(detection_results, self.bridge_gap)
        return ResultsOutput(
            source=video_path.name,
            cues=cues,
            waveform=waveform,
            film=container_tags or None,
            classification=merge_spans(classification.slots, self.classifier.labels),
            classification_windows=classification.windows,
            pending_audio=pending_audio,
        )


def build_pipeline(
    classifier: BaseClassifier,
    api: str,
    threshold: float = MUSIC_THRESHOLD,
    hop: float = CLASSIFICATION_HOP_S,
    window: float | None = None,
    bridge_gap: float = BRIDGE_GAP_S,
    isolation: bool = False,
) -> Pipeline:
    """The pipeline the app and the CLI analyze with.

    `api` names the detection provider(s), comma-separated to let them vote.
    A window is music above `threshold`, the classifier's music probability.
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
        bridge_gap=bridge_gap,
        threshold=threshold,
    )
