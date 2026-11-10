"""Pure orchestrator: enrich a results dict with composer metadata."""

from __future__ import annotations

import re
from typing import Any, Callable

import httpx

from soundtrackID.metadata.music import MusicLookup

ProgressCb = Callable[..., None]


def enrich(
    results: dict[str, Any],
    http_client: httpx.Client,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Enrich a results.json dict in place and return it.

    Looks up the composer of every cue that carries an ISRC (MusicBrainz →
    Wikidata).

    progress_cb signature: progress_cb(step: str, **kwargs). Steps emitted:
    music_start, music_cue (once per unique track), music_done.
    """
    cb = progress_cb or (lambda *_a, **_k: None)

    cues = results.get("cues") or []
    identified = [c for c in cues if _has_isrc(c)]

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


def _has_isrc(cue: dict[str, Any]) -> bool:
    return bool(cue.get("isrc"))


def _enrich_cue(cue: dict[str, Any], music_lookup: MusicLookup):
    return music_lookup.lookup_composer(
        isrc=cue.get("isrc"),
        title=cue.get("title"),
        artist=cue.get("artist"),
    )
