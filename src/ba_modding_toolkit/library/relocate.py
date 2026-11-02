"""Moving a mod library to another folder without a moment at which neither folder is complete.

``relocate`` copies each item into a staging folder inside the target (a temporary sibling of the final names), checks
the copy against the source, then publishes the copies under their final names. Nothing of the source changes up to
here: a failure removes what was copied and raises the cause, so the move can be retried at once. The caller then
switches to the target and calls ``Relocation.finish``, which removes the source item by item: each item is renamed
aside first (one step, so an item is either still whole in the source or gone from it) and the renamed folder is
deleted afterwards. If a source item cannot be removed, the target is complete and in use, and the source keeps that
item whole. ``Relocation.undo`` takes the published copies back when the caller cannot switch.
"""

from __future__ import annotations

import contextlib
import filecmp
import logging
import os
import shutil
import stat
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from ..filelock import replace
from ..logging_utils import log_failure
from .storage import sync_directory

logger = logging.getLogger(__name__)

STAGING = ".library-move-"  # the staging folder's name in the target, before a token
ASIDE = ".moved-"  # an item removed from the source is renamed to .<item>.moved-<token> before it is deleted
COMPARE_BYTES = 1024 * 1024  # files up to this size are compared byte by byte; larger ones by size
CHUNK = 1024 * 1024


def _unreadonly(function: Callable, path: str, error: BaseException) -> None:
    """rmtree's error handler: clear a read-only mark (copied from the source on Windows) and try once more."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove(path: Path) -> None:
    """Remove a file, a link (never what it points to) or a folder with everything in it."""
    if path.is_symlink() or not path.is_dir():
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
    else:
        shutil.rmtree(path, onexc=_unreadonly)


def _copy_file(source: Path, target: Path) -> None:
    with source.open("rb") as stream, target.open("xb") as out:
        shutil.copyfileobj(stream, out, CHUNK)
        out.flush()
        os.fsync(out.fileno())
    shutil.copystat(source, target)  # the modification time stays: the library's records compare it


def _copy(source: Path, target: Path) -> None:
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    elif source.is_dir():
        target.mkdir()
        with os.scandir(source) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            _copy(source / name, target / name)
        shutil.copystat(source, target)
    else:
        _copy_file(source, target)


def _differences(source: Path, target: Path) -> str | None:
    """What differs between a source item and its copy (a name, never a path), None when the copy is the same: the
    same names, kinds and sizes, link targets, and the same bytes in files of up to COMPARE_BYTES."""
    if source.is_symlink() or target.is_symlink():
        same = source.is_symlink() and target.is_symlink() and os.readlink(source) == os.readlink(target)
        return None if same else source.name
    if source.is_dir() or target.is_dir():
        if not (source.is_dir() and target.is_dir()):
            return source.name
        with os.scandir(source) as entries:
            names = sorted(entry.name for entry in entries)
        with os.scandir(target) as entries:
            if names != sorted(entry.name for entry in entries):
                return source.name
        for name in names:
            found = _differences(source / name, target / name)
            if found is not None:
                return found
        return None
    size = source.stat().st_size
    if size != target.stat().st_size:
        return source.name
    if size <= COMPARE_BYTES and not filecmp.cmp(source, target, shallow=False):
        return source.name
    return None


def _first_missing(folder: Path) -> Path | None:
    """The outermost folder of this path that does not exist yet, None when it all exists."""
    missing = None
    while not folder.exists():
        missing, folder = folder, folder.parent
    return missing


class Relocation:
    """The items of a source folder now also published in a target folder."""

    def __init__(self, source: Path, target: Path, published: list[str], created: Path | None) -> None:
        self.source, self.target = source, target
        self.published = published
        self.created = created  # the outermost target folder the move made, removed again by undo

    def undo(self) -> None:
        """Take the published copies back (the source was not touched), and the folders the move made."""
        for name in reversed(self.published):
            try:
                remove(self.target / name)
            except OSError as error:
                log_failure(logger, logging.ERROR, f"Cannot remove the copy of {name} from the new folder", error)
        self.published = []
        _remove_made(self.created, self.target)

    def finish(self) -> list[str]:
        """Remove the items from the source, the markers of a library first. Returns the names that are still in the
        source whole: nothing of the target depends on them, and the target is complete either way."""
        token = uuid.uuid4().hex[:12]
        left, aside = [], []
        for name in reversed(self.published):
            moved = self.source / f".{name}{ASIDE}{token}"
            try:
                replace(self.source / name, moved)
            except OSError as error:
                log_failure(logger, logging.WARNING, f"Cannot remove {name} from the old library folder", error)
                left.append(name)
                continue
            aside.append(moved)
        for moved in aside:
            try:
                remove(moved)
            except OSError as error:
                name = moved.name[1:].split(ASIDE)[0]
                log_failure(logger, logging.WARNING, f"Cannot delete the removed {name} in the old folder", error)
        return left


def _remove_made(created: Path | None, target: Path) -> None:
    """Remove the folders a move made, from the target up to the outermost one, each only while it is empty."""
    if created is None:
        return
    folder = target
    while True:
        with contextlib.suppress(OSError):
            folder.rmdir()
        if folder == created or folder == folder.parent:
            return
        folder = folder.parent


def relocate(source: Path, target: Path, items: Sequence[str]) -> Relocation:
    """Copy the items that exist in ``source`` into ``target`` and publish them there. OSError (the cause) after
    everything copied has been removed again; the source is never changed. Items are published in the given order,
    so list a library's folders before its state files."""
    source, target = Path(source), Path(target)
    present = [name for name in items if (source / name).exists()]
    for name in present:
        if target == source / name or (source / name) in target.parents:
            raise OSError(f"cannot move {name} into itself")
    created = _first_missing(target)
    published: list[str] = []
    staging = target / f"{STAGING}{uuid.uuid4().hex[:12]}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.glob(f"{STAGING}*"):  # left by a move that was cut short
            with contextlib.suppress(OSError):
                remove(stale)
        staging.mkdir()
        for name in present:
            _copy(source / name, staging / name)
        sync_directory(staging)
        for name in present:
            found = _differences(source / name, staging / name)
            if found is not None:
                raise OSError(f"the copy of {name} differs from the original")
        for name in present:
            if os.path.lexists(target / name):
                raise FileExistsError(f"{name} appeared in the new library folder")
            replace(staging / name, target / name)
            published.append(name)
        sync_directory(target)
    except BaseException:
        for name in reversed(published):
            with contextlib.suppress(OSError):
                remove(target / name)
        with contextlib.suppress(OSError):
            remove(staging)
        _remove_made(created, target)
        raise
    with contextlib.suppress(OSError):  # empty now: its items are published
        remove(staging)
    return Relocation(source, target, published, created)
