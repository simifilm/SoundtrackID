"""Video and audio loading."""

from pathlib import Path

import av
import numpy as np
from numpy.typing import NDArray


class VideoLoader:
    def __init__(self, target_sample_rate: int = 16000, mono: bool = True) -> None:
        self.target_sample_rate = target_sample_rate
        self.mono = mono

    def load(self, path: Path) -> tuple[NDArray[np.float32], int]:
        """Extract audio from a video file using PyAV."""
        container = av.open(str(path))
        audio_stream = container.streams.audio[0]

        # Set up resampler
        resampler = av.AudioResampler(
            format="s16",
            layout="mono" if self.mono else audio_stream.layout,
            rate=self.target_sample_rate,
        )

        frames = []
        for frame in container.decode(audio=0):
            resampled = resampler.resample(frame)
            for r in resampled:
                frames.append(r.to_ndarray())

        container.close()

        audio = np.concatenate(frames, axis=1).flatten()
        audio = audio.astype(np.float32) / 32768.0

        return audio, self.target_sample_rate


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "tests/test_video.mp4"
    loader = VideoLoader()
    audio, sr = loader.load(Path(path))
    print(f"Loaded: {path}")
    print(f"Duration: {len(audio) / sr:.1f}s, Sample rate: {sr} Hz")
