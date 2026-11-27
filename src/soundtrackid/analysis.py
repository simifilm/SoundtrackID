"""The analysis of a video, the same for the web app and the CLI.

analyze: the pipeline, then the date re-ranking and the composer lookup, then the
run's results.json, saved once the run is complete. analyze_batch: several
videos, one after another, as a batch. resume: identify the cues a provider left
pending, from their saved audio; resume_batch: those of each run of a batch.
"""

import copy
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from soundtrackid.batches import Batch
from soundtrackid.defaults import DEFAULT_API
from soundtrackid.results import MATCH_KEYS, alternatives, match_fields
from soundtrackid.runs import Run

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
    cancelled or failed run leaves no folder behind. Until the run is saved, it
    holds its lock (Run.lock), so others see it under way.
    """
    # The same file started within the same second would get this run's folder,
    # and Run.create refuses it.
    run = Run.create(runs_dir / f"{video.stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}")
    lock = run.lock()
    progress = _checked(on_progress, cancel)
    try:
        if not film:
            from soundtrackid.metadata.mp4_tags import read_mp4_tags

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
        lock.release()  # first: Windows removes no file held open
        run.remove()
        raise
    finally:
        lock.release()


def pipeline_for(classifier, settings: dict[str, Any]):
    """The pipeline that analyzes with `settings`, as a batch saves them
    (Batch.create)."""
    from soundtrackid.pipeline import build_pipeline

    return build_pipeline(
        classifier,
        settings["api"],
        threshold=settings["threshold"],
        hop=settings["hop"],
        window=settings["window"],
        bridge_gap=settings["bridge_gap"],
        isolation=settings["isolation"],
    )


def analyze_batch(
    pipeline,
    batch: Batch,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Analyze the films of `batch` that have no run, one after another, each
    as analyze() does, into the batch's folder, with the batch's settings
    (`pipeline` built from them: pipeline_for); return the batch's record: its
    name, and its films (batches). Those of a new batch are all to analyze;
    one continued analyzes those it didn't get to, and those that failed.

    A film that fails is recorded as failed, with why, and the batch goes on.
    Wrong API keys stop it, as they would fail every film after, and so does a
    cancel: they are raised, and the runs done stay. A film whose file the
    batch took over keeps it in its run, as analyze's keep_video does, and in
    the batch's queue until then, also if it fails, to be tried again.

    Reports the record ("batch") as the batch starts and as each film starts
    and ends, and each film's progress in between, as analyze() does. Holds
    the batch's lock meanwhile (Batch.lock): locks.Busy when another holds it.
    """
    with batch.lock():
        return _analyze_batch(pipeline, batch, on_progress, cancel)


def _analyze_batch(pipeline, batch, on_progress, cancel) -> dict[str, Any]:
    from soundtrackid.detection import ProviderAuthError

    record = batch.load()
    settings = record["settings"]
    films = record["films"]

    def report() -> None:
        if on_progress:
            # A copy: the record changes as the batch goes on.
            on_progress("batch", name=batch.name, **copy.deepcopy(record))

    def update(entry: dict[str, Any], **fields: Any) -> None:
        entry.update(fields)
        batch.save(record)
        report()

    todo = [(n, entry) for n, entry in enumerate(films, 1) if entry["status"] != "done"]
    batch.remove_unfinished_runs()
    for _, entry in todo:
        entry.update(status="queued", error=None)
    batch.save(record)
    report()  # all to do queued
    for number, entry in todo:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        log.info("Film %d of %d: %s", number, len(films), entry["source"])
        video = batch.film_file(number, entry)
        if not video.is_file():
            update(entry, status="failed", error=f"Its file is gone: {video}")
            continue
        update(entry, status="analyzing")
        try:
            results = analyze(
                pipeline,
                video,
                settings["api"],
                batch.path,
                film=entry["film"],
                max_segment_duration=settings["max_segment_duration"],
                keep_video=batch.took_over(entry),
                on_progress=on_progress,
                cancel=cancel,
            )
        except Cancelled:
            update(entry, status="cancelled")
            raise
        except ProviderAuthError as exc:
            update(entry, status="failed", error=str(exc))
            raise
        except Exception as exc:
            log.exception("Analyzing %s failed", entry["source"])
            update(entry, status="failed", error=str(exc))
            continue
        if batch.took_over(entry):
            batch.dequeue(number)
        update(entry, status="done", run=results["video_name"])
    return {"name": batch.name, **record}


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
    from soundtrackid.detection import (
        ProviderAuthError,
        RateLimitError,
        build_detection_client,
    )

    progress = _checked(on_progress, cancel)
    run = Run(run_dir)
    results = run.load()
    todo = _pending(results)
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
            fields = match_fields(det)
            cue.update(fields, alternatives=alternatives([fields]))
            identified.append(cue)
        # Answered, a match or not; only pending cues keep their audio.
        changes[cue["segment_id"]] = {"pending": False, "audio_file": None}
        answered_audio.append(cue["audio_file"])
        done_n += 1
        _report(progress, "resuming", f"{done_n}/{total}")

    _rerank_and_look_up_composers(results.get("film"), identified, progress)
    for cue in identified:
        found = (*MATCH_KEYS, "alternatives", "enrichment", "temporal_pick")
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


def resume_batch(
    batch: Batch,
    api: str | None = None,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Resume each of the batch's runs with pending cues, one after another,
    as resume() does; return the batch's record, as analyze_batch does.

    Reports each run as its resume starts ("batch_resume": the run, its number
    and how many), then its progress as resume() does. Wrong API keys stop it,
    and so does a cancel: the runs resumed before keep what they found. Holds
    the batch's lock meanwhile, as analyze_batch does.
    """
    with batch.lock():
        record = batch.load()
        runs = [
            run
            for film in record["films"]
            if film["status"] == "done"
            and (run := batch.run(film["run"])) is not None
            and run.exists()
            and _pending(run.load())
        ]
        for number, run in enumerate(runs, 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            log.info("Run %d of %d: %s", number, len(runs), run.name)
            if on_progress:
                on_progress(
                    "batch_resume", run=run.name, number=number, total=len(runs)
                )
            resume(run.path, api, on_progress=on_progress, cancel=cancel)
        return {"name": batch.name, **record}


def _pending(results: dict[str, Any]) -> list[dict[str, Any]]:
    """The cues a resume identifies: pending, with their audio."""
    return [
        c for c in results.get("cues") or [] if c.get("pending") and c.get("audio_file")
    ]


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
        from soundtrackid.temporal import temporal_rerank

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

        from soundtrackid.metadata.enricher import enrich

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
        from soundtrackid.thumbnails import extract_filmstrip, frame_rate

        filmstrip = extract_filmstrip(dest, run.filmstrip_path)
        results["video"] = {
            "file": dest.name,
            "filmstrip": filmstrip,
            "fps": frame_rate(dest),
        }
    except Exception:
        log.exception("Keeping the video and its filmstrip failed")
