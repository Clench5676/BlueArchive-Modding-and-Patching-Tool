"""Conservative Mod origin detection from Unity metadata and the selected install.

A CAB is an identity, not a region tag. It only supplies region evidence when it
matches a recoverable official file in the selected install. Textual resource
suffixes are shared by current Global and Japan releases and prove neither.
"""

from __future__ import annotations

import re
from typing import Any

from .library.integrity import Contents

SERVERS = ("global", "japan")
PLATFORMS = ("android", "ios", "windows")
UNITY_PLATFORMS = {
    "Android": "android",
    "iOS": "ios",
    "iPhone": "ios",
    "StandaloneWindows": "windows",
    "StandaloneWindows64": "windows",
}
UNKNOWN_PLATFORMS = {"UnknownPlatform", "NoTarget", "Unknown", "0", "-2"}


def platform_of(contents: Contents) -> str | None:
    """Return the one platform the bundles' Unity build targets name, unknown targets left out.

    Returns:
        ``"android"``, ``"ios"`` or ``"windows"``; None when no target or several platforms are named, or a target
        the App does not know.
    """
    platforms = {UNITY_PLATFORMS.get(name) for name in contents.platforms - UNKNOWN_PLATFORMS}
    return next(iter(platforms)) if len(platforms) == 1 else None


def server_hints(names: set[str]) -> set[str]:
    """Return the servers that names point to.

    Global is named by its package name or a ``_home_gl`` suffix, Japan by its package name.

    Args:
        names: Asset and file names, reference paths and install folders; case and separators do not matter.
    """
    hints = set()
    for value in names:
        value = value.lower().replace("\\", "/")
        if "com.nexon.bluearchive" in value or re.search(r"_home_gl(?=$|[^a-z0-9])", value):
            hints.add("global")
        if "com.yostarjp.bluearchive" in value.replace(" ", ""):
            hints.add("japan")
    return hints


def names_of(contents: Contents) -> set[str]:
    """Return every name in a bundle's metadata: assets, files, references, Spine skeletons, atlases and textures."""
    return (
        contents.names
        | contents.files
        | contents.references
        | contents.reference_paths
        | set(contents.skeletons)
        | set(contents.atlases)
        | set(contents.textures)
    )


def platform_hints(names: set[str]) -> set[str]:
    """Return the platforms named as a path segment (``android``, ``ios``, ``windows``) in any of ``names``."""
    return {part.lower() for name in names for part in name.replace("\\", "/").split("/") if part.lower() in PLATFORMS}


def target_of(settings: Any, pin: Any, official: Contents | None = None) -> dict:
    """Return the server and platform of the install a mod is meant for.

    The platform comes from the official bundles' build targets. Without them it comes from a platform folder in the
    pinned install's paths, else from the settings: Android in ADB mode, Windows otherwise. The server is the single
    server the official names and install folders point to, else the pin's, else the settings' region.

    Args:
        settings: The App settings (``is_adb_mode``, ``server_region``).
        pin: The game install and server the operation is fixed to; None when there is no install.
        official: Metadata of the official bundles the mod replaces; None when they are unknown.

    Returns:
        ``{"server", "platform"}``; the platform is None when the evidence names several.
    """
    platform = platform_of(official) if official and official.platforms else None
    if platform is None and not (official and official.platforms):
        parts = {part.lower() for directory in pin.dirs for part in directory.parts} if pin else set()
        folder_platforms = parts & {"windows", "android", "ios"}
        platform = next(iter(folder_platforms)) if len(folder_platforms) == 1 else None
        if platform is None and not folder_platforms:
            platform = "android" if settings.is_adb_mode() else "windows"
    names = names_of(official) if official else set()
    if pin:
        names.update(str(directory) for directory in pin.dirs)
    hints = server_hints(names)
    server = next(iter(hints)) if len(hints) == 1 else pin.server if pin else settings.server_region()
    return {"server": server, "platform": platform}


def constraints(names: set[str], selected: Contents, official: Contents, target: dict) -> tuple[dict, str | None]:
    """Return what a mod's own files prove about its server and platform.

    Each dimension is constrained by reliable evidence even when the other stays unknown. The platform comes from the
    bundles' build targets, else from a platform folder in their names. The server comes from names pointing to one
    server; without such names, a mod whose CAB names all match official files of the selected install takes the
    target's server, since a CAB is an identity, not a region tag.

    Args:
        names: The mod's file names.
        selected: Metadata of the mod's bundles.
        official: Metadata of the official bundles the mod replaces.
        target: The install the mod is meant for, as ``target_of`` gives it.

    Returns:
        ``({"server", "platform"}, None)`` with None for an unproven dimension, or ``({}, error)``:
        ``platform_unsupported`` for a build target the App does not handle, ``source_conflict`` for evidence of two
        platforms or two servers.
    """
    platforms = {UNITY_PLATFORMS.get(name) for name in selected.platforms}
    known = selected.platforms - UNKNOWN_PLATFORMS
    if any(name not in UNITY_PLATFORMS for name in known):
        return {}, "platform_unsupported"
    if len(platforms - {None}) > 1:
        return {}, "source_conflict"
    platform = platform_of(selected)
    if platform is None:
        hints = platform_hints(names_of(selected))
        if len(hints) > 1:
            return {}, "source_conflict"
        platform = next(iter(hints)) if hints else None
    hints = server_hints(names | names_of(selected))
    if not hints:
        cabs = {name for name in selected.files if name.startswith("cab-") and "." not in name}
        if cabs and cabs <= official.files:
            hints.add(target["server"])
    if len(hints) > 1:
        return {}, "source_conflict"
    return {"server": next(iter(hints)) if hints else None, "platform": platform}, None


def source_of(names: set[str], selected: Contents, official: Contents, target: dict) -> tuple[dict | None, str | None]:
    """Return the mod's source when its files prove both server and platform.

    Args:
        names: The mod's file names.
        selected: Metadata of the mod's bundles.
        official: Metadata of the official bundles the mod replaces.
        target: The install the mod is meant for, as ``target_of`` gives it.

    Returns:
        ``({"server", "platform", "detected": True}, None)``, or ``(None, error)``: the error of ``constraints``, or
        ``source_unknown`` when either dimension stays unproven.
    """
    evidence, error = constraints(names, selected, official, target)
    if error:
        return None, error
    if not all(evidence.values()):
        return None, "source_unknown"
    return {**evidence, "detected": True}, None


def candidates(
    names: set[str], selected: Contents, official: Contents | None = None, target: dict | None = None
) -> list[dict]:
    """Return the sources a mod may come from, varying only what its files leave unproven.

    Args:
        names: The mod's file names.
        selected: Metadata of the mod's bundles.
        official: Metadata of the official bundles the mod replaces; None for none.
        target: The install the mod is meant for, as ``target_of`` gives it; None for none.

    Returns:
        ``{"server", "platform", "detected": True}`` in Global, Japan and Android, iOS, Windows order; empty when the
        evidence conflicts.
    """
    evidence, error = constraints(names, selected, official or Contents(), target or {})
    if error:
        return []
    return [
        {"server": server, "platform": platform, "detected": True}
        for server in SERVERS
        for platform in PLATFORMS
        if evidence["server"] in (None, server) and evidence["platform"] in (None, platform)
    ]
