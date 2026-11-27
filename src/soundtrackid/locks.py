"""A folder's lock, which a process holds while it analyzes into the folder: a
run's, a batch's. So the app and the command line see a run or a batch another
process is analyzing as under way, and one a process left (it was killed, the
app closed) as interrupted: the operating system releases the lock as the
process ends, however it ends. Only whether the lock is held counts; its file
may stay behind.
"""

import sys
import time
from pathlib import Path

LOCK_FILE = ".lock"

if sys.platform == "win32":
    import msvcrt

    def _try_lock(file) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(file) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    # flock, not lockf: a lock of the file opened again in the same process
    # conflicts with it too, as the app's own runs need.
    def _try_lock(file) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(file) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


class Busy(Exception):
    """Another process, or another analysis in this one, holds the lock."""


class FolderLock:
    def __init__(self, folder: Path) -> None:
        self.path = folder / LOCK_FILE
        self._file = None

    def acquire(self) -> "FolderLock":
        """Hold the lock; Busy when it is held already. Tried a few times, as
        a look at whether it is held holds it for a moment (is_locked)."""
        file = open(self.path, "a+b")
        for attempt in range(10):
            try:
                _try_lock(file)
                self._file = file
                return self
            except OSError:
                time.sleep(0.05)
        file.close()
        raise Busy(f"{self.path.parent} is under way in another process")

    def release(self) -> None:
        """Release the lock, if held, and remove its file. Again does nothing."""
        if self._file is None:
            return
        try:
            _unlock(self._file)
        finally:
            self._file.close()
            self._file = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass  # Windows: another process is looking at it

    def __enter__(self) -> "FolderLock":
        return self if self._file is not None else self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(folder: Path) -> bool:
    """Whether a process, this one too, holds the folder's lock."""
    try:
        file = open(folder / LOCK_FILE, "rb")
    except OSError:
        return False  # none: never held, or released
    with file:
        try:
            _try_lock(file)
        except OSError:
            return True
        _unlock(file)
        return False
