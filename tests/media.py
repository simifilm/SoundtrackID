"""Generated media files for tests, so no film footage is checked in."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np

FPS = 10
WIDTH, HEIGHT = 64, 48


def write_video(path: Path, seconds: int, tone_hz: int = 440) -> Path:
    """Write an MP4: frames brightening from black to white at 10 fps, plus a
    stereo tone at 48 kHz."""
    sr = 48000
    n_frames = seconds * FPS
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=FPS)
        video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        audio = container.add_stream("aac", rate=sr)
        audio.layout = "stereo"

        for i in range(n_frames):
            level = round(255 * i / (n_frames - 1))
            pixels = np.full((HEIGHT, WIDTH, 3), level, np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            container.mux(video.encode(frame))

        t = np.arange(seconds * sr) / sr
        tone = (0.5 * np.sin(2 * np.pi * tone_hz * t)).astype(np.float32)
        stereo = np.stack([tone, tone])
        for start in range(0, stereo.shape[1], 1024):
            chunk = np.ascontiguousarray(stereo[:, start : start + 1024])
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate = sr
            frame.pts = start
            frame.time_base = Fraction(1, sr)
            container.mux(audio.encode(frame))

        container.mux(video.encode())
        container.mux(audio.encode())
    return path
