"""FastAPI web application for the SoundtrackID pipeline."""

import asyncio
import json
import mimetypes
import os
import queue
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import sys as _sys

# Load .env early so TMDb credentials are available downstream
try:
    from dotenv import load_dotenv
    if getattr(_sys, "frozen", False):
        # In the bundled .app, .env lives next to the binary
        _env_path = Path(_sys.executable).parent / ".env"
        if _env_path.exists():
            load_dotenv(_env_path)
        # Also try _MEIPASS for spec-bundled .env
        _meipass_env = Path(getattr(_sys, "_MEIPASS", ".")) / ".env"
        if _meipass_env.exists():
            load_dotenv(_meipass_env, override=False)
    else:
        load_dotenv()
except ImportError:
    pass

if getattr(_sys, "frozen", False):
    _STATIC_DIR = Path(_sys._MEIPASS) / "static"
else:
    _STATIC_DIR = Path(__file__).parent / "static"

if getattr(_sys, "frozen", False):
    # Writable user directory inside the macOS app sandbox
    _OUTPUT_DIR = Path.home() / "Library" / "Application Support" / "SoundtrackID" / "cache"
else:
    _OUTPUT_DIR = Path("./output/cache")

# ── API-key settings (managed via the UI, persisted to settings.json) ─────────
# Maps a UI field name → the environment variable the clients read. Values entered
# in the settings page are stored in settings.json and pushed into os.environ, so
# subsequent analysis runs (which build clients fresh) pick them up. This layers
# on top of .env: a saved value overrides the .env default.
_SETTINGS_PATH = _OUTPUT_DIR.parent / "settings.json"
_KEY_FIELDS: dict[str, str] = {
    "acrcloud_host": "ACRCLOUD_HOST",
    "acrcloud_access_key": "ACRCLOUD_ACCESS_KEY",
    "acrcloud_access_secret": "ACRCLOUD_ACCESS_SECRET",
    "audd_api_token": "AUDD_API_TOKEN",
    "tmdb_bearer": "FIWI_TMDB_API_TOKEN",
    "tmdb_key": "FIWI_TMDB_KEY",
}
# Fields shown in cleartext (not secrets); everything else is masked in responses.
_PLAIN_FIELDS = {"acrcloud_host"}


def _load_settings_into_env() -> None:
    """On startup, apply any saved API keys over the environment."""
    try:
        data = json.loads(_SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return
    for field, env_var in _KEY_FIELDS.items():
        val = data.get(field)
        if val:
            os.environ[env_var] = str(val)


def _mask(value: str) -> str:
    if not value:
        return ""
    return ("•" * 4 + value[-4:]) if len(value) > 4 else "•" * len(value)


def _settings_state() -> dict:
    """Report which keys are configured, without exposing secret values."""
    try:
        saved = json.loads(_SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    state = {}
    for field, env_var in _KEY_FIELDS.items():
        value = saved.get(field) or os.environ.get(env_var) or ""
        state[field] = {
            "configured": bool(value),
            "preview": value if field in _PLAIN_FIELDS else _mask(value),
        }
    return state

# ── Singleton classifier (loaded once at startup) ─────────────────────────────
_classifier = None
_classifier_lock = threading.Lock()

# ── Per-run cancellation registry ─────────────────────────────────────────────
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()

# ── Per-enrich cancellation registry ──────────────────────────────────────────
_enrich_cancel_events: dict[str, threading.Event] = {}
_enrich_cancel_lock = threading.Lock()

# ── Pre-analysis upload staging ───────────────────────────────────────────────
# /identify stores the uploaded file here and returns a staging_id; /analyze then
# consumes it by id, so a large film is uploaded only once even though the user
# confirms film metadata in between.
_STAGING_DIR = _OUTPUT_DIR.parent / "staging"
_staging: dict[str, Path] = {}
_staging_lock = threading.Lock()
_STAGING_TTL_S = 6 * 3600


def _prune_staging() -> None:
    """Best-effort removal of staged uploads left behind by abandoned confirmations."""
    import time
    try:
        for d in _STAGING_DIR.iterdir():
            try:
                if time.time() - d.stat().st_mtime > _STAGING_TTL_S:
                    shutil.rmtree(d, ignore_errors=True)
                    with _staging_lock:
                        _staging.pop(d.name, None)
            except OSError:
                pass
    except (OSError, FileNotFoundError):
        pass


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


def _build_pipeline(output_dir: Path, chunk_duration: float = 10.0, threshold: float = 0.2, export_csv: bool = False, api: str = "acrcloud"):
    from fiwi_filmmusik.aggregator import ChunkAggregator
    from fiwi_filmmusik.chunker import AudioChunker
    from fiwi_filmmusik.detection import build_detection_client
    from fiwi_filmmusik.isolators import DummyIsolator
    from fiwi_filmmusik.loaders import VideoLoader
    from fiwi_filmmusik.pipeline import Pipeline

    return Pipeline(
        loader=VideoLoader(),
        chunker=AudioChunker(chunk_duration=chunk_duration, overlap=0.0),
        classifier=_get_classifier(threshold=threshold),
        aggregator=ChunkAggregator(gap_tolerance=1.0),
        isolator=DummyIsolator(),
        detection_client=build_detection_client(api),
        output_dir=output_dir,
        export_csv=export_csv,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply saved API keys, then pre-warm the classifier so the first request
    # doesn't block.
    _load_settings_into_env()
    await asyncio.to_thread(_get_classifier)
    yield


app = FastAPI(title="SoundtrackID", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/settings")
async def get_settings() -> dict:
    """Which API keys are configured (masked, never the raw secret)."""
    return _settings_state()


@app.post("/settings")
async def save_settings(payload: dict = Body(...)) -> dict:
    """Persist API keys entered in the settings page and apply them to the
    environment so subsequent analysis runs use them. An empty string clears a
    field. Only known fields are accepted."""
    try:
        saved = json.loads(_SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    for field, value in payload.items():
        if field not in _KEY_FIELDS:
            continue
        env_var = _KEY_FIELDS[field]
        if value:
            saved[field] = str(value)
            os.environ[env_var] = str(value)
        else:  # empty string clears the key
            saved.pop(field, None)
            os.environ.pop(env_var, None)
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_SETTINGS_PATH, saved)
    return _settings_state()


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    # No-store so CSS/JS edits are picked up on a normal reload (the page is a
    # single self-contained file, so stale caching otherwise hides UI changes).
    return FileResponse(
        _STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.post("/cancel/{run_id}")
async def cancel_run(run_id: str) -> dict:
    with _cancel_lock:
        ev = _cancel_events.get(run_id)
    if ev:
        ev.set()
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Run not found")


@app.post("/identify")
async def identify(file: UploadFile, online: str = Form("0")) -> dict:
    """Stage an uploaded video and return prefilled film metadata for confirmation.

    Reads MP4 container tags immediately; if `online=1`, additionally resolves the
    film via TMDb/Wikidata/imdbapi. The returned `staging_id` is passed to /analyze
    so the file is not re-uploaded.
    """
    _prune_staging()
    contents = await file.read()
    original_name = file.filename or "video.mp4"
    staging_id = str(uuid.uuid4())
    staging_dir = _STAGING_DIR / staging_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / original_name
    staged_path.write_bytes(contents)
    with _staging_lock:
        _staging[staging_id] = staged_path

    film: dict = {}
    try:
        from fiwi_filmmusik.metadata.mp4_tags import read_mp4_tags
        film = read_mp4_tags(staged_path) or {}
    except Exception:
        film = {}

    if online == "1":
        try:
            import httpx
            from fiwi_filmmusik.metadata.film import FilmLookup
            client = httpx.Client(timeout=15.0)
            try:
                lookup = FilmLookup(
                    client,
                    tmdb_bearer_token=os.environ.get("FIWI_TMDB_API_TOKEN"),
                    tmdb_api_key=os.environ.get("FIWI_TMDB_KEY"),
                )
                info = lookup.lookup(
                    imdb_id=film.get("imdb_id"),
                    tmdb_id=film.get("tmdb_id"),
                    title=film.get("title"),
                    year=film.get("year"),
                )
                # First non-null wins: keep tag values, fill gaps from the lookup.
                resolved = info.to_dict()
                film = {k: (film.get(k) if film.get(k) is not None else resolved.get(k)) for k in {*film, *resolved}}
            finally:
                client.close()
        except Exception:
            pass

    return {"staging_id": staging_id, "original_name": original_name, "film": film}


@app.post("/film/lookup")
async def film_lookup(hint: dict = Body(default={})) -> dict:
    """Resolve film metadata online from a hint (imdb_id/tmdb_id/title/year). No file needed."""
    import httpx
    from fiwi_filmmusik.metadata.film import FilmLookup
    year = hint.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (ValueError, TypeError):
        year = None
    tmdb_id = hint.get("tmdb_id")
    try:
        tmdb_id = int(tmdb_id) if tmdb_id not in (None, "") else None
    except (ValueError, TypeError):
        tmdb_id = None
    client = httpx.Client(timeout=15.0)
    try:
        lookup = FilmLookup(
            client,
            tmdb_bearer_token=os.environ.get("FIWI_TMDB_API_TOKEN"),
            tmdb_api_key=os.environ.get("FIWI_TMDB_KEY"),
        )
        info = lookup.lookup(
            imdb_id=(hint.get("imdb_id") or None),
            tmdb_id=tmdb_id,
            title=(hint.get("title") or None),
            year=year,
        )
        return {"film": info.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Film lookup failed: {exc}")
    finally:
        client.close()


@app.post("/analyze")
async def analyze(
    file: UploadFile | None = File(None),
    staging_id: str = Form(""),
    film: str = Form(""),
    api: str = Form("acrcloud"),
    chunk_duration: float = Form(10.0),
    threshold: float = Form(0.2),
    max_segment: float = Form(0.0),
    export_csv: str = Form("0"),
) -> StreamingResponse:
    """Accept a video file (or a staged upload) and stream SSE progress events.

    Either `file` is uploaded directly, or `staging_id` references a file already
    staged by /identify (in which case `film` carries the user-confirmed metadata).
    """
    if staging_id:
        with _staging_lock:
            staged_path = _staging.pop(staging_id, None)
        if not staged_path or not staged_path.exists():
            raise HTTPException(status_code=400, detail="Staged upload not found or expired")
        contents = staged_path.read_bytes()
        original_name = staged_path.name
        shutil.rmtree(staged_path.parent, ignore_errors=True)
    elif file is not None:
        contents = await file.read()
        original_name = file.filename or "video.mp4"
    else:
        raise HTTPException(status_code=400, detail="No file or staging_id provided")

    film_override = None
    if film:
        try:
            parsed = json.loads(film)
            if isinstance(parsed, dict) and any(v not in (None, "") for v in parsed.values()):
                film_override = {k: v for k, v in parsed.items() if v not in (None, "")}
        except (ValueError, TypeError):
            film_override = None

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
            api=api,
            film_override=film_override,
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
    api: str = "acrcloud",
    film_override: dict | None = None,
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
            if film_override:
                # User-confirmed metadata from the pre-analysis step wins.
                container_tags = film_override
            else:
                from fiwi_filmmusik.metadata.mp4_tags import read_mp4_tags
                container_tags = read_mp4_tags(tmp_path) or None
            pipeline = _build_pipeline(_OUTPUT_DIR, chunk_duration=chunk_duration, threshold=threshold, export_csv=export_csv, api=api)
            results = pipeline.run(tmp_path, on_progress=on_progress, max_segment_duration=max_segment_duration, container_tags=container_tags)
            result_dict = results.to_dict()

            # Temporal re-rank: prefer a period-plausible candidate over an
            # anachronistic top hit, now that the film year is known.
            try:
                from fiwi_filmmusik.temporal import temporal_rerank
                film_year = (result_dict.get("film") or {}).get("year")
                temporal_rerank(result_dict, film_year)
            except Exception:
                import traceback as _tb
                print("temporal rerank failed:\n" + _tb.format_exc())

            # Automatic composer lookup: join composer / death-year / work directly
            # from external APIs (MusicBrainz → Wikidata) for every cue that carries
            # an ISRC. Best-effort — a network failure must not fail the analysis.
            try:
                import httpx
                from fiwi_filmmusik.metadata.enricher import enrich as _enrich_results

                def _music_cb(step: str, **kw) -> None:
                    if cancel_event.is_set():
                        raise _PipelineCancelled()
                    if step == "music_start":
                        ev_queue.put({"step": "enriching", "detail": f"0/{kw.get('total', 0)}"})
                    elif step == "music_cue":
                        ev_queue.put({"step": "enriching", "detail": f"{kw.get('index')}/{kw.get('total')}"})

                _client = httpx.Client(timeout=15.0)
                try:
                    _enrich_results(
                        results=result_dict,
                        film_hint=None,
                        http_client=_client,
                        scope="music",
                        music_isrc_only=True,
                        progress_cb=_music_cb,
                    )
                finally:
                    _client.close()
            except _PipelineCancelled:
                raise
            except Exception:
                import traceback as _tb
                print("automatic composer enrichment failed:\n" + _tb.format_exc())

            # Persist the source video + a thumbnail filmstrip so the UI can show a
            # video preview synced to the timeline (also for history/reopened runs).
            try:
                run_dir = _OUTPUT_DIR / Path(original_name).stem
                ext = Path(original_name).suffix or ".mp4"
                video_dest = run_dir / f"source{ext}"
                shutil.move(str(tmp_path), str(video_dest))
                from fiwi_filmmusik.thumbnails import extract_filmstrip
                film = extract_filmstrip(video_dest, run_dir / "filmstrip.jpg")
                result_dict["video"] = {"file": f"source{ext}", "filmstrip": film}
                _atomic_write_json(run_dir / "results.json", result_dict)
            except Exception:
                import traceback as _tb
                print("video/filmstrip persist failed:\n" + _tb.format_exc())
            # The waveform (up to ~60k points) is already streamed separately via
            # the "waveform" step and persisted in results.json; omit it from the
            # done event so the streamed payload stays small on long films.
            done_results = {k: v for k, v in result_dict.items() if k != "waveform"}
            ev_queue.put({"step": "done", "results": done_results})
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


def _safe_run_dir(video_name: str) -> Path:
    """Resolve a run directory, guarding against path traversal via video_name."""
    run_dir = (_OUTPUT_DIR / video_name).resolve()
    if not str(run_dir).startswith(str(_OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return run_dir


@app.post("/results/{video_name}")
async def save_results(video_name: str, payload: dict = Body(...)) -> dict:
    """Persist manual edits to a run's cues (fill-ins, corrections, removals).

    Only the `cues` array is replaced; film/waveform/etc. on disk are preserved.
    """
    cues = payload.get("cues")
    if not isinstance(cues, list):
        raise HTTPException(status_code=422, detail="Body must be {\"cues\": [...]}")
    results_path = _safe_run_dir(video_name) / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        data = json.loads(results_path.read_text())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read results.json: {exc}")
    data["cues"] = cues
    _atomic_write_json(results_path, data)
    return {"status": "saved", "cues": len(cues)}


@app.get("/output/{video_name}/segments/{filename}")
async def serve_segment(video_name: str, filename: str) -> FileResponse:
    """Serve a WAV segment file."""
    seg_path = _safe_run_dir(video_name) / "segments" / Path(filename).name
    if not seg_path.exists() or not seg_path.is_file():
        raise HTTPException(status_code=404, detail="Segment not found")
    return FileResponse(seg_path, media_type="audio/wav")


@app.get("/output/{video_name}/video")
async def serve_video(video_name: str) -> FileResponse:
    """Serve the stored source video (supports HTTP Range for scrubbing)."""
    run_dir = _safe_run_dir(video_name)
    matches = sorted(run_dir.glob("source.*")) if run_dir.is_dir() else []
    if not matches:
        raise HTTPException(status_code=404, detail="Video not found")
    video_path = matches[0]
    media_type = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    return FileResponse(video_path, media_type=media_type)


@app.get("/output/{video_name}/filmstrip")
async def serve_filmstrip(video_name: str) -> FileResponse:
    """Serve the thumbnail filmstrip sprite for a run."""
    strip_path = _safe_run_dir(video_name) / "filmstrip.jpg"
    if not strip_path.exists() or not strip_path.is_file():
        raise HTTPException(status_code=404, detail="Filmstrip not found")
    return FileResponse(strip_path, media_type="image/jpeg")


# ── Enrichment ────────────────────────────────────────────────────────────────


@app.post("/enrich/cancel/{enrich_id}")
async def cancel_enrich(enrich_id: str) -> dict:
    with _enrich_cancel_lock:
        ev = _enrich_cancel_events.get(enrich_id)
    if ev:
        ev.set()
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Enrich run not found")


@app.post("/enrich/{video_name}")
async def enrich_run(
    video_name: str,
    payload: dict = Body(default_factory=dict),
) -> StreamingResponse:
    """Enrich an existing results.json with film + composer metadata."""
    run_dir = (_OUTPUT_DIR / video_name).resolve()
    if not str(run_dir).startswith(str(_OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    results_path = run_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    film_hint = {
        "imdb_id": (payload or {}).get("imdb_id") or None,
        "tmdb_id": (payload or {}).get("tmdb_id") or None,
        "title": (payload or {}).get("title") or None,
        "year": (payload or {}).get("year") or None,
    }
    scope = (payload or {}).get("scope") or "all"
    if scope not in ("film", "music", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'film', 'music' or 'all'")
    enrich_id = str(uuid.uuid4())

    return StreamingResponse(
        _run_enrich_sse(results_path, film_hint, enrich_id, scope=scope),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_enrich_sse(
    results_path: Path,
    film_hint: dict,
    enrich_id: str,
    scope: str = "all",
) -> AsyncGenerator[str, None]:
    """Run enrichment in a thread and yield SSE events."""
    cancel_event = threading.Event()
    with _enrich_cancel_lock:
        _enrich_cancel_events[enrich_id] = cancel_event

    ev_queue: queue.Queue[dict] = queue.Queue()

    from fiwi_filmmusik.metadata.enricher import EnrichmentCancelled, enrich

    def progress_cb(step: str, **kwargs) -> None:
        if cancel_event.is_set():
            raise EnrichmentCancelled()
        ev_queue.put({"step": step, **kwargs})

    def run_sync() -> None:
        try:
            import httpx
        except ImportError as exc:
            ev_queue.put({"step": "error", "detail": f"httpx not installed: {exc}"})
            return

        try:
            results = json.loads(results_path.read_text())
        except Exception as exc:
            ev_queue.put({"step": "error", "detail": f"Failed to load results.json: {exc}"})
            return

        tmdb_bearer = os.environ.get("FIWI_TMDB_API_TOKEN")
        tmdb_key = os.environ.get("FIWI_TMDB_KEY")

        client = httpx.Client(timeout=15.0)
        try:
            enriched = enrich(
                results=results,
                film_hint=film_hint,
                http_client=client,
                tmdb_bearer_token=tmdb_bearer,
                tmdb_api_key=tmdb_key,
                progress_cb=progress_cb,
            )
        except EnrichmentCancelled:
            # Persist whatever we have so far
            _atomic_write_json(results_path, results)
            ev_queue.put({"step": "cancelled"})
            return
        except Exception as exc:
            import traceback
            ev_queue.put({"step": "error", "detail": str(exc), "traceback": traceback.format_exc()})
            return
        finally:
            client.close()
            with _enrich_cancel_lock:
                _enrich_cancel_events.pop(enrich_id, None)

        try:
            _atomic_write_json(results_path, enriched)
        except Exception as exc:
            ev_queue.put({"step": "error", "detail": f"Failed to write results.json: {exc}"})
            return

        ev_queue.put({"step": "done", "results": enriched})

    task = asyncio.create_task(asyncio.to_thread(run_sync))

    def encode(ev: dict) -> str:
        return f"data: {json.dumps(ev)}\n\n"

    yield encode({"step": "enrich_id", "enrich_id": enrich_id})

    while True:
        while True:
            try:
                ev = ev_queue.get_nowait()
                yield encode(ev)
                if ev.get("step") in ("done", "error", "cancelled"):
                    return
            except queue.Empty:
                break

        if task.done():
            while not ev_queue.empty():
                ev = ev_queue.get_nowait()
                yield encode(ev)
                if ev.get("step") in ("done", "error", "cancelled"):
                    return
            exc = task.exception()
            if exc:
                yield encode({"step": "error", "detail": str(exc)})
            return

        await asyncio.sleep(0.05)


# ── Resume detection after a rate limit ───────────────────────────────────────


def _apply_detection_to_cue(cue: dict, det) -> None:
    """Copy a DetectionResult onto an existing cue (same keys as models._build_cues)."""
    md = det.metadata or {}
    cue["provider"] = det.provider
    cue["title"] = det.title
    cue["artist"] = det.artist
    cue["album"] = md.get("album")
    cue["confidence"] = det.confidence
    for key in ("release_year", "isrc", "genre", "photo_url", "youtube_link",
                "spotify_url", "apple_music_url", "musicbrainz_recording_id",
                "musicbrainz_url", "agreement", "votes"):
        cue[key] = md.get(key)


@app.post("/resume/{video_name}")
async def resume_run(video_name: str, payload: dict = Body(default_factory=dict)) -> StreamingResponse:
    """Re-run identification on the still-unidentified cues of a rate-limited run,
    reading their saved segment WAVs. Fills gaps without re-analyzing the video."""
    run_dir = _safe_run_dir(video_name)
    results_path = run_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    api = (payload or {}).get("api") or "acrcloud"
    return StreamingResponse(
        _run_resume_sse(run_dir, results_path, api),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_resume_sse(run_dir: Path, results_path: Path, api: str) -> AsyncGenerator[str, None]:
    ev_queue: queue.Queue[dict] = queue.Queue()

    def run_sync() -> None:
        import numpy as np
        from scipy.io import wavfile

        from fiwi_filmmusik.detection import RateLimitError, build_detection_client
        from fiwi_filmmusik.models import MusicSegment

        try:
            results = json.loads(results_path.read_text())
        except Exception as exc:
            ev_queue.put({"step": "error", "detail": f"Failed to load results.json: {exc}"})
            return

        cues = results.get("cues") or []
        todo = [c for c in cues if not c.get("title") and c.get("audio_file")]
        total = len(todo)
        if total == 0:
            results["rate_limited"] = False
            results_lean = {k: v for k, v in results.items() if k != "waveform"}
            _atomic_write_json(results_path, results)
            ev_queue.put({"step": "done", "results": results_lean})
            return

        try:
            client = build_detection_client(api)
        except Exception as exc:
            ev_queue.put({"step": "error", "detail": str(exc)})
            return

        ev_queue.put({"step": "resuming", "detail": f"0/{total}"})
        still_limited = False
        done_n = 0
        for cue in todo:
            wav_path = run_dir / cue["audio_file"]
            if not wav_path.exists():
                continue
            try:
                sr, audio = wavfile.read(wav_path)
                if audio.dtype == np.int16:
                    audio = audio.astype(np.float32) / 32767.0
                elif audio.dtype == np.int32:
                    audio = audio.astype(np.float32) / 2147483647.0
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                seg = MusicSegment(audio=audio, start_time=0.0, end_time=len(audio) / sr, sample_rate=sr)
                det = client.detect(seg)
            except RateLimitError:
                still_limited = True
                break
            except Exception:
                continue
            if det.title:
                _apply_detection_to_cue(cue, det)
            done_n += 1
            ev_queue.put({"step": "resuming", "detail": f"{done_n}/{total}"})

        # Temporal re-rank the freshly-identified cues before enriching.
        try:
            from fiwi_filmmusik.temporal import temporal_rerank
            temporal_rerank(results, (results.get("film") or {}).get("year"))
        except Exception:
            pass

        # Enrich the (now) identified cues, best-effort.
        try:
            import httpx

            from fiwi_filmmusik.metadata.enricher import enrich as _enrich

            def _cb(step: str, **kw) -> None:
                if step == "music_start":
                    ev_queue.put({"step": "enriching", "detail": f"0/{kw.get('total', 0)}"})
                elif step == "music_cue":
                    ev_queue.put({"step": "enriching", "detail": f"{kw.get('index')}/{kw.get('total')}"})

            with httpx.Client(timeout=15.0) as _c:
                _enrich(results=results, film_hint=None, http_client=_c,
                        scope="music", music_isrc_only=True, progress_cb=_cb)
        except Exception:
            import traceback
            print("resume enrichment failed:\n" + traceback.format_exc())

        results["rate_limited"] = still_limited
        try:
            _atomic_write_json(results_path, results)
        except Exception as exc:
            ev_queue.put({"step": "error", "detail": f"Failed to write results.json: {exc}"})
            return
        results_lean = {k: v for k, v in results.items() if k != "waveform"}
        ev_queue.put({"step": "done", "results": results_lean})

    task = asyncio.create_task(asyncio.to_thread(run_sync))

    def encode(ev: dict) -> str:
        return f"data: {json.dumps(ev)}\n\n"

    while True:
        drained = False
        try:
            while True:
                ev = ev_queue.get_nowait()
                yield encode(ev)
                if ev.get("step") in ("done", "error"):
                    return
        except queue.Empty:
            drained = True
        if task.done() and drained and ev_queue.empty():
            exc = task.exception()
            if exc:
                yield encode({"step": "error", "detail": str(exc)})
            return
        await asyncio.sleep(0.05)


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
