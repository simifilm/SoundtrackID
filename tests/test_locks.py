"""A folder's lock (locks.py), which a process holds while it analyzes into
the folder, and the operating system releases as the process ends."""

import subprocess
import sys
import time

import pytest

from soundtrackid.locks import LOCK_FILE, Busy, FolderLock, is_locked

# Holds the lock of the folder given, says so, then waits to be killed.
HOLDER = """
import sys, time
from pathlib import Path
from soundtrackid.locks import FolderLock
lock = FolderLock(Path(sys.argv[1])).acquire()
print("held", flush=True)
time.sleep(60)
"""


def test_a_lock_another_process_held_is_released_as_it_ends(tmp_path):
    """However it ends: killed, its lock is free again."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)], stdout=subprocess.PIPE
    )
    try:
        assert holder.stdout.readline().strip() == b"held"

        assert is_locked(tmp_path)
        with pytest.raises(Busy):
            FolderLock(tmp_path).acquire()
    finally:
        holder.kill()
        holder.wait()

    deadline = time.monotonic() + 10
    while is_locked(tmp_path):
        assert time.monotonic() < deadline, "the lock wasn't released"
        time.sleep(0.05)
    assert (tmp_path / LOCK_FILE).exists()  # left behind, but free
    FolderLock(tmp_path).acquire().release()


def test_a_lock_this_process_holds_counts_too(tmp_path):
    """As the app's own runs need; released, its file goes."""
    lock = FolderLock(tmp_path).acquire()

    assert is_locked(tmp_path)
    with pytest.raises(Busy):
        FolderLock(tmp_path).acquire()

    lock.release()
    lock.release()  # again: nothing
    assert not is_locked(tmp_path)
    assert not (tmp_path / LOCK_FILE).exists()


def test_a_folder_never_locked_is_free(tmp_path):
    assert not is_locked(tmp_path)
    assert not is_locked(tmp_path / "gone")
