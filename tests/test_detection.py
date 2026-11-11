"""Tests for ShazamDetectionClient: native ShazamKit helper + shazamio fallback."""

import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from soundtrackID.detection import RateLimitError, ShazamDetectionClient
from soundtrackID.models import MusicSegment


def _segment():
    return MusicSegment(
        audio=np.zeros(2000, dtype=np.float32),
        start_time=0.0,
        end_time=1.0,
        sample_rate=2000,
    )


def _client():
    return ShazamDetectionClient(helper_path="/fake/shazamkit-match")


def _run_returning(stdout: str):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


# ── ShazamKit path ─────────────────────────────────────────────────────────


def test_shazamkit_match_is_parsed():
    payload = json.dumps(
        {
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
            "isrc": "GBARL9300135",
            "appleMusicURL": "https://music.apple.com/x",
        }
    )
    with patch("subprocess.run", return_value=_run_returning(payload)):
        r = _client().detect(_segment())
    assert r.title == "Never Gonna Give You Up" and r.artist == "Rick Astley"
    assert r.provider == "shazam"
    assert r.metadata["isrc"] == "GBARL9300135"


def test_shazamkit_genuine_no_match_is_trusted_no_fallback():
    # {"result": null} means ShazamKit ran and found nothing — do NOT fall back.
    with (
        patch.object(ShazamDetectionClient, "_detect_shazamio") as fb,
        patch("subprocess.run", return_value=_run_returning('{"result": null}')),
    ):
        r = _client().detect(_segment())
    assert r.title is None
    fb.assert_not_called()


# ── Fallback to shazamio ───────────────────────────────────────────────────


def test_202_falls_back_to_shazamio():
    ShazamDetectionClient._warned = True
    from soundtrackID.models import DetectionResult

    fb = DetectionResult(
        segment=_segment(),
        title="From shazamio",
        artist="X",
        provider="shazam",
    )
    with (
        patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=fb) as m,
        patch(
            "subprocess.run", return_value=_run_returning('{"error":"x","code":202}')
        ),
    ):
        r = _client().detect(_segment())
    assert r.title == "From shazamio"
    m.assert_called_once()


def test_no_helper_uses_shazamio():
    from soundtrackID.models import DetectionResult

    fb = DetectionResult(
        segment=_segment(),
        title="Fallback hit",
        artist="Y",
        provider="shazam",
    )
    with (
        patch("soundtrackID.detection._find_shazamkit_helper", return_value=None),
        patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=fb) as m,
    ):
        r = ShazamDetectionClient().detect(_segment())
    assert r.title == "Fallback hit"
    m.assert_called_once()


def test_timeout_falls_back():
    import subprocess

    with (
        patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=None) as m,
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)),
    ):
        r = _client().detect(_segment())
    assert r.title is None and r.provider == "shazam"
    m.assert_called_once()


def test_garbage_output_falls_back():
    with (
        patch.object(ShazamDetectionClient, "_detect_shazamio", return_value=None) as m,
        patch("subprocess.run", return_value=_run_returning("not json")),
    ):
        r = _client().detect(_segment())
    assert r.title is None
    m.assert_called_once()


# ── shazamio against a stand-in for Shazam's API ───────────────────────────

THROTTLED = (429, {"error": "Too Many Requests"})
SERVER_ERROR = (503, {"error": "Service Unavailable"})
NO_MATCH = (200, {"matches": []})
MATCH = (200, {"matches": [{}], "track": {"title": "Song", "subtitle": "Band"}})


@pytest.fixture
def shazam_api(monkeypatch):
    """Answers with the given (status, body) responses in turn, repeating the
    last one; returns them and the requests received. Retry backoff is skipped."""
    shazamio_misc = pytest.importorskip("shazamio.misc")
    responses, sent = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, body = responses[min(len(sent), len(responses) - 1)]
            sent.append(request)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/?{{language}}"
    monkeypatch.setattr(shazamio_misc.ShazamUrl, "SEARCH_FROM_FILE", url)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda delay, *a: real_sleep(0, *a))
    monkeypatch.setattr("soundtrackID.detection._find_shazamkit_helper", lambda: None)
    yield responses, sent
    server.shutdown()


def _music(seconds=5):
    audio = np.random.default_rng(0).uniform(-0.5, 0.5, 16000 * seconds)
    return MusicSegment(audio.astype(np.float32), 0.0, float(seconds), 16000)


@pytest.mark.parametrize(
    "answer", [THROTTLED, SERVER_ERROR], ids=["throttled", "server error"]
)
def test_shazamio_still_refusing_after_retries_is_a_rate_limit(shazam_api, answer):
    """Not a "no match": the run stops identifying and offers to resume."""
    responses, sent = shazam_api
    responses.append(answer)

    with pytest.raises(RateLimitError):
        ShazamDetectionClient().detect(_music())

    assert len(sent) == 20  # shazamio's retries


def test_shazam_unreachable_is_a_rate_limit(shazam_api, monkeypatch):
    """Offline, nobody listened to the segment: it stays pending for a resume
    instead of counting as "no match"."""
    with socket.socket() as s:  # a port that nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?{{language}}"
    monkeypatch.setattr("shazamio.misc.ShazamUrl.SEARCH_FROM_FILE", url)

    with pytest.raises(RateLimitError, match="Shazam unreachable"):
        ShazamDetectionClient().detect(_music())


def test_shazam_without_shazamkit_or_shazamio_fails_at_start(monkeypatch):
    """Rather than reporting "no match" for every segment."""
    monkeypatch.setattr("soundtrackID.detection._find_shazamkit_helper", lambda: None)
    monkeypatch.setitem(sys.modules, "shazamio", None)  # not installed

    with pytest.raises(ImportError, match=r"soundtrackID\[shazam\]"):
        ShazamDetectionClient()


@pytest.mark.parametrize(
    "answers, title",
    [([NO_MATCH], None), ([THROTTLED, THROTTLED, MATCH], "Song")],
    ids=["no match", "throttling clears within the retries"],
)
def test_shazamio_answers(shazam_api, answers, title):
    responses, sent = shazam_api
    responses.extend(answers)

    assert ShazamDetectionClient().detect(_music()).title == title
    assert len(sent) == len(answers)


def test_shazamio_listens_to_12_s_of_a_longer_clip(shazam_api):
    """Shazam matches nothing in 15 s or more (ShazamIO issue #150)."""
    responses, sent = shazam_api
    responses.append(NO_MATCH)

    ShazamDetectionClient().detect(_music(seconds=30))

    assert [request["signature"]["samplems"] for request in sent] == [12_000]
