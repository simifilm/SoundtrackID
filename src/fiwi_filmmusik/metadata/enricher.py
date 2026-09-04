"""Pure orchestrator: enrich a results dict with film + composer metadata."""

from __future__ import annotations

import re
from typing import Any, Callable, Literal

import httpx

Scope = Literal["film", "music", "all"]

from fiwi_filmmusik.metadata.film import FilmInfo, FilmLookup
from fiwi_filmmusik.metadata.music import MusicLookup


class EnrichmentCancelled(Exception):
    """Raised by progress_cb to abort enrichment mid-run."""


ProgressCb = Callable[..., None]


def enrich(
    results: dict[str, Any],
    film_hint: dict[str, Any] | None,
    http_client: httpx.Client,
    tmdb_bearer_token: str | None = None,
    tmdb_api_key: str | None = None,
    progress_cb: ProgressCb | None = None,
    scope: Scope = "all",
    music_isrc_only: bool = False,
) -> dict[str, Any]:
    """Enrich a results.json dict in place and return it.

    `scope` controls which subsystems run:
      - "film":  only film-level lookup (TMDb → Wikidata → imdbapi.dev)
      - "music": only per-cue composer lookup (MusicBrainz → Wikidata)
      - "all":   both, in that order

    `film_hint` carries pre-fill data from the modal (imdb_id, tmdb_id, title, year).
    Ignored when scope == "music".

    progress_cb signature: progress_cb(step: str, **kwargs). Steps emitted depend on scope.
    """
    cb = progress_cb or (lambda *_a, **_k: None)

    if scope in ("film", "all"):
        film_lookup = FilmLookup(
            http_client,
            tmdb_bearer_token=tmdb_bearer_token,
            tmdb_api_key=tmdb_api_key,
        )
        existing_film = results.get("film") or {}
        hint = film_hint or {}
        imdb_id = hint.get("imdb_id") or existing_film.get("imdb_id")
        tmdb_id = hint.get("tmdb_id") or existing_film.get("tmdb_id")
        title = hint.get("title") or existing_film.get("title")
        year = hint.get("year") or existing_film.get("year")

        cb("film_start")
        film_info = film_lookup.lookup(
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            title=title,
            year=year,
        )
        film_dict = film_info.to_dict()
        results["film"] = film_dict
        cb("film_done", film=film_dict)

    if scope in ("music", "all"):
        cues = results.get("cues") or []
        predicate = _has_isrc if music_isrc_only else _has_identification
        identified = [c for c in cues if predicate(c)]

        # Group by track identity so each unique piece is enriched once. A cue that
        # recurs later in the film (same title+artist) reuses the first lookup
        # instead of re-querying MusicBrainz/Wikidata.
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        order: list[tuple[str, str]] = []
        for cue in identified:
            key = _identity_key(cue)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(cue)

        total = len(order)
        cb("music_start", total=total)

        music_lookup = MusicLookup(http_client)
        for index, key in enumerate(order, start=1):
            group = groups[key]
            enrichment = _enrich_cue(group[0], music_lookup)
            enrichment_dict = enrichment.to_dict() if enrichment else None
            for cue in group:
                cue["enrichment"] = enrichment_dict
            cb(
                "music_cue",
                index=index,
                total=total,
                segment_id=group[0].get("segment_id"),
                enrichment=enrichment_dict,
            )

        cb("music_done")

    return results


def _identity_key(cue: dict[str, Any]) -> tuple[str, str]:
    """Normalized (title, artist) — cues that share it are the same piece."""
    def norm(s: Any) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()
    return (norm(cue.get("title")), norm(cue.get("artist")))


def _has_identification(cue: dict[str, Any]) -> bool:
    return bool(cue.get("title") or cue.get("isrc"))


def _has_isrc(cue: dict[str, Any]) -> bool:
    return bool(cue.get("isrc"))


def _enrich_cue(cue: dict[str, Any], music_lookup: MusicLookup):
    return music_lookup.lookup_composer(
        isrc=cue.get("isrc"),
        title=cue.get("title"),
        artist=cue.get("artist"),
    )


def merge_film_hint_into_results(results: dict[str, Any], hint: dict[str, Any]) -> None:
    """Pre-fill `results["film"]` from a manual-entry hint without running APIs."""
    existing = results.get("film") or {}
    info = FilmInfo(
        title=hint.get("title") or existing.get("title"),
        year=hint.get("year") or existing.get("year"),
        imdb_id=hint.get("imdb_id") or existing.get("imdb_id"),
        tmdb_id=hint.get("tmdb_id") or existing.get("tmdb_id"),
        director=existing.get("director"),
        director_death_year=existing.get("director_death_year"),
        sources=list(existing.get("sources") or []),
    )
    results["film"] = info.to_dict()
