"""Music isolation."""

from abc import ABC, abstractmethod

import numpy as np
from numpy.typing import NDArray


class BaseMusicIsolator(ABC):
    @abstractmethod
    def isolate(self, audio: NDArray[np.float32], sample_rate: int) -> NDArray[np.float32]:
        """Isolate music from audio."""
        ...


class DummyIsolator(BaseMusicIsolator):
    def isolate(self, audio: NDArray[np.float32], sample_rate: int) -> NDArray[np.float32]:
        return audio
