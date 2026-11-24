"""Shared UnityFS directory parsing without decompressing asset data blocks."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

from UnityPy.enums.BundleFile import ArchiveFlags, ArchiveFlagsOld
from UnityPy.helpers.CompressionHelper import DECOMPRESSION_MAP

from ..core.errors import ServiceError

# This bounds metadata allocation, not the size of the bundle or its asset data.
_MAX_BLOCK_INFO = 64 * 1024 * 1024
#: The newest UnityFS archive version this parser reads. Later versions lay out the block table differently, so
#: reading them with the 10-byte block entries of versions 6 to 8 would give wrong sizes and names.
NEWEST_ARCHIVE_VERSION = 8


class UnsupportedArchive(ServiceError, ValueError):
    """A UnityFS archive of a version newer than ``NEWEST_ARCHIVE_VERSION``.

    It is the ``bundle_unsupported`` service error, with ``file`` and ``version``, and a ValueError, so callers that
    skip unreadable bundles skip it too.
    """

    def __init__(self, path: Path, version: int):
        """Create the refusal.

        Args:
            path: The bundle file.
            version: The archive version its header declares.
        """
        super().__init__("bundle_unsupported", file=Path(path).name, version=version)
        self.version = version

    def __str__(self) -> str:
        """Name the file, its archive version and the newest version read."""
        return (
            f"UnityFS archive version {self.version} is not supported "
            f"(newest supported: {NEWEST_ARCHIVE_VERSION}): {self.params['file']}"
        )


def archive_version(path: Path) -> int | None:
    """Return the archive version a UnityFS header declares, or None for a file that is not UnityFS.

    Raises:
        OSError: The file could not be read.
    """
    with open(path, "rb") as stream:
        head = stream.read(12)
    if len(head) < 12 or head[:8] != b"UnityFS\0":
        return None
    return struct.unpack(">I", head[8:])[0]


def _cstring(stream) -> bytes:
    out = bytearray()
    for _ in range(4096):
        char = stream.read(1)
        if not char or char == b"\0":
            return bytes(out)
        out.extend(char)
    raise ValueError("UnityFS header string is too long")


def _engine_version(value: bytes) -> tuple[int, ...]:
    parts = []
    for token in value.decode("ascii", "ignore").split("."):
        digits = "".join(char for char in token if char.isdigit()) if token.isdigit() else ""
        if not digits:
            for char in token:
                if not char.isdigit():
                    break
                digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _read_directory(path: Path, max_directory: int) -> tuple[bytes, int] | None:
    """The decompressed block and node directory and the physical file size, or None for a non-UnityFS file.

    ``max_directory`` bounds both the compressed and the declared uncompressed size of the directory before anything
    is read or decompressed. Raises UnsupportedArchive for an archive version above ``NEWEST_ARCHIVE_VERSION``,
    ValueError for a corrupt, encrypted or otherwise unsupported directory.
    """
    with open(path, "rb") as stream:
        if stream.read(8) != b"UnityFS\0":
            return None
        version = struct.unpack(">I", stream.read(4))[0]
        if version > NEWEST_ARCHIVE_VERSION:
            raise UnsupportedArchive(path, version)
        _cstring(stream)
        engine = _engine_version(_cstring(stream))
        length, compressed, uncompressed, flags = struct.unpack(">qIII", stream.read(20))
        physical = os.fstat(stream.fileno()).st_size
        if (
            length < stream.tell()
            or length > physical
            or not 0 < compressed <= max_directory
            or not 24 <= uncompressed <= max_directory
        ):
            raise ValueError("Invalid UnityFS directory size")
        old = (
            engine < (2020,)
            or engine[0] == 2020
            and engine < (2020, 3, 34)
            or engine[0] == 2021
            and engine < (2021, 3, 2)
            or engine[0] == 2022
            and engine < (2022, 1, 1)
        )
        archive_flags = ArchiveFlagsOld(flags) if old else ArchiveFlags(flags)
        if archive_flags & archive_flags.UsesAssetBundleEncryption:
            raise ValueError("Encrypted UnityFS directory")
        if flags & ArchiveFlags.BlocksInfoAtTheEnd:
            # A CRC correction may append bytes outside the declared bundle.
            start = length - compressed
            if start < stream.tell():
                raise ValueError("UnityFS directory overlaps its header")
            stream.seek(start)
        else:
            if version >= 7:
                stream.seek((stream.tell() + 15) & ~15)
            elif engine >= (2019, 4):
                # Version 6 files can use either alignment, as in UnityPy.
                start = stream.tell()
                if any(stream.read((-start) % 16)):
                    stream.seek(start)
            if stream.tell() + compressed > length:
                raise ValueError("Truncated UnityFS directory")
        packed = stream.read(compressed)
        if len(packed) != compressed:
            raise ValueError("Truncated UnityFS directory")
    data = DECOMPRESSION_MAP[flags & 0x3F](packed, uncompressed)
    if len(data) != uncompressed:
        raise ValueError("Invalid UnityFS directory length")
    return data, physical


def _block_table(data: bytes) -> tuple[int, int]:
    """The block count and the offset of the node count that follows the block table."""
    count = struct.unpack_from(">i", data, 16)[0]
    pos = 20 + count * 10
    if count < 0 or pos + 4 > len(data):
        raise ValueError("Invalid UnityFS block table")
    return count, pos


def read_cab_names(path: Path) -> list[str] | None:
    """Return the names of the files a UnityFS bundle holds (its CAB names), in their original case.

    Only the header and the compressed directory are read.

    Args:
        path: The bundle file.

    Returns:
        The names in directory order, or None for a file that is not UnityFS.

    Raises:
        UnsupportedArchive: The archive version is newer than this parser reads.
        ValueError: The directory is corrupt, encrypted or unsupported.
        OSError: The file could not be read.
    """
    try:
        directory = _read_directory(path, _MAX_BLOCK_INFO)
        if directory is None:
            return None
        data = directory[0]
        _, pos = _block_table(data)
        nodes = struct.unpack_from(">i", data, pos)[0]
        pos += 4
        if nodes < 0 or nodes > (len(data) - pos) // 21:
            raise ValueError("Invalid UnityFS node table")
        names = []
        for _ in range(nodes):
            pos += 20
            end = data.index(b"\0", pos)
            names.append(data[pos:end].decode("utf-8", "replace"))
            pos = end + 1
        return names
    except (OSError, UnsupportedArchive):
        raise
    except Exception as exc:
        raise ValueError(f"Not a readable UnityFS directory: {path}") from exc


@dataclass(frozen=True)
class Layout:
    """The declared sizes of a UnityFS bundle, from its header and block table alone."""

    size: int  # physical file size
    directory: int  # declared uncompressed size of the block and node directory
    blocks: int  # number of data blocks
    largest_block: int  # largest declared uncompressed size of a single block
    total: int  # sum of the declared uncompressed sizes of all blocks


def read_layout(path: Path, max_directory: int = _MAX_BLOCK_INFO) -> Layout | None:
    """Return the declared sizes of a UnityFS bundle.

    Reads the header and the compressed directory only: no data block is decompressed.

    Args:
        path: The bundle file.
        max_directory: Bytes the directory may take, compressed and declared uncompressed, checked before it is read.

    Returns:
        The layout, or None for a file that is not UnityFS.

    Raises:
        UnsupportedArchive: The archive version is newer than this parser reads.
        ValueError: The directory is corrupt, encrypted, unsupported or above ``max_directory``.
        OSError: The file could not be read.
    """
    try:
        directory = _read_directory(path, max_directory)
        if directory is None:
            return None
        data, physical = directory
        count, _ = _block_table(data)
        largest = total = 0
        for uncompressed, _, _ in struct.iter_unpack(">IIH", memoryview(data)[20 : 20 + count * 10]):
            total += uncompressed
            largest = max(largest, uncompressed)
        return Layout(physical, len(data), count, largest, total)
    except (OSError, UnsupportedArchive):
        raise
    except Exception as exc:
        raise ValueError(f"Not a readable UnityFS directory: {path}") from exc
