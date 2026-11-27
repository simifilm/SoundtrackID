"""Tests for the smaller endpoints: page, film lookup, staging, playback,
history."""

import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from soundtrackid.metadata import film as film_module
from soundtrackid.metadata.film import FilmInfo
from soundtrackid.web import analyze, jobs
from soundtrackid.web.app import app

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


def test_page_is_served_uncached():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "no-store" in r.headers["cache-control"]


@pytest.mark.parametrize("path", ["/static/app.css", "/static/js/main.js"])
def test_the_pages_styles_and_scripts_are_checked_for_changes(path):
    """So an update shows on a normal reload, not a stale cached script."""
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


# ── /film/lookup ───────────────────────────────────────────────────────────


@pytest.fixture
def film_lookup(monkeypatch):
    """Replace FilmLookup (tested on its own); returns what the endpoint passed."""
    calls = {}

    class FakeFilmLookup:
        def __init__(self, client, tmdb_bearer_token=None, tmdb_api_key=None):
            calls["keys"] = (tmdb_bearer_token, tmdb_api_key)

        def lookup(self, **hint):
            calls["hint"] = hint
            return FilmInfo(title="Sunset Boulevard", year=1950, sources=["tmdb"])

        def search(self, **hint):
            calls["hint"] = hint
            return [{"tmdb_id": 599, "title": "Sunset Boulevard"}]

    monkeypatch.setattr(film_module, "FilmLookup", FakeFilmLookup)
    return calls


def test_film_lookup_passes_the_hint_and_the_keys(film_lookup, monkeypatch):
    monkeypatch.setenv("FIWI_TMDB_API_TOKEN", "tok")
    monkeypatch.setenv("FIWI_TMDB_KEY", "key")
    r = client.post(
        "/film/lookup",
        json={
            "imdb_id": "tt0043014",
            "tmdb_id": "599",
            "title": "Sunset Boulevard",
            "year": "1950",
            "media_type": "movie",
        },
    )

    assert r.status_code == 200
    expected = FilmInfo(title="Sunset Boulevard", year=1950, sources=["tmdb"])
    assert r.json() == {"film": expected.to_dict()}
    assert film_lookup["keys"] == ("tok", "key")
    # Numbers typed into the form arrive as text
    assert film_lookup["hint"] == {
        "imdb_id": "tt0043014",
        "tmdb_id": 599,
        "title": "Sunset Boulevard",
        "year": 1950,
        "media_type": "movie",
    }


def test_film_lookup_drops_unusable_values(film_lookup, monkeypatch):
    monkeypatch.delenv("FIWI_TMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("FIWI_TMDB_KEY", raising=False)
    r = client.post(
        "/film/lookup",
        json={
            "imdb_id": "",
            "tmdb_id": "abc",
            "title": "",
            "year": "unknown",
            "media_type": "film",
        },
    )

    assert r.status_code == 200
    assert film_lookup["keys"] == (None, None)
    assert film_lookup["hint"] == {
        "imdb_id": None,
        "tmdb_id": None,
        "title": None,
        "year": None,
        "media_type": None,
    }


def test_film_search_passes_the_hint_and_the_keys(film_lookup, monkeypatch):
    monkeypatch.setenv("FIWI_TMDB_API_TOKEN", "tok")
    monkeypatch.delenv("FIWI_TMDB_KEY", raising=False)
    r = client.post("/film/search", json={"tmdb_id": "599", "title": "Sunset"})

    assert r.status_code == 200
    assert r.json() == {"candidates": [{"tmdb_id": 599, "title": "Sunset Boulevard"}]}
    assert film_lookup["keys"] == ("tok", None)
    assert film_lookup["hint"] == {
        "imdb_id": None,
        "tmdb_id": 599,
        "title": "Sunset",
        "year": None,
    }


def test_film_lookup_failure_is_502(monkeypatch):
    class BrokenFilmLookup:
        def __init__(self, *args, **kwargs):
            pass

        def lookup(self, **hint):
            raise RuntimeError("boom")

    monkeypatch.setattr(film_module, "FilmLookup", BrokenFilmLookup)
    r = client.post("/film/lookup", json={"title": "Sunset Boulevard"})

    assert r.status_code == 502
    assert r.json()["detail"] == "Film lookup failed: boom"


# ── Staged uploads ─────────────────────────────────────────────────────────


def test_identify_removes_stale_staged_uploads(data_dir, monkeypatch):
    staging = data_dir / "staging"
    monkeypatch.setattr(analyze, "_staging", {})
    for name in ("abandoned", "recent"):
        (staging / name).mkdir(parents=True)
        (staging / name / "film.mp4").write_bytes(b"video")
        analyze._staging[name] = staging / name / "film.mp4"
    seven_hours_ago = time.time() - 7 * 3600
    os.utime(staging / "abandoned", (seven_hours_ago, seven_hours_ago))

    r = client.post("/identify", files={"file": ("new.mp4", b"video")})

    assert r.status_code == 200
    new_id = r.json()["staging_id"]
    assert not (staging / "abandoned").exists()
    assert set(analyze._staging) == {"recent", new_id}


def test_identify_leaves_the_film_an_analysis_took(data_dir, monkeypatch):
    """However long ago it was staged: the analysis removes it as it ends."""
    taken = data_dir / "staging" / "taken"
    taken.mkdir(parents=True)
    (taken / "film.mp4").write_bytes(b"video")
    seven_hours_ago = time.time() - 7 * 3600
    os.utime(taken, (seven_hours_ago, seven_hours_ago))
    monkeypatch.setattr(analyze, "_staging", {})

    client.post("/identify", files={"file": ("new.mp4", b"video")})

    assert (taken / "film.mp4").exists()


def test_the_server_starts_with_no_staged_uploads(data_dir, monkeypatch):
    """Nor films of analyses it left: the app closed during them."""
    staging = data_dir / "staging"
    (staging / "left").mkdir(parents=True)
    (staging / "left" / "film.mp4").write_bytes(b"video")

    analyze.clear_staging()

    assert not staging.exists()


@pytest.mark.parametrize("staging_id", ["unknown", "pruned"])
def test_analyze_needs_a_staged_upload(tmp_path, monkeypatch, staging_id):
    # "pruned" is still registered, but its file is gone
    monkeypatch.setattr(analyze, "_staging", {"pruned": tmp_path / "film.mp4"})
    r = client.post("/analyze", data={"staging_id": staging_id})

    assert r.status_code == 400
    assert r.json()["detail"] == "Staged upload not found or expired"


# ── Playback ───────────────────────────────────────────────────────────────


@pytest.fixture
def run_dir(runs_dir):
    run = runs_dir / "myrun"
    run.mkdir()
    return run


def test_video_supports_range_requests_for_scrubbing(run_dir):
    video = bytes(range(256)) * 4
    (run_dir / "source.mp4").write_bytes(video)

    r = client.get("/output/myrun/video", headers={"Range": "bytes=100-199"})

    assert r.status_code == 206
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == video[100:200]


def test_filmstrip_is_served_as_jpeg(run_dir):
    (run_dir / "filmstrip.jpg").write_bytes(b"\xff\xd8jpeg")

    r = client.get("/output/myrun/filmstrip")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8jpeg"


@pytest.mark.parametrize("what", ["video", "filmstrip"])
def test_missing_video_or_filmstrip_is_404(run_dir, what):
    assert client.get(f"/output/myrun/{what}").status_code == 404


# ── History ────────────────────────────────────────────────────────────────


def test_history_lists_only_runs_with_readable_results(runs_dir):
    """And those interrupted before they were saved, only to delete."""
    (runs_dir / "interrupted").mkdir()  # no results.json
    (runs_dir / "broken").mkdir()
    (runs_dir / "broken" / "results.json").write_text("{not json")
    (runs_dir / "done").mkdir()
    cues = [{"title": "A"}, {"title": None}]
    (runs_dir / "done" / "results.json").write_text(json.dumps({"cues": cues}))
    (runs_dir / "notes.txt").write_text("not a run")
    os.utime(runs_dir / "interrupted", (1000, 1000))

    done, interrupted = client.get("/history").json()

    assert (done["kind"], done["video_name"], done["total"], done["identified"]) == (
        "run",
        "done",
        2,
        1,
    )
    assert interrupted == {
        "kind": "interrupted",
        "video_name": "interrupted",
        "timestamp": 1000,
    }
    assert client.delete("/history/interrupted").status_code == 200
    assert not (runs_dir / "interrupted").exists()


def test_no_run_is_interrupted_while_one_is_analyzed(runs_dir, monkeypatch):
    """The one under way isn't saved yet either."""
    monkeypatch.setattr(jobs, "_jobs", {})
    (runs_dir / "film_2026-11-11_10-00-00").mkdir()
    gate = threading.Event()
    job = jobs.start("analysis", lambda on_progress, cancel: gate.wait(10))
    try:
        assert client.get("/history").json() == []
    finally:
        gate.set()
        client.get(f"/jobs/{job.id}/events")  # once it ended


def test_history_is_empty_without_a_cache_folder(data_dir):
    assert client.get("/history").json() == []


def test_deleting_the_history_removes_every_run(data_dir, runs_dir):
    """And only the runs: the settings next to the cache stay."""
    cache = runs_dir
    (cache / "done").mkdir()
    (cache / "done" / "results.json").write_text("{}")
    (cache / "broken").mkdir()  # not listed, but still in the cache
    (data_dir / "settings.json").write_text("{}")

    r = client.delete("/history")

    assert r.status_code == 200
    assert list(cache.iterdir()) == []
    assert (data_dir / "settings.json").exists()


def test_deleting_the_history_without_a_cache_folder(data_dir):
    assert client.delete("/history").status_code == 200
