"""What follows from a run's results (derived): rights, anachronism, track."""

from datetime import date

import pytest

from soundtrackID.derived import is_anachronistic, refresh, track_key

FILM = {"title": "Film", "year": 1950}


@pytest.mark.parametrize(
    "cue, film, expected",
    [
        # The work's own year decides when it is known, either way.
        ({"enrichment": {"original_year": 1960}}, FILM, True),
        ({"enrichment": {"original_year": 1925}, "release_year": 2020}, FILM, False),
        # Up to 3 years later is reissue or tagging noise.
        ({"enrichment": {"original_year": 1953}}, FILM, False),
        # Else the release year, only a hint, with two safeguards against it.
        ({"release_year": 2020}, FILM, True),
        ({"release_year": 1939}, FILM, False),
        (
            {"release_year": 2020, "enrichment": {"composer_death_year": 1949}},
            FILM,
            False,  # its composer died before the film: the work predates it
        ),
        (
            {"release_year": 2020, "artist": "Franz Waxman Orchestra"},
            {**FILM, "music_composer": "Franz Waxman"},
            False,  # by the film's own composer: it is the score
        ),
        # Without the film's year, nothing is later than it.
        ({"release_year": 2020}, {"title": "Film"}, False),
        ({"release_year": 2020}, None, False),
    ],
)
def test_music_released_after_the_film(cue, film, expected):
    assert is_anachronistic(cue, film) is expected


def test_the_same_piece_has_the_same_track_key():
    """Whatever the case and punctuation; accents are kept, so "Ernö" and
    "Erno" stay apart only if the providers spell them apart."""
    a = track_key({"title": "La Cumparsita", "artist": "Matos Rodríguez"})
    b = track_key({"title": "la cumparsita!", "artist": "matos  rodríguez"})

    assert a == b == "la cumparsita|matos rodríguez"
    assert track_key({"title": None}) is None


def test_refresh_adds_what_follows_to_every_cue():
    died = date.today().year - 71
    results = {
        "film": FILM,
        "cues": [
            {
                "title": "Song",
                "artist": "A",
                "release_year": 2020,
                "enrichment": {
                    "authors": [
                        {"name": "A", "roles": ["composer"], "death_year": died}
                    ],
                    "public_domain": False,  # as assessed last year
                },
            },
            {"title": None},
        ],
    }

    refresh(results)

    song, unidentified = results["cues"]
    assert (song["anachronistic"], song["track_key"]) == (True, "song|a")
    assert song["enrichment"]["public_domain"] is True
    assert (unidentified["anachronistic"], unidentified["track_key"]) == (False, None)
