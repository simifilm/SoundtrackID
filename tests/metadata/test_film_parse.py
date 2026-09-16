import json
from pathlib import Path

from soundtrackID.metadata.film import (
    _extract_tmdb_director_id,
    _parse_imdbapi_title,
    _parse_tmdb_movie,
    _parse_wikidata_film,
    _parse_year,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_tmdb_movie_director_found():
    movie = load("tmdb_movie.json")
    credits = load("tmdb_credits.json")
    info = _parse_tmdb_movie(movie, credits)
    assert info.title == "Sunset Boulevard"
    assert info.year == 1950
    assert info.imdb_id == "tt0043014"
    assert info.director == "Billy Wilder"
    assert info.director_death_year is None  # not in /movie/credits; needs /person


def test_extract_tmdb_director_id():
    credits = load("tmdb_credits.json")
    assert _extract_tmdb_director_id(credits) == 5602


def test_parse_tmdb_movie_no_director():
    movie = load("tmdb_movie.json")
    credits = {"crew": [{"id": 1, "name": "Anon", "job": "Editor"}]}
    info = _parse_tmdb_movie(movie, credits)
    assert info.title == "Sunset Boulevard"
    assert info.director is None


def test_parse_wikidata_film():
    info = _parse_wikidata_film(load("wikidata_film.json"))
    assert info is not None
    assert info.title == "Sunset Boulevard"
    assert info.year == 1950
    assert info.director == "Billy Wilder"
    assert info.director_death_year == 2002
    assert info.imdb_id == "tt0043014"


def test_parse_wikidata_film_empty():
    assert _parse_wikidata_film({"results": {"bindings": []}}) is None


def test_parse_imdbapi_title():
    info = _parse_imdbapi_title(load("imdbapi_title.json"))
    assert info is not None
    assert info.title == "Sunset Boulevard"
    assert info.year == 1950
    assert info.director == "Billy Wilder"
    assert info.director_death_year == 2002


def test_parse_imdbapi_title_missing_director():
    data = {"id": "tt0001", "primaryTitle": "X", "startYear": 2020}
    info = _parse_imdbapi_title(data)
    assert info is not None
    assert info.title == "X"
    assert info.director is None
    assert info.director_death_year is None


def test_parse_year_variants():
    assert _parse_year("1950-08-04") == 1950
    assert _parse_year("1950") == 1950
    assert _parse_year(1950) == 1950
    assert _parse_year(None) is None
    assert _parse_year("") is None
    assert _parse_year("not a year") is None
