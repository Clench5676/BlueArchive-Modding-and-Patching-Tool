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
  its parent maps to, else its parent's; per Biped bone, the group of secondary bones follows the matched bones only
  while it stretches no more than the slot's own costume under the slot's clips, else it stays rigid;
- checks: every reference resolved, the rest pose reproduced, the stretch within the slot's own range. A pair that fails
  raises ``source_unsupported``.

The caller supplies official copies of the files (``adapt``'s ``index``) and saves the changed bundles.
"""

from __future__ import annotations

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
from .model import (BundleGraph, PREFAB_KINDS, _external_cab, _key, _prefabs, bind_tracks, decode_clip,
                    euler_orders, euler_to_quat, ex_only_renderers, read_mesh, shader_kind, UNITY_ORDER)

logger = logging.getLogger(__name__)

CHARACTER_KINDS = {"body", "face", "hair", "eyemouth", "eyebrow"}
# torso, limb and head bones of the Biped: a costume part skins to at least one of them
BIPED_CORE = re.compile(r"^Bip001 (Pelvis|Spine\d*|Neck|Head|[LR] (Thigh|Calf|UpperArm|Forearm|Clavicle))$")
NEAR = 0.05           # a secondary bone maps onto a slot bone at most this far from it (prefab units, metres)
STRETCH_FLOOR = 1.5   # a group may always stretch this much
# clips whose poses judge the mapping, by name suffix; the idle comes first
PROBE_CLIPS = ("Formation_Idle", "Normal_Idle", "Move_Ing", "Vital_Death", "Formation_Pickup", "Cafe_Walk")
IDLE_CLIPS = ("Formation_Idle", "Normal_Idle", "Cafe_Idle", "Stand_Idle", "Idle")
FIELD_KINDS = ("Field_Player_{}", "Field_Player_{}_NonWeapon")


def refuse(reason: str, **params: Any) -> ServiceError:
    logger.info("Costume adaptation refused: %s %s", reason, params)
    return ServiceError("source_unsupported", source=reason)


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
        out: list[np.ndarray] = []
        for i, node in enumerate(self.nodes):  # parents come first
            m = local[i] if local is not None and i in local else trs(*node.local)
            out.append(out[node.parent] @ m if node.parent is not None else m)
        return out

    def first(self, name: str) -> int | None:
        return next((i for i, node in enumerate(self.nodes) if node.name == name), None)

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
        by_hash = {zlib.crc32(node.path.encode()): i for i, node in enumerate(self.nodes)}
        out = []
        for clip in clips:
            tree = clip.read_typetree()
            decoded = decode_clip(tree)
            bindings = tree["m_ClipBindingConstant"]["genericBindings"]
            tracks = bind_tracks(bindings, decoded.curves, by_hash)
            orders = euler_orders(bindings, by_hash)
            count = len(decoded.times)
            for frame in sorted({0, count // 2, count - 1}) if frames > 1 else [0]:
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
                out.append(self.world(local))
        return out


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
    names = [(c.peek_name() or "").lower() for c in clips]
    out = []
    for suffix in suffixes:
        hit = next((c for c, n in zip(clips, names) if n.endswith("_" + suffix.lower())), None)
        if hit is not None and hit not in out:
            out.append(hit)
    return out


# --- meshes --------------------------------------------------------------------------------------------------------

@dataclass
class Skin:
    vertices: np.ndarray  # in the renderer's mesh space
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray


def skin_of(mesh: Any) -> Skin:
    data = read_mesh(mesh.read())
    vertices = np.array(data.vertices, float).reshape(-1, 3)
    if not data.joints:
        raise refuse("unskinned costume part")
    tris = np.array([t for sub in data.triangles for t in sub], int).reshape(-1, 3)
    edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
    return Skin(vertices, np.array(data.joints, int), np.array(data.weights, float), edges)


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


def map_bones(target: Skeleton, source: Skeleton, bones: Iterable[int]) -> dict[int, int]:
    """Source node -> target node for every source bone and its ancestors, before the per-group choice."""
    rest_t, rest_s = target.world(), source.world()
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
        anchor_name = source.nodes[anchor].name if anchor is not None else None
        position = rest_s[i][:3, 3]
        best, distance = None, NEAR
        for c in [parent, *target.descendants(parent)]:
            if target.nodes[c].name.startswith("Bip001"):
                continue
            a = target.biped_ancestor(c)
            if (target.nodes[a].name if a is not None else None) != anchor_name:
                continue
            d = float(np.linalg.norm(rest_t[c][:3, 3] - position))
            if d < distance:
                best, distance = c, d
        mapping[i] = best if best is not None else parent
    return mapping


@dataclass
class Plan:
    mapping: dict[int, int]
    choices: dict[str, str]  # Biped bone -> "follow" | "rigid"
    stretch: float
    own: float


def plan(target: Costume, source: Costume, probes: list[list[np.ndarray]]) -> Plan:
    """The bone mapping with each group's choice, and the checks of the rest pose and the stretch."""
    t_rest, s_rest = target.skeleton.world(), source.skeleton.world()
    mapping = map_bones(target.skeleton, source.skeleton, source.bones())
    # every part's vertices in the root's space, with its bones as source node indices
    s_vertices, s_joints, s_weights, s_edges, offset = [], [], [], [], 0
    for part, skin in zip(source.parts, source.skins):
        world = s_rest[part.node]
        s_vertices.append((np.c_[skin.vertices, np.ones(len(skin.vertices))] @ world.T)[:, :3])
        s_joints.append(np.array([[part.bones[j] if part.bones[j] is not None else -1 for j in row] for row in skin.joints]))
        s_weights.append(skin.weights)
        s_edges.append(skin.edges + offset)
        offset += len(skin.vertices)
    sv, sj, sw, se = np.vstack(s_vertices), np.vstack(s_joints), np.vstack(s_weights), np.vstack(s_edges)
    if (sj[sw > 0] < 0).any():
        raise refuse("costume bone outside the prefab")
    t_vertices, t_matrices_rest, t_edges, offset, t_dom = [], [], [], 0, []
    for part, skin in zip(target.parts, target.skins):
        bp = bindposes(target.skeleton.graph, part.mesh)
        bones = [b if b is not None else 0 for b in part.bones]
        world = np.array([t_rest[b] for b in bones]) @ bp
        rest = deform(skin, world)
        t_vertices.append(rest)
        t_edges.append(skin.edges + offset)
        t_dom.append(np.array([bones[row[np.argmax(w)]] for row, w in zip(skin.joints, skin.weights)]))
        t_matrices_rest.append((part, skin, bp, bones))
        offset += len(skin.vertices)
    tv, te, tdom = np.vstack(t_vertices), np.vstack(t_edges), np.concatenate(t_dom)
    sdom = np.array([row[np.argmax(w)] for row, w in zip(sj, sw)])
    nodes = sorted(set(sj[sw > 0].tolist()))
    index = {n: k for k, n in enumerate(nodes)}
    joints = np.vectorize(lambda n: index.get(n, 0))(np.where(sj < 0, nodes[0], sj))
    flat = Skin(sv, joints, sw, se)

    def matrices(pose: list[np.ndarray], m: Mapping[int, int]) -> np.ndarray:
        return np.array([pose[m[n]] @ np.linalg.inv(t_rest[m[n]]) for n in nodes])

    def target_posed(pose: list[np.ndarray]) -> np.ndarray:
        return np.vstack([deform(skin, np.array([pose[b] for b in bones]) @ bp)
                          for _, skin, bp, bones in t_matrices_rest])

    groups: dict[int, list[int]] = {}
    for n in nodes:
        if not source.skeleton.nodes[n].name.startswith("Bip001"):
            anchor = source.skeleton.biped_ancestor(n)
            if anchor is not None:
                groups.setdefault(anchor, []).append(n)
    choices = {}
    posed_targets = [target_posed(p) for p in probes]
    for anchor, members in groups.items():
        rigid = mapping[anchor]
        mask = np.isin(sdom, members)
        mask = mask[se[:, 0]] & mask[se[:, 1]]
        name = source.skeleton.nodes[anchor].name
        if not probes or not mask.any():
            follow = False
        else:
            t_members = [j for j in set(tdom.tolist()) if not target.skeleton.nodes[j].name.startswith("Bip001")
                         and target.skeleton.biped_ancestor(j) is not None
                         and target.skeleton.nodes[target.skeleton.biped_ancestor(j)].name == name]
            t_mask = np.isin(tdom, t_members)
            t_mask = t_mask[te[:, 0]] & t_mask[te[:, 1]]
            own = max((stretch(tv, posed, te, t_mask) for posed in posed_targets), default=1.0) if t_mask.any() else 1.0
            mine = max(stretch(sv, deform(flat, matrices(p, mapping)), se, mask) for p in probes)
            follow = mine <= max(STRETCH_FLOOR, own)
        choices[name] = "follow" if follow else "rigid"
        if not follow:
            for n in members:
                mapping[n] = rigid
    # the checks: the rest pose reproduced, the stretch within the target's own range
    rest = deform(flat, matrices(t_rest, mapping))
    if float(np.abs(rest - sv).max()) > 1e-4:
        raise refuse("rest pose not reproduced")
    mine = max((stretch(sv, deform(flat, matrices(p, mapping)), se) for p in probes), default=1.0)
    own = max((stretch(tv, posed, te) for posed in posed_targets), default=1.0)
    if mine > max(1.5 * own, own + 1.0):
        raise refuse("stretch", adapted=round(mine, 2), own=round(own, 2))
    return Plan(mapping, choices, mine, own)


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
          source_files: list[Path], source_dev: str, open_writable: Callable[[Path], Any]) -> Result:
    """The changed bundles of ``target_files`` (the slot, official copies) carrying the costume of ``source_dev``'s
    model, whose prefab sits in ``source_files``. ``index`` maps CABs to files to read (the slot's own CABs to
    ``target_files``); ``resolver`` finds any other CAB. ``open_writable`` opens a slot file as an engine Bundle."""
    graph = BundleGraph(index, resolver=resolver)
    target_prefabs = _model_prefabs(graph, [f for f in target_files if f.name.startswith("character-")])
    source_prefabs = _model_prefabs(graph, source_files)
    t_name = choose_prefab(target_prefabs, target_dev)
    s_name = choose_prefab(source_prefabs, source_dev)
    t_skeleton = Skeleton.of(graph, target_prefabs[t_name].deref())
    s_skeleton = Skeleton.of(graph, source_prefabs[s_name].deref())
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
    probes = t_skeleton.poses(pick(t_clips, PROBE_CLIPS))
    result = plan(target, source, probes)
    t_rest, s_rest = t_skeleton.world(), s_skeleton.world()

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
    needed = {mesh_cab, material_cab, texture_cab} | {
        cab for cab, path in slot.items() if Path(path) in prefab_files}
    if None in needed or not needed <= set(slot):
        raise refuse("costume outside the slot")
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
        path = index.get(cab) or (graph.locations.get(cab))
        if cab in slot:
            return True
        return path is not None and Path(path).name.startswith(("prologdepengroup-", "prologgroup-"))

    # textures and materials
    textures: dict[tuple[str, int], int] = {}
    materials: dict[tuple[str, int], int] = {}
    for part in s_costume:
        for material in part.materials:
            if material is None or _key(material) in materials:
                continue
            tree = material.read_typetree()
            shader_cab = _external_cab(material, tree["m_Shader"]["m_FileID"])
            if tree["m_Shader"]["m_PathID"] and not shared(shader_cab):
                raise refuse("shader outside the always-loaded bundles")
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

    # meshes: the source parts' meshes with bindposes on the target skeleton
    new_meshes = []
    for part, skin in zip(s_costume, source.skins):
        tree = part.mesh.read_typetree()
        nodes = [result.mapping.get(b, 0) if b is not None else 0 for b in part.bones]
        poses = [np.linalg.inv(t_rest[n]) @ s_rest[part.node] for n in nodes]
        tree["m_BindPose"] = [_matrix(m) for m in poses]
        tree["m_BoneNameHashes"] = [zlib.crc32(t_skeleton.nodes[n].path.encode()) for n in nodes]
        tree["m_RootBoneNameHash"] = graph.tree(primary.mesh)["m_RootBoneNameHash"]
        for i, pose in enumerate(poses):
            hit = ((skin.joints == i) & (skin.weights > 0)).any(axis=1)
            if hit.any() and i < len(tree["m_BonesAABB"]):
                local = (np.c_[skin.vertices[hit], np.ones(hit.sum())] @ pose.T)[:, :3]
                tree["m_BonesAABB"][i] = {"m_Min": _vector(local.min(axis=0)), "m_Max": _vector(local.max(axis=0))}
        new_meshes.append((writer.add(mesh_sf, "Mesh", tree), nodes))

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
                              source.skins, result.mapping, s_rest)
    logger.info("Costume of %s on %s: %d part(s), groups %s, stretch %.2f (own %.2f)", s_name, t_name,
                len(s_costume), result.choices, result.stretch, result.own)
    return Result(opened, result, len(s_costume))


def _edit_prefabs(writer: _Writer, sf: Any, graph: BundleGraph, t_meshes: Mapping[tuple[str, int], str],
                  primary_materials: list[tuple[str, int]], mesh_cab: str, material_cab: str,
                  new_meshes: list[tuple[int, list[int]]], new_materials: list[list[int]], names: list[str],
                  root_node: int, t_skeleton: Skeleton, s_costume: list[Part], skins: list[Skin],
                  mapping: Mapping[int, int], s_rest: list[np.ndarray]) -> None:
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

        def bone(n: int) -> dict:
            pid = by_path.get(t_skeleton.nodes[n].path)
            if pid is None:
                raise refuse("bone missing in a prefab", bone=t_skeleton.nodes[n].path)
            return {"m_FileID": 0, "m_PathID": pid}
        added: list[dict] = []
        main = None
        for pid in renderers:
            r = tree(pid)
            mesh = r["m_Mesh"]
            role = t_meshes.get((_cab(sf, mesh["m_FileID"]), mesh["m_PathID"])) if mesh["m_PathID"] else None
            if role is None:
                continue
            if role == "primary":
                own = [(material_cab, m["m_PathID"]) for m in r["m_Materials"] if m["m_FileID"] == material_fid]
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
        if main is None:
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
            {"m_FileID": material_fid, "m_PathID": m} for ms in new_materials for m in ms]
    _extend_preload(asset_bundle, additions)
    bundle_object.save_typetree(asset_bundle)


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
