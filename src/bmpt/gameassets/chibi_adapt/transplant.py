"""``adapt``: the costume switch of one slot from start to end, and the edits of its prefabs."""

from __future__ import annotations

import copy
import dataclasses
import logging
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

import numpy as np

from ...core.progress_meter import QUIET, Meter
from .. import chibi_geometry as mg
from .. import chibi_hems as hm
from .. import chibi_skirts as ms
from ..errors import AssetNotFound
from ..costume_cache import PoseStore
from ..unity import load_bundle
from ..chibi import (
    BundleGraph,
    NPC_PREFAB,
    PREFAB_KINDS,
    _external_cab,
    _key,
    _prefabs,
    chibi_root,
)
from .costume import Costume, Plan, flatten, plan
from .face import clip_edit_error, clip_snapshot, face_retarget, saved_tree
from .halo import _IN, _MATERIAL, _MESH, _ROOT, DRAWN, HaloChange, _halo_change, _mesh_tree, _pointers
from .parts import Part, Skin, bindposes, deform, pick, prefab_clips, probe_poses, roles, skin_of
from .skeleton import FIELD_KINDS, IDLE_CLIPS, Node, Skeleton, _inverse, _shader_name, loaded_with, refuse
from .skirts import Hems, Skirt, curve_frames, hems, rigidify, skirt_bones, skirt_curves, springs
from .writing import (
    CARRIED_SHADER,
    EX_PARTS,
    MOUTH_ATLAS,
    MOUTH_FIELDS,
    _Writer,
    Mouth,
    Rendered,
    Written,
    _matrix,
    _sanity,
    _vector,
    mouth_of,
    retiled_mouths,
)

if TYPE_CHECKING:
    from ..chibi_props import WornStore


logger = logging.getLogger(__package__)


@dataclass
class Result:
    """Return writable changed bundles, adaptation choices, hem edits, and transfer cautions."""

    changed: dict[Path, Any]  # slot file -> engine Bundle with the changes
    plan: Plan
    parts: int
    hems: Hems | None = None
    cautions: list[str] = field(default_factory=list)  # ``EX_PARTS``, ``CARRIED_SHADER``
    dropped: list[str] = field(default_factory=list)  # slot renderers whose worn items are hidden
    carried: list[str] = field(default_factory=list)  # source renderers whose worn items are transferred
    arms: int = 0  # Optimize Physics: the slot's clips copied with the arms pushed out of the costume
    left: list[str] = field(default_factory=list)  # source set pieces that draw nothing (their top bones)
    kept: list[str] = field(default_factory=list)  # slot set pieces drawn again (their top bones)


def _chibi_prefabs(graph: BundleGraph, files: list[Path]) -> dict[str, Any]:
    return {name: pointer for name, pointer in _prefabs(graph, files).items()}


def own_prefabs(prefabs: Mapping[str, Any], dev: str) -> dict[str, Any]:
    """Select a chibi's own prefab kinds and field NPC prefabs by exact dev identity.

    Another chibi containing the dev name, such as ch0336_carrier beside ch0336, is excluded.

    Args:
        prefabs: Lower-case container stems mapped to prefab root pointers.
        dev: Character dev name or NPC ID used by PREFAB_KINDS and NPC_PREFAB.
    """
    names = {pattern.format(dev).lower() for pattern in PREFAB_KINDS.values()}
    return {
        name: pointer
        for name, pointer in prefabs.items()
        if name in names
        or ((match := NPC_PREFAB.fullmatch(name)) is not None and match.group(1).lower() == dev.lower())
    }


def choose_prefab(prefabs: Mapping[str, Any], dev: str) -> str:
    """Choose an echelon, battle, cafe, strategy, or field-player prefab in that order.

    Args:
        prefabs: Available lower-case prefab stems for the chibi.
        dev: Character dev name used to construct exact candidate stems.

    Raises:
        ServiceError: No supported prefab is present (source_unsupported).
    """
    for kind in ("echelon", "battle", "cafe", "strategy"):
        name = PREFAB_KINDS[kind].format(dev).lower()
        if name in prefabs:
            return name
    for pattern in FIELD_KINDS:
        name = pattern.format(dev).lower()
        if name in prefabs:
            return name
    raise refuse("no prefab", dev=dev)


# Progress weights use measured phase durations with and without Optimize Physics across cold, memory, and disk
# caches. Arm time varies by pair; "curves" includes arm clip copies and skirt, hair, and face curves.
# "stage" (the slot's copies) comes before the build and "save" (the changed bundles written, lzma) after it, both in
# the library's ``_stage_costume``.
COSTUME_PHASES: tuple[tuple[str, float, float], ...] = (
    ("stage", 0.01, 0.01),
    ("read", 0.04, 0.04),
    ("skeleton", 0.03, 0.03),
    ("clips", 0.14, 0.14),
    ("idle", 0.52, 0.64),
    ("worn", 0.37, 0.37),
    ("probes", 0.10, 0.09),
    ("plan", 0.76, 0.81),
    ("arms", 0.00, 3.16),
    ("hems", 0.47, 1.50),
    ("rigid", 0.01, 0.01),
    ("springs", 0.00, 0.45),
    ("face", 0.01, 0.03),
    ("chains", 0.01, 0.01),
    ("open", 0.07, 0.08),
    ("materials", 0.01, 0.01),
    ("meshes", 0.04, 0.04),
    ("carried", 0.01, 0.01),
    ("halo", 0.02, 0.12),
    ("prefabs", 0.03, 0.06),
    ("curves", 2.19, 3.20),
    ("check", 0.02, 0.02),
    ("save", 6.60, 7.59),
)


def costume_weights(physics: bool) -> list[tuple[str, float]]:
    """Return progress-meter phase weights, selecting Optimize Physics timings when physics is enabled."""
    return [(name, with_physics if physics else plain) for name, plain, with_physics in COSTUME_PHASES]


def adapt(
    *,
    index: Mapping[str, Path],
    resolver: Callable | None,
    target_files: list[Path],
    target_dev: str,
    source_files: list[Path],
    source_dev: str,
    open_writable: Callable[[Path], Any],
    physics: bool = False,
    halo: bool = False,
    step: Callable[[], None] | None = None,
    worn: WornStore | None = None,
    meter: Meter | None = None,
    poses: PoseStore | None = None,
) -> Result:
    """Build changed slot bundles carrying another chibi's costume without saving them.

    A costume past the stretch limit is built and applied all the same (``plan.unfit``).
    The slot's worn items (``chibi_props``: hats, glasses, bags) draw nothing and the source's are carried;
    packed items remain with the costume that encloses them (chibi_props).
    Set pieces in the costume meshes (``chibi_setpieces``: a wheelchair, a prop the clips bring out) stay with the
    slot they were made for: the source's draw nothing, the slot's are drawn again.
    The graph reads every bundle from memory (``unity.load_bundle``: a path lock for each load alone), so a build
    needs no chibi lock and exports run beside it. ``step`` is called between the build's steps (most under 0.5 s;
    the plan, the hems and a cold first read of the idle poses up to a few seconds, measured on Hina wearing Aoba):
    the caller's job yields there (``preview_gate.step``) or stops by raising.

    Args:
        index: Lower-case CAB names mapped to readable official copies, with slot CABs pointing to target_files.
        resolver: Optional on-demand lookup for CABs absent from index.
        target_files: Official destination slot copies, including its prefab and own dependency bundles.
        target_dev: Destination chibi dev name selecting the slot prefab and clip hierarchy.
        source_files: Incoming chibi's prefab bundles or own dependency bundles containing its rendered root.
        source_dev: Incoming chibi dev name selecting its costume, clips, and halo.
        open_writable: Opens a slot file as an engine Bundle for object edits; the caller saves returned bundles.
        physics: Whether Optimize Physics enables arm clearance, Rule B, and secondary-chain motion.
        halo: Whether to carry the source halo; False hides the slot halo.
        step: Optional no-argument yield/cancellation callback between build steps; raising cancels the build.
        worn: Optional per-chibi/game-revision cache of worn-item verdicts without Unity readers.
        meter: Optional progress meter receiving COSTUME_PHASES updates; None uses QUIET.
        poses: Optional per-chibi/game-revision store for numerical idle/probe matrices.

    Raises:
        ServiceError: Unsupported source data, unresolved references, or an edit verification failure refuses the pair.
    """
    with BundleGraph(index, resolver=resolver, opener=load_bundle) as graph:
        return _adapt(
            graph,
            index,
            target_files,
            target_dev,
            source_files,
            source_dev,
            open_writable,
            physics,
            halo,
            step or (lambda: None),
            worn,
            meter or QUIET,
            poses,
        )


def _adapt(
    graph: BundleGraph,
    index: Mapping[str, Path],
    target_files: list[Path],
    target_dev: str,
    source_files: list[Path],
    source_dev: str,
    open_writable: Callable[[Path], Any],
    physics: bool = False,
    halo: bool = False,
    step: Callable[[], None] = lambda: None,
    worn: WornStore | None = None,
    meter: Meter = QUIET,
    poses: PoseStore | None = None,
) -> Result:
    from .. import chibi_props as mp
    from .. import chibi_setpieces as sp

    phase = meter.phase
    phase("read")
    target_prefabs = _chibi_prefabs(graph, [f for f in target_files if f.name.startswith("character-")])
    source_prefabs = _chibi_prefabs(graph, source_files)
    t_name = choose_prefab(target_prefabs, target_dev)
    try:
        # a battle or field chibi, else a field NPC or a story-only or UI-only chibi of its own bundles
        s_root, s_name, _ = chibi_root(graph, source_files, source_dev)
    except AssetNotFound:
        raise refuse("no prefab", dev=source_dev) from None
    step()
    phase("skeleton")
    t_skeleton = Skeleton.of(graph, target_prefabs[t_name].deref())
    s_skeleton = Skeleton.of(graph, s_root)
    t_skeleton.pose_store, t_skeleton.dev = poses, target_dev
    s_skeleton.pose_store, s_skeleton.dev = poses, source_dev
    if not s_skeleton.renderers:
        # an unskinned chibi (Kei's Decagrammaton body, a static mesh): nothing to bind to the slot's bones
        raise refuse("static chibi", dev=source_dev)
    step()
    phase("clips")
    own = own_prefabs(target_prefabs, target_dev)
    t_clips = prefab_clips(graph, t_skeleton, own)
    s_clips = prefab_clips(graph, s_skeleton, own_prefabs(source_prefabs, source_dev))
    step()
    phase("idle")
    t_idle = next(iter(t_skeleton.poses(pick(t_clips, IDLE_CLIPS)[:1], frames=1)), None)
    step()
    phase("idle", 1, 2)
    s_idle = next(iter(s_skeleton.poses(pick(s_clips, IDLE_CLIPS)[:1], frames=1)), None)
    step()
    t_parts, s_parts = roles(t_skeleton, t_idle), roles(s_skeleton, s_idle)

    def costume(parts: list[Part]) -> list[Part]:
        ordered = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]
        if not ordered or ordered[0].role != "primary":
            raise refuse("no costume")
        return ordered

    t_costume, s_costume = costume(t_parts), costume(s_parts)
    phase("worn")
    t_worn = mp.worn_of(t_skeleton, t_parts, t_clips, t_idle, target_dev, worn, step)
    step()
    phase("worn", 1, 2)
    s_worn = mp.worn_of(s_skeleton, s_parts, s_clips, s_idle, source_dev, worn, step)
    step()
    phase("probes")
    target = Costume(t_skeleton, t_costume, [skin_of(p.mesh) for p in t_costume])
    source = Costume(s_skeleton, s_costume, [skin_of(p.mesh) for p in s_costume])
    step()
    probes = probe_poses(t_skeleton, t_clips)
    mesh_trees = [_mesh_tree(p.mesh) for p in s_costume]
    layouts = [mg.geometry_of(tree) for tree in mesh_trees]
    step()
    phase("plan")
    own_probes = probe_poses(s_skeleton, s_clips)
    result = plan(
        target,
        source,
        probes,
        fit=all(layout is not None for layout in layouts),
        own_probes=own_probes,
        clips=t_clips,
    )
    # set pieces (a wheelchair, a prop the clips bring out): the source's stay behind, the slot's stay
    s_flat, t_flat = flatten(source, s_skeleton.world()), flatten(target, t_skeleton.world())
    s_pieces = sp.set_pieces(source, s_flat, s_idle, own_probes)
    t_pieces = sp.set_pieces(target, t_flat, t_idle, probes)
    s_left = sp.part_triangles(source, s_flat, s_pieces)
    t_kept = sp.part_triangles(target, t_flat, t_pieces)
    step()
    from .. import chibi_arm_clips, chibi_arm_frames, chibi_arm_solve

    # Optimize Physics only: the arms pushed out of a bulkier costume cost seconds on an affected pair
    arm_clips = (
        chibi_arm_solve.retarget(
            target, source, result, t_clips, t_parts, step=step, progress=lambda done, count: phase("arms", done, count)
        )
        if physics
        else {}
    )
    t_rest, s_rest = t_skeleton.world(), s_skeleton.world()
    shapes = [result.fit.part(k, s_rest[part.node]) for k, part in enumerate(s_costume)]
    for k, (skin, (points, _)) in enumerate(zip(source.skins, shapes)):
        if layouts[k] is None and float(np.abs(points - skin.vertices).max(initial=0.0)) > 1e-4:
            raise refuse("costume geometry not writable", part=s_costume[k].mesh.peek_name())
    step()
    phase("hems")
    stores = [hm.store_of(tree) for tree in mesh_trees]
    hem = hems(
        target,
        source,
        result,
        t_clips,
        s_clips,
        [store is not None for store in stores],
        physics,
        shapes_writable=all(layout is not None for layout in layouts),
        meter=meter,
        step=step,
    )
    if hem.thin is not None:  # Optimize Physics thinned the legs under the cloth: the bound shape changes there
        rows, moved = hem.thin
        vertices = result.fit.vertices.copy()
        vertices[rows] = moved
        result.fit = dataclasses.replace(result.fit, vertices=vertices)
        shapes = [result.fit.part(k, s_rest[part.node]) for k, part in enumerate(s_costume)]
    step()
    phase("rigid")
    rigidify(target, source, result, probes, hem, [store is not None for store in stores])
    # Rule B's skirt, then (Optimize Physics) the swinging chains: new bones of the slot's skeleton, with curves in
    # the slot's clips that move their anchor
    phase("springs")
    chained = ([hem.skirt] if hem.skirt is not None else []) + (
        springs(target, source, result, s_clips, result.fit) if physics and result.fit is not None else []
    )
    step()
    phase("face")
    face = face_retarget(target, source, result, result.fit) if result.fit is not None else None
    if face is not None:  # another character's face: its eye, brow and mouth bones follow the slot's expressions
        chained.append(face)
    step()
    phase("chains")
    skirt_clips: list[Any] = []
    clip_skirts: dict[tuple[str, int], list[Skirt]] = {}
    for skirt in chained:
        if skirt.base < 0:
            skirt.base = len(t_skeleton.nodes)
        skirt_bones(t_skeleton, skirt)
        for k, n in enumerate(skirt.sources):
            result.mapping[n] = skirt.node(k)
        for n, k in skirt.riders.items():
            result.mapping[n] = skirt.node(k)
        anchor_hashes = {zlib.crc32(t_skeleton.nodes[a].path.encode()) for a in (skirt.anchors or [skirt.anchor])}
        for clip in t_clips:
            bindings = graph.tree(clip)["m_ClipBindingConstant"]["genericBindings"]
            if any(b["path"] in anchor_hashes and b["typeID"] == 4 for b in bindings):
                if _key(clip) not in clip_skirts:
                    skirt_clips.append(clip)
                clip_skirts.setdefault(_key(clip), []).append(skirt)
    hem.springs = [skirt for skirt in chained if skirt is not hem.skirt and skirt is not face]
    hem.face = face
    if chained:
        t_rest = t_skeleton.world()

    step()
    phase("open")
    # the slot files the new objects go to: the primary's mesh, materials and textures, and the prefab files
    primary = t_costume[0]
    mesh_cab = primary.mesh.assets_file.name.lower()
    material_cab = (
        primary.materials[0].assets_file.name.lower()
        if primary.materials and primary.materials[0] is not None
        else None
    )
    texture_cab = None
    for material in primary.materials:
        for _, value in graph.tree(material)["m_SavedProperties"]["m_TexEnvs"] if material is not None else []:
            texture = graph.deref(material, value["m_Texture"])
            if texture is not None:
                texture_cab = texture.assets_file.name.lower()
                break
        if texture_cab:
            break
    slot = {cab: path for cab, path in index.items() if Path(path) in set(target_files)}
    prefab_files = {Path(graph.locations[p.deref().assets_file.name.lower()]) for p in own.values()}
    loaded = loaded_with(graph, prefab_files, slot)
    # A target whose primary lives in another core's files (Izumi (Swimsuit) on Izumi's, ch0263 on the NPC en0002's)
    # never gets objects written there: the new objects go to a slot file loaded with the prefab that holds that type.
    kinds_in: dict[str, set[str]] = {}
    for obj in graph.objects.values():
        kinds_in.setdefault(obj.assets_file.name.lower(), set()).add(obj.type.name)
    prefab_cabs = {cab for cab, path in slot.items() if Path(path) in prefab_files}

    def home(cab: str | None, type_name: str) -> str:
        if cab is not None and cab in slot:
            return cab
        for candidate in sorted(loaded & set(slot), key=lambda c: (c not in prefab_cabs, Path(slot[c]).name, c)):
            if type_name in kinds_in.get(candidate, ()):
                return candidate
        raise refuse(
            "costume outside the slot", part=type_name, file=Path(str(graph.locations.get(cab or "", "?"))).name
        )

    mesh_cab, material_cab, texture_cab = (
        home(mesh_cab, "Mesh"),
        home(material_cab, "Material"),
        home(texture_cab, "Texture2D"),
    )
    # Rule B's curves go into the slot's own clips, where they are: a clip in another file stays as it is
    skirt_clips = [c for c in skirt_clips if c.assets_file.name.lower() in slot]
    arm_holders = chibi_arm_clips.receivers(graph, t_skeleton, own) if arm_clips else []
    if any(h.assets_file.name.lower() not in slot for h in arm_holders):
        raise refuse("arm controller outside the slot")
    arm_destinations = {k: home(k[0], "AnimationClip") for k in arm_clips}
    needed = (
        {mesh_cab, material_cab, texture_cab}
        | prefab_cabs
        | {c.assets_file.name.lower() for c in skirt_clips}
        | set(arm_destinations.values())
        | {h.assets_file.name.lower() for h in arm_holders}
    )
    opened: dict[Path, Any] = {}
    files: dict[str, Any] = {}
    for cab in needed:
        path = Path(slot[cab])
        bundle = opened.get(path) or open_writable(path)
        opened[path] = bundle
        for name, sf in bundle.env.file.files.items():
            if hasattr(sf, "objects"):
                files[name.lower()] = (bundle, sf)
    step()
    writer = _Writer(files, f"{target_dev}|{source_dev}")
    mesh_sf, material_sf, texture_sf = writer.file(mesh_cab), writer.file(material_cab), writer.file(texture_cab)

    def shared(cab: str) -> bool:
        """Whether the game has ``cab`` whenever the target's prefab is loaded.

        That holds for a slot file, a file the prefab's files already reference (its catalog dependencies), or an
        always-loaded (prolog) bundle.
        """
        path = index.get(cab) or (graph.locations.get(cab))
        if cab in slot or cab in loaded:
            return True
        return path is not None and Path(path).name.startswith(("prologdepengroup-", "prologgroup-"))

    # shaders the game does not load with the slot's prefab, carried as copies in the materials' file
    shaders: dict[tuple[str, int], int] = {}

    def carried(shader: Any, cab: str) -> int:
        if shader is None or shader.type.name != "Shader":
            raise refuse("shader not found", file=Path(str(index.get(cab) or graph.locations.get(cab, "?"))).name)
        if _key(shader) in shaders:
            return shaders[_key(shader)]
        s_tree = shader.read_typetree()
        fixed = [
            pair[1] if isinstance(pair, (list, tuple)) else pair.get("second", {})
            for pair in s_tree.get("m_NonModifiableTextures", [])
        ]
        if any(isinstance(texture, dict) and texture.get("m_PathID") for texture in fixed):
            raise refuse("shader with fixed textures", shader=_shader_name(shader))
        for dependency in s_tree.get("m_Dependencies", []):
            if not dependency["m_PathID"]:
                continue
            entry = shader.assets_file.externals[dependency["m_FileID"] - 1] if dependency["m_FileID"] else None
            dependency_cab = _external_cab(shader, dependency["m_FileID"])
            if entry is not None and not entry.path.lower().startswith("archive:"):
                dependency["m_FileID"] = writer.builtin(material_sf, entry)  # the player's own resources
            elif shared(dependency_cab):
                dependency["m_FileID"] = writer.external(material_sf, dependency_cab)
            else:
                dependency.update(m_FileID=0, m_PathID=carried(graph.deref(shader, dependency), dependency_cab))
        shaders[_key(shader)] = writer.add_like(material_sf, shader, s_tree)
        return shaders[_key(shader)]

    textures: dict[tuple[str, int], int] = {}
    materials: dict[tuple[str, int], int] = {}
    # the mouth: the slot's clips name cells of the slot's mouth atlas, so a source eye-mouth material drawing another
    # atlas takes the slot's (retiled_mouths)
    mouth = mouth_of(s_skeleton, s_costume)
    slot_atlas, retiled, mouth = retiled_mouths(graph, t_skeleton, t_costume, s_costume, mouth)
    if retiled:
        logger.info(
            "Costume of %s on %s: %d eye-mouth material(s) take the slot's mouth atlas", s_name, t_name, len(retiled)
        )

    def carry_material(material: Any) -> int:
        """A source material added to the slot's material file, with its textures.

        Its shader is added too where the game does not load it with the slot's prefab; an eye-mouth material in
        ``retiled`` is added with the slot's mouth atlas.
        """
        if _key(material) not in materials:
            tree = material.read_typetree()
            owners: dict[str, Any] = {}  # a texture entry taken from another material: that material
            if _key(material) in retiled and slot_atlas is not None:
                props = tree["m_SavedProperties"]
                envs = props["m_TexEnvs"]
                for i, pair in enumerate(envs):
                    if pair[0] == MOUTH_ATLAS:
                        envs[i] = type(pair)((MOUTH_ATLAS, copy.deepcopy(slot_atlas.env)))
                owners[MOUTH_ATLAS] = slot_atlas.material
                floats = props.setdefault("m_Floats", [])
                for name, value in slot_atlas.grid.items():
                    at = next((i for i, pair in enumerate(floats) if pair[0] == name), None)
                    if at is None:
                        floats.append(type(floats[0])((name, value)) if floats else (name, value))
                    else:
                        floats[at] = type(floats[at])((name, value))
            shader_cab = _external_cab(material, tree["m_Shader"]["m_FileID"])
            if tree["m_Shader"]["m_PathID"] and not shared(shader_cab):
                # a shader the game does not load with the slot's prefab: a copy of it goes into the material's file
                tree["m_Shader"] = {
                    "m_FileID": 0,
                    "m_PathID": carried(graph.deref(material, tree["m_Shader"]), shader_cab),
                }
            else:
                tree["m_Shader"] = {
                    "m_FileID": writer.external(material_sf, shader_cab),
                    "m_PathID": tree["m_Shader"]["m_PathID"],
                }
            for env_name, value in tree["m_SavedProperties"]["m_TexEnvs"]:
                pointer = value["m_Texture"]
                if not pointer["m_PathID"]:
                    continue
                owner = owners.get(env_name, material)
                cab = _external_cab(owner, pointer["m_FileID"])
                if shared(cab):
                    value["m_Texture"] = {
                        "m_FileID": writer.external(material_sf, cab),
                        "m_PathID": pointer["m_PathID"],
                    }
                    continue
                texture = graph.deref(owner, pointer)
                if texture is None:
                    raise refuse("texture not found")
                if _key(texture) not in textures:
                    t_tree = texture.read_typetree()
                    t_tree["image data"] = texture.read().get_image_data()
                    t_tree["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
                    textures[_key(texture)] = writer.add(texture_sf, "Texture2D", t_tree)
                value["m_Texture"] = {
                    "m_FileID": writer.external(material_sf, texture_cab),
                    "m_PathID": textures[_key(texture)],
                }
            materials[_key(material)] = writer.add(material_sf, "Material", tree)
        return materials[_key(material)]

    own_materials = [m for part in s_costume for m in part.materials if m is not None]
    for i, material in enumerate(own_materials):
        if _key(material) in materials:
            continue
        step()
        phase("materials", i, len(own_materials))
        carry_material(material)

    # meshes: the source parts' meshes with bindposes on the target skeleton
    new_meshes = []
    written: list[Written] = []
    rendered: list[Rendered] = []
    for k, (part, skin) in enumerate(zip(s_costume, source.skins)):
        step()
        phase("meshes", k, len(s_costume))
        tree = mesh_trees[k]
        nodes = [result.mapping.get(b, 0) if b is not None else 0 for b in part.bones]
        points, linear = shapes[k]
        if layouts[k] is not None and float(np.abs(points - skin.vertices).max(initial=0.0)) > 1e-6:
            try:
                mg.write_geometry(tree, layouts[k], points, linear)
            except OverflowError:  # a position past what half-precision or packed floats hold
                raise refuse("vertices out of range", part=part.mesh.peek_name()) from None
        if k in s_left and not mp.drop_triangles(tree, s_left[k]):
            logger.info(
                "Costume of %s on %s: set pieces of part %s kept: its triangles cannot be edited",
                s_name,
                t_name,
                part.mesh.peek_name(),
            )
            s_left.pop(k)
        skin = Skin(points, skin.joints, skin.weights, skin.edges, skin.triangles)
        joints, weights = skin.joints, skin.weights
        aabbs = len(tree["m_BonesAABB"]) == len(nodes)
        change = hem.parts[k] if k < len(hem.parts) else None
        if change is not None:  # the hem's new weights, on bones of this part's list or added to it
            rows, slot_nodes, new_weights = change
            slots: dict[int, int] = {}
            for i, n in enumerate(nodes):
                slots.setdefault(n, i)
            grown = list(nodes)
            for n in slot_nodes.ravel().tolist():
                if n not in slots:
                    slots[n] = len(grown)
                    grown.append(n)
            new_joints = np.vectorize(slots.get, otypes=[int])(slot_nodes)
            try:
                hm.write_weights(tree, stores[k], rows, new_joints, new_weights)
            except hm.SkinNotWritable as error:  # more bones than the mesh's index format holds: its skin stays
                logger.info(
                    "Costume of %s on %s: hem skipped on part %s: %s", s_name, t_name, part.mesh.peek_name(), error
                )
                hem.parts[k] = None
            else:
                nodes = grown
                joints, weights = joints.copy(), weights.copy()
                joints[rows], weights[rows] = new_joints, new_weights
        poses = [_inverse(t_rest[n], t_skeleton.nodes[n].name) @ s_rest[part.node] for n in nodes]
        tree["m_BindPose"] = [_matrix(m) for m in poses]
        tree["m_BoneNameHashes"] = [zlib.crc32(t_skeleton.nodes[n].path.encode()) for n in nodes]
        tree["m_RootBoneNameHash"] = graph.tree(primary.mesh)["m_RootBoneNameHash"]
        if aabbs:
            tree["m_BonesAABB"] += [
                {"m_Min": _vector((0, 0, 0)), "m_Max": _vector((0, 0, 0))}
                for _ in range(len(nodes) - len(tree["m_BonesAABB"]))
            ]
        for i, pose in enumerate(poses):
            hit = ((joints == i) & (weights > 0)).any(axis=1)
            if hit.any() and i < len(tree["m_BonesAABB"]):
                local = (np.c_[skin.vertices[hit], np.ones(hit.sum())] @ pose.T)[:, :3]
                tree["m_BonesAABB"][i] = {"m_Min": _vector(local.min(axis=0)), "m_Max": _vector(local.max(axis=0))}
        written.append(
            Written(
                part.mesh.peek_name(),
                points,
                linear,
                joints,
                weights,
                np.array(poses),
                [t_skeleton.nodes[n].name for n in nodes],
                len(tree["m_SubMeshes"]),
                source.skins[k].weights.sum(axis=1),
                [0 if m is None else 1 for m in part.materials],
                bool((tree.get("m_StreamData") or {}).get("size")),
            )
        )
        new_meshes.append((writer.add(mesh_sf, "Mesh", tree), nodes))
    step()
    phase("carried")
    # the source's worn items: further parts, bound rigidly to the slot's bones they follow
    carried_parts = mp.carried(t_skeleton, s_skeleton, s_worn, s_clips, s_idle, _mesh_tree)
    step()
    root_hash = graph.tree(primary.mesh)["m_RootBoneNameHash"]
    for c in carried_parts:
        for material in c.part.materials:
            if material is not None:
                carry_material(material)
        tree = c.tree
        tree["m_BindPose"] = [_matrix(m) for m in c.bindposes]
        tree["m_BoneNameHashes"] = [zlib.crc32(t_skeleton.nodes[n].path.encode()) for n in c.nodes]
        tree["m_RootBoneNameHash"] = root_hash
        if len(tree["m_BonesAABB"]) == len(c.nodes):
            for i, pose in enumerate(c.bindposes):
                hit = ((c.joints == i) & (c.weights > 0)).any(axis=1)
                if hit.any():
                    local = (np.c_[c.vertices[hit], np.ones(hit.sum())] @ pose.T)[:, :3]
                    tree["m_BonesAABB"][i] = {"m_Min": _vector(local.min(axis=0)), "m_Max": _vector(local.max(axis=0))}
        written.append(
            Written(
                c.part.mesh.peek_name(),
                c.vertices,
                c.linear,
                c.joints,
                c.weights,
                c.bindposes,
                [t_skeleton.nodes[n].name for n in c.nodes],
                len(tree["m_SubMeshes"]),
                c.weights.sum(axis=1),
                [0 if m is None else 1 for m in c.part.materials],
                False,
            )
        )
        new_meshes.append((writer.add(mesh_sf, "Mesh", tree), c.nodes))
    # the slot's set pieces: each costume mesh holding one is drawn again, on its own bones, without the costume
    kept_parts: list[Part] = []
    for k, triangles in sorted(t_kept.items()):
        part, skin = t_costume[k], target.skins[k]
        tree = _mesh_tree(part.mesh)
        poses = bindposes(graph, part.mesh)
        rest_of = np.setdiff1d(np.arange(len(skin.triangles)), triangles)
        if len(poses) != len(part.bones) or (len(rest_of) and not mp.drop_triangles(tree, rest_of)):
            logger.info(
                "Costume of %s on %s: set pieces of the slot's part %s not kept: its mesh cannot be edited",
                s_name,
                t_name,
                part.mesh.peek_name(),
            )
            continue
        for material in part.materials:
            if material is not None:
                carry_material(material)
        nodes = [b if b is not None else 0 for b in part.bones]
        written.append(
            Written(
                part.mesh.peek_name(),
                skin.vertices,
                None,
                skin.joints,
                skin.weights,
                poses,
                [t_skeleton.nodes[n].name for n in nodes],
                len(tree["m_SubMeshes"]),
                skin.weights.sum(axis=1),
                [0 if m is None else 1 for m in part.materials],
                bool((tree.get("m_StreamData") or {}).get("size")),
            )
        )
        new_meshes.append((writer.add(mesh_sf, "Mesh", tree), nodes))
        kept_parts.append(part)
    # the slot's worn items: a renderer drawing nothing else the clips show draws nothing, another a copy of its mesh
    # without them
    worn_meshes = mp.host_meshes(t_worn, lambda tree: writer.add(mesh_sf, "Mesh", tree), _mesh_tree)
    step()
    phase("halo")
    # the source's halo: its mesh added as it is (a skinned one is drawn unskinned), or the slot's hidden
    halo_change = (
        _halo_change(
            t_skeleton,
            s_skeleton,
            lambda mesh: writer.add(mesh_sf, "Mesh", _mesh_tree(mesh)),
            carry_material,
            (s_name, t_name),
            result.fit.head if result.fit is not None else None,
        )
        if halo
        else None
    )

    # prefabs: the primary takes the source primary, further parts get renderers of their own, the rest draws nothing
    rank = {"primary": 0, "costume": 1, "alternate": 2, "copy": 3}
    t_meshes: dict[tuple[str, int], str] = {}
    for p in sorted((p for p in t_parts if p.mesh is not None and p.role in rank), key=lambda p: rank[p.role]):
        t_meshes.setdefault(_key(p.mesh), "primary" if p.role == "primary" else "other")
    t_primary_materials = [_key(m) for m in primary.materials if m is not None]
    root_bone = graph.deref(primary.renderer, graph.tree(primary.renderer)["m_RootBone"])
    root_node = t_skeleton.by_transform.get(_key(root_bone), 0) if root_bone is not None else 0
    # the renderers' culling boxes hold each part at rest and in every probe pose, so parts that move far from their
    # rest place do not vanish at the screen's edge
    arm_probes = chibi_arm_frames.bounds_poses(t_skeleton, arm_clips)
    bounds = [
        _bounds(w, nodes, t_skeleton, root_node, [t_rest, *probes, *arm_probes])
        for w, (_, nodes) in zip(written, new_meshes)
    ]
    if mouth is None:
        logger.info(
            "Costume of %s on %s: the source has no mouth receiver on its costume, the slot's stays as it is",
            s_name,
            t_name,
        )
    for i, prefab_path in enumerate(prefab_files):
        step()
        phase("prefabs", i, len(prefab_files))
        bundle = opened[prefab_path]
        for name, sf in bundle.env.file.files.items():
            if hasattr(sf, "objects") and name.lower() in files:
                from_source = [*s_costume, *(c.part for c in carried_parts)]
                drawn_parts = [*from_source, *kept_parts]
                _edit_prefabs(
                    writer,
                    sf,
                    graph,
                    t_meshes,
                    t_primary_materials,
                    mesh_cab,
                    material_cab,
                    new_meshes,
                    [[materials[_key(m)] if m is not None else 0 for m in p.materials] for p in drawn_parts],
                    [s_skeleton.nodes[p.node].name for p in from_source]
                    + [t_skeleton.nodes[p.node].name for p in kept_parts],
                    root_node,
                    t_skeleton,
                    bounds,
                    halo_change,
                    rendered,
                    mouth,
                    worn_meshes,
                )
    step()
    # the "curves" phase: the arms' clip copies first (each clip they change counts as one), then the clips edited
    # in place
    curves = len(arm_clips) + len(skirt_clips)
    phase("curves", 0, curves)
    chibi_arm_clips.write_copies(writer, arm_clips, arm_destinations, arm_holders, t_skeleton, clip_skirts, step=step)
    for i, clip in enumerate(skirt_clips):
        step()
        phase("curves", len(arm_clips) + i, curves)
        if _key(clip) in arm_clips:
            continue
        obj = writer.file(clip.assets_file.name.lower()).objects[clip.path_id]
        tree = obj.read_typetree()
        before = clip_snapshot(tree) if ms.dense_grid(tree) is not None else None
        edited = [skirt for skirt in clip_skirts[_key(clip)] if skirt.solver.roots]
        prepared = curve_frames(t_skeleton, edited, tree) if before is not None and edited else None
        bones = (
            [skirt_curves(t_skeleton, skirt, tree, _key(clip), prepared=prepared, step=step) for skirt in edited]
            if before is not None
            else [None]
        )
        if edited and None not in bones and sum(bones):
            obj.save_typetree(tree)
            # each bone with curves got a rotation (4 curves) and a position (3): read the saved clip back
            problem = clip_edit_error(before, saved_tree(obj), 7 * sum(bones))
            if problem is not None:
                raise refuse("clip edit not verified", clip=clip.peek_name(), what=problem)
            for skirt in edited:
                skirt.clips += 1
        else:
            logger.info(
                "Costume of %s on %s: clip %s left without skirt curves (layout)", s_name, t_name, clip.peek_name()
            )
    phase("check")
    _sanity(written, rendered)
    cautions = []
    alternates = sorted(p.mesh.peek_name() for p in t_parts if p.role == "alternate" and p.mesh is not None)
    if alternates:  # a cut-in body or alternative face: blank for the frames the EX timeline shows it
        cautions.append(EX_PARTS)
        logger.info("Costume of %s on %s: EX-only parts draw nothing: %s", s_name, t_name, ", ".join(alternates))
    if shaders:  # death dissolve and hit flash look shaders up by name: untested on a carried copy
        cautions.append(CARRIED_SHADER)
        logger.info("Costume of %s on %s: %d shader(s) carried with the costume", s_name, t_name, len(shaders))
    logger.info(
        "Costume of %s on %s: %d part(s), groups %s, stretch %.2f (own %.2f), shape %s (changed %.3f, 99th "
        "percentile %.3f); hem %s%s",
        s_name,
        t_name,
        len(s_costume),
        {s_skeleton.nodes[n].name: c for n, c in result.choices.items()},
        result.stretch,
        result.own,
        result.fit.shape,
        result.fit.largest,
        result.fit.shift,
        hem.summary(),
        f"; {result.repair}" if result.repair else "",
    )
    dropped = sorted(
        t_skeleton.nodes[n].name for n, items in t_worn.by_renderer().items() if _key(items[0].part.mesh) in worn_meshes
    )
    carried_names = [s_skeleton.nodes[c.part.node].name for c in carried_parts]
    if dropped or carried_names:
        logger.info(
            "Costume of %s on %s: worn items of %s draw nothing, of %s are carried",
            s_name,
            t_name,
            ", ".join(dropped) or "none",
            ", ".join(carried_names) or "none",
        )
    left = sorted({s_skeleton.nodes[p.top].name for p in s_pieces}) if s_left else []
    kept = sorted({t_skeleton.nodes[p.top].name for p in t_pieces}) if kept_parts else []
    if left or kept:
        logger.info(
            "Costume of %s on %s: set pieces of the costume draw nothing (%s), the slot's stay (%s)",
            s_name,
            t_name,
            ", ".join(left) or "none",
            ", ".join(kept) or "none",
        )
    return Result(opened, result, len(s_costume), hem, cautions, dropped, carried_names, len(arm_clips), left, kept)


def _edit_prefabs(
    writer: _Writer,
    sf: Any,
    graph: BundleGraph,
    t_meshes: Mapping[tuple[str, int], str],
    primary_materials: list[tuple[str, int]],
    mesh_cab: str,
    material_cab: str,
    new_meshes: list[tuple[int, list[int]]],
    new_materials: list[list[int]],
    names: list[str],
    root_node: int,
    t_skeleton: Skeleton,
    bounds: list[dict],
    halo: HaloChange | None = None,
    rendered: list[Rendered] | None = None,
    mouth: Mouth | None = None,
    worn: Mapping[tuple[str, int], int | None] | None = None,
) -> None:
    """Make one prefab file's renderers draw the new costume.

    ``worn``: the slot's meshes of worn items and what their renderers draw instead (None for nothing, else a new mesh
    in the mesh file).
    """
    objects = sf.objects
    trees: dict[int, dict] = {}

    def tree(pid: int) -> dict:
        if pid not in trees:
            trees[pid] = objects[pid].read_typetree()
        return trees[pid]

    def save(pid: int) -> None:
        objects[pid].save_typetree(trees[pid])

    mesh_fid, material_fid = writer.external(sf, mesh_cab), writer.external(sf, material_cab)
    bundle_object = next(o for o in objects.values() if o.type.name == "AssetBundle")
    asset_bundle = bundle_object.read_typetree()
    additions: dict[int, list[dict]] = {}  # container entry -> additional objects its prefab loads
    seen = rendered if rendered is not None else []  # what is written on the renderers, for ``_sanity``
    for entry, (container, info) in enumerate(asset_bundle["m_Container"]):
        if not container.lower().endswith(".prefab") or info["asset"]["m_FileID"] != 0:
            continue
        # the prefab's transforms by path below its root (as the skeleton names them) and its renderers
        by_path: dict[str, int] = {}
        renderers: list[int] = []
        behaviours: list[int] = []
        stack = [(info["asset"]["m_PathID"], None)]
        while stack:
            go_pid, path = stack.pop()
            go = tree(go_pid)
            transform = None
            for c in go["m_Component"]:
                pid = c["component"]["m_PathID"]
                if c["component"]["m_FileID"] != 0 or pid not in objects:
                    continue
                kind = objects[pid].type.name
                if kind == "Transform":
                    transform = pid
                elif kind == "SkinnedMeshRenderer":
                    renderers.append(pid)
                elif kind == "MonoBehaviour":
                    behaviours.append(pid)
            if transform is None:
                continue
            here = "" if path is None else path
            by_path.setdefault(here, transform)
            for child in tree(transform)["m_Children"]:
                if child["m_FileID"] == 0 and child["m_PathID"] in objects:
                    child_go = tree(child["m_PathID"])["m_GameObject"]["m_PathID"]
                    name = tree(child_go)["m_Name"]
                    stack.append((child_go, f"{here}/{name}" if here else name))

        added: list[dict] = []

        def bone(n: int) -> dict:
            node = t_skeleton.nodes[n]
            pid = by_path.get(node.path)
            if pid is None and node.transform is None and node.parent is not None:
                pid = new_bone(node)  # a bone of Rule B: added to this prefab below its parent
            if pid is None:
                raise refuse("bone missing in a prefab", bone=node.path)
            return {"m_FileID": 0, "m_PathID": pid}

        def new_bone(node: Node) -> int:
            father = bone(node.parent)["m_PathID"]
            father_go = tree(father)["m_GameObject"]["m_PathID"]
            go_pid = writer.fresh(sf)
            tf_pid = writer.fresh(sf)
            while tf_pid == go_pid:
                tf_pid = writer.fresh(sf)
            (px, py, pz), (rx, ry, rz, rw), (sx, sy, sz) = node.local
            t = {
                **tree(father),
                "m_GameObject": {"m_FileID": 0, "m_PathID": go_pid},
                "m_LocalRotation": {"x": float(rx), "y": float(ry), "z": float(rz), "w": float(rw)},
                "m_LocalPosition": _vector((px, py, pz)),
                "m_LocalScale": _vector((sx, sy, sz)),
                "m_Children": [],
                "m_Father": {"m_FileID": 0, "m_PathID": father},
            }
            _add_with_id(writer, sf, "Transform", t, tf_pid)
            g = {
                **tree(father_go),
                "m_Name": node.name,
                "m_IsActive": True,
                "m_Component": [{"component": {"m_FileID": 0, "m_PathID": tf_pid}}],
            }
            _add_with_id(writer, sf, "GameObject", g, go_pid)
            # a new object reads back as its template until the bundle is saved: its tree is kept here instead
            trees[tf_pid], trees[go_pid] = t, g
            tree(father)["m_Children"].append({"m_FileID": 0, "m_PathID": tf_pid})
            save(father)
            by_path[node.path] = tf_pid
            added.extend({"m_FileID": 0, "m_PathID": pid} for pid in (go_pid, tf_pid))
            return tf_pid

        main = None
        costume_renderers = set()  # the slot's renderers of its costume: a mouth receiver pointing at one follows
        for pid in renderers:
            r = tree(pid)
            mesh = r["m_Mesh"]
            key = (_cab(sf, mesh["m_FileID"]), mesh["m_PathID"]) if mesh["m_PathID"] else None
            role = t_meshes.get(key) if key is not None else None
            if role is None:
                if worn and key in worn:  # worn items of the slot's costume
                    instead = worn[key]
                    r["m_Mesh"] = {"m_FileID": mesh_fid if instead else 0, "m_PathID": instead or 0}
                    if instead:
                        added.append({"m_FileID": mesh_fid, "m_PathID": instead})
                    save(pid)
                continue
            costume_renderers.add(pid)
            if role == "primary":
                # its materials wherever they live: a slot's body may take them from two files (Hifumi (Swimsuit))
                own = [(_cab(sf, m["m_FileID"]), m["m_PathID"]) for m in r["m_Materials"] if m["m_PathID"]]
                main_renderer = own == primary_materials
                mesh_pid, nodes = new_meshes[0]
                r["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": mesh_pid}
                r["m_Bones"] = [bone(n) for n in nodes]
                count = len(new_materials[0])
                if main_renderer:
                    r["m_Materials"] = [_material(material_fid, m) for m in new_materials[0]]
                    main = pid
                else:  # a copy (an effect's): its own materials, one per submesh
                    kept = r["m_Materials"] or [{"m_FileID": 0, "m_PathID": 0}]
                    r["m_Materials"] = (kept * count)[:count]
                r["m_RootBone"] = bone(root_node)
                r["m_AABB"] = copy.deepcopy(bounds[0])
                seen.append(Rendered(0, len(r["m_Bones"]), [m["m_PathID"] for m in r["m_Materials"]], r["m_AABB"]))
            else:  # the target's other costume parts, alternates and their copies draw nothing
                r["m_Mesh"] = {"m_FileID": 0, "m_PathID": 0}
            save(pid)
        halo_added = _edit_halo(halo, by_path, objects, tree, save, mesh_fid, material_fid, sf, writer) if halo else []
        if main is None:
            if added or halo_added:
                additions[entry] = list(added) + halo_added
            continue
        # further source parts: renderers of their own beside the primary
        drawing = {0: main}  # source part -> the renderer drawing it in this prefab
        template = tree(main)
        go_pid = template["m_GameObject"]["m_PathID"]
        go_tree = tree(go_pid)
        tf_pid = next(
            c["component"]["m_PathID"]
            for c in go_tree["m_Component"]
            if objects[c["component"]["m_PathID"]].type.name == "Transform"
        )
        for k in range(1, len(new_meshes)):
            mesh_pid, nodes = new_meshes[k]
            new_go, new_tf, new_r = writer.fresh(sf), None, None
            new_tf = writer.fresh(sf)
            while new_tf == new_go:
                new_tf = writer.fresh(sf)
            r = {**template}
            r["m_GameObject"] = {"m_FileID": 0, "m_PathID": new_go}
            r["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": mesh_pid}
            r["m_Materials"] = [_material(material_fid, m) for m in new_materials[k]]
            r["m_Bones"] = [bone(n) for n in nodes]
            r["m_RootBone"] = bone(root_node)
            r["m_AABB"] = copy.deepcopy(bounds[k])
            seen.append(Rendered(k, len(r["m_Bones"]), [m["m_PathID"] for m in r["m_Materials"]], r["m_AABB"]))
            new_r = _add_with_id(writer, sf, "SkinnedMeshRenderer", r)
            drawing[k] = new_r
            g = {
                **go_tree,
                "m_Name": names[k],
                "m_IsActive": True,
                "m_Component": [
                    {"component": {"m_FileID": 0, "m_PathID": new_tf}},
                    {"component": {"m_FileID": 0, "m_PathID": new_r}},
                ],
            }
            _add_with_id(writer, sf, "GameObject", g, new_go)
            # below the primary, where it stands: a clip or timeline that hides the primary hides it too
            t = {
                **tree(tf_pid),
                "m_GameObject": {"m_FileID": 0, "m_PathID": new_go},
                "m_Children": [],
                "m_Father": {"m_FileID": 0, "m_PathID": tf_pid},
                "m_LocalPosition": _vector((0, 0, 0)),
                "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                "m_LocalScale": _vector((1, 1, 1)),
            }
            _add_with_id(writer, sf, "Transform", t, new_tf)
            tree(tf_pid)["m_Children"].append({"m_FileID": 0, "m_PathID": new_tf})
            save(tf_pid)
            added += [{"m_FileID": 0, "m_PathID": pid} for pid in (new_go, new_tf, new_r)]
            # lists of renderers that name the primary (effects, flashes) take the new part as well
            for b in behaviours:
                if _append_beside(tree(b), {"m_FileID": 0, "m_PathID": main}, {"m_FileID": 0, "m_PathID": new_r}):
                    save(b)
        if mouth is not None and mouth.part in drawing:
            _edit_mouth(behaviours, tree, save, costume_renderers, drawing[mouth.part], mouth)
        additions[entry] = (
            added
            + [{"m_FileID": mesh_fid, "m_PathID": m} for m, _ in new_meshes]
            + [{"m_FileID": material_fid, "m_PathID": m} for ms in new_materials for m in ms if m]
            + halo_added
        )
    _extend_preload(asset_bundle, additions)
    bundle_object.save_typetree(asset_bundle)


def _edit_mouth(
    behaviours: list[int],
    tree: Callable[[int], dict],
    save: Callable[[int], None],
    costume_renderers: set[int],
    renderer: int,
    mouth: Mouth,
) -> None:
    """Make the prefab's mouth receivers that point at a slot costume renderer draw the source's mouth.

    That is every receiver that points at one of the slot's costume renderers: on ``renderer`` (the primary, or the part
    added for the source's mouth), with the source's material index and ``mouth``'s default tile (the source's, or the
    slot's where the mouth takes the slot's atlas). The receiver indexes the renderer's replacement materials in the
    source's order.
    """
    for pid in behaviours:
        t = tree(pid)
        pointer = t.get("MouthRenderer")
        if (
            not all(name in t for name in MOUTH_FIELDS)
            or not isinstance(pointer, dict)
            or pointer.get("m_FileID") != 0
            or pointer.get("m_PathID") not in costume_renderers
        ):
            continue
        t["MouthRenderer"] = {"m_FileID": 0, "m_PathID": renderer}
        t["MouthMaterialIndex"] = type(t["MouthMaterialIndex"])(mouth.index)
        t["MouthDefaultUV"] = copy.deepcopy(mouth.uv)
        save(pid)


def _edit_halo(
    halo: HaloChange,
    by_path: Mapping[str, int],
    objects: Mapping[int, Any],
    tree: Callable[[int], dict],
    save: Callable[[int], None],
    mesh_fid: int,
    material_fid: int,
    sf: Any,
    writer: _Writer,
) -> list[dict]:
    """Change one prefab's halo (:class:`HaloChange`).

    That is its renderer that draws the slot's halo mesh, and the follower of its followed node; or, hiding it,
    everything drawn at the hidden paths, then the source's halo nodes added below the followed node (a transplant). The
    slot's nodes stay, so the clips that move or toggle them still do. Returns the added objects the prefab loads
    through its preload entries.
    """

    def components(path: str) -> list[int]:
        transform = by_path.get(path)
        if transform is None:
            return []
        go = tree(transform)["m_GameObject"]["m_PathID"]
        return [
            c["component"]["m_PathID"]
            for c in tree(go)["m_Component"]
            if c["component"]["m_FileID"] == 0 and c["component"]["m_PathID"] in objects
        ]

    if halo.new_mesh is None:  # hidden: nothing draws, even where a clip turns a renderer or its node on
        for path in halo.hide:
            for pid in components(path):
                kind = objects[pid].type.name
                if kind not in DRAWN:
                    continue
                t = tree(pid)
                if kind == "ParticleSystem":
                    initial = t.get("InitialModule")
                    if not isinstance(initial, dict) or "maxNumParticles" not in initial:
                        continue
                    initial["maxNumParticles"] = type(initial["maxNumParticles"])(0)
                    if "playOnAwake" in t:
                        t["playOnAwake"] = type(t["playOnAwake"])(0)
                else:
                    t["m_Enabled"] = type(t["m_Enabled"])(0)
                    if "m_Mesh" in t:  # a skinned renderer's own mesh
                        t["m_Mesh"] = {"m_FileID": 0, "m_PathID": 0}
                    if kind == "MeshRenderer":
                        for other in components(path):
                            if objects[other].type.name == "MeshFilter":
                                tree(other)["m_Mesh"] = {"m_FileID": 0, "m_PathID": 0}
                                save(other)
                save(pid)
        if halo.transplant is None or by_path.get(halo.root) is None:
            return []
        return _transplant(
            halo, by_path[halo.root], components(halo.root), objects, tree, save, mesh_fid, material_fid, sf, writer
        )
    here = components(halo.path)
    filt = next((pid for pid in here if objects[pid].type.name == "MeshFilter"), None)
    renderer = next((pid for pid in here if objects[pid].type.name == "MeshRenderer"), None)
    if filt is None or renderer is None:
        return []
    mesh = tree(filt)["m_Mesh"]
    if not mesh["m_PathID"] or (_cab(sf, mesh["m_FileID"]), mesh["m_PathID"]) != halo.mesh:
        return []
    r = tree(renderer)
    tree(filt)["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": halo.new_mesh}
    r["m_Materials"] = [{"m_FileID": material_fid, "m_PathID": m} for m in halo.materials]
    position, rotation, scale = halo.local
    transform = by_path[halo.path]
    tree(transform).update(
        m_LocalPosition=_vector(position),
        m_LocalScale=_vector(scale),
        m_LocalRotation=dict(zip("xyzw", (float(x) for x in rotation))),
    )
    for pid in (filt, renderer, transform):
        save(pid)
    for pid in components(halo.root):  # the FxFollower: the one behaviour there with its offsets
        if (
            halo.follower
            and objects[pid].type.name == "MonoBehaviour"
            and all(name in tree(pid) for name in halo.follower)
        ):
            tree(pid).update(copy.deepcopy(halo.follower))
            save(pid)
    return [{"m_FileID": mesh_fid, "m_PathID": halo.new_mesh}] + [
        {"m_FileID": material_fid, "m_PathID": m} for m in halo.materials
    ]


def _transplant(
    halo: HaloChange,
    root: int,
    root_components: list[int],
    objects: Mapping[int, Any],
    tree: Callable[[int], dict],
    save: Callable[[int], None],
    mesh_fid: int,
    material_fid: int,
    sf: Any,
    writer: _Writer,
) -> list[dict]:
    """Add the source's halo nodes (:class:`Transplant`) to one prefab.

    They go below its followed node ``root`` (a Transform), which keeps the source's follower offsets. Returns the new
    objects the prefab loads.
    """
    plan = halo.transplant
    ids: list[int] = []
    for _ in plan.objects:
        pid = writer.fresh(sf)
        while pid in ids:
            pid = writer.fresh(sf)
        ids.append(pid)
    files = {_IN: 0, _ROOT: 0, _MESH: mesh_fid, _MATERIAL: material_fid}
    for k, (source, template) in enumerate(plan.objects):
        new = copy.deepcopy(template)
        for pointer in _pointers(new):
            marker = pointer["m_FileID"]
            if marker == _IN:
                pointer.update(m_FileID=0, m_PathID=ids[pointer["m_PathID"]])
            elif marker == _ROOT:
                pointer.update(m_FileID=0, m_PathID=root)
            elif marker in files:
                pointer["m_FileID"] = files[marker]
        pid = writer.add_like(sf, source, new)
        if pid != ids[k]:
            obj = sf.objects.pop(pid)
            obj.path_id = ids[k]
            sf.objects[ids[k]] = obj
    ordered = sorted(sf.objects.items())
    sf.objects.clear()
    sf.objects.update(ordered)
    tree(root)["m_Children"].extend({"m_FileID": 0, "m_PathID": ids[k]} for k in plan.top)
    save(root)
    for pid in root_components:  # the FxFollower: the source's offsets
        if (
            halo.follower
            and objects[pid].type.name == "MonoBehaviour"
            and all(name in tree(pid) for name in halo.follower)
        ):
            tree(pid).update(copy.deepcopy(halo.follower))
            save(pid)
    return [{"m_FileID": 0, "m_PathID": pid} for pid in ids] + [
        {"m_FileID": files[marker], "m_PathID": pid} for marker, pid in plan.carried
    ]


def _material(file_id: int, path_id: int) -> dict:
    """A renderer's material pointer; a material the source leaves empty stays empty, on its own submesh."""
    return {"m_FileID": file_id if path_id else 0, "m_PathID": path_id}


def _cab(sf: Any, file_id: int) -> str:
    if file_id == 0:
        return sf.name.lower()
    return sf.externals[file_id - 1].path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _add_with_id(writer: _Writer, sf: Any, type_name: str, tree: dict, pid: int | None = None) -> int:
    new = writer.add(sf, type_name, tree)
    if pid is None or pid == new:
        return new
    obj = sf.objects.pop(new)
    obj.path_id = pid
    sf.objects[pid] = obj
    ordered = sorted(sf.objects.items())
    sf.objects.clear()
    sf.objects.update(ordered)
    return pid


def _append_beside(value: Any, existing: dict, new: dict) -> bool:
    changed = False
    if isinstance(value, dict):
        for item in value.values():
            changed |= _append_beside(item, existing, new)
    elif isinstance(value, list):
        if existing in value and all(isinstance(v, dict) and set(v) == {"m_FileID", "m_PathID"} for v in value):
            value.append(dict(new))
            return True
        for item in value:
            changed |= _append_beside(item, existing, new)
    return changed


def _bounds(mesh: Written, nodes: list[int], t_skeleton: Skeleton, root: int, poses: list[list[np.ndarray]]) -> dict:
    """A renderer's ``m_AABB``, in its root bone's space.

    The box around the part as the slot skins it in each of ``poses`` (the rest first), its vertices without weight
    where the rest puts them. A bone added after the poses were taken (Rule B's) goes with its nearest ancestor there.
    """
    rest = t_skeleton.world()
    skin = Skin(mesh.vertices, mesh.joints, mesh.weights, np.zeros((0, 2), int))
    weighted = mesh.weights.sum(axis=1) > 1e-9
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)

    def at(pose: list[np.ndarray], n: int) -> np.ndarray:
        a = n
        while a >= len(pose):
            a = t_skeleton.nodes[a].parent
        return pose[n] if a == n else pose[a] @ np.linalg.inv(rest[a]) @ rest[n]

    for k, pose in enumerate(poses):
        matrices = np.array([at(pose, n) for n in nodes]) @ mesh.bindposes
        world = deform(skin, matrices)
        points = world[weighted] if k else world
        if not k and (~weighted).any():  # without weight: where the rest draws it, by the first bone
            points = np.where(
                weighted[:, None], world, (np.c_[mesh.vertices, np.ones(len(mesh.vertices))] @ matrices[0].T)[:, :3]
            )
        if not len(points):
            continue
        local = (np.c_[points, np.ones(len(points))] @ np.linalg.inv(at(pose, root)).T)[:, :3]
        lo, hi = np.minimum(lo, local.min(axis=0)), np.maximum(hi, local.max(axis=0))
    if not np.isfinite(lo).all():
        lo = hi = np.zeros(3)
    return {"m_Center": _vector((lo + hi) / 2), "m_Extent": _vector((hi - lo) / 2)}


def _extend_preload(asset_bundle: dict, additions: Mapping[int, list[dict]]) -> None:
    """The preload ranges of the edited prefabs grown by their added objects (``additions``, by container entry).

    Contiguous ranges are rebuilt in place, a range two entries share taking both entries' objects; otherwise each
    edited entry gets a copy of its range at the end, so two entries that shared one never take each other's.
    """
    table = asset_bundle["m_PreloadTable"]
    containers = asset_bundle["m_Container"]
    ranges = sorted({(i["preloadIndex"], i["preloadSize"]) for _, i in containers if i["preloadSize"]})
    contiguous = (
        sum(n for _, n in ranges) == len(table)
        and all(ranges[k][0] + ranges[k][1] == ranges[k + 1][0] for k in range(len(ranges) - 1))
        and (not ranges or ranges[0][0] == 0)
    )
    if not contiguous:  # ranges that overlap or leave gaps: each edited entry gets a copy of its range at the end
        for entry, extra in sorted(additions.items()):
            info = containers[entry][1]
            i, n = info["preloadIndex"], info["preloadSize"]
            info["preloadIndex"], info["preloadSize"] = len(table), n + len(extra)
            table.extend(table[i : i + n] + extra)
        return
    grown: dict[tuple[int, int], list[dict]] = {}
    for entry, extra in sorted(additions.items()):
        info = containers[entry][1]
        merged = grown.setdefault(
            (info["preloadIndex"], info["preloadSize"]) if info["preloadSize"] else ("end", entry), []
        )
        merged += [pointer for pointer in extra if pointer not in merged]
    moved: dict[tuple, tuple[int, int]] = {}
    rebuilt: list[dict] = []
    for i, n in ranges:
        extra = grown.get((i, n), [])
        moved[(i, n)] = (len(rebuilt), n + len(extra))
        rebuilt.extend(table[i : i + n] + extra)
    for key, extra in grown.items():  # empty preload ranges need a separate appended range
        if key[0] == "end":
            moved[key] = (len(rebuilt), len(extra))
            rebuilt.extend(extra)
    asset_bundle["m_PreloadTable"] = rebuilt
    for entry, (_, info) in enumerate(containers):
        key = (info["preloadIndex"], info["preloadSize"]) if info["preloadSize"] else ("end", entry)
        if key in moved:
            info["preloadIndex"], info["preloadSize"] = moved[key]


def field_chibis(graph: BundleGraph, files: Iterable[Path]) -> dict[str, list[Path]]:
    """Index Field_Player_<dev>[_NonWeapon] prefabs by lower-case dev name and containing bundle.

    Args:
        graph: Graph loading each candidate bundle's container once.
        files: Field-chibi bundles whose /character/ prefab container paths are inspected.
    """
    out: dict[str, list[Path]] = {}
    for path in files:
        env = graph.add(path)
        for name in env.container.keys() if env is not None else ():
            stem = name.rsplit("/", 1)[-1]
            match = re.fullmatch(r"Field_Player_(.+?)(_NonWeapon)?\.prefab", stem, re.IGNORECASE)
            if match and "/character/" in name.lower():
                out.setdefault(match.group(1).lower(), [])
                if Path(path) not in out[match.group(1).lower()]:
                    out[match.group(1).lower()].append(Path(path))
    return out
