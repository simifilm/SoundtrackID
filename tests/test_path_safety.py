"""Client-supplied names (run folders, upload filenames) stay inside their folder."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from soundtrackID import app as app_module

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app_module.app)


@pytest.mark.parametrize("name", [".", "..", "../cache2"])
def test_safe_run_dir_rejects_names_outside_the_cache(tmp_path, monkeypatch, name):
    """`.` is the cache folder itself, `../cache2` a sibling sharing its prefix."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path / "cache")

    with pytest.raises(HTTPException) as exc:
        app_module._safe_run_dir(name)

    assert exc.value.status_code == 400


def test_delete_history_removes_run(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    (tmp_path / "myrun").mkdir()

    r = client.delete("/history/myrun")

    assert r.status_code == 200
    assert not (tmp_path / "myrun").exists()


def test_delete_history_keeps_the_cache_folder(tmp_path, monkeypatch):
    """DELETE /history/%2e must not delete every run at once."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    (tmp_path / "myrun").mkdir()

    r = client.delete("/history/%2e")

    assert r.status_code == 400
    assert (tmp_path / "myrun").is_dir()


def test_identify_stages_upload_under_its_base_name(tmp_path, monkeypatch):
    """An upload named ../../x.mp4 is staged as x.mp4, not two folders up."""
    staging = tmp_path / "app" / "staging"
    monkeypatch.setattr(app_module, "_STAGING_DIR", staging)
    monkeypatch.setattr(app_module, "_staging", {})

    r = client.post("/identify", files={"file": ("../../x.mp4", b"video", "video/mp4")})

    assert r.status_code == 200
    body = r.json()
    assert body["original_name"] == "x.mp4"
    assert (staging / body["staging_id"] / "x.mp4").read_bytes() == b"video"
    assert not (tmp_path / "app" / "x.mp4").exists()
