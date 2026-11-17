"""A run's progress, streamed to the page as server-sent events, and cancelling
it: POST /cancel/{run_id}."""

import asyncio
import json
import logging
import threading
import traceback
from typing import AsyncGenerator, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from soundtrackid import analysis

log = logging.getLogger(__name__)

router = APIRouter()

# ── Per-run cancellation registry ─────────────────────────────────────────────
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


@router.post("/cancel/{run_id}")
async def cancel_run(run_id: str) -> dict:
    with _cancel_lock:
        ev = _cancel_events.get(run_id)
    if ev:
        ev.set()
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Run not found")


async def sse(
    work: Callable[[analysis.ProgressCb, threading.Event], dict], run_id: str
) -> AsyncGenerator[str, None]:
    """Run `work(on_progress, cancel)` in a thread and stream its progress as SSE.

    Starts with the run_id (POST /cancel/{run_id} cancels it) and ends with
    "done" and the results, "cancelled" or "error".
    """
    loop = asyncio.get_running_loop()
    events: asyncio.Queue[dict] = asyncio.Queue()
    cancel = threading.Event()
    with _cancel_lock:
        _cancel_events[run_id] = cancel

    def put(event: dict) -> None:
        loop.call_soon_threadsafe(events.put_nowait, event)

    def run() -> None:
        try:
            results = work(lambda step, **fields: put({"step": step, **fields}), cancel)
            put({"step": "done", "results": results})
        except analysis.Cancelled:
            put({"step": "cancelled"})
        except Exception as exc:
            log.exception("Run %s failed", run_id)
            put(
                {
                    "step": "error",
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        finally:
            with _cancel_lock:
                _cancel_events.pop(run_id, None)

    worker = asyncio.create_task(asyncio.to_thread(run))
    yield _sse_event({"step": "run_id", "run_id": run_id})
    while True:
        event = await events.get()
        yield _sse_event(event)
        if event["step"] in ("done", "error", "cancelled"):
            await worker
            return


def _sse_event(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def event_stream(events: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
