"""Fixtures the tests share."""

import pytest

from soundtrackid.web import data


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """The server's data folder, a temporary one: the runs, the saved API keys
    and the staged uploads go there."""
    monkeypatch.setattr(data, "root", tmp_path)
    return tmp_path


@pytest.fixture
def runs_dir(data_dir):
    """The server's runs folder, in a temporary data folder."""
    runs = data.runs_dir()
    runs.mkdir()
    return runs
