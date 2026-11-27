"""Client-supplied names (run folders, upload filenames) stay inside their folder."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from soundtrackid.batches import Batch
from soundtrackid.web import analyze, data
from soundtrackid.web.app import app

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


@pytest.mark.parametrize("name", [".", "..", "../cache2"])
def test_a_run_name_outside_the_cache_is_rejected(data_dir, name):
    """`.` is the cache folder itself, `../cache2` a sibling sharing its prefix."""

    with pytest.raises(HTTPException) as exc:
        data.find_run(name)

    assert exc.value.status_code == 400


def test_delete_history_removes_run(runs_dir):
    (runs_dir / "myrun").mkdir()

    r = client.delete("/history/myrun")

    assert r.status_code == 200
    assert not (runs_dir / "myrun").exists()


def test_delete_history_keeps_the_cache_folder(runs_dir):
    """DELETE /history/%2e must not delete every run at once."""
    (runs_dir / "myrun").mkdir()

    r = client.delete("/history/%2e")

    assert r.status_code == 400
    assert (runs_dir / "myrun").is_dir()


def test_a_run_in_a_batch_is_found_by_its_name(runs_dir):
    batch = Batch.create(runs_dir, [("film.mp4", None)])
    run = batch.path / "film_2026-11-11_10-00-00"
    run.mkdir()
    (run / "results.json").write_text(json.dumps({"cues": []}))

    r = client.get("/results/film_2026-11-11_10-00-00")

    assert r.status_code == 200
    assert data.find_run("film_2026-11-11_10-00-00").path == run.resolve()


def test_a_batch_or_its_queue_is_no_run(runs_dir, tmp_path):
    """Deleting one as a run would delete the batch's runs or films at once."""
    video = tmp_path / "film.mp4"
    video.write_bytes(b"video")
    batch = Batch.create(runs_dir, [("film.mp4", None)])
    batch.queue(1, video)

    assert client.delete(f"/history/{batch.name}").status_code == 404
    assert client.delete("/history/queue").status_code == 404
    assert (batch.path / "queue" / "01" / "film.mp4").read_bytes() == b"video"


def test_a_run_that_isnt_there_is_404(runs_dir):
    with pytest.raises(HTTPException) as exc:
        data.find_run("film_2026-11-11_10-00-00")

    assert exc.value.status_code == 404


def test_identify_stages_upload_under_its_base_name(data_dir, monkeypatch):
    """An upload named ../../x.mp4 is staged as x.mp4, not two folders up."""
    staging = data_dir / "staging"
    monkeypatch.setattr(analyze, "_staging", {})

    r = client.post("/identify", files={"file": ("../../x.mp4", b"video", "video/mp4")})

    assert r.status_code == 200
    body = r.json()
    assert body["original_name"] == "x.mp4"
    assert (staging / body["staging_id"] / "x.mp4").read_bytes() == b"video"
    assert not (data_dir / "x.mp4").exists()
