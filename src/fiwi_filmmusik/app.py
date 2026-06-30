"""FastAPI web application for the FIWI Filmmusik pipeline."""

import asyncio
import json
import os
import queue
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import sys as _sys

if getattr(_sys, "frozen", False):
    _STATIC_DIR = Path(_sys._MEIPASS) / "static"
else:
    _STATIC_DIR = Path(__file__).parent / "static"

if getattr(_sys, "frozen", False):
    # Writable user directory inside the macOS app sandbox
    _OUTPUT_DIR = Path.home() / "Library" / "Application Support" / "FIWI Filmmusik" / "cache"
else:
    _OUTPUT_DIR = Path("./output/cache")

# ── Singleton classifier (loaded once at startup) ─────────────────────────────
_classifier = None
_classifier_lock = threading.Lock()

# ── Per-run cancellation registry ─────────────────────────────────────────────
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


class _PipelineCancelled(Exception):
    pass


def _get_classifier(threshold: float = 0.2):
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                if getattr(_sys, "frozen", False):
                    from fiwi_filmmusik.classifiers import OnnxClassifier
                    _classifier = OnnxClassifier(
                        model_dir=str(Path(_sys._MEIPASS) / "ast_model"),
                        music_labels=["Music"],
                        threshold=threshold,
                    )
                else:
                    onnx_model_dir = Path(__file__).parents[2] / "assets" / "ast_model"
                    use_hf = os.environ.get("FIWI_HF") or not onnx_model_dir.exists()
                    if use_hf:
                        from fiwi_filmmusik.classifiers import HuggingFaceClassifier
                        _classifier = HuggingFaceClassifier(music_labels=["Music"], threshold=threshold)
                    else:
                        from fiwi_filmmusik.classifiers import OnnxClassifier
                        _classifier = OnnxClassifier(
                            model_dir=str(onnx_model_dir),
                            music_labels=["Music"],
                            threshold=threshold,
                        )
    _classifier.threshold = threshold
    return _classifier


def _build_pipeline(output_dir: Path, chunk_duration: float = 10.0, threshold: float = 0.2, export_csv: bool = False):
    from fiwi_filmmusik.aggregator import ChunkAggregator
    from fiwi_filmmusik.chunker import AudioChunker
    from fiwi_filmmusik.detection import ShazamDetectionClient
    from fiwi_filmmusik.isolators import DummyIsolator
    from fiwi_filmmusik.loaders import VideoLoader
    from fiwi_filmmusik.pipeline import Pipeline

    return Pipeline(
        loader=VideoLoader(),
        chunker=AudioChunker(chunk_duration=chunk_duration, overlap=0.0),
        classifier=_get_classifier(threshold=threshold),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=ShazamDetectionClient(),
        output_dir=output_dir,
        export_csv=export_csv,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm the classifier so the first request doesn't block
    await asyncio.to_thread(_get_classifier)
    yield


app = FastAPI(title="FIWI Filmmusik Analyzer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/cancel/{run_id}")
async def cancel_run(run_id: str) -> dict:
    with _cancel_lock:
        ev = _cancel_events.get(run_id)
    if ev:
        ev.set()
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Run not found")


@app.post("/analyze")
async def analyze(
    file: UploadFile,
    api: str = Form("shazam"),
    chunk_duration: float = Form(10.0),
    threshold: float = Form(0.2),
    max_segment: float = Form(0.0),
    export_csv: str = Form("0"),
) -> StreamingResponse:
    """Accept a video file and stream SSE progress events while running the pipeline."""
    contents = await file.read()
    original_name = file.filename or "video.mp4"
    run_id = str(uuid.uuid4())

    return StreamingResponse(
        _run_pipeline_sse(
            contents,
            original_name,
            run_id=run_id,
            chunk_duration=chunk_duration,
            threshold=threshold,
            max_segment_duration=max_segment or None,
            export_csv=export_csv == "1",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_pipeline_sse(
    video_bytes: bytes,
    original_name: str,
    run_id: str,
    chunk_duration: float = 10.0,
    threshold: float = 0.2,
    max_segment_duration: float | None = None,
    export_csv: bool = False,
) -> AsyncGenerator[str, None]:
    """Run the pipeline in a thread and yield SSE events."""
    cancel_event = threading.Event()
    with _cancel_lock:
        _cancel_events[run_id] = cancel_event

    ev_queue: queue.Queue[dict] = queue.Queue()

    def on_progress(step: str, detail: str) -> None:
        if cancel_event.is_set():
            raise _PipelineCancelled()
        if step == "waveform":
            ev_queue.put({"step": "waveform", **json.loads(detail)})
        else:
            ev_queue.put({"step": step, "detail": detail})

    def run_sync() -> None:
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_path = tmp_dir / original_name
        try:
            tmp_path.write_bytes(video_bytes)
            pipeline = _build_pipeline(_OUTPUT_DIR, chunk_duration=chunk_duration, threshold=threshold, export_csv=export_csv)
            results = pipeline.run(tmp_path, on_progress=on_progress, max_segment_duration=max_segment_duration)
            ev_queue.put({"step": "done", "results": results.to_dict()})
        except _PipelineCancelled:
            ev_queue.put({"step": "cancelled"})
        except Exception as exc:
            import traceback
            ev_queue.put({"step": "error", "detail": str(exc), "traceback": traceback.format_exc()})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with _cancel_lock:
                _cancel_events.pop(run_id, None)

    task = asyncio.create_task(asyncio.to_thread(run_sync))

    def encode(ev: dict) -> str:
        return f"data: {json.dumps(ev)}\n\n"

    yield encode({"step": "run_id", "run_id": run_id})

    while True:
        # Drain all pending events
        while True:
            try:
                ev = ev_queue.get_nowait()
                yield encode(ev)
                if ev.get("step") in ("done", "error", "cancelled"):
                    return
            except queue.Empty:
                break

        if task.done():
            # Final drain after task completion
            while not ev_queue.empty():
                ev = ev_queue.get_nowait()
                yield encode(ev)
                if ev.get("step") in ("done", "error", "cancelled"):
                    return
            # Task ended without a terminal event
            exc = task.exception()
            if exc:
                yield encode({"step": "error", "detail": str(exc)})
            return

        await asyncio.sleep(0.05)


@app.delete("/history/{video_name}")
async def delete_history_item(video_name: str) -> dict:
    """Delete a run directory from the cache."""
    run_dir = (_OUTPUT_DIR / video_name).resolve()
    if not str(run_dir).startswith(str(_OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    shutil.rmtree(run_dir)
    return {"status": "deleted"}


@app.get("/history")
async def history() -> list[dict]:
    """Return the last 10 completed runs, newest first."""
    items = []
    if _OUTPUT_DIR.exists():
        dirs = sorted(
            (p for p in _OUTPUT_DIR.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for run_dir in dirs:
            results_path = run_dir / "results.json"
            if not results_path.exists():
                continue
            try:
                data = json.loads(results_path.read_text())
                cues = data.get("cues", [])
                items.append({
                    "video_name": run_dir.name,
                    "timestamp": results_path.stat().st_mtime,
                    "total": len(cues),
                    "identified": sum(1 for c in cues if c.get("title")),
                    "results": data,
                })
            except Exception:
                pass
            if len(items) >= 10:
                break
    return items


@app.get("/output/{video_name}/segments/{filename}")
async def serve_segment(video_name: str, filename: str) -> FileResponse:
    """Serve a WAV segment file."""
    seg_path = _OUTPUT_DIR / video_name / "segments" / filename
    if not seg_path.exists() or not seg_path.is_file():
        raise HTTPException(status_code=404, detail="Segment not found")
    return FileResponse(seg_path, media_type="audio/wav")
