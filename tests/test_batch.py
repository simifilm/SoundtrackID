"""A batch (analysis.analyze_batch, batches.Batch): several films analyzed one
after another, each a run in the batch's folder; continued, and resumed
(analysis.resume_batch)."""

import json
import threading
from types import SimpleNamespace

import pytest

from soundtrackid import analysis
from soundtrackid.batches import Batch
from soundtrackid.detection import ProviderAuthError
from soundtrackid.locks import LOCK_FILE, Busy
from soundtrackid.models import DetectionResult
from soundtrackid.runs import Run
from tests.fakes import (
    BATCH_SETTINGS,
    FakeClassifier,
    FakeLoader,
    RejectingClient,
    ThrottledClient,
    fake_components,
)


def _pipeline(monkeypatch, client=None, broken=()):
    """The pipeline of BATCH_SETTINGS, which hears 30 s of music in each film
    and asks the provider about it at once; the films `broken` can't be read."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)

    class Loader(FakeLoader):
        def load(self, path):
            if path.name in broken:
                raise ValueError(f"Could not read the audio of {path.name}")
            return super().load(path)

    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", Loader)
    return analysis.pipeline_for(get_classifier(), BATCH_SETTINGS)


def _create(tmp_path, names, take_files=False):
    """A new batch in tmp_path/runs of films of these file names in tmp_path,
    each its name as its content."""
    videos = [tmp_path / name for name in names]
    for video in videos:
        video.write_bytes(video.name.encode())
    return Batch.create(
        tmp_path / "runs",
        [(video, {"title": video.stem}) for video in videos],
        BATCH_SETTINGS,
        take_files=take_files,
    )


def _analyze(batch, pipeline, cancel=None):
    """Analyze the batch; returns its record and the progress."""
    events = []
    record = analysis.analyze_batch(
        pipeline,
        batch,
        on_progress=lambda step, **fields: events.append((step, fields)),
        cancel=cancel,
    )
    return record, events


def _batch(tmp_path, monkeypatch, names, client=None, broken=(), **options):
    """Analyze films of these file names as a new batch; returns its record
    and the progress."""
    pipeline = _pipeline(monkeypatch, client, broken)
    cancel = options.pop("cancel", None)
    return _analyze(_create(tmp_path, names, **options), pipeline, cancel)


def _statuses(record):
    return [film["status"] for film in record["films"]]


def _saved(tmp_path):
    """The record the batch saved, and its folder."""
    [folder] = (tmp_path / "runs").iterdir()
    return json.loads((folder / "batch.json").read_text()), folder


def _cancelled_at_the_second_film(tmp_path, monkeypatch, names):
    cancel = threading.Event()
    calls = []

    class Client:
        def detect(self, segment):
            calls.append(segment)
            if len(calls) == 2:  # the second film's one stretch of music
                cancel.set()
            return DetectionResult(segment=segment, title=None, artist=None)

    with pytest.raises(analysis.Cancelled):
        _batch(tmp_path, monkeypatch, names, client=Client(), cancel=cancel)


def test_each_film_is_a_run_in_the_batchs_folder(tmp_path, monkeypatch):
    record, _ = _batch(tmp_path, monkeypatch, ["a.mp4", "b.mp4"])

    saved, folder = _saved(tmp_path)
    assert folder.name.startswith("batch_")
    assert record == {"name": folder.name, **saved}
    assert saved["settings"] == BATCH_SETTINGS
    assert [(f["source"], f["status"]) for f in saved["films"]] == [
        ("a.mp4", "done"),
        ("b.mp4", "done"),
    ]
    for film in saved["films"]:
        results = json.loads((folder / film["run"] / "results.json").read_text())
        assert results["film"]["title"] == film["film"]["title"]
    # Not taken over: the films stay where they are, and the batch knows where.
    assert (tmp_path / "a.mp4").read_bytes() == b"a.mp4"
    assert saved["films"][0]["path"] == str((tmp_path / "a.mp4").resolve())


def test_a_film_that_fails_is_recorded_and_the_batch_goes_on(tmp_path, monkeypatch):
    record, _ = _batch(
        tmp_path, monkeypatch, ["a.mp4", "b.mp4", "c.mp4"], broken=["b.mp4"]
    )

    a, b, c = record["films"]
    assert _statuses(record) == ["done", "failed", "done"]
    assert (b["error"], b["run"]) == ("Could not read the audio of b.mp4", None)
    _, folder = _saved(tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["batch.json", a["run"], c["run"]]
    )


def test_wrong_api_keys_stop_the_batch(tmp_path, monkeypatch):
    """They would fail every film after."""
    with pytest.raises(ProviderAuthError):
        _batch(tmp_path, monkeypatch, ["a.mp4", "b.mp4"], client=RejectingClient())

    saved, folder = _saved(tmp_path)
    assert _statuses(saved) == ["failed", "queued"]
    assert "rejected the API keys" in saved["films"][0]["error"]
    assert [p.name for p in folder.iterdir()] == ["batch.json"]


def test_a_cancel_stops_the_batch_and_keeps_the_runs_done(tmp_path, monkeypatch):
    _cancelled_at_the_second_film(tmp_path, monkeypatch, ["a.mp4", "b.mp4", "c.mp4"])

    saved, folder = _saved(tmp_path)
    assert _statuses(saved) == ["done", "cancelled", "queued"]
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["batch.json", saved["films"][0]["run"]]
    )


def test_films_taken_over_wait_in_the_queue_until_their_run_has_them(
    tmp_path, monkeypatch
):
    """As the app's uploads; one that failed stays, to be tried again."""
    record, _ = _batch(
        tmp_path, monkeypatch, ["a.mp4", "b.mp4"], broken=["b.mp4"], take_files=True
    )

    _, folder = _saved(tmp_path)
    run = folder / record["films"][0]["run"]
    assert (run / "source.mp4").read_bytes() == b"a.mp4"
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["batch.json", "queue", run.name]
    )
    assert [p.name for p in (folder / "queue").iterdir()] == ["02"]
    assert (folder / "queue" / "02" / "b.mp4").read_bytes() == b"b.mp4"
    assert [film["path"] for film in record["films"]] == [None, None]
    assert not (tmp_path / "a.mp4").exists()
    assert not (tmp_path / "b.mp4").exists()


def test_the_batch_reports_its_films_as_they_go(tmp_path, monkeypatch):
    """With each film's own progress in between."""
    record, events = _batch(tmp_path, monkeypatch, ["a.mp4", "b.mp4"])

    reported = [
        _statuses(fields) if step == "batch" else step
        for step, fields in events
        if step in ("batch", "loading")
    ]
    assert reported == [
        ["queued", "queued"],
        ["analyzing", "queued"],
        "loading",
        "loading",
        ["done", "queued"],
        ["done", "analyzing"],
        "loading",
        "loading",
        ["done", "done"],
    ]
    assert all(f["name"] == record["name"] for s, f in events if s == "batch")


def test_continuing_analyzes_the_films_the_batch_didnt_get_to(tmp_path, monkeypatch):
    """The runs done stay as they are."""
    _cancelled_at_the_second_film(tmp_path, monkeypatch, ["a.mp4", "b.mp4", "c.mp4"])
    before, folder = _saved(tmp_path)

    record, events = _analyze(Batch(folder), _pipeline(monkeypatch))

    assert _statuses(record) == ["done", "done", "done"]
    assert record["films"][0]["run"] == before["films"][0]["run"]
    assert [_statuses(f) for s, f in events if s == "batch"][0] == [
        "done",
        "queued",
        "queued",
    ]
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["batch.json", *(f["run"] for f in record["films"])]
    )


def test_continuing_tries_the_films_that_failed_again(tmp_path, monkeypatch):
    """E.g. with the API keys put right; their files waited in the queue."""
    with pytest.raises(ProviderAuthError):
        _batch(
            tmp_path,
            monkeypatch,
            ["a.mp4", "b.mp4"],
            client=RejectingClient(),
            take_files=True,
        )
    _, folder = _saved(tmp_path)

    record, _ = _analyze(Batch(folder), _pipeline(monkeypatch))

    assert _statuses(record) == ["done", "done"]
    assert [f["error"] for f in record["films"]] == [None, None]
    run = folder / record["films"][0]["run"]
    assert (run / "source.mp4").read_bytes() == b"a.mp4"
    assert not (folder / "queue").exists()


def test_continuing_analyzes_a_film_left_unfinished_again(tmp_path, monkeypatch):
    """As the app closing during it leaves it: being analyzed, with a run not
    saved, which goes."""
    batch = _create(tmp_path, ["a.mp4"])
    record = batch.load()
    record["films"][0]["status"] = "analyzing"
    batch.save(record)
    unfinished = batch.path / "a_2026-11-11_10-00-00"
    (unfinished / "segments").mkdir(parents=True)

    record, _ = _analyze(batch, _pipeline(monkeypatch))

    assert _statuses(record) == ["done"]
    assert not unfinished.exists()


def test_a_batch_and_its_film_are_locked_while_analyzed(tmp_path, monkeypatch):
    """So another process sees them under way, and can't analyze the batch
    too; their locks go with the batch's end."""
    batch = _create(tmp_path, ["a.mp4"])
    pipeline = _pipeline(monkeypatch)
    seen = []

    def on_progress(step, **fields):
        if step == "loading" and not seen:
            [run] = [p for p in batch.path.iterdir() if p.is_dir()]
            seen.extend([batch.under_way(), Run(run).under_way()])
            with pytest.raises(Busy):
                analysis.analyze_batch(pipeline, batch)

    record = analysis.analyze_batch(pipeline, batch, on_progress=on_progress)

    assert seen == [True, True]
    assert not batch.under_way()
    assert sorted(p.name for p in batch.path.iterdir()) == sorted(
        ["batch.json", record["films"][0]["run"]]
    )
    assert not (batch.path / record["films"][0]["run"] / LOCK_FILE).exists()


def test_a_film_whose_file_is_gone_fails(tmp_path, monkeypatch):
    batch = _create(tmp_path, ["a.mp4", "b.mp4"])
    (tmp_path / "a.mp4").unlink()

    record, _ = _analyze(batch, _pipeline(monkeypatch))

    assert _statuses(record) == ["failed", "done"]
    assert record["films"][0]["error"] == (
        f"Its file is gone: {(tmp_path / 'a.mp4').resolve()}"
    )


def test_resuming_a_batch_resumes_each_run_with_pending_cues(tmp_path, monkeypatch):
    record, _ = _batch(
        tmp_path,
        monkeypatch,
        ["a.mp4", "b.mp4", "c.mp4"],
        client=ThrottledClient(answers=1),  # the first film's, then the limit
    )
    _, folder = _saved(tmp_path)
    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client",
        lambda api: SimpleNamespace(
            detect=lambda seg: DetectionResult(seg, "Song", "A", "acrcloud")
        ),
    )
    events = []

    analysis.resume_batch(
        Batch(folder), on_progress=lambda step, **fields: events.append((step, fields))
    )

    a, b, c = (f["run"] for f in record["films"])
    assert [
        (f["run"], f["number"], f["total"]) for s, f in events if s == "batch_resume"
    ] == [
        (b, 1, 2),
        (c, 2, 2),
    ]
    for run in (b, c):
        cues = json.loads((folder / run / "results.json").read_text())["cues"]
        assert [(cue["title"], cue["pending"]) for cue in cues] == [("Song", False)]
    cues = json.loads((folder / a / "results.json").read_text())["cues"]
    assert [cue["title"] for cue in cues] == [None]  # answered: no match
