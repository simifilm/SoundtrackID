"""Chunk aggregation."""

from collections.abc import Iterable, Iterator

import numpy as np
from numpy.typing import NDArray

from soundtrackID.models import ClassificationResult, MusicSegment


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
        (chunks separated by gaps <= ``gap_tolerance`` merge into one run).
        Within a run, if
        ``window_duration`` is set, each fixed-length window is emitted the moment
        classification has advanced past its end — so identification can overlap
        classification even for a single long, uninterrupted music cue. The
        trailing window is emitted when the run closes (a gap larger than
        ``gap_tolerance``) or when ``results`` is exhausted; it is anchored to
        the run's end, so it keeps the full ``window_duration`` by overlapping
        the previous window, unless the run itself is shorter. With
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

        def build_trailing(
            run_start: float, run_end: float, emitted_until: float
        ) -> MusicSegment:
            start = (
                max(run_start, run_end - window_duration)
                if window_duration
                else emitted_until
            )
            return build(start, run_end)

        for result in results:
            if not result.is_music:
                continue
            chunk = result.chunk
            if run_start is None:
                run_start, run_end, emitted_until = (
                    chunk.start_time,
                    chunk.end_time,
                    chunk.start_time,
                )
            elif chunk.start_time - run_end <= self.gap_tolerance:
                run_end = chunk.end_time
            else:
                # Run closed by a gap: emit its trailing window, start a new run.
                if run_end > emitted_until:
                    yield build_trailing(run_start, run_end, emitted_until)
                run_start, run_end, emitted_until = (
                    chunk.start_time,
                    chunk.end_time,
                    chunk.start_time,
                )

            if window_duration:
                while run_end - emitted_until >= window_duration:
                    yield build(emitted_until, emitted_until + window_duration)
                    emitted_until += window_duration

        if run_start is not None and run_end > emitted_until:
            yield build_trailing(run_start, run_end, emitted_until)
