"""Tests for the manual-edit save endpoint POST /results/{video_name}."""

import csv
import io
import json
import os
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile

from soundtrackID import app as app_module
from soundtrackID.derived import refresh
from soundtrackID.detection import RateLimitError
from soundtrackID.models import (
    ANALYSIS_KEYS,
    MATCH_KEYS,
    RESULTS_VERSION,
    DetectionResult,
    MusicSegment,
    ResultsOutput,
    upgrade_results,
)
from soundtrackID.pipeline import build_cues

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
            "enrichment": {"original_year": 1916},
        },
    ]
    r = client.post("/results/myrun", json={"cues": new_cues})
    assert r.status_code == 200
    assert r.json()["cues"] == 1

    saved = json.loads((run_dir / "results.json").read_text())
    [cue] = saved["cues"]  # replaced
    assert {key: cue[key] for key in new_cues[0]} == new_cues[0]
    assert saved["film"] == {"title": "Test", "year": 1950}  # preserved
    # Preserved, in the timeline's own file now.
    timeline = json.loads((run_dir / "timeline.json").read_text())
    assert timeline == {"waveform": {"samples": [1, 2, 3]}}


def _run_with_cue(tmp_path, cue, film=None):
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(json.dumps({"film": film, "cues": [cue]}))
    return run_dir


def test_a_saved_edit_comes_back_with_what_follows_from_it(tmp_path, monkeypatch):
    """A work dated after the film by hand is marked as later than it, in the
    answer (which the page shows) and on disk."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    cue = {"segment_id": "seg_001", "title": "Song", "artist": "A", "enrichment": {}}
    run_dir = _run_with_cue(tmp_path, cue, film={"title": "F", "year": 1950})
    edited = {**cue, "enrichment": {"original_year": 1975}}

    r = client.post("/results/myrun", json={"cues": [edited]})

    [answered] = r.json()["results"]["cues"]
    [saved] = json.loads((run_dir / "results.json").read_text())["cues"]
    assert answered["anachronistic"] is saved["anachronistic"] is True
    assert answered["track_key"] == "song|a"


def test_a_hand_edited_composer_keeps_the_co_authors_and_their_verdict(
    tmp_path, monkeypatch
):
    """The life dates were the composer looked up, so they go; the lyricist
    found before stays, and their death in 1993 keeps the work protected."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    enrichment = {
        "composer": "Glenn Miller",
        "composer_death_year": 1944,
        "authors": [
            {"name": "Glenn Miller", "roles": ["composer"], "death_year": 1944},
            {"name": "Mitchell Parish", "roles": ["lyricist"], "death_year": 1993},
        ],
    }
    cue = {"segment_id": "seg_001", "title": "Moonlight Serenade"}
    _run_with_cue(tmp_path, {**cue, "enrichment": enrichment})
    edited = {**cue, "enrichment": {**enrichment, "composer": "Someone Else"}}

    r = client.post("/results/myrun", json={"cues": [edited]})

    saved = r.json()["results"]["cues"][0]["enrichment"]
    assert [a["name"] for a in saved["authors"]] == ["Someone Else", "Mitchell Parish"]
    assert saved["authors"][0]["death_year"] is None
    assert saved["composer_death_year"] is None
    assert saved["authors_partial"] is True
    assert (saved["latest_author_death_year"], saved["public_domain"]) == (1993, False)


def test_an_edit_keeps_where_a_cue_is_and_whether_it_is_pending(tmp_path, monkeypatch):
    """Those are the analysis's: a resume reads and removes the audio_file."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    cue = {
        "segment_id": "seg_001",
        "start": 10.0,
        "duration": 5.0,
        "audio_file": "segments/seg_001.wav",
        "pending": True,
        "title": None,
    }
    (run_dir / "results.json").write_text(json.dumps({"cues": [cue]}))
    edited = {
        **cue,
        "start": 0.0,
        "audio_file": "../../elsewhere.wav",
        "pending": False,
        "title": "Typed in",
    }

    r = client.post("/results/myrun", json={"cues": [edited]})

    assert r.status_code == 200
    [saved] = json.loads((run_dir / "results.json").read_text())["cues"]
    assert {key: saved[key] for key in ANALYSIS_KEYS} == {
        key: cue[key] for key in ANALYSIS_KEYS
    }
    assert saved["title"] == "Typed in"


@pytest.mark.parametrize(
    "cues",
    [
        [{"segment_id": "seg_999", "title": "Not of this run"}],
        [{"segment_id": "seg_001"}, {"segment_id": "seg_001"}],
        ["not a cue"],
        [{"segment_id": "seg_001", "title": 42}],
        [{"segment_id": "seg_001", "enrichment": "not an object"}],
    ],
    ids=["unknown", "twice", "not-an-object", "title-number", "enrichment-text"],
)
def test_an_edit_that_doesnt_fit_the_run_is_refused(tmp_path, monkeypatch, cues):
    """And nothing is saved."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)
    before = (run_dir / "results.json").read_text()

    r = client.post("/results/myrun", json={"cues": cues})

    assert r.status_code == 422
    assert (run_dir / "results.json").read_text() == before


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


# ── Reading runs: GET /results/{video_name} and the history list ───────────


def test_get_returns_the_run_results_in_the_current_format(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)  # saved without a version

    r = client.get("/results/myrun")

    assert r.status_code == 200
    saved = json.loads((run_dir / "results.json").read_text())
    del saved["waveform"]  # in the run's timeline
    assert r.json() == refresh(upgrade_results(saved))
    assert r.json()["version"] == RESULTS_VERSION


def _run_with_enrichment(tmp_path, enrichment):
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    cue = {"segment_id": "seg_001", "title": "Song", "enrichment": enrichment}
    (run_dir / "results.json").write_text(json.dumps({"cues": [cue]}))


def test_the_rights_verdict_is_that_of_the_year_the_run_is_read(tmp_path, monkeypatch):
    """The last author died 71 years ago: still protected when the run was
    saved last year, in the public domain now, also in the CSV."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    died = date.today().year - 71
    _run_with_enrichment(
        tmp_path,
        {
            "composer": "A",
            "authors": [{"name": "A", "roles": ["composer"], "death_year": died}],
            "latest_author_death_year": died,
            "authors_complete": True,
            "authors_partial": False,
            "public_domain": False,  # as assessed last year
            "pd_presumption": None,
        },
    )

    cue = client.get("/results/myrun").json()["cues"][0]
    [row] = csv.DictReader(io.StringIO(client.get("/output/myrun/csv").text))

    assert cue["enrichment"]["public_domain"] is True
    assert row["enrichment.public_domain"] == "True"


def test_a_verdict_saved_without_its_authors_is_kept(tmp_path, monkeypatch):
    """Nothing to assess it from again."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    enrichment = {"composer": "A", "composer_death_year": 1900, "public_domain": True}
    _run_with_enrichment(tmp_path, enrichment)

    cue = client.get("/results/myrun").json()["cues"][0]

    assert cue["enrichment"] == enrichment


def test_results_carry_their_format_version():
    results = ResultsOutput("film.mp4", []).to_dict()

    assert results["version"] == RESULTS_VERSION
    assert "ID" not in results and "fiwi_id" not in results


def test_older_results_are_upgraded_when_read():
    """Saved before skipped cues were marked: in a rate-limited run any
    unidentified cue may be one. Marks saved since are kept."""
    limited = {
        "ID": None,
        "fiwi_id": None,
        "rate_limited": True,
        "cues": [{"title": "Song"}, {"title": None}],
    }
    finished = {"rate_limited": False, "cues": [{"title": None}]}
    marked = {"rate_limited": True, "cues": [{"title": None, "pending": False}]}

    assert upgrade_results(limited) == {
        "version": RESULTS_VERSION,
        "cues": [{"title": "Song", "pending": False}, {"title": None, "pending": True}],
    }
    assert upgrade_results(finished)["cues"] == [{"title": None, "pending": False}]
    assert upgrade_results(marked)["cues"] == [{"title": None, "pending": False}]


def test_vocal_isolation_moves_from_the_cues_into_the_settings():
    """Every cue said whether vocals were removed (conditioning); it's a setting."""
    cue = {"title": "Song", "pending": False}
    isolated = {"version": 2, "cues": [{**cue, "conditioning": "vocals_removed"}]}
    original = {
        "version": 2,
        "settings": {"hop": 5.0},
        "cues": [{**cue, "conditioning": "original"}],
    }

    assert upgrade_results(isolated) == {
        "version": RESULTS_VERSION,
        "settings": {"isolation": True},
        "cues": [cue],
    }
    assert upgrade_results(original)["settings"] == {"hop": 5.0, "isolation": False}


def test_rate_limit_flag_gives_way_to_the_pending_cues():
    """A resume is due while any cue is pending; the run-wide flag could
    disagree with the cues."""
    data = {"version": 3, "rate_limited": True, "cues": [{"pending": False}]}

    assert upgrade_results(data) == {
        "version": RESULTS_VERSION,
        "cues": [{"pending": False}],
    }


def test_older_results_lose_their_confidence():
    """It meant something else for each provider (a constant 1.0 for Shazam and
    AudD), and nothing used it."""
    vote = {"provider": "shazam", "title": "Song", "confidence": 1.0}
    data = {
        "version": 4,
        "cues": [{"title": "Song", "confidence": 1.0, "votes": [vote]}],
    }

    assert upgrade_results(data)["cues"] == [
        {"title": "Song", "votes": [{"provider": "shazam", "title": "Song"}]}
    ]


def test_timeline_of_a_run_saved_before_it_had_its_own_file(tmp_path, monkeypatch):
    """Read from results.json, and moved into timeline.json when the results
    are next saved, as they leave it out."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = _make_run(tmp_path)  # its waveform in results.json
    timeline = {"waveform": {"samples": [1, 2, 3]}}

    assert client.get("/output/myrun/timeline").json() == timeline
    client.post("/results/myrun", json={"cues": []})  # an edit saves the results

    assert "waveform" not in json.loads((run_dir / "results.json").read_text())
    assert json.loads((run_dir / "timeline.json").read_text()) == timeline
    assert client.get("/output/myrun/timeline").json() == timeline


def test_timeline_of_a_missing_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    assert client.get("/output/nope/timeline").status_code == 404


def test_get_missing_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    assert client.get("/results/nope").status_code == 404


@pytest.mark.parametrize("name", ["%2e", "%2e%2e"])
def test_get_path_traversal_rejected(tmp_path, monkeypatch, name):
    # "." and ".." would reach these results files without the check
    cache = tmp_path / "cache"
    cache.mkdir()
    for folder in (cache, tmp_path):
        (folder / "results.json").write_text("{}")
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", cache)
    assert client.get(f"/results/{name}").status_code == 400


def test_history_lists_every_run_newest_first(tmp_path, monkeypatch):
    """Not just the last few; without their results, which GET /results loads."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    for i in range(12):
        run_dir = _make_run(tmp_path, f"run{i:02d}")
        os.utime(run_dir, (1000 + i, 1000 + i))  # run11 is the newest

    items = client.get("/history").json()

    assert [item["video_name"] for item in items] == [
        f"run{i:02d}" for i in reversed(range(12))
    ]
    assert {k: v for k, v in items[0].items() if k != "timestamp"} == {
        "video_name": "run11",
        "total": 1,
        "identified": 1,
    }


# ── Resume endpoint ────────────────────────────────────────────────────────


def test_resume_missing_run_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.post("/resume/nope", json={})
    assert r.status_code == 404


def test_resume_with_nothing_pending_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run = tmp_path / "r"
    run.mkdir()
    results = {
        "version": RESULTS_VERSION,
        "cues": [{"segment_id": "s1", "title": "X", "pending": False}],
    }
    (run / "results.json").write_text(json.dumps(results))

    r = client.post("/resume/r", json={})

    assert r.status_code == 200
    assert '"step": "done"' in r.text
    assert json.loads((run / "results.json").read_text()) == results


def test_resumed_cue_gets_the_same_fields_as_an_analyzed_one(tmp_path, monkeypatch):
    """E.g. its candidates, without which the date re-ranking skips it."""
    metadata = {
        key: f"<{key}>"
        for key in MATCH_KEYS
        if key not in ("provider", "title", "artist")
    }

    def detect(segment):
        return DetectionResult(segment, "T", "A", "acrcloud", dict(metadata))

    clip = MusicSegment(np.zeros(1000, np.float32), 0.0, 1.0, 1000)
    [analyzed], _ = build_cues([(clip, detect(clip))], bridge_gap=2.0)
    cues = [{"segment_id": "s1", "title": None, "pending": True}]
    [resumed] = _resume(tmp_path, monkeypatch, detect, cues)["cues"]

    assert {key: resumed[key] for key in MATCH_KEYS} == {
        key: analyzed[key] for key in MATCH_KEYS
    }


def _resume(tmp_path, monkeypatch, detect, cues, enrich=None, asked=None, **results):
    """POST /resume for a run with these cues (each with a short silent clip),
    identifying them with `detect` and looking up composers with `enrich` (by
    default not at all); returns the saved results. `asked` collects the
    providers the resume asks for."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)

    def build_detection_client(api):
        if asked is not None:
            asked.append(api)
        return SimpleNamespace(detect=detect)

    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client", build_detection_client
    )
    monkeypatch.setattr(
        "soundtrackID.metadata.enricher.enrich", enrich or (lambda **kw: None)
    )
    run = tmp_path / "r"
    (run / "segments").mkdir(parents=True)
    for cue in cues:
        cue["audio_file"] = f"segments/{cue['segment_id']}.wav"
        wavfile.write(run / cue["audio_file"], 1000, np.zeros(1000, dtype=np.int16))
    (run / "results.json").write_text(json.dumps({**results, "cues": cues}))

    r = client.post("/resume/r", json={})

    assert r.status_code == 200
    return json.loads((run / "results.json").read_text())


def test_a_resume_continues_with_the_runs_own_providers(tmp_path, monkeypatch):
    """Unless the page asks for others; as the CLI's resume."""
    asked = []
    cues = [{"segment_id": "s1", "title": None, "pending": True}]

    _resume(
        tmp_path,
        monkeypatch,
        lambda seg: DetectionResult(seg, "T", "A", "audd"),
        cues,
        asked=asked,
        providers=["audd", "shazam"],
    )

    assert asked == ["audd,shazam"]


def test_resume_reranks_newly_identified_cues(tmp_path, monkeypatch):
    """A resumed cue whose top match postdates the film gets the period pick."""
    candidates = [
        {"title": "Sampler", "release_year": 2022, "score": 90.0},
        {"title": "Original", "release_year": 1971, "score": 70.0},
    ]

    def detect(segment):
        metadata = {"release_year": 2022, "candidates": candidates}
        return DetectionResult(segment, "Sampler", "A", "acrcloud", metadata)

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
        return DetectionResult(segment, "Song", "A", "acrcloud", {})

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


def test_resume_gives_up_on_a_cue_whose_audio_is_gone(tmp_path, monkeypatch):
    """No resume could identify it, so it no longer counts as pending."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        "soundtrackID.detection.build_detection_client",
        lambda api: SimpleNamespace(detect=lambda seg: pytest.fail("nothing to send")),
    )
    monkeypatch.setattr("soundtrackID.metadata.enricher.enrich", lambda **kw: None)
    run = tmp_path / "r"
    run.mkdir()
    cue = {"segment_id": "s1", "pending": True, "audio_file": "segments/s1.wav"}
    results = {"version": RESULTS_VERSION, "cues": [cue]}
    (run / "results.json").write_text(json.dumps(results))

    client.post("/resume/r", json={})

    [saved] = json.loads((run / "results.json").read_text())["cues"]
    assert (saved["pending"], saved["audio_file"]) == (False, None)


def test_resume_changes_only_the_cues_it_identifies(tmp_path, monkeypatch):
    """The others keep their composers, e.g. one edited by hand."""

    def detect(segment):
        return DetectionResult(segment, "New", "B", "acrcloud", {"isrc": "X2"})

    def enrich(results, **kw):
        for cue in results["cues"]:
            cue["enrichment"] = {"composer": "Looked up"}

    cues = [
        {
            "segment_id": "s1",
            "title": "Old",
            "isrc": "X1",
            "pending": False,
            "enrichment": {"composer": "Edited by hand"},
        },
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(
        tmp_path, monkeypatch, detect, cues, enrich=enrich, rate_limited=True
    )

    assert [c["enrichment"]["composer"] for c in saved["cues"]] == [
        "Edited by hand",
        "Looked up",
    ]


def test_resume_removes_the_audio_of_answered_cues(tmp_path, monkeypatch):
    """Only pending cues keep their audio, for the next resume."""
    calls = []

    def detect(segment):
        calls.append(segment)
        if len(calls) > 1:
            raise RateLimitError("quota exceeded")
        return DetectionResult(segment, None, None)  # no match

    cues = [
        {"segment_id": "s1", "title": None, "pending": True},
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(tmp_path, monkeypatch, detect, cues, rate_limited=True)

    assert [(c["pending"], c["audio_file"]) for c in saved["cues"]] == [
        (False, None),
        (True, "segments/s2.wav"),
    ]
    assert [p.name for p in (tmp_path / "r" / "segments").iterdir()] == ["s2.wav"]
