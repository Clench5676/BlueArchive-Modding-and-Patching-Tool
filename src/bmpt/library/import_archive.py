"""Mod archives (ZIP) taken for import: their bundles are unpacked once into the library's staging folder.

Only the archive's own bundles are unpacked, with the package manifests beside them (``ba_mod``, read when the
bundles are taken). Archives inside it stay packed and are left out, like every other file that is no bundle.
Member names come from an untrusted file: each must stay below the unpacked folder, and the folder layout
inside the archive is kept, so mods in separate folders of one archive stay separate mods.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from ..core import devmode
from ..engine import is_safe_relative_name
from ..core.errors import ServiceError
from ..core.filelock import file_lock
from ..core.openfiles import remove_tree
from .ba_mod import FILE_NAME as MANIFEST

logger = logging.getLogger(__name__)
PREFIX = "zip-"
SUFFIX = ".zip"
BUNDLE_SUFFIXES = (".bundle", ".bundle.backup")
DIGITS = 16  # a short folder name, as for the import staging folders (Windows path length)
SPACE_MARGIN = 512 * 1024 * 1024


def is_archive(path: Path) -> bool:
    """Return whether the filename has a case-insensitive ZIP extension."""
    return path.name.lower().endswith(SUFFIX)


def _members(archive: zipfile.ZipFile, file: Path) -> tuple[list[tuple[zipfile.ZipInfo, tuple[str, ...]]], int]:
    """Pick the bundle and package manifest members of an archive to unpack.

    Returns:
        The members to unpack with their path parts, and the number of other members left out.
    """
    taken, keys, skipped = [], set(), 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if (
            not parts
            or not is_safe_relative_name(name)
            or name.startswith("/")
            or not (parts[-1].endswith(BUNDLE_SUFFIXES) or parts[-1] == MANIFEST)
            # the resource forks a macOS archiver adds beside each file
            or parts[0] == "__MACOSX"
            or parts[-1].startswith("._")
        ):
            skipped += 1
            continue
        if info.flag_bits & 0x1:
            raise ServiceError("archive_invalid", file=str(file))  # encrypted
        key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
        if key in keys:
            skipped += 1  # the same name twice cannot be told apart on a case-insensitive disk
            continue
        keys.add(key)
        taken.append((info, parts))
    return taken, skipped


def _digest(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(file: Path, root: Path) -> tuple[list[Path], int]:
    """Unpack an archive's bundles and manifests into reusable library staging storage.

    Args:
        file: ZIP archive whose content digest identifies the reusable unpacked folder.
        root: Library folder containing staging storage.

    Returns:
        Bundle paths and the number of skipped members, including nested archives,
        other files, unsafe names, and duplicates. Manifests are unpacked but not returned.

    Raises:
        ServiceError: The archive is damaged or encrypted (archive_invalid), space is
            insufficient (not_enough_space), staging is busy, or filesystem access fails
            (library_io_error).
    """
    staging = root / "staging"
    devmode.check_read(file)
    devmode.check_read(staging)
    devmode.check_write(staging)
    try:
        digest = _digest(file)
        with zipfile.ZipFile(file) as archive:
            members, skipped = _members(archive, file)
            folder = staging / f"{PREFIX}{digest[:DIGITS]}"
            paths = [folder.joinpath(*parts) for _, parts in members]
            bundles = [path for path in paths if path.name != MANIFEST]
            if not bundles:
                return [], skipped + len(paths)
            staging.mkdir(parents=True, exist_ok=True)
            with file_lock(staging / ".lock", timeout=5):
                if folder.is_dir() and all(path.is_file() for path in paths):
                    os.utime(folder)  # in use again: a startup cleanup keeps it for another day
                    return bundles, skipped
            need = sum(info.file_size for info, _ in members)
            free = shutil.disk_usage(staging).free
            if need + SPACE_MARGIN > free:
                raise ServiceError("not_enough_space", need=need, free=free)
            temporary = Path(tempfile.mkdtemp(prefix=f"{PREFIX}part-", dir=staging))
            try:
                for (info, parts), path in zip(members, paths):
                    output = temporary.joinpath(*parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, output.open("xb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                with file_lock(staging / ".lock", timeout=5):
                    if folder.is_dir() and all(path.is_file() for path in paths):
                        pass  # another instance unpacked the same archive meanwhile: its copy is the same
                    else:
                        if folder.exists():
                            remove_tree(folder)  # an earlier unpacking that lost files
                        os.rename(temporary, folder)
            finally:
                remove_tree(temporary)  # nothing left there once renamed
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        RuntimeError,
        EOFError,
        ValueError,
        zlib.error,
    ) as error:
        logger.info("Import: archive not readable (%s)", type(error).__name__)
        raise ServiceError("archive_invalid", file=str(file)) from None
    except TimeoutError:
        raise ServiceError("busy") from None
    except OSError as error:
        raise ServiceError("library_io_error", file=error.filename or str(file)) from None
    return bundles, skipped
