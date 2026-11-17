"""Cheap bundle discovery and resource version calculation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from bmpt.engine import parse_filename


def bundle_paths(resource_dirs: list[Path]) -> list[Path]:
    """List bundle files once, preserving resource directory precedence.

    Args:
        resource_dirs: Directories scanned nonrecursively; the first copy of each basename wins.

    Returns:
        Paths sorted by bundle basename.
    """
    found: dict[str, Path] = {}
    for directory in resource_dirs:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.endswith(".bundle") and entry.is_file():
                    found.setdefault(entry.name, Path(entry.path))
    return [found[name] for name in sorted(found)]


def resource_version(resource_dirs: list[Path]) -> str:
    """Hash the Windows catalog, falling back to sorted file names and sizes.

    Args:
        resource_dirs: Resource folders whose ancestors are searched for Catalog/Windows/catalog_Windows.hash.

    Returns:
        SHA-1 of the first catalog hash found, or of all immediate file names and sizes.
    """
    checked: set[Path] = set()
    for directory in resource_dirs:
        for parent in (directory, *directory.parents):
            catalog = parent / "Catalog" / "Windows" / "catalog_Windows.hash"
            if catalog in checked:
                continue
            checked.add(catalog)
            if catalog.is_file():
                return hashlib.sha1(catalog.read_bytes()).hexdigest()
    listing = []
    for directory in resource_dirs:
        with os.scandir(directory) as entries:
            listing.extend((entry.name, entry.stat().st_size) for entry in entries if entry.is_file())
    return hashlib.sha1(json.dumps(sorted(listing), separators=(",", ":")).encode()).hexdigest()


def group_bundles(paths: list[Path], category: str) -> dict[str, list[Path]]:
    """Group all dated bundle parts by parsed category and lower-case core.

    Args:
        paths: Bundle paths to sort and group, retaining coexisting dates and parts.
        category: Exact parse_filename category to include, such as spr or lobby.
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(paths):
        parsed = parse_filename(path.name)
        if parsed.category == category:
            groups.setdefault(parsed.core.lower(), []).append(path)
    return groups


def is_atlas(path: Path) -> bool:
    """Check whether a bundle belongs to the UI atlas family."""
    return "uis-atlas-" in path.name


def is_texture(path: Path) -> bool:
    """Check whether a bundle belongs to the shared UI texture families."""
    name = path.name
    return (
        "addressableasset-uis-_mxprolog-" in name
        or "uis-00_images-_mxload-" in name
        or "uis-00_images-formation-" in name
        or "uis-00_images-imagefont-" in name
        or name.startswith(("uis-01_common-09_popupimage-", "uis-01_common-13_campaignimage-"))
    )


def is_pointer_texture(path: Path) -> bool:
    """Check whether a bundle can hold mouse cursor or touch effect textures.

    The PC cursor is in UIs/GLPC_Cursor and the shared UI textures; Resources/UI/FX_Touch
    uses the EffectsFreezed texture families.

    Args:
        path: Bundle filename to screen for cursor/shared-dependency/touch-effect family markers without loading it.
    """
    name = path.name
    return (
        "-uis-glpc_cursor-" in name
        or "-uis-_mxcommon-_mxdependency-textures-" in name
        or "-effectsfreezed-texture-" in name
    )


def is_prefab(path: Path) -> bool:
    """Check whether a bundle can contain a UI prefab root.

    Resources/UI holds the loading screen UILoading; tooltip bundles hold UIDescriptionTooltip.

    Args:
        path: Bundle filename to screen for prefab, tooltip, or prologue UI families without loading it.
    """
    return (
        path.name.startswith(("ui-_mxload-prefabs-", "ui-uiassetobject-", "ui-tooltip-_mxload-prefabs-"))
        or (path.name.startswith("prologdepengroup-") and "-addressableasset-ui-" in path.name)
        or (path.name.startswith("prologgroup-") and "-resources-ui-" in path.name)
    )


def cab_names(path: Path) -> list[str]:
    """Read lower-case internal names, preserving corrupt-directory errors.

    Args:
        path: UnityFS bundle whose directory is inspected without decoding asset blocks.
    """
    from ..unityfs import read_cab_names

    return [name.lower() for name in read_cab_names(path) or ()]


def is_model_dependency(path: Path) -> bool:
    """Check for families holding chibi prefabs, meshes, materials, textures, shaders, or shared animators."""
    name = path.name
    return (
        "characters" in name
        or "-_mx-npcs-" in name
        or name.startswith("character-")
        or "-_mx-shaders-" in name
        or "-_mx-program-animators-" in name
    )


# parts of a character's or NPC's own dependency bundles named for an asset type that is never a model's root
_NO_ROOT_PARTS = ("-textures-", "-materials-", "-audio-", "-textassets-", "-shaders-")


def own_bundle(name: str, dev: str) -> bool:
    """Check for a character's or NPC's own dependency bundles.

    Args:
        name: Bundle filename in the assets-_mx-npcs|characters-<dev>-_mxdependency-* families.
        dev: Character or NPC dev name to match without case sensitivity.
    """
    parsed = parse_filename(name)
    return parsed.category in ("npcs", "characters") and parsed.core.lower() == dev.lower()


def may_hold_model_root(name: str) -> bool:
    """Check whether a dependency part can hold a model root.

    Parts named for textures, materials, audio, text, or shaders are excluded.

    Args:
        name: Character/NPC dependency filename whose asset-type marker determines whether to inspect its containers.
    """
    return not any(part in name for part in _NO_ROOT_PARTS)


def is_lobby_prefab(path: Path) -> bool:
    """Check for memorial lobby prefab bundles containing UILobbyElement/Lobby<Name>.prefab."""
    return path.name.startswith("ui-uilobbyelement-")


def is_layout_texture(path: Path) -> bool:
    """Check for texture-only families additionally referenced by the detail prefab.

    These supply names for embedded campaign widgets and UI effects, rather
    than expanding the shared UITexture index to character or gameplay art.

    Args:
        path: Bundle filename to screen for campaign, skill-icon, or effect texture families.
    """
    return (
        path.name.startswith(("uis-01_common-25_campaign-", "uis-02_tactics-02_skillicon-"))
        or "-effectsfreezed-texture-" in path.name
    )
