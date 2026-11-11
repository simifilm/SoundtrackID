"""Tests for the data models (soundtrackID.models)."""

import numpy as np
from scipy.io import wavfile

from soundtrackID.models import MusicSegment, parse_year


def test_a_clip_is_written_as_16_bit_wav_and_clipped_at_full_scale(tmp_path):
    """Louder than full scale is clipped, not wrapped around into a click."""
    audio = np.array([0.0, 0.5, 1.5, -1.5], dtype=np.float32)
    clip = MusicSegment(audio=audio, start_time=3.0, end_time=4.0, sample_rate=1000)

    clip.write_wav(tmp_path / "clip.wav")

    sample_rate, written = wavfile.read(tmp_path / "clip.wav")
    assert sample_rate == 1000
    assert written.dtype == np.int16
    assert written.tolist() == [0, 16383, 32767, -32767]


def test_a_year_is_the_first_four_digits_of_a_date_or_the_year_itself():
    """As providers, TMDb, Wikidata, MusicBrainz and MP4 tags give it."""
    assert parse_year("1950-08-04") == 1950
    assert parse_year("+1827-03-26T00:00:00Z") == 1827
    assert parse_year("1950") == 1950
    assert parse_year(1950) == 1950
    assert parse_year(None) is None
    assert parse_year("") is None
    assert parse_year("not a year") is None
    assert parse_year({}) is None
