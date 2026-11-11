"""Tests for the filmstrip built from a video's frames."""

import numpy as np
from PIL import Image

from soundtrackID.thumbnails import extract_filmstrip
from tests.media import write_video


def test_filmstrip_from_video(tmp_path):
    video = write_video(tmp_path / "film.mp4", seconds=10)
    out = tmp_path / "run" / "filmstrip.jpg"

    meta = extract_filmstrip(video, out)

    # 10 s gives the minimum of 24 thumbnails; 64x48 frames give 64x48 thumbnails
    assert meta == {
        "count": 24,
        "thumb_width": 64,
        "thumb_height": 48,
        "duration": 10.0,
    }
    strip = np.asarray(Image.open(out).convert("L"), dtype=float)
    assert strip.shape == (48, 24 * 64)

    # The frames brighten over time, so the thumbnails must too: in order, from
    # the dark start to the bright end.
    brightness = strip.reshape(48, 24, 64).mean(axis=(0, 2))
    assert all(b >= a - 3 for a, b in zip(brightness, brightness[1:]))
    assert brightness[0] < 40 and brightness[-1] > 215


def test_filmstrip_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    out = tmp_path / "filmstrip.jpg"

    assert extract_filmstrip(path, out) is None
    assert not out.exists()
