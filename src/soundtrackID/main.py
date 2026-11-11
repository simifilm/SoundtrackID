"""Main module: launches the SoundtrackID web server."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


def main() -> None:
    """Entry point: start the FastAPI/uvicorn server."""
    import argparse

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -e '.[web]'")
        raise SystemExit(1)

    parser = argparse.ArgumentParser(description="SoundtrackID web server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    from soundtrackID.app import LOG_PATH

    _configure_logging(LOG_PATH)
    uvicorn.run("soundtrackID.app:app", host=args.host, port=args.port, reload=False)


def _configure_logging(path: Path) -> None:
    """Log to the console and to `path`: the desktop app has no console, so the
    file is all that is left of what went wrong.

    The package logs its progress too; other libraries only their warnings, as
    httpx logs every request's URL, and a TMDb key can be one of its parameters.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    for handler in (
        logging.StreamHandler(),
        RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    logging.getLogger("soundtrackID").setLevel(logging.INFO)


if __name__ == "__main__":
    main()
