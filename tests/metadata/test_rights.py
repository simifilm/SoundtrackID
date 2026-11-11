from soundtrackID.metadata.rights import _pd_status, _presumption_applies, verdict

# ── Public-domain assessment (multi-role) ─────────────────────────────────


def _author(name, roles, death):
    return {"name": name, "roles": roles, "death_year": death}


def test_pd_all_authors_long_dead_is_public_domain():
    authors = [
        _author("Rapee", ["composer"], 1945),
        _author("Pollack", ["lyricist"], 1946),
    ]
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
    return {
        "name": "X",
        "roles": ["composer"],
        "birth_year": birth,
        "death_year": death,
    }


def test_presumption_applies_born_more_than_170_years_ago():
    assert _presumption_applies([_life(1855, None)], current_year=2026) is True


def test_presumption_boundary_exactly_170_years_not_applied():
    assert _presumption_applies([_life(1856, None)], current_year=2026) is False


def test_presumption_not_applicable_when_all_deaths_known():
    assert _presumption_applies([_life(1800, 1850)], current_year=2026) is None


def test_presumption_needs_birth_year():
    assert _presumption_applies([_life(None, None)], current_year=2026) is None


def test_presumption_latest_birth_decides():
    assert (
        _presumption_applies([_life(1800, None), _life(1900, None)], current_year=2026)
        is False
    )


def test_presumption_with_long_dead_coauthor():
    assert (
        _presumption_applies([_life(1850, 1940), _life(1850, None)], current_year=2026)
        is True
    )


def test_presumption_blocked_by_recently_dead_coauthor():
    # A co-author who died in 1980 keeps the work protected either way.
    assert (
        _presumption_applies([_life(1900, 1980), _life(1850, None)], current_year=2026)
        is None
    )


# ── The verdict as a whole ────────────────────────────────────────────────


def test_verdict_of_a_whole_author_set():
    authors = [_life(1891, 1945), _life(1890, 1946)]

    assert verdict(authors, partial=False, year=2026) == {
        "latest_author_death_year": 1946,
        "authors_complete": True,
        "public_domain": True,
        "pd_presumption": None,
    }


def test_a_partial_author_set_never_concludes_towards_public_domain():
    """An author not found could only push the expiry later."""
    long_dead = verdict([_life(1800, 1850)], partial=True, year=2026)
    presumed = verdict([_life(1800, None)], partial=True, year=2026)

    assert long_dead["public_domain"] is None
    assert presumed["pd_presumption"] is None


def test_the_verdict_changes_with_the_year():
    """The term runs to the end of the 70th year after the last death."""
    authors = [_life(1900, 1956)]

    assert verdict(authors, partial=False, year=2026)["public_domain"] is False
    assert verdict(authors, partial=False, year=2027)["public_domain"] is True
