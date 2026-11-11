"""The environment files a run from source reads.

.env.dev holds a developer's own keys (ACRCloud, AudD, signing): never bundled.
.env holds what the app ships, the TMDb keys every install shares; the build
(fiwi-server.spec) bundles it as it is and refuses one holding anything else.
"""


def load_env_files() -> None:
    """Read .env.dev, then .env, from the project folder: .env.dev wins where
    both set a key, and the environment wins over both."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    load_dotenv(find_dotenv(".env.dev"))
    load_dotenv(find_dotenv(".env"))
