"""Atomic file storage and cached bundle fingerprints."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bmpt.engine import Bundle
from ..core.filelock import replace as replace_file
from ..host.store_formats import managed_bytes, runtime_store

logger = logging.getLogger(__name__)


def sync_directory(path: Path) -> None:
    """Persist a rename on filesystems that support flushing directory handles.

    Args:
        path: Directory containing the renamed entry; Windows needs no directory flush.
    """
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(fd)


def _make_parents(path: Path) -> None:
    """Persist new directory entries before a transfer can remove its source."""
    missing = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        sync_directory(directory.parent)


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    """Replace a JSON file only after the complete new contents are flushed.

    Args:
        path: Destination file, whose parent directories are created if needed.
        data: JSON document to serialize with ASCII escapes and two-space indentation.
    """
    store = runtime_store(path)
    raw = managed_bytes(store, data) if store else (json.dumps(data, ensure_ascii=True, indent=2) + "\n").encode()
    atomic_bytes(path, raw)


def atomic_bytes(path: Path, raw: bytes) -> None:
    """Replace a document only after flushing its complete contents."""
    _make_parents(path)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_copy(source: Path, target: Path) -> None:
    """Copy bytes and atomically replace a destination on its own filesystem.

    Args:
        source: File whose bytes are copied and flushed before publication.
        target: Destination file; an existing file's permission bits are preserved.
    """
    _make_parents(target)
    fd, name = tempfile.mkstemp(prefix=".library-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            temporary.chmod(target.stat().st_mode & 0o777)
        replace_file(temporary, target)
        sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _clone(source: Path, target: Path) -> bool:
    """Create a native copy-on-write clone, or report an unsupported operation."""
    if sys.platform == "darwin":
        import ctypes

        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
        clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        clonefile.restype = ctypes.c_int
        return clonefile(os.fsencode(source), os.fsencode(target), 0) == 0
    if os.name == "nt":
        _refs_clone(source, target)
        return True
    return False


def _refs_clone(source: Path, target: Path) -> None:
    """Clone aligned ReFS extents; only a final partial cluster needs a byte copy."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Extents(ctypes.Structure):
        _fields_ = [
            ("FileHandle", wintypes.HANDLE),
            ("SourceFileOffset", ctypes.c_int64),
            ("TargetFileOffset", ctypes.c_int64),
            ("ByteCount", ctypes.c_int64),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR, *([ctypes.POINTER(wintypes.DWORD)] * 4)]
    kernel.GetDiskFreeSpaceW.restype = wintypes.BOOL
    kernel.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    sectors, sector_size, free, total = (wintypes.DWORD() for _ in range(4))
    if not kernel.GetDiskFreeSpaceW(
        source.anchor, ctypes.byref(sectors), ctypes.byref(sector_size), ctypes.byref(free), ctypes.byref(total)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    cluster = sectors.value * sector_size.value
    with source.open("r+b") as input_stream, target.open("x+b") as output:
        os.fsync(input_stream.fileno())
        size = os.fstat(input_stream.fileno()).st_size
        aligned = size - size % cluster
        if not aligned:
            raise OSError(errno.ENOTSUP, "File smaller than a clone extent", str(source))
        output.truncate(size)
        offset = 0
        while offset < aligned:
            count = min(aligned - offset, (1024**3 // cluster) * cluster)
            extents = Extents(msvcrt.get_osfhandle(input_stream.fileno()), offset, offset, count)
            returned = wintypes.DWORD()
            if not kernel.DeviceIoControl(
                msvcrt.get_osfhandle(output.fileno()),
                0x00098344,
                ctypes.byref(extents),
                ctypes.sizeof(extents),
                None,
                0,
                ctypes.byref(returned),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            offset += count
        if aligned < size:
            input_stream.seek(aligned)
            output.seek(aligned)
            shutil.copyfileobj(input_stream, output)
        output.flush()
        os.fsync(output.fileno())


def atomic_clone(source: Path, target: Path) -> bool:
    """Publish a flushed native clone without falling back to a full byte copy.

    Args:
        source: File to clone with the filesystem's copy-on-write facility.
        target: Published clone location, retaining destination permission bits when replacing a file.

    Returns:
        Whether cloning succeeded; False leaves the destination unpublished.
    """
    _make_parents(target)
    temporary = target.parent / f".library-{uuid.uuid4().hex}"
    try:
        try:
            if not _clone(source, temporary):
                return False
        except (OSError, AttributeError):
            return False
        if target.exists():
            temporary.chmod(target.stat().st_mode & 0o777)
        with temporary.open("r+b" if os.name == "nt" else "rb") as stream:
            os.fsync(stream.fileno())
        replace_file(temporary, target)
        sync_directory(target.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def verify_file(path: Path, sha1: str) -> None:
    """Verify the actual destination bytes before deleting any source.

    Args:
        path: File to read in full for verification.
        sha1: Expected hexadecimal SHA-1 digest.

    Raises:
        OSError: The file cannot be read or its digest differs.
    """
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha1").hexdigest()
    if actual != sha1:
        raise OSError(errno.EIO, "File verification failed", str(path))


def verified_move(source: Path, target: Path, sha1: str) -> None:
    """Move a journaled file, verifying a cross-volume copy before deleting its source.

    Callers must write their recovery journal before starting the move.

    Args:
        source: File to flush and move, deleting it once a cross-volume copy is verified.
        target: Destination file, verified again after directory entries are flushed.
        sha1: Expected hexadecimal SHA-1 digest of the destination bytes.

    Raises:
        OSError: Transfer, flushing, or digest verification fails.
    """
    _make_parents(target)
    with source.open("r+b" if os.name == "nt" else "rb") as stream:
        os.fsync(stream.fileno())
    try:
        # Retry transient file locks from programs such as virus scanners.
        replace_file(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        atomic_copy(source, target)
        verify_file(target, sha1)
        source.unlink()
    sync_directory(target.parent)
    sync_directory(source.parent)
    verify_file(target, sha1)


@dataclass(frozen=True)
class Fingerprint:
    """Cheap bundle metadata plus lazily cached content hashes."""

    sha1: str
    crc: int
    trailing: int | None
    size: int
    sha256: str | None = None


def file_revision(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    """Identify a file revision, including replacements and changes through hard links."""
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class Fingerprints:
    """Cache hashes by resolved path, nanosecond file times, and size.

    The lock guards only the dictionary: two threads may hash one file once each,
    and the later result wins.
    """

    def __init__(self) -> None:
        """Create an empty, thread-safe fingerprint cache."""
        self._values: dict[Path, tuple[tuple[int, int, int, int, int], Fingerprint]] = {}
        self._lock = threading.Lock()

    def get(self, path: Path, *, fresh: bool = False, include_sha256: bool = False) -> Fingerprint:
        """Read a header and hashes once for each observed file version.

        Args:
            path: Bundle whose resolved path, file times, and size identify the cache entry.
            fresh: Hash the file even when its cached stat signature matches.
            include_sha256: Compute SHA-256 alongside SHA-1 and CRC when a kept original needs it.

        Returns:
            The file's SHA-1, CRC, trailing-byte count, and byte size.
        """
        path = path.resolve()
        stat = path.stat()
        signature = file_revision(stat)
        with self._lock:
            cached = self._values.get(path)
        # Windows reports creation time as ctime, so a restored mtime can hide an in-place edit.
        reusable = not (include_sha256 and os.name == "nt")
        if reusable and not fresh and cached and cached[0] == signature and (not include_sha256 or cached[1].sha256):
            return cached[1]
        sha1, crc = hashlib.sha1(), 0
        sha256 = hashlib.sha256() if include_sha256 else None
        with path.open("rb") as stream:
            if file_revision(os.fstat(stream.fileno())) != signature:
                raise OSError(errno.EIO, "File changed before verification", str(path))
            while block := stream.read(1024 * 1024):
                sha1.update(block)
                if sha256 is not None:
                    sha256.update(block)
                crc = zlib.crc32(block, crc)
        value = Fingerprint(
            sha1.hexdigest(), crc, Bundle.get_trailing_bytes(path), stat.st_size, sha256.hexdigest() if sha256 else None
        )
        if file_revision(path.stat()) != signature:
            raise OSError(errno.EIO, "File changed during verification", str(path))
        with self._lock:
            cached = self._values.get(path)
            if sha256 is None and cached and cached[0] == signature and cached[1].sha256 is not None:
                return cached[1]
            self._values[path] = (signature, value)
        return value


TAIL = 8  # the last bytes read to tell a file with trailing bytes (a CRC-fixed mod) from another of its size


def trailing_bytes(path: Path) -> int | None:
    """Count bytes beyond a bundle's declared size using only its header.

    Args:
        path: Bundle to check for a CRC-fixing suffix.

    Returns:
        The trailing-byte count, or None for an unreadable header or a truncated file.
    """
    return Bundle.get_trailing_bytes(path)


def read_tail(path: Path, count: int = TAIL) -> str | None:
    """Read a file's suffix as hexadecimal for comparison with a recorded mod.

    Args:
        path: File whose trailing bytes identify the installed copy.
        count: Number of bytes to read from the end.

    Returns:
        The hexadecimal suffix, or None if the file is unreadable or too short.
    """
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if size < count:
                return None
            stream.seek(size - count)
            return stream.read(count).hex()
    except OSError:
        return None
