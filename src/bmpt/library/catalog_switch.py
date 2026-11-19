"""Determine catalog switch eligibility from a pinned install's layout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .records import Pin

MODE_FILES = "files"
MODE_CATALOG = "catalog"
MODS_DIR = "ModBundles"

_RESOURCE_DIR = Path("BlueArchive_Data/StreamingAssets/PUB/Resource")
_SHA1 = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class CatalogPaths:
    """The live catalog, its companion hash, and the placed-bundle directory."""

    live: Path
    hash: Path
    mods_dir: Path


def _layout(pin: Pin) -> tuple[CatalogPaths | None, str | None]:
    if pin.server != "global":
        return None, "unsupported_server"

    resource = pin.root / _RESOURCE_DIR
    live = resource / "Catalog/Windows/catalog_Windows.bytes"
    bundles = resource / "GameData/Windows"
    paths = CatalogPaths(live, live.with_suffix(".hash"), bundles / MODS_DIR)
    try:
        root = pin.root.resolve()
        if any(root not in path.resolve().parents for path in (paths.live, paths.hash, bundles, paths.mods_dir)):
            return None, "outside_root"
        if not live.is_file():
            return None, "missing_catalog"
        if bundles.resolve() not in pin.dirs or not bundles.is_dir():
            return None, "missing_bundle_folder"
    except (OSError, RuntimeError):
        return None, "invalid_layout"
    return paths, None


def catalog_paths(pin: Pin) -> CatalogPaths | None:
    """Get the fixed catalog and placed-bundle paths for an eligible install.

    Args:
        pin: The install root, search directories, and server.

    Returns:
        Paths under the pinned root, or None when the layout is ineligible.
    """
    paths, _ = _layout(pin)
    return paths


def placed_name(sha1: str) -> str:
    """Name a placed bundle after the SHA-1 of its contents.

    Args:
        sha1: The digest as 40 lowercase hexadecimal digits.

    Returns:
        The digest with the .bundle extension.

    Raises:
        ValueError: The digest is outside the required format.
    """
    if not isinstance(sha1, str) or not _SHA1.fullmatch(sha1):
        raise ValueError("Expected a SHA-1 digest of 40 lowercase hexadecimal digits.")
    return f"{sha1}.bundle"


def mode_of(pin: Pin) -> tuple[str, str | None]:
    """Select an install's switch mode from its server and filesystem layout.

    Args:
        pin: The install root, search directories, and server.

    Returns:
        Catalog mode with no reason, or file mode with the reason the layout is ineligible.
    """
    paths, reason = _layout(pin)
    return (MODE_CATALOG, None) if paths is not None else (MODE_FILES, reason)
