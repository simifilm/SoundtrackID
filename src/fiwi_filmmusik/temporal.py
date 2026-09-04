"""Temporal re-ranking of identification candidates.

ACRCloud returns several candidates per segment, highest-score first. The top hit
is sometimes a later work that samples or re-uses the film's score (e.g. a 2022
track over a 1971 film). When we know the film year, we can prefer a candidate
whose recording year is period-plausible over an anachronistic top hit, as long as
that alternative is still a confident match.
"""

from __future__ import annotations

from typing import Any

TOLERANCE = 3        # years of slack for reissues / tagging noise
MIN_ALT_SCORE = 40.0  # ACRCloud score is 0-100; require a decent alternative

# Fields copied from a chosen candidate onto the cue (cue-shaped keys).
_APPLY_KEYS = (
    "title", "artist", "album", "release_year", "isrc", "genre",
    "spotify_url", "youtube_link", "musicbrainz_recording_id", "musicbrainz_url",
)


def _apply_candidate(cue: dict[str, Any], cand: dict[str, Any]) -> None:
    for key in _APPLY_KEYS:
        cue[key] = cand.get(key)
    cue["confidence"] = (cand.get("score") or 0.0) / 100.0
    cue["temporal_pick"] = True


def temporal_rerank(results: dict[str, Any], film_year: int | None, tolerance: int = TOLERANCE) -> int:
    """For each cue whose primary match postdates the film, swap in the best
    period-plausible candidate (recording year <= film year + tolerance) that is
    still a confident match. Mutates cues in place; returns how many were changed."""
    if not film_year:
        return 0
    changed = 0
    for cue in results.get("cues") or []:
        cands = cue.get("candidates")
        if not cands or len(cands) < 2:
            continue
        primary_year = cue.get("release_year")
        # Only act when the current pick is itself temporally implausible.
        if not (primary_year and primary_year > film_year + tolerance):
            continue
        alts = [
            c for c in cands[1:]
            if c.get("release_year") and c["release_year"] <= film_year + tolerance
            and (c.get("score") or 0.0) >= MIN_ALT_SCORE
        ]
        if not alts:
            continue
        best = max(alts, key=lambda c: c.get("score") or 0.0)
        _apply_candidate(cue, best)
        changed += 1
    return changed
