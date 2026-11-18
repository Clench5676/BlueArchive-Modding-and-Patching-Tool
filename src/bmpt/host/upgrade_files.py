"""Filesystem primitives for publishing and verifying upgrade epochs."""

from __future__ import annotations

import errno
import hashlib
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .store_formats import Document, InvalidStore, json_bytes


class UnpinnablePayload(OSError):
    """The destination cannot retain a payload by clone or hard link."""


def owned_stat(path: Path) -> os.stat_result:
    """Inspect a managed path without allowing symlinks, reparse points or special files."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise InvalidStore(f"Linked managed path: {path}")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise InvalidStore(f"Unsupported managed path: {path}")
    return info


def present(path: Path) -> bool:
    """Report path presence, propagating failures other than a missing entry."""
    try:
        owned_stat(path)
    except FileNotFoundError:
        return False
    return True


def tree_files(path: Path, *, skip: frozenset[str] = frozenset()) -> Iterator[Path]:
    """List regular files in a managed tree without following links or mount points."""
    if not present(path):
        return
    root = owned_stat(path)
    if not stat.S_ISDIR(root.st_mode):
        raise InvalidStore(f"Expected a directory: {path}")

    def walk(directory: Path) -> Iterator[Path]:
        for child in sorted(directory.iterdir()):
            if child.name in skip or child.name.endswith(".lock"):
                continue
            info = owned_stat(child)
            if info.st_dev != root.st_dev:
                raise InvalidStore(f"Mount inside managed tree: {child}")
            if stat.S_ISDIR(info.st_mode):
                yield from walk(child)
            else:
                yield child

    yield from walk(path)


def digest(path: Path, algorithm: str = "sha256") -> str:
    """Read the complete file and return its digest."""
    info = owned_stat(path)
    with path.open("rb") as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise InvalidStore(f"File changed during inspection: {path}")
        answer = hashlib.file_digest(stream, algorithm).hexdigest()
        after = os.fstat(stream.fileno())
        if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise InvalidStore(f"File changed during inspection: {path}")
    if not os.path.samestat(info, owned_stat(path)):
        raise InvalidStore(f"File replaced during inspection: {path}")
    return answer


def volume(path: Path) -> str:
    """Identify the local volume before using durable publication primitives."""
    while not present(path):
        path = path.parent
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(path)],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            # diskutil accepts a mount point rather than an arbitrary directory.
            mount = path
            while mount != mount.parent and not mount.is_mount():
                mount = mount.parent
            result = subprocess.run(
                ["/usr/sbin/diskutil", "info", "-plist", str(mount)],
                capture_output=True,
                timeout=10,
                check=True,
            )
        info = plistlib.loads(result.stdout)
        return str(info.get("FilesystemType", "")).lower()
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        name = ctypes.create_unicode_buffer(32768)
        filesystem = ctypes.create_unicode_buffer(256)
        if (
            kernel.GetVolumePathNameW(str(path), name, len(name))
            and kernel.GetDriveTypeW(name) == 3
            and kernel.GetVolumeInformationW(name, None, 0, None, None, None, filesystem, len(filesystem))
        ):
            return filesystem.value.lower()
    return ""


def sync_directory(path: Path) -> None:
    """Flush POSIX directory entries. Windows replacements carry a write-through flag."""
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def flush_file(fd: int) -> None:
    """Flush an upgrade file and request a device flush where available."""
    if sys.platform == "darwin":
        import fcntl

        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def make_directory(path: Path) -> None:
    """Create and sync each missing path component."""
    if present(path):
        if not path.is_dir():
            raise InvalidStore(f"Expected a directory: {path}")
        return
    make_directory(path.parent)
    path.mkdir(exist_ok=True)
    owned_stat(path)
    sync_directory(path.parent)


def replace_path(source: Path, destination: Path) -> None:
    """Replace a file or rename an epoch on the same volume and flush its directory."""
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.MoveFileExW(str(source), str(destination), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
    sync_directory(destination.parent)
    if source.parent != destination.parent:
        sync_directory(source.parent)


def write_bytes(path: Path, raw: bytes) -> None:
    """Durably replace a file with the supplied bytes."""
    make_directory(path.parent)
    if present(path) and not path.is_file():
        raise InvalidStore(f"Expected a file: {path}")
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            flush_file(stream.fileno())
        replace_path(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_document(path: Path, data: Document) -> None:
    """Durably replace a lossless JSON document."""
    write_bytes(path, json_bytes(data))


def native_clone(source: Path, target: Path) -> bool:
    """Attempt a native copy-on-write clone with no streamed-copy fallback."""
    if os.name == "nt":
        from ..library.storage import atomic_clone

        return atomic_clone(source, target)
    if sys.platform != "darwin":
        return False
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    clonefile = library.clonefile
    clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    clonefile.restype = ctypes.c_int
    if clonefile(os.fsencode(source), os.fsencode(target), 0) == 0:
        return True
    reason = ctypes.get_errno()
    if reason not in (errno.ENOTSUP, errno.EXDEV, errno.EINVAL, errno.ENOSYS, errno.EPERM):
        raise OSError(reason, os.strerror(reason), source)
    return False


def pin(source: Path, target: Path, expected: str) -> None:
    """Pin verified payload bytes by native clone, falling back to a hard link."""
    make_directory(target.parent)
    if native_clone(source, target):
        with target.open("r+b" if os.name == "nt" else "rb") as stream:
            flush_file(stream.fileno())
    else:
        try:
            os.link(source, target, follow_symlinks=False)
        except OSError as error:
            if error.errno in (errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM, errno.EMLINK):
                raise UnpinnablePayload(error.errno, f"Cannot clone or link payload to {target}", source) from error
            raise
    if digest(target) != expected:
        raise InvalidStore(f"Payload differs from source: {target}")
    sync_directory(target.parent)
