"""The analyses, batches and resumes under way, each a job: a request starts it,
and it runs in a thread of its own until it ends, whether a page follows it or
not. A page follows a job's progress as server-sent events from the job's
start, so a page loaded anew (e.g. reloaded) shows a job under way where it is,
and can still cancel it.

    GET  /jobs                 the jobs under way
    GET  /jobs/{id}/events     a job's progress, from its start to its end
    POST /jobs/{id}/cancel     cancel a job
"""

import asyncio
import json
import logging
import threading
import time
import traceback
import uuid
from typing import AsyncGenerator, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from soundtrackid import analysis

log = logging.getLogger(__name__)

router = APIRouter()

# What a job does: work(on_progress, cancel), which returns the results the job
# ends with.
Work = Callable[[analysis.ProgressCb, threading.Event], dict]

# The steps a job's last event has: the results, or why there are none.
_LAST_STEPS = ("done", "cancelled", "error")

# How long a job is kept once it ended: the page that started it asks for its
# events right after, maybe only once it ended.
_ENDED_KEPT_S = 300

# The steps whose event shows all a page needs of the events before it, which
# are dropped then: a batch's record as a film starts or ends, a batch resume's
# next run. So a batch job keeps the progress of its film under way, not of all
# the films before (a film's can be ~1 MB).
_FRESH_STEPS = ("batch", "batch_resume")


class Job:
    def __init__(self, kind: str, name: str | None = None) -> None:
        self.id = str(uuid.uuid4())
        self.kind = kind  # "analysis", "batch", "resume" or "batch_resume"
        self.name = name  # what it is of: a batch's folder, a resume's run
        self.cancel = threading.Event()
        self.ended_at: float | None = None  # time.monotonic()
        self._events: list[dict] = []
        self._followers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    @property
    def ended(self) -> bool:
        return self.ended_at is not None

    def run(self, work: Work) -> None:
        """Do `work`, and tell the followers how it goes: its progress, then
        "done" with the results, "cancelled" or "error"."""
        try:
            results = work(
                lambda step, **fields: self._publish({"step": step, **fields}),
                self.cancel,
            )
            self._publish({"step": "done", "results": results})
        except analysis.Cancelled:
            self._publish({"step": "cancelled"})
        except Exception as exc:
            log.exception("Job %s failed", self.id)
            self._publish(
                {
                    "step": "error",
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    def follow(self) -> tuple[list[dict], asyncio.Queue]:
        """The job's events so far (from its latest of _FRESH_STEPS), and a
        queue in the running event loop that the events to come arrive in, none
        twice or missed; unfollow() once done."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        with self._lock:
            if not self.ended:
                self._followers.append((asyncio.get_running_loop(), queue))
            return list(self._events), queue

    def unfollow(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._followers = [f for f in self._followers if f[1] is not queue]

    def _publish(self, event: dict) -> None:
        with self._lock:
            if event["step"] in _FRESH_STEPS:
                self._events.clear()
            self._events.append(event)
            for loop, queue in self._followers:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                except RuntimeError:
                    pass  # its event loop is closed: the page went away
            if event["step"] in _LAST_STEPS:
                self.ended_at = time.monotonic()
                self._followers.clear()


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def start(kind: str, work: Work, name: str | None = None) -> Job:
    """Do `work` as a new job, in a thread of its own, and return the job."""
    job = Job(kind, name)
    with _jobs_lock:
        now = time.monotonic()
        for old in list(_jobs.values()):
            if old.ended_at is not None and now - old.ended_at > _ENDED_KEPT_S:
                del _jobs[old.id]
        _jobs[job.id] = job
    # A daemon, so that a job under way doesn't keep the server from stopping.
    threading.Thread(target=job.run, args=(work,), daemon=True).start()
    return job


def under_way(*kinds: str) -> set[str]:
    """What the jobs of these kinds under way are of, e.g. the batches."""
    return {job["name"] for job in _under_way() if job["kind"] in kinds}


@router.get("/jobs")
async def jobs_under_way() -> list[dict]:
    """The jobs under way, the oldest first."""
    return _under_way()


def _under_way() -> list[dict]:
    with _jobs_lock:
        jobs = list(_jobs.values())
    return [
        {"id": job.id, "kind": job.kind, "name": job.name}
        for job in jobs
        if not job.ended
    ]


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """A job's progress as server-sent events, from its start: a page that
    comes to a job under way catches up. Leaving doesn't stop the job."""
    return StreamingResponse(
        _events(_job(job_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    """Stop a job under way at its next step; it ends with "cancelled"."""
    job = _job(job_id)
    if job.ended:
        raise HTTPException(status_code=404, detail="Job not under way")
    job.cancel.set()
    return {"status": "cancelled"}


def _job(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _events(job: Job) -> AsyncGenerator[str, None]:
    past, upcoming = job.follow()
    try:
        for event in past:
            yield _sse_event(event)
        step = past[-1]["step"] if past else None
        while step not in _LAST_STEPS:
            event = await upcoming.get()
            yield _sse_event(event)
            step = event["step"]
    finally:
        job.unfollow(upcoming)


def _sse_event(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
