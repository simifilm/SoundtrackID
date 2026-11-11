"""Tests for the command line (soundtrackID.cli): the same analysis as the app."""

import csv
import json
from types import SimpleNamespace

import pytest

from soundtrackID import cli
from soundtrackID.isolators import BaseMusicIsolator
from soundtrackID.models import DetectionResult
from tests.test_pipeline import FakeClassifier, ThrottledClient, fake_components


def _analyze(tmp_path, monkeypatch, *options, client=None):
    """Run `cli analyze film.mp4` on 30 s of music; returns the run's folder."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)
    monkeypatch.setattr(
        cli, "_classifier", lambda name, threshold: get_classifier(threshold)
    )

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
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: SimpleNamespace(
            detect=lambda seg: DetectionResult(seg, "Song", "A", "acrcloud")
        ),
    )

    cli.main(["resume", str(run_dir)])

    results = json.loads((run_dir / "results.json").read_text())
    assert [c["title"] for c in results["cues"] if c["segment_id"] in pending] == [
        "Song"
    ] * len(pending)
    assert not any(c["pending"] for c in results["cues"])
