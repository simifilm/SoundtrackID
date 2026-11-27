"""The saved runs and batches: listing and deleting them (/history, /batches),
their results and the edits made to them (/results), a cue's song chosen by
hand among others (/results/…/song), and their timeline, video, filmstrip and
exports (/output)."""

import logging
import mimetypes
import shutil
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

from soundtrackid.batches import Batch
from soundtrackid.exports import build_mediapkg, build_vian_project, results_to_csv
from soundtrackid.results import SONG_KEYS, apply_edits, chosen_song, take_song
from soundtrackid.runs import Run
from soundtrackid.thumbnails import video_info
from soundtrackid.web import data, jobs

log = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/history/{video_name}")
async def delete_history_item(video_name: str) -> dict:
    """Delete a run directory from the cache."""
    run = data.find_run(video_name)
    if not run.path.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    shutil.rmtree(run.path)
    return {"status": "deleted"}


@router.delete("/history")
async def delete_history() -> dict:
    """Delete every run directory from the cache."""
    runs_dir = data.runs_dir()
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            if run_dir.is_dir():
                shutil.rmtree(run_dir)
    return {"status": "deleted"}


@router.delete("/batches/{name}")
async def delete_batch(name: str) -> dict:
    """Delete a batch's folder: its films' runs, and what else it holds."""
    batch = data.find_batch(name)
    if batch.name in jobs.under_way("batch"):
        raise HTTPException(
            status_code=409, detail="The batch is under way: cancel it first"
        )
    shutil.rmtree(batch.path)
    return {"status": "deleted"}


@router.get("/history")
async def history() -> list[dict]:
    """Every completed run and every batch, the latest first; GET
    /results/{video_name} returns a run's results.

    A run: {"kind": "run", video_name, timestamp, total, identified}, its cues
    counted. A batch: {"kind": "batch", name, timestamp (its start), films},
    each film with its source, status, error and run, as a run's above when
    it is done. The latest is the one whose folder, or a run's of a batch,
    changed last.
    """
    entries = []  # (when it changed last, entry)
    runs_dir = data.runs_dir()
    batches_under_way = jobs.under_way("batch")
    for folder in runs_dir.iterdir() if runs_dir.exists() else ():
        try:
            batch = Batch(folder)
            if batch.exists():
                entry = _batch_entry(batch, batch.name in batches_under_way)
                runs = [
                    batch.path / f["run"]["video_name"]
                    for f in entry["films"]
                    if f["run"]
                ]
                changed = max(p.stat().st_mtime for p in (folder, *runs))
                entries.append((changed, entry))
            elif Run(folder).exists():
                entry = {"kind": "run", **_run_entry(Run(folder))}
                entries.append((folder.stat().st_mtime, entry))
        except Exception:
            log.warning("Could not list %s", folder, exc_info=True)
    entries.sort(key=lambda e: e[0], reverse=True)
    return [entry for _, entry in entries]


def _run_entry(run: Run) -> dict:
    cues = run.load().get("cues", [])
    return {
        "video_name": run.name,
        "timestamp": run.results_path.stat().st_mtime,
        "total": len(cues),
        "identified": sum(1 for c in cues if c.get("title")),
    }


def _batch_entry(batch: Batch, under_way: bool) -> dict:
    """A batch for the history, its films' status as of now: also "deleted",
    a film done whose run is gone, and "not_analyzed", one queued or being
    analyzed when the batch stopped (a cancel, wrong API keys, the app closed).
    """
    record = batch.load()
    films = []
    for film in record["films"]:
        status, run = film["status"], None
        if status == "done":
            found = batch.run(film["run"])
            if found is not None and found.exists():
                run = _run_entry(found)
            else:
                status = "deleted"
        elif status in ("queued", "analyzing") and not under_way:
            status = "not_analyzed"
        films.append(
            {
                "source": film["source"],
                "status": status,
                "error": film["error"],
                "run": run,
            }
        )
    return {
        "kind": "batch",
        "name": batch.name,
        "timestamp": datetime.fromisoformat(record["started"]).timestamp(),
        "films": films,
    }


@router.get("/results/{video_name}")
async def get_results(video_name: str) -> dict:
    """A run's results.json, in the current format."""
    return data.read_results(data.find_run(video_name))


@router.get("/output/{video_name}/timeline")
async def get_timeline(video_name: str) -> dict:
    """A run's waveform and music classification, for the timeline and the
    Classes view."""
    run = data.find_run(video_name)
    if not run.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return run.timeline()


@router.post("/results/{video_name}")
async def save_cues(video_name: str, payload: dict = Body(...)) -> dict:
    """Persist manual edits to a run's cues (fill-ins, corrections, removals).

    Only the `cues` array is replaced; film/waveform/etc. on disk are preserved.
    Each cue must be one of the run's; where it is and whether it is pending
    (with its audio, which a resume reads and removes) stay as saved. Returns the
    results as saved, with what follows from the edit (derived), which the page
    shows.
    """
    cues = payload.get("cues")
    if not isinstance(cues, list):
        raise HTTPException(status_code=422, detail='Body must be {"cues": [...]}')
    run = data.find_run(video_name)
    data.read_results(run)  # 404 or 500 rather than a save

    def edit(results: dict) -> None:
        results["cues"] = apply_edits(results.get("cues") or [], cues)

    try:
        results = run.update(edit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "saved", "cues": len(cues), "results": results}


@router.post("/results/{video_name}/cues/{segment_id}/song")
def choose_song(video_name: str, segment_id: str, choice: dict = Body(...)) -> dict:
    """Give one cue the song chosen by hand: one of those the providers named in
    it, {"alternative": <its index>}, or one typed in, {"title", "artist"}. Its
    composer is looked up again, as after an analysis: the one found was the
    old song's. The run's other cues keep theirs. Returns the results as saved.
    """
    run = data.find_run(video_name)
    cues = data.read_results(run).get("cues") or []
    cue = next((c for c in cues if c.get("segment_id") == segment_id), None)
    if cue is None:
        raise HTTPException(status_code=404, detail="Cue not found")
    try:
        song = chosen_song(cue, choice)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    take_song(cue, song)
    cue.update(temporal_pick=False, enrichment=None)
    _look_up_composer(cue)
    # The lookup takes a while: saved into the results as they are by then.
    changes = {key: cue[key] for key in (*SONG_KEYS, "temporal_pick", "enrichment")}

    def choose(results: dict) -> None:
        for saved in results.get("cues") or []:
            if saved.get("segment_id") == segment_id:
                saved.update(changes)

    return {"status": "saved", "results": run.update(choose)}


def _look_up_composer(cue: dict) -> None:
    """The composer of the cue's song, as after an analysis (by its ISRC).
    Best-effort: without it, the cue has none."""
    try:
        import httpx

        from soundtrackid.metadata.enricher import enrich

        with httpx.Client(timeout=15.0) as client:
            enrich(results={"cues": [cue]}, http_client=client)
    except Exception:
        log.exception("Composer lookup failed")


@router.get("/output/{video_name}/video")
async def serve_video(video_name: str) -> FileResponse:
    """Serve the stored source video (supports HTTP Range for scrubbing)."""
    video_path = data.find_run(video_name).video
    if video_path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    media_type = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    return FileResponse(video_path, media_type=media_type)


@router.get("/output/{video_name}/csv")
async def export_csv(video_name: str) -> Response:
    """This run's results as CSV, built from its results.json as it is now."""
    results = data.read_results(data.find_run(video_name))
    filename = f"{video_name}_results.csv"
    return Response(
        content=results_to_csv(results),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/output/{video_name}/vian")
async def export_vian(video_name: str) -> Response:
    """This run's cues as a VIAN project zip. VIAN counts in frames, so this
    needs the run's video for its frame rate."""
    run = data.find_run(video_name)
    results = data.read_results(run)
    video = video_info(run.video) if run.video else None
    if video is None:
        raise HTTPException(status_code=404, detail="No stored video for this run")
    filename = f"{video_name}_vian_project.zip"
    return Response(
        content=build_vian_project(results.get("cues", []), video),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/output/{video_name}/mediapkg")
async def export_mediapkg(video_name: str) -> Response:
    """This run's cues as a .mediapkg, MAVA's exchange format."""
    run = data.find_run(video_name)
    results = data.read_results(run)
    video = video_info(run.video) if run.video else None
    src = results.get("source") or video_name
    filename = f"{video_name}.mediapkg"
    return Response(
        content=build_mediapkg(results.get("cues", []), video_name, src, video),
        media_type="application/vnd.mava.mediapkg+zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/output/{video_name}/filmstrip")
async def serve_filmstrip(video_name: str) -> FileResponse:
    """Serve the thumbnail filmstrip sprite for a run."""
    strip_path = data.find_run(video_name).filmstrip_path
    if not strip_path.is_file():
        raise HTTPException(status_code=404, detail="Filmstrip not found")
    return FileResponse(strip_path, media_type="image/jpeg")
