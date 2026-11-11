"""Tests for reading the film from a video's MP4 tags (iTunes / Subler atoms)."""

import plistlib

import pytest
from mutagen.mp4 import MP4, MP4FreeForm

from soundtrackID.metadata.mp4_tags import read_mp4_tags
from tests.media import write_video

ITUNES = "----:com.apple.iTunes:"


@pytest.fixture
def video(tmp_path):
    return write_video(tmp_path / "film.mp4", seconds=1)


def _tag(path, tags: dict):
    mp4 = MP4(str(path))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags.update(tags)
    mp4.save()


def _text(value: str) -> list:
    return [MP4FreeForm(value.encode())]


def _itunmovi(directors: list) -> list:
    return [MP4FreeForm(plistlib.dumps({"directors": directors}))]


def test_reads_the_tags_subler_writes(video):
    _tag(
        video,
        {
            "\xa9nam": ["Sunset Boulevard"],
            "\xa9day": ["1950-08-10T07:00:00Z"],
            ITUNES + "iTunMOVI": _itunmovi([{"name": "Billy Wilder"}]),
            ITUNES + "IMDB ID": _text("tt0043014"),
            ITUNES + "TMDB ID": _text("599"),
        },
    )
    assert read_mp4_tags(video) == {
        "title": "Sunset Boulevard",
        "year": 1950,
        "director": "Billy Wilder",
        "imdb_id": "tt0043014",
        "tmdb_id": 599,
    }


def test_lowercase_id_keys_and_plain_director_names(video):
    _tag(
        video,
        {
            ITUNES + "iTunMOVI": _itunmovi(["Billy Wilder"]),
            ITUNES + "imdb id": _text("tt0043014"),
            ITUNES + "tmdb id": _text("599"),
        },
    )
    assert read_mp4_tags(video) == {
        "director": "Billy Wilder",
        "imdb_id": "tt0043014",
        "tmdb_id": 599,
    }


def test_malformed_values_are_skipped(video):
    _tag(
        video,
        {
            "\xa9day": ["unknown"],
            ITUNES + "iTunMOVI": [MP4FreeForm(b"not a plist")],
            ITUNES + "IMDB ID": _text("0043014"),  # IMDb IDs start with "tt"
            ITUNES + "TMDB ID": _text("abc"),
        },
    )
    assert read_mp4_tags(video) == {}


def test_untagged_video_gives_nothing(video):
    assert read_mp4_tags(video) == {}


def test_unreadable_file_gives_nothing(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    assert read_mp4_tags(path) == {}
