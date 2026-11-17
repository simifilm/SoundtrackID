"""Browser tests of the page (static/: index.html, app.css, js/).

A headless Chromium drives the real app, which runs in this process with fakes
for the model, the video loader, the providers and the online lookups. The tests
check what a user sees and what reaches the server, not how the page's script is
organized, so they hold while it is split into files.

They need Chromium (uv run playwright install chromium) and are skipped without
it. CI runs them on pull requests only: pytest -m browser.
"""

import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from mutagen.mp4 import MP4

from soundtrackID.detection import RateLimitError
from soundtrackID.models import DetectionResult
from soundtrackID.web import analyze, data
from soundtrackID.web.app import app
from tests.fakes import FakeClassifier, FakeLoader
from tests.media import write_video
from tests.ui.helpers import MUSIC_STARTS

# The API keys the app reads from the environment; cleared for every test.
KEY_VARS = (
    "ACRCLOUD_HOST",
    "ACRCLOUD_ACCESS_KEY",
    "ACRCLOUD_ACCESS_SECRET",
    "AUDD_API_TOKEN",
    "FIWI_TMDB_API_TOKEN",
    "FIWI_TMDB_KEY",
)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """Skip the browser tests, once selected, where Chromium isn't installed."""
    selected = [item for item in items if item.get_closest_marker("browser")]
    if selected and not _chromium_installed():
        skip = pytest.mark.skip(
            reason="no Chromium: uv run playwright install chromium"
        )
        for item in selected:
            item.add_marker(skip)


def _chromium_installed() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return Path(p.chromium.executable_path).exists()


@pytest.fixture(scope="session")
def base_url():
    """The app on a free port, for the session; page.goto("/") opens it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="off",  # it would load the real model
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(page):
    """The page, failing the test on an error in its script."""
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: (
            errors.append(msg.text)
            if msg.type == "error"
            and not msg.text.startswith("Failed to load resource")
            else None
        ),
    )
    yield page
    assert not errors


class Fakes:
    """What the app analyzes with in these tests; a test sets what it needs."""

    def __init__(self):
        self.tracks = {}  # identification window start (s) → (title, artist, metadata)
        self.answers = None  # calls the provider answers before its limit; None: all
        self.composers = {}  # ISRC → the enrichment the composer lookup finds
        self.film = {}  # what "Look up online" finds
        # Cleared, classification (after the first window) or identification wait.
        self.classifying = threading.Event()
        self.identifying = threading.Event()
        self.classifying.set()
        self.identifying.set()
        self._calls = 0
        self._lock = threading.Lock()

    def get_classifier(self):
        fakes = self

        class Classifier(FakeClassifier):
            def classify(self, chunk):
                if chunk.start_time > 0:
                    fakes.classifying.wait(timeout=10)
                return super().classify(chunk)

        return Classifier(music_starts=MUSIC_STARTS)

    def detect(self, segment):
        self.identifying.wait(timeout=10)
        with self._lock:
            self._calls += 1
            refused = self.answers is not None and self._calls > self.answers
        if refused:
            raise RateLimitError("quota exceeded")
        title, artist, metadata = self.tracks.get(segment.start_time, (None, None, {}))
        return DetectionResult(segment, title, artist, "acrcloud", metadata)

    def enrich(self, results, http_client=None, progress_cb=None):
        cues = [c for c in results["cues"] if c.get("isrc") in self.composers]
        progress = progress_cb or (lambda *args, **kwargs: None)
        progress("music_start", total=len(cues))
        for index, cue in enumerate(cues, start=1):
            cue["enrichment"] = json.loads(json.dumps(self.composers[cue["isrc"]]))
            progress("music_cue", index=index, total=len(cues))
        return results

    def lookup_film(self, imdb_id=None, tmdb_id=None, title=None, year=None):
        return dict(self.film)


@pytest.fixture
def fakes(data_dir, monkeypatch):
    """The app with its runs, staging and settings in a temporary data folder,
    the API keys unset, and the fakes in place of the model, providers and
    lookups."""
    monkeypatch.setattr(analyze, "_staging", {})
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    fakes = Fakes()
    monkeypatch.setattr(analyze, "get_classifier", fakes.get_classifier)
    monkeypatch.setattr("soundtrackID.pipeline.VideoLoader", FakeLoader)
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: SimpleNamespace(detect=fakes.detect),
    )
    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", fakes.enrich)
    monkeypatch.setattr("soundtrackID.metadata.film.lookup_film", fakes.lookup_film)
    yield fakes
    # Never leave a server thread waiting.
    fakes.classifying.set()
    fakes.identifying.set()


@pytest.fixture
def runs(fakes):
    """The folder of the app's runs."""
    return data.runs_dir()


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    """A short MP4 without tags."""
    return write_video(tmp_path_factory.mktemp("media") / "film.mp4", seconds=2)


@pytest.fixture(scope="session")
def tagged_video(tmp_path_factory):
    """A short MP4 tagged with a film's title and year, as Subler writes them."""
    path = write_video(tmp_path_factory.mktemp("media") / "sunset.mp4", seconds=2)
    mp4 = MP4(str(path))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags.update({"\xa9nam": ["Sunset Boulevard"], "\xa9day": ["1950"]})
    mp4.save()
    return path
