"""Tests for main module."""

import logging

import uvicorn

from soundtrackID import main as main_module
from soundtrackID.app import LOG_PATH
from soundtrackID.main import _configure_logging, main


def test_main(monkeypatch) -> None:
    """main() logs to the app's log file and starts uvicorn with the default
    host and port."""
    calls = []
    logs = []

    def fake_run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr("sys.argv", ["soundtrackID"])
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(main_module, "_configure_logging", logs.append)

    main()

    assert logs == [LOG_PATH]
    assert calls == [
        ("soundtrackID.app:app", {"host": "127.0.0.1", "port": 8000, "reload": False})
    ]


def test_server_log_keeps_progress_and_warnings(tmp_path) -> None:
    """The desktop app has no console, so the file is all that is left. Other
    libraries' info stays out: httpx logs request URLs, which can carry a key."""
    root = logging.getLogger()
    package = logging.getLogger("soundtrackID")
    handlers, level = list(root.handlers), package.level
    try:
        _configure_logging(tmp_path / "logs" / "server.log")
        logging.getLogger("soundtrackID.pipeline").info("classifying")
        logging.getLogger("httpx").info("GET /3/movie?api_key=secret")
        logging.getLogger("acrcloud").warning("odd answer")
    finally:
        for handler in [h for h in root.handlers if h not in handlers]:
            root.removeHandler(handler)
            handler.close()
        package.setLevel(level)

    text = (tmp_path / "logs" / "server.log").read_text(encoding="utf-8")
    assert "classifying" in text and "odd answer" in text
    assert "secret" not in text
