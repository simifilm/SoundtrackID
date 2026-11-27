"""Tests for batches in the app: starting one (POST /batches), listing them
with their films (GET /history, /batches/{name}), deleting one (DELETE
/batches/{name}), continuing and resuming one, and downloading its results."""

import io
import json
import os
import threading
import zipfile
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from soundtrackid.batches import Batch
from soundtrackid.locks import FolderLock
from soundtrackid.web import analyze, jobs
from soundtrackid.web.app import app
from tests.fakes import BATCH_SETTINGS, FakeClassifier, FakeLoader, fake_components
from tests.jobs import job_events
from tests.media import write_video

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
    as a done film's. Their files are where the command line left them, but
    gone."""
    batch = Batch.create(
        runs_dir, [(runs_dir / source, None) for source, _, _ in films], BATCH_SETTINGS
    )
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
        "pending": 0,
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
    with _under_way("batch", batch.name):
        r = client.delete(f"/batches/{batch.name}")
        client.delete("/history")

        assert r.status_code == 409
        assert batch.exists()


@contextmanager
def _under_way(kind, name):
    """A job of this kind, of this batch or run, under way meanwhile."""
    gate = threading.Event()
    job = jobs.start(kind, lambda on_progress, cancel: gate.wait(10), name)
    try:
        yield
    finally:
        gate.set()
        client.get(f"/jobs/{job.id}/events")  # once it ended


def test_a_batch_is_continued_with_its_settings(data_dir, monkeypatch):
    """Its films not done, from their files the batch kept."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    monkeypatch.setattr(
        analyze, "get_classifier", fake_components(monkeypatch, None, classifier)
    )

    class Loader(FakeLoader):
        def load(self, path):
            if path.name == "b.mp4":
                raise ValueError("Could not read the audio")
            return super().load(path)

    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", Loader)
    films = [{"staging_id": _stage(name)} for name in ("a.mp4", "b.mp4")]
    r = client.post("/batches", data={"films": json.dumps(films), "hop": "2.5"})
    batch = Batch(data_dir / "cache" / job_events(client, r)[-1]["results"]["name"])
    assert [f["status"] for f in batch.load()["films"]] == ["done", "failed"]
    assert client.get(f"/batches/{batch.name}").json()["to_continue"] == 1
    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", FakeLoader)

    r = client.post(f"/batches/{batch.name}/continue")

    events = job_events(client, r)
    assert [f["status"] for f in events[-1]["results"]["films"]] == ["done", "done"]
    run = batch.path / events[-1]["results"]["films"][1]["run"]
    assert (run / "source.mp4").read_bytes() == b"b.mp4"
    assert json.loads((run / "results.json").read_text())["settings"]["hop"] == 2.5


def test_a_batch_another_process_analyzes_is_under_way(runs_dir):
    """E.g. the command line, which holds its lock: not to continue, resume
    or delete meanwhile."""
    batch = _batch(runs_dir, ("a.mp4", "analyzing", None), ("b.mp4", "queued", None))
    (runs_dir / "b.mp4").write_bytes(b"video")
    with FolderLock(batch.path):
        entry = client.get(f"/batches/{batch.name}").json()
        actions = [
            client.post(f"/batches/{batch.name}/continue"),
            client.post(f"/batches/{batch.name}/resume"),
            client.delete(f"/batches/{batch.name}"),
        ]
        client.delete("/history")

        assert entry["under_way"] is True
        assert [f["status"] for f in entry["films"]] == ["analyzing", "queued"]
        assert entry["to_continue"] == 0
        assert [r.status_code for r in actions] == [409, 409, 409]
        assert batch.exists()

    entry = client.get(f"/batches/{batch.name}").json()
    assert (entry["under_way"], entry["to_continue"]) == (False, 1)


def test_a_batch_under_way_is_not_continued_or_resumed(runs_dir):
    batch = _batch(runs_dir, ("a.mp4", "analyzing", None))
    for kind in ("batch", "batch_resume"):
        with _under_way(kind, batch.name):
            for action in ("continue", "resume"):
                r = client.post(f"/batches/{batch.name}/{action}")

                assert r.status_code == 409


def test_a_batch_is_resumed_run_by_run(runs_dir, monkeypatch):
    batch = _batch(runs_dir, ("a.mp4", "done", "a_2026-11-11_10-00-00"))
    resumed = []
    monkeypatch.setattr(
        analyze.analysis,
        "resume_batch",
        lambda batch, **kwargs: resumed.append(batch.name) or {},
    )

    r = client.post(f"/batches/{batch.name}/resume")

    assert job_events(client, r)[-1]["step"] == "done"
    assert resumed == [batch.name]


def test_a_run_is_resumed_once_at_a_time(runs_dir):
    """Also not while its batch is."""
    batch = _batch(runs_dir, ("a.mp4", "done", "a_2026-11-11_10-00-00"))
    with _under_way("batch_resume", batch.name):
        assert client.post("/resume/a_2026-11-11_10-00-00").status_code == 409
    with _under_way("resume", "a_2026-11-11_10-00-00"):
        assert client.post("/resume/a_2026-11-11_10-00-00").status_code == 409
        assert client.post(f"/batches/{batch.name}/resume").status_code == 409


def test_a_batch_says_what_its_films_need(runs_dir):
    """Its runs' pending cues, to resume, and the films whose files it has, to
    continue; and its providers, whose keys that takes."""
    batch = _batch(
        runs_dir,
        ("a.mp4", "done", "a_2026-11-11_10-00-00"),
        ("b.mp4", "failed", None),
        ("c.mp4", "cancelled", None),
    )
    cues = [{"title": None, "pending": True, "audio_file": "segments/s.wav"}]
    run = batch.path / "a_2026-11-11_10-00-00"
    (run / "results.json").write_text(json.dumps({"cues": cues}))
    (runs_dir / "c.mp4").write_bytes(b"video")  # b's is gone

    entry = client.get(f"/batches/{batch.name}").json()

    assert entry["films"][0]["run"]["pending"] == 1
    assert entry["to_continue"] == 1
    assert entry["providers"] == [BATCH_SETTINGS["api"]]
    assert client.get("/batches/nope").status_code == 404


# Each format: a run's own download or export of it, and its file's name.
SINGLE = {
    "json": ("/results/{}", "{}_results.json"),
    "csv": ("/output/{}/csv", "{}_results.csv"),
    "vian": ("/output/{}/vian", "{}_vian_project.zip"),
    "mediapkg": ("/output/{}/mediapkg", "{}.mediapkg"),
}


def _exported_batch(runs_dir):
    """A batch of two runs with a cue each, and one failed film; only the
    first run kept its video."""
    batch = _batch(
        runs_dir,
        ("a.mp4", "done", "a_2026-11-11_10-00-00"),
        ("b.mp4", "failed", None),
        ("c.mp4", "done", "c_2026-11-11_10-10-00"),
    )
    cue = {"segment_id": "seg_001", "start": 0.5, "duration": 1.0, "title": "Song"}
    for run in ("a_2026-11-11_10-00-00", "c_2026-11-11_10-10-00"):
        results = {"source": run[0] + ".mp4", "cues": [cue]}
        (batch.path / run / "results.json").write_text(json.dumps(results))
    write_video(batch.path / "a_2026-11-11_10-00-00" / "source.mp4", seconds=2)
    return batch


def _members(content):
    """A zip's files, by name."""
    archive = zipfile.ZipFile(io.BytesIO(content))
    return {name: archive.read(name) for name in archive.namelist()}


def _said(fmt, export):
    """What a VIAN or MAVA export says, without when it was made or the IDs
    made up for it."""
    files = _members(export)
    if fmt == "mediapkg":
        manifest = json.loads(files.pop("manifest.json"))
        del manifest["created"]
        return manifest, files
    [timeline] = json.loads(files["undoable.json"])["timelines"]
    segments = [(s["start"], s["end"], s["annotation"]) for s in timeline["data"]]
    return files["main.json"], segments


@pytest.mark.parametrize("fmt", SINGLE)
def test_a_batchs_results_download_together(runs_dir, fmt):
    """Each run's as its own download or export has it, in a zip; VIAN's only
    of the runs that kept their video, which its frames are counted in."""
    batch = _exported_batch(runs_dir)

    r = client.get(f"/batches/{batch.name}/results/{fmt}")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert (
        f'filename="{batch.name}_results_{fmt}.zip"' in r.headers["content-disposition"]
    )
    files = _members(r.content)
    runs = ["a_2026-11-11_10-00-00"]
    if fmt != "vian":
        runs.append("c_2026-11-11_10-10-00")
    url, file_name = SINGLE[fmt]
    assert list(files) == [file_name.format(run) for run in runs]
    for run in runs:
        single = client.get(url.format(run))
        content = files[file_name.format(run)]
        if fmt == "json":
            assert json.loads(content) == single.json()
        elif fmt == "csv":
            assert content == single.content
        else:
            assert _said(fmt, content) == _said(fmt, single.content)


def test_a_batchs_downloads_need_something_to_hold(runs_dir):
    batch = _exported_batch(runs_dir)
    (batch.path / "a_2026-11-11_10-00-00" / "source.mp4").unlink()

    r = client.get(f"/batches/{batch.name}/results/vian")

    assert r.status_code == 404
    assert r.json()["detail"] == "No stored video for the batch's runs"
    assert client.get(f"/batches/{batch.name}/results/xml").status_code == 404
