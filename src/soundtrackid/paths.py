"""Where SoundtrackID finds its own files, run from source or installed.

The one place that tells the two apart. Installed, the app is a PyInstaller
bundle (fiwi-server.spec): its files are unpacked into a folder of its own and
its data lives in the user's application data folder. From source the files are
in the project folder.
"""

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# The installed app: PyInstaller sets sys.frozen.
FROZEN = bool(getattr(sys, "frozen", False))

_PACKAGE_DIR = Path(__file__).resolve().parent

# From source: the project folder, as the package is src/soundtrackid.
SOURCE_ROOT = _PACKAGE_DIR.parents[1]

# Installed: the server's own folder, and the one PyInstaller unpacked the app's
# files into (sys._MEIPASS: the page, the model and the .env the app ships).
SERVER_DIR = Path(sys.executable).parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", SERVER_DIR))

# The page: index.html, its styles and its scripts (web/static/ from source).
STATIC_DIR = BUNDLE_DIR / "static" if FROZEN else _PACKAGE_DIR / "web" / "static"

# The ONNX export of the AST model; from source in assets/ (Git LFS).
MODEL_DIR = (BUNDLE_DIR if FROZEN else SOURCE_ROOT / "assets") / "ast_model"

# The runs from source, the CLI's and the app's alike, so the app lists both:
# in the project folder, whichever folder either is started from. The installed
# app keeps them in its data folder (web/data.py).
SOURCE_RUNS_DIR = SOURCE_ROOT / "output" / "cache"


def app_data_dir() -> Path:
    """Where the installed app keeps its runs, saved API keys and log: the
    user's application data folder (the local one on Windows: runs keep films)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "SoundtrackID"
    return Path.home() / "Library" / "Application Support" / "SoundtrackID"


def move_misplaced_windows_data(new: Path) -> None:
    """Move what the Windows app kept under the macOS path in the home folder,
    before it had a folder of its own, into `new`, so earlier runs and saved
    keys stay. What `new` has already is kept; the old folders go once empty."""
    old = Path.home() / "Library" / "Application Support" / "SoundtrackID"
    if not old.is_dir():
        return
    new.mkdir(parents=True, exist_ok=True)
    for item in old.iterdir():
        if not (new / item.name).exists():
            try:
                shutil.move(str(item), str(new / item.name))
            except OSError:
                log.warning("Could not move %s to %s", item, new, exc_info=True)
    for folder in (old, old.parent, old.parent.parent):
        try:
            folder.rmdir()  # only while empty
        except OSError:
            break
