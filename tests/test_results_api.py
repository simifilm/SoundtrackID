"""Tests for the manual-edit save endpoint POST /results/{video_name}."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile

from soundtrackID import app as app_module
from soundtrackID.detection import RateLimitError
from soundtrackID.models import (
    CUE_METADATA_KEYS,
    DetectionResult,
    IdentificationOutput,
    ResultsOutput,
    SegmentOutput,
)

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app_module.app)


def _make_run(tmp_path, name="myrun"):
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "ID": "abc",
                "film": {"title": "Test", "year": 1950},
                "waveform": {"samples": [1, 2, 3]},
                "cues": [{"segment_id": "seg_001", "title": "Old", "artist": "A"}],
            }
        )
    )
    return run_dir


def test_save_replaces_cues_and_preserves_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)

    new_cues = [
        {
            "segment_id": "seg_001",
            "title": "New Title",
            "artist": "A",
            "enrichment": {"composer": "Somebody", "original_year": 1916},
        },
    ]
    r = client.post("/results/myrun", json={"cues": new_cues})
    assert r.status_code == 200
    assert r.json()["cues"] == 1

    saved = json.loads((run_dir / "results.json").read_text())
    assert saved["cues"] == new_cues  # cues replaced
    assert saved["film"] == {"title": "Test", "year": 1950}  # preserved
    assert saved["waveform"] == {"samples": [1, 2, 3]}  # preserved


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


@pytest.mark.parametrize("name", ["%2e", "%2e%2e"])
def test_path_traversal_rejected(tmp_path, monkeypatch, name):
    # "." and ".." would reach these results files without the check
    cache = tmp_path / "cache"
    cache.mkdir()
    for folder in (cache, tmp_path):
        (folder / "results.json").write_text('{"cues": ["keep"]}')
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", cache)
    r = client.post(f"/results/{name}", json={"cues": []})
    assert r.status_code == 400
    for folder in (cache, tmp_path):
        assert json.loads((folder / "results.json").read_text()) == {"cues": ["keep"]}


# ── Resume endpoint ────────────────────────────────────────────────────────


def test_resume_missing_run_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.post("/resume/nope", json={})
    assert r.status_code == 404


def test_resume_no_pending_clears_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run = tmp_path / "r"
    run.mkdir()
    (run / "results.json").write_text(
        json.dumps(
            {
                "rate_limited": True,
                "cues": [
                    {"segment_id": "s1", "title": "X", "audio_file": "segments/s1.wav"}
                ],
            }
        )
    )
    r = client.post("/resume/r", json={})
    assert r.status_code == 200
    assert '"step": "done"' in r.text
    saved = json.loads((run / "results.json").read_text())
    assert saved["rate_limited"] is False  # nothing pending -> resolved


def test_apply_detection_to_cue_maps_fields():
    from types import SimpleNamespace

    cue = {"segment_id": "s1", "title": None}
    det = SimpleNamespace(
        provider="acrcloud",
        title="Diane",
        artist="Rapee",
        confidence=0.8,
        metadata={
            "album": "OST",
            "isrc": "US123",
            "release_year": 1927,
            "agreement": {"count": 1, "total": 3},
            "votes": [],
        },
    )
    app_module._apply_detection_to_cue(cue, det)
    assert cue["title"] == "Diane" and cue["artist"] == "Rapee"
    assert cue["album"] == "OST" and cue["isrc"] == "US123"
    assert cue["release_year"] == 1927 and cue["agreement"] == {"count": 1, "total": 3}


def test_resumed_cue_gets_the_same_fields_as_an_analyzed_one():
    """E.g. its candidates, without which the date re-ranking skips it."""
    metadata = {key: f"<{key}>" for key in CUE_METADATA_KEYS}
    segment = SegmentOutput("s1", 0, 10, "original", "segments/s1.wav", 0.9)
    identification = IdentificationOutput(
        "s1", "acrcloud", "T", "A", "Al", 0.9, metadata
    )
    results = ResultsOutput("film.mp4", [segment], identifications=[identification])
    analyzed = results.to_dict()["cues"][0]

    resumed = {}
    detection = DetectionResult(
        None, "T", "A", 0.9, "acrcloud", {**metadata, "album": "Al"}
    )
    app_module._apply_detection_to_cue(resumed, detection)

    segment_fields = {"segment_id", "start", "duration", "conditioning", "audio_file"}
    detection_fields = set(analyzed) - segment_fields - {"pending", "enrichment"}
    assert resumed == {key: analyzed[key] for key in detection_fields}


def _resume(tmp_path, monkeypatch, detect, cues, **results):
    """POST /resume for a run with these cues (each with a short silent clip),
    identifying them with `detect`; returns the saved results."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: SimpleNamespace(detect=detect),
    )
    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", lambda **kw: None)
    run = tmp_path / "r"
    (run / "segments").mkdir(parents=True)
    for cue in cues:
        cue["audio_file"] = f"segments/{cue['segment_id']}.wav"
        wavfile.write(run / cue["audio_file"], 1000, np.zeros(1000, dtype=np.int16))
    (run / "results.json").write_text(json.dumps({**results, "cues": cues}))

    r = client.post("/resume/r", json={})

    assert r.status_code == 200
    return json.loads((run / "results.json").read_text())


def test_resume_reranks_newly_identified_cues(tmp_path, monkeypatch):
    """A resumed cue whose top match postdates the film gets the period pick."""
    candidates = [
        {"title": "Sampler", "release_year": 2022, "score": 90.0},
        {"title": "Original", "release_year": 1971, "score": 70.0},
    ]

    def detect(segment):
        metadata = {"release_year": 2022, "candidates": candidates}
        return DetectionResult(segment, "Sampler", "A", 0.9, "acrcloud", metadata)

    cues = [{"segment_id": "s1", "title": None}]
    saved = _resume(
        tmp_path, monkeypatch, detect, cues, film={"year": 1971}, rate_limited=True
    )

    [cue] = saved["cues"]
    assert (cue["title"], cue["release_year"]) == ("Original", 1971)
    assert cue["temporal_pick"] is True


def test_resume_asks_only_about_pending_cues(tmp_path, monkeypatch):
    """A cue the provider already answered with "no match" isn't asked again."""

    def detect(segment):
        return DetectionResult(segment, "Song", "A", 0.9, "acrcloud", {})

    cues = [
        {"segment_id": "s1", "title": None, "pending": False},
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(tmp_path, monkeypatch, detect, cues, rate_limited=True)

    assert [(c["title"], c["pending"]) for c in saved["cues"]] == [
        (None, False),
        ("Song", False),
    ]


def test_resume_of_an_older_run_keeps_its_unasked_cues_pending(tmp_path, monkeypatch):
    """Its cues aren't marked, so every unidentified one counts as pending,
    including those a renewed rate limit leaves unasked."""

    def detect(segment):
        raise RateLimitError("quota exceeded")

    cues = [
        {"segment_id": "s1", "title": "Song"},
        {"segment_id": "s2", "title": None},
        {"segment_id": "s3", "title": None},
    ]
    saved = _resume(tmp_path, monkeypatch, detect, cues, rate_limited=True)

    assert [c["pending"] for c in saved["cues"]] == [False, True, True]
    assert saved["rate_limited"] is True
