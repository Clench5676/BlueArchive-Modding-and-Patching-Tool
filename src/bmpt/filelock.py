"""File locks shared between processes, and a replace that waits for a moment on Windows.

Several app instances may share one data folder or one mod library: each change to a shared file is made under a lock
file the instances share (``file_lock``), and the new contents replace the file in one step (``replace``).
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

from .logging_utils import log_failure

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
    """Hold an exclusive lock on a lock file, shared with the other processes using it.

    The lock is ``fcntl.flock``, ``msvcrt.locking`` on Windows. Without a timeout, a lock file that cannot be opened
    (an unwritable folder) is logged and the block runs unlocked, so the write itself reports the failure.

    Args:
        path: The lock file; it and its folder are created when missing.
        timeout: Seconds to wait for the lock. None waits as long as the platform does (indefinitely, about 10 s on
            Windows) and runs unlocked when the file cannot be opened; with a timeout, that failure raises.

    Raises:
        TimeoutError: Another process kept the lock past ``timeout``.
        OSError: The lock file could not be opened (with a timeout only) or locked.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")  # noqa: SIM115 - held for the lock's lifetime
    except OSError as error:
        if timeout is not None:
            raise
        log_failure(logger, logging.WARNING, "Cannot lock file", error)
        yield
        return
    with handle:
        if timeout is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out locking {path}") from error
                    time.sleep(min(0.05, remaining))
        if os.name == "nt":
            import msvcrt

            if timeout is None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # retries for 10 s, then raises
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            if timeout is None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# Windows: a file another program has open without delete sharing (a virus scanner or the search indexer looking at
# the new file) cannot be replaced for a moment; a scanner may take seconds over a large game bundle
IN_USE_WAIT = 5.0 if os.name == "nt" else 0.0
REPLACE_TRIES, REPLACE_WAIT = (100, 0.05) if os.name == "nt" else (1, 0.0)


def replace(src: str | Path, dst: Path, tries: int = REPLACE_TRIES, wait: float = REPLACE_WAIT) -> None:
    """Replace ``dst`` with ``src`` like ``os.replace``, retrying for a moment while another program holds the file.

    The first retry also closes the files of this process's unreferenced bundle readers (``openfiles.release``).

    Args:
        src: The new file.
        dst: The file it replaces.
        tries: Attempts before the last PermissionError is raised; 100 on Windows, 1 elsewhere.
        wait: Seconds between attempts.

    Raises:
        PermissionError: The file stayed in use through every attempt.
    """
    from .openfiles import release

    for attempt in range(1, tries + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt >= tries:
                raise
            if attempt == 1:
                release()
            time.sleep(wait)
