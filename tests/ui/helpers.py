"""Runs on disk as an analysis saves them, and opening them in the page, for
the browser tests."""

import json

import numpy as np
from scipy.io import wavfile

from soundtrackid.results import RESULTS_VERSION

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


def cue(n, title=None, **fields):
    """The n-th cue of a run: 10 s from (n - 1) * 10 s."""
    return {
        "segment_id": f"seg_{n:03d}",
        "start": 10.0 * (n - 1),
        "duration": 10.0,
        "audio_file": None,
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
