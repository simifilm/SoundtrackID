"""The analysis of a video, the same for the web app and the CLI.

analyze: the pipeline, then the date re-ranking and the composer lookup, then the
run's results.json, saved once the run is complete. resume: identify the cues a
provider left pending, from their saved audio.
"""

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from soundtrackID.defaults import DEFAULT_API
from soundtrackID.models import MATCH_KEYS, match_fields
from soundtrackID.runs import Run

log = logging.getLogger(__name__)

ProgressCb = Callable[..., None]


class Cancelled(Exception):
    """The run was cancelled."""


def analyze(
    pipeline,
    video: Path,
    api: str,
    runs_dir: Path,
    film: dict[str, Any] | None = None,
    max_segment_duration: float | None = None,
    keep_video: bool = False,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Analyze `video` as a new run and return its results.

    The run gets a folder of its own in `runs_dir`, named after the video's file
    and the time, so analyzing the same film again keeps the earlier run. `api`
    names the provider(s) the pipeline identifies with; `film` is what is known
    about the film, by default the file's MP4 tags. `keep_video` moves the video
    into the run folder, with a filmstrip, for the app's video preview. A
    cancelled or failed run leaves no folder behind.
    """
    # The same file started within the same second would get this run's folder,
    # and Run.create refuses it.
    run = Run.create(runs_dir / f"{video.stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}")
    progress = _checked(on_progress, cancel)
    try:
        if not film:
            from soundtrackID.metadata.mp4_tags import read_mp4_tags

            film = read_mp4_tags(video) or None
        output = pipeline.run(
            video,
            on_progress=progress,
            max_segment_duration=max_segment_duration,
            container_tags=film,
        )
        output.video_name = run.name
        results = output.to_dict()
        for cue in results["cues"]:
            clip = output.pending_audio.get(cue["segment_id"])
            if clip is not None:
                cue["audio_file"] = run.write_audio(cue["segment_id"], clip)
        # Saved so a resume continues with the same providers.
        results["providers"] = [p.strip() for p in api.split(",") if p.strip()]
        # Everything the run was analyzed with, so it can be repeated exactly.
        results["settings"] = {
            **pipeline.settings,
            "max_segment_duration": max_segment_duration,
        }
        _rerank_and_look_up_composers(results.get("film"), results["cues"], progress)
        if keep_video:
            _keep_video(video, run, results)
        run.save_timeline(output.timeline())
        run.save(results)
        return results
    except BaseException:
        run.remove()
        raise


def resume(
    run_dir: Path,
    api: str | None = None,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Identify the pending cues of a run (skipped at a provider's limit) from
    their saved audio, and save and return the run's results. `api` names the
    provider(s), by default the ones the run was analyzed with.

    Only the cues identified now are re-ranked and looked up: the others keep
    what they have, e.g. a composer edited by hand. The provider can take
    minutes, so what this resume found goes into the results as they are when
    it saves, keeping the edits saved meanwhile. A cancelled resume saves
    nothing, so its cues stay pending.
    """
    from soundtrackID.detection import (
        ProviderAuthError,
        RateLimitError,
        build_detection_client,
    )

    progress = _checked(on_progress, cancel)
    run = Run(run_dir)
    results = run.load()
    cues = results.get("cues") or []
    todo = [c for c in cues if c.get("pending") and c.get("audio_file")]
    total = len(todo)
    if total == 0:
        return results

    # Runs from before the providers were saved get the default.
    client = build_detection_client(
        api or ",".join(results.get("providers") or [DEFAULT_API])
    )
    _report(progress, "resuming", f"0/{total}")
    done_n = 0
    changes: dict[str, dict[str, Any]] = {}  # what this resume sets, by segment_id
    identified = []  # the cues this resume identifies
    answered_audio = []  # of cues no longer pending
    for cue in todo:
        try:
            clip = run.read_audio(cue["audio_file"])
            if clip is None:
                # Nothing left to send, so no resume can identify it.
                changes[cue["segment_id"]] = {"pending": False, "audio_file": None}
                continue
            det = client.detect(clip)
        except RateLimitError:
            break  # still refused: the rest stays pending
        except ProviderAuthError:
            raise  # nothing saved: the cues stay pending for the right keys
        except Exception:
            log.exception("Identifying %s failed", cue.get("segment_id"))
            continue  # stays pending: a later resume tries again
        if det.title:
            cue.update(match_fields(det))
            identified.append(cue)
        # Answered, a match or not; only pending cues keep their audio.
        changes[cue["segment_id"]] = {"pending": False, "audio_file": None}
        answered_audio.append(cue["audio_file"])
        done_n += 1
        _report(progress, "resuming", f"{done_n}/{total}")

    _rerank_and_look_up_composers(results.get("film"), identified, progress)
    for cue in identified:
        found = (*MATCH_KEYS, "enrichment", "temporal_pick")
        changes[cue["segment_id"]].update({k: cue[k] for k in found if k in cue})

    def apply(saved: dict[str, Any]) -> None:
        for cue in saved.get("cues") or []:
            cue.update(changes.get(cue.get("segment_id"), {}))

    results = run.update(apply)
    # Only once saved: until then the saved cues are still pending, and a resume
    # needs their audio.
    for audio_file in answered_audio:
        run.remove_audio(audio_file)
    return results


def _checked(
    on_progress: ProgressCb | None, cancel: threading.Event | None
) -> ProgressCb:
    """`on_progress`, stopping the run at its next step once it is cancelled."""

    def progress(step: str, **fields: Any) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if on_progress:
            on_progress(step, **fields)

    return progress


def _report(progress: ProgressCb, step: str, detail: str) -> None:
    log.info("[%s] %s", step, detail)
    progress(step, detail=detail)


def _rerank_and_look_up_composers(
    film: dict[str, Any] | None, cues: list[dict[str, Any]], progress: ProgressCb
) -> None:
    """Prefer period-plausible matches for `cues` now that the film's year is
    known, then look up their composers (MusicBrainz → Wikidata). Best-effort:
    a failure leaves the cues as they are; only a cancel stops the run."""
    try:
        from soundtrackID.temporal import temporal_rerank

        temporal_rerank({"cues": cues}, (film or {}).get("year"))
    except Exception:
        log.exception("Temporal rerank failed")

    def on_lookup(step: str, **fields: Any) -> None:
        if step == "music_start":
            _report(progress, "enriching", f"0/{fields['total']}")
        elif step == "music_cue":
            _report(progress, "enriching", f"{fields['index']}/{fields['total']}")

    try:
        import httpx

        from soundtrackID.metadata.enricher import enrich

        with httpx.Client(timeout=15.0) as client:
            enrich(results={"cues": cues}, http_client=client, progress_cb=on_lookup)
    except Cancelled:
        raise
    except Exception:
        log.exception("Composer lookup failed")


def _keep_video(video: Path, run: Run, results: dict[str, Any]) -> None:
    """Move the video into the run folder, with a thumbnail filmstrip, so the app
    shows it synced to the timeline (also when the run is reopened). Best-effort:
    the results are saved without it."""
    try:
        dest = run.keep_video(video)
        from soundtrackID.thumbnails import extract_filmstrip

        filmstrip = extract_filmstrip(dest, run.filmstrip_path)
        results["video"] = {"file": dest.name, "filmstrip": filmstrip}
    except Exception:
        log.exception("Keeping the video and its filmstrip failed")
