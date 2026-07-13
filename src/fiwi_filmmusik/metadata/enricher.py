"""Pure orchestrator: enrich a results dict with film + composer metadata."""

from __future__ import annotations

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
        identified = [c for c in cues if _has_identification(c)]
        total = len(identified)
        cb("music_start", total=total)

        music_lookup = MusicLookup(http_client)
        processed = 0
        for cue in cues:
            if not _has_identification(cue):
                continue
            processed += 1
            enrichment = _enrich_cue(cue, music_lookup)
            cue["enrichment"] = enrichment.to_dict() if enrichment else None
            cb(
                "music_cue",
                index=processed,
                total=total,
                segment_id=cue.get("segment_id"),
                enrichment=cue["enrichment"],
            )

        cb("music_done")

    return results


def _has_identification(cue: dict[str, Any]) -> bool:
    return bool(cue.get("title") or cue.get("isrc"))


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
