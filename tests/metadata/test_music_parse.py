import json
from pathlib import Path

from fiwi_filmmusik.metadata.music import (
    _artist_death,
    _extract_composer,
    _parse_first_death_year,
    _parse_wikidata_composer,
    _parse_year,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_extract_composer_present():
    work = load("mb_work_with_composer.json")
    mbid, name = _extract_composer(work)
    assert name == "Ludwig van Beethoven"
    assert mbid == "1f9df192-a621-4f54-8850-2c5373b7eac9"


def test_extract_composer_missing():
    mbid, name = _extract_composer({"relations": [{"type": "lyricist", "artist": {"name": "X"}}]})
    assert (mbid, name) == (None, None)


def test_extract_composer_no_relations():
    mbid, name = _extract_composer({})
    assert (mbid, name) == (None, None)


def test_artist_death_present():
    artist = load("mb_artist_dead.json")
    assert _artist_death(artist) == "1827-03-26"


def test_artist_death_missing():
    assert _artist_death({"life-span": {}}) is None
    assert _artist_death({}) is None


def test_parse_wikidata_composer():
    info = _parse_wikidata_composer(load("wikidata_composer_search.json"))
    assert info is not None
    assert info.composer == "Ludwig van Beethoven"
    assert info.composer_death_year == 1827
    assert info.work_title == "Symphony No. 9"
    assert info.composer_match == "search"
    assert info.sources == ["wikidata"]


def test_parse_wikidata_composer_empty():
    assert _parse_wikidata_composer({"results": {"bindings": []}}) is None


def test_parse_first_death_year():
    assert _parse_first_death_year(load("wikidata_death_only.json")) == 2020
    assert _parse_first_death_year({"results": {"bindings": []}}) is None


def test_parse_year_variants():
    assert _parse_year("1827-03-26") == 1827
    assert _parse_year(None) is None
    assert _parse_year("not a year") is None
