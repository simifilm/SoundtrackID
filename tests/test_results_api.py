"""Tests for the manual-edit save endpoint POST /results/{video_name}."""

import json

import pytest
from fastapi.testclient import TestClient

from soundtrackID import app as app_module

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app_module.app)


def _make_run(tmp_path, name="myrun"):
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "results.json").write_text(json.dumps({
        "ID": "abc",
        "film": {"title": "Test", "year": 1950},
        "waveform": {"samples": [1, 2, 3]},
        "cues": [{"segment_id": "seg_001", "title": "Old", "artist": "A"}],
    }))
    return run_dir


def test_save_replaces_cues_and_preserves_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)

    new_cues = [
        {"segment_id": "seg_001", "title": "New Title", "artist": "A",
         "enrichment": {"composer": "Somebody", "original_year": 1916}},
    ]
    r = client.post("/results/myrun", json={"cues": new_cues})
    assert r.status_code == 200
    assert r.json()["cues"] == 1

    saved = json.loads((run_dir / "results.json").read_text())
    assert saved["cues"] == new_cues            # cues replaced
    assert saved["film"] == {"title": "Test", "year": 1950}   # preserved
    assert saved["waveform"] == {"samples": [1, 2, 3]}        # preserved


def test_save_can_remove_all_cues(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)
    r = client.post("/results/myrun", json={"cues": []})
    assert r.status_code == 200
    assert json.loads((run_dir / "results.json").read_text())["cues"] == []


def test_missing_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.post("/results/nope", json={"cues": []})
    assert r.status_code == 404


def test_bad_body_is_422(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    _make_run(tmp_path)
    r = client.post("/results/myrun", json={"not_cues": 1})
    assert r.status_code == 422


@pytest.mark.parametrize("name", ["..%2f..%2fevil", "%2e%2e%2fetc"])
def test_path_traversal_rejected(tmp_path, monkeypatch, name):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.post(f"/results/{name}", json={"cues": []})
    assert r.status_code in (400, 404)


# ── Resume endpoint ────────────────────────────────────────────────────────

def test_resume_missing_run_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.post("/resume/nope", json={})
    assert r.status_code == 404


def test_resume_no_pending_clears_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run = tmp_path / "r"; run.mkdir()
    (run / "results.json").write_text(json.dumps({
        "rate_limited": True,
        "cues": [{"segment_id": "s1", "title": "X", "audio_file": "segments/s1.wav"}],
    }))
    r = client.post("/resume/r", json={})
    assert r.status_code == 200
    assert '"step": "done"' in r.text
    saved = json.loads((run / "results.json").read_text())
    assert saved["rate_limited"] is False  # nothing pending -> resolved


def test_apply_detection_to_cue_maps_fields():
    from types import SimpleNamespace
    cue = {"segment_id": "s1", "title": None}
    det = SimpleNamespace(
        provider="acrcloud", title="Diane", artist="Rapee", confidence=0.8,
        metadata={"album": "OST", "isrc": "US123", "release_year": 1927,
                  "agreement": {"count": 1, "total": 3}, "votes": []},
    )
    app_module._apply_detection_to_cue(cue, det)
    assert cue["title"] == "Diane" and cue["artist"] == "Rapee"
    assert cue["album"] == "OST" and cue["isrc"] == "US123"
    assert cue["release_year"] == 1927 and cue["agreement"] == {"count": 1, "total": 3}
