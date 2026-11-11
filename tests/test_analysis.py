"""Tests for the analysis the app and the CLI share (analysis.analyze)."""

import json
import threading
from pathlib import Path

import pytest

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.analysis import Cancelled, analyze
from soundtrackID.chunker import AudioChunker
from soundtrackID.isolators import DummyIsolator
from soundtrackID.pipeline import Pipeline
from tests.test_pipeline import (
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
    """With what the analysis adds to the pipeline's results, e.g. the
    rate-limit flag, which the pipeline sets only once its provider stopped."""
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
    assert saved["rate_limited"] is True
    assert (saved["providers"], saved["film"]) == (["acrcloud"], {"title": "F"})


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
