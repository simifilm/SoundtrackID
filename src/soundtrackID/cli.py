"""Command line: analyze a video, or resume a run a provider stopped.

    python -m soundtrackID.cli analyze path/to/film.mp4
    python -m soundtrackID.cli resume output/<run>

Runs the same analysis as the app (soundtrackID.analysis): pipeline, date
re-ranking, composer lookup, one results.json per run.
"""

import argparse
import logging
from pathlib import Path
from typing import Any

from soundtrackID.analysis import analyze, resume
from soundtrackID.envfiles import load_env_files
from soundtrackID.models import load_results, results_to_csv
from soundtrackID.pipeline import CLASSIFICATION_HOP_S, build_pipeline


def main(argv: list[str] | None = None) -> None:
    load_env_files()  # .env.dev, then .env
    # The analysis's progress and what went wrong, on the console; other
    # libraries only their warnings (httpx logs request URLs, maybe with a key).
    logging.basicConfig(format="%(message)s")
    logging.getLogger("soundtrackID").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(
        prog="python -m soundtrackID.cli",
        description="Find and identify the music in a film.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    a = commands.add_parser("analyze", help="Analyze a video as a new run")
    a.add_argument("video_path", type=Path, help="Path to the video file")
    a.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./output"),
        help="Folder of the runs (default: ./output)",
    )
    a.add_argument(
        "--api",
        default="acrcloud",
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
        "list for ensemble majority voting (default: acrcloud)",
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
        default=10.0,
        help="Length of a classified window in seconds (default: 10.0)",
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
        default=None,
        help="Split segments into chunks of at most N seconds before detection",
    )
    a.add_argument(
        "--threshold",
        type=float,
        default=0.2,
        help="Music classification threshold (default: 0.2)",
    )
    a.add_argument(
        "--bridge-gap",
        type=float,
        default=2.0,
        help="Join clips of the same track across up to N seconds without music "
        "(default: 2.0)",
    )
    a.add_argument("--title", help="The film's title (instead of its file's tag)")
    a.add_argument("--year", type=int, help="The film's year (instead of its tag)")
    a.add_argument("--imdb-id", help="The film's IMDb ID, e.g. tt0043014")
    a.add_argument(
        "--lookup-film",
        action="store_true",
        help="Fill in what is missing about the film online (TMDb, Wikidata), "
        "like the app's Look up online",
    )
    a.add_argument(
        "--csv",
        action="store_true",
        help="Also write results.csv: the same results as a spreadsheet",
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
    if args.command == "analyze":
        pipeline = build_pipeline(
            _classifier(args.classifier, args.threshold),
            args.api,
            hop=args.hop,
            window=args.chunk_duration,
            bridge_gap=args.bridge_gap,
            isolation=args.isolation,
        )
        results = analyze(
            pipeline,
            args.video_path,
            args.api,
            args.output_dir,
            film=_film(args),
            max_segment_duration=args.max_segment,
        )
        run_dir = args.output_dir / results["video_name"]
    else:
        run_dir = args.run_dir
        api = args.api or ",".join(
            load_results(run_dir / "results.json").get("providers") or ["acrcloud"]
        )
        results = resume(run_dir, api)

    if args.csv:
        # newline="": the CSV brings its own line endings.
        (run_dir / "results.csv").write_text(
            results_to_csv(results), encoding="utf-8", newline=""
        )
    _summarize(results, run_dir)


def _classifier(name: str, threshold: float):
    if name == "hf":
        from soundtrackID.classifiers import HuggingFaceClassifier

        return HuggingFaceClassifier(music_labels=["Music"], threshold=threshold)
    from soundtrackID.classifiers import OnnxClassifier

    return OnnxClassifier(
        model_dir=str(Path(__file__).parents[2] / "assets" / "ast_model"),
        music_labels=["Music"],
        threshold=threshold,
    )


def _film(args: argparse.Namespace) -> dict[str, Any] | None:
    """What is known about the film: its file's MP4 tags, then the options; with
    --lookup-film, what is missing is looked up online."""
    from soundtrackID.metadata.mp4_tags import read_mp4_tags

    film = read_mp4_tags(args.video_path) or {}
    given = {"title": args.title, "year": args.year, "imdb_id": args.imdb_id}
    film.update({k: v for k, v in given.items() if v is not None})
    if args.lookup_film:
        from soundtrackID.metadata.film import lookup_film

        hint = {k: film.get(k) for k in ("imdb_id", "tmdb_id", "title", "year")}
        # As in the app: what is known stays, the lookup fills the gaps.
        found = {**lookup_film(**hint), **film}
        film = {k: v for k, v in found.items() if v not in (None, "")}
    return film or None


def _summarize(results: dict[str, Any], run_dir: Path) -> None:
    cues = results.get("cues") or []
    identified = sum(1 for c in cues if c.get("title"))
    pending = sum(1 for c in cues if c.get("pending"))
    print(f"\n{identified} of {len(cues)} cues identified. Results in: {run_dir}")
    if pending:
        print(
            f"{pending} cue(s) still pending: a provider hit its limit, couldn't "
            "be reached or failed. "
            f'Continue later with: python -m soundtrackID.cli resume "{run_dir}"'
        )


if __name__ == "__main__":
    main()
