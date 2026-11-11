"""Tests for the analysis the app and the CLI share (analysis.analyze)."""

import json
import threading
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.analysis import Cancelled, analyze, resume
from soundtrackID.chunker import AudioChunker
from soundtrackID.detection import ProviderAuthError
from soundtrackID.isolators import DummyIsolator
from soundtrackID.models import DetectionResult
from soundtrackID.pipeline import Pipeline
from soundtrackID.runs import Run
from tests.test_pipeline import (
    SR,
    FailingClient,
    FakeClassifier,
    FakeLoader,
    NoMatchClient,
    RejectingClient,
    ThrottledClient,
)


def _pipeline(client):
    return Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=client,
        detection_concurrency=1,  # windows are asked in order
    )


def _analyze_throttled(tmp_path):
    """A run whose provider answered two windows, then hit its limit; returns
    the run's folder."""
    analyze(
        _pipeline(ThrottledClient(answers=2)),
        Path("film.mp4"),
        "acrcloud",
        tmp_path,
        film={"title": "F"},
        max_segment_duration=5.0,
    )
    [run_dir] = tmp_path.iterdir()
    return run_dir


def test_results_are_saved_once_the_run_is_complete(tmp_path):
    """The results, with what the analysis adds to the pipeline's, and the
    timeline; the cues the provider's limit skipped are pending."""
    results = analyze(
        _pipeline(ThrottledClient(answers=2)),
        Path("film.mp4"),
        "acrcloud",
        tmp_path,
        film={"title": "F"},
        max_segment_duration=5.0,
    )

    [run_dir] = tmp_path.iterdir()
    saved = json.loads((run_dir / "results.json").read_text())
    assert saved == results
    assert saved["video_name"] == run_dir.name
    assert (saved["providers"], saved["film"]) == (["acrcloud"], {"title": "F"})
    assert any(c["pending"] for c in saved["cues"])
    timeline = json.loads((run_dir / "timeline.json").read_text())
    assert timeline["waveform"]["duration"] == 30.0
    assert len(timeline["classification_windows"]) == 5


def test_only_pending_cues_keep_their_audio(tmp_path):
    """A resume sends it again; nothing else reads it."""
    run_dir = _analyze_throttled(tmp_path)

    cues = json.loads((run_dir / "results.json").read_text())["cues"]
    assert [c["audio_file"] for c in cues] == [
        None,  # answered ("no match")
        "segments/seg_002.wav",
        "segments/seg_003.wav",
        "segments/seg_004.wav",
        "segments/seg_005.wav",
    ]
    segments = run_dir / "segments"
    assert sorted(p.name for p in segments.iterdir()) == [
        "seg_002.wav",
        "seg_003.wav",
        "seg_004.wav",
        "seg_005.wav",
    ]
    # The clip the provider would have heard: 10-15 s of the film.
    rate, wav = wavfile.read(segments / "seg_002.wav")
    audio, _ = FakeLoader().load(None)
    assert rate == SR
    np.testing.assert_allclose(wav / 32767, audio[10 * SR : 15 * SR], atol=1e-4)


def test_a_cancelled_run_leaves_no_folder(tmp_path, monkeypatch):
    """Also when cancelled after the pipeline, during the composer lookup."""
    cancel = threading.Event()

    def enrich(results, http_client, progress_cb):
        cancel.set()  # the user clicks Cancel
        progress_cb("music_start", total=1)

    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", enrich)

    with pytest.raises(Cancelled):
        analyze(
            _pipeline(NoMatchClient()),
            Path("film.mp4"),
            "acrcloud",
            tmp_path,
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
        _pipeline(NoMatchClient()),
        Path("film.mp4"),
        "acrcloud",
        tmp_path,
        film={"title": "F"},
    )

    [run_dir] = tmp_path.iterdir()
    assert (run_dir / "results.json").exists()
    assert "MusicBrainz is down" in caplog.text


def test_a_resume_logs_a_cue_the_provider_failed_on(tmp_path, monkeypatch, caplog):
    """The cue stays pending, with its audio, for the next resume."""
    run_dir = _analyze_throttled(tmp_path)
    cues = json.loads((run_dir / "results.json").read_text())["cues"]
    pending = [c["segment_id"] for c in cues if c["pending"]]
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: FailingClient(fail_at=(0.0,)),  # resumed clips start at 0 s
    )

    resumed = resume(run_dir, "acrcloud")

    still = [c for c in resumed["cues"] if c["pending"]]
    assert [c["segment_id"] for c in still] == pending
    assert all((run_dir / c["audio_file"]).exists() for c in still)
    assert "malformed answer" in caplog.text


def test_a_resume_keeps_the_edits_saved_while_it_ran(tmp_path, monkeypatch):
    """It waits on the provider for minutes; meanwhile a cue it isn't asking
    about is corrected and a pending one removed, which its save keeps."""
    run_dir = _analyze_throttled(tmp_path)
    run = Run(run_dir)

    def edit_meanwhile(results):
        cues = results["cues"]
        cues[0]["title"] = "Corrected"  # not pending
        results["cues"] = cues[:-1]  # the last one, pending, removed

    class EditedMeanwhile:
        edited = False

        def detect(self, segment):
            if not self.edited:
                self.edited = True
                run.update(edit_meanwhile)
            return DetectionResult(segment, "Found", "A", "acrcloud")

    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: EditedMeanwhile(),
    )

    resume(run_dir, "acrcloud")

    cues = json.loads((run_dir / "results.json").read_text())["cues"]
    assert cues[0]["title"] == "Corrected"
    assert [(c["title"], c["pending"]) for c in cues[1:]] == [("Found", False)] * 3
    assert not list((run_dir / "segments").iterdir())  # answered: no audio kept


def test_a_resume_with_rejected_keys_fails_and_keeps_the_run(tmp_path, monkeypatch):
    """It says why, and the cues stay pending for a resume with the right keys."""
    run_dir = _analyze_throttled(tmp_path)
    before = (run_dir / "results.json").read_text()
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client", lambda api: RejectingClient()
    )

    with pytest.raises(ProviderAuthError):
        resume(run_dir, "acrcloud")

    assert (run_dir / "results.json").read_text() == before
