"""Tests for the film lookup chain TMDb → Wikidata → imdbapi.dev, over a fake API."""

import json
from pathlib import Path

import httpx

from soundtrackID.metadata import USER_AGENT
from soundtrackID.metadata.film import FilmLookup
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
