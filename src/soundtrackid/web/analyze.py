"""Starting a run from the page: stage the film and confirm what it is
(/identify, /film/search, /film/lookup), analyze it (/analyze), or several as a
batch (/batches), analyze a run's film again (/rerun), continue a batch
(/batches/…/continue), or identify the cues a provider left pending, of a run
(/resume) or of each run of a batch (/batches/…/resume). All but the first
three each start a job (web/jobs.py) and return its ID, whose progress the
page follows."""

import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Form, HTTPException, UploadFile

from soundtrackid import analysis
from soundtrackid.batches import Batch
from soundtrackid.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackid.web import data, jobs

log = logging.getLogger(__name__)

router = APIRouter()

# ── Singleton classifier (loaded once at startup) ─────────────────────────────
_classifier = None
_classifier_lock = threading.Lock()

# ── Pre-analysis upload staging ───────────────────────────────────────────────
# /identify stores the uploaded file here and returns a staging_id; /analyze then
# consumes it by id, so a large film is uploaded only once even though the user
# confirms film metadata in between. The film a single analysis took stays in its
# staging folder until the run has it.
_staging: dict[str, Path] = {}
_staging_lock = threading.Lock()
_STAGING_TTL_S = 6 * 3600


def _prune_staging() -> None:
    """Remove the uploads staged more than _STAGING_TTL_S ago that no analysis
    took: their confirmation was left. An analysis removes the one it took once
    it ends, and the server those left when it stopped (clear_staging)."""
    now = time.time()
    with _staging_lock:
        for staging_id, path in list(_staging.items()):
            try:
                expired = now - path.parent.stat().st_mtime > _STAGING_TTL_S
            except OSError:
                expired = True  # gone
            if expired:
                del _staging[staging_id]
                shutil.rmtree(path.parent, ignore_errors=True)


def clear_staging() -> None:
    """Remove what the staging folder holds as the server starts: uploads and
    the films of analyses the server left when it stopped (e.g. the app
    closed), none of which it can use any more."""
    shutil.rmtree(data.staging_dir(), ignore_errors=True)


def get_classifier():
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                from soundtrackid.classifiers import load_classifier

                _classifier = load_classifier(hf=bool(os.environ.get("FIWI_HF")))
    return _classifier


def _build_pipeline(threshold: float, api: str, bridge_gap: float, hop: float):
    from soundtrackid.pipeline import build_pipeline

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

    Reads MP4 container tags; /film/search and /film/lookup find the film online
    on request. The
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
        from soundtrackid.metadata.mp4_tags import read_mp4_tags

        film = read_mp4_tags(staged_path) or {}
    except Exception:
        log.warning("Could not read the tags of %s", original_name, exc_info=True)
        film = {}

    return {"staging_id": staging_id, "original_name": original_name, "film": film}


def _film_hint(hint: dict) -> dict:
    """What a request knows about a film (imdb_id/tmdb_id/title/year), as the
    lookup takes it: numbers typed into the form arrive as text, and what is
    unusable becomes None."""

    def number(key: str) -> int | None:
        value = hint.get(key)
        try:
            return int(value) if value not in (None, "") else None
        except (ValueError, TypeError):
            return None

    return {
        "imdb_id": hint.get("imdb_id") or None,
        "tmdb_id": number("tmdb_id"),
        "title": hint.get("title") or None,
        "year": number("year"),
    }


@router.post("/film/search")
async def film_search(hint: dict = Body(default={})) -> dict:
    """The films a hint may mean, for the user to choose from. No file needed."""
    from soundtrackid.metadata.film import search_films

    try:
        candidates = search_films(**_film_hint(hint))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Film search failed: {exc}")
    return {"candidates": candidates}


@router.post("/film/lookup")
async def film_lookup(hint: dict = Body(default={})) -> dict:
    """Resolve film metadata online from a hint (imdb_id/tmdb_id/title/year,
    and media_type: whether the TMDb ID is a film's "movie" or a series' "tv").

    No file needed.
    """
    from soundtrackid.metadata.film import MEDIA_TYPES, lookup_film

    media_type = hint.get("media_type")
    try:
        film = lookup_film(
            **_film_hint(hint),
            media_type=media_type if media_type in MEDIA_TYPES else None,
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
) -> dict:
    """Analyze a staged upload, as a job.

    `staging_id` references a file staged by /identify; `film` carries the
    user-confirmed metadata.
    """
    [video] = _take_staged([staging_id])

    try:
        film_override = _confirmed_film(json.loads(film)) if film else None
    except ValueError:
        film_override = None

    return _start_analysis(
        video,
        threshold=threshold,
        bridge_gap=bridge_gap,
        hop=hop,
        max_segment_duration=max_segment or None,
        api=api,
        film_override=film_override,
    )


@router.post("/rerun/{video_name}")
async def rerun(
    video_name: str,
    api: str = Form(DEFAULT_API),
    threshold: float = Form(MUSIC_THRESHOLD),
    bridge_gap: float = Form(BRIDGE_GAP_S),
    hop: float = Form(CLASSIFICATION_HOP_S),
    max_segment: float = Form(MAX_SEGMENT_S),  # 0: each whole
) -> dict:
    """Analyze a run's stored source video again with new settings, as a new run
    and a job.

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
    # has no hard links. In a staging folder of its own, as an upload taken.
    video = data.staging_dir() / str(uuid.uuid4()) / file_name
    video.parent.mkdir(parents=True)
    try:
        os.link(source, video)
    except OSError:
        shutil.copy2(source, video)

    return _start_analysis(
        video,
        threshold=threshold,
        bridge_gap=bridge_gap,
        hop=hop,
        max_segment_duration=max_segment or None,
        api=api,
        film_override=results.get("film"),
    )


def _start_analysis(
    video: Path,
    threshold: float,
    bridge_gap: float,
    hop: float,
    max_segment_duration: float | None,
    api: str,
    film_override: dict | None,
) -> dict:
    """Analyze `video` as a job; returns {"job_id": its ID}.

    `video`, named like the film's file, lies in a staging folder of its own,
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

    return {"job_id": jobs.start("analysis", work).id}


@router.post("/batches")
async def start_batch(
    films: str = Form(...),
    api: str = Form(DEFAULT_API),
    threshold: float = Form(MUSIC_THRESHOLD),
    bridge_gap: float = Form(BRIDGE_GAP_S),
    hop: float = Form(CLASSIFICATION_HOP_S),
    max_segment: float = Form(MAX_SEGMENT_S),  # 0: each whole
) -> dict:
    """Analyze several staged uploads one after another as a batch, as a job.

    `films` lists them in order, as JSON: each {"staging_id": …, "film": {…}},
    a file staged by /identify and the user-confirmed metadata. The batch's
    folder is there once this returns, holding the films until their run has
    them, so that the batch can be continued (/batches/…/continue).
    """
    try:
        items = json.loads(films)
        staging_ids = [item["staging_id"] for item in items]
    except (ValueError, TypeError, KeyError):
        raise HTTPException(
            status_code=422,
            detail='films must be [{"staging_id": …, "film": {…}}, …]',
        )
    if not staging_ids:
        raise HTTPException(status_code=422, detail="No films")
    staged = _take_staged(staging_ids)
    settings = {
        "classifier": "hf" if os.environ.get("FIWI_HF") else "onnx",
        "api": api,
        "threshold": threshold,
        "hop": hop,
        "window": None,
        "bridge_gap": bridge_gap,
        "isolation": False,
        "max_segment_duration": max_segment or None,
    }
    try:
        batch = Batch.create(
            data.runs_dir(),
            [
                (path, _confirmed_film(item.get("film")))
                for path, item in zip(staged, items)
            ],
            settings,
            take_files=True,
        )
    finally:
        for path in staged:
            shutil.rmtree(path.parent, ignore_errors=True)
    return _analyze_batch(batch)


@router.post("/batches/{name}/continue")
async def continue_batch(name: str) -> dict:
    """Analyze the films the batch didn't get to (it was cancelled, the app
    closed) and those that failed, with its settings, as a job."""
    batch = data.find_batch(name)
    _check_idle(batch)
    return _analyze_batch(batch)


def _analyze_batch(batch: Batch) -> dict:
    """Analyze the batch's films that have no run, as a job; returns
    {"job_id": its ID}."""

    def work(on_progress, cancel):
        pipeline = analysis.pipeline_for(get_classifier(), batch.load()["settings"])
        return analysis.analyze_batch(
            pipeline, batch, on_progress=on_progress, cancel=cancel
        )

    return {"job_id": jobs.start("batch", work, name=batch.name).id}


@router.post("/batches/{name}/resume")
async def resume_batch(name: str) -> dict:
    """Identify the cues providers left pending in each of the batch's runs,
    from their saved audio, each run with its own providers, as a job."""
    batch = data.find_batch(name)
    _check_idle(batch)
    if any(batch.run(run) is not None for run in jobs.under_way("resume")):
        raise HTTPException(status_code=409, detail="A run of the batch is resuming")
    job = jobs.start(
        "batch_resume",
        lambda on_progress, cancel: analysis.resume_batch(
            batch, on_progress=on_progress, cancel=cancel
        ),
        name=batch.name,
    )
    return {"job_id": job.id}


def _check_idle(batch: Batch) -> None:
    """A 409 while the batch is analyzed or resumed."""
    if batch.name in jobs.under_way("batch", "batch_resume"):
        raise HTTPException(status_code=409, detail="The batch is under way")


def _take_staged(staging_ids: list[str]) -> list[Path]:
    """The files staged by /identify under these IDs, staged no more; a 400,
    taking none, when one isn't (any more)."""
    with _staging_lock:
        paths = [_staging.get(staging_id) for staging_id in staging_ids]
        if len(set(staging_ids)) < len(staging_ids) or not all(
            path is not None and path.exists() for path in paths
        ):
            raise HTTPException(
                status_code=400, detail="Staged upload not found or expired"
            )
        for staging_id in staging_ids:
            del _staging[staging_id]
    return paths


def _confirmed_film(film: object) -> dict | None:
    """The film's details the user confirmed, without those left empty; None
    when there are none."""
    if not isinstance(film, dict):
        return None
    return {k: v for k, v in film.items() if v not in (None, "")} or None


# ── Resume detection after a rate limit ───────────────────────────────────────


@router.post("/resume/{video_name}")
async def resume_run(
    video_name: str, payload: dict = Body(default_factory=dict)
) -> dict:
    """Re-run identification on the still-unidentified cues of a rate-limited run,
    reading their saved segment WAVs, as a job. Fills gaps without re-analyzing
    the video."""
    run = data.find_run(video_name)
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    # Not twice at once: both would ask the provider about the same cues.
    if run.name in jobs.under_way("resume") or run.path.parent.name in (
        jobs.under_way("batch_resume")
    ):
        raise HTTPException(status_code=409, detail="The run is resuming")
    api = (payload or {}).get("api") or None  # None: the run's own providers
    job = jobs.start(
        "resume",
        lambda on_progress, cancel: analysis.resume(
            run.path, api, on_progress=on_progress, cancel=cancel
        ),
        name=run.name,
    )
    return {"job_id": job.id}
