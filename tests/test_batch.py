"""A batch (analysis.analyze_batch, batches.Batch): several films analyzed one
after another, each a run in the batch's folder."""

import json
import threading

import pytest

from soundtrackid import analysis
from soundtrackid.detection import ProviderAuthError
from soundtrackid.models import DetectionResult
from soundtrackid.pipeline import build_pipeline
from tests.fakes import FakeClassifier, FakeLoader, RejectingClient, fake_components


def _batch(tmp_path, monkeypatch, names, client=None, broken=(), **options):
    """Analyze films of these file names as a batch into tmp_path/runs; those
    `broken` can't be read. Each is 30 s of music, which the provider is asked
    about at once. Returns the batch's record and the progress."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)

    class Loader(FakeLoader):
        def load(self, path):
            if path.name in broken:
                raise ValueError(f"Could not read the audio of {path.name}")
            return super().load(path)

    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", Loader)
    films = []
    for name in names:
        video = tmp_path / name
        video.write_bytes(name.encode())
        films.append((video, {"title": video.stem}))
    events = []
    record = analysis.analyze_batch(
        build_pipeline(get_classifier(), "acrcloud"),
        films,
        "acrcloud",
        tmp_path / "runs",
        on_progress=lambda step, **fields: events.append((step, fields)),
        **options,
    )
    return record, events


def _statuses(record):
    return [film["status"] for film in record["films"]]


def _saved(tmp_path):
    """The record the batch saved, and its folder."""
    [folder] = (tmp_path / "runs").iterdir()
    return json.loads((folder / "batch.json").read_text()), folder


def test_each_film_is_a_run_in_the_batchs_folder(tmp_path, monkeypatch):
    record, _ = _batch(tmp_path, monkeypatch, ["a.mp4", "b.mp4"])

    saved, folder = _saved(tmp_path)
    assert folder.name.startswith("batch_")
    assert record == {"name": folder.name, **saved}
    assert [(f["source"], f["status"]) for f in saved["films"]] == [
        ("a.mp4", "done"),
        ("b.mp4", "done"),
    ]
    for film in saved["films"]:
        results = json.loads((folder / film["run"] / "results.json").read_text())
        assert results["film"]["title"] == film["film"]["title"]
    # Not kept: the films stay where they are.
    assert (tmp_path / "a.mp4").read_bytes() == b"a.mp4"


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
    cancel = threading.Event()
    calls = []

    class Client:
        def detect(self, segment):
            calls.append(segment)
            if len(calls) == 2:  # the second film's one stretch of music
                cancel.set()
            return DetectionResult(segment=segment, title=None, artist=None)

    with pytest.raises(analysis.Cancelled):
        _batch(
            tmp_path,
            monkeypatch,
            ["a.mp4", "b.mp4", "c.mp4"],
            client=Client(),
            cancel=cancel,
        )

    saved, folder = _saved(tmp_path)
    assert _statuses(saved) == ["done", "cancelled", "queued"]
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["batch.json", saved["films"][0]["run"]]
    )


def test_kept_videos_go_into_their_runs_and_the_rest_away(tmp_path, monkeypatch):
    """As the app's uploads: each waits in the batch's queue until its turn."""
    record, _ = _batch(
        tmp_path,
        monkeypatch,
        ["a.mp4", "b.mp4"],
        broken=["b.mp4"],
        keep_videos=True,
    )

    _, folder = _saved(tmp_path)
    run = folder / record["films"][0]["run"]
    assert (run / "source.mp4").read_bytes() == b"a.mp4"
    assert sorted(p.name for p in folder.iterdir()) == sorted(["batch.json", run.name])
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
