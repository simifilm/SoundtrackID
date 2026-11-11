"""Tests for the analysis the app and the CLI share (analysis.analyze)."""

import json
import threading
from pathlib import Path

import pytest

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.analysis import Cancelled, analyze, resume
from soundtrackID.chunker import AudioChunker
from soundtrackID.isolators import DummyIsolator
from soundtrackID.pipeline import Pipeline
from tests.test_pipeline import (
    FailingClient,
    FakeClassifier,
    FakeLoader,
    NoMatchClient,
    ThrottledClient,
)


def _pipeline(tmp_path, client):
    return Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=client,
        output_dir=tmp_path,
        detection_concurrency=1,
    )


def test_results_are_saved_once_the_run_is_complete(tmp_path):
    """The results, with what the analysis adds to the pipeline's, and the
    timeline; the cues the provider's limit skipped are pending."""
    pipeline = _pipeline(tmp_path, ThrottledClient(answers=2))

    results = analyze(
        pipeline,
        Path("film.mp4"),
        "acrcloud",
        film={"title": "F"},
        max_segment_duration=5.0,
    )

    [run_dir] = tmp_path.iterdir()
    saved = json.loads((run_dir / "results.json").read_text())
    assert saved == results
    assert (saved["providers"], saved["film"]) == (["acrcloud"], {"title": "F"})
    assert any(c["pending"] for c in saved["cues"])
    timeline = json.loads((run_dir / "timeline.json").read_text())
    assert timeline["waveform"]["duration"] == 30.0
    assert len(timeline["classification_windows"]) == 5


def test_a_cancelled_run_leaves_no_folder(tmp_path, monkeypatch):
    """Also when cancelled after the pipeline, during the composer lookup."""
    cancel = threading.Event()

    def enrich(results, http_client, progress_cb):
        cancel.set()  # the user clicks Cancel
        progress_cb("music_start", total=1)

    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", enrich)

    with pytest.raises(Cancelled):
        analyze(
            _pipeline(tmp_path, NoMatchClient()),
            Path("film.mp4"),
            "acrcloud",
            film={"title": "F"},
            cancel=cancel,
        )

    assert list(tmp_path.iterdir()) == []


def test_a_failing_composer_lookup_is_logged(tmp_path, monkeypatch, caplog):
    """The run is saved without composers, and the log says why."""

    def enrich(**kwargs):
        raise ValueError("MusicBrainz is down")

    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", enrich)

    analyze(
        _pipeline(tmp_path, NoMatchClient()),
        Path("film.mp4"),
        "acrcloud",
        film={"title": "F"},
    )

    [run_dir] = tmp_path.iterdir()
    assert (run_dir / "results.json").exists()
    assert "MusicBrainz is down" in caplog.text


def test_a_resume_logs_a_cue_the_provider_failed_on(tmp_path, monkeypatch, caplog):
    """The cue stays pending, with its audio, for the next resume."""
    results = analyze(
        _pipeline(tmp_path, ThrottledClient(answers=2)),
        Path("film.mp4"),
        "acrcloud",
        film={"title": "F"},
        max_segment_duration=5.0,
    )
    pending = [c["segment_id"] for c in results["cues"] if c["pending"]]
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: FailingClient(fail_at=(0.0,)),  # resumed clips start at 0 s
    )

    [run_dir] = tmp_path.iterdir()
    resumed = resume(run_dir, "acrcloud")

    still = [c for c in resumed["cues"] if c["pending"]]
    assert [c["segment_id"] for c in still] == pending
    assert all((run_dir / c["audio_file"]).exists() for c in still)
    assert "malformed answer" in caplog.text
