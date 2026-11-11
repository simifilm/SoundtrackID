"""FastAPI web application for the SoundtrackID pipeline."""

import asyncio
import copy
import io
import json
import logging
import mimetypes
import os
import shutil
import sys as _sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Callable

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from soundtrackID import analysis
from soundtrackID.envfiles import load_env_files
from soundtrackID.models import apply_edits, results_to_csv
from soundtrackID.runs import Run

log = logging.getLogger(__name__)

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
        load_env_files()  # .env.dev, then .env
except ImportError:
    pass

if getattr(_sys, "frozen", False):
    _STATIC_DIR = Path(_sys._MEIPASS) / "static"
else:
    _STATIC_DIR = Path(__file__).parent / "static"


def _app_data_dir() -> Path:
    """Where the installed app keeps its runs, saved API keys and log: the
    user's application data folder (the local one on Windows: runs keep films)."""
    if _sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "SoundtrackID"
    return Path.home() / "Library" / "Application Support" / "SoundtrackID"


def _move_misplaced_windows_data(new: Path) -> None:
    """Move what the Windows app kept under the macOS path in the home folder,
    before it had a folder of its own, into `new`, so earlier runs and saved
    keys stay. What `new` has already is kept; the old folders go once empty."""
    old = Path.home() / "Library" / "Application Support" / "SoundtrackID"
    if not old.is_dir():
        return
    new.mkdir(parents=True, exist_ok=True)
    for item in old.iterdir():
        if not (new / item.name).exists():
            try:
                shutil.move(str(item), str(new / item.name))
            except OSError:
                log.warning("Could not move %s to %s", item, new, exc_info=True)
    for folder in (old, old.parent, old.parent.parent):
        try:
            folder.rmdir()  # only while empty
        except OSError:
            break


if getattr(_sys, "frozen", False):
    _OUTPUT_DIR = _app_data_dir() / "cache"
else:
    _OUTPUT_DIR = Path("./output/cache")

# The server's log (main.py), next to the runs.
LOG_PATH = _OUTPUT_DIR.parent / "soundtrackid.log"

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


def _get_classifier(threshold: float = 0.2):
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                if getattr(_sys, "frozen", False):
                    from soundtrackID.classifiers import OnnxClassifier

                    _classifier = OnnxClassifier(
                        model_dir=str(Path(_sys._MEIPASS) / "ast_model"),
                        music_labels=["Music"],
                        threshold=threshold,
                    )
                else:
                    onnx_model_dir = Path(__file__).parents[2] / "assets" / "ast_model"
                    use_hf = os.environ.get("FIWI_HF") or not onnx_model_dir.exists()
                    if use_hf:
                        from soundtrackID.classifiers import HuggingFaceClassifier

                        _classifier = HuggingFaceClassifier(
                            music_labels=["Music"], threshold=threshold
                        )
                    else:
                        from soundtrackID.classifiers import OnnxClassifier

                        _classifier = OnnxClassifier(
                            model_dir=str(onnx_model_dir),
                            music_labels=["Music"],
                            threshold=threshold,
                        )
    # A copy per run sharing the loaded model: setting the threshold on the
    # shared classifier changed it for analyses already running, too.
    run_classifier = copy.copy(_classifier)
    run_classifier.threshold = threshold
    return run_classifier


def _build_pipeline(
    threshold: float = 0.2,
    api: str = "acrcloud",
    bridge_gap: float = 2.0,
    hop: float = 5.0,
):
    from soundtrackID.pipeline import build_pipeline

    return build_pipeline(
        _get_classifier(threshold=threshold),
        api,
        hop=hop,
        bridge_gap=bridge_gap,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Move what an earlier Windows build kept under the macOS path, apply saved
    # API keys, then pre-warm the classifier so the first request doesn't block.
    if getattr(_sys, "frozen", False) and _sys.platform == "win32":
        _move_misplaced_windows_data(_OUTPUT_DIR.parent)
    _load_settings_into_env()
    await asyncio.to_thread(_get_classifier)
    yield


class _RevalidatedStaticFiles(StaticFiles):
    """The page's styles and scripts, checked for changes on every load (a 304
    when unchanged), so an edit or an update shows on a normal reload."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="SoundtrackID", lifespan=lifespan)
app.mount("/static", _RevalidatedStaticFiles(directory=str(_STATIC_DIR)), name="static")


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
    # No-store, like the styles and scripts it loads (revalidated), so UI
    # changes show on a normal reload instead of a stale cached page.
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
async def identify(file: UploadFile) -> dict:
    """Stage an uploaded video and return prefilled film metadata for confirmation.

    Reads MP4 container tags; /film/lookup fills the gaps online on request. The
    returned `staging_id` is passed to /analyze so the file is not re-uploaded.
    """
    _prune_staging()
    # Base name only: the client-supplied name must not escape the staging folder.
    original_name = Path(file.filename).name or "video.mp4"
    staging_id = str(uuid.uuid4())
    staging_dir = _STAGING_DIR / staging_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / original_name
    # In chunks, not read whole: a film can be several GB.
    with staged_path.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    with _staging_lock:
        _staging[staging_id] = staged_path

    film: dict = {}
    try:
        from soundtrackID.metadata.mp4_tags import read_mp4_tags

        film = read_mp4_tags(staged_path) or {}
    except Exception:
        log.warning("Could not read the tags of %s", original_name, exc_info=True)
        film = {}

    return {"staging_id": staging_id, "original_name": original_name, "film": film}


@app.post("/film/lookup")
async def film_lookup(hint: dict = Body(default={})) -> dict:
    """Resolve film metadata online from a hint (imdb_id/tmdb_id/title/year).

    No file needed.
    """
    from soundtrackID.metadata.film import lookup_film

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
    try:
        film = lookup_film(
            imdb_id=(hint.get("imdb_id") or None),
            tmdb_id=tmdb_id,
            title=(hint.get("title") or None),
            year=year,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Film lookup failed: {exc}")
    return {"film": film}


@app.post("/analyze")
async def analyze(
    staging_id: str = Form(""),
    film: str = Form(""),
    api: str = Form("acrcloud"),
    threshold: float = Form(0.2),
    bridge_gap: float = Form(2.0),
    hop: float = Form(5.0),
    max_segment: float = Form(0.0),
) -> StreamingResponse:
    """Analyze a staged upload and stream SSE progress events.

    `staging_id` references a file staged by /identify; `film` carries the
    user-confirmed metadata.
    """
    with _staging_lock:
        staged_path = _staging.pop(staging_id, None)
    if not staged_path or not staged_path.exists():
        raise HTTPException(
            status_code=400, detail="Staged upload not found or expired"
        )
    # The run takes the staged file over, moved rather than read.
    video = Path(tempfile.mkdtemp()) / staged_path.name
    shutil.move(staged_path, video)
    shutil.rmtree(staged_path.parent, ignore_errors=True)

    film_override = None
    if film:
        try:
            parsed = json.loads(film)
            if isinstance(parsed, dict) and any(
                v not in (None, "") for v in parsed.values()
            ):
                film_override = {k: v for k, v in parsed.items() if v not in (None, "")}
        except (ValueError, TypeError):
            film_override = None

    return _event_stream(
        _run_pipeline_sse(
            video,
            run_id=str(uuid.uuid4()),
            threshold=threshold,
            bridge_gap=bridge_gap,
            hop=hop,
            max_segment_duration=max_segment or None,
            api=api,
            film_override=film_override,
        )
    )


@app.post("/rerun/{video_name}")
async def rerun(
    video_name: str,
    api: str = Form("acrcloud"),
    threshold: float = Form(0.2),
    bridge_gap: float = Form(2.0),
    hop: float = Form(5.0),
    max_segment: float = Form(0.0),
) -> StreamingResponse:
    """Analyze a run's stored source video again with new settings, as a new run.

    Keeps the run's film metadata, so the video needn't be uploaded again.
    """
    run = _run(video_name)
    source = run.video
    if source is None:
        raise HTTPException(status_code=404, detail="No stored video for this run")
    results = _load(run) if run.exists() else {}
    # The new run is named after the film's file, not after this run.
    file_name = Path(results.get("source") or video_name).stem + source.suffix
    # Hard-linked, so both runs share the video on disk; copied where the drive
    # has no hard links.
    video = Path(tempfile.mkdtemp()) / file_name
    try:
        os.link(source, video)
    except OSError:
        shutil.copy2(source, video)

    return _event_stream(
        _run_pipeline_sse(
            video,
            run_id=str(uuid.uuid4()),
            threshold=threshold,
            bridge_gap=bridge_gap,
            hop=hop,
            max_segment_duration=max_segment or None,
            api=api,
            film_override=results.get("film"),
        )
    )


async def _run_pipeline_sse(
    video: Path,
    run_id: str,
    threshold: float = 0.2,
    bridge_gap: float = 2.0,
    hop: float = 5.0,
    max_segment_duration: float | None = None,
    api: str = "acrcloud",
    film_override: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Analyze `video` and stream the progress as SSE events.

    `video`, named like the film's file, lies in a temporary folder of its own,
    removed when the run ends; the run keeps the video in its run folder.
    """

    def work(on_progress, cancel):
        try:
            pipeline = _build_pipeline(
                threshold=threshold,
                api=api,
                bridge_gap=bridge_gap,
                hop=hop,
            )
            return analysis.analyze(
                pipeline,
                video,
                api,
                _OUTPUT_DIR,
                # User-confirmed metadata from the pre-analysis step; else the tags.
                film=film_override,
                max_segment_duration=max_segment_duration,
                keep_video=True,
                on_progress=on_progress,
                cancel=cancel,
            )
        finally:
            shutil.rmtree(video.parent, ignore_errors=True)

    async for event in _sse(work, run_id):
        yield event


async def _sse(
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


def _event_stream(events: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/history/{video_name}")
async def delete_history_item(video_name: str) -> dict:
    """Delete a run directory from the cache."""
    run = _run(video_name)
    if not run.path.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    shutil.rmtree(run.path)
    return {"status": "deleted"}


@app.delete("/history")
async def delete_history() -> dict:
    """Delete every run directory from the cache."""
    if _OUTPUT_DIR.exists():
        for run_dir in _OUTPUT_DIR.iterdir():
            if run_dir.is_dir():
                shutil.rmtree(run_dir)
    return {"status": "deleted"}


@app.get("/history")
async def history() -> list[dict]:
    """Summarize every completed run, newest first; GET /results/{video_name}
    returns a run's results."""
    items = []
    if _OUTPUT_DIR.exists():
        dirs = sorted(
            (p for p in _OUTPUT_DIR.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for run in map(Run, dirs):
            if not run.exists():
                continue
            try:
                cues = run.load().get("cues", [])
                items.append(
                    {
                        "video_name": run.name,
                        "timestamp": run.results_path.stat().st_mtime,
                        "total": len(cues),
                        "identified": sum(1 for c in cues if c.get("title")),
                    }
                )
            except Exception:
                log.warning("Could not list %s", run.path, exc_info=True)
    return items


def _run(video_name: str) -> Run:
    """A run by its name, guarding against path traversal via video_name.

    Runs are direct children of the cache, so anything else (the cache itself
    via ".", a sibling such as "../cache2") is rejected.
    """
    cache = _OUTPUT_DIR.resolve()
    run_dir = (cache / video_name).resolve()
    if run_dir.parent != cache:
        raise HTTPException(status_code=400, detail="Invalid path")
    return Run(run_dir)


def _load(run: Run) -> dict:
    """A run's results, or the error saying why there are none."""
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        return run.load()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read results.json: {exc}"
        )


@app.get("/results/{video_name}")
async def get_results(video_name: str) -> dict:
    """A run's results.json, in the current format."""
    return _load(_run(video_name))


@app.get("/output/{video_name}/timeline")
async def get_timeline(video_name: str) -> dict:
    """A run's waveform and music classification, for the timeline and the
    Classes view."""
    run = _run(video_name)
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return run.timeline()


@app.post("/results/{video_name}")
async def save_cues(video_name: str, payload: dict = Body(...)) -> dict:
    """Persist manual edits to a run's cues (fill-ins, corrections, removals).

    Only the `cues` array is replaced; film/waveform/etc. on disk are preserved.
    Each cue must be one of the run's; where it is and whether it is pending
    (with its audio, which a resume reads and removes) stay as saved.
    """
    cues = payload.get("cues")
    if not isinstance(cues, list):
        raise HTTPException(status_code=422, detail='Body must be {"cues": [...]}')
    run = _run(video_name)
    _load(run)  # 404 or 500 rather than a save

    def edit(results: dict) -> None:
        results["cues"] = apply_edits(results.get("cues") or [], cues)

    try:
        run.update(edit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "saved", "cues": len(cues)}


@app.get("/output/{video_name}/video")
async def serve_video(video_name: str) -> FileResponse:
    """Serve the stored source video (supports HTTP Range for scrubbing)."""
    video_path = _run(video_name).video
    if video_path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    media_type = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    return FileResponse(video_path, media_type=media_type)


@app.get("/output/{video_name}/csv")
async def export_csv(video_name: str) -> Response:
    """This run's results as CSV, built from its results.json as it is now."""
    data = _load(_run(video_name))
    filename = f"{video_name}_results.csv"
    return Response(
        content=results_to_csv(data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/output/{video_name}/mava")
async def export_mava(video_name: str) -> Response:
    """Export this run's cues as a MAVA/VIAN-Export compatible TSV + mapping JSON.

    MAVA (https://github.com/sdsc-ordes/mava-api) has no flat JSON export format;
    it ingests a TSV file plus a JSON "mapping" via POST /graph/import_tsv, which
    turns each row into a mava:AnnotationSegment. Bundles both files as a zip since
    that endpoint expects them together.
    """
    data = _load(_run(video_name))

    from soundtrackID.mava_export import build_mava_mapping, build_mava_tsv

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mava_annotations.tsv", build_mava_tsv(data.get("cues", [])))
        zf.writestr(
            "mava_mapping.json", json.dumps(build_mava_mapping(video_name), indent=2)
        )

    filename = f"{video_name}_mava_export.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/output/{video_name}/filmstrip")
async def serve_filmstrip(video_name: str) -> FileResponse:
    """Serve the thumbnail filmstrip sprite for a run."""
    strip_path = _run(video_name).filmstrip_path
    if not strip_path.is_file():
        raise HTTPException(status_code=404, detail="Filmstrip not found")
    return FileResponse(strip_path, media_type="image/jpeg")


# ── Resume detection after a rate limit ───────────────────────────────────────


@app.post("/resume/{video_name}")
async def resume_run(
    video_name: str, payload: dict = Body(default_factory=dict)
) -> StreamingResponse:
    """Re-run identification on the still-unidentified cues of a rate-limited run,
    reading their saved segment WAVs. Fills gaps without re-analyzing the video."""
    run = _run(video_name)
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    api = (payload or {}).get("api") or "acrcloud"
    return _event_stream(
        _sse(
            lambda on_progress, cancel: analysis.resume(
                run.path, api, on_progress=on_progress, cancel=cancel
            ),
            str(uuid.uuid4()),
        )
    )


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
