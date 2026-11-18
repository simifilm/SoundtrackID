"""The API keys entered in the app (Settings → API keys) and the detection
providers ticked there: GET and POST /settings."""

import json
import os

from fastapi import APIRouter, Body

from soundtrackid.detection.ensemble import PROVIDER_PRIORITY
from soundtrackid.results import save_json
from soundtrackid.web import data

router = APIRouter()

# Maps a UI field name → the environment variable the clients read. Values entered
# in the settings page are stored in settings.json and pushed into os.environ, so
# subsequent analysis runs (which build clients fresh) pick them up. This layers
# on top of .env: a saved value overrides the .env default.
_KEY_FIELDS: dict[str, str] = {
    "acrcloud_host": "ACRCLOUD_HOST",
    "acrcloud_access_key": "ACRCLOUD_ACCESS_KEY",
    "acrcloud_access_secret": "ACRCLOUD_ACCESS_SECRET",
    "audd_api_token": "AUDD_API_TOKEN",
    "tmdb_bearer": "FIWI_TMDB_API_TOKEN",
    "tmdb_key": "FIWI_TMDB_KEY",
}
# Fields shown in cleartext (not secrets); everything else is masked in responses.
_PLAIN_FIELDS = {"acrcloud_host"}


def load_saved_keys() -> None:
    """On startup, apply any saved API keys over the environment."""
    try:
        saved = json.loads(data.settings_path().read_text())
    except (OSError, ValueError):
        return
    for field, env_var in _KEY_FIELDS.items():
        val = saved.get(field)
        if val:
            os.environ[env_var] = str(val)


def _mask(value: str) -> str:
    if not value:
        return ""
    return ("•" * 4 + value[-4:]) if len(value) > 4 else "•" * len(value)


def _settings_state() -> dict:
    """Report which keys are configured, without exposing secret values."""
    try:
        saved = json.loads(data.settings_path().read_text())
    except (OSError, ValueError):
        saved = {}
    state = {}
    for field, env_var in _KEY_FIELDS.items():
        value = saved.get(field) or os.environ.get(env_var) or ""
        state[field] = {
            "configured": bool(value),
            "preview": value if field in _PLAIN_FIELDS else _mask(value),
        }
    # The providers ticked last time; empty when never changed (the page's default).
    state["providers"] = saved.get("providers", [])
    return state


@router.get("/settings")
async def get_settings() -> dict:
    """Which API keys are configured (masked, never the raw secret), and the
    providers ticked last time."""
    return _settings_state()


@router.post("/settings")
async def save_settings(payload: dict = Body(...)) -> dict:
    """Persist API keys entered in the settings page and apply them to the
    environment so subsequent analysis runs use them. An empty string clears a
    field. Only known fields are accepted.

    `providers` (a list of names) keeps the ticked providers for the next start;
    only known names are kept, and an empty list clears the choice."""
    path = data.settings_path()
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    if "providers" in payload:
        providers = [p for p in payload["providers"] if p in PROVIDER_PRIORITY]
        if providers:
            saved["providers"] = providers
        else:
            saved.pop("providers", None)
    for field, value in payload.items():
        if field not in _KEY_FIELDS:
            continue
        env_var = _KEY_FIELDS[field]
        if value:
            saved[field] = str(value)
            os.environ[env_var] = str(value)
        else:  # empty string clears the key
            saved.pop(field, None)
            os.environ.pop(env_var, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, saved)
    return _settings_state()
