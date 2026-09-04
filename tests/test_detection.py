"""Tests for ShazamDetectionClient: native ShazamKit helper + shazamio fallback."""

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


def _client():
    return ShazamDetectionClient(helper_path="/fake/shazamkit-match")


def _run_returning(stdout: str):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


# ── ShazamKit path ─────────────────────────────────────────────────────────

def test_shazamkit_match_is_parsed():
    payload = json.dumps({
        "title": "Never Gonna Give You Up", "artist": "Rick Astley",
        "isrc": "GBARL9300135", "appleMusicURL": "https://music.apple.com/x",
    })
    with patch("subprocess.run", return_value=_run_returning(payload)):
        r = _client().detect(_segment())
    assert r.title == "Never Gonna Give You Up" and r.artist == "Rick Astley"
    assert r.provider == "shazam" and r.confidence == 1.0
    assert r.metadata["isrc"] == "GBARL9300135"


def test_shazamkit_genuine_no_match_is_trusted_no_fallback():
    # {"result": null} means ShazamKit ran and found nothing — do NOT fall back.
    with patch.object(ShazamDetectionClient, "_detect_shazamio") as fb, \
         patch("subprocess.run", return_value=_run_returning('{"result": null}')):
        r = _client().detect(_segment())
    assert r.title is None
    fb.assert_not_called()


# ── Fallback to shazamio ───────────────────────────────────────────────────

def test_202_falls_back_to_shazamio():
    ShazamDetectionClient._warned = True
    from fiwi_filmmusik.models import DetectionResult
    fb = DetectionResult(segment=_segment(), title="From shazamio", artist="X",
                         confidence=1.0, provider="shazam")
    with patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=fb) as m, \
         patch("subprocess.run", return_value=_run_returning('{"error":"x","code":202}')):
        r = _client().detect(_segment())
    assert r.title == "From shazamio"
    m.assert_called_once()


def test_no_helper_uses_shazamio():
    from fiwi_filmmusik.models import DetectionResult
    fb = DetectionResult(segment=_segment(), title="Fallback hit", artist="Y",
                         confidence=1.0, provider="shazam")
    with patch("fiwi_filmmusik.detection._find_shazamkit_helper", return_value=None), \
         patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=fb) as m:
        r = ShazamDetectionClient().detect(_segment())
    assert r.title == "Fallback hit"
    m.assert_called_once()


def test_timeout_falls_back():
    import subprocess
    with patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=None) as m, \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
        r = _client().detect(_segment())
    assert r.title is None and r.provider == "shazam"
    m.assert_called_once()


def test_garbage_output_falls_back():
    with patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=None) as m, \
         patch("subprocess.run", return_value=_run_returning("not json")):
        r = _client().detect(_segment())
    assert r.title is None
    m.assert_called_once()
