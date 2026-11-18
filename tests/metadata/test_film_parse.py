import json
from pathlib import Path

from soundtrackid.metadata.film import (
    _extract_tmdb_director_id,
    _parse_imdbapi_title,
    _parse_tmdb_movie,
    _parse_tmdb_series,
    _parse_wikidata_film,
    _sparql_film_by_imdb,
    _sparql_film_by_title,
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


def test_parse_tmdb_series_lists_every_director_most_episodes_first():
    show = load("tmdb_tv.json")
    credits = load("tmdb_tv_aggregate_credits.json")
    info = _parse_tmdb_series(show, {"imdb_id": "tt0903747"}, credits)

    assert (info.title, info.year, info.media_type) == ("Breaking Bad", 2008, "tv")
    assert info.imdb_id == "tt0903747"
    # Ties in TMDb's order; Slovis as director only, not as photography director
    assert info.director == (
        "Michelle MacLaren, Adam Bernstein, Vince Gilligan, Michael Slovis, "
        "Bryan Cranston, Rian Johnson"
    )
    assert info.music_composer == "Dave Porter"
    assert info.director_death_year is None


def test_parse_tmdb_series_with_one_director():
    credits = {"crew": [{"name": "Johan Renck", "jobs": [{"job": "Director"}]}]}
    info = _parse_tmdb_series({"name": "Chernobyl"}, {}, credits)

    assert info.director == "Johan Renck"
    assert info.imdb_id is None


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


def test_wikidata_queries_keep_what_the_user_typed_inside_their_strings():
    """A quote or a backslash in the title or the IMDb ID is escaped."""
    title_query = _sparql_film_by_title('Say "No" \\ Yes', None)
    imdb_query = _sparql_film_by_imdb('tt1" \\')

    assert 'rdfs:label "Say \\"No\\" \\\\ Yes"@en.' in title_query
    assert 'wdt:P345 "tt1\\" \\\\".' in imdb_query
