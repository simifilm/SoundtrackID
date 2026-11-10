"""Tests for the ACRCloud and AudD clients, against recorded API responses.

The fixtures were recorded from the live APIs with a 15 s segment of
"It's De-Lovely" (match), white noise (no match), silence, and a wrong
credential. Responses that can't be recorded without spending the quota
(limit reached) are built from the documented format.
"""

import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from scipy.io import wavfile

from soundtrackID.detection import (
    ACRCloudDetectionClient,
    AudDDetectionClient,
    RateLimitError,
    _parse_acr_candidate,
)
from soundtrackID.models import MusicSegment

FIXTURES = Path(__file__).parent / "fixtures"
REAL_HTTPX_CLIENT = httpx.Client


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _segment():
    return MusicSegment(
        audio=np.zeros(3 * 8000, dtype=np.float32),
        start_time=0.0,
        end_time=3.0,
        sample_rate=8000,
    )


def _acr_detect(monkeypatch, response: str, **client_kwargs):
    """Run ACRCloud detection with the SDK replaced by a canned response."""
    calls = []

    class FakeRecognizer:
        def __init__(self, config):
            self.config = config

        def recognize_by_file(self, path, start, duration):
            sr, audio = wavfile.read(path)
            calls.append(
                {
                    "config": self.config,
                    "path": path,
                    "start": start,
                    "duration": duration,
                    "sample_rate": sr,
                    "samples": len(audio),
                }
            )
            return response

    sdk = SimpleNamespace(ACRCloudRecognizer=FakeRecognizer)
    monkeypatch.setitem(sys.modules, "acrcloud.recognizer", sdk)
    client = ACRCloudDetectionClient(
        host="host", access_key="key", access_secret="secret", **client_kwargs
    )
    return client.detect(_segment()), calls


def _audd_detect(monkeypatch, response: dict):
    """Run AudD detection against a mock api.audd.io that returns `response`."""
    requests = []

    def handler(request):
        request.read()
        requests.append(request)
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    return AudDDetectionClient(api_token="token").detect(_segment()), requests


# ── ACRCloud ───────────────────────────────────────────────────────────────


def test_acrcloud_match_is_parsed(monkeypatch):
    r, _ = _acr_detect(monkeypatch, load("acrcloud_match.json"))
    assert r.title == "It's De-Lovely (Album Version)"
    assert r.artist == "Robbie Williams"
    assert r.provider == "acrcloud" and r.confidence == 0.25
    assert r.metadata["isrc"] == "USSM10403979"
    assert r.metadata["album"] == "De-Lovely Music From The Motion Picture"
    assert r.metadata["release_year"] == 2004
    assert r.metadata["genre"] == "Pop"
    assert r.metadata["acrcloud_id"] == "f391f3b88050fd916bbc4e6cfa957528"
    assert r.metadata["youtube_link"] == "https://www.youtube.com/watch?v=IeWqQ4i8dRk"
    assert r.metadata["spotify_url"] is None
    assert [c["score"] for c in r.metadata["candidates"]] == [25.0]


def test_acrcloud_sends_the_segment_as_wav(monkeypatch):
    _, calls = _acr_detect(monkeypatch, load("acrcloud_match.json"))
    [call] = calls
    assert call["config"]["host"] == "host"
    assert call["config"]["access_key"] == "key"
    assert call["config"]["access_secret"] == "secret"
    assert (call["start"], call["duration"]) == (0, 3)
    assert (call["sample_rate"], call["samples"]) == (8000, 3 * 8000)
    assert not os.path.exists(call["path"])


@pytest.mark.parametrize("fixture", ["acrcloud_no_match.json", "acrcloud_mute.json"])
def test_acrcloud_no_match_is_empty(monkeypatch, fixture):
    r, _ = _acr_detect(monkeypatch, load(fixture))
    assert r.title is None and r.confidence == 0.0 and r.provider == "acrcloud"


@pytest.mark.parametrize(
    "response, code",
    [
        (load("acrcloud_invalid_signature.json"), 3014),
        (json.dumps({"status": {"msg": "Limit exceeded", "code": 3003}}), 3003),
    ],
    ids=["invalid-signature", "limit-exceeded"],
)
def test_acrcloud_auth_and_quota_errors_stop_the_run(monkeypatch, response, code):
    with pytest.raises(RateLimitError, match=f"ACRCloud error {code}"):
        _acr_detect(monkeypatch, response)


def test_acrcloud_match_below_min_score_is_empty(monkeypatch):
    # The recorded match scores 25
    r, _ = _acr_detect(monkeypatch, load("acrcloud_match.json"), min_score=26)
    assert r.title is None and r.confidence == 0.0


def test_acrcloud_keeps_the_top_five_candidates(monkeypatch):
    payload = json.loads(load("acrcloud_match.json"))
    candidate = payload["metadata"]["music"][0]
    payload["metadata"]["music"] = [
        {**candidate, "title": f"Track {i}", "score": 90 - 10 * i} for i in range(6)
    ]
    r, _ = _acr_detect(monkeypatch, json.dumps(payload))
    assert r.title == "Track 0" and r.confidence == 0.9
    assert [c["title"] for c in r.metadata["candidates"]] == [
        f"Track {i}" for i in range(5)
    ]


def test_acrcloud_candidate_spotify_and_musicbrainz_links():
    # Shaped like ACRCloud's documented external_metadata; the recorded match
    # carries neither.
    candidate = {
        "title": "T",
        "artists": [{"name": "A"}, {"name": "B"}],
        "album": {"name": "Album", "release_date": "1999-01-01"},
        "external_metadata": {
            "spotify": {"track": {"id": "sp1"}},
            "musicbrainz": [{"track": {"id": "mb1"}}],
        },
    }
    parsed = _parse_acr_candidate(candidate)
    assert parsed["artist"] == "A, B"
    assert parsed["release_year"] == 1999  # from the album when the track has none
    assert parsed["spotify_url"] == "https://open.spotify.com/track/sp1"
    assert parsed["musicbrainz_recording_id"] == "mb1"
    assert parsed["musicbrainz_url"] == "https://musicbrainz.org/recording/mb1"


def test_acrcloud_missing_credentials_raise(monkeypatch):
    for var in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_ACCESS_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="ACRCloud credentials missing"):
        ACRCloudDetectionClient()


# ── AudD ───────────────────────────────────────────────────────────────────


def test_audd_match_is_parsed(monkeypatch):
    r, _ = _audd_detect(monkeypatch, json.loads(load("audd_match.json")))
    assert r.title == "It's De-Lovely (Album Version)"
    assert r.artist == "Robbie Williams"
    assert r.provider == "audd" and r.confidence == 1.0
    assert r.metadata["album"] == "De-Lovely Music From The Motion Picture"
    assert r.metadata["release_year"] == 2004
    assert r.metadata["song_link"] == "https://lis.tn/qvpMy"
    mb_id = "dffa2836-6df8-4dbd-bbd7-c2632d89dde6"
    assert r.metadata["musicbrainz_recording_id"] == mb_id
    assert r.metadata["musicbrainz_url"] == f"https://musicbrainz.org/recording/{mb_id}"
    assert r.metadata["spotify_url"] is None
    assert r.metadata["apple_music_url"] is None


def test_audd_isrc_falls_back_to_musicbrainz(monkeypatch):
    # The recorded match has no Apple Music / Spotify block, only MusicBrainz
    r, _ = _audd_detect(monkeypatch, json.loads(load("audd_match.json")))
    assert r.metadata["isrc"] == "USSM10403979"


def test_audd_isrc_and_links_from_apple_music_and_spotify(monkeypatch):
    # Shaped like AudD's documented apple_music / spotify blocks; the recorded
    # match carries neither.
    payload = json.loads(load("audd_match.json"))
    payload["result"]["apple_music"] = {
        "isrc": "APPLE0000001",
        "url": "https://music.apple.com/x",
    }
    payload["result"]["spotify"] = {
        "external_ids": {"isrc": "SPOTI0000001"},
        "external_urls": {"spotify": "https://open.spotify.com/track/x"},
    }
    r, _ = _audd_detect(monkeypatch, payload)
    assert r.metadata["isrc"] == "APPLE0000001"
    assert r.metadata["apple_music_url"] == "https://music.apple.com/x"
    assert r.metadata["spotify_url"] == "https://open.spotify.com/track/x"

    spotify_only = copy.deepcopy(payload)
    del spotify_only["result"]["apple_music"]
    r, _ = _audd_detect(monkeypatch, spotify_only)
    assert r.metadata["isrc"] == "SPOTI0000001"


def test_audd_sends_token_and_wav(monkeypatch):
    _, [request] = _audd_detect(monkeypatch, json.loads(load("audd_match.json")))
    assert request.method == "POST"
    assert str(request.url) == "https://api.audd.io/"
    body = request.content
    assert b'name="api_token"\r\n\r\ntoken\r\n' in body
    assert b'name="return"\r\n\r\napple_music,spotify,musicbrainz\r\n' in body
    assert b'name="file"' in body and b"RIFF" in body


@pytest.mark.parametrize(
    "fixture", ["audd_no_match.json", "audd_fingerprint_error.json"]
)
def test_audd_no_match_is_empty(monkeypatch, fixture):
    r, _ = _audd_detect(monkeypatch, json.loads(load(fixture)))
    assert r.title is None and r.confidence == 0.0 and r.provider == "audd"


@pytest.mark.parametrize(
    "response, code",
    [
        (json.loads(load("audd_invalid_token.json")), 900),
        (
            {
                "status": "error",
                "error": {"error_code": 901, "error_message": "limit reached"},
            },
            901,
        ),
    ],
    ids=["invalid-token", "limit-reached"],
)
def test_audd_token_and_limit_errors_stop_the_run(monkeypatch, response, code):
    with pytest.raises(RateLimitError, match=f"AudD error {code}"):
        _audd_detect(monkeypatch, response)


def test_audd_missing_token_raises(monkeypatch):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="AudD API token missing"):
        AudDDetectionClient()
