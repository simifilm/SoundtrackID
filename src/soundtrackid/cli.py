"""Command line: analyze a video, or several as a batch, or resume a run a
provider stopped.

    soundtrackid analyze path/to/film.mp4
    soundtrackid analyze film1.mp4 film2.mkv path/to/folder
    soundtrackid resume output/cache/<run>

Runs the same analysis as the app (soundtrackid.analysis): pipeline, date
re-ranking, composer lookup, one results.json per run.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from soundtrackid import paths
from soundtrackid.analysis import analyze, analyze_batch, resume
from soundtrackid.classifiers import OnnxClassifier, load_classifier
from soundtrackid.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackid.envfiles import load_env_files
from soundtrackid.exports import results_to_csv
from soundtrackid.pipeline import build_pipeline
from soundtrackid.runs import Run

# The files of a folder analyzed: those the app accepts (upload.js).
MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
    ".mp3",
    ".wav",
}


def main(argv: list[str] | None = None) -> None:
    load_env_files()  # .env.dev, then .env
    # The analysis's progress and what went wrong, on the console; other
    # libraries only their warnings (httpx logs request URLs, maybe with a key).
    logging.basicConfig(format="%(message)s")
    logging.getLogger("soundtrackid").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(
        prog="soundtrackid",
        description="Find and identify the music in a film.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    a = commands.add_parser(
        "analyze", help="Analyze a video as a new run, or several as a batch"
    )
    a.add_argument(
        "video_paths",
        type=Path,
        nargs="+",
        metavar="video_path",
        help="A video file, or a folder of them; several are analyzed one after "
        "another as a batch, each a run in the batch's folder",
    )
    a.add_argument(
        "--output-dir",
        type=Path,
        default=paths.SOURCE_RUNS_DIR,
        help=f"Folder of the runs (default: {paths.SOURCE_RUNS_DIR}, the app's from "
        "source, so its history lists them)",
    )
    a.add_argument(
        "--api",
        default=DEFAULT_API,
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
        f"list for ensemble majority voting (default: {DEFAULT_API})",
    )
    a.add_argument(
        "--classifier",
        choices=("onnx", "hf"),
        default="onnx",
        help="Music classifier backend (default: onnx)",
    )
    a.add_argument(
        "--isolation",
        action="store_true",
        help="Remove the vocals before identification (Demucs; needs the "
        "'demucs' extras)",
    )
    # The default since --isolation; still accepted for older commands.
    a.add_argument(
        "--no-isolation", action="store_false", dest="isolation", help=argparse.SUPPRESS
    )
    a.add_argument(
        "--chunk-duration",
        type=float,
        default=None,
        help="Length of a classified window in seconds (default: the model's "
        f"own, {OnnxClassifier.window_duration:.2f})",
    )
    a.add_argument(
        "--hop",
        type=float,
        default=CLASSIFICATION_HOP_S,
        help=(
            "Step between overlapping windows in seconds "
            f"(default: {CLASSIFICATION_HOP_S})"
        ),
    )
    a.add_argument(
        "--max-segment",
        type=float,
        default=MAX_SEGMENT_S,
        help="Split segments into chunks of at most N seconds before detection; "
        f"0 sends each whole (default: {MAX_SEGMENT_S:g})",
    )
    a.add_argument(
        "--threshold",
        type=float,
        default=MUSIC_THRESHOLD,
        help=f"Music classification threshold (default: {MUSIC_THRESHOLD})",
    )
    a.add_argument(
        "--bridge-gap",
        type=float,
        default=BRIDGE_GAP_S,
        help="Join clips of the same track across up to N seconds without music "
        f"(default: {BRIDGE_GAP_S:g})",
    )
    a.add_argument(
        "--title", help="The film's title (instead of its file's tag; one film only)"
    )
    a.add_argument(
        "--year", type=int, help="The film's year (instead of its tag; one film only)"
    )
    a.add_argument(
        "--imdb-id", help="The film's IMDb ID, e.g. tt0043014 (one film only)"
    )
    a.add_argument(
        "--lookup-film",
        action="store_true",
        help="Fill in what is missing about each film online (TMDb, Wikidata)",
    )
    a.add_argument(
        "--csv",
        action="store_true",
        help="Also write each run's results.csv: the same results as a spreadsheet",
    )

    r = commands.add_parser(
        "resume", help="Identify the cues a provider's limit left pending"
    )
    r.add_argument("run_dir", type=Path, help="The run's folder")
    r.add_argument(
        "--api",
        default=None,
        help="Detection provider(s) (default: the ones the run was analyzed with)",
    )
    r.add_argument(
        "--csv",
        action="store_true",
        help="Also write results.csv: the same results as a spreadsheet",
    )

    args = parser.parse_args(argv)
    if args.command == "resume":
        results = resume(args.run_dir, args.api)
        print()
        _finish(results, args.run_dir, args.csv)
        return

    videos = _videos(args.video_paths, a)
    if len(videos) > 1 and any(
        option is not None for option in (args.title, args.year, args.imdb_id)
    ):
        a.error("--title, --year and --imdb-id describe one film, not several")
    pipeline = build_pipeline(
        load_classifier(hf=args.classifier == "hf"),
        args.api,
        threshold=args.threshold,
        hop=args.hop,
        window=args.chunk_duration,
        bridge_gap=args.bridge_gap,
        isolation=args.isolation,
    )
    if len(videos) == 1:
        results = analyze(
            pipeline,
            videos[0],
            args.api,
            args.output_dir,
            film=_film(videos[0], args),
            max_segment_duration=args.max_segment or None,
        )
        print()
        _finish(results, args.output_dir / results["video_name"], args.csv)
        return

    record = analyze_batch(
        pipeline,
        [(video, _film(video, args)) for video in videos],
        args.api,
        args.output_dir,
        max_segment_duration=args.max_segment or None,
    )
    batch_dir = args.output_dir / record["name"]
    print()
    for film in record["films"]:
        if film["status"] == "done":
            run = Run(batch_dir / film["run"])
            _finish(run.load(), run.path, args.csv, f"{film['source']}: ")
        else:
            print(f"{film['source']}: {film['status']}: {film['error']}")
    done = sum(film["status"] == "done" for film in record["films"])
    print(f"\n{done} of {len(videos)} films analyzed. The batch: {batch_dir}")
    if done < len(videos):
        sys.exit(1)


def _videos(paths: list[Path], parser: argparse.ArgumentParser) -> list[Path]:
    """The videos to analyze: each path given, a folder's media files instead
    of the folder, by name."""
    videos = []
    for path in paths:
        if not path.is_dir():
            videos.append(path)
            continue
        found = sorted(p for p in path.iterdir() if p.suffix.lower() in MEDIA_SUFFIXES)
        if not found:
            parser.error(f"no video or audio files in {path}")
        videos += found
    return videos


def _finish(results: dict[str, Any], run_dir: Path, csv: bool, label: str = "") -> None:
    """Write the run's results.csv if asked to, and say how it went, after
    `label`: in a batch, the film's file."""
    if csv:
        # newline="": the CSV brings its own line endings.
        (run_dir / "results.csv").write_text(
            results_to_csv(results), encoding="utf-8", newline=""
        )
    _summarize(results, run_dir, label)


def _film(video: Path, args: argparse.Namespace) -> dict[str, Any] | None:
    """What is known about the film: its file's MP4 tags, then the options; with
    --lookup-film, what is missing is looked up online."""
    from soundtrackid.metadata.mp4_tags import read_mp4_tags

    film = read_mp4_tags(video) or {}
    given = {"title": args.title, "year": args.year, "imdb_id": args.imdb_id}
    film.update({k: v for k, v in given.items() if v is not None})
    if args.lookup_film:
        from soundtrackid.metadata.film import lookup_film

        hint = {k: film.get(k) for k in ("imdb_id", "tmdb_id", "title", "year")}
        # Unlike the app, no one chooses among the films found: what is known
        # stays, the lookup fills the gaps.
        found = {**lookup_film(**hint), **film}
        film = {k: v for k, v in found.items() if v not in (None, "")}
    return film or None


def _summarize(results: dict[str, Any], run_dir: Path, label: str = "") -> None:
    cues = results.get("cues") or []
    identified = sum(1 for c in cues if c.get("title"))
    pending = sum(1 for c in cues if c.get("pending"))
    print(f"{label}{identified} of {len(cues)} cues identified. Results in: {run_dir}")
    if pending:
        print(
            f"{pending} cue(s) still pending: a provider hit its limit, couldn't "
            "be reached or failed. "
            f'Continue later with: soundtrackid resume "{run_dir}"'
        )


if __name__ == "__main__":
    main()
