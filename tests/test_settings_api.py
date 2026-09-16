"""Tests for the API-key settings endpoints (masked GET, persisted POST)."""

import json

from fastapi.testclient import TestClient

from soundtrackID import app as app_module

client = TestClient(app_module.app)


def _isolate(tmp_path, monkeypatch):
    """Point the settings file at a tmp path and clear the managed env vars."""
    monkeypatch.setattr(app_module, "_SETTINGS_PATH", tmp_path / "settings.json")
    for env_var in app_module._KEY_FIELDS.values():
        monkeypatch.delenv(env_var, raising=False)


def test_get_reports_unset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = client.get("/settings").json()
    assert state["audd_api_token"] == {"configured": False, "preview": ""}


def test_post_persists_and_masks(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    r = client.post("/settings", json={"audd_api_token": "ABCDEF123456"})
    assert r.status_code == 200
    body = r.json()
    # Secret is masked in the response...
    assert body["audd_api_token"]["configured"] is True
    assert body["audd_api_token"]["preview"] == "••••3456"
    assert "ABCDEF123456" not in json.dumps(body)
    # ...applied to the environment for subsequent runs...
    assert app_module.os.environ["AUDD_API_TOKEN"] == "ABCDEF123456"
    # ...and written to disk in full.
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved == {"audd_api_token": "ABCDEF123456"}


def test_host_shown_in_clear(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client.post("/settings", json={"acrcloud_host": "identify-eu.acrcloud.com"})
    state = client.get("/settings").json()
    assert state["acrcloud_host"]["preview"] == "identify-eu.acrcloud.com"


def test_empty_value_clears_key(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client.post("/settings", json={"audd_api_token": "TOKEN12345"})
    client.post("/settings", json={"audd_api_token": ""})
    assert app_module.os.environ.get("AUDD_API_TOKEN") is None
    assert json.loads((tmp_path / "settings.json").read_text()) == {}


def test_unknown_fields_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client.post("/settings", json={"evil": "x", "audd_api_token": "KEEP123456"})
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "evil" not in saved and saved["audd_api_token"] == "KEEP123456"


def test_load_settings_into_env(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps({"tmdb_key": "hex123"}))
    app_module._load_settings_into_env()
    assert app_module.os.environ["FIWI_TMDB_KEY"] == "hex123"
