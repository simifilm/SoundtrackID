import json
from pathlib import Path

from fiwi_filmmusik.metadata.music import (
    _artist_death,
    _extract_authors,
    _normalize_name,
    _parse_first_death_year,
    _parse_wikidata_composer,
    _parse_year,
    _pd_status,
    _score_work_authors,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_extract_authors_present():
    work = load("mb_work_with_composer.json")
    authors = _extract_authors(work)
    assert authors == [("composer", "1f9df192-a621-4f54-8850-2c5373b7eac9", "Ludwig van Beethoven")]


def test_extract_authors_multiple_roles():
    work = {"relations": [
        {"type": "composer", "artist": {"id": "c1", "name": "Erno Rapee"}},
        {"type": "lyricist", "artist": {"id": "l1", "name": "Lew Pollack"}},
        {"type": "performer", "artist": {"id": "p1", "name": "An Orchestra"}},  # excluded
        {"type": "writer", "artist": {"id": "w1", "name": "Someone"}},
    ]}
    authors = _extract_authors(work)
    assert ("composer", "c1", "Erno Rapee") in authors
    assert ("lyricist", "l1", "Lew Pollack") in authors
    assert ("writer", "w1", "Someone") in authors
    assert all(role != "performer" for role, _, _ in authors)


def test_extract_authors_no_relations():
    assert _extract_authors({}) == []


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


# ── Public-domain assessment (multi-role) ─────────────────────────────────


def _author(name, roles, death):
    return {"name": name, "roles": roles, "death_year": death}


def test_pd_all_authors_long_dead_is_public_domain():
    authors = [_author("Rapee", ["composer"], 1945), _author("Pollack", ["lyricist"], 1946)]
    latest, complete, pd = _pd_status(authors, current_year=2026)
    assert (latest, complete, pd) == (1946, True, True)


def test_pd_one_recent_author_not_public_domain():
    # La Cumparsita: composer d.1948 but a lyricist d.1957 keeps it protected.
    authors = [
        _author("Matos Rodríguez", ["composer", "lyricist"], 1948),
        _author("Maroni", ["lyricist"], 1957),
    ]
    latest, complete, pd = _pd_status(authors, current_year=2026)
    assert latest == 1957 and complete is True and pd is False


def test_pd_unknown_death_is_indeterminate():
    authors = [_author("A", ["composer"], 1945), _author("B", ["lyricist"], None)]
    latest, complete, pd = _pd_status(authors, current_year=2026)
    assert complete is False and pd is None


def test_pd_no_authors():
    assert _pd_status([], 2026) == (None, False, None)


def test_pd_boundary_exactly_70_years_not_yet_public_domain():
    _, _, pd = _pd_status([_author("A", ["composer"], 1956)], current_year=2026)
    assert pd is False  # needs strictly more than 70 years


# ── Work-candidate scoring / name matching ────────────────────────────────


def test_normalize_name_strips_accents():
    assert _normalize_name("Ernö Rapée") == "erno rapee"


def test_score_matches_surname():
    authors = [_author("Ernö Rapée", ["composer"], 1945), _author("Lew Pollack", ["lyricist"], 1946)]
    assert _score_work_authors(authors, "Franz Waxman, Erno Rapee & Lew Pollack") == 2


def test_score_tolerates_misspelled_compound_surname():
    # ACRCloud wrote "Rodriquez" (q); a distinctive token ("Matos") still matches.
    authors = [_author("Gerardo Matos Rodríguez", ["composer"], 1948)]
    assert _score_work_authors(authors, "Franz Waxman & Matos Rodriquez") == 1


def test_score_zero_without_artist():
    authors = [_author("Someone", ["composer"], 1900)]
    assert _score_work_authors(authors, None) == 0
