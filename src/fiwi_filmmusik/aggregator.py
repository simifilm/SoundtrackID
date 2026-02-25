"""Chunk aggregation."""

import numpy as np
from numpy.typing import NDArray

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
        raise NotImplementedError
