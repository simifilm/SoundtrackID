"""Tests for the film lookup chain TMDb → Wikidata → imdbapi.dev, over a fake API."""

import json
from pathlib import Path

import httpx

from soundtrackid.metadata import USER_AGENT
from soundtrackid.metadata.film import FilmLookup
from tests.metadata.mock_http import mock_client

FIXTURES = Path(__file__).parent / "fixtures"
TMDB = "api.themoviedb.org"
WIKIDATA = "query.wikidata.org/sparql"
IMDBAPI = "api.imdbapi.dev/titles/tt0043014"

SUNSET_BOULEVARD = {
    "title": "Sunset Boulevard",
    "year": 1950,
    "director": "Billy Wilder",
    "director_death_year": 2002,
    "imdb_id": "tt0043014",
    "tmdb_id": 599,
    "media_type": "movie",
    "music_composer": None,
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _tmdb_routes(deathday="2002-03-27") -> dict:
    return {
        f"{TMDB}/3/find/tt0043014": {"movie_results": [{"id": 599}]},
        f"{TMDB}/3/movie/599": load("tmdb_movie.json"),
        f"{TMDB}/3/movie/599/credits": load("tmdb_credits.json"),
        f"{TMDB}/3/person/5602": {"deathday": deathday},
    }


def _hosts(requests) -> set:
    return {r.url.host for r in requests}


def test_tmdb_alone_completes_the_film():
    client, requests = mock_client(_tmdb_routes())
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(imdb_id="tt0043014")

    assert info.to_dict() == {**SUNSET_BOULEVARD, "sources": ["tmdb"]}
    # Complete after TMDb, so the fallbacks are never asked
    assert _hosts(requests) == {TMDB}
    for r in requests:
        assert r.headers["authorization"] == "Bearer tok"
        assert "api_key" not in r.url.params


def test_tmdb_v3_key_goes_in_the_query():
    client, requests = mock_client(_tmdb_routes())
    FilmLookup(client, tmdb_api_key="k").lookup(imdb_id="tt0043014")

    for r in requests:
        assert r.url.params["api_key"] == "k"
        assert "authorization" not in r.headers


def test_tmdb_searches_by_title_without_ids():
    routes = {f"{TMDB}/3/search/movie": {"results": [{"id": 599}]}, **_tmdb_routes()}
    client, requests = mock_client(routes)
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        title="Sunset Boulevard", year=1950
    )

    assert info.tmdb_id == 599 and info.director == "Billy Wilder"
    search = requests[0]
    assert search.url.path == "/3/search/movie"
    assert search.url.params["query"] == "Sunset Boulevard"
    assert search.url.params["year"] == "1950"


def test_without_tmdb_credentials_wikidata_is_asked():
    client, requests = mock_client({WIKIDATA: load("wikidata_film.json")})
    info = FilmLookup(client).lookup(imdb_id="tt0043014")

    assert info.to_dict() == {
        **SUNSET_BOULEVARD,
        "tmdb_id": None,
        "media_type": None,
        "sources": ["wikidata"],
    }
    assert _hosts(requests) == {"query.wikidata.org"}
    assert 'wdt:P345 "tt0043014"' in requests[0].url.params["query"]
    assert requests[0].headers["user-agent"] == USER_AGENT


def test_wikidata_fills_only_what_tmdb_lacks():
    wikidata = load("wikidata_film.json")
    wikidata["results"]["bindings"][0]["filmLabel"]["value"] = "Boulevard du crépuscule"
    client, _ = mock_client({**_tmdb_routes(deathday=None), WIKIDATA: wikidata})
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(imdb_id="tt0043014")

    # TMDb has no death year; Wikidata adds it but doesn't replace TMDb's title
    assert info.to_dict() == {**SUNSET_BOULEVARD, "sources": ["tmdb", "wikidata"]}


def test_imdbapi_is_the_last_resort():
    client, _ = mock_client(
        {
            f"{TMDB}/3/find/tt0043014": 500,
            WIKIDATA: {"results": {"bindings": []}},
            IMDBAPI: load("imdbapi_title.json"),
        }
    )
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(imdb_id="tt0043014")

    assert info.to_dict() == {
        **SUNSET_BOULEVARD,
        "tmdb_id": None,
        "media_type": None,
        "sources": ["imdbapi"],
    }


def test_network_errors_leave_only_the_given_ids():
    down = httpx.ConnectError("down")
    routes = {
        f"{TMDB}/3/find/tt0043014": down,
        WIKIDATA: down,
        IMDBAPI: down,
    }
    client, requests = mock_client(routes)
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(imdb_id="tt0043014")

    assert info.to_dict() == {
        **{k: None for k in SUNSET_BOULEVARD},
        "imdb_id": "tt0043014",
        "sources": [],
    }
    assert _hosts(requests) == {TMDB, "query.wikidata.org", "api.imdbapi.dev"}


def test_a_wrong_tmdb_id_falls_back_to_the_title():
    routes = {f"{TMDB}/3/search/movie": {"results": [{"id": 599}]}, **_tmdb_routes()}
    client, requests = mock_client(routes)
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        tmdb_id=1, title="Sunset Boulevard"
    )

    # /3/movie/1 is unknown (404), so the title is searched
    assert info.to_dict() == {**SUNSET_BOULEVARD, "sources": ["tmdb"]}
    assert [r.url.path for r in requests][:2] == ["/3/movie/1", "/3/search/movie"]


def test_the_films_own_imdb_id_beats_a_wrong_one():
    """Also for the fallbacks, asked about the film TMDb found, not another one."""
    client, requests = mock_client(
        {**_tmdb_routes(deathday=None), WIKIDATA: load("wikidata_film.json")}
    )
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        tmdb_id=599, imdb_id="tt9999999"
    )

    assert info.imdb_id == "tt0043014"
    [wikidata] = [r for r in requests if r.url.host == "query.wikidata.org"]
    assert 'wdt:P345 "tt0043014"' in wikidata.url.params["query"]


# ── Search: the films to choose from ───────────────────────────────────────

SEARCH = f"{TMDB}/3/search/movie"
SUNSET_1988 = {"id": 42, "title": "Sunset", "release_date": "1988-04-29"}


def test_search_lists_the_id_matches_first_then_the_title_matches():
    movie = {**load("tmdb_movie.json"), "poster_path": "/p.jpg", "overview": "Joe…"}
    client, _ = mock_client(
        {
            f"{TMDB}/3/movie/599": movie,
            f"{TMDB}/3/find/tt0043014": {"movie_results": [{"id": 599}]},
            SEARCH: {"results": [{"id": 599}, SUNSET_1988]},
        }
    )
    candidates = FilmLookup(client, tmdb_bearer_token="tok").search(
        tmdb_id=599, imdb_id="tt0043014", title="Sunset"
    )

    # Sunset Boulevard is found three times and listed once, as the ID's film
    assert candidates == [
        {
            "tmdb_id": 599,
            "media_type": "movie",
            "title": "Sunset Boulevard",
            "original_title": "Sunset Boulevard",
            "year": 1950,
            "overview": "Joe…",
            "poster_url": "https://image.tmdb.org/t/p/w92/p.jpg",
            "match": "tmdb_id",
        },
        {
            "tmdb_id": 42,
            "media_type": "movie",
            "title": "Sunset",
            "original_title": None,
            "year": 1988,
            "overview": None,
            "poster_url": None,
            "match": None,
        },
    ]


def test_search_tries_again_without_the_year():
    def search(request):
        return {"results": [] if "year" in request.url.params else [SUNSET_1988]}

    client, requests = mock_client({SEARCH: search})
    candidates = FilmLookup(client, tmdb_bearer_token="tok").search(
        title="Sunset", year=1950
    )

    assert [c["tmdb_id"] for c in candidates] == [42]
    # Films and series, with the year, then both again without it
    assert [(r.url.path, r.url.params.get("year")) for r in requests] == [
        ("/3/search/movie", "1950"),
        ("/3/search/tv", "1950"),
        ("/3/search/movie", None),
        ("/3/search/tv", None),
    ]


def test_search_without_tmdb_offers_the_film_wikidata_finds():
    client, _ = mock_client({WIKIDATA: load("wikidata_film.json")})
    [candidate] = FilmLookup(client).search(title="Sunset Boulevard")

    film = {
        **SUNSET_BOULEVARD,
        "tmdb_id": None,
        "media_type": None,
        "sources": ["wikidata"],
    }
    assert candidate["film"] == film
    assert (candidate["title"], candidate["year"]) == ("Sunset Boulevard", 1950)


def test_search_finding_nothing_offers_nothing():
    client, _ = mock_client(
        {SEARCH: {"results": []}, WIKIDATA: {"results": {"bindings": []}}}
    )
    candidates = FilmLookup(client, tmdb_bearer_token="tok").search(title="Nope")

    assert candidates == []


# ── Series ─────────────────────────────────────────────────────────────────

BREAKING_BAD = {
    "title": "Breaking Bad",
    "year": 2008,
    "director": (
        "Michelle MacLaren, Adam Bernstein, Vince Gilligan, Michael Slovis, "
        "Bryan Cranston, Rian Johnson"
    ),
    "director_death_year": None,
    "imdb_id": "tt0903747",
    "tmdb_id": 1396,
    "media_type": "tv",
    "music_composer": "Dave Porter",
    "sources": ["tmdb"],
}
# The film with Breaking Bad's TMDb ID
MIRROR = {"id": 1396, "title": "Mirror"}


def _series_routes() -> dict:
    return {
        f"{TMDB}/3/tv/1396": load("tmdb_tv.json"),
        f"{TMDB}/3/tv/1396/external_ids": {"imdb_id": "tt0903747"},
        f"{TMDB}/3/tv/1396/aggregate_credits": load("tmdb_tv_aggregate_credits.json"),
    }


def test_a_series_is_found_by_its_imdb_id():
    find = {"movie_results": [], "tv_results": [{"id": 1396}]}
    client, requests = mock_client(
        {f"{TMDB}/3/find/tt0903747": find, **_series_routes()}
    )
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(imdb_id="tt0903747")

    assert info.to_dict() == BREAKING_BAD
    # Complete without a director's death year, so the fallbacks, which would
    # add one of the directors, are never asked
    assert _hosts(requests) == {TMDB}


def test_a_chosen_series_is_looked_up_as_one():
    client, requests = mock_client({f"{TMDB}/3/movie/1396": MIRROR, **_series_routes()})
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        tmdb_id=1396, media_type="tv"
    )

    assert info.to_dict() == BREAKING_BAD
    assert "/3/movie/1396" not in [r.url.path for r in requests]


def test_a_series_tmdb_id_stays_one_when_tmdb_fails():
    client, _ = mock_client({f"{TMDB}/3/tv/1396": 500})
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        tmdb_id=1396, media_type="tv"
    )

    assert (info.tmdb_id, info.media_type) == (1396, "tv")


def test_a_title_is_searched_among_series_when_no_film_matches():
    routes = {
        SEARCH: {"results": []},
        f"{TMDB}/3/search/tv": {"results": [{"id": 1396}]},
        **_series_routes(),
    }
    client, requests = mock_client(routes)
    info = FilmLookup(client, tmdb_bearer_token="tok").lookup(
        title="Breaking Bad", year=2013
    )

    assert info.to_dict() == BREAKING_BAD
    # TMDb matches the year against every episode's
    [search] = [r for r in requests if r.url.path == "/3/search/tv"]
    assert search.url.params["year"] == "2013"


def test_search_offers_films_and_series_alternately():
    films = {"results": [{"id": 1, "title": "A"}, {"id": 2}, {"id": 3}]}
    series = {
        "results": [
            {"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20"}
        ]
    }
    client, _ = mock_client({SEARCH: films, f"{TMDB}/3/search/tv": series})
    candidates = FilmLookup(client, tmdb_bearer_token="tok").search(
        title="Breaking Bad"
    )

    assert [(c["media_type"], c["tmdb_id"]) for c in candidates] == [
        ("movie", 1),
        ("tv", 1396),
        ("movie", 2),
        ("movie", 3),
    ]
    assert (candidates[1]["title"], candidates[1]["year"]) == ("Breaking Bad", 2008)


def test_search_offers_the_film_and_the_series_of_a_tmdb_id():
    client, _ = mock_client(
        {f"{TMDB}/3/movie/1396": MIRROR, f"{TMDB}/3/tv/1396": load("tmdb_tv.json")}
    )
    candidates = FilmLookup(client, tmdb_bearer_token="tok").search(tmdb_id=1396)

    assert [(c["media_type"], c["title"], c["match"]) for c in candidates] == [
        ("movie", "Mirror", "tmdb_id"),
        ("tv", "Breaking Bad", "tmdb_id"),
    ]


def test_search_finds_a_series_by_its_imdb_id():
    find = {"movie_results": [], "tv_results": [load("tmdb_tv.json")]}
    client, _ = mock_client({f"{TMDB}/3/find/tt0903747": find})
    [candidate] = FilmLookup(client, tmdb_bearer_token="tok").search(
        imdb_id="tt0903747"
    )

    assert (candidate["media_type"], candidate["tmdb_id"]) == ("tv", 1396)
    assert candidate["match"] == "imdb_id"
