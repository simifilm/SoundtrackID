"""The saved runs: listing and deleting them (/history), their results and the
edits made to them (/results), and their timeline, video, filmstrip and exports
(/output)."""

import io
import json
import logging
import mimetypes
import shutil
import zipfile

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

from soundtrackid.exports import build_mava_mapping, build_mava_tsv, results_to_csv
from soundtrackid.results import apply_edits
from soundtrackid.runs import Run
from soundtrackid.web import data

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


@router.get("/history")
async def history() -> list[dict]:
    """Summarize every completed run, newest first; GET /results/{video_name}
    returns a run's results."""
    items = []
    runs_dir = data.runs_dir()
    if runs_dir.exists():
        dirs = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir()),
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


@router.get("/output/{video_name}/mava")
async def export_mava(video_name: str) -> Response:
    """Export this run's cues as a MAVA/VIAN-Export compatible TSV + mapping JSON.

    MAVA (https://github.com/sdsc-ordes/mava-api) has no flat JSON export format;
    it ingests a TSV file plus a JSON "mapping" via POST /graph/import_tsv, which
    turns each row into a mava:AnnotationSegment. Bundles both files as a zip since
    that endpoint expects them together.
    """
    results = data.read_results(data.find_run(video_name))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mava_annotations.tsv", build_mava_tsv(results.get("cues", [])))
        zf.writestr(
            "mava_mapping.json", json.dumps(build_mava_mapping(video_name), indent=2)
        )

    filename = f"{video_name}_mava_export.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/output/{video_name}/filmstrip")
async def serve_filmstrip(video_name: str) -> FileResponse:
    """Serve the thumbnail filmstrip sprite for a run."""
    strip_path = data.find_run(video_name).filmstrip_path
    if not strip_path.is_file():
        raise HTTPException(status_code=404, detail="Filmstrip not found")
    return FileResponse(strip_path, media_type="image/jpeg")
