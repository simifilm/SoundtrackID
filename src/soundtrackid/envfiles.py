"""The environment files the app and the CLI read their keys from.

.env.dev holds a developer's own keys (ACRCloud, AudD, signing): never bundled.
.env holds what the app ships, the TMDb keys every install shares; the build
(fiwi-server.spec) bundles it as it is and refuses one holding anything else.
"""

from soundtrackid import paths


def load_env_files() -> None:
    """From source, read .env.dev, then .env, from the project folder: .env.dev
    wins where both set a key, and the environment wins over both. Installed,
    read the .env next to the server, then the one the app ships."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    if paths.FROZEN:
        for env in (paths.SERVER_DIR / ".env", paths.BUNDLE_DIR / ".env"):
            if env.exists():
                load_dotenv(env)
        return
    load_dotenv(find_dotenv(".env.dev"))
    load_dotenv(find_dotenv(".env"))
