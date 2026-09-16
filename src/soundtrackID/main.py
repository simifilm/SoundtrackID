"""Main module: launches the SoundtrackID web server."""

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

    uvicorn.run("soundtrackID.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
