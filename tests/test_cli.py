"""Tests for the command line (soundtrackID.cli): the same analysis as the app."""

import csv
import json
from types import SimpleNamespace

import pytest

from soundtrackID import cli
from soundtrackID.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackID.isolators import BaseMusicIsolator
from soundtrackID.models import DetectionResult
from tests.test_pipeline import FakeClassifier, ThrottledClient, fake_components


def _analyze(tmp_path, monkeypatch, *options, client=None):
    """Run `cli analyze film.mp4` on 30 s of music; returns the run's folder."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)
    monkeypatch.setattr(cli, "load_classifier", lambda hf: get_classifier())

    cli.main(["analyze", "film.mp4", "--output-dir", str(tmp_path)] + list(options))

    [run_dir] = tmp_path.iterdir()
    return run_dir


class FakeDemucs(BaseMusicIsolator):
    built = 0

    def __init__(self):
        FakeDemucs.built += 1

    def isolate(self, audio, sample_rate):
        return audio


@pytest.mark.parametrize("options", [[], ["--no-isolation"]], ids=["default", "old"])
def test_vocals_stay_unless_isolation_is_asked_for(tmp_path, monkeypatch, options):
    """Demucs needs the demucs extra, which the documented install lacks; the
    old --no-isolation is still accepted."""
    monkeypatch.setattr("soundtrackID.isolators.DemucsIsolator", FakeDemucs)
    FakeDemucs.built = 0

    run_dir = _analyze(tmp_path, monkeypatch, *options)

    assert FakeDemucs.built == 0
    settings = json.loads((run_dir / "results.json").read_text())["settings"]
    assert settings["isolation"] is False


def test_isolation_removes_the_vocals_when_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr("soundtrackID.isolators.DemucsIsolator", FakeDemucs)
    FakeDemucs.built = 0

    run_dir = _analyze(tmp_path, monkeypatch, "--isolation")

    assert FakeDemucs.built == 1
    settings = json.loads((run_dir / "results.json").read_text())["settings"]
    assert settings["isolation"] is True


def test_analyze_saves_a_run_like_the_app(tmp_path, monkeypatch):
    run_dir = _analyze(
        tmp_path, monkeypatch, "--title", "Sunset Boulevard", "--year", "1950", "--csv"
    )

    assert run_dir.name.startswith("film_")  # the file's name and the time
    results = json.loads((run_dir / "results.json").read_text())
    assert results["film"] == {"title": "Sunset Boulevard", "year": 1950}
    assert results["settings"]["window"] == 10.0
    assert results["settings"]["isolation"] is False
    with (run_dir / "results.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["segment_id"] for r in rows] == [c["segment_id"] for c in results["cues"]]


def test_threshold_decides_what_is_music(tmp_path, monkeypatch):
    """The film's windows all score 0.6: at a threshold of 0.7, none is music."""
    run_dir = _analyze(tmp_path, monkeypatch, "--threshold", "0.7")

    results = json.loads((run_dir / "results.json").read_text())
    assert results["settings"]["threshold"] == 0.7
    assert results["cues"] == []


def test_by_default_the_cli_analyzes_like_the_app_into_its_runs(tmp_path, monkeypatch):
    """With the app's settings, and in the app's runs folder, so its history
    lists the run."""
    from fastapi.testclient import TestClient

    from soundtrackID import app

    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    classifier.window_duration = 10.23  # the model's own, not a round 10 s
    get_classifier = fake_components(monkeypatch, None, classifier)
    monkeypatch.setattr(cli, "load_classifier", lambda hf: get_classifier())
    monkeypatch.chdir(tmp_path)

    cli.main(["analyze", "film.mp4"])

    client = TestClient(app.app)
    [run] = client.get("/history").json()
    results = client.get(f"/results/{run['video_name']}").json()
    assert results["providers"] == [DEFAULT_API]
    assert results["settings"] == {
        "classifier": "FakeClassifier",
        "threshold": MUSIC_THRESHOLD,
        "window": 10.23,
        "hop": CLASSIFICATION_HOP_S,
        "bridge_gap": BRIDGE_GAP_S,
        "isolation": False,
        "max_segment_duration": MAX_SEGMENT_S,
    }


def test_whole_music_segments_are_max_segment_0(tmp_path, monkeypatch):
    """As the app's "Whole music segment"."""
    run_dir = _analyze(tmp_path, monkeypatch, "--max-segment", "0")

    results = json.loads((run_dir / "results.json").read_text())
    assert results["settings"]["max_segment_duration"] is None
    assert [(c["start"], c["duration"]) for c in results["cues"]] == [(0.0, 30.0)]


def test_lookup_film_fills_in_what_is_missing(tmp_path, monkeypatch):
    """As the app's "Look up online": what is known stays."""
    hints = []

    def lookup_film(**hint):
        hints.append(hint)
        found = {"title": "Sunset Blvd.", "year": 1950, "director": "Billy Wilder"}
        return {**found, "imdb_id": None}

    monkeypatch.setattr("soundtrackID.metadata.film.lookup_film", lookup_film)

    run_dir = _analyze(
        tmp_path, monkeypatch, "--title", "Sunset Boulevard", "--lookup-film"
    )

    results = json.loads((run_dir / "results.json").read_text())
    assert hints == [
        {"imdb_id": None, "tmdb_id": None, "title": "Sunset Boulevard", "year": None}
    ]
    assert results["film"] == {
        "title": "Sunset Boulevard",
        "year": 1950,
        "director": "Billy Wilder",
    }


def test_resume_identifies_what_a_limit_left_pending(tmp_path, monkeypatch, capsys):
    run_dir = _analyze(
        tmp_path, monkeypatch, "--max-segment", "5", client=ThrottledClient(answers=2)
    )
    assert "cli resume" in capsys.readouterr().out
    pending = [
        c["segment_id"]
        for c in json.loads((run_dir / "results.json").read_text())["cues"]
        if c["pending"]
    ]
    assert pending
    asked = []

    def build_detection_client(api):
        asked.append(api)
        return SimpleNamespace(
            detect=lambda seg: DetectionResult(seg, "Song", "A", "acrcloud")
        )

    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client", build_detection_client
    )

    cli.main(["resume", str(run_dir)])

    results = json.loads((run_dir / "results.json").read_text())
    assert [c["title"] for c in results["cues"] if c["segment_id"] in pending] == [
        "Song"
    ] * len(pending)
    assert not any(c["pending"] for c in results["cues"])
    assert asked == [",".join(results["providers"])]  # the run's own
