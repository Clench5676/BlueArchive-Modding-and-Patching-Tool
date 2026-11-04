"""Bound bundle sizes before loading.

A UnityFS bundle declares its own sizes and UnityPy trusts them. ``check_bundle`` reads the header and the block table
(``unityfs.read_layout``, which decompresses no data block) and refuses a bundle whose file size, single block or total
declared uncompressed size is above a ceiling, before UnityPy loads it. A file that is not UnityFS is left alone.

Every ceiling is well above the largest value of the official game files, which the test suite measures on the
verified development view.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ServiceError
from .unityfs import Layout, read_layout

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

    Returns the layout, or None for a file that is not UnityFS. A UnityFS file whose header or block table cannot be
    read is refused as well. Raises ``ServiceError("bundle_invalid", file=file)`` (``file`` defaults to the file name)
    and lets file-system errors (OSError) through."""
    name = file if file is not None else Path(path).name
    try:
        layout = read_layout(Path(path), MAX_DIRECTORY_BYTES)
    except ValueError as refusal:
        raise ServiceError("bundle_invalid", file=name) from refusal
    if layout is not None and (layout.size > MAX_BUNDLE_BYTES or layout.largest_block > MAX_BLOCK_BYTES
                               or layout.total > MAX_TOTAL_BYTES):
        raise ServiceError("bundle_invalid", file=name)
    return layout
