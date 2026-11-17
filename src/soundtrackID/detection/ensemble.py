"""Several providers per clip, and the answer most of them agree on."""

import logging
import re

from soundtrackID.detection.base import (
    BaseMusicDetectionClient,
    ProviderAuthError,
    RateLimitError,
)
from soundtrackID.models import DetectionResult, MusicSegment
from soundtrackID.results import MATCH_KEYS

log = logging.getLogger(__name__)


# Provider priority for tie-breaks and canonical title/artist selection, based
# on observed catalog quality for film scores (ACRCloud strongest).
PROVIDER_PRIORITY = ["acrcloud", "shazam", "audd"]

# Metadata keys merged across a winning cluster (first non-null by priority),
# so the consensus keeps the best ISRC + platform links any provider supplied:
# every field a cue takes from an answer, but those the ensemble sets itself.
_MERGE_KEYS = tuple(
    key
    for key in MATCH_KEYS
    if key not in ("provider", "title", "artist", "agreement", "votes")
)


def _normalize_title(s: str | None) -> str:
    """Collapse a track title to a comparison key: drop parentheticals,
    bracketed tags, feat./remaster suffixes, and punctuation."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # (From "Interstellar")
    s = re.sub(r"\[[^\]]*\]", " ", s)  # [Remastered]
    s = re.sub(r"\bfeat\.?.*$", " ", s)  # feat. …
    s = re.sub(r"-\s*remaster(ed)?.*$", " ", s)  # - Remastered 2011
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_artist(s: str | None) -> str:
    """Normalize the primary artist (first credited) for comparison."""
    if not s:
        return ""
    s = s.lower().split(",")[0].split("&")[0].split(" feat")[0]
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_key(vote: dict) -> tuple[str, str] | None:
    t = _normalize_title(vote.get("title"))
    return (t, _normalize_artist(vote.get("artist"))) if t else None


def _isrc_key(vote: dict) -> str | None:
    isrc = (vote.get("isrc") or "").strip().upper()
    return isrc or None


def _cluster_votes(matched: list[dict]) -> list[list[int]]:
    """Greedy-cluster matched votes that agree, where two votes agree when they
    share an ISRC or a normalized title+artist. Votes are processed in the given
    (priority) order; returns clusters as lists of indices into `matched`."""
    clusters: list[dict] = []  # {"isrcs": set, "titles": set, "members": [idx]}
    for i, v in enumerate(matched):
        ik, tk = _isrc_key(v), _title_key(v)
        found = None
        for c in clusters:
            if (ik and ik in c["isrcs"]) or (tk and tk in c["titles"]):
                found = c
                break
        if found is None:
            found = {"isrcs": set(), "titles": set(), "members": []}
            clusters.append(found)
        if ik:
            found["isrcs"].add(ik)
        if tk:
            found["titles"].add(tk)
        found["members"].append(i)
    return [c["members"] for c in clusters]


def _consensus_from_votes(
    votes: list[dict], total: int
) -> tuple[str | None, str | None, dict, dict]:
    """Pure voting core. `votes` is every provider's result in priority order
    (each a dict with provider/title/artist/isrc/matched + metadata).
    Returns (title, artist, merged_metadata, agreement)."""
    matched = [v for v in votes if v.get("matched")]
    if not matched:
        return None, None, {}, {"count": 0, "total": total, "providers": []}

    clusters = _cluster_votes(matched)

    # Winner: most providers, then — since only ACRCloud scores its matches,
    # and AudD and Shazam don't — the cluster containing the highest-priority
    # provider. `matched` is in priority order, so the smallest member index is
    # best.
    winner = max(clusters, key=lambda m: (len(m), -min(m)))
    members = [matched[i] for i in winner]

    rep = members[0]  # highest-priority provider in the cluster → canonical text
    merged: dict = {}
    for key in _MERGE_KEYS:
        for m in members:
            val = (m.get("metadata") or {}).get(key)
            if val is not None:
                merged[key] = val
                break

    agreement = {
        "count": len(members),
        "total": total,
        "providers": [m["provider"] for m in members],
    }
    return rep.get("title"), rep.get("artist"), merged, agreement


class EnsembleDetectionClient(BaseMusicDetectionClient):
    """Run several providers per segment and return a consensus by majority vote.

    Presents as an ordinary detection client, so the pipeline's grouping, merging
    and writing are unchanged. The consensus carries two extra metadata keys:
    ``agreement`` (how many providers concurred) and ``votes`` (each provider's
    raw answer), which the frontend renders as a confidence badge + breakdown.
    """

    def __init__(self, clients: list[tuple[str, BaseMusicDetectionClient]]):
        if not clients:
            raise ValueError("EnsembleDetectionClient needs at least one client")
        self._clients = list(clients)

    def detect(self, segment: MusicSegment) -> DetectionResult:
        from concurrent.futures import ThreadPoolExecutor

        def run(name: str, client: BaseMusicDetectionClient):
            try:
                return name, client.detect(segment), None, False
            except ProviderAuthError:
                # Voting on without it would quietly drop a chosen provider.
                raise
            except RateLimitError as exc:
                return name, None, str(exc), True
            except Exception as exc:  # one provider failing must not sink the run
                log.warning("%s failed", name, exc_info=True)
                return name, None, str(exc), False

        with ThreadPoolExecutor(max_workers=len(self._clients)) as ex:
            raw = [f.result() for f in [ex.submit(run, n, c) for n, c in self._clients]]

        # If no provider answered, nobody listened to this window: propagate, so
        # the pipeline keeps it pending for a resume instead of recording it as
        # "no match". With one rate-limited, the ensemble is throttled and the
        # pipeline stops calling it; when all failed otherwise, it asks on.
        if not any(r is not None for _, r, _, _ in raw):
            errors = "no provider answered: " + "; ".join(e for _, _, e, _ in raw if e)
            if any(rl for _, _, _, rl in raw):
                raise RateLimitError(errors)
            raise RuntimeError(errors)
        raw = [(n, r, e) for n, r, e, _ in raw]

        # Keep providers in the configured priority order.
        order = {n: i for i, (n, _) in enumerate(self._clients)}
        raw.sort(key=lambda t: order.get(t[0], 99))

        votes: list[dict] = []
        for name, result, error in raw:
            matched = bool(result and result.title)
            votes.append(
                {
                    "provider": name,
                    "title": result.title if result else None,
                    "artist": result.artist if result else None,
                    "isrc": (result.metadata.get("isrc") if result else None),
                    "matched": matched,
                    "metadata": (result.metadata if result else {}),
                    "error": error,
                }
            )

        title, artist, merged, agreement = _consensus_from_votes(
            votes, total=len(self._clients)
        )

        public_votes = [
            {
                k: v[k]
                for k in ("provider", "title", "artist", "isrc", "matched", "error")
            }
            for v in votes
        ]

        return DetectionResult(
            segment=segment,
            title=title,
            artist=artist,
            provider="ensemble",
            metadata={**merged, "agreement": agreement, "votes": public_votes},
        )
