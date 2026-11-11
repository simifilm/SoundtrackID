"""Tests for the pipeline's classification output."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from soundtrackID.aggregator import ChunkAggregator
from soundtrackID.chunker import AudioChunker
from soundtrackID.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackID.detection import ProviderAuthError, RateLimitError
from soundtrackID.isolators import DummyIsolator
from soundtrackID.models import MATCH_KEYS, DetectionResult, MusicSegment
from soundtrackID.pipeline import (
    Pipeline,
    _group_detections,
    waveform_peaks,
)

SR = 1000
LABELS = ["Speech", "Music", "Applause", "Piano"]


class FakeLoader:
    def load(self, path):
        return np.linspace(-0.5, 0.5, 30 * SR, dtype=np.float32), SR


class FakeClassifier:
    """Only the windows starting at `music_starts` are music: they score 0.6,
    the others 0.05."""

    labels = LABELS
    window_duration = 10.0

    def __init__(self, music_starts=(10.0,)):
        self.music_starts = music_starts

    def classify(self, chunk):
        is_music = chunk.start_time in self.music_starts
        probs = np.array(
            [0.3, 0.6, 0.06, 0.04] if is_music else [0.9, 0.05, 0.03, 0.02],
            dtype=np.float32,
        )
        return float(probs[1]), probs


class NoMatchClient:
    def detect(self, segment):
        return DetectionResult(segment=segment, title=None, artist=None)


class TitleByStartClient:
    """Matches "X" for clips starting at the given times, nothing otherwise."""

    def __init__(self, starts):
        self.starts = starts

    def detect(self, segment):
        title = "X" if segment.start_time in self.starts else None
        return DetectionResult(segment=segment, title=title, artist=None)


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
        return DetectionResult(segment=segment, title=None, artist=None)


class FailingClient:
    """Fails with an unexpected error on clips starting at `fail_at`, as on a
    provider's malformed answer; matches "X" otherwise."""

    def __init__(self, fail_at):
        self.fail_at = fail_at

    def detect(self, segment):
        if segment.start_time in self.fail_at:
            raise ValueError("malformed answer")
        return DetectionResult(segment=segment, title="X", artist=None)


def test_the_waveform_is_the_audios_peaks_scaled_to_one():
    """5 points a second, each the loudest sample of its stretch."""
    audio = np.zeros(4 * SR, dtype=np.float32)
    audio[250] = -0.5  # in the 2nd point, 200-400 ms
    audio[3999] = 0.25  # in the last

    waveform = waveform_peaks(audio, SR)

    assert waveform["duration"] == 4.0
    assert len(waveform["samples"]) == 20
    assert (waveform["samples"][1], waveform["samples"][19]) == (1.0, 0.5)
    assert sum(waveform["samples"]) == 1.5


def test_the_waveform_of_silence_is_flat():
    assert waveform_peaks(np.zeros(SR, dtype=np.float32), SR)["samples"] == [0.0] * 5


def test_classification_spans_cover_whole_audio():
    """The results get gap-free music / no-music spans with every class."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(5.0, 10.0, 15.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=NoMatchClient(),
    )

    results = pipeline.run(Path("film.mp4"))

    spans = results.classification
    assert [(s["start"], s["end"], s["is_music"]) for s in spans] == [
        (0.0, 10.0, False),
        (10.0, 20.0, True),
        (20.0, 30.0, False),
    ]
    assert all(set(s["classes"]) == set(LABELS) for s in spans)
    assert spans[1]["classes"]["Music"] > pipeline.threshold


def test_classification_windows_list_top_three_classes():
    """Every classification window is listed with its three most likely classes."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=NoMatchClient(),
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


class AudDLikeClient:
    """Matches every clip, with each field a cue takes from an answer, as
    AudD's (song_link only AudD has), but no score."""

    metadata = {
        key: f"<{key}>"
        for key in MATCH_KEYS
        if key not in ("provider", "title", "artist")
    }

    def detect(self, segment):
        return DetectionResult(segment, "T", "A", "audd", dict(self.metadata))


def test_a_cue_has_the_fields_of_its_answer_and_no_confidence():
    """No made-up score: Shazam and AudD give none, ACRCloud's is in its
    candidates. AudD's song_link is kept like any other field."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(5.0, 10.0, 15.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=AudDLikeClient(),
    )

    [cue] = pipeline.run(Path("film.mp4")).to_dict()["cues"]

    assert "confidence" not in cue
    assert cue["song_link"] == "<song_link>"
    assert {key: cue[key] for key in MATCH_KEYS} == {
        "provider": "audd",
        "title": "T",
        "artist": "A",
        **AudDLikeClient.metadata,
    }


def test_the_pending_cues_come_with_their_audio(tmp_path, monkeypatch):
    """For the run to keep, so a resume can send it again; the pipeline itself
    writes nothing."""
    monkeypatch.chdir(tmp_path)
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=ThrottledClient(answers=2),
        detection_concurrency=1,  # windows are asked in order
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=5.0)

    pending = [c["segment_id"] for c in results.cues if c["pending"]]
    assert pending == ["seg_002", "seg_003", "seg_004", "seg_005"]
    assert sorted(results.pending_audio) == pending
    # The clip the provider would have heard: 10-15 s of the film.
    clip = results.pending_audio["seg_002"]
    audio, _ = FakeLoader().load(None)
    assert (clip.start_time, clip.end_time) == (10.0, 15.0)
    np.testing.assert_array_equal(clip.audio, audio[10 * SR : 15 * SR])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bridge_gap, n_cues", [(10.0, 1), (5.0, 3)])
def test_bridge_gap_limits_bridged_unidentified_clips(bridge_gap, n_cues):
    """X, no match (10 s), X becomes one cue only if 10 s fits the bridge gap."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=TitleByStartClient(starts=(0.0, 20.0)),
        bridge_gap=bridge_gap,
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=10.0)

    assert len(results.cues) == n_cues


def test_rate_limit_skips_the_calls_of_windows_already_queued():
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
        detection_concurrency=1,  # the other windows wait in the queue
    )

    results = pipeline.run(
        Path("film.mp4"), on_progress=on_progress, max_segment_duration=5.0
    )

    assert throttled.calls == 1
    assert results.cues and all(c["pending"] for c in results.cues)
    assert not any(c["title"] for c in results.cues)


def test_windows_skipped_at_a_rate_limit_stay_separate_pending_cues():
    """So a resume identifies each one; windows answered before still merge."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=ThrottledClient(answers=2),
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


def test_a_window_the_provider_failed_on_stays_pending(caplog):
    """The run goes on and keeps what was identified; a resume asks again."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=FailingClient(fail_at=(10.0,)),
        detection_concurrency=1,
    )

    results = pipeline.run(Path("film.mp4"), max_segment_duration=5.0)

    cues = results.to_dict()["cues"]
    assert [(c["start"], c["duration"], c["title"], c["pending"]) for c in cues] == [
        (0.0, 10.0, "X", False),
        (10.0, 5.0, None, True),
        (15.0, 15.0, "X", False),
    ]
    assert list(results.pending_audio) == ["seg_002"]  # kept for a resume
    assert "malformed answer" in caplog.text


class RejectingClient:
    """Rejects its keys, as a provider does with a wrong one."""

    def detect(self, segment):
        raise ProviderAuthError("ACRCloud rejected the API keys (error 3014)")


def test_rejected_keys_stop_the_run():
    """Waiting doesn't fix them, so no pending cues for a resume that would fail
    the same way: the run fails at once and says why."""
    pipeline = Pipeline(
        loader=FakeLoader(),
        chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
        classifier=FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0)),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=RejectingClient(),
    )

    with pytest.raises(ProviderAuthError, match="rejected the API keys"):
        pipeline.run(Path("film.mp4"), max_segment_duration=5.0)


def test_progress_events_carry_their_data_as_fields():
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


def fake_components(monkeypatch, client=None, classifier=None):
    """Make build_pipeline load audio with FakeLoader and identify with `client`
    (default: no match); returns a stand-in for the app's _get_classifier."""
    monkeypatch.setattr("soundtrackID.pipeline.VideoLoader", FakeLoader)
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: client or NoMatchClient(),
    )

    return lambda: classifier or FakeClassifier()


def _analyze(tmp_path, monkeypatch, video=b"video", **form):
    """POST /analyze for film.mp4 with the fake components; returns the SSE events."""
    from fastapi.testclient import TestClient

    from soundtrackID import app

    monkeypatch.setattr(app, "_OUTPUT_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(app, "_staging", {})
    monkeypatch.setattr(app, "_get_classifier", fake_components(monkeypatch))

    client = TestClient(app.app)
    staged = client.post("/identify", files={"file": ("film.mp4", video)}).json()
    r = client.post(
        "/analyze",
        data={"staging_id": staged["staging_id"], "film": '{"title": "F"}', **form},
    )
    return [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]


def test_analyze_saves_its_settings_with_the_results(tmp_path, monkeypatch):
    """Everything the run was analyzed with, also what the form doesn't set."""
    form = {"threshold": "0.3", "hop": "2.5", "bridge_gap": "5", "max_segment": "10"}

    events = _analyze(tmp_path, monkeypatch, **form)

    assert events[-1]["results"]["settings"] == {
        "classifier": "FakeClassifier",
        "threshold": 0.3,
        "window": FakeClassifier.window_duration,
        "hop": 2.5,
        "bridge_gap": 5.0,
        "isolation": False,
        "max_segment_duration": 10.0,
    }


def test_an_analysis_without_settings_uses_the_defaults(tmp_path, monkeypatch):
    """The same as the page's and the CLI's."""
    events = _analyze(tmp_path, monkeypatch)

    results = events[-1]["results"]
    assert results["providers"] == [DEFAULT_API]
    assert results["settings"] == {
        "classifier": "FakeClassifier",
        "threshold": MUSIC_THRESHOLD,
        "window": FakeClassifier.window_duration,
        "hop": CLASSIFICATION_HOP_S,
        "bridge_gap": BRIDGE_GAP_S,
        "isolation": False,
        "max_segment_duration": MAX_SEGMENT_S,
    }


def test_results_are_saved_in_full_when_the_video_cannot_be_stored(
    tmp_path, monkeypatch
):
    """With everything the analysis adds, e.g. the settings and providers."""
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
    assert saved["providers"] == [DEFAULT_API]
    assert "video" not in saved


def test_each_analysis_gets_its_own_run(tmp_path, monkeypatch):
    """Also of a film with the same file name, e.g. the same one again with
    other settings: the run folder carries the time of the analysis."""
    times = iter([datetime(2026, 11, 10, 15, 23, 1), datetime(2026, 11, 10, 15, 40)])
    monkeypatch.setattr(
        "soundtrackID.analysis.datetime", SimpleNamespace(now=lambda: next(times))
    )

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
    monkeypatch.setattr(
        "soundtrackID.analysis.datetime", SimpleNamespace(now=lambda: next(times))
    )
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
        det = DetectionResult(segment=seg, title=title, artist=None)
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

    monkeypatch.setattr(app, "_get_classifier", FakeClassifier)
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client", lambda api: NoMatchClient()
    )

    pipeline = app._build_pipeline(
        threshold=MUSIC_THRESHOLD, api=DEFAULT_API, bridge_gap=7.0, hop=2.5
    )

    assert pipeline.bridge_gap == 7.0
    assert pipeline.chunker.chunk_duration == FakeClassifier.window_duration
    assert pipeline.chunker.chunk_duration - pipeline.chunker.overlap == 2.5


def test_each_run_classifies_with_its_own_threshold():
    """Runs share the loaded model, not the threshold: the classifier only scores
    the windows, the run decides which are music."""
    classifier = FakeClassifier()  # the window at 10 s scores 0.6, the rest 0.05

    def music_windows(threshold):
        pipeline = Pipeline(
            loader=FakeLoader(),
            chunker=AudioChunker(chunk_duration=10.0, overlap=5.0),
            classifier=classifier,
            aggregator=ChunkAggregator(gap_tolerance=1.0),
            isolator=DummyIsolator(),
            detection_client=NoMatchClient(),
            threshold=threshold,
        )
        windows = pipeline.run(Path("film.mp4")).classification_windows
        return [w["start"] for w in windows if w["is_music"]]

    assert music_windows(0.5) == [10.0]
    assert music_windows(0.7) == []
    assert music_windows(0.01) == [0.0, 5.0, 10.0, 15.0, 20.0]
