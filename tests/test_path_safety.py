"""Client-supplied names (run folders, upload filenames) stay inside their folder."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from soundtrackID.web import analyze, data
from soundtrackID.web.app import app

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
