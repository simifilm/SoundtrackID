"""The analysis of a video, the same for the web app and the CLI.

analyze: the pipeline, then the date re-ranking and the composer lookup, then the
run's results.json, saved once the run is complete. resume: identify the cues a
provider left pending, from their saved audio.
"""

import shutil
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from soundtrackID.models import CUE_METADATA_KEYS, load_results, save_results

ProgressCb = Callable[..., None]


class Cancelled(Exception):
    """The run was cancelled."""


def analyze(
    pipeline,
    video: Path,
    api: str,
    film: dict[str, Any] | None = None,
    max_segment_duration: float | None = None,
    keep_video: bool = False,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Analyze `video` as a new run and return its results.

    The run gets a folder of its own in the pipeline's output folder, named after
    the video's file and the time, so analyzing the same film again keeps the
    earlier run. `api` names the provider(s) the pipeline identifies with; `film`
    is what is known about the film, by default the file's MP4 tags.
    `keep_video` moves the video into the run folder, with a filmstrip, for the
    app's video preview. A cancelled or failed run leaves no folder behind.
    """
    run_name = f"{video.stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    run_dir = pipeline.output_dir / run_name
    # Not exist_ok: a failure removes this folder, so it must not be another
    # run's (the same file, started within the same second).
    run_dir.mkdir(parents=True)
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
            run_name=run_name,
        )
        results = output.to_dict()
        # Saved so a resume continues with the same providers.
        results["providers"] = [p.strip() for p in api.split(",") if p.strip()]
        # Everything the run was analyzed with, so it can be repeated exactly.
        results["settings"] = {
            **pipeline.settings,
            "max_segment_duration": max_segment_duration,
        }
        _rerank_and_look_up_composers(results.get("film"), results["cues"], progress)
        if keep_video:
            _keep_video(video, run_dir, results)
        save_results(run_dir / "results.json", results)
        return results
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise


def resume(
    run_dir: Path,
    api: str,
    on_progress: ProgressCb | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Identify the pending cues of a run (skipped at a provider's limit) from
    their saved audio, and save and return the run's results.

    Only the cues identified now are re-ranked and looked up: the others keep
    what they have, e.g. a composer edited by hand. A cancelled resume saves
    nothing, so its cues stay pending.
    """
    import numpy as np
    from scipy.io import wavfile

    from soundtrackID.detection import RateLimitError, build_detection_client
    from soundtrackID.models import MusicSegment

    progress = _checked(on_progress, cancel)
    results_path = run_dir / "results.json"
    results = load_results(results_path)
    cues = results.get("cues") or []
    todo = [c for c in cues if c.get("pending") and c.get("audio_file")]
    total = len(todo)
    if total == 0:
        results["rate_limited"] = False
        save_results(results_path, results)
        return results

    client = build_detection_client(api)
    _report(progress, "resuming", f"0/{total}")
    still_limited = False
    done_n = 0
    identified = []  # the cues this resume identifies
    answered_audio = []  # of cues no longer pending
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
            seg = MusicSegment(
                audio=audio,
                start_time=0.0,
                end_time=len(audio) / sr,
                sample_rate=sr,
            )
            det = client.detect(seg)
        except RateLimitError:
            still_limited = True
            break
        except Exception:
            continue
        if det.title:
            _apply_detection_to_cue(cue, det)
            identified.append(cue)
        cue["pending"] = False  # answered, a match or not
        cue["audio_file"] = None  # only pending cues keep their audio
        answered_audio.append(wav_path)
        done_n += 1
        _report(progress, "resuming", f"{done_n}/{total}")

    _rerank_and_look_up_composers(results.get("film"), identified, progress)
    results["rate_limited"] = still_limited
    save_results(results_path, results)
    # Only once saved: until then the saved cues are still pending, and a resume
    # needs their audio.
    for wav_path in answered_audio:
        wav_path.unlink(missing_ok=True)
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
    print(f"[{step}] {detail}")
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
        print("temporal rerank failed:\n" + traceback.format_exc())

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
        print("composer lookup failed:\n" + traceback.format_exc())


def _keep_video(video: Path, run_dir: Path, results: dict[str, Any]) -> None:
    """Move the video into the run folder, with a thumbnail filmstrip, so the app
    shows it synced to the timeline (also when the run is reopened). Best-effort:
    the results are saved without it."""
    try:
        dest = run_dir / f"source{video.suffix or '.mp4'}"
        shutil.move(str(video), str(dest))
        from soundtrackID.thumbnails import extract_filmstrip

        filmstrip = extract_filmstrip(dest, run_dir / "filmstrip.jpg")
        results["video"] = {"file": dest.name, "filmstrip": filmstrip}
    except Exception:
        print("video/filmstrip persist failed:\n" + traceback.format_exc())


def _apply_detection_to_cue(cue: dict, det) -> None:
    """Copy a DetectionResult onto an existing cue (same keys as models._build_cues)."""
    md = det.metadata or {}
    cue["provider"] = det.provider
    cue["title"] = det.title
    cue["artist"] = det.artist
    cue["album"] = md.get("album")
    cue["confidence"] = det.confidence
    for key in CUE_METADATA_KEYS:
        cue[key] = md.get(key)
