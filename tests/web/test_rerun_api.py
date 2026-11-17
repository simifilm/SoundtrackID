"""Tests for re-running a stored video: POST /rerun/{video_name}."""

import json
import shutil

from fastapi.testclient import TestClient

from soundtrackid.web import analyze
from soundtrackid.web.app import app

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


def test_rerun_uses_stored_video_film_and_new_settings(runs_dir, monkeypatch):
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    (run_dir / "source.mp4").write_bytes(b"video-bytes")
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "film": {"title": "Test", "year": 1950},
                "cues": [],
            }
        )
    )
    calls = []

    async def fake_pipeline_sse(video, **kwargs):
        calls.append((video.read_bytes(), video.name, kwargs))
        shutil.rmtree(video.parent)  # its temporary folder, as the real run does
        yield "data: {}\n\n"

    monkeypatch.setattr(analyze, "_run_pipeline_sse", fake_pipeline_sse)

    r = client.post(
        "/rerun/myrun",
        data={
            "api": "shazam",
            "threshold": "0.3",
            "bridge_gap": "2",
            "max_segment": "30",
            "hop": "2.5",
        },
    )

    assert r.status_code == 200
    [(video_bytes, original_name, kwargs)] = calls
    assert (video_bytes, original_name) == (b"video-bytes", "myrun.mp4")
    assert kwargs["film_override"] == {"title": "Test", "year": 1950}
    assert kwargs["api"] == "shazam"
    assert kwargs["threshold"] == 0.3
    assert kwargs["bridge_gap"] == 2.0
    assert kwargs["max_segment_duration"] == 30.0
    assert kwargs["hop"] == 2.5


def test_rerun_without_stored_video_is_404(runs_dir):
    (runs_dir / "myrun").mkdir()

    r = client.post("/rerun/myrun", data={})

    assert r.status_code == 404
    assert r.json()["detail"] == "No stored video for this run"
