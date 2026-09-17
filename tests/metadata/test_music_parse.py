import json
from pathlib import Path

from soundtrackID.metadata.music import (
    MusicLookup,
    _artist_birth,
    _artist_death,
    _extract_authors,
    _normalize_name,
    _parse_life_years,
    _parse_wikidata_composer,
    _parse_year,
    _pd_status,
    _presumption_applies,
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


def test_artist_birth_present():
    artist = load("mb_artist_dead.json")
    assert _artist_birth(artist) == "1770-12-17"


def test_artist_birth_missing():
    assert _artist_birth({"life-span": {}}) is None
    assert _artist_birth({}) is None


def test_parse_wikidata_composer():
    info = _parse_wikidata_composer(load("wikidata_composer_search.json"))
    assert info is not None
    assert info.composer == "Ludwig van Beethoven"
    assert info.composer_birth_year == 1770
    assert info.composer_death_year == 1827
    assert info.work_title == "Symphony No. 9"
    assert info.composer_match == "search"
    assert info.sources == ["wikidata"]


def test_parse_wikidata_composer_empty():
    assert _parse_wikidata_composer({"results": {"bindings": []}}) is None


def test_parse_life_years():
    assert _parse_life_years(load("wikidata_death_only.json")) == (None, 2020)
    both = {"results": {"bindings": [
        {"birth": {"value": "1891-06-04T00:00:00Z"}, "death": {"value": "1945-06-26T00:00:00Z"}},
    ]}}
    assert _parse_life_years(both) == (1891, 1945)
    assert _parse_life_years({"results": {"bindings": []}}) == (None, None)


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


# ── Presumption rule (death year unknown, birth year known) ───────────────


def _life(birth, death):
    return {"name": "X", "roles": ["composer"], "birth_year": birth, "death_year": death}


def test_presumption_applies_born_more_than_170_years_ago():
    assert _presumption_applies([_life(1855, None)], current_year=2026) is True


def test_presumption_boundary_exactly_170_years_not_applied():
    assert _presumption_applies([_life(1856, None)], current_year=2026) is False


def test_presumption_not_applicable_when_all_deaths_known():
    assert _presumption_applies([_life(1800, 1850)], current_year=2026) is None


def test_presumption_needs_birth_year():
    assert _presumption_applies([_life(None, None)], current_year=2026) is None


def test_presumption_latest_birth_decides():
    assert _presumption_applies([_life(1800, None), _life(1900, None)], current_year=2026) is False


def test_presumption_with_long_dead_coauthor():
    assert _presumption_applies([_life(1850, 1940), _life(1850, None)], current_year=2026) is True


def test_presumption_blocked_by_recently_dead_coauthor():
    # A co-author who died in 1980 keeps the work protected either way.
    assert _presumption_applies([_life(1900, 1980), _life(1850, None)], current_year=2026) is None


# ── Birth/death resolution: people only ───────────────────────────────────


def _lookup_with(artists, wikidata=None):
    """MusicLookup with MusicBrainz artist and Wikidata calls stubbed out."""
    lookup = MusicLookup(client=None)
    wd_calls = []

    def wd(aid):
        wd_calls.append(aid)
        return (wikidata or {}).get(aid, (None, None))

    lookup._mb_artist = artists.get
    lookup._wikidata_life_years_by_mbid = wd
    return lookup, wd_calls


def _work(artist_id):
    return {"relations": [{"type": "composer", "artist": {"id": artist_id, "name": "X"}}]}


def _years(authors):
    return authors[0]["birth_year"], authors[0]["death_year"]


def test_person_life_years_from_musicbrainz():
    lookup, wd_calls = _lookup_with({"p": {"type": "Person", "life-span": {"begin": "1770-12-17", "end": "1827-03-26"}}})
    authors, used_wd = lookup._authors_from_work(_work("p"))
    assert _years(authors) == (1770, 1827)
    assert wd_calls == [] and used_wd is False


def test_person_missing_birth_falls_back_to_wikidata():
    lookup, _ = _lookup_with(
        {"p": {"type": "Person", "life-span": {"end": "1945"}}},
        wikidata={"p": (1891, 1950)},
    )
    authors, used_wd = lookup._authors_from_work(_work("p"))
    assert _years(authors) == (1891, 1945)  # MusicBrainz death year wins
    assert used_wd is True


def test_band_gets_no_life_years():
    lookup, wd_calls = _lookup_with({"b": {"type": "Group", "life-span": {"begin": "1970", "end": "1991"}}})
    authors, _ = lookup._authors_from_work(_work("b"))
    assert _years(authors) == (None, None)
    assert wd_calls == []


def test_unknown_type_uses_wikidata_only():
    lookup, _ = _lookup_with(
        {"u": {"life-span": {"begin": "1970", "end": "1991"}}},
        wikidata={"u": (1900, 1960)},
    )
    authors, _ = lookup._authors_from_work(_work("u"))
    assert _years(authors) == (1900, 1960)


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
