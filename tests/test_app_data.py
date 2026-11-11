"""Where the installed app keeps its data: its runs, saved API keys and log."""

import sys
from pathlib import Path

from soundtrackID import app as app_module


def test_windows_keeps_the_data_in_local_app_data(tmp_path, monkeypatch):
    """Local, not roaming: the runs keep their films."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    assert app_module._app_data_dir() == tmp_path / "Local" / "SoundtrackID"


def test_macos_keeps_the_data_in_application_support(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert app_module._app_data_dir() == (
        tmp_path / "Library" / "Application Support" / "SoundtrackID"
    )


def _old_windows_data(home):
    """What the Windows app kept under the macOS path in the home folder."""
    old = home / "Library" / "Application Support" / "SoundtrackID"
    (old / "cache" / "film_run").mkdir(parents=True)
    (old / "cache" / "film_run" / "results.json").write_text("{}")
    (old / "settings.json").write_text('{"audd_api_token": "OLD"}')
    return old


def test_the_windows_data_under_the_macos_path_is_moved_over(tmp_path, monkeypatch):
    """Earlier runs and saved keys stay; the folders left empty go."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _old_windows_data(tmp_path)
    new = tmp_path / "Local" / "SoundtrackID"
    new.mkdir(parents=True)
    (new / "soundtrackid.log").write_text("started")  # the log is opened first

    app_module._move_misplaced_windows_data(new)

    assert (new / "cache" / "film_run" / "results.json").exists()
    assert (new / "settings.json").read_text() == '{"audd_api_token": "OLD"}'
    assert (new / "soundtrackid.log").read_text() == "started"
    assert not (tmp_path / "Library").exists()


def test_data_already_in_the_new_folder_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    old = _old_windows_data(tmp_path)
    new = tmp_path / "Local" / "SoundtrackID"
    new.mkdir(parents=True)
    (new / "settings.json").write_text('{"audd_api_token": "NEW"}')

    app_module._move_misplaced_windows_data(new)

    assert (new / "settings.json").read_text() == '{"audd_api_token": "NEW"}'
    assert (old / "settings.json").exists()  # left where it was
    assert (new / "cache" / "film_run" / "results.json").exists()


def test_nothing_to_move_without_old_data(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    new = tmp_path / "Local" / "SoundtrackID"

    app_module._move_misplaced_windows_data(new)

    assert not new.exists()
