"""Tests for the pipeline's classification output."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.detection import RateLimitError
from soundtrackID.isolators import DummyIsolator
from soundtrackID.models import ClassificationResult, DetectionResult, MusicSegment
from soundtrackID.pipeline import Pipeline, _group_detections

SR = 1000
LABELS = ["Speech", "Music", "Applause", "Piano"]


class FakeLoader:
    def load(self, path):
        return np.linspace(-0.5, 0.5, 30 * SR, dtype=np.float32), SR


class FakeClassifier:
    """Only the windows starting at `music_starts` are music."""

    labels = LABELS
    threshold = 0.2
    window_duration = 10.0

    def __init__(self, music_starts=(10.0,)):
        self.music_starts = music_starts

    def classify(self, chunk):
        is_music = chunk.start_time in self.music_starts
        probs = np.array(
            [0.3, 0.6, 0.06, 0.04] if is_music else [0.9, 0.05, 0.03, 0.02],
            dtype=np.float32,
        )
        return ClassificationResult(
            chunk=chunk, is_music=is_music, confidence=float(probs[1]), probs=probs
        )


class NoMatchClient:
    def detect(self, segment):
        return DetectionResult(segment=segment, title=None, artist=None, confidence=0.0)


class TitleByStartClient:
    """Matches "X" for clips starting at the given times, nothing otherwise."""

    def __init__(self, starts):
        self.starts = starts

    def detect(self, segment):
        title = "X" if segment.start_time in self.starts else None
        return DetectionResult(
            segment=segment, title=title, artist=None, confidence=1.0
        )


class ReleasedClient:
    """Answers only once released, e.g. after classification has finished."""

    def __init__(self, client):
        self.client = client
        self.released = threading.Event()

    def detect(self, segment):
        self.released.wait(timeout=10)
        return self.client.detect(segment)


class ThrottledClient:
    """Answers "no match" to the first `answers` calls, then refuses every call
    as rate-limited; counts the calls."""

    def __init__(self, answers=0):
        self.answers = answers
        self.calls = 0

    def detect(self, segment):
        self.calls += 1
        if self.calls > self.answers:
            raise RateLimitError("quota exceeded")
        return DetectionResult(segment=segment, title=None, artist=None, confidence=0)


def test_classification_spans_cover_whole_audio(tmp_path):
    """results.json gets gap-free music / no-music spans with every class."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(5.0, 10.0, 15.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=NoMatchClient(),
        output_dir=tmp_path,
    )

    results = pipeline.run(Path("film.mp4"))

    spans = results.classification
    assert [(s["start"], s["end"], s["is_music"]) for s in spans] == [
        (0.0, 10.0, False),
        (10.0, 20.0, True),
        (20.0, 30.0, False),
    ]
    assert all(set(s["classes"]) == set(LABELS) for s in spans)
    assert spans[1]["classes"]["Music"] > FakeClassifier.threshold
    saved = json.loads((tmp_path / "film" / "results.json").read_text())
    assert saved["classification"] == spans


def test_classification_windows_list_top_three_classes(tmp_path):
    """Every classification window is written with its three most likely classes."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=NoMatchClient(),
        output_dir=tmp_path,
    )

    results = pipeline.run(Path("film.mp4"))

    windows = results.classification_windows
    assert [(w["start"], w["end"], w["is_music"]) for w in windows] == [
        (0.0, 10.0, False),
        (5.0, 15.0, False),
        (10.0, 20.0, True),
        (15.0, 25.0, False),
        (20.0, 30.0, False),
    ]
    assert windows[2]["music"] == 0.6
    assert windows[2]["classes"] == {"Music": 0.6, "Speech": 0.3, "Applause": 0.06}
    saved = json.loads((tmp_path / "film" / "results.json").read_text())
    assert saved["classification_windows"] == windows


def test_unidentified_cue_has_no_confidence(tmp_path):
    """Not a made-up value that would show up in the CSV export."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(5.0, 10.0, 15.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=NoMatchClient(),
        output_dir=tmp_path,
    )

    results = pipeline.run(Path("film.mp4"))

    assert [c["confidence"] for c in results.to_dict()["cues"]] == [None]


def test_only_pending_cues_keep_their_audio(tmp_path):
    """A resume sends it again; nothing else reads it."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=ThrottledClient(answers=2),
        output_dir=tmp_path,
        detection_concurrency=1,  # windows are asked in order
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=5.0)

    cues = results.to_dict()["cues"]
    assert [c["audio_file"] for c in cues] == [
        None,  # answered ("no match")
        "segments/seg_002.wav",
        "segments/seg_003.wav",
        "segments/seg_004.wav",
        "segments/seg_005.wav",
    ]
    segments = tmp_path / "film" / "segments"
    assert sorted(p.name for p in segments.iterdir()) == [
        "seg_002.wav",
        "seg_003.wav",
        "seg_004.wav",
        "seg_005.wav",
    ]
    # The clip the provider would have heard: 10-15 s of the film.
    rate, wav = wavfile.read(segments / "seg_002.wav")
    audio, _ = FakeLoader().load(None)
    np.testing.assert_allclose(wav / 32767, audio[10 * SR : 15 * SR], atol=1e-4)


@pytest.mark.parametrize("bridge_gap, n_cues", [(10.0, 1), (5.0, 3)])
def test_bridge_gap_limits_bridged_unidentified_clips(tmp_path, bridge_gap, n_cues):
    """X, no match (10 s), X becomes one cue only if 10 s fits the bridge gap."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=TitleByStartClient(starts=(0.0, 20.0)),
        output_dir=tmp_path,
        bridge_gap=bridge_gap,
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=10.0)

    assert len(results.segments) == n_cues


def test_rate_limit_skips_the_calls_of_windows_already_queued(tmp_path):
    """They would be refused too, Shazam only after ~12 minutes of retries."""
    throttled = ThrottledClient()
    client = ReleasedClient(throttled)

    def on_progress(step, **fields):
        if step == "detecting" and "title" not in fields:  # classification is done
            client.released.set()

    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=client,
        output_dir=tmp_path,
        detection_concurrency=1,  # the other windows wait in the queue
    )

    results = pipeline.run(
        Path("film.mp4"), on_progress=on_progress, max_segment_duration=5.0
    )

    assert results.rate_limited
    assert throttled.calls == 1
    assert not any(i.title for i in results.identifications)


def test_windows_skipped_at_a_rate_limit_stay_separate_pending_cues(tmp_path):
    """So a resume identifies each one; windows answered before still merge."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=ThrottledClient(answers=2),
        output_dir=tmp_path,
        detection_concurrency=1,  # windows are asked in order
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=5.0)

    cues = results.to_dict()["cues"]
    assert [(c["start"], c["duration"], c["pending"]) for c in cues] == [
        (0.0, 10.0, False),  # the two "no match" answers, merged as before
        (10.0, 5.0, True),
        (15.0, 5.0, True),
        (20.0, 5.0, True),
        (25.0, 5.0, True),
    ]


def test_progress_events_carry_their_data_as_fields(tmp_path):
    """The UI reads the fields; `detail` is only a message and is never parsed."""
    client = ReleasedClient(TitleByStartClient(starts=(10.0,)))
    events = []

    def on_progress(step, **fields):
        events.append((step, fields))
        if step == "detecting" and "title" not in fields:  # classification is done
            client.released.set()

    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(5.0, 10.0, 15.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=client,
        output_dir=tmp_path,
    )

    pipeline.run(Path("film.mp4"), on_progress=on_progress, max_segment_duration=5.0)

    def values(step, *keys):
        """`keys` of every `step` event that has the first key."""
        return [
            tuple(f[k] for k in keys) for s, f in events if s == step and keys[0] in f
        ]

    assert values("waveform", "duration") == [(30.0,)]
    assert values("classifying", "start", "end", "is_music") == [
        (0.0, 10.0, False),
        (5.0, 15.0, True),
        (10.0, 20.0, True),
        (15.0, 25.0, True),
        (20.0, 30.0, False),
    ]
    assert sorted(values("identified", "start", "end", "title", "artist", "total")) == [
        (10.0, 15.0, "X", None, 2),
        (15.0, 20.0, None, None, 2),
    ]
    # Labels for the step indicator: a no-match window still has a `title` field.
    assert sorted(values("detecting", "title", "artist", "total"), key=str) == [
        ("X", None, 2),
        (None, None, 2),
    ]


def _analyze(tmp_path, monkeypatch, video=b"video", **form):
    """POST /analyze for film.mp4 with the fake pipeline; returns the SSE events."""
    from fastapi.testclient import TestClient

    from soundtrackID import app

    monkeypatch.setattr(app, "_OUTPUT_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(app, "_staging", {})
    monkeypatch.setattr(
        app,
        "_build_pipeline",
        lambda output_dir, **kwargs: Pipeline(
            loader=FakeLoader(),
            chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
            classifier=FakeClassifier(),
            aggregator=ChunkAggregator(gap_tolerance=1.0),
            isolator=DummyIsolator(),
            detection_client=NoMatchClient(),
            output_dir=output_dir,
        ),
    )

    client = TestClient(app.app)
    staged = client.post("/identify", files={"file": ("film.mp4", video)}).json()
    r = client.post(
        "/analyze",
        data={"staging_id": staged["staging_id"], "film": '{"title": "F"}', **form},
    )
    return [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]


def test_analyze_saves_its_settings_with_the_results(tmp_path, monkeypatch):
    form = {"threshold": "0.3", "hop": "2.5", "bridge_gap": "5", "max_segment": "10"}

    events = _analyze(tmp_path, monkeypatch, **form)

    assert events[-1]["results"]["settings"] == {
        "threshold": 0.3,
        "hop": 2.5,
        "bridge_gap": 5.0,
        "max_segment_duration": 10.0,
    }


def test_results_are_saved_in_full_when_the_video_cannot_be_stored(
    tmp_path, monkeypatch
):
    """The pipeline's own results.json lacks what is added after it, e.g. the
    rate-limit flag (Resume), settings, re-ranking and composers."""
    import shutil

    real_move = shutil.move

    def move(src, dst):
        if Path(dst).name.startswith("source"):  # the video into its run folder
            raise OSError(28, "No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", move)

    events = _analyze(tmp_path, monkeypatch)

    run_dir = tmp_path / "cache" / events[-1]["results"]["video_name"]
    saved = json.loads((run_dir / "results.json").read_text())
    assert saved["settings"]["hop"] == 5.0
    assert saved["providers"] == ["acrcloud"]
    assert "video" not in saved


def test_each_analysis_gets_its_own_run(tmp_path, monkeypatch):
    """Also of a film with the same file name, e.g. the same one again with
    other settings: the run folder carries the time of the analysis."""
    from soundtrackID import app

    times = iter([datetime(2026, 11, 10, 15, 23, 1), datetime(2026, 11, 10, 15, 40)])
    monkeypatch.setattr(app, "datetime", SimpleNamespace(now=lambda: next(times)))

    _analyze(tmp_path, monkeypatch, video=b"first film")
    events = _analyze(tmp_path, monkeypatch, video=b"second film")

    assert events[-1]["results"]["video_name"] == "film_2026-11-10_15-40-00"
    assert events[-1]["results"]["source"] == "film.mp4"
    cache = tmp_path / "cache"
    assert (cache / "film_2026-11-10_15-23-01" / "source.mp4").read_bytes() == (
        b"first film"
    )
    assert (cache / "film_2026-11-10_15-40-00" / "source.mp4").read_bytes() == (
        b"second film"
    )


def test_rerun_is_a_new_run_and_keeps_the_earlier_one(tmp_path, monkeypatch):
    """Named after the film's file, not after the run it re-analyzes, and sharing
    that run's video on disk (a hard link, not a second copy)."""
    from fastapi.testclient import TestClient

    from soundtrackID import app

    times = iter([datetime(2026, 11, 10, 15, 23, 1), datetime(2026, 11, 10, 15, 40)])
    monkeypatch.setattr(app, "datetime", SimpleNamespace(now=lambda: next(times)))
    _analyze(tmp_path, monkeypatch, video=b"film")
    client = TestClient(app.app)

    r = client.post("/rerun/film_2026-11-10_15-23-01", data={"hop": 10})

    events = [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]
    assert events[-1]["results"]["video_name"] == "film_2026-11-10_15-40-00"
    first = tmp_path / "cache" / "film_2026-11-10_15-23-01"
    second = tmp_path / "cache" / "film_2026-11-10_15-40-00"
    assert (first / "results.json").exists()
    assert os.path.samefile(first / "source.mp4", second / "source.mp4")
    # Deleting the earlier run keeps the video of the re-run.
    client.delete("/history/film_2026-11-10_15-23-01")
    assert (second / "source.mp4").read_bytes() == b"film"


def test_analyze_streams_progress_fields_unwrapped(tmp_path, monkeypatch):
    """The SSE events carry the pipeline's fields at their top level."""
    events = _analyze(tmp_path, monkeypatch)

    assert events[-1]["step"] == "done"
    assert [e["duration"] for e in events if e["step"] == "waveform"] == [30.0]
    windows = [e for e in events if e["step"] == "classifying" and "start" in e]
    assert [(e["start"], e["end"], e["is_music"]) for e in windows] == [
        (0.0, 10.0, False),
        (5.0, 15.0, False),
        (10.0, 20.0, True),
        (15.0, 25.0, False),
        (20.0, 30.0, False),
    ]


def clips(*specs):
    """(start, end, title) tuples as (MusicSegment, DetectionResult) pairs."""
    items = []
    for start, end, title in specs:
        seg = MusicSegment(
            audio=np.zeros(int((end - start) * SR), dtype=np.float32),
            start_time=start,
            end_time=end,
            sample_rate=SR,
        )
        det = DetectionResult(segment=seg, title=title, artist=None, confidence=1.0)
        items.append((seg, det))
    return items


def spans_of(groups):
    return [(g[0][0].start_time, g[-1][0].end_time, g[0][1].title) for g in groups]


@pytest.mark.parametrize("title", ["X", None])
def test_grouping_does_not_merge_across_long_non_music(title):
    """Same title (or both unidentified) 25 s apart stays two cues at a 10 s gap."""
    groups = _group_detections(clips((0, 15, title), (40, 55, title)), 10.0)

    assert spans_of(groups) == [(0, 15, title), (40, 55, title)]


def test_grouping_merges_across_short_non_music():
    """Same title 5 s apart becomes one cue at a 10 s gap."""
    groups = _group_detections(clips((0, 15, "X"), (20, 35, "X")), 10.0)

    assert spans_of(groups) == [(0, 35, "X")]


def test_grouping_merges_overlapping_anchored_clip():
    """A run's last clip overlapping the previous one still joins it."""
    groups = _group_detections(clips((5, 20, "X"), (10, 25, "X")), 10.0)

    assert spans_of(groups) == [(5, 25, "X")]


def test_bridging_counts_non_music_between_the_tracks():
    """A 5 s unidentified clip doesn't bridge X to X when they are 15 s apart."""
    groups = _group_detections(clips((0, 15, "X"), (25, 30, None), (30, 45, "X")), 10.0)

    assert spans_of(groups) == [(0, 15, "X"), (25, 30, None), (30, 45, "X")]


def test_build_pipeline_passes_bridge_gap_and_hop(monkeypatch):
    """The bridge gap and hop from the UI reach the writer and the chunker."""
    from soundtrackID import app

    monkeypatch.setattr(app, "_get_classifier", lambda threshold: FakeClassifier())
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client", lambda api: NoMatchClient()
    )

    pipeline = app._build_pipeline(Path("."), bridge_gap=7.0, hop=2.5)

    assert pipeline.output_writer.bridge_gap == 7.0
    assert pipeline.chunker.chunk_duration == FakeClassifier.window_duration
    assert pipeline.chunker.chunk_duration - pipeline.chunker.overlap == 2.5


def test_each_run_classifies_with_its_own_threshold(monkeypatch):
    """Two analyses at the same time share the loaded model, not the threshold."""
    from soundtrackID import app

    monkeypatch.setattr(app, "_classifier", FakeClassifier())

    first = app._get_classifier(threshold=0.2)
    second = app._get_classifier(threshold=0.8)  # starts while the first runs

    assert (first.threshold, second.threshold) == (0.2, 0.8)
    assert first.music_starts is second.music_starts  # the same model underneath
