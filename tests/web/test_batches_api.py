"""Tests for batches in the app: starting one (POST /batches), listing them
with their films (GET /history) and deleting one (DELETE /batches/{name})."""

import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

from soundtrackid.batches import Batch
from soundtrackid.web import analyze, jobs
from soundtrackid.web.app import app
from tests.fakes import FakeClassifier, fake_components
from tests.jobs import job_events

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


@pytest.fixture(autouse=True)
def no_other_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(analyze, "_staging", {})


def _stage(name):
    r = client.post("/identify", files={"file": (name, name.encode())})
    return r.json()["staging_id"]


def _batch(runs_dir, *films):
    """A batch on disk: `films` are (source, status, run); a run named is made,
    as a done film's."""
    batch = Batch.create(runs_dir, [(source, None) for source, _, _ in films])
    record = batch.load()
    for entry, (_, status, run) in zip(record["films"], films):
        entry.update(status=status, run=run)
        if status == "failed":
            entry["error"] = "Could not read the audio"
        if run:
            (batch.path / run).mkdir()
            cues = [{"title": "Song"}, {"title": None}]
            (batch.path / run / "results.json").write_text(json.dumps({"cues": cues}))
    batch.save(record)
    return batch


def test_a_batch_analyzes_the_staged_uploads_one_after_another(data_dir, monkeypatch):
    """Each a run in the batch's folder, with its film and its video."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    monkeypatch.setattr(
        analyze, "get_classifier", fake_components(monkeypatch, None, classifier)
    )
    films = [
        {"staging_id": _stage("a.mp4"), "film": {"title": "A", "year": 1950}},
        {"staging_id": _stage("b.mp4"), "film": {"title": "", "year": None}},
    ]

    r = client.post("/batches", data={"films": json.dumps(films), "hop": "2.5"})

    events = job_events(client, r)
    record = events[-1]["results"]
    assert [e["films"][0]["status"] for e in events if e["step"] == "batch"][:2] == [
        "queued",
        "analyzing",
    ]
    assert [(f["source"], f["status"]) for f in record["films"]] == [
        ("a.mp4", "done"),
        ("b.mp4", "done"),
    ]
    assert [f["film"] for f in record["films"]] == [{"title": "A", "year": 1950}, None]
    folder = data_dir / "cache" / record["name"]
    run = folder / record["films"][0]["run"]
    assert (run / "source.mp4").read_bytes() == b"a.mp4"
    assert json.loads((run / "results.json").read_text())["settings"]["hop"] == 2.5
    assert list((data_dir / "staging").iterdir()) == []
    [entry] = client.get("/history").json()
    assert entry["kind"] == "batch"
    assert [f["run"]["video_name"] for f in entry["films"]] == [
        f["run"] for f in record["films"]
    ]


def test_a_batch_with_an_upload_not_staged_takes_none(data_dir):
    """So the page can try again with the others."""
    staged = _stage("a.mp4")
    films = [{"staging_id": staged}, {"staging_id": "expired"}]

    r = client.post("/batches", data={"films": json.dumps(films)})

    assert r.status_code == 400
    assert r.json()["detail"] == "Staged upload not found or expired"
    assert staged in analyze._staging
    assert not (data_dir / "cache").exists()


@pytest.mark.parametrize("films", ["[]", "not json", '["a"]', '[{"film": {}}]'])
def test_a_batch_needs_its_films(data_dir, films):
    assert client.post("/batches", data={"films": films}).status_code == 422


def test_the_history_says_what_became_of_each_film(runs_dir):
    """Also of one whose run was deleted, or one the batch didn't get to."""
    batch = _batch(
        runs_dir,
        ("a.mp4", "done", "a_2026-11-11_10-00-00"),
        ("b.mp4", "done", "b_2026-11-11_10-10-00"),
        ("c.mp4", "failed", None),
        ("d.mp4", "cancelled", None),
        ("e.mp4", "analyzing", None),
        ("f.mp4", "queued", None),
    )
    assert client.delete("/history/b_2026-11-11_10-10-00").status_code == 200

    [entry] = client.get("/history").json()

    assert entry["name"] == batch.name
    assert [(f["source"], f["status"]) for f in entry["films"]] == [
        ("a.mp4", "done"),
        ("b.mp4", "deleted"),
        ("c.mp4", "failed"),
        ("d.mp4", "cancelled"),
        ("e.mp4", "not_analyzed"),
        ("f.mp4", "not_analyzed"),
    ]
    a, _, c, *_ = entry["films"]
    assert {k: v for k, v in a["run"].items() if k != "timestamp"} == {
        "video_name": "a_2026-11-11_10-00-00",
        "total": 2,
        "identified": 1,
    }
    assert c["error"] == "Could not read the audio"


def test_the_films_of_a_batch_under_way_are_queued_or_analyzing(runs_dir):
    batch = _batch(runs_dir, ("a.mp4", "analyzing", None), ("b.mp4", "queued", None))
    gate = threading.Event()
    job = jobs.start("batch", lambda on_progress, cancel: gate.wait(10), batch.name)
    try:
        [entry] = client.get("/history").json()

        assert [f["status"] for f in entry["films"]] == ["analyzing", "queued"]
    finally:
        gate.set()
        client.get(f"/jobs/{job.id}/events")  # once it ended


def test_a_batch_is_listed_as_of_its_runs_latest_change(runs_dir):
    """Among the runs, as a run is as of its own."""
    batch = _batch(runs_dir, ("a.mp4", "done", "a_2026-11-11_10-00-00"))
    for name in ("older", "newer"):
        (runs_dir / name).mkdir()
        (runs_dir / name / "results.json").write_text('{"cues": []}')
    os.utime(runs_dir / "older", (1000, 1000))
    os.utime(batch.path, (2000, 2000))
    os.utime(runs_dir / "newer", (3000, 3000))
    os.utime(batch.path / "a_2026-11-11_10-00-00", (4000, 4000))  # edited last

    entries = client.get("/history").json()

    names = [e.get("name") or e.get("video_name") for e in entries]
    assert names == [batch.name, "newer", "older"]


def test_deleting_a_batch_deletes_its_runs(runs_dir):
    batch = _batch(runs_dir, ("a.mp4", "done", "a_2026-11-11_10-00-00"))
    (runs_dir / "single").mkdir()

    r = client.delete(f"/batches/{batch.name}")

    assert r.status_code == 200
    assert [p.name for p in runs_dir.iterdir()] == ["single"]


def test_only_a_batch_is_deleted_as_one(runs_dir):
    (runs_dir / "single").mkdir()

    assert client.delete("/batches/single").status_code == 404
    assert client.delete("/batches/%2e").status_code == 400
    assert (runs_dir / "single").is_dir()


def test_a_batch_under_way_is_not_deleted(runs_dir):
    batch = _batch(runs_dir, ("a.mp4", "analyzing", None))
    gate = threading.Event()
    job = jobs.start("batch", lambda on_progress, cancel: gate.wait(10), batch.name)
    try:
        r = client.delete(f"/batches/{batch.name}")

        assert r.status_code == 409
        assert batch.exists()
    finally:
        gate.set()
        client.get(f"/jobs/{job.id}/events")  # once it ended
