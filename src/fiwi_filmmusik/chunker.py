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
        """Split audio into overlapping chunks."""
        raise NotImplementedError
