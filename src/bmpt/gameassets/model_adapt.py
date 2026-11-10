"""Put another variant's costume on a student's 3D model slot.

A student's model slot (the ``character-<dev>-_mxload-*`` prefabs and their own dependency bundles) gets the costume of
another model: its skinned costume meshes with bindposes rebound onto the slot's skeleton, its materials and its
textures, all added as new objects to the slot's own bundles; the slot's prefabs then draw them in place of their own
costume. Nothing existing changes in place: field, cutscene and other variants' bundles reference the slot's objects.

Everything is decided from data, never from names a character's artist chose, so new characters need no rules of their
own:

- renderer roles (:func:`roles`): shading of the materials, membership of the main ``Bip001`` rig, visibility at rest
  and in the idle clip;
- bone mapping (:func:`map_bones`): Biped bones (``Bip001 ...``, the names every clip and IK component uses) by name;
  every other bone by structure: the nearest bone of the slot's skeleton that hangs off the same Biped bone, in the chain
  its parent maps to, else its parent's; never a bone the slot's clips hide by scaling it to nothing. A Biped bone the
  slot lacks, or a bone outside the Biped, goes with the Biped bone that moves it alike under the source's own clips
  (:func:`carriers`: a Battle NPC's ForeTwist with the forearm, a bag's strap with the body). Per Biped bone, the group
  of secondary bones follows the matched bones only while it stretches no more than the slot's own costume under the
  slot's clips, else it stays rigid;
- the shape (:func:`plan`, BL-128): the costume as its vertex data stores it, as its prefab draws it at rest (by its own
  bindposes: some meshes lie a metre off their renderer), and refitted to the slot's joints (:func:`refit`), each
  judged under the slot's clips; the least stretch wins and its vertices, normals and tangents are written
  (``model_geometry``);
- checks: every reference resolved, the rest pose reproduced, the stretch within the slot's own range. A pair that fails
  raises ``source_unsupported``;
- the hem (:func:`hems`, rules in ``model_hems``): cloth around the legs that stays rigid gets new skin weights on the
  pelvis, thighs and calves, or (Rule B, ``model_skirts``) its skirt chains become new bones of the slot with rotation
  curves in the slot's clips, by the rule that keeps the legs inside best under the slot's own clips within the slot's
  own stretch range; rigid weights stay where no rule does better. Optimize Physics (``physics``) also judges the
  leg-anchor groups set to ``follow`` again: the rules are built as if they were rigid too and must beat the follow
  result by the same measure;
- the halo (:func:`halo_of`, BL-131), for another student's costume only (``adapt``'s ``halo``): the source's halo mesh,
  materials and textures are added like the costume's and the slot's halo renderer draws them where the source draws
  its own, its follower offset taken from the source; a source without a halo hides the slot's. Any other halo shape
  (several renderers, particles, a skinned halo) keeps the slot's halo. A costume of the same student keeps the slot's
  halo as it is.

The caller supplies official copies of the files (``adapt``'s ``index``) and saves the changed bundles.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import math
import random
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from ..errors import ServiceError
from . import model_geometry as mg
from . import model_hems as hm
from . import model_skirts as ms
from .errors import AssetNotFound
from .model import (BundleGraph, PREFAB_KINDS, _external_cab, _key, _prefabs, bind_tracks, decode_clip,
                    euler_orders, euler_to_quat, ex_only_renderers, model_root, read_mesh, shader_kind, UNITY_ORDER)

logger = logging.getLogger(__name__)

CHARACTER_KINDS = {"body", "face", "hair", "eyemouth", "eyebrow"}
# torso, limb and head bones of the Biped: a costume part skins to at least one of them
BIPED_CORE = re.compile(r"^Bip001 (Pelvis|Spine\d*|Neck|Head|[LR] (Thigh|Calf|UpperArm|Forearm|Clavicle))$")
# the Biped's body chain, hands and feet with their fingers and toes: the bones a costume bone may be carried by
# (never a weapon, prop or eye bone a vendor named Bip001 too)
BIPED_CHAIN = re.compile(r"^Bip001 (Pelvis|Spine\d*|Neck\d*|Head|[LR] (Clavicle|UpperArm|Forearm|Hand|Thigh|Calf|Foot|"
                         r"Finger\d+|Toe\d+))$")
BIPED_ROOT = "Bip001"
NEAR = 0.05           # a secondary bone maps onto a slot bone at most this far from it (prefab units, metres)
STRETCH_FLOOR = 1.5   # a group may always stretch this much
# how far a costume may stretch under the slot's clips past the slot's own costume (max(1.5 x own, own + 1)), with a
# tenth more allowed (owner, 2026-11-08: pairs up to 1.1 times the limit, such as Momoi <- Momoi (Battle) at 1.01)
STRETCH_TOLERANCE = 1.1


def stretch_limit(own: float) -> float:
    return STRETCH_TOLERANCE * max(1.5 * own, own + 1.0)


# the shapes a costume may be bound in (BL-128), in the order they are judged: its vertex data in its renderer's space
# (before BL-128), as its prefab draws it at rest, with its joints moved onto the slot's, refitted (moved and turned)
SHAPES = ("stored", "drawn", "moved", "refitted")
SHAPE_MARGIN = 0.02  # a later shape is taken when it stretches this share less
CARRIER_SAMPLES = 200  # vertices measured, about, per unmatched bone
CARRIER_SHARE = 0.5    # a carrier moves an unmatched bone's vertices at most this much as far off as its parent does
HIDDEN = 1e-6  # a bone a pose scales to this share of its rest volume or less is hidden

# clips whose poses judge the mapping, by name suffix; the idle comes first
PROBE_CLIPS = ("Formation_Idle", "Normal_Idle", "Move_Ing", "Vital_Death", "Formation_Pickup", "Cafe_Walk")
IDLE_CLIPS = ("Formation_Idle", "Normal_Idle", "Cafe_Idle", "Stand_Idle", "Idle")
FIELD_KINDS = ("Field_Player_{}", "Field_Player_{}_NonWeapon")
PROBE_SCAN = 24  # clips looked at, at most, for the probes


def refuse(reason: str, **params: Any) -> ServiceError:
    """A pair the rule cannot carry: ``reason`` names the failed check, ``params`` its detail (in the log and in the
    error, for the costume verdicts and the diagnostics)."""
    logger.info("Costume adaptation refused: %s %s", reason, params)
    return ServiceError("source_unsupported", source=reason, **{key: str(value) for key, value in params.items()})


def _shader_name(shader: Any) -> str:
    try:
        return shader.read_typetree()["m_ParsedForm"]["m_Name"] or shader.peek_name()
    except Exception:  # the name is detail for the diagnostics only
        return shader.peek_name() or ""


def loaded_with(graph: BundleGraph, prefab_files: Iterable[Path], slot: Mapping[str, str]) -> set[str]:
    """The CABs the game loads with the target's prefab: its own files', and every CAB a loaded file references,
    followed through the slot's files and whatever else is open (a bundle's catalog dependencies come from these
    references). The slot's files are opened for it."""
    for path in sorted(set(slot.values())):
        graph.add(path)
    files: dict[str, Any] = {}
    for obj in graph.objects.values():
        files.setdefault(obj.assets_file.name.lower(), obj.assets_file)
    prefabs = {Path(p) for p in prefab_files}
    seen: set[str] = set()
    todo = [cab for cab, path in slot.items() if Path(path) in prefabs]
    while todo:
        cab = todo.pop()
        if cab in seen:
            continue
        seen.add(cab)
        sf = files.get(cab)
        for entry in (getattr(sf, "externals", None) or []):
            todo.append(entry.path.replace("\\", "/").rsplit("/", 1)[-1].lower())
    return seen


# --- skeleton ------------------------------------------------------------------------------------------------------

def trs(p: Iterable[float], r: Iterable[float], s: Iterable[float]) -> np.ndarray:
    x, y, z, w = r
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = rot * np.array(list(s))
    m[:3, 3] = list(p)
    return m


@dataclass
class Node:
    name: str
    path: str  # below the root ("" for the root), as clips bind it
    parent: int | None
    local: tuple
    go: Any
    transform: Any
    active: bool  # with every ancestor


@dataclass
class Skeleton:
    """A prefab's hierarchy in Unity space, relative to its root."""

    graph: BundleGraph
    nodes: list[Node] = field(default_factory=list)
    by_transform: dict[tuple[str, int], int] = field(default_factory=dict)
    renderers: list[tuple[int, Any]] = field(default_factory=list)  # (node, SkinnedMeshRenderer)
    behaviours: list[Any] = field(default_factory=list)
    _rest: list[np.ndarray] = field(default_factory=list, repr=False)  # local matrices at rest

    @classmethod
    def of(cls, graph: BundleGraph, root_go: Any) -> "Skeleton":
        skeleton = cls(graph)
        skeleton._visit(root_go, None, "", True)
        return skeleton

    def _visit(self, go_obj: Any, parent: int | None, path: str, active: bool) -> None:
        g = self.graph
        go = g.tree(go_obj)
        transform, components = None, []
        for c in go["m_Component"]:
            obj = g.deref(go_obj, c["component"])
            if obj is None:
                continue
            if obj.type.name in ("Transform", "RectTransform"):
                transform = obj
            else:
                components.append(obj)
        if transform is None:
            return
        t = g.tree(transform)
        p, r, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
        here = active and bool(go["m_IsActive"])
        index = len(self.nodes)
        self.nodes.append(Node(go["m_Name"], path, parent, ((p["x"], p["y"], p["z"]), (r["x"], r["y"], r["z"], r["w"]),
                                                             (s["x"], s["y"], s["z"])), go_obj, transform, here))
        self.by_transform[_key(transform)] = index
        for obj in components:
            if obj.type.name == "SkinnedMeshRenderer":
                self.renderers.append((index, obj))
            elif obj.type.name == "MonoBehaviour":
                self.behaviours.append(obj)
        for child in t["m_Children"]:
            ct = g.deref(transform, child)
            cgo = g.deref(ct, g.tree(ct)["m_GameObject"]) if ct is not None else None
            if cgo is not None:
                name = g.tree(cgo)["m_Name"]
                self._visit(cgo, index, f"{path}/{name}" if path else name, here)

    def world(self, local: Mapping[int, np.ndarray] | None = None) -> list[np.ndarray]:
        if len(self._rest) != len(self.nodes):
            self._rest = [trs(*node.local) for node in self.nodes]
        out: list[np.ndarray] = []
        for i, node in enumerate(self.nodes):  # parents come first
            m = local[i] if local is not None and i in local else self._rest[i]
            out.append(out[node.parent] @ m if node.parent is not None else m.copy())
        return out

    def first(self, name: str) -> int | None:
        return next((i for i, node in enumerate(self.nodes) if node.name == name), None)

    def chain_ancestor(self, i: int) -> int | None:
        """The nearest ancestor on the Biped's body chain (``BIPED_CHAIN``), or None."""
        p = self.nodes[i].parent
        while p is not None and not BIPED_CHAIN.match(self.nodes[p].name):
            p = self.nodes[p].parent
        return p

    def biped_ancestor(self, i: int) -> int | None:
        p = self.nodes[i].parent
        while p is not None and not self.nodes[p].name.startswith("Bip001"):
            p = self.nodes[p].parent
        return p

    def descendants(self, i: int) -> list[int]:
        prefix = self.nodes[i].path + "/"
        return [j for j, node in enumerate(self.nodes) if node.path.startswith(prefix) or (i == 0 and j > 0)]

    def poses(self, clips: list[Any], frames: int = 3) -> list[list[np.ndarray]]:
        """World matrices of the first, middle and last frames of each clip (rest where a clip has no curve)."""
        return [pose for clip in clips for pose in self.clip_poses(clip, None if frames > 1 else 1)[0]]

    def clip_poses(self, clip: Any, frames: int | None = None) -> tuple[list[list[np.ndarray]], set[int]]:
        """World matrices of a clip's first, middle and last frames (``frames`` None) or of ``frames`` evenly spaced
        ones, and the nodes the clip animates."""
        times, pose, animated = self.clip_frames(clip.read_typetree())
        count = len(times)
        if frames is None:
            chosen = sorted({0, count // 2, count - 1})
        else:
            chosen = sorted({min(count - 1, round(k * (count - 1) / max(1, frames - 1))) for k in range(frames)})
        return [pose(frame) for frame in chosen], animated

    def clip_frames(self, tree: Mapping[str, Any]) -> tuple[list[float], Callable[[int], list[np.ndarray]], set[int]]:
        """A clip's frame times (its sample grid), the world matrices of a frame by index, and the nodes it animates."""
        by_hash = {zlib.crc32(node.path.encode()): i for i, node in enumerate(self.nodes)}
        decoded = decode_clip(tree)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        tracks = bind_tracks(bindings, decoded.curves, by_hash)
        orders = euler_orders(bindings, by_hash)

        def pose(frame: int) -> list[np.ndarray]:
            local = {}
            for i, attrs in tracks.items():
                p, r, s = (list(v) for v in self.nodes[i].local)
                for attribute, parts in attrs.items():
                    value = [part[min(frame, len(part) - 1)] for part in parts]
                    if attribute == 1:
                        p = value
                    elif attribute == 2:
                        r = value
                    elif attribute == 3:
                        s = value
                    elif attribute == 4:
                        r = list(euler_to_quat(value, orders.get(i, UNITY_ORDER)))
                local[i] = trs(p, r, s)
            return self.world(local)

        return decoded.times, pose, set(tracks)


# --- renderer roles ------------------------------------------------------------------------------------------------

@dataclass
class Part:
    node: int
    renderer: Any
    mesh: Any
    materials: list[Any]
    kinds: list[str]
    bones: list[int | None]
    indices: int
    subparts: list[tuple[str, int]] = field(default_factory=list)  # (shading kind, index count) per submesh
    role: str = "prop"  # primary, costume, alternate, copy, weapon, halo, prop


def _shader_kind(graph: BundleGraph, material: Any) -> str:
    if material is None:
        return "general"
    shader = graph.deref(material, graph.tree(material).get("m_Shader"))
    try:
        name = graph.tree(shader)["m_ParsedForm"]["m_Name"] if shader is not None else None
    except (KeyError, TypeError, ValueError):
        name = None
    return shader_kind(name)


def roles(skeleton: Skeleton, idle: list[np.ndarray] | None) -> list[Part]:
    """The skinned renderers of a prefab with their roles (see the module's description). ``idle`` is the world pose of
    the idle clip's first frame: a part every bone of which it scales to nothing is a prop."""
    g = skeleton.graph
    main = skeleton.first("Bip001")
    main_path = skeleton.nodes[main].path if main is not None else None
    parts: list[Part] = []
    for node, renderer in skeleton.renderers:
        tree = g.tree(renderer)
        mesh = g.deref(renderer, tree["m_Mesh"])
        materials = [g.deref(renderer, m) for m in tree["m_Materials"]]
        subs = g.tree(mesh).get("m_SubMeshes", []) if mesh is not None else []
        kinds = [_shader_kind(g, m) for m in materials]
        bones = [skeleton.by_transform.get(_key(t)) if t is not None else None
                 for t in (g.deref(renderer, b) for b in tree["m_Bones"])]
        counts = [int(s.get("indexCount", 0)) for s in subs]
        parts.append(Part(node, renderer, mesh, materials, kinds, bones, sum(counts), list(zip(kinds, counts))))
    visible = [p for p in parts if p.mesh is not None and skeleton.nodes[p.node].active
               and g.tree(p.renderer)["m_Enabled"]]
    shown = {id(p) for p in visible}
    aside = ex_only_renderers((p.node, skeleton.nodes[p.node].path, p.subparts) for p in visible)
    for p in parts:
        kinds = set(p.kinds)
        bones = [b for b in p.bones if b is not None]
        in_main = sum(1 for b in bones if main_path is not None and (
            skeleton.nodes[b].path == main_path or skeleton.nodes[b].path.startswith(main_path + "/")))
        biped = any(BIPED_CORE.match(skeleton.nodes[b].name) for b in bones)
        away = bool(idle) and bool(bones) and all(abs(np.linalg.det(idle[b][:3, :3])) < 1e-9 for b in bones)
        if "weapon" in kinds:
            p.role = "weapon"
        elif "halo" in kinds:
            p.role = "halo"
        elif kinds & CHARACTER_KINDS and bones and in_main >= 0.5 * len(bones) and biped and p.mesh is not None:
            p.role = "prop" if away else "costume" if id(p) in shown and p.node not in aside else "alternate"
    costume = {_key(p.mesh) for p in parts if p.role == "costume"}
    seen: set = set()
    for p in parts:  # a second renderer drawing a costume mesh is a copy
        if p.mesh is not None and _key(p.mesh) in costume:
            if p.role == "costume" and _key(p.mesh) not in seen:
                seen.add(_key(p.mesh))
            else:
                p.role = "copy"
    candidates = [p for p in parts if p.role == "costume"]
    faces = [p for p in candidates if "face" in p.kinds]
    primary = max(faces or candidates, key=lambda p: p.indices, default=None)
    if primary is not None:
        primary.role = "primary"
    return parts


# --- clips ---------------------------------------------------------------------------------------------------------

def prefab_clips(graph: BundleGraph, skeleton: Skeleton, prefabs: Mapping[str, Any]) -> list[Any]:
    """The clips a prefab plays: those its FieldAnimancerController lists (field prefabs), else every clip of the
    animator controllers of ``prefabs`` (one model's prefabs)."""
    for behaviour in skeleton.behaviours:
        if graph.script(behaviour) == "FieldAnimancerController":
            return [c for c in (graph.deref(behaviour, p) for p in graph.tree(behaviour).get("animationClips", []))
                    if c is not None]
    clips, seen = [], set()
    for pointer in prefabs.values():
        animator = graph.component(pointer.deref(), "Animator")
        controller = graph.deref(animator, graph.tree(animator)["m_Controller"]) if animator is not None else None
        if controller is None:
            continue
        tree = graph.tree(controller)
        if controller.type.name == "AnimatorOverrideController":
            pointers = [c["m_OverrideClip"] if c["m_OverrideClip"].get("m_PathID") else c["m_OriginalClip"]
                        for c in tree["m_Clips"]]
        else:
            pointers = tree.get("m_AnimationClips", [])
        for pointer_ in pointers:
            clip = graph.deref(controller, pointer_)
            if clip is not None and _key(clip) not in seen:
                seen.add(_key(clip))
                clips.append(clip)
    return clips


def pick(clips: list[Any], suffixes: Iterable[str]) -> list[Any]:
    """The first clip named for each suffix: ``<model>_<suffix>``, or the suffix alone (a Battle NPC's)."""
    names = [(c.peek_name() or "").lower() for c in clips]
    out = []
    for suffix in suffixes:
        hit = next((c for c, n in zip(clips, names) if n.endswith("_" + suffix.lower())), None)
        if hit is None:
            hit = next((c for c, n in zip(clips, names) if n == suffix.lower()), None)
        if hit is not None and hit not in out:
            out.append(hit)
    return out


def probe_poses(skeleton: Skeleton, clips: list[Any], suffixes: Iterable[str] = PROBE_CLIPS) -> list[list[np.ndarray]]:
    """World poses (first, middle and last frames) of the clips that judge a costume on ``skeleton``: those named for
    ``suffixes`` that move its Biped; where none does, as many other clips that do (a Battle NPC's named states are
    empty, its own clips carry its name), of the first ``PROBE_SCAN``."""
    suffixes = tuple(suffixes)
    named = pick(clips, suffixes)
    out = _moving(skeleton, named, len(named))
    return out or _moving(skeleton, [c for c in clips if c not in named][:PROBE_SCAN], len(suffixes))


def _moving(skeleton: Skeleton, clips: list[Any], count: int) -> list[list[np.ndarray]]:
    """The poses of the first ``count`` of ``clips`` that move the Biped."""
    rest = skeleton.world()
    out: list[list[np.ndarray]] = []
    kept = 0
    for clip in clips:
        if kept >= count:
            break
        poses, animated = skeleton.clip_poses(clip)
        biped = [i for i in animated if skeleton.nodes[i].name.startswith("Bip001")]
        if not any(float(np.abs(pose[i] - rest[i]).max()) > 1e-5 for pose in poses for i in biped):
            continue
        out += poses
        kept += 1
    return out


# --- meshes --------------------------------------------------------------------------------------------------------

@dataclass
class Skin:
    vertices: np.ndarray  # in the renderer's mesh space
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray
    triangles: np.ndarray | None = None


def skin_of(mesh: Any) -> Skin:
    data = read_mesh(mesh.read())
    vertices = np.array(data.vertices, float).reshape(-1, 3)
    if not data.joints:
        raise refuse("unskinned costume part")
    tris = np.array([t for sub in data.triangles for t in sub], int).reshape(-1, 3)
    edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
    return Skin(vertices, np.array(data.joints, int), np.array(data.weights, float), edges, tris)


def bindposes(graph: BundleGraph, mesh: Any) -> np.ndarray:
    return np.array([[[b[f"e{r}{c}"] for c in range(4)] for r in range(4)] for b in graph.tree(mesh)["m_BindPose"]])


def deform(skin: Skin, matrices: np.ndarray) -> np.ndarray:
    homogeneous = np.c_[skin.vertices, np.ones(len(skin.vertices))]
    out = np.zeros((len(skin.vertices), 3))
    for k in range(skin.joints.shape[1]):
        out += skin.weights[:, k:k + 1] * np.einsum("nij,nj->ni", matrices[skin.joints[:, k]], homogeneous)[:, :3]
    return out


def stretch(rest: np.ndarray, posed: np.ndarray, edges: np.ndarray, mask: np.ndarray | None = None) -> float:
    """The 99.9th percentile of how much the edges grow or shrink (1 = unchanged; shrinking counts as its inverse)."""
    if mask is not None:
        edges = edges[mask]
    if not len(edges):
        return 1.0
    l0 = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    l1 = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)
    ok = l0 > 1e-6
    ratio = l1[ok] / l0[ok]
    ratio = np.maximum(ratio, 1 / np.maximum(ratio, 1e-6))
    return float(np.percentile(ratio, 99.9)) if len(ratio) else 1.0


# --- bone mapping --------------------------------------------------------------------------------------------------

@dataclass
class Costume:
    """Costume parts of one prefab, flattened for measuring: every part's skin in the prefab root's space."""

    skeleton: Skeleton
    parts: list[Part]
    skins: list[Skin]

    def bones(self) -> list[int]:
        return sorted({b for p in self.parts for b in p.bones if b is not None})


def unmatched(target: Skeleton, source: Skeleton, flat: Flat) -> list[int]:
    """Among the costume's bones and their anchors: the source's Biped bones the slot has no bone of the name for,
    and the bones outside the Biped (no Biped ancestor: a bag's strap hung off the prefab's root)."""
    out = set()
    for n in set(flat.joints[flat.weights > 0].tolist()) - {-1}:
        anchor = source.biped_ancestor(n)
        if not source.nodes[n].name.startswith("Bip001") and anchor is None and source.nodes[n].parent is not None:
            out.add(n)
        for b in (n, anchor):
            if b is not None and source.nodes[b].name.startswith("Bip001") and target.first(source.nodes[b].name) is None:
                out.add(b)
    return sorted(out)


def carriers(target: Skeleton, source: Skeleton, flat: Flat, poses: list[list[np.ndarray]]) -> dict[int, int]:
    """For each unmatched source bone (``unmatched``), the source bone the slot has by name that moves its vertices
    most alike under the source's own clips (``poses``): a Biped's ForeTwist hangs off the UpperArm but turns with the
    forearm, and where the slot has no such bone the costume's sleeve goes with the forearm, not with the upper arm;
    a bag's strap keyed to the body goes with the body. Measured on the vertices it or its secondary bones dominate
    (else its own frame): the least mean distance between where the two carry them, which must be under
    ``CARRIER_SHARE`` of the distance from where its parent carries them (else it stays with its parent, as before).
    A bone outside the Biped is judged with its whole branch (``group``: Sena (Casual)'s bag under a prop node the slot
    lacks), against the nearest ancestor the slot has, where the mapping would leave it; the result is keyed by the
    branch's top node."""
    missing = unmatched(target, source, flat)
    if not missing or not poses:
        return {}
    rest = source.world()
    present = [i for i, node in enumerate(source.nodes) if BIPED_CHAIN.match(node.name)
               and target.first(node.name) is not None]
    if not present:
        return {}
    dominant = flat.joints[np.arange(len(flat.vertices)), np.argmax(flat.weights, axis=1)]

    def group(n: int) -> int:
        """A Biped bone stands for itself; a bone outside the Biped for its branch: the node right under the nearest
        ancestor the slot has by name (a prop's whole chain, never torn across the body)."""
        if source.nodes[n].name.startswith("Bip001"):
            return n
        kept = _kept_ancestor(target, source, n)
        while source.nodes[n].parent is not None and source.nodes[n].parent != kept \
                and source.nodes[source.nodes[n].parent].parent is not None:
            n = source.nodes[n].parent
        return n

    groups = sorted({group(m) for m in missing})
    anchor = {n: (group(n) if n in missing else n if source.nodes[n].name.startswith("Bip001")
                  else source.biped_ancestor(n)) for n in set(dominant.tolist()) - {-1}}
    # a Biped bone against its parent (a finger's last joint bends with the one before); a prop's branch against
    # where the mapping would leave it, the nearest ancestor the slot has
    parents = {m: source.nodes[m].parent if source.nodes[m].name.startswith("Bip001")
               else _kept_ancestor(target, source, m) for m in groups}
    inverse = {i: np.linalg.inv(rest[i]) for i in {*present, *groups, *(p for p in parents.values() if p is not None)}}
    out = {}
    for m in groups:
        rows = np.where(np.isin(dominant, [n for n, a in anchor.items() if a == m]))[0]
        if len(rows):
            rows = rows[::max(1, len(rows) // CARRIER_SAMPLES)]
            points, own = flat.vertices[rows], dominant[rows]
        else:  # its frame: the joint and a point along each axis
            points = rest[m][:3, 3] + np.vstack([np.zeros(3), 0.05 * rest[m][:3, :3].T])
            own = np.full(len(points), m)
        homogeneous = np.c_[points, np.ones(len(points))]
        bones = sorted(set(own.tolist()))
        column = np.searchsorted(bones, own)
        inverse.update({b: np.linalg.inv(rest[b]) for b in bones if b not in inverse})
        errors = np.zeros(len(present))
        stay = 0.0  # with its parent
        parent = parents[m]
        for pose in poses:
            # where each vertex's own bone takes it
            moves = np.array([pose[b] @ inverse[b] for b in bones])[column]
            here = np.einsum("nij,nj->ni", moves, homogeneous)
            carried_by_parent = homogeneous @ (pose[parent] @ inverse[parent]).T if parent is not None else homogeneous
            stay += float(np.linalg.norm(carried_by_parent - here, axis=1).mean())
            stack = np.array([pose[c] @ inverse[c] for c in present])
            there = np.einsum("cij,nj->cni", stack, homogeneous)
            errors += np.linalg.norm(there - here[None], axis=2).mean(axis=1)
        best = int(np.argmin(errors))
        if stay > 1e-4 * len(poses) and errors[best] < CARRIER_SHARE * stay:
            out[m] = present[best]
    if out:
        logger.debug("Costume bones the slot lacks, carried by: %s",
                     {source.nodes[m].name: source.nodes[c].name for m, c in out.items()})
    return out


def _kept_ancestor(target: Skeleton, source: Skeleton, node: int) -> int | None:
    """The nearest ancestor of ``node`` the slot has a bone of the name for: where the mapping leaves an unmatched
    bone that has no carrier (a bag hung off a prop node the slot lacks stays with the prefab's root, None)."""
    p = source.nodes[node].parent
    while p is not None and source.nodes[p].parent is not None and target.first(source.nodes[p].name) is None:
        p = source.nodes[p].parent
    return p if p is not None and source.nodes[p].parent is not None else None


def hidden_bones(skeleton: Skeleton, poses: list[list[np.ndarray]]) -> set[int]:
    """The bones a pose scales to (almost) nothing, at rest or in ``poses``: the parts a clip hides (Sena's prop in
    her Formation_Pickup). A costume's vertices never follow them."""
    if not skeleton.nodes:
        return set()
    rest = np.array(skeleton.world())
    base = np.abs(np.linalg.det(rest[:, :3, :3]))
    out = set(np.where(base < 1e-9)[0].tolist())
    for pose in poses:
        scale = np.abs(np.linalg.det(np.array(pose)[:, :3, :3]))
        out |= set(np.where(scale < HIDDEN * np.maximum(base, 1e-9))[0].tolist())
    return out


def map_bones(target: Skeleton, source: Skeleton, bones: Iterable[int],
              carried: Mapping[int, int] | None = None, hidden: set[int] | None = None) -> dict[int, int]:
    """Source node -> target node for every source bone and its ancestors, before the per-group choice. An unmatched
    bone the slot has no bone near goes to the slot's bone of its carrier's name (``carriers``); no secondary bone goes
    to one the slot's clips hide (``hidden``)."""
    rest_t, rest_s = target.world(), source.world()
    carried = carried or {}
    hidden = hidden or set()
    mapping: dict[int, int] = {}
    root = 0
    wanted = set()
    for b in bones:
        while b is not None and b not in wanted:
            wanted.add(b)
            b = source.nodes[b].parent
    for i, node in enumerate(source.nodes):
        if i not in wanted:
            continue
        if node.parent is None:
            mapping[i] = root
            continue
        if node.name.startswith("Bip001"):
            same = target.first(node.name)
            if same is not None:
                mapping[i] = same
                continue
        parent = mapping.get(node.parent, root)
        anchor = source.biped_ancestor(i)
        if anchor is None:  # a bone outside the Biped matches near its branch's carrier's bones only
            b: int | None = i
            while b is not None and b not in carried:
                b = source.nodes[b].parent
            anchor = b
        anchor = carried.get(anchor, anchor) if anchor is not None else None
        anchor_name = source.nodes[anchor].name if anchor is not None else None
        position = rest_s[i][:3, 3]
        best, distance = None, NEAR
        for c in [parent, *target.descendants(parent)]:
            if target.nodes[c].name.startswith("Bip001") or c in hidden:
                continue
            a = target.biped_ancestor(c)
            if (target.nodes[a].name if a is not None else None) != anchor_name:
                continue
            d = float(np.linalg.norm(rest_t[c][:3, 3] - position))
            if d < distance:
                best, distance = c, d
        if best is None and i in carried and (same := target.first(source.nodes[carried[i]].name)) is not None:
            best = same  # an unmatched bone the slot has nothing near: its carrier's
        mapping[i] = best if best is not None else parent
    return mapping


@dataclass
class Fit:
    """A shape the costume may be bound in (BL-128), in the prefab root's space (``SHAPES``): per vertex its position
    and the blended linear part from its part's mesh space, which turns its normal and tangent; for a refit, how far
    it moves the vertices past the costume's common move (99th percentile and largest) and the costume's size."""

    vertices: np.ndarray
    linear: np.ndarray
    shift: float
    largest: float
    size: float
    starts: list[int] = field(default_factory=list)  # each part's first vertex
    shape: str = "stored"

    def part(self, k: int, world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Part ``k``'s vertices and linear parts in the mesh space ``world`` (prefab root's space) defines."""
        end = self.starts[k + 1] if k + 1 < len(self.starts) else len(self.vertices)
        inverse = np.linalg.inv(world)
        points = (np.c_[self.vertices[self.starts[k]:end], np.ones(end - self.starts[k])] @ inverse.T)[:, :3]
        return points, inverse[:3, :3] @ self.linear[self.starts[k]:end]


@dataclass
class Plan:
    mapping: dict[int, int]
    choices: dict[str, str]  # Biped bone -> "follow" | "rigid"
    stretch: float
    own: float
    groups: dict[int, list[int]] = field(default_factory=dict)  # source Biped node -> its secondary bones
    unfit: bool = False  # past the stretch limit: built for a preview only (``strict=False``), never applied
    fit: Fit | None = None  # the shape the costume is bound and judged in (``Fit``)


@dataclass
class Flat:
    """Every costume part's vertices where the prefab draws them at rest, in its root's space, with their bones as
    skeleton node indices (-1 for a bone outside the prefab), edges and triangles; ``starts`` holds each part's first
    vertex, ``linear`` each vertex's blended linear part (its part's mesh space to the root's)."""

    vertices: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray
    triangles: np.ndarray
    starts: list[int]
    linear: np.ndarray | None = None


def drawn(skin: Skin, carry: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    """Per vertex, the blend of the 4 x 4 ``carry`` matrices of its bones by its weights; ``fallback`` where it has
    no weight."""
    total = skin.weights.sum(axis=1, keepdims=True)
    blended = np.einsum("nk,nkij->nij", skin.weights / np.maximum(total, 1e-12), carry[skin.joints])
    blended[total[:, 0] <= 1e-12] = fallback
    return blended


def flatten(costume: Costume, rest: list[np.ndarray]) -> Flat:
    """The costume as the prefab draws it at rest: each part skinned by its own bindposes. A mesh's vertex data need
    not sit in its renderer's space (some lie a metre or more off, their bindposes bringing them back), and a mesh
    bound in another pose than the prefab's rest is drawn in that rest."""
    vertices, linear, joints, weights, edges, triangles, starts, offset = [], [], [], [], [], [], [], 0
    skeleton = costume.skeleton
    graph = skeleton.graph
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        bp = bindposes(graph, part.mesh)
        carry = [rest[b] @ bp[j] if b is not None and j < len(bp) else node for j, b in enumerate(part.bones)]
        # a face's eye, brow and mouth bones may rest off their bind (a default expression the clips replace): the
        # face is drawn as bound, rigidly with the head, so no expression of the source's rest goes along (BL-128:
        # Midori (Battle)'s brows rested 1.4 cm up, through Midori's bangs)
        head = {b: j for j, b in enumerate(part.bones) if b is not None and skeleton.nodes[b].name == "Bip001 Head"}
        for j, b in enumerate(part.bones):
            if b is None or j >= len(bp) or not face_bone(skeleton, b):
                continue
            h = skeleton.chain_ancestor(b)
            if h in head and head[h] < len(bp):
                carry[j] = rest[h] @ bp[head[h]]
        carry = np.array(carry or [node])
        blended = drawn(Skin(skin.vertices, np.minimum(skin.joints, len(carry) - 1), skin.weights, skin.edges),
                        carry, node)
        vertices.append(np.einsum("nij,nj->ni", blended, np.c_[skin.vertices, np.ones(len(skin.vertices))])[:, :3])
        linear.append(blended[:, :3, :3])
        joints.append(np.array([[part.bones[j] if part.bones[j] is not None else -1 for j in row]
                                for row in skin.joints]).reshape(-1, skin.joints.shape[1]))
        weights.append(skin.weights)
        edges.append(skin.edges + offset)
        tris = skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)
        triangles.append(tris + offset)
        starts.append(offset)
        offset += len(skin.vertices)
    return Flat(np.vstack(vertices), np.vstack(joints), np.vstack(weights), np.vstack(edges), np.vstack(triangles),
                starts, np.concatenate(linear))


@dataclass
class OwnMesh:
    """A prefab's costume drawn by its own bindposes: rest vertices, edges, each vertex's dominant node."""

    rest: np.ndarray
    edges: np.ndarray
    dominant: np.ndarray
    parts: list[tuple[Skin, np.ndarray, list[int]]]

    @classmethod
    def of(cls, costume: Costume) -> "OwnMesh":
        rest_world = costume.skeleton.world()
        vertices, edges, dominant, parts, offset = [], [], [], [], 0
        for part, skin in zip(costume.parts, costume.skins):
            bp = bindposes(costume.skeleton.graph, part.mesh)
            bones = [b if b is not None else 0 for b in part.bones]
            vertices.append(deform(skin, np.array([rest_world[b] for b in bones]) @ bp))
            edges.append(skin.edges + offset)
            dominant.append(np.array([bones[row[np.argmax(w)]] for row, w in zip(skin.joints, skin.weights)]))
            parts.append((skin, bp, bones))
            offset += len(skin.vertices)
        return cls(np.vstack(vertices), np.vstack(edges), np.concatenate(dominant), parts)

    def posed(self, pose: list[np.ndarray]) -> np.ndarray:
        return np.vstack([deform(skin, np.array([pose[b] for b in bones]) @ bp) for skin, bp, bones in self.parts])


def face_bone(skeleton: Skeleton, node: int) -> bool:
    """An eye, brow or mouth bone: named Bip001 like the Biped, outside its body chain and twist bones, under the
    head."""
    name = skeleton.nodes[node].name
    if not name.startswith("Bip001") or BIPED_CHAIN.match(name) or "Twist" in name:
        return False
    h = skeleton.chain_ancestor(node)
    return h is not None and skeleton.nodes[h].name == "Bip001 Head"


def fit_anchor(target: Skeleton, source: Skeleton, node: int,
               carried: Mapping[int, int] | None = None) -> tuple[int, int] | None:
    """The Biped bone a source node is refitted by, with the slot's bone of its name: the node itself or its nearest
    ancestor the slot has by name (a secondary bone's Biped anchor), an unmatched bone standing for its carrier
    (``carriers``). None when there is none. Only the body chain, hands, feet and their twist bones anchor
    (``BIPED_CHAIN``), and the Biped's root (a skirt hung off Bip001, Sena (Casual)'s, moves with the body, not with
    the prefab's root that a slot resting aside leaves behind): a face's eye, brow and mouth bones (named Bip001 too) go rigidly with the head, as two models'
    face rigs differ (BL-128: Midori (Battle)'s lower eyelids sat 3.5 cm off Midori's and pulled the eye out)."""
    carried = carried or {}
    n: int | None = node
    while n is not None:
        name = source.nodes[n].name
        if (BIPED_CHAIN.match(name) or name == BIPED_ROOT or (name.startswith("Bip001") and "Twist" in name)) \
                and (same := target.first(name)) is not None:
            return n, same
        if n in carried and (same := target.first(source.nodes[carried[n]].name)) is not None:
            return carried[n], same
        n = source.nodes[n].parent
    return None


def stored(costume: Costume, rest: list[np.ndarray], flat: Flat) -> Fit:
    """The shape the adapter bound costumes in before BL-128: each part's vertex data in its renderer's space."""
    vertices, linear = [], []
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        vertices.append((np.c_[skin.vertices, np.ones(len(skin.vertices))] @ node.T)[:, :3])
        linear.append(np.repeat(node[None, :3, :3], len(skin.vertices), axis=0))
    return Fit(np.vstack(vertices), np.concatenate(linear), 0.0, 0.0, 0.0, list(flat.starts), "stored")


def refit(target: Skeleton, source: Skeleton, flat: Flat, carried: Mapping[int, int] | None = None,
          shape: str = "refitted") -> Fit:
    """The costume carried from the source's rest skeleton to the slot's (BL-128): each vertex follows its bones by
    their anchors' transforms (``fit_anchor``), so the Biped's proportions change and hair, skirts and props move
    rigidly with their anchor; the skin weights stay. ``shape`` "moved": each anchor's joint move alone, "refitted":
    its move and turn. ``shift`` and ``largest``: how far the vertices move past the costume's common move (a slot
    whose prefab rests a metre aside, or lying, takes the costume along as a whole), for the log; what the refit does
    to the costume's edges counts in its stretch (``plan``)."""
    t_rest, s_rest = target.world(), source.world()
    used = sorted(set(flat.joints[flat.weights > 0].tolist()) - {-1})
    matrices = [np.eye(4)]  # column 0: a bone outside the prefab or without weight
    columns = {}
    for n in used:
        hit = fit_anchor(target, source, n, carried)
        if hit is None:
            columns[n] = 0
            continue
        a, t = hit
        m = t_rest[t] @ np.linalg.inv(s_rest[a])
        if shape == "moved":  # the joint's move alone, no turn
            m = np.eye(4)
            m[:3, 3] = t_rest[t][:3, 3] - s_rest[a][:3, 3]
        columns[n] = len(matrices)
        matrices.append(m)
    stack = np.array(matrices)
    local = np.vectorize(lambda n: columns.get(int(n), 0), otypes=[int])(np.where(flat.weights > 0, flat.joints, -1))
    blended = drawn(Skin(flat.vertices, local, flat.weights, flat.edges), stack, np.eye(4))
    vertices = np.einsum("nij,nj->ni", blended, np.c_[flat.vertices, np.ones(len(flat.vertices))])[:, :3]
    delta = vertices - flat.vertices
    changed = np.linalg.norm(delta - np.median(delta, axis=0), axis=1) if len(delta) else np.zeros(0)
    size = float(np.ptp(flat.vertices, axis=0).max()) if len(flat.vertices) else 0.0
    linear = blended[:, :3, :3] @ flat.linear if flat.linear is not None else blended[:, :3, :3]
    return Fit(vertices, linear, float(np.percentile(changed, 99)) if len(changed) else 0.0,
               float(changed.max()) if len(changed) else 0.0, size, list(flat.starts), shape)


def plan(target: Costume, source: Costume, probes: list[list[np.ndarray]], *, strict: bool = True,
         fit: bool = True, own_probes: list[list[np.ndarray]] | None = None) -> Plan:
    """The bone mapping with each group's choice, the shape the costume is bound in, and the checks of the rest pose
    and the stretch. The mapping (``map_bones``) takes the carriers of the unmatched bones from the source's own clips
    (``own_probes``, see ``carriers``) and leaves out the slot's hidden bones. With ``fit`` (the costume's geometry can
    be rewritten), each of ``SHAPES`` is judged under the slot's clips and the least stretch wins, a later shape only
    by more than ``SHAPE_MARGIN``; without, the costume keeps its stored shape."""
    s_rest = source.skeleton.world()
    # every part's vertices in the root's space, with its bones as source node indices
    flat_source = flatten(source, s_rest)
    sj, sw = flat_source.joints, flat_source.weights
    if (sj[sw > 0] < 0).any():
        raise refuse("costume bone outside the prefab")
    carried = carriers(target.skeleton, source.skeleton, flat_source, own_probes or [])
    mapping = map_bones(target.skeleton, source.skeleton, source.bones(), carried, hidden_bones(target.skeleton, probes))
    own_mesh = OwnMesh.of(target)
    posed_targets = [own_mesh.posed(p) for p in probes]
    own = max((stretch(own_mesh.rest, posed, own_mesh.edges) for posed in posed_targets), default=1.0)
    shapes = [stored(source, s_rest, flat_source)]
    if fit:
        shapes.append(Fit(flat_source.vertices, flat_source.linear, 0.0, 0.0, 0.0, list(flat_source.starts), "drawn"))
        shapes += [refit(target.skeleton, source.skeleton, flat_source, carried, shape) for shape in ("moved", "refitted")]
    best: Plan | None = None
    judged = []
    for shape in shapes:
        # a refit's own change of the costume counts: measured from the shape the source draws
        reference = shape.vertices if shape.shape == "stored" else flat_source.vertices
        candidate = _judge(target, source, probes, flat_source, shape, reference, dict(mapping), carried, own_mesh,
                           posed_targets, own)
        judged.append(f"{shape.shape} {candidate.stretch:.2f}")
        if best is None or candidate.stretch < best.stretch * (1 - SHAPE_MARGIN):
            best = candidate
    assert best is not None
    logger.debug("Costume shapes judged: %s; %s chosen", ", ".join(judged), best.fit.shape if best.fit else "?")
    if best.unfit and strict:
        raise refuse("stretch", adapted=round(best.stretch, 2), own=round(own, 2))
    return best


def _judge(target: Costume, source: Costume, probes: list[list[np.ndarray]], flat_source: Flat, shape: Fit,
           reference: np.ndarray, mapping: dict[int, int], carried: Mapping[int, int], own_mesh: OwnMesh,
           posed_targets: list[np.ndarray], own: float) -> Plan:
    """One shape's plan: each group's choice (``mapping`` is changed in place), the rest pose, the stretch from
    ``reference`` (the costume's shape before the refit)."""
    t_rest = target.skeleton.world()
    sv, sj, sw, se = shape.vertices, flat_source.joints, flat_source.weights, flat_source.edges
    tv, te, tdom = own_mesh.rest, own_mesh.edges, own_mesh.dominant
    sdom = np.array([row[np.argmax(w)] for row, w in zip(sj, sw)])
    nodes = sorted(set(sj[sw > 0].tolist()))
    index = {n: k for k, n in enumerate(nodes)}
    joints = np.vectorize(lambda n: index.get(n, 0))(np.where(sj < 0, nodes[0], sj))
    flat = Skin(sv, joints, sw, se)

    def matrices(pose: list[np.ndarray], m: Mapping[int, int]) -> np.ndarray:
        return np.array([pose[m[n]] @ np.linalg.inv(t_rest[m[n]]) for n in nodes])

    groups: dict[int, list[int]] = {}
    for n in nodes:
        if not source.skeleton.nodes[n].name.startswith("Bip001"):
            anchor = source.skeleton.biped_ancestor(n)
            if anchor is not None:
                groups.setdefault(anchor, []).append(n)
    choices = {}
    for anchor, members in groups.items():
        rigid = mapping[anchor]
        mask = np.isin(sdom, members)
        mask = mask[se[:, 0]] & mask[se[:, 1]]
        name = source.skeleton.nodes[anchor].name
        slot_name = source.skeleton.nodes[carried.get(anchor, anchor)].name  # a Biped bone the slot lacks: its carrier
        if not probes or not mask.any():
            follow = False
        else:
            t_members = [j for j in set(tdom.tolist()) if not target.skeleton.nodes[j].name.startswith("Bip001")
                         and target.skeleton.biped_ancestor(j) is not None
                         and target.skeleton.nodes[target.skeleton.biped_ancestor(j)].name == slot_name]
            t_mask = np.isin(tdom, t_members)
            t_mask = t_mask[te[:, 0]] & t_mask[te[:, 1]]
            group_own = max((stretch(tv, posed, te, t_mask) for posed in posed_targets), default=1.0) \
                if t_mask.any() else 1.0
            mine = max(stretch(reference, deform(flat, matrices(p, mapping)), se, mask) for p in probes)
            follow = mine <= max(STRETCH_FLOOR, group_own)
        choices[name] = "follow" if follow else "rigid"
        if not follow:
            for n in members:
                mapping[n] = rigid
    # the checks: the rest pose reproduced, the stretch within the target's own range
    rest = deform(flat, matrices(t_rest, mapping))
    if float(np.abs(rest - sv).max()) > 1e-4:
        raise refuse("rest pose not reproduced")
    mine = max((stretch(reference, deform(flat, matrices(p, mapping)), se) for p in [t_rest, *probes]), default=1.0)
    return Plan(mapping, choices, mine, own, groups, mine > stretch_limit(own), shape)


# --- skirts and hems (BL-118, see model_hems) ------------------------------------------------------------------------

HEM_CLIPS = ("Formation_Idle", "Formation_Pickup", "Normal_Attack_Ing", "Exs", "Move_Ing", "Cafe_Walk", "Vital_Death")
HEM_FRAMES = 6     # frames measured per check clip
FIT_FRAMES = 12    # frames per clip of the costume's own clips, for the fit
LEG_SAMPLES = 700  # leg vertices measured, about
HEM_RULES = (("blend", 0.5), ("short", 0.5), ("short", 0.7), ("short", 1.0), ("fit", 0.5), ("fit", 0.7), ("fit", 1.0))
RAMPS = {"blend": (1.0, (1.0, 2.0)), "short": (0.3, (0.9, 1.3))}  # rise in thigh lengths, thigh -> calf change
LEG_KEYS = ("pelvis", "thigh L", "thigh R", "calf L", "calf R", "foot L", "foot R")
FIT_KEYS = LEG_KEYS[:5]
REGROUP = "regroup "  # Optimize Physics: a rule built as if the leg-anchor groups set to ``follow`` were rigid


def legs_of(skeleton: Skeleton) -> dict[str, int] | None:
    """The Biped's pelvis, thighs, calves and feet, by the names every clip and IK component uses."""
    out = {}
    for key in LEG_KEYS:
        if key == "pelvis":
            name = "Bip001 Pelvis"
        else:
            bone, side = key.split()
            name = f"Bip001 {side} {bone.title()}"
        out[key] = skeleton.first(name)
    return out if None not in out.values() else None


def lower_anchors(skeleton: Skeleton, legs: Mapping[str, int]) -> set[int]:
    """The Biped bones whose secondary bones hang around the legs: the pelvis and every Biped bone of each leg."""
    out = {legs["pelvis"]}
    for side in "LR":
        thigh = legs[f"thigh {side}"]
        out |= {thigh} | {j for j in skeleton.descendants(thigh) if skeleton.nodes[j].name.startswith("Bip001")}
    return out


def followed_groups(plan_: Plan, skeleton: Skeleton, anchors: set[int]) -> dict[int, list[int]]:
    """The leg-anchor groups (``anchors``: :func:`lower_anchors`) that the group choice set to ``follow``, with their
    secondary bones: Optimize Physics judges them again by pass-through (:func:`hems`)."""
    return {anchor: members for anchor, members in plan_.groups.items() if members and anchor in anchors
            and plan_.choices.get(skeleton.nodes[anchor].name) == "follow"}


def chain_root(skeleton: Skeleton, node: int) -> int:
    """The first secondary bone of the chain ``node`` hangs in, right below its Biped bone."""
    while (parent := skeleton.nodes[node].parent) is not None and not skeleton.nodes[parent].name.startswith("Bip001"):
        node = parent
    return node


def cloth_mask(skeleton: Skeleton, dominant: np.ndarray, anchors: set[int], axis_distance: np.ndarray,
               hip_width: float) -> np.ndarray:
    """Vertices of the cloth around the legs: dominated by a secondary bone below one of ``anchors``, in a chain whose
    vertices lie within 1.5 hip widths of the leg axes (median), which keeps wings and tails out."""
    chains = np.full(len(dominant), -1)
    for node in np.unique(dominant).tolist():
        if node < 0 or skeleton.nodes[node].name.startswith("Bip001"):
            continue
        if skeleton.biped_ancestor(node) in anchors:
            chains[dominant == node] = chain_root(skeleton, node)
    out = np.zeros(len(dominant), bool)
    for chain in np.unique(chains[chains >= 0]).tolist():
        members = chains == chain
        if float(np.median(axis_distance[members])) < 1.5 * hip_width:
            out |= members
    return out


def fit_own(skeleton: Skeleton, flat: Flat, on_rigid: np.ndarray, share: np.ndarray, legs: Mapping[str, int],
            clips: list[Any]) -> np.ndarray | None:
    """Per vertex, the weights on the pelvis, thighs and calves (``FIT_KEYS``) that best reproduce where the costume's
    own clips put its rigid part (the vendor's keyed skirt bones): least squares over the sampled frames of every clip
    with Biped curves. None without such clips."""
    rows = np.where(share > 1e-6)[0]
    rest = skeleton.world()
    bones = [legs[k] for k in FIT_KEYS]
    weights = np.where(on_rigid[rows], flat.weights[rows], 0.0)
    joints = np.where(on_rigid[rows], flat.joints[rows], bones[0])
    nodes = sorted(set(joints.ravel().tolist()) | set(bones))
    index = {n: k for k, n in enumerate(nodes)}
    local = np.vectorize(index.get, otypes=[int])(joints)
    inverse = np.array([np.linalg.inv(rest[n]) for n in nodes])
    points = flat.vertices[rows]
    homogeneous = np.c_[points, np.ones(len(points))]
    normal = hm.Normal(len(rows), len(bones))
    for clip in clips:
        poses, animated = skeleton.clip_poses(clip, FIT_FRAMES)
        if not any(skeleton.nodes[i].name.startswith("Bip001") for i in animated):
            continue
        for pose in poses:
            matrices = np.array([pose[n] for n in nodes]) @ inverse
            target = hm.skin(points, local, weights, matrices) / share[rows, None]
            basis = np.stack([(homogeneous @ matrices[index[b]].T)[:, :3] for b in bones], axis=2)
            normal.add(basis, target)
    if not normal.frames:
        return None
    fitted, residual = hm.fit_simplex(normal)
    logger.debug("Hem fit: %d vertices over %d frames, rms residual %.1f mm", len(rows), normal.frames,
                 1000 * float(residual.mean()) if len(residual) else 0.0)
    out = np.zeros((len(flat.vertices), len(bones)))
    out[rows] = fitted
    return out


@dataclass
class Skirt:
    """Rule B's new bones (``model_skirts``): the source chain nodes they come from (parents first), their names and
    local transforms (position, rotation, scale) below their parent (an index into them, or -1 for the slot's
    ``anchor``), the solver that turns their roots, and ``base``, the slot node index the first of them takes."""

    sources: list[int]
    names: list[str]
    parents: list[int]
    locals: list[tuple]
    anchor: int
    solver: ms.Solver
    base: int
    clips: int = 0  # clips given curves
    solved: dict[tuple[str, int], tuple[list[int], list[dict[int, np.ndarray]]]] = field(default_factory=dict)

    def node(self, k: int) -> int:
        return self.base + k


@dataclass
class Hems:
    """What became of the hem: the rule chosen ("rigid" when nothing moves), why nothing was measured, the stretch
    limit, the scores (rigid first) and, per source part, the rewritten vertices (rows, slot nodes, weights); for
    Rule B (rule "skirt" or "skirt smooth") the new bones. With Optimize Physics a rule may carry the ``REGROUP``
    prefix: it was built as if the leg-anchor groups the group choice set to ``follow`` were rigid (``regrouped``),
    and the first score, the default build's, is the baseline it had to beat."""

    rule: str = "rigid"
    note: str = ""
    limit: float = 0.0
    scores: list[hm.Score] = field(default_factory=list)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = field(default_factory=list)
    skirt: Skirt | None = None
    regrouped: bool = False

    def score(self, rule: str) -> hm.Score | None:
        return next((s for s in self.scores if s.rule == rule), None)

    def summary(self) -> str:
        if not self.scores:
            return f"rigid ({self.note})"
        rigid, chosen = self.scores[0], self.score(self.rule) or self.scores[0]
        extra = f", {len(self.skirt.solver.roots)} chains in {self.skirt.clips} clips" if self.skirt else ""
        return (f"{self.rule}: deep pass-through {rigid.mean:.1f}% -> {chosen.mean:.1f}%, stretch {rigid.stretch:.2f} -> "
                f"{chosen.stretch:.2f} (limit {self.limit:.2f}){extra}; per clip " + ", ".join(
                    f"{clip} {rigid.deep[clip]:.0f}->{chosen.deep[clip]:.0f}" for clip in rigid.deep))


@dataclass
class _SkirtCandidate:
    """A Rule B candidate: dense weights over the measured columns and then the chains' bones, and its solver."""

    rule: str
    weights: np.ndarray
    solver: ms.Solver


def _skirt_candidates(ts: Skeleton, ss: Skeleton, plan_: Plan, flat: Flat, sv: np.ndarray, dominant: np.ndarray,
                      cloth: np.ndarray, legs: np.ndarray, t_legs: Mapping[str, int], s_legs: Mapping[str, int],
                      mapped: np.ndarray, columns: list[int],
                      writable_rows: np.ndarray) -> tuple[list[_SkirtCandidate], Skirt | None]:
    """Rule B's candidates ("skirt": the costume's weights, "skirt smooth": blended across the chains' seams) and
    its new bones, for the skirt chains hanging off the pelvis that the group choice left rigid. ``flat``: the
    costume in the source's space; ``sv``: its vertices in the shape bound on the slot (BL-128), where each chain's
    bones go along with its cloth."""
    pelvis = s_legs["pelvis"]
    if plan_.choices.get(ss.nodes[pelvis].name) != "rigid":
        return [], None
    members = set(plan_.groups.get(pelvis, []))
    roots = {chain_root(ss, int(n)) for n in np.unique(dominant[cloth]).tolist() if int(n) in members}
    skinned = {int(n) for n in np.unique(flat.joints[flat.weights > 0]).tolist()
               if int(n) in members and chain_root(ss, int(n)) in roots}
    wanted: set[int] = set()
    for n in skinned:
        while n not in wanted:
            wanted.add(n)
            if n in roots:
                break
            n = ss.nodes[n].parent
    sources = sorted(wanted)  # the skeleton lists parents first
    if not sources:
        return [], None
    new = {n: k for k, n in enumerate(sources)}
    anchor = plan_.mapping[pelvis]
    t_rest, s_rest = ts.world(), ss.world()
    parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
    # each chain moves with its cloth from the source's space to the bound shape (a refit carries it with the pelvis)
    carry = {}
    for k, n in enumerate(sources):
        if parents[k] < 0:
            share = (flat.weights * np.isin(flat.joints, [sources[j] for j in range(len(sources))
                                                         if chain_root(ss, sources[j]) == n])).sum(axis=1)
            rows = np.where(share > 0.5)[0]
            carry[n] = ms.rigid_fit(flat.vertices[rows], sv[rows], share[rows]) if len(rows) >= 3 else np.eye(4)
    rest = np.array([carry[chain_root(ss, n)] @ s_rest[n] for n in sources])
    local = np.array([np.linalg.inv(rest[p] if p >= 0 else t_rest[anchor]) @ rest[k] for k, p in enumerate(parents)])
    chains = ms.Chains(anchor, parents, local, rest)
    # unique names below the slot's own bones
    taken = {node.path for node in ts.nodes}
    names, paths, locals_ = [], [], []
    for k, n in enumerate(sources):
        name = ss.nodes[n].name
        parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[anchor].path
        while f"{parent_path}/{name}" in taken:
            name += "_costume"
        names.append(name)
        paths.append(f"{parent_path}/{name}")
        taken.add(paths[-1])
        if parents[k] >= 0:
            locals_.append(ss.nodes[n].local)  # as it hangs in the costume's own skeleton
        else:
            m = local[k]
            scale = np.linalg.norm(m[:3, :3], axis=0)
            locals_.append((tuple(m[:3, 3]), tuple(ms.quaternion(m[:3, :3] / scale)), tuple(scale)))
    # dense weights over the columns, then the chains' bones
    width = len(columns) + len(sources)
    index = {c: k for k, c in enumerate(columns)}
    chain = np.vectorize(lambda n: new.get(int(n), -1), otypes=[int])(flat.joints)
    on = (chain >= 0) & (flat.weights > 0)
    cols = np.where(on, len(columns) + np.maximum(chain, 0),
                    np.vectorize(lambda n: index.get(int(n), 0), otypes=[int])(mapped))
    cols = np.where(flat.weights > 0, cols, 0)
    plain = hm.densify(cols, flat.weights, width)
    smooth = plain.copy()
    smooth[:, len(columns):] = ms.smooth_weights(plain[:, len(columns):], flat.edges)
    smooth[~writable_rows] = plain[~writable_rows]
    leg_joints = np.where(flat.weights[legs] > 0, mapped[legs], anchor)
    leg_set = ms.Legs(sv[legs], leg_joints, flat.weights[legs])
    out = []
    for rule, dense in (("skirt", plain), ("skirt smooth", smooth)):
        if rule == "skirt smooth" and np.abs(smooth - plain).max() < 1e-9:
            continue
        on_chains = dense[:, len(columns):].sum(axis=1) > 1e-9
        # with the ring of cloth around them that the chains do not carry, so that the edges across the seam keep
        # their length too (a chain's head may move)
        ring = np.zeros(len(on_chains), bool)
        seam = on_chains[flat.edges[:, 0]] != on_chains[flat.edges[:, 1]]
        ring[flat.edges[seam].ravel()] = True
        rows = np.where(on_chains | ring)[0]
        joints4, weights4 = hm.keep_four(dense[rows])
        fixed = sorted({columns[c] for c in joints4[weights4 > 0].ravel().tolist() if c < len(columns)} | {anchor})
        slot = {c: fixed.index(columns[c]) + len(sources) for c in range(len(columns)) if columns[c] in fixed}
        joints = np.vectorize(lambda c: c - len(columns) if c >= len(columns) else slot.get(c, slot[index[anchor]]),
                              otypes=[int])(joints4)
        position = np.full(len(flat.vertices), -1)
        position[rows] = np.arange(len(rows))
        edges = position[flat.edges]
        edges = edges[(edges >= 0).all(axis=1)]
        cloth_set = ms.Cloth(sv[rows], joints, weights4, fixed, edges)
        out.append(_SkirtCandidate(rule, dense, ms.Solver(chains, cloth_set, leg_set, t_rest, t_legs["pelvis"])))
    if not out:
        return [], None
    return out, Skirt(sources, names, parents, locals_, anchor, out[0].solver, len(ts.nodes))


def hems(target: Costume, source: Costume, plan_: Plan, t_clips: list[Any], s_clips: list[Any],
         writable: list[bool], physics: bool = False) -> Hems:
    """Rule A of BL-118: new skin weights for the cloth the group choice left rigid around the legs, and, with
    ``physics`` (Optimize Physics, BL-132), Rule B of BL-124: the skirt chains as new bones turned by the legs in every
    frame; picked by measurement under the slot's own clips (see ``model_hems`` and ``model_skirts``). ``writable``:
    per source part, whether its mesh's skin can be rewritten.

    With ``physics`` the leg-anchor groups the group choice set to ``follow`` are judged again by pass-through: both
    rules are also built as if those groups were rigid (named with the ``REGROUP`` prefix), and ``model_hems.choose``
    compares them with the follow result, the first score, which they must beat within the same stretch ceiling. A build
    without ``physics`` never looks at them."""
    ts, ss = target.skeleton, source.skeleton
    t_legs, s_legs = legs_of(ts), legs_of(ss)
    if t_legs is None or s_legs is None:
        return Hems(note="no Biped legs")
    s_anchors = lower_anchors(ss, s_legs)
    rigid = sorted({n for anchor, members in plan_.groups.items() if anchor in s_anchors
                    and plan_.choices.get(ss.nodes[anchor].name) == "rigid" for n in members})
    followed = followed_groups(plan_, ss, s_anchors) if physics else {}
    flat = flatten(source, ss.world())  # the source's space, for the fit to its own clips
    on_rigid = np.isin(flat.joints, rigid) & (flat.weights > 0)
    share = (flat.weights * on_rigid).sum(axis=1)
    # Optimize Physics: the same cloth with the followed groups rigid as well
    on_regroup = np.isin(flat.joints, sorted({*rigid, *(n for members in followed.values() for n in members)})) \
        & (flat.weights > 0) if followed else None
    share_regroup = (flat.weights * on_regroup).sum(axis=1) if followed else share
    if not (share > 1e-6).any() and not (share_regroup > 1e-6).any():
        return Hems(note="no rigid cloth")
    rows_ok = np.concatenate([np.full(len(skin.vertices), ok) for skin, ok in zip(source.skins, writable)])
    on_rigid &= rows_ok[:, None]
    share = np.where(rows_ok, share, 0.0)
    if followed:
        on_regroup &= rows_ok[:, None]
        share_regroup = np.where(rows_ok, share_regroup, 0.0)

    # the slot's legs at rest; heights along Unity's vertical (+Y of the prefab's space), from the pelvis
    t_rest = ts.world()
    at = {key: t_rest[node][:3, 3] for key, node in t_legs.items()}
    hip_width = float(np.linalg.norm(at["thigh L"] - at["thigh R"]))
    up = np.array([0.0, 1.0, 0.0])
    if hip_width < 1e-4 or float((at["pelvis"] - (at["foot L"] + at["foot R"]) / 2) @ up) < 1e-4:
        return Hems(note="no legs")
    hip = float(((at["thigh L"] + at["thigh R"]) / 2 - at["pelvis"]) @ up)
    knee = float(((at["calf L"] + at["calf R"]) / 2 - at["pelvis"]) @ up)
    if hip - knee < 1e-4:
        return Hems(note="no legs")
    sv = plan_.fit.vertices if plan_.fit is not None else flat.vertices  # the shape bound on the slot
    left = hm.leg_distance(sv, at["thigh L"], at["calf L"], at["foot L"])
    right = hm.leg_distance(sv, at["thigh R"], at["calf R"], at["foot R"])
    axis = np.minimum(left, right)

    # the costume's legs and the cloth around them; the slot's own cloth
    dominant = flat.joints[np.arange(len(sv)), np.argmax(flat.weights, axis=1)]
    all_legs = np.where(np.isin(dominant, sorted(s_anchors - {s_legs["pelvis"]})))[0]
    legs = all_legs[::max(1, len(all_legs) // LEG_SAMPLES)]
    cloth = cloth_mask(ss, dominant, s_anchors, axis, hip_width)
    triangles = flat.triangles[cloth[flat.triangles].all(axis=1)] if len(flat.triangles) else flat.triangles
    edges = flat.edges[cloth[flat.edges].all(axis=1)]
    if not len(legs) or not len(triangles):
        return Hems(note="no cloth around the legs")
    state, _ = hm.cover(sv, legs, triangles, at["pelvis"], up)
    covered = legs[state == 1]
    logger.debug("Hem: %d cloth vertices (%d triangles), %d rigid-weighted, %d of %d sampled leg vertices covered",
                 int(cloth.sum()), len(triangles), int((share > 1e-6).sum()), len(covered), len(legs))
    if not len(covered):
        return Hems(note="no leg under the cloth")
    own = OwnMesh.of(target)
    t_axis = np.minimum(hm.leg_distance(own.rest, at["thigh L"], at["calf L"], at["foot L"]),
                        hm.leg_distance(own.rest, at["thigh R"], at["calf R"], at["foot R"]))
    t_cloth = cloth_mask(ts, own.dominant, lower_anchors(ts, t_legs), t_axis, hip_width)
    t_edges = own.edges[t_cloth[own.edges].all(axis=1)]

    # dense weights per slot node, with the rigid part apart
    mapped = np.vectorize(lambda n: plan_.mapping.get(int(n), 0), otypes=[int])(flat.joints)
    used = set(mapped[flat.weights > 0].tolist())
    if followed:  # the followed groups' secondary bones on the anchor's slot node, as a rigid group is mapped
        regroup = {**plan_.mapping, **{n: plan_.mapping[anchor] for anchor, members in followed.items() for n in members}}
        mapped_regroup = np.vectorize(lambda n: regroup.get(int(n), 0), otypes=[int])(flat.joints)
        used |= set(mapped_regroup[flat.weights > 0].tolist())
    columns = sorted(used | {t_legs[k] for k in FIT_KEYS})
    index = {n: k for k, n in enumerate(columns)}
    column_of = lambda nodes: np.vectorize(index.get, otypes=[int])(np.where(flat.weights > 0, nodes, columns[0]))  # noqa: E731
    cols = column_of(mapped)
    dense = hm.densify(cols, flat.weights, len(columns))
    rigid_part = hm.densify(cols, np.where(on_rigid, flat.weights, 0.0), len(columns))

    # the rules: Rule A where a part's skin can be rewritten, Rule B (its smooth variant likewise)
    rules: list[tuple[str, np.ndarray, ms.Solver | None]] = [("rigid", dense, None)]
    if (share > 1e-6).any() or (followed and (share_regroup > 1e-6).any()):
        height = (hip - (sv - at["pelvis"]) @ up) / (hip - knee)
        near = 1 - hm.smoothstep(hip_width, 2 * hip_width, axis)
        sigma = hip_width / 2
        close_l, close_r = np.exp(-(left / sigma) ** 2), np.exp(-(right / sigma) ** 2)
        ramp_columns = [index[t_legs[k]] for k in ("thigh L", "thigh R", "calf L", "calf R")]
        fit_columns = [index[t_legs[k]] for k in FIT_KEYS]

        def rule_a(base: np.ndarray, part: np.ndarray, covered_share: np.ndarray, on: np.ndarray, prefix: str):
            fitted = fit_own(ss, flat, on, covered_share, s_legs, s_clips)
            out = []
            for name, alpha in HEM_RULES:
                if name == "fit":
                    if fitted is None:
                        continue
                    weights = hm.reweight(base, part, alpha * (covered_share > 1e-6), fitted, fit_columns)
                else:
                    top, change = RAMPS[name]
                    fraction, moves = hm.ramp(height, near, close_l, close_r, alpha, top, change)
                    weights = hm.reweight(base, part, fraction, moves, ramp_columns)
                out.append((f"{prefix}{name} {alpha}", weights, None))
            return out

        if (share > 1e-6).any():
            rules += rule_a(dense, rigid_part, share, on_rigid, "")
    skirt_rules, skirt = _skirt_candidates(ts, ss, plan_, flat, sv, dominant, cloth, all_legs, t_legs, s_legs,
                                           mapped, columns, rows_ok) if physics else ([], None)
    rules += [(c.rule, c.weights, c.solver) for c in skirt_rules]
    if followed:
        # the same rules with the followed groups rigid: the pelvis group's chains only where it was followed
        cols_regroup = column_of(mapped_regroup)
        dense_regroup = hm.densify(cols_regroup, flat.weights, len(columns))
        part_regroup = hm.densify(cols_regroup, np.where(on_regroup, flat.weights, 0.0), len(columns))
        if (share_regroup > 1e-6).any():
            rules += rule_a(dense_regroup, part_regroup, share_regroup, on_regroup, REGROUP)
        if s_legs["pelvis"] in followed:
            lowered = dataclasses.replace(plan_, mapping=regroup, choices={
                **plan_.choices, **{ss.nodes[anchor].name: "rigid" for anchor in followed}})
            again, again_skirt = _skirt_candidates(ts, ss, lowered, flat, sv, dominant, cloth, all_legs, t_legs, s_legs,
                                                   mapped_regroup, columns, rows_ok)
            again = [dataclasses.replace(c, rule=REGROUP + c.rule) for c in again]
            skirt_rules, skirt = skirt_rules + again, again_skirt if skirt is None else skirt
            rules += [(c.rule, c.weights, c.solver) for c in again]
    if len(rules) == 1:
        return Hems(note="skin not writable")

    # measured under the slot's own clips
    names = [(c.peek_name() or "").lower() for c in t_clips]
    checks = []
    inverse = np.array([np.linalg.inv(t_rest[c]) for c in columns])
    rest_rotation = np.linalg.inv(t_rest[t_legs["pelvis"]][:3, :3])
    for suffix in HEM_CLIPS:
        clip = next((c for c, n in zip(t_clips, names) if n.endswith("_" + suffix.lower())), None)
        if clip is not None:
            tree = clip.read_typetree()
            times, pose_at, _ = ts.clip_frames(tree)
            frames = []
            for frame in measured_frames(len(times)):
                pose = pose_at(frame)
                frames.append((frame, np.array([pose[c] for c in columns]) @ inverse, pose[t_legs["pelvis"]][:3, 3],
                               pose[t_legs["pelvis"]][:3, :3] @ rest_rotation @ up, pose))
            checks.append((suffix, clip, tree, frames))
    if not checks:
        return Hems(note="no check clips")
    limit = max(hm.STRETCH_FLOOR, max(hm.stretch_p99(own.rest, own.posed(pose), t_edges)
                                      for *_, frames in checks for *_, pose in frames))
    scores, four, solved = [], {}, {}
    for rule, weights, solver in rules:
        joints4, weights4 = hm.keep_four(weights)
        deep, worst = {}, 1.0
        for suffix, clip, tree, frames in checks:
            deep[suffix] = 0.0
            if solver is not None:  # the chains' turns in time, as the clip will hold them
                sequence = skirt_sequence(ts, solver, tree)
                solved[(rule, _key(clip))] = sequence
                turns = dict(zip(*sequence))
            for frame, matrices, centre, carried, pose in frames:
                if solver is not None:
                    matrices = np.concatenate([matrices, solver.posed(pose, turns[frame]) @ solver.inverse])
                posed = hm.skin(sv, joints4, weights4, matrices)
                state, depth = hm.cover(posed, covered, triangles, centre, carried)
                deep[suffix] = max(deep[suffix], hm.deep_share(state, depth))
                worst = max(worst, hm.stretch_p99(sv, posed, edges))
        scores.append(hm.Score(rule, deep, worst))
        four[rule] = (joints4, weights4)
    best = hm.choose(scores, limit)
    result = Hems(best.rule, "", limit, scores)
    if best.rule == "rigid":
        return result
    joints4, weights4 = four[best.rule]
    chosen = next(weights for rule, weights, _ in rules if rule == best.rule)
    bounds = [*flat.starts, len(sv)]
    result.regrouped = best.rule.startswith(REGROUP)
    if best.rule.removeprefix(REGROUP).startswith("skirt"):
        candidate = next(c for c in skirt_rules if c.rule == best.rule)
        result.skirt = skirt
        skirt.solver = candidate.solver
        skirt.solved = {key: value for (rule, key), value in solved.items() if rule == best.rule}
        nodes = np.array(columns + [skirt.node(k) for k in range(len(skirt.sources))])
        if result.regrouped:  # every vertex that differs from the follow result is written: the groups' other bones too
            changed = np.abs(chosen - np.pad(dense, ((0, 0), (0, len(skirt.sources))))).max(axis=1) > 1e-9
        else:
            plain = skirt_rules[0].weights
            changed = np.abs(chosen - plain).max(axis=1) > 1e-9  # the smooth variant rewrites the blended vertices
    else:
        nodes = np.array(columns)
        changed = np.abs(chosen - dense).max(axis=1) > 1e-9
    for k in range(len(source.parts)):
        rows = np.where(changed[bounds[k]:bounds[k + 1]])[0]
        result.parts.append((rows, nodes[joints4[bounds[k] + rows]], weights4[bounds[k] + rows]) if len(rows) else None)
    return result


def skirt_bones(skeleton: Skeleton, skirt: Skirt) -> None:
    """Rule B's new bones appended to the slot's skeleton (no objects yet: ``_edit_prefabs`` adds them)."""
    if len(skeleton.nodes) != skirt.base:
        raise refuse("skeleton changed")
    for k, (name, parent, local) in enumerate(zip(skirt.names, skirt.parents, skirt.locals)):
        father = skirt.node(parent) if parent >= 0 else skirt.anchor
        path = f"{skeleton.nodes[father].path}/{name}" if skeleton.nodes[father].path else name
        skeleton.nodes.append(Node(name, path, father, local, None, None, True))
    world = skeleton.world()
    for k in range(len(skirt.sources)):
        if float(np.abs(world[skirt.node(k)] - skirt.solver.chains.rest[k]).max()) > 1e-4:
            raise refuse("rest pose not reproduced")


def measured_frames(count: int) -> list[int]:
    """The frames of a clip the hem rules measure (``HEM_FRAMES`` evenly spaced)."""
    return sorted({min(count - 1, round(k * (count - 1) / max(1, HEM_FRAMES - 1))) for k in range(HEM_FRAMES)})


def skirt_sequence(skeleton: Skeleton, solver: ms.Solver, tree: Mapping[str, Any]) -> tuple[list[int], list[dict]]:
    """Rule B's solved frames of a clip (every ``SOLVE_EVERY``-th, the last and the measured ones) and the chains'
    turns there, in time (``Solver.sequence``)."""
    times, pose, _ = skeleton.clip_frames(tree)
    count = len(times)
    frames = sorted(set(range(0, count, ms.SOLVE_EVERY)) | {count - 1} | set(measured_frames(count)))
    loop = bool(tree["m_MuscleClip"].get("m_LoopTime"))
    return frames, solver.sequence([times[f] for f in frames], lambda k: pose(frames[k]), loop)


def skirt_curves(skeleton: Skeleton, skirt: Skirt, tree: dict, key: tuple[str, int] | None = None) -> bool:
    """Rule B's rotation and position curves for the chains' first bones added to a clip typetree of the slot: the
    turns and shifts of
    :func:`skirt_sequence` (or those measured for the clip ``key``), interpolated onto its dense grid. False when the
    clip has a layout the writer does not edit."""
    grid = ms.dense_grid(tree)
    if grid is None:
        return False
    times = skeleton.clip_frames(tree)[0]
    frames, turns = skirt.solved.get(key) or skirt_sequence(skeleton, skirt.solver, tree)
    roots = skirt.solver.roots
    rotations = np.array([[ms.quaternion(t[root][:3, :3]) for root in roots] for t in turns])
    positions = np.array([[t[root][:3, 3] for root in roots] for t in turns])
    at = np.clip(grid, times[0], times[-1])
    columns = []
    for r in range(len(roots)):  # per root its rotation (4 curves), then its position (3)
        if len(frames) > 1:
            solved_at = np.array([times[f] for f in frames])
            columns.append(ms.resample(solved_at, ms.continuous(rotations[:, r]), at))
            columns.append(np.stack([np.interp(at, solved_at, positions[:, r, k]) for k in range(3)], axis=1))
        else:
            columns.append(np.repeat(rotations[:1, r], len(grid), axis=0))
            columns.append(np.repeat(positions[:1, r], len(grid), axis=0))
    samples = np.concatenate(columns, axis=1)
    template = tree["m_ClipBindingConstant"]["genericBindings"][0]
    bindings = []
    for root in roots:
        path = skeleton.nodes[skirt.node(root)].path
        bindings += [ms.rotation_binding(template, path), ms.position_binding(template, path)]
    ms.add_dense_curves(tree, bindings, samples)
    return True


# --- the halo (BL-131) ---------------------------------------------------------------------------------------------

@dataclass
class Halo:
    """A prefab's halo: the node its ``FxFollower`` keeps beside the head (HaloRoot) and the one unskinned renderer
    below it (``shape`` "single"). ``shape`` is "none" for a prefab without a halo; anything else names why the halo
    is not one renderer that can be carried (several renderers, particles, a skinned halo, a halo with no follower)."""

    shape: str
    root: int | None = None      # the followed node
    follower: Any = None         # its FxFollower
    node: int | None = None      # the renderer's node, below the followed one
    renderer: Any = None         # the MeshRenderer
    mesh: Any = None             # its MeshFilter's mesh
    materials: list[Any] = field(default_factory=list)


@dataclass
class HaloChange:
    """What the slot's prefabs change on their halo (:func:`_halo_change`): the renderer at ``path`` drawing ``mesh``
    (the slot's halo mesh) draws ``new_mesh`` with ``materials`` at ``local`` (position, rotation, scale), and the
    follower at ``root`` takes ``follower``'s offsets; with ``new_mesh`` None it draws nothing."""

    path: str
    root: str
    mesh: tuple[str, int]
    new_mesh: int | None = None
    materials: list[int] = field(default_factory=list)
    local: tuple | None = None
    follower: dict[str, Any] | None = None


FOLLOWER_OFFSETS = ("TargetRelativePosition", "TargetRelativeRotation")


def halo_of(skeleton: Skeleton) -> Halo:
    """The halo of a prefab (see :class:`Halo`): the node an ``FxFollower`` moves that has something drawn below it.
    Renderers with halo shading outside it (Aris's weapon effect) are not its halo."""
    g = skeleton.graph
    # (Rule B's new bones have no objects yet)
    components = [[o for c in g.tree(node.go)["m_Component"] if (o := g.deref(node.go, c["component"])) is not None]
                  if node.go is not None else [] for node in skeleton.nodes]

    def follower(i: int) -> Any:
        return next((o for o in components[i] if o.type.name == "MonoBehaviour" and g.script(o) == "FxFollower"), None)

    def drawn(i: int) -> list[Any]:
        return [o for o in components[i] if o.type.name.endswith("Renderer") or o.type.name == "ParticleSystem"]

    def below(i: int) -> list[int]:
        return [i] + skeleton.descendants(i)

    roots = [i for i in range(len(skeleton.nodes)) if follower(i) is not None and any(drawn(j) for j in below(i))]
    if not roots:
        shaded = any(o.type.name in ("MeshRenderer", "SkinnedMeshRenderer") and "halo" in (
            _shader_kind(g, g.deref(o, m)) for m in g.tree(o)["m_Materials"]) for objs in components for o in objs)
        return Halo("halo without a follower" if shaded else "none")
    if len(roots) > 1:
        return Halo("several followed nodes")
    root = roots[0]
    found = [(j, o) for j in below(root) for o in drawn(j)]
    if len(found) != 1 or found[0][1].type.name != "MeshRenderer":
        return Halo(", ".join(sorted(o.type.name for _, o in found)))
    node, renderer = found[0]
    if node == root:
        return Halo("renderer on the followed node")
    filt = next((o for o in components[node] if o.type.name == "MeshFilter"), None)
    mesh = g.deref(filt, g.tree(filt)["m_Mesh"]) if filt is not None else None
    if mesh is None:
        return Halo("no mesh")
    materials = [g.deref(renderer, m) for m in g.tree(renderer)["m_Materials"]]
    if not materials or any(m is None for m in materials):
        return Halo("material not found")
    return Halo("single", root, follower(root), node, renderer, mesh, materials)


def _mesh_tree(mesh: Any) -> dict:
    """A mesh's tree holding its vertex data: a mesh that streams it from its bundle's resource file (Yukino's halo)
    takes it along, so the copy reads in any file."""
    tree = mesh.read_typetree()
    stream = tree.get("m_StreamData") or {}
    if stream.get("size"):
        from UnityPy.helpers.ResourceReader import get_resource_data

        tree["m_VertexData"]["m_DataSize"] = get_resource_data(stream["path"], mesh.assets_file, stream["offset"],
                                                               stream["size"])
        tree["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
    return tree


def _halo_change(t_skeleton: Skeleton, s_skeleton: Skeleton, carry_mesh: Callable[[Any], int],
                 carry_material: Callable[[Any], int], names: tuple[str, str]) -> HaloChange | None:
    """The slot's halo for another student's costume: the source's carried (its mesh and materials added by
    ``carry_mesh`` and ``carry_material``) and placed below the slot's followed node as the source's is below its own,
    the levels between folded into the renderer's node; a source without a halo hides the slot's. None (the slot's
    halo kept, logged) for any other shape of either."""
    t, s = halo_of(t_skeleton), halo_of(s_skeleton)
    s_name, t_name = names
    if t.shape != "single" or s.shape not in ("single", "none"):
        if t.shape != "none":
            logger.info("Costume of %s on %s: the slot's halo kept (slot: %s, source: %s)", s_name, t_name, t.shape,
                        s.shape)
        return None
    change = HaloChange(t_skeleton.nodes[t.node].path, t_skeleton.nodes[t.root].path, _key(t.mesh))
    if s.shape == "none":
        logger.info("Costume of %s on %s: the source has no halo, the slot's is hidden", s_name, t_name)
        return change
    s_world, t_world = s_skeleton.world(), t_skeleton.world()
    placed = np.linalg.inv(s_world[s.root]) @ s_world[s.node]
    parent = t_skeleton.nodes[t.node].parent
    local = np.linalg.inv(np.linalg.inv(t_world[t.root]) @ t_world[parent]) @ placed
    scale = np.linalg.norm(local[:3, :3], axis=0)
    rotation = local[:3, :3] / scale
    if np.linalg.det(rotation) < 0:  # a mirrored halo: the mirror goes into the scale
        scale[0], rotation[:, 0] = -scale[0], -rotation[:, 0]
    quaternion = ms.quaternion(rotation)
    change.materials = [carry_material(m) for m in s.materials]
    change.new_mesh = carry_mesh(s.mesh)
    change.local = (local[:3, 3], quaternion, scale)
    follower = s_skeleton.graph.tree(s.follower)
    change.follower = {name: copy.deepcopy(follower[name]) for name in FOLLOWER_OFFSETS if name in follower}
    logger.info("Costume of %s on %s: halo %s carried", s_name, t_name, s.mesh.peek_name())
    return change


# --- writing -------------------------------------------------------------------------------------------------------

class _Writer:
    """Objects added to the slot's bundles, opened for writing."""

    def __init__(self, files: Mapping[str, Any], seed: str):
        self.files = files  # lower-case CAB -> (engine Bundle, SerializedFile)
        self.random = random.Random(zlib.crc32(seed.encode()))

    def file(self, cab: str) -> Any:
        return self.files[cab][1]

    def fresh(self, sf: Any) -> int:
        while True:
            pid = self.random.randrange(-(1 << 63), 1 << 63)
            if pid and pid not in sf.objects:
                return pid

    def add(self, sf: Any, type_name: str, tree: dict) -> int:
        template = next((o for o in sf.objects.values() if o.type.name == type_name), None)
        if template is None:
            raise refuse("no object type in the slot", type=type_name)
        new = object.__new__(type(template))
        new.__dict__.update(template.__dict__)
        new.path_id = self.fresh(sf)
        new.data = b""
        new.save_typetree(tree)
        sf.objects[new.path_id] = new
        ordered = sorted(sf.objects.items())
        sf.objects.clear()
        sf.objects.update(ordered)
        return new.path_id

    def add_like(self, sf: Any, source: Any, tree: dict) -> int:
        """A new object of ``source``'s type in ``sf``, the type copied from ``source``'s file when ``sf`` holds no
        object of it (a shader carried with a costume). Both files must come from the same build."""
        src_type, src_file = source.serialized_type, source.assets_file
        if (src_type is None or sf.header.version != src_file.header.version or sf.unity_version != src_file.unity_version
                or sf._enable_type_tree != src_file._enable_type_tree or sf.header.version < 16):
            raise refuse("object type of another build", type=source.type.name)
        index = next((i for i, typ in enumerate(sf.types) if typ.class_id == source.class_id
                      and typ.old_type_hash == src_type.old_type_hash and typ.script_type_index == src_type.script_type_index), None)
        if index is None:
            if src_type.type_dependencies:
                raise refuse("object type with dependencies", type=source.type.name)
            sf.types.append(copy.copy(src_type))
            index = len(sf.types) - 1
        template = next(iter(sf.objects.values()))
        new = object.__new__(type(template))
        new.__dict__.update(template.__dict__)
        new.type_id, new.serialized_type, new.class_id, new.type = index, sf.types[index], source.class_id, source.type
        new.path_id = self.fresh(sf)
        new.data = b""
        new.save_typetree(tree)
        sf.objects[new.path_id] = new
        ordered = sorted(sf.objects.items())
        sf.objects.clear()
        sf.objects.update(ordered)
        return new.path_id

    def builtin(self, sf: Any, entry: Any) -> int:
        """The file id of one of the player's own resource files (``Library/unity default resources``), its external
        entry copied as it is when ``sf`` has none."""
        for i, e in enumerate(sf.externals):
            if e.path.lower() == entry.path.lower():
                return i + 1
        sf.externals.append(copy.copy(entry))
        return len(sf.externals)

    def external(self, sf: Any, cab: str) -> int:
        if cab == sf.name.lower():
            return 0
        for i, e in enumerate(sf.externals):
            if e.path.replace("\\", "/").rsplit("/", 1)[-1].lower() == cab:
                return i + 1
        real = "CAB-" + cab[4:] if cab.startswith("cab-") else cab
        entry = object.__new__(type(sf.externals[0]))
        for slot in ("temp_empty", "guid", "type"):
            setattr(entry, slot, getattr(sf.externals[0], slot, None))
        entry.path = f"archive:/{real}/{real}"
        sf.externals.append(entry)
        return len(sf.externals)


def _matrix(m: np.ndarray) -> dict[str, float]:
    return {f"e{r}{c}": float(m[r, c]) for c in range(4) for r in range(4)}


def _vector(v: Iterable[float]) -> dict[str, float]:
    return dict(zip("xyz", (float(x) for x in v)))


@dataclass
class Result:
    changed: dict[Path, Any]  # slot file -> engine Bundle with the changes
    plan: Plan
    parts: int
    hems: Hems | None = None


def _model_prefabs(graph: BundleGraph, files: list[Path]) -> dict[str, Any]:
    return {name: pointer for name, pointer in _prefabs(graph, files).items()}


def choose_prefab(prefabs: Mapping[str, Any], dev: str) -> str:
    for kind in ("echelon", "battle", "cafe", "strategy"):
        name = PREFAB_KINDS[kind].format(dev).lower()
        if name in prefabs:
            return name
    for pattern in FIELD_KINDS:
        name = pattern.format(dev).lower()
        if name in prefabs:
            return name
    raise refuse("no prefab", dev=dev)


def adapt(*, index: Mapping[str, Path], resolver: Callable | None, target_files: list[Path], target_dev: str,
          source_files: list[Path], source_dev: str, open_writable: Callable[[Path], Any], strict: bool = True,
          physics: bool = False, halo: bool = False) -> Result:
    """The changed bundles of ``target_files`` (the slot, official copies) carrying the costume of ``source_dev``'s
    model, whose prefab sits in ``source_files``. ``index`` maps CABs to files to read (the slot's own CABs to
    ``target_files``); ``resolver`` finds any other CAB. ``open_writable`` opens a slot file as an engine Bundle.
    With ``strict=False`` a costume past the stretch limit is built anyway (``plan.unfit``): for a preview, or for
    another student's costume, which is applied all the same (BL-131).
    ``physics`` (Optimize Physics, BL-132) lets the hem rule also offer Rule B, the skirt chains as new bones.
    ``halo`` (another student's costume, BL-131) carries the source's halo onto the slot's (:func:`_halo_change`).
    The caller holds the model lock (``gameassets.service.model_lock``): the graph holds the path lock of every bundle
    it reads until it closes, like an export's."""
    with BundleGraph(index, resolver=resolver) as graph:
        return _adapt(graph, index, target_files, target_dev, source_files, source_dev, open_writable, strict, physics,
                      halo)


def _adapt(graph: BundleGraph, index: Mapping[str, Path], target_files: list[Path], target_dev: str,
           source_files: list[Path], source_dev: str, open_writable: Callable[[Path], Any], strict: bool = True,
           physics: bool = False, halo: bool = False) -> Result:
    target_prefabs = _model_prefabs(graph, [f for f in target_files if f.name.startswith("character-")])
    source_prefabs = _model_prefabs(graph, source_files)
    t_name = choose_prefab(target_prefabs, target_dev)
    try:
        # a battle or field model, else a field NPC or a story-only or UI-only model of its own bundles
        s_root, s_name, _ = model_root(graph, source_files, source_dev)
    except AssetNotFound:
        raise refuse("no prefab", dev=source_dev) from None
    t_skeleton = Skeleton.of(graph, target_prefabs[t_name].deref())
    s_skeleton = Skeleton.of(graph, s_root)
    if not s_skeleton.renderers:
        # an unskinned model (Kei's Decagrammaton body, a static mesh): nothing to bind to the slot's bones
        raise refuse("static model", dev=source_dev)
    own = {k: v for k, v in target_prefabs.items() if target_dev in k}
    t_clips = prefab_clips(graph, t_skeleton, own)
    s_clips = prefab_clips(graph, s_skeleton, {k: v for k, v in source_prefabs.items() if source_dev in k})
    t_idle = next(iter(t_skeleton.poses(pick(t_clips, IDLE_CLIPS)[:1], frames=1)), None)
    s_idle = next(iter(s_skeleton.poses(pick(s_clips, IDLE_CLIPS)[:1], frames=1)), None)
    t_parts, s_parts = roles(t_skeleton, t_idle), roles(s_skeleton, s_idle)

    def costume(parts: list[Part]) -> list[Part]:
        ordered = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]
        if not ordered or ordered[0].role != "primary":
            raise refuse("no costume")
        return ordered

    t_costume, s_costume = costume(t_parts), costume(s_parts)
    target = Costume(t_skeleton, t_costume, [skin_of(p.mesh) for p in t_costume])
    source = Costume(s_skeleton, s_costume, [skin_of(p.mesh) for p in s_costume])
    probes = probe_poses(t_skeleton, t_clips)
    mesh_trees = [p.mesh.read_typetree() for p in s_costume]
    layouts = [mg.geometry_of(tree) for tree in mesh_trees]
    result = plan(target, source, probes, strict=strict, fit=all(layout is not None for layout in layouts),
                  own_probes=probe_poses(s_skeleton, s_clips))
    t_rest, s_rest = t_skeleton.world(), s_skeleton.world()
    # each part's vertices in its renderer's space at the source's rest, as they are bound (the costume's shape)
    shapes = [result.fit.part(k, s_rest[part.node]) for k, part in enumerate(s_costume)]
    for k, (skin, (points, _)) in enumerate(zip(source.skins, shapes)):
        if layouts[k] is None and float(np.abs(points - skin.vertices).max(initial=0.0)) > 1e-4:
            raise refuse("costume geometry not writable", part=s_costume[k].mesh.peek_name())
    stores = [hm.store_of(tree) for tree in mesh_trees]
    hem = hems(target, source, result, t_clips, s_clips, [store is not None for store in stores], physics)
    skirt_clips: list[Any] = []
    if hem.skirt is not None:  # Rule B: the chains join the slot's skeleton, the slot's clips that move it get curves
        skirt_bones(t_skeleton, hem.skirt)
        for k, n in enumerate(hem.skirt.sources):
            result.mapping[n] = hem.skirt.node(k)
        t_rest = t_skeleton.world()
        pelvis_hash = zlib.crc32(t_skeleton.nodes[hem.skirt.anchor].path.encode())
        for clip in t_clips:
            bindings = graph.tree(clip)["m_ClipBindingConstant"]["genericBindings"]
            if any(b["path"] == pelvis_hash and b["typeID"] == 4 for b in bindings):
                skirt_clips.append(clip)

    # the slot files the new objects go to: the primary's mesh, materials and textures, and the prefab files
    primary = t_costume[0]
    mesh_cab = primary.mesh.assets_file.name.lower()
    material_cab = primary.materials[0].assets_file.name.lower() if primary.materials and primary.materials[0] is not None else None
    texture_cab = None
    for material in primary.materials:
        for _, value in (graph.tree(material)["m_SavedProperties"]["m_TexEnvs"] if material is not None else []):
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
        raise refuse("costume outside the slot", part=type_name, file=Path(str(graph.locations.get(cab or "", "?"))).name)

    mesh_cab, material_cab, texture_cab = home(mesh_cab, "Mesh"), home(material_cab, "Material"), home(texture_cab, "Texture2D")
    # Rule B's curves go into the slot's own clips, where they are: a clip in another file stays as it is
    skirt_clips = [c for c in skirt_clips if c.assets_file.name.lower() in slot]
    needed = {mesh_cab, material_cab, texture_cab} | prefab_cabs | {c.assets_file.name.lower() for c in skirt_clips}
    opened: dict[Path, Any] = {}
    files: dict[str, Any] = {}
    for cab in needed:
        path = Path(slot[cab])
        bundle = opened.get(path) or open_writable(path)
        opened[path] = bundle
        for name, sf in bundle.env.file.files.items():
            if hasattr(sf, "objects"):
                files[name.lower()] = (bundle, sf)
    writer = _Writer(files, f"{target_dev}|{source_dev}")
    mesh_sf, material_sf, texture_sf = writer.file(mesh_cab), writer.file(material_cab), writer.file(texture_cab)

    def shared(cab: str) -> bool:
        """Whether the game has ``cab`` whenever the target's prefab is loaded: a slot file, a file the prefab's
        files already reference (its catalog dependencies), or an always-loaded (prolog) bundle."""
        path = index.get(cab) or (graph.locations.get(cab))
        if cab in slot or cab in loaded:
            return True
        return path is not None and Path(path).name.startswith(("prologdepengroup-", "prologgroup-"))

    # shaders the game does not load with the slot's prefab, carried as copies in the materials' file (BL-122)
    shaders: dict[tuple[str, int], int] = {}

    def carried(shader: Any, cab: str) -> int:
        if shader is None or shader.type.name != "Shader":
            raise refuse("shader not found", file=Path(str(index.get(cab) or graph.locations.get(cab, "?"))).name)
        if _key(shader) in shaders:
            return shaders[_key(shader)]
        s_tree = shader.read_typetree()
        fixed = [pair[1] if isinstance(pair, (list, tuple)) else pair.get("second", {})
                 for pair in s_tree.get("m_NonModifiableTextures", [])]
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

    # textures and materials
    textures: dict[tuple[str, int], int] = {}
    materials: dict[tuple[str, int], int] = {}

    def carry_material(material: Any) -> int:
        """A source material added to the slot's material file, with its textures (and its shader where the game does
        not load it with the slot's prefab)."""
        if _key(material) not in materials:
            tree = material.read_typetree()
            shader_cab = _external_cab(material, tree["m_Shader"]["m_FileID"])
            if tree["m_Shader"]["m_PathID"] and not shared(shader_cab):
                # a shader the game does not load with the slot's prefab: a copy of it goes into the material's file
                tree["m_Shader"] = {"m_FileID": 0, "m_PathID": carried(graph.deref(material, tree["m_Shader"]), shader_cab)}
            else:
                tree["m_Shader"] = {"m_FileID": writer.external(material_sf, shader_cab), "m_PathID": tree["m_Shader"]["m_PathID"]}
            for _, value in tree["m_SavedProperties"]["m_TexEnvs"]:
                pointer = value["m_Texture"]
                if not pointer["m_PathID"]:
                    continue
                cab = _external_cab(material, pointer["m_FileID"])
                if shared(cab):
                    value["m_Texture"] = {"m_FileID": writer.external(material_sf, cab), "m_PathID": pointer["m_PathID"]}
                    continue
                texture = graph.deref(material, pointer)
                if texture is None:
                    raise refuse("texture not found")
                if _key(texture) not in textures:
                    t_tree = texture.read_typetree()
                    t_tree["image data"] = texture.read().get_image_data()
                    t_tree["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
                    textures[_key(texture)] = writer.add(texture_sf, "Texture2D", t_tree)
                value["m_Texture"] = {"m_FileID": writer.external(material_sf, texture_cab),
                                      "m_PathID": textures[_key(texture)]}
            materials[_key(material)] = writer.add(material_sf, "Material", tree)
        return materials[_key(material)]

    for part in s_costume:
        for material in part.materials:
            if material is None or _key(material) in materials:
                continue
            carry_material(material)

    # meshes: the source parts' meshes with bindposes on the target skeleton
    new_meshes = []
    fitted_skins = []
    for k, (part, skin) in enumerate(zip(s_costume, source.skins)):
        tree = mesh_trees[k]
        nodes = [result.mapping.get(b, 0) if b is not None else 0 for b in part.bones]
        points, linear = shapes[k]
        if layouts[k] is not None and float(np.abs(points - skin.vertices).max(initial=0.0)) > 1e-6:
            mg.write_geometry(tree, layouts[k], points, linear)
        skin = Skin(points, skin.joints, skin.weights, skin.edges, skin.triangles)
        fitted_skins.append(skin)
        joints, weights = skin.joints, skin.weights
        aabbs = len(tree["m_BonesAABB"]) == len(nodes)
        change = hem.parts[k] if k < len(hem.parts) else None
        if change is not None:  # the hem's new weights, on bones of this part's list or added to it
            rows, slot_nodes, new_weights = change
            slots: dict[int, int] = {}
            for i, n in enumerate(nodes):
                slots.setdefault(n, i)
            for n in slot_nodes.ravel().tolist():
                if n not in slots:
                    slots[n] = len(nodes)
                    nodes.append(n)
            new_joints = np.vectorize(slots.get, otypes=[int])(slot_nodes)
            hm.write_weights(tree, stores[k], rows, new_joints, new_weights)
            joints, weights = joints.copy(), weights.copy()
            joints[rows], weights[rows] = new_joints, new_weights
        poses = [np.linalg.inv(t_rest[n]) @ s_rest[part.node] for n in nodes]
        tree["m_BindPose"] = [_matrix(m) for m in poses]
        tree["m_BoneNameHashes"] = [zlib.crc32(t_skeleton.nodes[n].path.encode()) for n in nodes]
        tree["m_RootBoneNameHash"] = graph.tree(primary.mesh)["m_RootBoneNameHash"]
        if aabbs:
            tree["m_BonesAABB"] += [{"m_Min": _vector((0, 0, 0)), "m_Max": _vector((0, 0, 0))}
                                    for _ in range(len(nodes) - len(tree["m_BonesAABB"]))]
        for i, pose in enumerate(poses):
            hit = ((joints == i) & (weights > 0)).any(axis=1)
            if hit.any() and i < len(tree["m_BonesAABB"]):
                local = (np.c_[skin.vertices[hit], np.ones(hit.sum())] @ pose.T)[:, :3]
                tree["m_BonesAABB"][i] = {"m_Min": _vector(local.min(axis=0)), "m_Max": _vector(local.max(axis=0))}
        new_meshes.append((writer.add(mesh_sf, "Mesh", tree), nodes))
    # another student's halo (BL-131): an unskinned mesh, added as it is
    halo_change = _halo_change(t_skeleton, s_skeleton, lambda mesh: writer.add(mesh_sf, "Mesh", _mesh_tree(mesh)),
                               carry_material, (s_name, t_name)) if halo else None

    # prefabs: the primary takes the source primary, further parts get renderers of their own, the rest draws nothing
    rank = {"primary": 0, "costume": 1, "alternate": 2, "copy": 3}
    t_meshes: dict[tuple[str, int], str] = {}
    for p in sorted((p for p in t_parts if p.mesh is not None and p.role in rank), key=lambda p: rank[p.role]):
        t_meshes.setdefault(_key(p.mesh), "primary" if p.role == "primary" else "other")
    t_primary_materials = [_key(m) for m in primary.materials if m is not None]
    root_bone = graph.deref(primary.renderer, graph.tree(primary.renderer)["m_RootBone"])
    root_node = t_skeleton.by_transform.get(_key(root_bone), 0) if root_bone is not None else 0
    for prefab_path in prefab_files:
        bundle = opened[prefab_path]
        for name, sf in bundle.env.file.files.items():
            if hasattr(sf, "objects") and name.lower() in files:
                _edit_prefabs(writer, sf, graph, t_meshes, t_primary_materials, mesh_cab, material_cab, new_meshes,
                              [[materials[_key(m)] for m in p.materials if m is not None] for p in s_costume],
                              [s_skeleton.nodes[p.node].name for p in s_costume], root_node, t_skeleton, s_costume,
                              fitted_skins, result.mapping, s_rest, halo_change)
    if hem.skirt is not None:
        for clip in skirt_clips:
            obj = writer.file(clip.assets_file.name.lower()).objects[clip.path_id]
            tree = obj.read_typetree()
            if skirt_curves(t_skeleton, hem.skirt, tree, _key(clip)):
                obj.save_typetree(tree)
                hem.skirt.clips += 1
            else:
                logger.info("Costume of %s on %s: clip %s left without skirt curves (layout)", s_name, t_name,
                            clip.peek_name())
    logger.info("Costume of %s on %s: %d part(s), groups %s, stretch %.2f (own %.2f), shape %s (changed %.3f, 99th "
                "percentile %.3f); hem %s", s_name, t_name, len(s_costume), result.choices, result.stretch, result.own,
                result.fit.shape, result.fit.largest, result.fit.shift, hem.summary())
    return Result(opened, result, len(s_costume), hem)


def _edit_prefabs(writer: _Writer, sf: Any, graph: BundleGraph, t_meshes: Mapping[tuple[str, int], str],
                  primary_materials: list[tuple[str, int]], mesh_cab: str, material_cab: str,
                  new_meshes: list[tuple[int, list[int]]], new_materials: list[list[int]], names: list[str],
                  root_node: int, t_skeleton: Skeleton, s_costume: list[Part], skins: list[Skin],
                  mapping: Mapping[int, int], s_rest: list[np.ndarray], halo: HaloChange | None = None) -> None:
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
    additions: dict[tuple[int, int], list[dict]] = {}
    for container, info in asset_bundle["m_Container"]:
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
            t = {**tree(father), "m_GameObject": {"m_FileID": 0, "m_PathID": go_pid},
                 "m_LocalRotation": {"x": float(rx), "y": float(ry), "z": float(rz), "w": float(rw)},
                 "m_LocalPosition": _vector((px, py, pz)), "m_LocalScale": _vector((sx, sy, sz)),
                 "m_Children": [], "m_Father": {"m_FileID": 0, "m_PathID": father}}
            _add_with_id(writer, sf, "Transform", t, tf_pid)
            g = {**tree(father_go), "m_Name": node.name, "m_IsActive": True,
                 "m_Component": [{"component": {"m_FileID": 0, "m_PathID": tf_pid}}]}
            _add_with_id(writer, sf, "GameObject", g, go_pid)
            # a new object reads back as its template until the bundle is saved: its tree is kept here instead
            trees[tf_pid], trees[go_pid] = t, g
            tree(father)["m_Children"].append({"m_FileID": 0, "m_PathID": tf_pid})
            save(father)
            by_path[node.path] = tf_pid
            added.extend({"m_FileID": 0, "m_PathID": pid} for pid in (go_pid, tf_pid))
            return tf_pid

        main = None
        for pid in renderers:
            r = tree(pid)
            mesh = r["m_Mesh"]
            role = t_meshes.get((_cab(sf, mesh["m_FileID"]), mesh["m_PathID"])) if mesh["m_PathID"] else None
            if role is None:
                continue
            if role == "primary":
                # its materials wherever they live: a slot's body may take them from two files (Hifumi (Swimsuit))
                own = [(_cab(sf, m["m_FileID"]), m["m_PathID"]) for m in r["m_Materials"] if m["m_PathID"]]
                main_renderer = own == primary_materials
                mesh_pid, nodes = new_meshes[0]
                r["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": mesh_pid}
                r["m_Bones"] = [bone(n) for n in nodes]
                count = len(new_materials[0])
                if main_renderer:
                    r["m_Materials"] = [{"m_FileID": material_fid, "m_PathID": m} for m in new_materials[0]]
                    main = pid
                else:  # a copy (an effect's): its own materials, one per submesh
                    kept = r["m_Materials"] or [{"m_FileID": 0, "m_PathID": 0}]
                    r["m_Materials"] = (kept * count)[:count]
                r["m_RootBone"] = bone(root_node)
                r["m_AABB"] = _bounds(skins[0], s_costume[0], t_skeleton, root_node, s_rest)
            else:  # the target's other costume parts, alternates and their copies draw nothing
                r["m_Mesh"] = {"m_FileID": 0, "m_PathID": 0}
            save(pid)
        halo_added = _edit_halo(halo, by_path, objects, tree, save, mesh_fid, material_fid, sf) if halo else []
        if main is None:
            if added or halo_added:
                additions[(info["preloadIndex"], info["preloadSize"])] = list(added) + halo_added
            continue
        # further source parts: renderers of their own beside the primary
        template = tree(main)
        go_pid = template["m_GameObject"]["m_PathID"]
        go_tree = tree(go_pid)
        tf_pid = next(c["component"]["m_PathID"] for c in go_tree["m_Component"]
                      if objects[c["component"]["m_PathID"]].type.name == "Transform")
        father = tree(tf_pid)["m_Father"]["m_PathID"]
        for k in range(1, len(new_meshes)):
            mesh_pid, nodes = new_meshes[k]
            new_go, new_tf, new_r = writer.fresh(sf), None, None
            new_tf = writer.fresh(sf)
            while new_tf == new_go:
                new_tf = writer.fresh(sf)
            r = {**template}
            r["m_GameObject"] = {"m_FileID": 0, "m_PathID": new_go}
            r["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": mesh_pid}
            r["m_Materials"] = [{"m_FileID": material_fid, "m_PathID": m} for m in new_materials[k]]
            r["m_Bones"] = [bone(n) for n in nodes]
            r["m_RootBone"] = bone(root_node)
            r["m_AABB"] = _bounds(skins[k], s_costume[k], t_skeleton, root_node, s_rest)
            new_r = _add_with_id(writer, sf, "SkinnedMeshRenderer", r)
            g = {**go_tree, "m_Name": names[k], "m_IsActive": True,
                 "m_Component": [{"component": {"m_FileID": 0, "m_PathID": new_tf}},
                                 {"component": {"m_FileID": 0, "m_PathID": new_r}}]}
            _add_with_id(writer, sf, "GameObject", g, new_go)
            t = {**tree(tf_pid), "m_GameObject": {"m_FileID": 0, "m_PathID": new_go}, "m_Children": []}
            _add_with_id(writer, sf, "Transform", t, new_tf)
            tree(father)["m_Children"].append({"m_FileID": 0, "m_PathID": new_tf})
            save(father)
            added += [{"m_FileID": 0, "m_PathID": pid} for pid in (new_go, new_tf, new_r)]
            # lists of renderers that name the primary (effects, flashes) take the new part as well
            for b in behaviours:
                if _append_beside(tree(b), {"m_FileID": 0, "m_PathID": main}, {"m_FileID": 0, "m_PathID": new_r}):
                    save(b)
        key = (info["preloadIndex"], info["preloadSize"])
        additions[key] = added + [{"m_FileID": mesh_fid, "m_PathID": m} for m, _ in new_meshes] + [
            {"m_FileID": material_fid, "m_PathID": m} for ms in new_materials for m in ms] + halo_added
    _extend_preload(asset_bundle, additions)
    bundle_object.save_typetree(asset_bundle)


def _edit_halo(halo: HaloChange, by_path: Mapping[str, int], objects: Mapping[int, Any], tree: Callable[[int], dict],
               save: Callable[[int], None], mesh_fid: int, material_fid: int, sf: Any) -> list[dict]:
    """One prefab's halo changed (:class:`HaloChange`): its renderer that draws the slot's halo mesh, and the follower
    of its followed node. The nodes stay, so the clips that move or toggle them still do. Returns the new objects the
    prefab now loads (its preload entries)."""

    def components(path: str) -> list[int]:
        transform = by_path.get(path)
        if transform is None:
            return []
        go = tree(transform)["m_GameObject"]["m_PathID"]
        return [c["component"]["m_PathID"] for c in tree(go)["m_Component"]
                if c["component"]["m_FileID"] == 0 and c["component"]["m_PathID"] in objects]

    here = components(halo.path)
    filt = next((pid for pid in here if objects[pid].type.name == "MeshFilter"), None)
    renderer = next((pid for pid in here if objects[pid].type.name == "MeshRenderer"), None)
    if filt is None or renderer is None:
        return []
    mesh = tree(filt)["m_Mesh"]
    if not mesh["m_PathID"] or (_cab(sf, mesh["m_FileID"]), mesh["m_PathID"]) != halo.mesh:
        return []
    r = tree(renderer)
    if halo.new_mesh is None:  # the source has no halo: the slot's draws nothing, even where a clip turns it on
        r["m_Enabled"] = type(r["m_Enabled"])(0)
        tree(filt)["m_Mesh"] = {"m_FileID": 0, "m_PathID": 0}
        save(filt)
        save(renderer)
        return []
    tree(filt)["m_Mesh"] = {"m_FileID": mesh_fid, "m_PathID": halo.new_mesh}
    r["m_Materials"] = [{"m_FileID": material_fid, "m_PathID": m} for m in halo.materials]
    position, rotation, scale = halo.local
    transform = by_path[halo.path]
    tree(transform).update(m_LocalPosition=_vector(position), m_LocalScale=_vector(scale),
                           m_LocalRotation=dict(zip("xyzw", (float(x) for x in rotation))))
    for pid in (filt, renderer, transform):
        save(pid)
    for pid in components(halo.root):  # the FxFollower: the one behaviour there with its offsets
        if halo.follower and objects[pid].type.name == "MonoBehaviour" and all(name in tree(pid) for name in halo.follower):
            tree(pid).update(copy.deepcopy(halo.follower))
            save(pid)
    return [{"m_FileID": mesh_fid, "m_PathID": halo.new_mesh}] + [
        {"m_FileID": material_fid, "m_PathID": m} for m in halo.materials]


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


def _bounds(skin: Skin, part: Part, t_skeleton: Skeleton, root: int, s_rest: list[np.ndarray]) -> dict:
    world = t_skeleton.world()
    points = (np.c_[skin.vertices, np.ones(len(skin.vertices))] @ (np.linalg.inv(world[root]) @ s_rest[part.node]).T)[:, :3]
    lo, hi = points.min(axis=0), points.max(axis=0)
    return {"m_Center": _vector((lo + hi) / 2), "m_Extent": _vector((hi - lo) / 2)}


def _extend_preload(asset_bundle: dict, additions: Mapping[tuple[int, int], list[dict]]) -> None:
    table = asset_bundle["m_PreloadTable"]
    ranges = sorted({(i["preloadIndex"], i["preloadSize"]) for _, i in asset_bundle["m_Container"] if i["preloadSize"]})
    contiguous = sum(n for _, n in ranges) == len(table) and all(
        ranges[k][0] + ranges[k][1] == ranges[k + 1][0] for k in range(len(ranges) - 1)) and (not ranges or ranges[0][0] == 0)
    moved: dict[tuple[int, int], tuple[int, int]] = {}
    if contiguous:
        rebuilt: list[dict] = []
        for i, n in ranges:
            extra = additions.get((i, n), [])
            moved[(i, n)] = (len(rebuilt), n + len(extra))
            rebuilt.extend(table[i:i + n] + extra)
        asset_bundle["m_PreloadTable"] = rebuilt
    else:  # ranges that overlap or leave gaps: the edited prefabs get a copy of their range at the end
        for (i, n), extra in additions.items():
            moved[(i, n)] = (len(table), n + len(extra))
            table.extend(table[i:i + n] + extra)
    for _, info in asset_bundle["m_Container"]:
        key = (info["preloadIndex"], info["preloadSize"])
        if key in moved:
            info["preloadIndex"], info["preloadSize"] = moved[key]


def field_models(graph: BundleGraph, files: Iterable[Path]) -> dict[str, list[Path]]:
    """Field player models by lower-case dev name (``Field_Player_<dev>[_NonWeapon]``), with the files holding them."""
    out: dict[str, list[Path]] = {}
    for path in files:
        env = graph.add(path)
        for name in (env.container.keys() if env is not None else ()):
            stem = name.rsplit("/", 1)[-1]
            match = re.fullmatch(r"Field_Player_(.+?)(_NonWeapon)?\.prefab", stem, re.IGNORECASE)
            if match and "/character/" in name.lower():
                out.setdefault(match.group(1).lower(), [])
                if Path(path) not in out[match.group(1).lower()]:
                    out[match.group(1).lower()].append(Path(path))
    return out
