"""Tests for temporal re-ranking of identification candidates."""

from soundtrackID.temporal import temporal_rerank


def _cue(primary_year, alts):
    """Build a cue whose top candidate has primary_year plus given alternatives
    (each a (release_year, score) tuple)."""
    candidates = [{"title": "Top", "artist": "X", "release_year": primary_year, "score": 90.0}]
    for i, (yr, score) in enumerate(alts):
        candidates.append({
            "title": f"Alt{i}", "artist": "Y", "release_year": yr, "score": score,
            "isrc": f"ALT{i}", "album": "A",
        })
    return {
        "segment_id": "s1", "title": "Top", "artist": "X",
        "release_year": primary_year, "candidates": candidates,
    }


def test_swaps_to_period_plausible_alternative():
    # 1971 film, top hit is 2022, alternative is 1817 with a decent score.
    results = {"cues": [_cue(2022, [(1817, 70.0)])]}
    n = temporal_rerank(results, film_year=1971)
    assert n == 1
    cue = results["cues"][0]
    assert cue["title"] == "Alt0"
    assert cue["release_year"] == 1817
    assert cue["temporal_pick"] is True


def test_no_swap_when_primary_is_plausible():
    # Top hit predates the film: leave it alone even if an older alt exists.
    results = {"cues": [_cue(1965, [(1817, 90.0)])]}
    assert temporal_rerank(results, film_year=1971) == 0
    assert results["cues"][0]["title"] == "Top"


def test_no_swap_when_alternative_too_weak():
    # Only anachronistic alternatives, or the plausible one scores too low.
    results = {"cues": [_cue(2022, [(1817, 10.0)])]}
    assert temporal_rerank(results, film_year=1971) == 0
    assert results["cues"][0]["title"] == "Top"


def test_no_swap_without_film_year():
    results = {"cues": [_cue(2022, [(1817, 90.0)])]}
    assert temporal_rerank(results, film_year=None) == 0


def test_picks_highest_scoring_plausible_alt():
    results = {"cues": [_cue(2022, [(1900, 55.0), (1850, 80.0)])]}
    temporal_rerank(results, film_year=1971)
    assert results["cues"][0]["release_year"] == 1850  # higher score wins


def test_tolerance_allows_slightly_later_reissue():
    # Alt at film_year + 3 is within tolerance; +4 is not.
    assert temporal_rerank({"cues": [_cue(2022, [(1974, 70.0)])]}, 1971) == 1
    assert temporal_rerank({"cues": [_cue(2022, [(1975, 70.0)])]}, 1971) == 0
