"""Tests for the composer lookup chain MusicBrainz → Wikidata, over a fake API."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from soundtrackID.metadata import music
from soundtrackID.metadata.music import MB_USER_AGENT, MusicLookup
from tests.metadata.mock_http import mock_client

FIXTURES = Path(__file__).parent / "fixtures"
MB = "musicbrainz.org/ws/2"
WIKIDATA = "query.wikidata.org/sparql"
ISRC = "DEAAA0000001"
BEETHOVEN_ID = "1f9df192-a621-4f54-8850-2c5373b7eac9"
BEETHOVEN = {"type": "Person", "life-span": {"begin": "1770-12-17", "end": "1827"}}
NO_ROWS = {"results": {"bindings": []}}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Record the waits (MusicBrainz's 1 request/s, back-off) instead of sleeping."""
    waits = []
    fake_time = SimpleNamespace(sleep=waits.append, monotonic=time.monotonic)
    monkeypatch.setattr(music, "time", fake_time)
    return waits


def _isrc_chain(artist=BEETHOVEN) -> dict:
    """MusicBrainz: ISRC → recording → work → its composer, Beethoven."""
    return {
        f"{MB}/isrc/{ISRC}": {"recordings": [{"id": "rec1"}]},
        f"{MB}/recording/rec1": {
            "relations": [{"type": "performance", "work": {"id": "work1"}}]
        },
        f"{MB}/work/work1": load("mb_work_with_composer.json"),
        f"{MB}/artist/{BEETHOVEN_ID}": artist,
    }


def _wikidata(life=NO_ROWS, work_year=NO_ROWS, composer_search=NO_ROWS):
    """Answer Wikidata's three queries: an artist's life years by MusicBrainz
    ID, a work's year, and the composer search by title and artist."""

    def answer(request):
        query = request.url.params["query"]
        if "wdt:P434" in query:
            return life
        if "wd:Q2188189" in query:
            return work_year
        if "wd:Q207628" in query:
            return composer_search
        return 400

    return answer


def test_isrc_chain_resolves_the_composer():
    year = {"results": {"bindings": [{"date": {"value": "1808-12-22T00:00:00Z"}}]}}
    routes = {**_isrc_chain(), WIKIDATA: _wikidata(work_year=year)}
    client, requests = mock_client(routes)
    info = MusicLookup(client).lookup_composer(
        isrc=ISRC, title="Symphony No. 5", artist="Berliner Philharmoniker"
    )

    assert info.composer == "Ludwig van Beethoven"
    assert (info.composer_birth_year, info.composer_death_year) == (1770, 1827)
    assert info.work_title == "Symphony no. 5 in C minor, op. 67"
    assert info.composer_match == "isrc"
    assert info.original_year == 1808
    assert info.sources == ["musicbrainz", "wikidata"]
    assert info.public_domain is True and info.authors_complete is True

    mb = [r for r in requests if r.url.host == "musicbrainz.org"]
    assert [r.url.path for r in mb] == [
        f"/ws/2/isrc/{ISRC}",
        "/ws/2/recording/rec1",
        "/ws/2/work/work1",
        f"/ws/2/artist/{BEETHOVEN_ID}",
    ]
    assert [r.url.params.get("inc") for r in mb] == [
        None,
        "work-rels",
        "artist-rels",
        "url-rels",
    ]
    for r in mb:
        assert r.url.params["fmt"] == "json"
        assert r.headers["user-agent"] == MB_USER_AGENT


def test_repeated_track_is_served_from_the_cache():
    client, requests = mock_client({**_isrc_chain(), WIKIDATA: _wikidata()})
    lookup = MusicLookup(client)
    first = lookup.lookup_composer(isrc=ISRC, title="Symphony No. 5", artist="X")
    calls = len(requests)
    second = lookup.lookup_composer(isrc=ISRC, title="Symphony No. 5", artist="X")

    assert len(requests) == calls
    assert second.composer == first.composer


def test_musicbrainz_calls_wait_a_second_apart(sleeps):
    client, requests = mock_client(_isrc_chain())
    MusicLookup(client).lookup_composer(isrc=ISRC)

    assert len(requests) == 4
    assert len(sleeps) == 3 and all(0.9 < s <= 1.0 for s in sleeps)


def test_musicbrainz_503_is_retried_once(sleeps):
    answers = iter([503, {"recordings": [{"id": "rec1"}]}])
    routes = {**_isrc_chain(), f"{MB}/isrc/{ISRC}": lambda request: next(answers)}
    client, _ = mock_client(routes)
    info = MusicLookup(client).lookup_composer(isrc=ISRC)

    assert info.composer == "Ludwig van Beethoven"
    assert 1.5 in sleeps  # backed off before the retry


def test_unknown_isrc_falls_back_to_the_work_by_title():
    mahler_work = {
        "id": "work0",
        "title": "Symphony No. 5",
        "relations": [
            {"type": "composer", "artist": {"id": "mahler", "name": "Gustav Mahler"}}
        ],
    }
    routes = {
        f"{MB}/work": {"works": [{"id": "work0"}, {"id": "work1"}]},
        f"{MB}/work/work0": mahler_work,
        f"{MB}/work/work1": load("mb_work_with_composer.json"),
        f"{MB}/artist/mahler": {"type": "Person", "life-span": {"end": "1911"}},
        f"{MB}/artist/{BEETHOVEN_ID}": BEETHOVEN,
        WIKIDATA: _wikidata(),
    }
    client, requests = mock_client(routes)
    info = MusicLookup(client).lookup_composer(
        isrc=ISRC, title="Symphony No. 5", artist="Ludwig van Beethoven"
    )

    # Of the same-titled works, the one whose composer matches the artist wins
    assert info.composer == "Ludwig van Beethoven"
    assert info.composer_match == "work"
    search = next(r for r in requests if r.url.path == "/ws/2/work")
    assert search.url.params["query"] == 'work:"Symphony No. 5"'


def test_wikidata_search_is_the_last_resort():
    routes = {
        f"{MB}/work": {"works": []},
        WIKIDATA: _wikidata(composer_search=load("wikidata_composer_search.json")),
    }
    client, _ = mock_client(routes)
    info = MusicLookup(client).lookup_composer(
        isrc=ISRC, title="Symphony No. 9", artist="Ludwig van Beethoven"
    )

    assert info.composer == "Ludwig van Beethoven"
    assert info.composer_death_year == 1827
    assert info.composer_match == "search"
    # The search only finds the composer, so other authors may be missing: no
    # public-domain verdict
    assert info.authors_partial is True and info.public_domain is None


def test_death_year_from_wikidata_when_musicbrainz_lacks_it():
    artist = {"type": "Person", "life-span": {"begin": "1932"}}
    routes = {
        **_isrc_chain(artist=artist),
        WIKIDATA: _wikidata(life=load("wikidata_death_only.json")),
    }
    client, requests = mock_client(routes)
    info = MusicLookup(client).lookup_composer(isrc=ISRC)

    assert (info.composer_birth_year, info.composer_death_year) == (1932, 2020)
    assert info.sources == ["musicbrainz", "wikidata"]
    assert info.public_domain is False  # died 2020, protected for 70 years
    wikidata = next(r for r in requests if r.url.host == "query.wikidata.org")
    assert f'wdt:P434 "{BEETHOVEN_ID}"' in wikidata.url.params["query"]


@pytest.mark.parametrize(
    "answer", [404, httpx.ConnectError("down")], ids=["not-found", "network-down"]
)
def test_nothing_found_gives_none(answer):
    routes = {f"{MB}/isrc/{ISRC}": answer, f"{MB}/work": answer, WIKIDATA: answer}
    client, _ = mock_client(routes)
    info = MusicLookup(client).lookup_composer(
        isrc=ISRC, title="Untitled", artist="Nobody"
    )
    assert info is None
