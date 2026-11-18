"""Tests for the web server's launcher (soundtrackid.server)."""

import logging

import uvicorn

from soundtrackid import server
from soundtrackid.server import _configure_logging, main
from soundtrackid.web.data import log_path


def test_main(monkeypatch) -> None:
    """main() logs to the app's log file and starts uvicorn with the default
    host and port."""
    calls = []
    logs = []

    def fake_run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr("sys.argv", ["soundtrackid"])
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(server, "_configure_logging", logs.append)

    main()

    assert logs == [log_path()]
    assert calls == [
        (
            "soundtrackid.web.app:app",
            {"host": "127.0.0.1", "port": 8000, "reload": False},
        )
    ]


def test_server_log_keeps_progress_and_warnings(tmp_path) -> None:
    """The desktop app has no console, so the file is all that is left. Other
    libraries' info stays out: httpx logs request URLs, which can carry a key."""
    root = logging.getLogger()
    package = logging.getLogger("soundtrackid")
    handlers, level = list(root.handlers), package.level
    try:
        _configure_logging(tmp_path / "logs" / "server.log")
        logging.getLogger("soundtrackid.pipeline").info("classifying")
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
