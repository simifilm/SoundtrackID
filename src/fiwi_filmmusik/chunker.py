"""Audio chunking."""

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

from fiwi_filmmusik.models import AudioChunk


class AudioChunker:
    def __init__(self, chunk_duration: float = 10.0, overlap: float = 2.0) -> None:
        self.chunk_duration = chunk_duration
        self.overlap = overlap

    def chunk(self, audio: NDArray[np.float32], sample_rate: int) -> Iterator[AudioChunk]:
        """Split audio into overlapping chunks.

        Yields chunks of chunk_duration seconds, stepping by (chunk_duration - overlap).
        Discards the final chunk if shorter than chunk_duration.
        """
        chunk_samples = int(self.chunk_duration * sample_rate)
        step_samples = int((self.chunk_duration - self.overlap) * sample_rate)

        for i in range(0, len(audio) - chunk_samples + 1, step_samples):
            chunk_data = audio[i : i + chunk_samples]
            start_time = i / sample_rate
            end_time = (i + chunk_samples) / sample_rate
            yield AudioChunk(
                data=chunk_data,
                start_time=start_time,
                end_time=end_time,
                sample_rate=sample_rate,
            )
