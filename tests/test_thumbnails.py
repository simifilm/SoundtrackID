"""Tests for the filmstrip built from a video's frames, its frame rate and size."""

import numpy as np
import pytest
from PIL import Image

from soundtrackid.thumbnails import extract_filmstrip, frame_rate, video_info
from tests.media import FPS, HEIGHT, WIDTH, write_video


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


def test_frame_rate_of_video(tmp_path):
    video = write_video(tmp_path / "film.mp4", seconds=1)

    assert frame_rate(video) == FPS


def test_frame_rate_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")

    assert frame_rate(path) is None


def test_video_info_of_video(tmp_path):
    video = write_video(tmp_path / "film.mp4", seconds=2)

    info = video_info(video)

    assert info is not None
    assert (info["fps"], info["frames"]) == (FPS, 2 * FPS)
    assert (info["width"], info["height"]) == (WIDTH, HEIGHT)
    assert info["duration"] == pytest.approx(2.0, abs=0.1)


def test_video_info_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")

    assert video_info(path) is None
