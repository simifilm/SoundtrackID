"""Identify the music in WAV files, to try a provider out.

Run: python -m soundtrackID.detection clip.wav [--api acrcloud,shazam]
"""

import argparse

from soundtrackID.defaults import DEFAULT_API
from soundtrackID.detection import build_detection_client
from soundtrackID.envfiles import load_env_files
from soundtrackID.models import MusicSegment


def main() -> None:
    load_env_files()  # .env.dev, then .env, like the app and the CLI

    parser = argparse.ArgumentParser(
        description="Identify music in one or more WAV files via Shazam or ACRCloud."
    )
    parser.add_argument("wav_files", nargs="+", help="WAV file(s) to identify")
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
        help="Detection provider(s): acrcloud, shazam, audd — or a comma-separated "
        f"list for ensemble majority voting (default: {DEFAULT_API})",
    )
    args = parser.parse_args()

    client = build_detection_client(args.api)
    print(f"Using provider: {args.api}")

    for wav_path in args.wav_files:
        print(f"\nProcessing: {wav_path}")

        result = client.detect(MusicSegment.read_wav(wav_path))

        if result.title:
            print(f"  Title:      {result.title}")
            print(f"  Artist:     {result.artist}")
            if result.metadata.get("album"):
                print(f"  Album:      {result.metadata['album']}")
            if result.metadata.get("genre"):
                print(f"  Genre:      {result.metadata['genre']}")
            if result.metadata.get("musicbrainz_url"):
                print(f"  MusicBrainz: {result.metadata['musicbrainz_url']}")
        else:
            print("  No match found")


if __name__ == "__main__":
    main()
