"""Resolve uploaded bundles from their asset names before storing them."""

from __future__ import annotations

import re
from pathlib import Path

from bmpt.engine import Bundle, parse_filename
from ..core.errors import ServiceError
from .judge import CHIBI_CATEGORIES, resource_type_of, resource_type_of_file, other_target

# Match complete Spine core names, including asset names followed by a page or role.
CORE = re.compile(r"(?<![a-z0-9_])([a-z][a-z0-9_]*?_(?:spr|home(?:_gl|_teen)?))(?=$|[^a-z0-9])", re.I)
CONTAINER = re.compile(r"(?:^|/)(spinebackground|spinelobbies|spinecharacters)/([a-z0-9_]+)(?:/|\.)", re.I)

# Meshes, textures and clips live under characters/ or npcs/, prefabs and controllers under addressableasset/character/.
CHIBI_CONTAINER = re.compile(r"(?:^|/)(?:characters|npcs|addressableasset/character)/([a-z0-9_]+)(?:/|\.)", re.I)
CHIBI_ROLE = (
    r"(?:(?:cut_?in)_)?(?:body|face|hair|head|eyemouth|eyesmouth|eyebrow|halo|weapon|"
    r"formation|cafe|victory|normal|stand|idle)(?:_[a-z0-9_]+)?(?:\.[a-z0-9]+)?"
)
CHIBI_ASSET = re.compile(r"^([a-z][a-z0-9_]*?)_" + CHIBI_ROLE + r"$", re.I)
CHIBI_PREFAB = re.compile(r"^(?:echelon|cafe|strategy)_([a-z0-9_]+)(?:\.prefab)?$", re.I)


def content_cores(path: Path) -> set[str]:
    """Identify character cores from asset names without decoding textures, meshes, or skeletons.

    Args:
        path: Uploaded bundle whose containers and object names establish its character identity.

    Returns:
        Lower-case Spine cores and chibi cores prefixed with chibi:.

    Raises:
        ServiceError: The bundle cannot be loaded (bundle_invalid).
    """
    bundle = Bundle.load(path)
    if bundle is None:
        raise ServiceError("bundle_invalid", file=path)
    names = set(bundle.env.container)
    for obj in bundle.env.objects:
        name = obj.peek_name()
        if name:
            names.add(name)
        if obj.container:
            names.add(obj.container)
    names = {name.replace("\\", "/").lower() for name in names}
    parsed = parse_filename(path.name.removesuffix(".backup"))
    special_file = resource_type_of(parsed.core, parsed.category) == "memorialLobby" and (
        parsed.category != "spinelobbies" or resource_type_of(parsed.core) != "memorialLobby"
    )
    special = {}
    for name in names:
        match = CONTAINER.search(name)
        if match and (
            match[1] == "spinebackground"
            or (
                match[2] == parsed.core.lower()
                and resource_type_of(parsed.core, match[1]) == "memorialLobby"
                and (match[1] != "spinelobbies" or resource_type_of(parsed.core) != "memorialLobby")
            )
        ):
            special[name] = match[2]
    container_suffixes: dict[str, set[str]] = {}
    # Asset paths in a Spine folder named by a core (spinelobbies/aru_home/...) belong to that core, whatever the names
    # inside it: the game's own files keep layers, effects and misspellings there that look like other cores (Aru's
    # Aru_scene, CH0060's CH0060BG_home, CH9996's CH0996_home, FX_Wakamo_home, a Hanako_home skeleton in Hare's
    # memorial lobby).
    owners = {
        name: folder[2]
        for name in names
        if name not in special
        and (folder := CONTAINER.search(name))
        and folder[1] != "spinebackground"
        and CORE.fullmatch(folder[2])
    }
    chibi_names = {name: chibi[1].lower() for name in names if (chibi := CHIBI_CONTAINER.search(name))}
    # Asset paths name the chibi a bundle replaces; only without them do prefab and object names identify it.
    contained = bool(chibi_names)
    for name in names:
        if not contained and (chibi := CHIBI_PREFAB.fullmatch(Path(name).name)):
            chibi_names[name] = chibi[1].lower()
        suffix = "home" if "spinelobbies/" in name else "spr" if "spinecharacters/" in name else None
        if suffix and name.endswith((".skel", ".atlas")):
            container_suffixes.setdefault(Path(name).stem, set()).add(suffix)
    cores = set(special.values())
    chibi_cores = set(chibi_names.values())
    if not chibi_cores and resource_type_of(parsed.core, parsed.category) == "chibi":
        chibi_cores.add(parsed.core.lower())
    for normalized in names:
        if normalized in chibi_names:
            cores.add("chibi:" + chibi_names[normalized])
            continue
        if chibi_cores:
            bare = Path(normalized).name
            # Prefabs and containers own the core; cut-in bodies and animation slots add roles to it.
            matches = {core for core in chibi_cores if re.fullmatch(re.escape(core) + "_" + CHIBI_ROLE, bare, re.I)}
            if not matches and (chibi := CHIBI_ASSET.fullmatch(bare)):
                if contained:
                    # Object names beside asset paths are free-form in the game's own files (bones, clips, props,
                    # misspelt meshes) and claim no other chibi.
                    continue
                matches.add(chibi[1].lower())
            if matches:
                cores.update("chibi:" + core for core in matches)
                continue
        if normalized in special or (special and "/" not in normalized):
            continue
        if normalized in owners:
            cores.add(owners[normalized])
            continue
        if owners and "/" not in normalized:
            # The asset paths own the bundle's names (object names of the assets the folders hold).
            continue
        if special_file and "/" not in normalized:
            if Path(normalized).stem == parsed.core.lower() and normalized.endswith((".skel", ".atlas")):
                cores.add(parsed.core.lower())
            continue
        found = CORE.findall(normalized)
        cores.update(match.lower() for match in found)
        if not found and normalized.lower().endswith((".skel", ".atlas")):
            stem = Path(normalized).stem.lower()
            if contained:
                # A chibi's own Spine effects (Kotama's skill bear, emoji) sit under its asset paths, not a portrait's.
                continue
            if special:
                # Special scenes often use several unrelated skeleton names. Their containers own those layers.
                continue
            if special_file:
                # Bare layer names do not identify a scene. The filename still owns texture-only parts too.
                if stem == parsed.core.lower():
                    cores.add(stem)
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]*", stem):
                if stem == parsed.core.lower() and resource_type_of(parsed.core, parsed.category) == "memorialLobby":
                    cores.add(stem)
                    continue
                suffixes = container_suffixes.get(stem) or {
                    "home" if resource_type_of(parsed.core, parsed.category) == "memorialLobby" else "spr"
                }
                cores.update(f"{stem}_{suffix}" for suffix in suffixes)
    return cores


def identify(path: Path, name: str, catalog, *, filename_only: bool = False) -> tuple[str, str, str]:
    """Resolve a stored filename, target, and kind, preferring character content over renames.

    Args:
        path: Uploaded bundle to inspect for character identity.
        name: Proposed bundle filename to normalize to the detected core and category.
        catalog: Character catalog resolving Spine and chibi cores to alts.
        filename_only: Whether a verified current slot permits trusting the filename without asset inspection.

    Returns:
        Normalized filename, alt or generic-set target, and library kind.

    Raises:
        ServiceError: The bundle is invalid, its character is unknown, or several targets are detected.
    """
    parsed = parse_filename(name)
    resolve = getattr(catalog, "alt_for_asset_core", catalog.alt_for_core)
    resolve_chibi = getattr(catalog, "alt_for_chibi_core", catalog.alt_for_core)
    declared_kind = resource_type_of(parsed.core, parsed.category)
    if declared_kind is None and resource_type_of_file(name) == "other":
        return name, other_target([name]), "other"
    declared = (resolve_chibi if declared_kind == "chibi" else resolve)(parsed.core.lower())
    if filename_only:
        if declared_kind == "chibi" and declared is None:
            return name, parsed.core.lower(), declared_kind
        if declared is None or declared_kind is None:
            raise ServiceError("mod_target_unknown", file=path)
        return name, declared.key, declared_kind
    cores = content_cores(path)
    resolved = [
        (core, (resolve_chibi(core[6:]) if core.startswith("chibi:") else resolve(core))) for core in sorted(cores)
    ]
    if any(alt is None for _, alt in resolved):
        raise ServiceError("mod_target_unknown", file=path)
    targets = {
        (
            alt.key,
            "chibi"
            if core.startswith("chibi:")
            else "memorialLobby"
            if getattr(alt, "spine_category", None)
            else resource_type_of(core),
        )
        for core, alt in resolved
    }
    if len(targets) > 1:
        raise ServiceError("mod_target_ambiguous", file=path)
    if not targets:
        resource_type = resource_type_of(parsed.core, parsed.category)
        if declared is None or resource_type is None:
            raise ServiceError("mod_target_unknown", file=path)
        target, alt = declared.key, declared
    else:
        target, resource_type = targets.pop()
        alt = resolved[0][1]
    if resource_type == "chibi":
        # Keep prefab/dependency categories: their separate slots share the same chibi dev name.
        chibi_cores = {core[6:] for core, _ in resolved if core.startswith("chibi:")}
        if len(chibi_cores) > 1:
            raise ServiceError("mod_target_ambiguous", file=path)
        core = next(iter(chibi_cores)) if chibi_cores else parsed.core
        category = parsed.category if parsed.category in CHIBI_CATEGORIES else "characters"
    else:
        core = (alt.portrait if resource_type == "portrait" else alt.memorial_lobby) or (
            resolved[0][0] if resolved else parsed.core
        )
        category = getattr(alt, "spine_category", None) or (
            "spinecharacters" if resource_type == "portrait" else "spinelobbies"
        )
    prefix = parsed.prefix.replace(f"-{parsed.core}-", f"-{core}-", 1)
    if parsed.category:
        prefix = prefix.replace(f"-{parsed.category}-", f"-{category}-", 1)
    return prefix + name[len(parsed.prefix) :], target, resource_type
