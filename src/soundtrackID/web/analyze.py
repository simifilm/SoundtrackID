"""Starting a run from the page: stage the film and confirm what it is
(/identify, /film/lookup), analyze it (/analyze), analyze a run's film again
(/rerun) or identify the cues a provider left pending (/resume)."""

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Body, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from soundtrackID import analysis
from soundtrackID.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackID.web import data
from soundtrackID.web.stream import event_stream, sse

log = logging.getLogger(__name__)

router = APIRouter()

# ── Singleton classifier (loaded once at startup) ─────────────────────────────
_classifier = None
_classifier_lock = threading.Lock()

# ── Pre-analysis upload staging ───────────────────────────────────────────────
# /identify stores the uploaded file here and returns a staging_id; /analyze then
# consumes it by id, so a large film is uploaded only once even though the user
# confirms film metadata in between.
_staging: dict[str, Path] = {}
_staging_lock = threading.Lock()
_STAGING_TTL_S = 6 * 3600


def _prune_staging() -> None:
    """Best-effort removal of staged uploads left behind by abandoned confirmations."""
    import time

    try:
        for d in data.staging_dir().iterdir():
            try:
                if time.time() - d.stat().st_mtime > _STAGING_TTL_S:
                    shutil.rmtree(d, ignore_errors=True)
                    with _staging_lock:
                        _staging.pop(d.name, None)
            except OSError:
                pass
    except (OSError, FileNotFoundError):
        pass


def get_classifier():
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                from soundtrackID.classifiers import load_classifier

                _classifier = load_classifier(hf=bool(os.environ.get("FIWI_HF")))
    return _classifier


def _build_pipeline(threshold: float, api: str, bridge_gap: float, hop: float):
    from soundtrackID.pipeline import build_pipeline

    return build_pipeline(
        get_classifier(),
        api,
        threshold=threshold,
        hop=hop,
        bridge_gap=bridge_gap,
    )


@router.post("/identify")
async def identify(file: UploadFile) -> dict:
    """Stage an uploaded video and return prefilled film metadata for confirmation.

    Reads MP4 container tags; /film/lookup fills the gaps online on request. The
    returned `staging_id` is passed to /analyze so the file is not re-uploaded.
    """
    _prune_staging()
    # Base name only: the client-supplied name must not escape the staging folder.
    original_name = Path(file.filename or "").name or "video.mp4"
    staging_id = str(uuid.uuid4())
    staging_dir = data.staging_dir() / staging_id
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


@router.post("/film/lookup")
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


@router.post("/analyze")
async def analyze(
    staging_id: str = Form(""),
    film: str = Form(""),
    api: str = Form(DEFAULT_API),
    threshold: float = Form(MUSIC_THRESHOLD),
    bridge_gap: float = Form(BRIDGE_GAP_S),
    hop: float = Form(CLASSIFICATION_HOP_S),
    max_segment: float = Form(MAX_SEGMENT_S),  # 0: each whole
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

    return event_stream(
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


@router.post("/rerun/{video_name}")
async def rerun(
    video_name: str,
    api: str = Form(DEFAULT_API),
    threshold: float = Form(MUSIC_THRESHOLD),
    bridge_gap: float = Form(BRIDGE_GAP_S),
    hop: float = Form(CLASSIFICATION_HOP_S),
    max_segment: float = Form(MAX_SEGMENT_S),  # 0: each whole
) -> StreamingResponse:
    """Analyze a run's stored source video again with new settings, as a new run.

    Keeps the run's film metadata, so the video needn't be uploaded again.
    """
    run = data.find_run(video_name)
    source = run.video
    if source is None:
        raise HTTPException(status_code=404, detail="No stored video for this run")
    results = data.read_results(run) if run.exists() else {}
    # The new run is named after the film's file, not after this run.
    file_name = Path(results.get("source") or video_name).stem + source.suffix
    # Hard-linked, so both runs share the video on disk; copied where the drive
    # has no hard links.
    video = Path(tempfile.mkdtemp()) / file_name
    try:
        os.link(source, video)
    except OSError:
        shutil.copy2(source, video)

    return event_stream(
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
    threshold: float,
    bridge_gap: float,
    hop: float,
    max_segment_duration: float | None,
    api: str,
    film_override: dict | None,
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
                data.runs_dir(),
                # User-confirmed metadata from the pre-analysis step; else the tags.
                film=film_override,
                max_segment_duration=max_segment_duration,
                keep_video=True,
                on_progress=on_progress,
                cancel=cancel,
            )
        finally:
            shutil.rmtree(video.parent, ignore_errors=True)

    async for event in sse(work, run_id):
        yield event


# ── Resume detection after a rate limit ───────────────────────────────────────


@router.post("/resume/{video_name}")
async def resume_run(
    video_name: str, payload: dict = Body(default_factory=dict)
) -> StreamingResponse:
    """Re-run identification on the still-unidentified cues of a rate-limited run,
    reading their saved segment WAVs. Fills gaps without re-analyzing the video."""
    run = data.find_run(video_name)
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    api = (payload or {}).get("api") or None  # None: the run's own providers
    return event_stream(
        sse(
            lambda on_progress, cancel: analysis.resume(
                run.path, api, on_progress=on_progress, cancel=cancel
            ),
            str(uuid.uuid4()),
        )
    )
