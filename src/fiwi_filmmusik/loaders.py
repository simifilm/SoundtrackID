"""Video and audio loading."""

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class VideoLoader:
    def __init__(self, target_sample_rate: int = 44100, mono: bool = True) -> None:
        self.target_sample_rate = target_sample_rate
        self.mono = mono

    def load(self, path: Path) -> tuple[NDArray[np.float32], int]:
        """Extract audio from a video file."""
        raise NotImplementedError
