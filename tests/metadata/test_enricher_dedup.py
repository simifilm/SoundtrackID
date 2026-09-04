"""Enrichment dedupes per unique track identity (one API lookup per piece)."""

from fiwi_filmmusik.metadata import enricher
from fiwi_filmmusik.metadata.music import ComposerInfo


class _CountingLookup:
    """Stand-in for MusicLookup that records how often it is queried."""

    def __init__(self, *_a, **_k):
        self.calls = []

    def lookup_composer(self, isrc=None, title=None, artist=None):
        self.calls.append((title, artist))
        return ComposerInfo(composer=f"composer-of-{title}", composer_match="isrc")


def test_recurring_track_enriched_once(monkeypatch):
    counter = _CountingLookup()
    monkeypatch.setattr(enricher, "MusicLookup", lambda *_a, **_k: counter)

    results = {"cues": [
        {"segment_id": "seg_001", "title": "Diane", "artist": "Erno Rapee", "isrc": "X1"},
        {"segment_id": "seg_002", "title": "Diane", "artist": "Erno Rapee", "isrc": "X1"},
        {"segment_id": "seg_003", "title": "La Cumparsita", "artist": "Matos", "isrc": "Y1"},
        {"segment_id": "seg_004", "title": "Diane", "artist": "Erno Rapee", "isrc": "X1"},
    ]}

    enricher.enrich(results=results, film_hint=None, http_client=None, scope="music")

    # One lookup per unique (title, artist), not per cue.
    assert len(counter.calls) == 2
    assert {c[0] for c in counter.calls} == {"Diane", "La Cumparsita"}

    # Every Diane cue carries the same enrichment; La Cumparsita its own.
    cues = results["cues"]
    diane = [c["enrichment"] for c in cues if c["title"] == "Diane"]
    assert all(e["composer"] == "composer-of-Diane" for e in diane)
    assert cues[2]["enrichment"]["composer"] == "composer-of-La Cumparsita"


def test_identity_key_normalizes_case_and_punctuation():
    a = enricher._identity_key({"title": "La Cumparsita", "artist": "Matos Rodríguez"})
    b = enricher._identity_key({"title": "la cumparsita", "artist": "matos rodríguez"})
    assert a == b


def test_progress_counts_unique_tracks(monkeypatch):
    counter = _CountingLookup()
    monkeypatch.setattr(enricher, "MusicLookup", lambda *_a, **_k: counter)
    events = []
    results = {"cues": [
        {"segment_id": "s1", "title": "Diane", "artist": "R", "isrc": "X"},
        {"segment_id": "s2", "title": "Diane", "artist": "R", "isrc": "X"},
    ]}
    enricher.enrich(
        results=results, film_hint=None, http_client=None, scope="music",
        progress_cb=lambda step, **kw: events.append((step, kw.get("total"))),
    )
    starts = [t for step, t in events if step == "music_start"]
    assert starts == [1]  # one unique track, not two cues
