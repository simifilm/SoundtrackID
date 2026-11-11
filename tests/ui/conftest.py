"""Browser tests of the page (static/index.html).

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

import numpy as np
import pytest
import uvicorn
from mutagen.mp4 import MP4
from scipy.io import wavfile

from soundtrackID import app as app_module
from soundtrackID.detection import RateLimitError
from soundtrackID.models import RESULTS_VERSION, DetectionResult
from tests.media import write_video
from tests.test_pipeline import FakeClassifier, FakeLoader

# The API keys the app reads from the environment; cleared for every test.
KEY_VARS = (
    "ACRCLOUD_HOST",
    "ACRCLOUD_ACCESS_KEY",
    "ACRCLOUD_ACCESS_SECRET",
    "AUDD_API_TOKEN",
    "FIWI_TMDB_API_TOKEN",
    "FIWI_TMDB_KEY",
)

# 30 s of music, as the fake classifier hears it in FakeLoader's audio: windows
# of 10 s every 5 s.
MUSIC_STARTS = (0.0, 5.0, 10.0, 15.0, 20.0)
WINDOWS = [
    {
        "start": s,
        "end": s + 10.0,
        "music": 0.6,
        "is_music": True,
        "classes": {"Music": 0.6, "Speech": 0.3, "Piano": 0.1},
    }
    for s in MUSIC_STARTS
]
TIMELINE = {
    "waveform": {"samples": [0.5] * 150, "duration": 30.0},
    "classification": [
        {"start": 0.0, "end": 30.0, "is_music": True, "classes": {"Music": 0.6}}
    ],
    "classification_windows": WINDOWS,
}


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
        app_module.app,
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

    def get_classifier(self, threshold):
        fakes = self

        class Classifier(FakeClassifier):
            def classify(self, chunk):
                if chunk.start_time > 0:
                    fakes.classifying.wait(timeout=10)
                return super().classify(chunk)

        classifier = Classifier(music_starts=MUSIC_STARTS)
        classifier.threshold = threshold
        return classifier

    def detect(self, segment):
        self.identifying.wait(timeout=10)
        with self._lock:
            self._calls += 1
            refused = self.answers is not None and self._calls > self.answers
        if refused:
            raise RateLimitError("quota exceeded")
        title, artist, metadata = self.tracks.get(segment.start_time, (None, None, {}))
        confidence = 0.9 if title else 0.0
        return DetectionResult(segment, title, artist, confidence, "acrcloud", metadata)

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
def fakes(tmp_path, monkeypatch):
    """The app with its runs, staging and settings in tmp_path, the API keys
    unset, and the fakes in place of the model, providers and lookups."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(app_module, "_staging", {})
    monkeypatch.setattr(app_module, "_SETTINGS_PATH", tmp_path / "settings.json")
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    fakes = Fakes()
    monkeypatch.setattr(app_module, "_get_classifier", fakes.get_classifier)
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
def runs(fakes, tmp_path):
    """The folder of the app's runs."""
    return tmp_path / "cache"


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


def cue(n, title=None, **fields):
    """The n-th cue of a run: 10 s from (n - 1) * 10 s."""
    return {
        "segment_id": f"seg_{n:03d}",
        "start": 10.0 * (n - 1),
        "duration": 10.0,
        "audio_file": None,
        "confidence": 0.9 if title else None,
        "pending": False,
        "provider": "acrcloud" if title else None,
        "title": title,
        "artist": "Artist" if title else None,
        "album": None,
        "enrichment": None,
        **fields,
    }


def make_run(runs, cues, name="film_2026-11-11_10-00-00", **results):
    """A run on disk as an analysis saves it; pending cues get their audio."""
    run = runs / name
    (run / "segments").mkdir(parents=True)
    for c in cues:
        if c.get("audio_file"):
            wavfile.write(run / c["audio_file"], 1000, np.zeros(1000, np.int16))
    data = {
        "version": RESULTS_VERSION,
        "source": "film.mp4",
        "video_name": name,
        "film": None,
        "cues": cues,
        "providers": ["shazam"],
        **results,
    }
    (run / "results.json").write_text(json.dumps(data))
    (run / "timeline.json").write_text(json.dumps(TIMELINE))
    return run


def saved(run):
    """The run's results.json as the server saved it."""
    return json.loads((run / "results.json").read_text())


def open_run(page, name="film_2026-11-11_10-00-00"):
    """Open a run from Recent runs."""
    page.goto("/")
    page.locator("#history-panel > summary").click()
    page.locator(".history-item", has_text=name).click()
    page.locator("#results-view").wait_for()
