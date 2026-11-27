"""Tests for the jobs (web/jobs.py): analyses and resumes that run on whether a
page follows them or not, followed from their start, and cancelled."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from soundtrackid import analysis
from soundtrackid.web import jobs
from soundtrackid.web.app import app

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


@pytest.fixture(autouse=True)
def no_other_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})


def _waiting_job(kind="analysis", name=None):
    """A job under way: it reports "loading", then waits for the returned gate
    before it reports "classifying" and ends with its results."""
    gate = threading.Event()
    loading = threading.Event()

    def work(on_progress, cancel):
        on_progress("loading", detail="Loading")
        loading.set()
        gate.wait(timeout=10)
        if cancel.is_set():
            raise analysis.Cancelled()
        on_progress("classifying", detail="Classifying")
        return {"video_name": "film"}

    job = jobs.start(kind, work, name)
    loading.wait(timeout=10)
    return job, gate


def _events(job_id):
    r = client.get(f"/jobs/{job_id}/events")
    assert r.status_code == 200
    return [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]


def _steps(events):
    return [e["step"] for e in events]


def _wait_until_ended():
    deadline = time.monotonic() + 10
    while client.get("/jobs").json():
        assert time.monotonic() < deadline, "the job didn't end"
        time.sleep(0.01)


def test_a_page_coming_to_a_job_under_way_gets_every_event_once():
    """What happened before it came, then what happens after."""
    job, gate = _waiting_job()

    def go_on_once_followed():
        while not job._followers:
            time.sleep(0.01)
        gate.set()

    threading.Thread(target=go_on_once_followed).start()

    events = _events(job.id)

    assert _steps(events) == ["loading", "classifying", "done"]
    assert events[-1]["results"] == {"video_name": "film"}


def test_a_job_runs_to_its_end_with_no_page_following_it():
    """And a page that comes only then gets all of it."""
    job, gate = _waiting_job("resume", "film_2026-11-11_10-00-00")
    assert client.get("/jobs").json() == [
        {"id": job.id, "kind": "resume", "name": "film_2026-11-11_10-00-00"}
    ]

    gate.set()
    _wait_until_ended()

    assert _steps(_events(job.id)) == ["loading", "classifying", "done"]


def test_cancel_stops_a_job_under_way():
    job, gate = _waiting_job()

    assert client.post(f"/jobs/{job.id}/cancel").json() == {"status": "cancelled"}
    gate.set()

    assert _steps(_events(job.id)) == ["loading", "cancelled"]
    assert client.post(f"/jobs/{job.id}/cancel").status_code == 404  # ended


def test_a_failed_job_ends_with_why():
    def work(on_progress, cancel):
        raise ValueError("Could not read the audio")

    job = jobs.start("analysis", work)

    [event] = _events(job.id)
    assert (event["step"], event["detail"]) == ("error", "Could not read the audio")


def test_an_unknown_job_is_404():
    assert client.get("/jobs/nope/events").status_code == 404
    assert client.post("/jobs/nope/cancel").status_code == 404


def test_a_job_that_ended_is_dropped_in_time(monkeypatch):
    """When the next one starts."""
    monkeypatch.setattr(jobs, "_ENDED_KEPT_S", 0)
    ended = jobs.start("analysis", lambda on_progress, cancel: {})
    _events(ended.id)
    time.sleep(0.01)

    jobs.start("analysis", lambda on_progress, cancel: {})

    assert client.get(f"/jobs/{ended.id}/events").status_code == 404
