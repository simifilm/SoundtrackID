"""What follows from a run's results, worked out again whenever they are read or
saved (runs.Run), so it follows the year, the film and every hand edit, and the
app, its exports and the CLI all see the same:

- each cue's rights verdict (metadata.rights), which changes with the year;
- whether a cue's music came out well after the film (anachronistic);
- which piece a cue is (track_key): cues with the same key are one track.
"""

import re
from typing import Any

from soundtrackID.metadata import rights
from soundtrackID.temporal import TOLERANCE


def refresh(results: dict[str, Any]) -> dict[str, Any]:
    """Work out, in place, what follows from `results`; return them."""
    year = rights.current_year()
    film = results.get("film") or {}
    for cue in results.get("cues") or []:
        enrichment = cue.get("enrichment")
        if isinstance(enrichment, dict) and "authors" in enrichment:
            # An enrichment saved without its authors keeps its verdict, with
            # nothing to assess it from.
            partial = bool(enrichment.get("authors_partial"))
            enrichment.update(rights.verdict(enrichment["authors"], partial, year))
        cue["anachronistic"] = is_anachronistic(cue, film)
        cue["track_key"] = track_key(cue)
    return results


def is_anachronistic(cue: dict[str, Any], film: dict[str, Any] | None) -> bool:
    """Whether a cue's music came out more than TOLERANCE years after the film,
    so it can't be original to it: likely a later work using the score, or one
    the provider matched wrongly. False without the film's year."""
    film = film or {}
    film_year = _int(film.get("year"))
    if not film_year:
        return False
    enrichment = cue.get("enrichment") or {}
    # The work's own (publication or composition) year, from Wikidata, decides
    # when it is known.
    original_year = _int(enrichment.get("original_year"))
    if original_year:
        return original_year > film_year + TOLERANCE
    # Else the provider's release year, often a reissue's, so only a hint, with
    # two safeguards against wrongly marking the film's own music.
    release_year = _int(cue.get("release_year"))
    if not (release_year and release_year > film_year + TOLERANCE):
        return False
    # Its composer died before the film: the work predates it.
    death_year = _int(enrichment.get("composer_death_year"))
    if death_year and death_year <= film_year:
        return False
    # By the film's own credited composer: it is the score.
    film_composer = (film.get("music_composer") or "").lower()
    names = [n.strip() for n in re.split(r",|&|\band\b", film_composer) if n.strip()]
    credited = f"{cue.get('artist') or ''} {enrichment.get('composer') or ''}".lower()
    return not any(name in credited for name in names)


def track_key(cue: dict[str, Any]) -> str | None:
    """Which piece a cue is: its title and artist without case and punctuation.
    Cues with the same key are one track (the app's Tracks view, one composer
    lookup); None for a cue nothing identified."""
    if not cue.get("title"):
        return None
    # Punctuation is gone from both, so the separator can't appear in them.
    return f"{_normalize(cue.get('title'))}|{_normalize(cue.get('artist'))}"


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _int(value: Any) -> int | None:
    """A year, also when a hand edit saved it as text; None for anything else."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
