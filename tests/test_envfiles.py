"""The environment files a run from source reads: .env.dev, then .env."""

import os

from soundtrackID.envfiles import load_env_files

KEYS = ("FIWI_TMDB_KEY", "FIWI_TMDB_API_TOKEN", "AUDD_API_TOKEN")


def test_a_developers_keys_win_over_the_shipped_ones(tmp_path, monkeypatch):
    """.env.dev holds a developer's own keys, .env what the app ships; a key
    set in the environment wins over both."""
    (tmp_path / ".env.dev").write_text("FIWI_TMDB_KEY=mine\nAUDD_API_TOKEN=dev\n")
    (tmp_path / ".env").write_text("FIWI_TMDB_KEY=shared\nFIWI_TMDB_API_TOKEN=tok\n")
    monkeypatch.setattr("dotenv.find_dotenv", lambda name: str(tmp_path / name))
    for key in KEYS:
        monkeypatch.setenv(key, "")  # so the test leaves the environment as it was
        monkeypatch.delenv(key)
    monkeypatch.setenv("FIWI_TMDB_API_TOKEN", "from-the-shell")

    load_env_files()

    assert os.environ["FIWI_TMDB_KEY"] == "mine"
    assert os.environ["AUDD_API_TOKEN"] == "dev"
    assert os.environ["FIWI_TMDB_API_TOKEN"] == "from-the-shell"
