"""Main module — launches the FIWI Filmmusik web server."""


def main() -> None:
    """Entry point: start the FastAPI/uvicorn server."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -e '.[web]'")
        raise SystemExit(1)

    uvicorn.run("fiwi_filmmusik.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
