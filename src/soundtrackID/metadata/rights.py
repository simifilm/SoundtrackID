"""Whether a work is in the public domain, from its authors' life years.

The verdict depends on the year it is made in: a work whose last author died in
1956 is protected in 2026 and in the public domain from 2027. So it is made
again whenever a run is read (models.load_results), not kept from the lookup.
"""

from datetime import date
from typing import Any


def current_year() -> int:
    return date.today().year


def verdict(authors: list[dict[str, Any]], partial: bool, year: int) -> dict[str, Any]:
    """A work's rights verdict as of `year`, from its `authors` (each with a
    birth_year and death_year); `partial`: the author set may be incomplete.
    The keys are those of a cue's enrichment."""
    latest, complete, public_domain = _pd_status(authors, year, partial=partial)
    return {
        "latest_author_death_year": latest,
        "authors_complete": complete,
        "public_domain": public_domain,
        # The presumption rule concludes *towards* PD, so it needs the same whole
        # author set the positive verdict does.
        "pd_presumption": None if partial else _presumption_applies(authors, year),
    }


def _pd_status(
    authors: list[dict[str, Any]],
    current_year: int,
    term: int = 70,
    partial: bool = False,
) -> tuple[int | None, bool, bool | None]:
    """Public-domain assessment from a work's authors (PMLE, `term` years post
    mortem auctoris — 70 in the EU/CH/US). Returns
    (latest_author_death_year, authors_complete, public_domain).

    The term runs from the *last surviving* author, so the two verdicts need
    different evidence: an author we haven't found can only push the expiry later,
    never earlier. "Protected" is therefore safe to assert from a known recent
    death alone, while "public domain" additionally needs every author's death
    year (`authors_complete`) and the set itself to be whole (`partial` False).
    None = cannot be asserted."""
    if not authors:
        return None, False, None
    deaths = [a.get("death_year") for a in authors]
    known = [d for d in deaths if d]
    complete = len(known) == len(authors)
    latest = max(known) if known else None
    if latest is not None and (current_year - latest) <= term:
        return latest, complete, False
    if partial or not complete or latest is None:
        return latest, complete, None
    return latest, complete, True


def _presumption_applies(
    authors: list[dict[str, Any]],
    current_year: int,
    term: int = 70,
    max_lifespan: int = 100,
) -> bool | None:
    """Presumption rule for authors without a death year: if the last of them was
    born more than `max_lifespan` + `term` years ago, they're presumed dead for at
    least `term` years. None when not applicable: every death year is known, a
    birth year is missing, or a known author died within `term` years (protected
    either way)."""
    unknown = [a for a in authors if not a.get("death_year")]
    if not unknown or any(not a.get("birth_year") for a in unknown):
        return None
    if any(
        current_year - a["death_year"] <= term for a in authors if a.get("death_year")
    ):
        return None
    latest_birth = max(a["birth_year"] for a in unknown)
    return (current_year - latest_birth) > max_lifespan + term
