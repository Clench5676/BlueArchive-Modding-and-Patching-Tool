"""Files this process may still hold open, and removal that waits for them.

UnityPy keeps a bundle's file open while the objects it read are alive. Those objects form reference cycles, so the
file closes only at a garbage collection, not when the last name for them goes. Windows cannot delete, rename or
replace a file that is open: a folder holding a bundle read moments ago cannot be removed yet. ``release`` collects
those readers; ``remove_tree`` and ``temporary_directory`` release and retry before they give up.
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .logging_utils import log_failure

logger = logging.getLogger(__name__)
REMOVE_TRIES, REMOVE_WAIT = (10, 0.1) if os.name == "nt" else (1, 0.0)


def release() -> None:
    """Close the files of bundle readers that are no longer referenced."""
    gc.collect()


def remove_tree(path: str | Path, *, tries: int = REMOVE_TRIES, wait: float = REMOVE_WAIT) -> None:
    """shutil.rmtree, releasing this process's unreferenced bundle readers and retrying while a file is in use.
    A missing folder is already removed."""
    for attempt in range(1, tries + 1):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt >= tries:
                raise
            release()
            time.sleep(wait)


@contextmanager
def temporary_directory(prefix: str | None = None, dir: str | Path | None = None) -> Iterator[str]:
    """A temporary folder like tempfile.TemporaryDirectory whose removal never fails the work done in it.

    A folder that cannot be removed (a file still in use) is logged and left for a later cleanup; an error raised
    inside the block passes on unchanged."""
    path = tempfile.mkdtemp(prefix=prefix, dir=dir)
    try:
        yield path
    finally:
        try:
            remove_tree(path)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Could not remove a temporary folder", error)
