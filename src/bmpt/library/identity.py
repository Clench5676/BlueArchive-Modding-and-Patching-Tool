"""Resolve uploaded bundles from their asset names before storing them."""

from __future__ import annotations

import re
from pathlib import Path

from ba_modding_toolkit.bundle import Bundle
from ..errors import ServiceError
from ba_modding_toolkit.naming import parse_filename
from .judge import MODEL_CATEGORIES, kind_of, kind_of_file, other_target

# Match complete Spine core names, including asset names followed by a page or role.
CORE = re.compile(r"(?<![a-z0-9_])([a-z][a-z0-9_]*?_(?:spr|home(?:_gl|_teen)?))(?=$|[^a-z0-9])", re.I)
CONTAINER = re.compile(r"(?:^|/)(spinebackground|spinelobbies|spinecharacters)/([a-z0-9_]+)(?:/|\.)", re.I)

# Meshes, textures and clips live under characters/ or npcs/, prefabs and controllers under addressableasset/character/.
MODEL_CONTAINER = re.compile(r"(?:^|/)(?:characters|npcs|addressableasset/character)/([a-z0-9_]+)(?:/|\.)", re.I)
MODEL_ROLE = (
    r"(?:(?:cut_?in)_)?(?:body|face|hair|head|eyemouth|eyesmouth|eyebrow|halo|weapon|"
    r"formation|cafe|victory|normal|stand|idle)(?:_[a-z0-9_]+)?(?:\.[a-z0-9]+)?")
MODEL_ASSET = re.compile(r"^([a-z][a-z0-9_]*?)_" + MODEL_ROLE + r"$", re.I)
MODEL_PREFAB = re.compile(r"^(?:echelon|cafe|strategy)_([a-z0-9_]+)(?:\.prefab)?$", re.I)


def content_cores(path: Path) -> set[str]:
    """Read names only, without decoding textures, meshes or skeleton payloads."""
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
    special_file = kind_of(parsed.core, parsed.category) == "lobby" and (
        parsed.category != "spinelobbies" or kind_of(parsed.core) != "lobby")
    special = {}
    for name in names:
        match = CONTAINER.search(name)
        if match and (match[1] == "spinebackground" or (
                match[2] == parsed.core.lower() and kind_of(parsed.core, match[1]) == "lobby"
                and (match[1] != "spinelobbies" or kind_of(parsed.core) != "lobby"))):
            special[name] = match[2]
    container_kinds: dict[str, set[str]] = {}
    owned: set[str] = set()
    model_names = {name: model[1].lower() for name in names if (model := MODEL_CONTAINER.search(name))}
    # Asset paths name the model a bundle replaces; only without them do prefab and object names identify it.
    contained = bool(model_names)
    for name in names:
        if not contained and (model := MODEL_PREFAB.fullmatch(Path(name).name)):
            model_names[name] = model[1].lower()
        kind = "home" if "spinelobbies/" in name else "spr" if "spinecharacters/" in name else None
        if kind and name.endswith((".skel", ".atlas")):
            container_kinds.setdefault(Path(name).stem, set()).add(kind)
        # A skeleton or atlas in a Spine folder named by a core (spinelobbies/aru_home/) belongs to that core, whatever
        # its own name (Aru's lobby keeps a scene layer, Aru_scene, beside Aru_home): its bare name names no other core.
        if (folder := CONTAINER.search(name)) and folder[1] != "spinebackground" and CORE.fullmatch(folder[2]):
            stem = re.sub(r"\.(?:bytes|txt)$", "", Path(name).name)
            if stem.endswith((".skel", ".atlas")):
                owned.add(Path(stem).stem)
    cores = set(special.values())
    model_cores = set(model_names.values())
    if not model_cores and kind_of(parsed.core, parsed.category) == "model":
        model_cores.add(parsed.core.lower())
    for normalized in names:
        if normalized in model_names:
            cores.add("model:" + model_names[normalized])
            continue
        if model_cores:
            bare = Path(normalized).name
            # Prefabs and containers own the core; cut-in bodies and animation slots add roles to it.
            matches = {core for core in model_cores
                       if re.fullmatch(re.escape(core) + "_" + MODEL_ROLE, bare, re.I)}
            if not matches and (model := MODEL_ASSET.fullmatch(bare)):
                if contained:
                    # Object names beside asset paths are free-form in the game's own files (bones, clips, props,
                    # misspelt meshes) and claim no other model.
                    continue
                matches.add(model[1].lower())
            if matches:
                cores.update("model:" + core for core in matches)
                continue
        if normalized in special or (special and "/" not in normalized):
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
                # A model's own Spine effects (Kotama's skill bear, emoji) sit under its asset paths, not a portrait's.
                continue
            if special:
                # Special scenes often use several unrelated skeleton names. Their containers own those layers.
                continue
            if special_file:
                # Bare layer names do not identify a scene. The filename still owns texture-only parts too.
                if stem == parsed.core.lower():
                    cores.add(stem)
                continue
            if stem in owned:
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]*", stem):
                if stem == parsed.core.lower() and kind_of(parsed.core, parsed.category) == "lobby":
                    cores.add(stem)
                    continue
                kinds = container_kinds.get(stem) or {
                    "home" if kind_of(parsed.core, parsed.category) == "lobby" else "spr"}
                cores.update(f"{stem}_{kind}" for kind in kinds)
    return cores


def identify(path: Path, name: str, catalog, *, filename_only: bool = False) -> tuple[str, str, str]:
    """Return the stored filename, target and kind. Character content wins over a renamed filename;
    verified current slots can use their filename without inspecting asset names."""
    parsed = parse_filename(name)
    resolve = getattr(catalog, "variant_for_asset_core", catalog.variant_for_core)
    resolve_model = getattr(catalog, "variant_for_model_core", catalog.variant_for_core)
    declared_kind = kind_of(parsed.core, parsed.category)
    if declared_kind is None and kind_of_file(name) == "other":
        return name, other_target([name]), "other"
    declared = (resolve_model if declared_kind == "model" else resolve)(parsed.core.lower())
    if filename_only:
        if declared_kind == "model" and declared is None:
            return name, parsed.core.lower(), declared_kind
        if declared is None or declared_kind is None:
            raise ServiceError("mod_target_unknown", file=path)
        return name, declared.key, declared_kind
    cores = content_cores(path)
    resolved = [(core, (resolve_model(core[6:]) if core.startswith("model:") else resolve(core)))
                for core in sorted(cores)]
    if any(variant is None for _, variant in resolved):
        raise ServiceError("mod_target_unknown", file=path)
    targets = {(variant.key, "model" if core.startswith("model:") else
                "lobby" if getattr(variant, "spine_category", None) else kind_of(core))
               for core, variant in resolved}
    if len(targets) > 1:
        raise ServiceError("mod_target_ambiguous", file=path)
    if not targets:
        kind = kind_of(parsed.core, parsed.category)
        if declared is None or kind is None:
            raise ServiceError("mod_target_unknown", file=path)
        target, variant = declared.key, declared
    else:
        target, kind = targets.pop()
        variant = resolved[0][1]
    if kind == "model":
        # Keep prefab/dependency categories: their separate slots share the same model dev name.
        model_cores = {core[6:] for core, _ in resolved if core.startswith("model:")}
        if len(model_cores) > 1:
            raise ServiceError("mod_target_ambiguous", file=path)
        core = next(iter(model_cores)) if model_cores else parsed.core
        category = parsed.category if parsed.category in MODEL_CATEGORIES else "characters"
    else:
        core = (variant.spr if kind == "spr" else variant.lobby) or (resolved[0][0] if resolved else parsed.core)
        category = getattr(variant, "spine_category", None) or ("spinecharacters" if kind == "spr" else "spinelobbies")
    prefix = parsed.prefix.replace(f"-{parsed.core}-", f"-{core}-", 1)
    if parsed.category:
        prefix = prefix.replace(f"-{parsed.category}-", f"-{category}-", 1)
    return prefix + name[len(parsed.prefix):], target, kind
