"""Tests for main module."""

import uvicorn

from soundtrackID.main import main


def test_main(monkeypatch) -> None:
    """main() starts uvicorn with the default host and port."""
    calls = []

    def fake_run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr("sys.argv", ["soundtrackID"])
    monkeypatch.setattr(uvicorn, "run", fake_run)

    main()

    assert calls == [
        ("soundtrackID.app:app", {"host": "127.0.0.1", "port": 8000, "reload": False})
    ]
