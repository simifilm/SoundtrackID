"""Tests for the ShazamKit-based ShazamDetectionClient (native helper subprocess)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from fiwi_filmmusik.detection import ShazamDetectionClient
from fiwi_filmmusik.models import MusicSegment


def _segment():
    return MusicSegment(
        audio=np.zeros(2000, dtype=np.float32), start_time=0.0, end_time=1.0, sample_rate=2000
    )


def _client_with_helper():
    """A client that believes the helper exists (path is never actually run;
    subprocess.run is mocked in each test)."""
    return ShazamDetectionClient(helper_path="/fake/shazamkit-match")


def _run_returning(stdout: str):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


def test_no_helper_returns_no_match():
    ShazamDetectionClient._warned = True  # silence the one-time notice
    client = ShazamDetectionClient(helper_path=None)
    # Force _find to also yield nothing.
    with patch("fiwi_filmmusik.detection._find_shazamkit_helper", return_value=None):
        client = ShazamDetectionClient()
    r = client.detect(_segment())
    assert r.title is None and r.provider == "shazam" and r.confidence == 0.0


def test_successful_match_is_parsed():
    payload = json.dumps({
        "title": "Never Gonna Give You Up", "artist": "Rick Astley",
        "isrc": "GBARL9300135", "appleMusicURL": "https://music.apple.com/x",
        "shazamID": "12345",
    })
    with patch("subprocess.run", return_value=_run_returning(payload)):
        r = _client_with_helper().detect(_segment())
    assert r.title == "Never Gonna Give You Up"
    assert r.artist == "Rick Astley"
    assert r.provider == "shazam" and r.confidence == 1.0
    assert r.metadata["isrc"] == "GBARL9300135"
    assert r.metadata["apple_music_url"] == "https://music.apple.com/x"


def test_no_match_result_null():
    with patch("subprocess.run", return_value=_run_returning('{"result": null}')):
        r = _client_with_helper().detect(_segment())
    assert r.title is None and r.confidence == 0.0


def test_entitlement_error_202_is_graceful_no_match():
    ShazamDetectionClient._warned = True
    payload = json.dumps({"error": "match failed", "code": 202})
    with patch("subprocess.run", return_value=_run_returning(payload)):
        r = _client_with_helper().detect(_segment())
    assert r.title is None and r.provider == "shazam"


def test_garbage_output_is_no_match():
    with patch("subprocess.run", return_value=_run_returning("not json at all")):
        r = _client_with_helper().detect(_segment())
    assert r.title is None


def test_timeout_is_no_match():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
        r = _client_with_helper().detect(_segment())
    assert r.title is None and r.confidence == 0.0
