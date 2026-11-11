"""Tests for the smaller endpoints: page, film lookup, staging, playback, cancel,
history."""

import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from soundtrackID import app as app_module
from soundtrackID.metadata import film as film_module
from soundtrackID.metadata.film import FilmInfo

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app_module.app)


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
    }


def test_film_lookup_drops_unusable_values(film_lookup, monkeypatch):
    monkeypatch.delenv("FIWI_TMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("FIWI_TMDB_KEY", raising=False)
    r = client.post(
        "/film/lookup",
        json={"imdb_id": "", "tmdb_id": "abc", "title": "", "year": "unknown"},
    )

    assert r.status_code == 200
    assert film_lookup["keys"] == (None, None)
    assert film_lookup["hint"] == {
        "imdb_id": None,
        "tmdb_id": None,
        "title": None,
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


def test_identify_removes_stale_staged_uploads(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    monkeypatch.setattr(app_module, "_STAGING_DIR", staging)
    monkeypatch.setattr(app_module, "_staging", {})
    for name in ("abandoned", "recent"):
        (staging / name).mkdir(parents=True)
        (staging / name / "film.mp4").write_bytes(b"video")
        app_module._staging[name] = staging / name / "film.mp4"
    seven_hours_ago = time.time() - 7 * 3600
    os.utime(staging / "abandoned", (seven_hours_ago, seven_hours_ago))

    r = client.post("/identify", files={"file": ("new.mp4", b"video")})

    assert r.status_code == 200
    new_id = r.json()["staging_id"]
    assert not (staging / "abandoned").exists()
    assert set(app_module._staging) == {"recent", new_id}


@pytest.mark.parametrize("staging_id", ["unknown", "pruned"])
def test_analyze_needs_a_staged_upload(tmp_path, monkeypatch, staging_id):
    # "pruned" is still registered, but its file is gone
    monkeypatch.setattr(app_module, "_staging", {"pruned": tmp_path / "film.mp4"})
    r = client.post("/analyze", data={"staging_id": staging_id})

    assert r.status_code == 400
    assert r.json()["detail"] == "Staged upload not found or expired"


# ── Playback ───────────────────────────────────────────────────────────────


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run = tmp_path / "myrun"
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


# ── Cancel ─────────────────────────────────────────────────────────────────


def test_cancel_stops_only_a_known_run(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(app_module, "_cancel_events", {"run1": event})

    assert client.post("/cancel/other").status_code == 404
    assert not event.is_set()
    assert client.post("/cancel/run1").json() == {"status": "cancelled"}
    assert event.is_set()


# ── History ────────────────────────────────────────────────────────────────


def test_history_lists_only_runs_with_readable_results(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    (tmp_path / "running").mkdir()  # no results.json yet
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "results.json").write_text("{not json")
    (tmp_path / "done").mkdir()
    cues = [{"title": "A"}, {"title": None}]
    (tmp_path / "done" / "results.json").write_text(json.dumps({"cues": cues}))
    (tmp_path / "notes.txt").write_text("not a run")

    items = client.get("/history").json()

    assert [(i["video_name"], i["total"], i["identified"]) for i in items] == [
        ("done", 2, 1)
    ]


def test_history_is_empty_without_a_cache_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path / "missing")
    assert client.get("/history").json() == []


def test_deleting_the_history_removes_every_run(tmp_path, monkeypatch):
    """And only the runs: the settings next to the cache stay."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", cache)
    (cache / "done").mkdir(parents=True)
    (cache / "done" / "results.json").write_text("{}")
    (cache / "broken").mkdir()  # not listed, but still in the cache
    (tmp_path / "settings.json").write_text("{}")

    r = client.delete("/history")

    assert r.status_code == 200
    assert list(cache.iterdir()) == []
    assert (tmp_path / "settings.json").exists()


def test_deleting_the_history_without_a_cache_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path / "missing")
    assert client.delete("/history").status_code == 200
