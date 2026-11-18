"""Serialize read-only Unity bundle access with process-wide path locks.

open_bundle holds each lock through object reads, allowing exports under the chibi lock
to hold several bundles. load_bundle holds it only for loading, then reads from memory.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from UnityPy.environment import Environment
from UnityPy.files import ObjectReader

from bmpt.engine import Bundle
from ..core.errors import ServiceError

logger = logging.getLogger(__name__)
_guard = threading.Lock()
_locks: dict[Path, threading.RLock] = {}


@contextmanager
def open_bundle(path: Path) -> Iterator[Environment]:
    """Hold the process-wide path lock throughout loading and object reads.

    Args:
        path: Bundle file, resolved to select the shared path lock.

    Yields:
        The loaded Unity environment while its path lock is held.

    Raises:
        ServiceError: The bundle could not be loaded (asset_load_failed).
    """
    path = path.resolve()
    with _guard:
        lock = _locks.setdefault(path, threading.RLock())
    with lock:
        bundle = Bundle.load(path, log=logger.debug)
        if bundle is None:
            raise ServiceError("asset_load_failed", path=str(path))
        yield bundle.env


@contextmanager
def load_bundle(path: Path) -> Iterator[Environment]:
    """Load a bundle under its path lock, releasing the lock before object reads.

    Bundle.load reads a
    bundle's blocks into memory and closes the file, so its objects are read afterwards without it. A reader that
    opens several bundles this way holds at most one path lock at a time, for one load, so it never waits on a
    reader that holds many (an export) while holding one itself, and needs no chibi lock (a costume build).

    Args:
        path: Bundle file, resolved to select the shared path lock.

    Yields:
        The loaded Unity environment, with no path lock held.

    Raises:
        ServiceError: The bundle could not be loaded (asset_load_failed).
    """
    path = path.resolve()
    with _guard:
        lock = _locks.setdefault(path, threading.RLock())
    with lock:
        bundle = Bundle.load(path, log=logger.debug)
    if bundle is None:
        raise ServiceError("asset_load_failed", path=str(path))
    yield bundle.env


def reference(obj: ObjectReader, pointer: dict) -> tuple[str, int]:
    """Resolve a typetree pointer to its CAB name and path ID.

    Args:
        obj: Object owning the pointer and its serialized file's external-reference table.
        pointer: m_FileID and m_PathID fields; file ID zero refers to the owning CAB.
    """
    fid = pointer.get("m_FileID", 0)
    cab = (
        obj.assets_file.name
        if fid == 0
        else obj.assets_file.externals[fid - 1].path.replace("\\", "/").rsplit("/", 1)[-1]
    )
    return cab, pointer.get("m_PathID", 0)


def ref_key(cab: str, pid: int) -> str:
    """Encode a CAB/path-ID pair as a JSON object key.

    Args:
        cab: Serialized file name to normalize to lower case.
        pid: Unity object path ID within that CAB.
    """
    return f"{cab.lower()}:{pid}"


def object_record(path: Path, obj: ObjectReader, name: str) -> dict:
    """Build a JSON-ready object locator without retaining a Unity environment.

    Args:
        path: Containing bundle; only its basename is stored so caches survive install moves.
        obj: Unity object supplying its CAB name and path ID.
        name: Display or asset name to retain alongside the locator.
    """
    return {"bundle": path.name, "cab": obj.assets_file.name, "pid": obj.path_id, "name": name}


def find_object(env: Environment, record: dict) -> ObjectReader:
    """Find an object in a loaded bundle without confusing IDs across CABs.

    Args:
        env: Loaded environment containing the object's serialized file.
        record: Locator with exact cab and pid fields from object_record.

    Raises:
        StopIteration: No object matches both locator fields.
    """
    return next(o for o in env.objects if o.path_id == record["pid"] and o.assets_file.name == record["cab"])
