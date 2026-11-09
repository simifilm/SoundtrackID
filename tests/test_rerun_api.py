"""Tests for re-running a stored video: POST /rerun/{video_name}."""

import json

from fastapi.testclient import TestClient

from soundtrackID import app as app_module

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app_module.app)


def test_rerun_uses_stored_video_film_and_new_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    (run_dir / "source.mp4").write_bytes(b"video-bytes")
    (run_dir / "results.json").write_text(json.dumps({
        "film": {"title": "Test", "year": 1950},
        "cues": [],
    }))
    calls = []

    async def fake_pipeline_sse(video_bytes, original_name, **kwargs):
        calls.append((video_bytes, original_name, kwargs))
        yield "data: {}\n\n"

    monkeypatch.setattr(app_module, "_run_pipeline_sse", fake_pipeline_sse)

    r = client.post("/rerun/myrun", data={
        "api": "shazam", "threshold": "0.3", "bridge_gap": "2", "max_segment": "30",
        "hop": "2.5",
    })

    assert r.status_code == 200
    [(video_bytes, original_name, kwargs)] = calls
    assert (video_bytes, original_name) == (b"video-bytes", "myrun.mp4")
    assert kwargs["film_override"] == {"title": "Test", "year": 1950}
    assert kwargs["api"] == "shazam"
    assert kwargs["threshold"] == 0.3
    assert kwargs["bridge_gap"] == 2.0
    assert kwargs["max_segment_duration"] == 30.0
    assert kwargs["hop"] == 2.5


def test_rerun_without_stored_video_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    (tmp_path / "myrun").mkdir()

    r = client.post("/rerun/myrun", data={})

    assert r.status_code == 404
    assert r.json()["detail"] == "No stored video for this run"
