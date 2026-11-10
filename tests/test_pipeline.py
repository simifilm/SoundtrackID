"""Tests for the pipeline's classification output."""

import json
import threading
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
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


class SameTitleClient:
    def detect(self, segment):
        return DetectionResult(segment=segment, title="X", artist="A", confidence=1.0)


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


def test_cue_audio_skips_overlap_of_anchored_clip(tmp_path):
    """Clips 0-15 s and 5-20 s of one cue give 20 s of audio, not 30 s."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0)),  # run 0-20 s
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=SameTitleClient(),
        output_dir=tmp_path,
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=15.0)

    [cue] = results.segments
    assert (cue.start, cue.duration) == (0.0, 20.0)
    rate, wav = wavfile.read(tmp_path / "film" / cue.audio_file)
    audio, _ = FakeLoader().load(None)
    np.testing.assert_allclose(wav / 32767, audio[: 20 * SR], atol=1e-4)


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


def test_analyze_streams_progress_fields_unwrapped(tmp_path, monkeypatch):
    """The SSE events carry the pipeline's fields at their top level."""
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
    staged = client.post("/identify", files={"file": ("film.mp4", b"video")}).json()
    r = client.post(
        "/analyze",
        data={"staging_id": staged["staging_id"], "film": json.dumps({"title": "F"})},
    )

    events = [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]
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
