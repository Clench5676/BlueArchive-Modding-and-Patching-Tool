"""Bundle filename suffixes and the CRC used for game writes."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from bmpt.engine import CRCUtils, SaveOptions

from ..core.errors import ServiceError

if TYPE_CHECKING:
    from .storage import Fingerprints

BUNDLE_SUFFIX = re.compile(r"_(?:(?P<hash>[0-9a-fA-F]{32})|(?P<crc>[0-9]+))\.bundle\Z")


def name_suffix(name: str) -> str | None:
    """Return the decimal CRC or catalog hash exactly as it appears in the name."""
    match = BUNDLE_SUFFIX.search(name.removesuffix(".backup"))
    return (match["hash"] or match["crc"]) if match else None


def name_crc(name: str) -> int | None:
    """Return a bundle name's decimal CRC, or None when it has a hash or no suffix."""
    match = BUNDLE_SUFFIX.search(name.removesuffix(".backup"))
    return int(match["crc"]) if match and match["crc"] is not None else None


def name_hash(name: str) -> str | None:
    """Return a bundle name's 32-hex catalog hash, if present."""
    match = BUNDLE_SUFFIX.search(name.removesuffix(".backup"))
    return match["hash"] if match else None


def target_crc(name: str, official: Path | None = None, *, hashes: Fingerprints | None = None) -> int | None:
    """Get the target's named CRC or, for a hash, the whole-file CRC of a verified official copy.

    The caller supplies the official copy when available. Without it, the catalog hash
    cannot determine the CRC.
    """
    crc = name_crc(name)
    if crc is not None or official is None or name_hash(name) is None:
        return crc
    return hashes.get(official).crc if hashes is not None else CRCUtils.compute_crc32(official)


def target_save_options(options: SaveOptions, name: str, official: Path | None = None) -> SaveOptions:
    """Supply the engine with an explicit CRC when saving to a hash name.

    Args:
        options: Save settings, optionally carrying an already resolved target CRC.
        name: Target bundle filename.
        official: Verified official bytes, when the CRC still needs to be resolved.

    Raises:
        ServiceError: The hash name needs a CRC and no official reference was supplied.
    """
    if not options.perform_crc or options.target_crc is not None or name_hash(name) is None:
        return options
    crc = target_crc(name, official)
    if crc is None:
        raise ServiceError("crc_fix_failed", file=name)
    return replace(options, target_crc=crc)
