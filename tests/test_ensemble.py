"""Tests for the multi-provider ensemble (majority voting)."""

import numpy as np

from fiwi_filmmusik.detection import (
    BaseMusicDetectionClient,
    EnsembleDetectionClient,
    _consensus_from_votes,
    _normalize_title,
    build_detection_client,
)
from fiwi_filmmusik.models import DetectionResult, MusicSegment


def _vote(provider, title, isrc=None, conf=1.0, matched=True, meta=None):
    m = dict(meta or {})
    if isrc:
        m["isrc"] = isrc
    return {
        "provider": provider,
        "title": title,
        "artist": "Hans Zimmer",
        "isrc": isrc,
        "confidence": conf,
        "matched": matched,
        "metadata": m,
    }


# ── normalization ─────────────────────────────────────────────────────────


def test_normalize_strips_parenthetical_and_remaster():
    assert _normalize_title("Cornfield Chase") == "cornfield chase"
    assert _normalize_title('Cornfield Chase (From "Interstellar")') == "cornfield chase"
    assert _normalize_title("No Time To Die - Remastered 2021") == "no time to die"


# ── pure voting core ──────────────────────────────────────────────────────


def test_unanimous_three_of_three():
    title, _, conf, _, ag = _consensus_from_votes(
        [
            _vote("acrcloud", "Cornfield Chase"),
            _vote("shazam", 'Cornfield Chase (From "Interstellar")'),
            _vote("audd", "Cornfield Chase"),
        ],
        total=3,
    )
    assert title == "Cornfield Chase"
    assert ag == {"count": 3, "total": 3, "providers": ["acrcloud", "shazam", "audd"]}
    assert conf == 1.0


def test_majority_two_of_three():
    title, _, _, _, ag = _consensus_from_votes(
        [
            _vote("acrcloud", "Cornfield Chase"),
            _vote("shazam", "Cornfield Chase"),
            _vote("audd", "Other Song"),
        ],
        total=3,
    )
    assert title == "Cornfield Chase"
    assert ag["count"] == 2


def test_agreement_by_shared_isrc_across_different_titles():
    _, _, _, _, ag = _consensus_from_votes(
        [
            _vote("acrcloud", "Cornfield Chase", isrc="USABC1234567"),
            _vote("audd", "Cornfield Chase - Remastered", isrc="USABC1234567"),
        ],
        total=3,
    )
    assert ag["count"] == 2
    assert ag["providers"] == ["acrcloud", "audd"]


def test_metadata_merge_fills_gaps_by_priority():
    _, _, _, merged, _ = _consensus_from_votes(
        [
            _vote("acrcloud", "Cornfield Chase", meta={"spotify_url": "s"}),
            _vote("audd", "Cornfield Chase", isrc="USX", meta={"isrc": "USX"}),
        ],
        total=3,
    )
    assert merged["isrc"] == "USX"        # only audd had it
    assert merged["spotify_url"] == "s"   # only acrcloud had it


def test_single_provider_one_of_three():
    title, _, conf, _, ag = _consensus_from_votes(
        [
            _vote("acrcloud", None, matched=False),
            _vote("shazam", None, matched=False),
            _vote("audd", "Abyss"),
        ],
        total=3,
    )
    assert title == "Abyss"
    assert ag["count"] == 1
    assert round(conf, 2) == 0.33


def test_no_match_anywhere():
    title, _, conf, _, ag = _consensus_from_votes(
        [_vote("acrcloud", None, matched=False)], total=3
    )
    assert title is None
    assert conf == 0.0
    assert ag == {"count": 0, "total": 3, "providers": []}


def test_all_disagree_falls_back_to_priority():
    title, _, _, _, ag = _consensus_from_votes(
        [_vote("acrcloud", "A"), _vote("shazam", "B"), _vote("audd", "C")],
        total=3,
    )
    assert title == "A"  # highest-priority provider wins the 1-1-1 tie
    assert ag["count"] == 1


def test_disagreement_ignores_incomparable_confidence():
    # AudD reports a constant 1.0; ACRCloud a real score. On a disagreement the
    # higher-priority provider must win, not whoever reports the bigger number.
    title, _, _, _, ag = _consensus_from_votes(
        [
            _vote("acrcloud", "Cornfield Chase", conf=0.7),
            _vote("audd", "Murphys Law", conf=1.0),
        ],
        total=3,
    )
    assert title == "Cornfield Chase"
    assert ag["providers"] == ["acrcloud"]


# ── EnsembleDetectionClient.detect() ──────────────────────────────────────


class _FakeClient(BaseMusicDetectionClient):
    def __init__(self, title, *, isrc=None, raises=False):
        self._title = title
        self._isrc = isrc
        self._raises = raises

    def detect(self, segment):
        if self._raises:
            raise RuntimeError("provider quota exceeded")
        meta = {"isrc": self._isrc} if self._isrc else {}
        return DetectionResult(
            segment=segment,
            title=self._title,
            artist="Hans Zimmer" if self._title else None,
            confidence=1.0 if self._title else 0.0,
            metadata=meta,
        )


def _segment():
    return MusicSegment(
        audio=np.zeros(1000, dtype=np.float32), start_time=0.0, end_time=1.0, sample_rate=1000
    )


def test_ensemble_detect_builds_consensus_and_votes():
    ens = EnsembleDetectionClient(
        [
            ("acrcloud", _FakeClient("Cornfield Chase")),
            ("shazam", _FakeClient(None)),
            ("audd", _FakeClient("Cornfield Chase")),
        ]
    )
    res = ens.detect(_segment())
    assert res.provider == "ensemble"
    assert res.title == "Cornfield Chase"
    assert res.metadata["agreement"] == {
        "count": 2,
        "total": 3,
        "providers": ["acrcloud", "audd"],
    }
    votes = {v["provider"]: v["matched"] for v in res.metadata["votes"]}
    assert votes == {"acrcloud": True, "shazam": False, "audd": True}


def test_ensemble_survives_a_raising_provider():
    ens = EnsembleDetectionClient(
        [
            ("acrcloud", _FakeClient("Abyss")),
            ("audd", _FakeClient(None, raises=True)),
        ]
    )
    res = ens.detect(_segment())
    assert res.title == "Abyss"
    audd_vote = next(v for v in res.metadata["votes"] if v["provider"] == "audd")
    assert audd_vote["matched"] is False
    assert "quota" in (audd_vote["error"] or "")


# ── factory ───────────────────────────────────────────────────────────────


def test_build_single_returns_plain_client(monkeypatch):
    # acrcloud construction needs creds; stub it out.
    import fiwi_filmmusik.detection as det

    monkeypatch.setattr(det, "_build_single_client", lambda name: _FakeClient(name))
    client = build_detection_client("audd")
    assert isinstance(client, _FakeClient)


def test_build_list_returns_ensemble_in_priority_order(monkeypatch):
    import fiwi_filmmusik.detection as det

    monkeypatch.setattr(det, "_build_single_client", lambda name: _FakeClient(name))
    client = build_detection_client("audd,acrcloud")
    assert isinstance(client, EnsembleDetectionClient)
    assert [n for n, _ in client._clients] == ["acrcloud", "audd"]  # reordered by priority
