"""Bound bundle sizes before loading.

A UnityFS bundle declares its own sizes and UnityPy trusts them. ``check_bundle`` reads the header and the block table
(``unityfs.read_layout``, which decompresses no data block) and refuses a bundle whose file size, single block or total
declared uncompressed size is above a ceiling, before UnityPy loads it. A file that is not UnityFS is left alone. An
archive version newer than the App reads is refused as ``bundle_unsupported`` (``refuse_unsupported`` checks only that).

Every ceiling is well above the largest value of the official game files, which the test suite measures on the
verified development view.
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import ServiceError
from .unityfs import NEWEST_ARCHIVE_VERSION, Layout, UnsupportedArchive, archive_version, read_layout

# Ceilings in bytes, each at least four times the largest value among the official game files (the test suite measures
# them on the verified development view). A bundle saved without chunking (LZMA or none) keeps its whole content in
# one block, so the single-block ceiling is far above the official chunk size.
MAX_BUNDLE_BYTES = 4 << 30
MAX_BLOCK_BYTES = 2 << 30
MAX_TOTAL_BYTES = 10 << 30
# Bounds reading the block table (compressed and declared uncompressed size).
MAX_DIRECTORY_BYTES = 4 << 20


def check_bundle(path: Path, file: str | None = None) -> Layout | None:
    """Refuse a bundle whose declared sizes are out of bounds, before anything decompresses it.

    A UnityFS file whose header or block table cannot be read is refused as well.

    Args:
        path: The bundle file.
        file: The name the error shows; None uses the file name of ``path``.

    Returns:
        The bundle's layout, or None for a file that is not UnityFS.

    Raises:
        ServiceError: ``bundle_unsupported`` with ``file`` and ``version``, for an archive version newer than the App
            reads; ``bundle_invalid`` with ``file``, for any other refused bundle.
        OSError: The file could not be read.
    """
    name = file if file is not None else Path(path).name
    try:
        layout = read_layout(Path(path), MAX_DIRECTORY_BYTES)
    except UnsupportedArchive as refusal:
        raise ServiceError("bundle_unsupported", file=name, version=refusal.version) from refusal
    except ValueError as refusal:
        raise ServiceError("bundle_invalid", file=name) from refusal
    if layout is not None and (
        layout.size > MAX_BUNDLE_BYTES or layout.largest_block > MAX_BLOCK_BYTES or layout.total > MAX_TOTAL_BYTES
    ):
        raise ServiceError("bundle_invalid", file=name)
    return layout


def refuse_unsupported(path: Path, file: str | None = None) -> None:
    """Refuse a UnityFS archive whose version is newer than the App reads; leave every other file to its loader.

    Only the first 12 bytes are read. A loader that turns any refusal into "cannot load" calls this first, so the
    user is told the format is not supported rather than that the file is broken.

    Args:
        path: The bundle file.
        file: The name the error shows; None uses the file name of ``path``.

    Raises:
        ServiceError: ``bundle_unsupported`` with ``file`` and ``version``.
    """
    try:
        version = archive_version(Path(path))
    except OSError:
        return  # an unreadable file fails in its loader as before
    if version is not None and version > NEWEST_ARCHIVE_VERSION:
        raise ServiceError("bundle_unsupported", file=file if file is not None else Path(path).name, version=version)
