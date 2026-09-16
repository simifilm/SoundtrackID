"""Audio chunking."""

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

from soundtrackID.models import AudioChunk


class AudioChunker:
    def __init__(self, chunk_duration: float = 10.0, overlap: float = 2.0) -> None:
        self.chunk_duration = chunk_duration
        self.overlap = overlap

    def chunk(self, audio: NDArray[np.float32], sample_rate: int) -> Iterator[AudioChunk]:
        """Split audio into overlapping chunks.

        Yields chunks of chunk_duration seconds, stepping by (chunk_duration - overlap).
        Always emits a final chunk anchored to the end of the audio so the tail is
        covered even when its length is not a multiple of the step size.
        """
        chunk_samples = int(self.chunk_duration * sample_rate)
        step_samples = int((self.chunk_duration - self.overlap) * sample_rate)
        if len(audio) < chunk_samples:
            return

        last_start = -1
        for i in range(0, len(audio) - chunk_samples + 1, step_samples):
            last_start = i
            yield AudioChunk(
                data=audio[i : i + chunk_samples],
                start_time=i / sample_rate,
                end_time=(i + chunk_samples) / sample_rate,
                sample_rate=sample_rate,
            )

        tail_start = len(audio) - chunk_samples
        if tail_start > last_start:
            yield AudioChunk(
                data=audio[tail_start : tail_start + chunk_samples],
                start_time=tail_start / sample_rate,
                end_time=len(audio) / sample_rate,
                sample_rate=sample_rate,
            )
