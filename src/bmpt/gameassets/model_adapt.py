"""Put another variant's costume on a student's 3D model slot.

A student's model slot (the ``character-<dev>-_mxload-*`` prefabs and their own dependency bundles) gets the costume of
another model: its skinned costume meshes with bindposes rebound onto the slot's skeleton, its materials and its
textures, all added as new objects to the slot's own bundles; the slot's prefabs then draw them in place of their own
costume. What exists changes only where the slot's prefabs need it (their renderers, transforms, behaviours and preload
table) and, with Rule B, in the slot's own clips, which gain curves for the new skirt bones and retain every curve
(read back value for value, :func:`clip_edit_error`): field, cutscene and other variants' bundles reference the slot's
objects, and a prefab of theirs that plays such a clip has no node at the new curves' paths, which Unity leaves unbound.

Everything is decided from data, never from names a character's artist chose, so new characters need no rules of their
own:

- renderer roles (:func:`roles`): shading of the materials, membership of the main ``Bip001`` rig, visibility at rest
  and in the idle clip;
- bone mapping (:func:`map_bones`): Biped bones (``Bip001 ...``, the names every clip and IK component uses) by name;
  every other bone by structure: the nearest slot bone hanging off the same Biped bone, in the chain
  its parent maps to, else its parent's; never a bone the slot's clips hide by scaling it to nothing. A missing
  Biped bone, or a bone outside the Biped, follows the Biped bone that moves it alike under the source's own clips
  (:func:`carriers`: a Battle NPC's ForeTwist with the forearm, a bag's strap with the body). Per Biped bone, the group
  of secondary bones follows the matched bones only while it stretches no more than the slot's own costume under the
  slot's clips, else it stays rigid;
- the shape (:func:`plan`): the costume as its vertex data stores it, as its prefab draws it at rest (by its own
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
- the mouth (:func:`mouth_of`): the slot's mouth receivers index the renderer's replacement materials, which are the
  source's, so they take the source receiver's material index and default tile, and point at the part added for the
  source's mouth where that is not on the primary. The slot's clips name mouth tiles as cells of the slot's own mouth
  atlas: a source eye-mouth material drawing another atlas (another texture, scale or grid: Hina's 4 x 4 atlas, the
  shared 8 x 8 one) takes the slot's, and the receivers keep the slot's default tile (:func:`retiled_mouths`);
- the halo (:func:`halo_of`): after any swap the halo is the source's or none, never the slot's own
  (``adapt``'s ``halo``). The source's halo mesh, materials and textures are added like the costume's and the slot's
  halo renderer draws them where the source draws its own: a followed halo (one unskinned renderer below an
  ``FxFollower``) with its follower offset taken from the source, a halo skinned to one bone below the head (NPC asset
  models such as Ayame's) at that bone's rest place, the follower set to keep it there. A followed halo of any other
  shape (particles: Tsurugi's, Mika's), or one going onto such a slot, is carried with its nodes: what the slot's halo
  draws is hidden and the source's nodes below its followed node become new nodes below the slot's, with their
  renderers and particle systems (:func:`_halo_transplant`). Every other case (a source without a halo, a boss's
  halo, anything below the followed node that points elsewhere) hides everything the slot's halo draws.

The caller supplies official copies of the files (``adapt``'s ``index``) and saves the changed bundles.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import logging
import math
import random
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from ..core.errors import ServiceError
from ..core.progress_meter import QUIET, Meter
from . import model_geometry as mg
from . import model_hems as hm
from . import model_skirts as ms
from .errors import AssetNotFound
from .costume_cache import PoseStore, clip_digest, decode_clip, memoize_pose
from .unity import load_bundle
from .model import (
    BundleGraph,
    NPC_PREFAB,
    PREFAB_KINDS,
    _external_cab,
    _key,
    _prefabs,
    ex_only_renderers,
    model_root,
    shader_kind,
)
from .model_glb import bind_tracks, euler_orders, euler_to_quat, read_mesh, UNITY_ORDER

if TYPE_CHECKING:
    from .model_props import WornStore

logger = logging.getLogger(__name__)

CHARACTER_KINDS = {"body", "face", "hair", "eyemouth", "eyebrow"}
# torso, limb and head bones of the Biped: a costume part skins to at least one of them
BIPED_CORE = re.compile(r"^Bip001 (Pelvis|Spine\d*|Neck|Head|[LR] (Thigh|Calf|UpperArm|Forearm|Clavicle))$")
# the Biped's body chain, hands and feet with their fingers and toes: the bones a costume bone may be carried by
# (never a weapon, prop or eye bone a vendor named Bip001 too)
BIPED_CHAIN = re.compile(
    r"^Bip001 (Pelvis|Spine\d*|Neck\d*|Head|[LR] (Clavicle|UpperArm|Forearm|Hand|Thigh|Calf|Foot|"
    r"Finger\d+|Toe\d+))$"
)
BIPED_ROOT = "Bip001"
NEAR = 0.05  # a secondary bone maps onto a slot bone at most this far from it (prefab units, metres)
STRETCH_FLOOR = 1.5  # a group may always stretch this much
# how far a costume may stretch under the slot's clips past the slot's own costume (max(1.5 x own, own + 1)), with a
# tenth more allowed (pairs up to 1.1 times the limit, such as Momoi <- Momoi (Battle) at 1.01)
STRETCH_TOLERANCE = 1.1


def stretch_limit(own: float) -> float:
    """Allow 1.1 times the greater of 1.5 times the slot's own stretch ratio or that ratio plus one."""
    return STRETCH_TOLERANCE * max(1.5 * own, own + 1.0)


# Candidate shapes, in judging order: stored vertex data, prefab rest appearance, translated joints, refitted joints.
SHAPES = ("stored", "drawn", "moved", "refitted")
SHAPE_MARGIN = 0.02  # a later shape is taken when it stretches this share less
CARRIER_SAMPLES = 200  # vertices measured, about, per unmatched bone
CARRIER_SHARE = 0.5  # a carrier moves an unmatched bone's vertices at most this much as far off as its parent does
HIDDEN = 1e-6  # a bone a pose scales to this share of its rest volume or less is hidden

# clips whose poses judge the mapping, by name suffix; the idle comes first
PROBE_CLIPS = ("Formation_Idle", "Normal_Idle", "Move_Ing", "Vital_Death", "Formation_Pickup", "Cafe_Walk")
IDLE_CLIPS = ("Formation_Idle", "Normal_Idle", "Cafe_Idle", "Stand_Idle", "Idle")
FIELD_KINDS = ("Field_Player_{}", "Field_Player_{}_NonWeapon")
PROBE_SCAN = 24  # clips looked at, at most, for the probes
# the sanity pass over what a run writes
CONDITION_LIMIT = 1e8  # a rest matrix whose linear part is worse conditioned than this is singular
BINDPOSE_LIMIT = 1e3  # a bindpose entry larger than this (in magnitude) is out of range
VERTEX_LIMIT = 50.0  # a written vertex farther than this from the prefab's root bone (metres) is out of range
WEIGHT_TOLERANCE = 1e-3  # a vertex's skin weights add up to 1 within this


def refuse(reason: str, **params: Any) -> ServiceError:
    """Log a failed costume check and construct its source_unsupported error.

    Args:
        reason: Failed rule/check name used as the error's source and diagnostic message.
        **params: Check details logged directly and stringified into structured error parameters.
    """
    logger.info("Costume adaptation refused: %s %s", reason, params)
    return ServiceError("source_unsupported", source=reason, **{key: str(value) for key, value in params.items()})


def _inverse(matrix: np.ndarray, bone: str) -> np.ndarray:
    """The inverse of a rest matrix.

    A singular or nearly singular one (a bone at no volume) is a refusal, not a program error or bindposes of 1e4 and
    more.
    """
    linear = matrix[:3, :3]
    if not np.isfinite(matrix).all():
        raise refuse("singular bone", bone=bone)
    try:
        if not np.linalg.cond(linear) <= CONDITION_LIMIT:
            raise refuse("singular bone", bone=bone)
        return np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        raise refuse("singular bone", bone=bone) from None


def _shader_name(shader: Any) -> str:
    try:
        return shader.read_typetree()["m_ParsedForm"]["m_Name"] or shader.peek_name()
    except Exception:  # the name is detail for the diagnostics only
        return shader.peek_name() or ""


def loaded_with(graph: BundleGraph, prefab_files: Iterable[Path], slot: Mapping[str, str]) -> set[str]:
    """Follow the CAB dependencies loaded with a target prefab through available serialized files.

    Catalog dependencies come from these external references; slot files are opened to follow them.

    Args:
        graph: Graph containing any shared files already loaded, extended with all slot files.
        prefab_files: Target prefab bundle paths defining the starting CABs.
        slot: Lower-case slot CAB names mapped to readable file paths.
    """
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
        for entry in getattr(sf, "externals", None) or []:
            todo.append(entry.path.replace("\\", "/").rsplit("/", 1)[-1].lower())
    return seen


# --- skeleton ------------------------------------------------------------------------------------------------------


def trs(p: Iterable[float], r: Iterable[float], s: Iterable[float]) -> np.ndarray:
    """Build a Unity-space 4 by 4 affine transform.

    Args:
        p: Three-component local position in Unity units.
        r: Local (x, y, z, w) quaternion, normalized before matrix construction.
        s: Three-component local axis scale.
    """
    x, y, z, w = r
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    rot = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    m = np.eye(4)
    m[:3, :3] = rot * np.array(list(s))
    m[:3, 3] = list(p)
    return m


@dataclass
class Node:
    """Record a prefab node's binding path, parent, local TRS, Unity readers, and inherited activity."""

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
    _names: tuple[int, dict[str, list[int]]] = field(default=(0, {}), repr=False)  # node count, nodes by name
    _rig: int | None = field(default=None, repr=False)  # the main Biped (``main_rig``), -1 for none
    pose_store: PoseStore | None = field(default=None, repr=False)
    dev: str = ""

    @classmethod
    def of(cls, graph: BundleGraph, root_go: Any) -> "Skeleton":
        """Read a prefab hierarchy, skinned renderers, and behaviours from its root.

        Args:
            graph: Dependency graph resolving component and child pointers.
            root_go: Root GameObject reader whose transform defines the traversal start.
        """
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
        self.nodes.append(
            Node(
                go["m_Name"],
                path,
                parent,
                ((p["x"], p["y"], p["z"]), (r["x"], r["y"], r["z"], r["w"]), (s["x"], s["y"], s["z"])),
                go_obj,
                transform,
                here,
            )
        )
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
        """Compose node world matrices, using rest locals where no override is supplied.

        Args:
            local: Optional node-index map of local 4 by 4 transforms; None returns the rest world pose.
        """
        if len(self._rest) != len(self.nodes):
            self._rest = [trs(*node.local) for node in self.nodes]
        out: list[np.ndarray] = []
        for i, node in enumerate(self.nodes):  # parents come first
            m = local[i] if local is not None and i in local else self._rest[i]
            out.append(out[node.parent] @ m if node.parent is not None else m.copy())
        return out

    def named(self, name: str) -> list[int]:
        """Return indices of nodes with an exact name in hierarchy order."""
        count, names = self._names
        if count != len(self.nodes):
            names = {}
            for i, node in enumerate(self.nodes):
                names.setdefault(node.name, []).append(i)
            self._names = (len(self.nodes), names)
        return names.get(name, [])

    def main_rig(self) -> int | None:
        """Choose the Bip001 rig containing the most renderer bones, preferring the first on ties.

        Prefabs can have separate halo, body, and cut-in rigs, as in Haruna (Track).
        Return None when no Bip001 node exists.
        """
        if self._rig is None:
            rigs = self.named(BIPED_ROOT)
            best = rigs[0] if rigs else -1
            if len(rigs) > 1:
                g = self.graph
                counts = dict.fromkeys(rigs, 0)
                for _, renderer in self.renderers:
                    for pointer in g.tree(renderer)["m_Bones"]:
                        bone = g.deref(renderer, pointer)
                        n = self.by_transform.get(_key(bone)) if bone is not None else None
                        if n is None:
                            continue
                        for r in rigs:
                            if n == r or self.nodes[n].path.startswith(self.nodes[r].path + "/"):
                                counts[r] += 1
                best = max(rigs, key=lambda r: (counts[r], -rigs.index(r)))
            self._rig = best
        return self._rig if self._rig >= 0 else None

    def first(self, name: str) -> int | None:
        """Find an exact node name, preferring the main rig among duplicates, or None if absent."""
        hits = self.named(name)
        if len(hits) > 1 and len(self.named(BIPED_ROOT)) > 1 and (rig := self.main_rig()) is not None:
            prefix = self.nodes[rig].path + "/"
            inside = next((i for i in hits if i == rig or self.nodes[i].path.startswith(prefix)), None)
            if inside is not None:
                return inside
        return hits[0] if hits else None

    def chain_ancestor(self, i: int) -> int | None:
        """Return node i's nearest strict ancestor matching BIPED_CHAIN, or None."""
        p = self.nodes[i].parent
        while p is not None and not BIPED_CHAIN.match(self.nodes[p].name):
            p = self.nodes[p].parent
        return p

    def biped_ancestor(self, i: int) -> int | None:
        """Return node i's nearest strict ancestor named with the Bip001 prefix, or None."""
        p = self.nodes[i].parent
        while p is not None and not self.nodes[p].name.startswith("Bip001"):
            p = self.nodes[p].parent
        return p

    def descendants(self, i: int) -> list[int]:
        """Return strict descendants of node i in hierarchy order, using binding-path prefixes."""
        prefix = self.nodes[i].path + "/"
        return [j for j, node in enumerate(self.nodes) if node.path.startswith(prefix) or (i == 0 and j > 0)]

    def poses(self, clips: list[Any], frames: int = 3) -> list[list[np.ndarray]]:
        """Collect world poses from each clip, retaining rest values where curves are absent.

        Args:
            clips: AnimationClip readers to sample in their existing order.
            frames: Values above one select first/middle/last frames; otherwise only the first frame is used.
        """
        return [pose for clip in clips for pose in self.clip_poses(clip, None if frames > 1 else 1)[0]]

    def clip_poses(self, clip: Any, frames: int | None = None) -> tuple[list[list[np.ndarray]], set[int]]:
        """Sample world poses from a clip and report its animated nodes.

        Args:
            clip: AnimationClip reader resolved through this skeleton's graph.
            frames: Number of evenly spaced samples; None selects unique first, middle, and last frames.

        Returns:
            Sampled node world-matrix lists and the set of nodes with bound Transform curves.
        """
        token = None
        if self.pose_store is not None and (frames is None or frames <= 3):
            digest = hashlib.sha256(repr([(n.path, n.parent, n.local) for n in self.nodes]).encode())
            digest.update(clip.get_raw_data())
            token = f"{frames}|{digest.hexdigest()}"
            found = self.pose_store.get(self.dev, token)
            if found is not None and found[0].shape[1] == len(self.nodes):
                poses, animated = found
                return [list(p) for p in poses], set(animated.tolist())
        times, pose, animated = self.clip_frames(self.graph.tree(clip))
        count = len(times)
        if frames is None:
            chosen = sorted({0, count // 2, count - 1})
        else:
            chosen = sorted({min(count - 1, round(k * (count - 1) / max(1, frames - 1))) for k in range(frames)})
        poses = [pose(frame) for frame in chosen]
        if token is not None:
            self.pose_store.put(self.dev, token, np.asarray(poses), np.array(sorted(animated), dtype=np.int64))
        return poses, animated

    def clip_frames(
        self, tree: Mapping[str, Any], nodes: Iterable[int] | None = None, *, cache: bool = False
    ) -> tuple[list[float], Callable[[int], list[np.ndarray]], set[int]]:
        """Prepare frame times, a world-pose callback, and the set of animated nodes.

        With ``nodes``, only those nodes and their ancestors are posed;
        other entries are None. This keeps a face-only pass from posing the body, props and new costume bones.
        ``cache`` shares numerical frames in the bounded pose memory, keyed by revision and all pose inputs.

        Args:
            tree: AnimationClip typetree with generic Transform bindings and muscle curves.
            nodes: Optional node indices needed by the caller; unneeded pose entries are None.
            cache: Whether to share independently mutable numerical frames in the bounded memory cache.
        """
        needed = set(range(len(self.nodes))) if nodes is None else set(nodes)
        for i in list(needed):
            parent = self.nodes[i].parent
            while parent is not None and parent not in needed:
                needed.add(parent)
                parent = self.nodes[parent].parent
        by_hash = {zlib.crc32(node.path.encode()): i for i, node in enumerate(self.nodes)}
        by_hash = {path: i for path, i in by_hash.items() if i in needed}
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
            if nodes is None:
                return self.world(local)
            if len(self._rest) != len(self.nodes):
                self._rest = [trs(*node.local) for node in self.nodes]
            world = [None] * len(self.nodes)
            for i in sorted(needed):
                node = self.nodes[i]
                m = local.get(i, self._rest[i])
                world[i] = world[node.parent] @ m if node.parent is not None else m.copy()
            return world

        if cache:
            indices = tuple(sorted(needed)) if nodes is not None else None
            revision = self.pose_store.revisions.get(self.dev) if self.pose_store is not None else None
            digest = hashlib.sha256(
                repr(
                    (self.dev, revision, [(n.path, n.parent, n.local) for n in self.nodes], bindings, indices)
                ).encode()
            )
            digest.update(clip_digest(tree).encode())
            pose = memoize_pose(pose, digest.hexdigest(), indices, len(self.nodes))
        return decoded.times, pose, set(tracks)


# --- renderer roles ------------------------------------------------------------------------------------------------


@dataclass
class Part:
    """Describe a skinned renderer's mesh, materials, skeleton bones, submeshes, and costume role."""

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
    """Classify skinned renderers by shading, rig membership, and rest/idle visibility.

    Args:
        skeleton: Prefab hierarchy and graph supplying skinned renderers, bones, and materials.
        idle: First idle-frame world matrices; None skips the test that makes wholly scaled-away parts props.
    """
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
        bones = [
            skeleton.by_transform.get(_key(t)) if t is not None else None
            for t in (g.deref(renderer, b) for b in tree["m_Bones"])
        ]
        counts = [int(s.get("indexCount", 0)) for s in subs]
        parts.append(Part(node, renderer, mesh, materials, kinds, bones, sum(counts), list(zip(kinds, counts))))
    visible = [
        p for p in parts if p.mesh is not None and skeleton.nodes[p.node].active and g.tree(p.renderer)["m_Enabled"]
    ]
    shown = {id(p) for p in visible}
    aside = ex_only_renderers((p.node, skeleton.nodes[p.node].path, p.subparts) for p in visible)
    for p in parts:
        kinds = set(p.kinds)
        bones = [b for b in p.bones if b is not None]
        in_main = sum(
            1
            for b in bones
            if main_path is not None
            and (skeleton.nodes[b].path == main_path or skeleton.nodes[b].path.startswith(main_path + "/"))
        )
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
    """Collect field-controller clips, otherwise the clips of the model's prefab Animators.

    Args:
        graph: Dependency graph resolving controllers and effective override/original clips.
        skeleton: Chosen prefab hierarchy whose FieldAnimancerController takes precedence when present.
        prefabs: One model's prefab stems mapped to root pointers for Animator traversal.
    """
    for behaviour in skeleton.behaviours:
        if graph.script(behaviour) == "FieldAnimancerController":
            return [
                c
                for c in (graph.deref(behaviour, p) for p in graph.tree(behaviour).get("animationClips", []))
                if c is not None
            ]
    clips, seen = [], set()
    for pointer in prefabs.values():
        animator = graph.component(pointer.deref(), "Animator")
        controller = graph.deref(animator, graph.tree(animator)["m_Controller"]) if animator is not None else None
        if controller is None:
            continue
        tree = graph.tree(controller)
        if controller.type.name == "AnimatorOverrideController":
            pointers = [
                c["m_OverrideClip"] if c["m_OverrideClip"].get("m_PathID") else c["m_OriginalClip"]
                for c in tree["m_Clips"]
            ]
        else:
            pointers = tree.get("m_AnimationClips", [])
        for pointer_ in pointers:
            clip = graph.deref(controller, pointer_)
            if clip is not None and _key(clip) not in seen:
                seen.add(_key(clip))
                clips.append(clip)
    return clips


def pick(clips: list[Any], suffixes: Iterable[str]) -> list[Any]:
    """Pick the first clip for each suffix, preferring <model>_<suffix> over a bare Battle NPC state name.

    Args:
        clips: Available AnimationClip readers, with earlier matching names preferred.
        suffixes: State suffixes to match without case sensitivity; repeated readers are returned only once.
    """
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
    """Choose first/middle/last world poses from clips that move the skeleton's Biped.

    Prefer named suffixes that move the Biped. If none does, inspect the first PROBE_SCAN
    other clips and keep up to the suffix count. Battle NPC named states can be empty
    while their own clips carry the character's name.

    Args:
        skeleton: Model hierarchy whose Biped movement determines whether a clip contributes probes.
        clips: Available model-owned AnimationClip readers.
        suffixes: Preferred state suffixes and maximum fallback clip count.
    """
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
    """Hold mesh-space vertex positions, influence columns/weights, unique edges, and optional triangles."""

    vertices: np.ndarray  # in the renderer's mesh space
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray
    triangles: np.ndarray | None = None


def skin_of(mesh: Any) -> Skin:
    """Decode a Mesh reader's skin and unique triangle edges.

    Args:
        mesh: Unity Mesh reader with bone-index and weight channels.

    Raises:
        ServiceError: The mesh has no skin (source_unsupported).
    """
    data = read_mesh(mesh.read())
    vertices = np.array(data.vertices, float).reshape(-1, 3)
    if not data.joints:
        raise refuse("unskinned costume part")
    tris = np.array([t for sub in data.triangles for t in sub], int).reshape(-1, 3)
    edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
    return Skin(vertices, np.array(data.joints, int), np.array(data.weights, float), edges, tris)


def bindposes(graph: BundleGraph, mesh: Any) -> np.ndarray:
    """Read a Mesh's bind matrices as bone-by-4-by-4 arrays through the supplied bundle graph."""
    return np.array([[[b[f"e{r}{c}"] for c in range(4)] for r in range(4)] for b in graph.tree(mesh)["m_BindPose"]])


def deform(skin: Skin, matrices: np.ndarray) -> np.ndarray:
    """Skin mesh vertices through influence-indexed 4 by 4 bone matrices."""
    homogeneous = np.c_[skin.vertices, np.ones(len(skin.vertices))]
    out = np.zeros((len(skin.vertices), 3))
    for k in range(skin.joints.shape[1]):
        out += skin.weights[:, k : k + 1] * np.einsum("nij,nj->ni", matrices[skin.joints[:, k]], homogeneous)[:, :3]
    return out


def stretch(rest: np.ndarray, posed: np.ndarray, edges: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Measure the 99.9th percentile symmetric edge stretch, counting shrinkage by its inverse.

    Args:
        rest: Reference vertex positions in the same units as posed.
        posed: Corresponding deformed positions.
        edges: Vertex-index pairs to measure, ignoring degenerate reference edges.
        mask: Optional boolean selector over edges; None measures every edge.

    Returns:
        Stretch ratio, with one meaning unchanged or no measurable edge.
    """
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
        """Return sorted unique skeleton nodes referenced by the costume parts' skin bones."""
        return sorted({b for p in self.parts for b in p.bones if b is not None})


def unmatched(target: Skeleton, source: Skeleton, flat: Flat) -> list[int]:
    """Find source Biped bones missing by name on the slot and bones outside the Biped.

    A bag strap below the prefab root has no Biped ancestor and needs a carrier too.

    Args:
        target: Slot hierarchy searched for corresponding Biped names.
        source: Costume hierarchy supplying bone ancestry and names.
        flat: Flattened costume whose positive-weight influences identify used bones and anchors.
    """
    out = set()
    for n in set(flat.joints[flat.weights > 0].tolist()) - {-1}:
        anchor = source.biped_ancestor(n)
        if not source.nodes[n].name.startswith("Bip001") and anchor is None and source.nodes[n].parent is not None:
            out.add(n)
        for b in (n, anchor):
            if (
                b is not None
                and source.nodes[b].name.startswith("Bip001")
                and target.first(source.nodes[b].name) is None
            ):
                out.add(b)
    return sorted(out)


def carriers(target: Skeleton, source: Skeleton, flat: Flat, poses: list[list[np.ndarray]]) -> dict[int, int]:
    """Choose matched source bones that best carry unmatched costume branches under source clips.

    Find the source bone the slot has by name that moves unmatched vertices
    most alike under the source's own clips (``poses``): a Biped's ForeTwist hangs off the UpperArm but turns with the
    forearm, and where the slot has no such bone the costume's sleeve goes with the forearm, not with the upper arm;
    a bag's strap keyed to the body goes with the body. Measured on the vertices it or its secondary bones dominate
    (else its own frame): the least mean distance between where the two carry them, which must be under
    ``CARRIER_SHARE`` of the distance from where its parent carries them (else it stays with its parent).
    A bone outside the Biped is judged with its whole branch (``group``: Sena (Casual)'s bag under a prop node the slot
    lacks), against the nearest ancestor the slot has, where the mapping would leave it; the result is keyed by the
    branch's top node.

    Args:
        target: Slot skeleton defining which Biped carrier names are available.
        source: Incoming costume skeleton whose unmatched branches need carriers.
        flat: Source prefab-rest geometry and skeleton-node influences used for distance measurements.
        poses: Source-owned world poses used to compare branch movement with candidate carriers.
    """
    missing = unmatched(target, source, flat)
    if not missing or not poses:
        return {}
    rest = source.world()
    present = [
        i for i, node in enumerate(source.nodes) if BIPED_CHAIN.match(node.name) and target.first(node.name) is not None
    ]
    if not present:
        return {}
    dominant = flat.joints[np.arange(len(flat.vertices)), np.argmax(flat.weights, axis=1)]

    def group(n: int) -> int:
        """The group a bone stands for.

        A Biped bone stands for itself; a bone outside the Biped for its branch: the node right under the nearest
        ancestor the slot has by name (a prop's whole chain, never torn across the body).
        """
        return n if source.nodes[n].name.startswith("Bip001") else _branch_top(target, source, n)

    groups = sorted({group(m) for m in missing})
    anchor = {
        n: (group(n) if n in missing else n if source.nodes[n].name.startswith("Bip001") else source.biped_ancestor(n))
        for n in set(dominant.tolist()) - {-1}
    }
    # a Biped bone against its parent (a finger's last joint bends with the one before); a prop's branch against
    # where the mapping would leave it, the nearest ancestor the slot has
    parents = {
        m: source.nodes[m].parent if source.nodes[m].name.startswith("Bip001") else _kept_ancestor(target, source, m)
        for m in groups
    }
    inverse = {i: np.linalg.inv(rest[i]) for i in {*present, *groups, *(p for p in parents.values() if p is not None)}}
    out = {}
    for m in groups:
        rows = np.where(np.isin(dominant, [n for n, a in anchor.items() if a == m]))[0]
        if len(rows):
            rows = rows[:: max(1, len(rows) // CARRIER_SAMPLES)]
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
        if (shoulder := _shoulder_mount(target, source, m, dominant)) is not None:
            out[m] = shoulder
    if out:
        logger.debug(
            "Costume bones the slot lacks, carried by: %s",
            {source.nodes[m].name: source.nodes[c].name for m, c in out.items()},
        )
    return out


UPPER_ARM = re.compile(r"^Bip001 ([LR]) UpperArm$")


def _shoulder_mount(target: Skeleton, source: Skeleton, node: int, dominant: np.ndarray) -> int | None:
    """The slot's node a mount on an upper arm the slot lacks hangs from, or None.

    A mount on an upper arm the slot lacks is a Biped-named bone below ``Bip001 L/R UpperArm`` that skins nothing itself
    and holds only props (Airi (Band)'s shoulder bag on its strap): the slot's arm moves otherwise than the source's,
    whose own keys held the prop at her side, so the prop hangs from the shoulder (that side's clavicle) instead of
    following the arm. None for anything else.
    """
    parent = source.nodes[node].parent
    side = UPPER_ARM.match(source.nodes[parent].name) if parent is not None else None
    if side is None or not source.nodes[node].name.startswith("Bip001") or (dominant == node).any():
        return None
    children = [i for i, n in enumerate(source.nodes) if n.parent == node]
    if not children or any(source.nodes[c].name.startswith("Bip001") for c in children):
        return None
    clavicle = source.first(f"Bip001 {side.group(1)} Clavicle")
    return clavicle if clavicle is not None and target.first(source.nodes[clavicle].name) is not None else None


def stowed(
    target: Skeleton, source: Skeleton, flat: Flat, poses: list[list[np.ndarray]], carried: Mapping[int, int]
) -> dict[int, tuple[int, np.ndarray, np.ndarray]]:
    """Find loose props keyed onto the body from a distant costume rest position.

    Aoba's pipe
    wrench, skinned in her body mesh below a ``Prop_root`` the slot lacks, rests 40 cm beside her, and every clip of
    hers keys it to her hip. ``carriers`` measures from the rest and finds no bone that explains it, so the slot's
    clips, which never key it, leave it where it rests while her body moves away. For such a branch (outside the
    Biped, no carrier, its vertices skinned to it alone, held by the first probe pose, the idle, at its size and
    more than ``NEAR`` away from where its parent would carry its rest): the bone the slot has by name that moves it
    most alike measured from the idle, under the share ``carriers`` asks. Keyed by the branch's top node: the
    carrier, the branch's vertex rows and per row the move that puts it where the idle holds it against that carrier,
    at the carrier's rest. A prop the idle hides or scales retains its existing placement.

    Args:
        target: Slot skeleton defining available Biped carrier names.
        source: Costume skeleton supplying branch structure, rest matrices, and candidate carrier nodes.
        flat: Source prefab-rest vertices and positive-weight bone influences.
        poses: Source probe world poses, with the idle/reference pose first.
        carried: Branch-top to source-carrier mapping already resolved by carriers; those branches are skipped.

    Returns:
        Branch tops mapped to source carrier, vertex rows, and per-row rest-placement transforms.
    """
    if not poses:
        return {}
    rest = source.world()
    ref = poses[0]
    present = [
        i for i, node in enumerate(source.nodes) if BIPED_CHAIN.match(node.name) and target.first(node.name) is not None
    ]
    weighted = flat.weights > 0
    out = {}
    for n in unmatched(target, source, flat):
        if source.nodes[n].name.startswith("Bip001") or (m := _branch_top(target, source, n)) in carried or m in out:
            continue
        branch = {m, *source.descendants(m)}
        inside = np.isin(flat.joints, sorted(branch)) & weighted
        rows = np.where(inside.any(axis=1))[0]
        if not len(rows) or (inside[rows] != weighted[rows]).any():  # skinned to the body too: not a loose prop
            continue
        bones = sorted(set(flat.joints[rows][weighted[rows]].tolist()))
        if any(
            not 0.9 < abs(np.linalg.det(ref[b][:3, :3])) / max(abs(np.linalg.det(rest[b][:3, :3])), 1e-12) < 1.1
            for b in bones
        ):
            continue  # the idle hides or scales it: a prop the clips show and hide, not one they carry
        parent = _kept_ancestor(target, source, m)
        moves = {b: ref[b] @ np.linalg.inv(rest[b]) for b in bones}
        dominant = flat.joints[rows, np.argmax(flat.weights[rows], axis=1)]
        sample = np.arange(len(rows))[:: max(1, len(rows) // CARRIER_SAMPLES)]
        points = np.c_[flat.vertices[rows[sample]], np.ones(len(sample))]
        at_rest = points @ (ref[parent] @ np.linalg.inv(rest[parent])).T if parent is not None else points
        held = np.einsum("nij,nj->ni", np.array([moves[b] for b in dominant[sample]]), points)
        if float(np.linalg.norm(held - at_rest, axis=1).mean()) <= NEAR:
            continue  # it rests where the clips hold it
        errors = np.zeros(len(present))
        stay = 0.0
        for pose in poses:
            here = np.einsum(
                "nij,nj->ni", np.array([pose[b] @ np.linalg.inv(rest[b]) for b in dominant[sample]]), points
            )
            kept = held @ (pose[parent] @ np.linalg.inv(ref[parent])).T if parent is not None else held
            stay += float(np.linalg.norm(kept - here, axis=1).mean())
            stack = np.array([pose[c] @ np.linalg.inv(ref[c]) for c in present])
            errors += np.linalg.norm(np.einsum("cij,nj->cni", stack, held) - here[None], axis=2).mean(axis=1)
        best = int(np.argmin(errors)) if len(present) else -1
        if best < 0 or not (stay > 1e-4 * len(poses) and errors[best] < CARRIER_SHARE * stay):
            continue
        c = present[best]
        back = rest[c] @ np.linalg.inv(ref[c])  # from where the idle holds the carrier to its rest
        row_moves = drawn(
            Skin(
                flat.vertices[rows],
                np.searchsorted(bones, np.where(weighted[rows], flat.joints[rows], bones[0])),
                flat.weights[rows],
                np.zeros((0, 2), int),
            ),
            np.array([back @ moves[b] for b in bones]),
            np.eye(4),
        )
        out[m] = (c, rows, row_moves)
    if out:
        logger.debug(
            "Costume props stowed by the source's clips, carried by: %s",
            {source.nodes[m].name: source.nodes[c].name for m, (c, _, _) in out.items()},
        )
    return out


def stow(fit: Fit, flat: Flat, props: Mapping[int, tuple[int, np.ndarray, np.ndarray]]) -> Fit:
    """Place stowed props against their carriers consistently across candidate shapes.

    Args:
        fit: Candidate bound shape to copy with corrected prop positions and linear transforms.
        flat: Original prefab-rest shape supplying the prop vertices before placement.
        props: stowed branch records containing carrier, vertex rows, and per-row placement transforms.
    """
    if not props:
        return fit
    vertices, linear = fit.vertices.copy(), fit.linear.copy()
    for _, rows, moves in props.values():
        vertices[rows] = np.einsum("nij,nj->ni", moves, np.c_[flat.vertices[rows], np.ones(len(rows))])[:, :3]
        linear[rows] = moves[:, :3, :3] @ flat.linear[rows]
    return dataclasses.replace(fit, vertices=vertices, linear=linear)


def _branch_top(target: Skeleton, source: Skeleton, n: int) -> int:
    """The node that stands for the branch of a bone outside the Biped.

    A bone outside the Biped stands for its branch: the node right under the nearest ancestor the slot has by name
    (``carriers``'s groups).
    """
    kept = _kept_ancestor(target, source, n)
    while (
        source.nodes[n].parent is not None
        and source.nodes[n].parent != kept
        and source.nodes[source.nodes[n].parent].parent is not None
    ):
        n = source.nodes[n].parent
    return n


def _kept_ancestor(target: Skeleton, source: Skeleton, node: int) -> int | None:
    """The nearest ancestor of ``node`` the slot has a bone of the name for.

    This is where the mapping leaves an unmatched bone that has no carrier (a bag hung off a prop node the slot lacks
    stays with the prefab's root, None).
    """
    p = source.nodes[node].parent
    while p is not None and source.nodes[p].parent is not None and target.first(source.nodes[p].name) is None:
        p = source.nodes[p].parent
    return p if p is not None and source.nodes[p].parent is not None else None


def hidden_bones(skeleton: Skeleton, poses: list[list[np.ndarray]]) -> set[int]:
    """Find bones scaled almost to zero at rest or in probes so costume vertices never follow them.

    Sena's Formation_Pickup hides a prop this way.

    Args:
        skeleton: Slot hierarchy supplying rest volumes and node indices.
        poses: Slot world poses whose bone volumes are compared with rest using HIDDEN.
    """
    if not skeleton.nodes:
        return set()
    rest = np.array(skeleton.world())
    base = np.abs(np.linalg.det(rest[:, :3, :3]))
    out = set(np.where(base < 1e-9)[0].tolist())
    for pose in poses:
        scale = np.abs(np.linalg.det(np.array(pose)[:, :3, :3]))
        out |= set(np.where(scale < HIDDEN * np.maximum(base, 1e-9))[0].tolist())
    return out


def scaled_away(skeleton: Skeleton, clips: Iterable[Any]) -> set[int]:
    """Find bones scaled below HIDDEN of their rest volume anywhere in the slot clips.

    This supplements hidden_bones probes for a magazine hidden in reload, a book in an EX
    skill, or a hair ornament in the cafe. A bone's volume includes its ancestors' scales, so a
    node scaled down above another scaled up (a tank's hull at 0.01, its body at 100) hides neither; it is read from
    the clips' scale curves alone, no pose is computed. Frames that hide the body (a pop-in) hide no bone.

    Args:
        skeleton: Slot hierarchy supplying local rest scales, ancestors, and body root.
        clips: AnimationClip readers whose full scale curves supplement the sparse probe poses.
    """
    g = skeleton.graph
    nodes = skeleton.nodes
    if not nodes:
        return set()
    by_hash = {zlib.crc32(node.path.encode()): i for i, node in enumerate(nodes)}
    rest = np.array([math.log(max(abs(float(np.prod(node.local[2]))), 1e-30)) for node in nodes])
    rest_world = rest.copy()
    for i, node in enumerate(nodes):  # parents come first
        if node.parent is not None:
            rest_world[i] += rest_world[node.parent]
    body = skeleton.first(BIPED_ROOT)
    limit = math.log(HIDDEN)
    out: set[int] = set()
    for clip in clips:
        tree = g.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] == 3 and b["path"] in by_hash for b in bindings):
            continue
        decoded = decode_clip(tree)
        frames = len(decoded.times)
        local: dict[int, np.ndarray] = {}
        for node, attributes in bind_tracks(bindings, decoded.curves, by_hash).items():
            if 3 in attributes:
                x, y, z = (np.asarray(part, float) for part in attributes[3])
                local[node] = np.log(np.maximum(np.abs(x * y * z), 1e-30)) * np.ones(frames)
        if not local:
            continue
        world = np.empty((len(nodes), frames))
        for i, node in enumerate(nodes):
            world[i] = local.get(i, rest[i]) + (world[node.parent] if node.parent is not None else 0.0)
        shrunk = world - rest_world[:, None] < limit
        if body is not None:
            shrunk[:, shrunk[body]] = False
        out.update(np.where(shrunk.any(axis=1))[0].tolist())
    return out


def map_bones(
    target: Skeleton,
    source: Skeleton,
    bones: Iterable[int],
    carried: Mapping[int, int] | None = None,
    hidden: set[int] | None = None,
) -> dict[int, int]:
    """Map source bones and ancestors to slot nodes before per-group follow/rigid choices.

    An unmatched
    bone the slot has no bone near goes to the slot's bone of its carrier's name (``carriers``); no secondary bone goes
    to one the slot's clips hide (``hidden``).

    Args:
        target: Slot skeleton providing named Biped and nearby secondary bones.
        source: Incoming skeleton whose hierarchy is traversed in parent order.
        bones: Source node indices used by the costume; their ancestors are included.
        carried: Optional unmatched-branch to source-carrier map from carriers/stowed.
        hidden: Slot nodes excluded as secondary-bone destinations; None excludes none.
    """
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
    """Hold a candidate costume shape in prefab-root space.

    Each SHAPES candidate retains per-vertex positions and blended mesh-to-root linear
    transforms for normals/tangents. Refit metrics record costume size and vertex motion
    beyond the common move, as the 99th percentile and maximum.
    """

    vertices: np.ndarray
    linear: np.ndarray
    shift: float
    largest: float
    size: float
    starts: list[int] = field(default_factory=list)  # each part's first vertex
    shape: str = "stored"
    # a refit: where the source's head joint lands against the slot's Head joint, in the prefab root's space (the
    # head goes with the neck, ``_anchor_move``); what hangs off the head (the halo) moves along by it
    head: np.ndarray | None = None

    def part(self, k: int, world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Convert one part's fitted positions and linear transforms back to its mesh space.

        Args:
            k: Costume part index selecting its vertex range from starts.
            world: Part mesh-to-prefab-root 4 by 4 transform.
        """
        end = self.starts[k + 1] if k + 1 < len(self.starts) else len(self.vertices)
        inverse = np.linalg.inv(world)
        points = (np.c_[self.vertices[self.starts[k] : end], np.ones(end - self.starts[k])] @ inverse.T)[:, :3]
        return points, inverse[:3, :3] @ self.linear[self.starts[k] : end]


@dataclass
class Plan:
    """Record bone mapping, group choices, fitted shape, and stretch verdict for a costume pair."""

    mapping: dict[int, int]
    choices: dict[int, str]  # source Biped node -> "follow" | "rigid"
    stretch: float
    own: float
    groups: dict[int, list[int]] = field(default_factory=dict)  # source Biped node -> its secondary bones
    unfit: bool = False  # past the stretch limit: may clip or distort, still built and applied
    fit: Fit | None = None  # the shape the costume is bound and judged in (``Fit``)
    repair: str = ""  # past the limit: what stiffening its worst edges did (:func:`rigidify`), for the log


@dataclass
class Flat:
    """Combine costume parts in prefab-root space at rest, retaining topology and influence nodes.

    Bone influences use skeleton node indices (-1 outside the prefab). starts records
    each part's first vertex; linear records each vertex's blended mesh-to-root linear transform.
    """

    vertices: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray
    triangles: np.ndarray
    starts: list[int]
    linear: np.ndarray | None = None


def drawn(skin: Skin, carry: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    """Blend per-bone 4 by 4 transforms with normalized vertex weights.

    Args:
        skin: Vertex influence indices and weights selecting matrices from carry.
        carry: Bone transforms in the destination coordinate space.
        fallback: Transform assigned to vertices with zero total weight.
    """
    total = skin.weights.sum(axis=1, keepdims=True)
    blended = np.einsum("nk,nkij->nij", skin.weights / np.maximum(total, 1e-12), carry[skin.joints])
    blended[total[:, 0] <= 1e-12] = fallback
    return blended


def flatten(costume: Costume, rest: list[np.ndarray]) -> Flat:
    """Flatten costume parts as the prefab draws them at rest using their own bindposes.

    A mesh's vertex data need
    not sit in its renderer's space (some lie a metre or more off, their bindposes bringing them back), and a mesh
    bound in another pose than the prefab's rest is drawn in that rest.

    Args:
        costume: Source parts/skins and hierarchy to combine, keeping facial expression bones head-rigid.
        rest: Source skeleton's rest world matrices defining prefab-root placement.
    """
    vertices, linear, joints, weights, edges, triangles, starts, offset = [], [], [], [], [], [], [], 0
    skeleton = costume.skeleton
    graph = skeleton.graph
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        bp = bindposes(graph, part.mesh)
        carry = [rest[b] @ bp[j] if b is not None and j < len(bp) else node for j, b in enumerate(part.bones)]
        # a face's eye, brow and mouth bones may rest off their bind (a default expression the clips replace): the
        # face stays head-rigid to avoid carrying a default expression (Midori (Battle)'s brows sit 1.4 cm up).
        head = {b: j for j, b in enumerate(part.bones) if b is not None and skeleton.nodes[b].name == "Bip001 Head"}
        for j, b in enumerate(part.bones):
            if b is None or j >= len(bp) or not face_bone(skeleton, b):
                continue
            h = skeleton.chain_ancestor(b)
            if h in head and head[h] < len(bp):
                carry[j] = rest[h] @ bp[head[h]]
        carry = np.array(carry or [node])
        blended = drawn(
            Skin(skin.vertices, np.minimum(skin.joints, len(carry) - 1), skin.weights, skin.edges), carry, node
        )
        vertices.append(np.einsum("nij,nj->ni", blended, np.c_[skin.vertices, np.ones(len(skin.vertices))])[:, :3])
        linear.append(blended[:, :3, :3])
        joints.append(
            np.array(
                [[part.bones[j] if part.bones[j] is not None else -1 for j in row] for row in skin.joints]
            ).reshape(-1, skin.joints.shape[1])
        )
        weights.append(skin.weights)
        edges.append(skin.edges + offset)
        tris = skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)
        triangles.append(tris + offset)
        starts.append(offset)
        offset += len(skin.vertices)
    return Flat(
        np.vstack(vertices),
        np.vstack(joints),
        np.vstack(weights),
        np.vstack(edges),
        np.vstack(triangles),
        starts,
        np.concatenate(linear),
    )


@dataclass
class OwnMesh:
    """A prefab's costume drawn by its own bindposes: rest vertices, edges, each vertex's dominant node."""

    rest: np.ndarray
    edges: np.ndarray
    dominant: np.ndarray
    parts: list[tuple[Skin, np.ndarray, list[int]]]

    @classmethod
    def of(cls, costume: Costume) -> "OwnMesh":
        """Build reference geometry, edge indices, and dominant bones from a costume's original bindposes."""
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
        """Skin all reference parts under node world matrices and concatenate their positions."""
        return np.vstack([deform(skin, np.array([pose[b] for b in bones]) @ bp) for skin, bp, bones in self.parts])


def face_bone(skeleton: Skeleton, node: int) -> bool:
    """Recognize an eye, brow, or mouth bone below the head, excluding body-chain and twist bones.

    Args:
        skeleton: Source hierarchy used to find the nearest Biped-chain ancestor.
        node: Node index whose Bip001-prefixed name is tested.
    """
    name = skeleton.nodes[node].name
    if not name.startswith("Bip001") or BIPED_CHAIN.match(name) or "Twist" in name:
        return False
    h = skeleton.chain_ancestor(node)
    return h is not None and skeleton.nodes[h].name == "Bip001 Head"


def fit_anchor(
    target: Skeleton, source: Skeleton, node: int, carried: Mapping[int, int] | None = None
) -> tuple[int, int] | None:
    """Find a source node's refit anchor and the matching slot bone.

    Use the node or nearest named ancestor, substituting carriers for unmatched bones.
    Eligible anchors are the Biped body chain, hands, feet, twist bones, and root. A skirt
    below Bip001, such as Sena (Casual)'s, follows the body even when the prefab rests aside.
    Facial bones follow the head rigidly because rigs differ: Midori (Battle)'s lower
    eyelids rest 3.5 cm from Midori's, enough to pull the eye out.

    Args:
        target: Slot hierarchy searched for available anchor names.
        source: Costume hierarchy whose node ancestry and carriers determine the anchor.
        node: Source node index to refit.
        carried: Optional unmatched-branch to source-carrier map overriding missing named anchors.

    Returns:
        Source/slot anchor indices, or None when no eligible anchor is shared.
    """
    carried = carried or {}
    n: int | None = node
    while n is not None:
        name = source.nodes[n].name
        if (BIPED_CHAIN.match(name) or name == BIPED_ROOT or (name.startswith("Bip001") and "Twist" in name)) and (
            same := target.first(name)
        ) is not None:
            return n, same
        if n in carried and (same := target.first(source.nodes[carried[n]].name)) is not None:
            return carried[n], same
        n = source.nodes[n].parent
    return None


def stored(costume: Costume, rest: list[np.ndarray], flat: Flat) -> Fit:
    """Build the stored shape by treating each part's vertex data as renderer-local coordinates.

    Args:
        costume: Source costume parts with their original mesh-space vertex arrays.
        rest: Source rest world matrices placing each renderer in prefab-root space.
        flat: Flattened costume supplying part vertex offsets for the resulting Fit.
    """
    vertices, linear = [], []
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        vertices.append((np.c_[skin.vertices, np.ones(len(skin.vertices))] @ node.T)[:, :3])
        linear.append(np.repeat(node[None, :3, :3], len(skin.vertices), axis=0))
    return Fit(np.vstack(vertices), np.concatenate(linear), 0.0, 0.0, 0.0, list(flat.starts), "stored")


def _anchor_move(
    target: Skeleton,
    source: Skeleton,
    a: int,
    t: int,
    shape: str,
    t_rest: list[np.ndarray],
    s_rest: list[np.ndarray],
    carried: Mapping[int, int] | None = None,
) -> np.ndarray:
    """How a refit carries what anchors on the source's ``a`` to the slot's ``t`` (``refit``).

    "moved" is the joint's move alone; "refitted", its move and turn. The head goes with the neck: it keeps its own
    turn, but its joint goes where the neck's move takes it, not onto the slot's Head joint. Two rigs put the Head joint
    at different heights inside the head (Aris's 3.5 cm above her chin, Yuzu's 1.6 cm), so a whole head carried onto the
    slot's joint rises or sinks against the collar that the neck and spine carry, stretching the neck between (Yuzu's
    costume on Aris: her head 1.8 cm up out of her high collar) or vanished. The head then turns about the slot's joint.
    """
    m = t_rest[t] @ _inverse(s_rest[a], source.nodes[a].name)
    if shape == "moved":  # the joint's move alone, no turn
        m = np.eye(4)
        m[:3, 3] = t_rest[t][:3, 3] - s_rest[a][:3, 3]
    parent = source.nodes[a].parent
    if (
        source.nodes[a].name == HEAD
        and parent is not None
        and (hit := fit_anchor(target, source, parent, carried)) is not None
        and hit[0] != a
    ):
        joint = s_rest[a][:, 3]
        m[:3, 3] += (_anchor_move(target, source, *hit, shape, t_rest, s_rest, carried) @ joint - m @ joint)[:3]
    return m


def refit(
    target: Skeleton, source: Skeleton, flat: Flat, carried: Mapping[int, int] | None = None, shape: str = "refitted"
) -> Fit:
    """Refit a costume from its source rest skeleton to the slot's joints.

    Each vertex follows its bones by
    their anchors' transforms (``fit_anchor``, ``_anchor_move``), so the Biped's proportions change and hair, skirts
    and props move rigidly with their anchor; the skin weights stay. ``shape`` "moved": each anchor's joint move
    alone, "refitted": its move and turn. ``shift`` and ``largest``: how far the vertices move past the costume's
    common move (a slot whose prefab rests a metre aside, or lying, takes the costume along as a whole), for the log;
    what the refit does to the costume's edges counts in its stretch (``plan``). ``head``: where the source's head
    joint lands against the slot's.

    Args:
        target: Slot skeleton supplying destination rest joints and orientations.
        source: Incoming skeleton supplying source rest anchors.
        flat: Source prefab-rest geometry, influence nodes, and mesh-to-root linear transforms.
        carried: Optional unmatched-branch to source-carrier map used by fit_anchor.
        shape: "moved" translates anchors only; "refitted" also turns them to destination orientations.
    """
    t_rest, s_rest = target.world(), source.world()
    used = sorted(set(flat.joints[flat.weights > 0].tolist()) - {-1})
    matrices = [np.eye(4)]  # column 0: a bone outside the prefab or without weight
    columns = {}
    moves: dict[int, np.ndarray] = {}  # per source anchor
    for n in used:
        hit = fit_anchor(target, source, n, carried)
        if hit is None:
            columns[n] = 0
            continue
        a, t = hit
        if a not in moves:
            moves[a] = _anchor_move(target, source, a, t, shape, t_rest, s_rest, carried)
        columns[n] = len(matrices)
        matrices.append(moves[a])
    head = None
    s_head, t_head = source.first(HEAD), target.first(HEAD)
    if s_head is not None and t_head is not None:
        move = _anchor_move(target, source, s_head, t_head, shape, t_rest, s_rest, carried)
        head = (move @ s_rest[s_head][:, 3])[:3] - t_rest[t_head][:3, 3]
    stack = np.array(matrices)
    local = np.vectorize(lambda n: columns.get(int(n), 0), otypes=[int])(np.where(flat.weights > 0, flat.joints, -1))
    blended = drawn(Skin(flat.vertices, local, flat.weights, flat.edges), stack, np.eye(4))
    vertices = np.einsum("nij,nj->ni", blended, np.c_[flat.vertices, np.ones(len(flat.vertices))])[:, :3]
    delta = vertices - flat.vertices
    changed = np.linalg.norm(delta - np.median(delta, axis=0), axis=1) if len(delta) else np.zeros(0)
    size = float(np.ptp(flat.vertices, axis=0).max()) if len(flat.vertices) else 0.0
    linear = blended[:, :3, :3] @ flat.linear if flat.linear is not None else blended[:, :3, :3]
    return Fit(
        vertices,
        linear,
        float(np.percentile(changed, 99)) if len(changed) else 0.0,
        float(changed.max()) if len(changed) else 0.0,
        size,
        list(flat.starts),
        shape,
        head,
    )


def plan(
    target: Costume,
    source: Costume,
    probes: list[list[np.ndarray]],
    *,
    fit: bool = True,
    own_probes: list[list[np.ndarray]] | None = None,
    clips: Iterable[Any] = (),
) -> Plan:
    """Choose bone mapping, follow/rigid groups, and the least-stretched supported bound shape.

    The mapping (``map_bones``) takes the carriers of the unmatched bones from the source's own clips
    (``own_probes``, see ``carriers``) and leaves out the slot's hidden bones: those the probes hide, and those any of
    the slot's ``clips`` scales to nothing (:func:`scaled_away`). With ``fit`` (the costume's geometry can
    be rewritten), a prop the costume rests aside and its own clips hold on the body is moved there and carried
    (``stowed``), and each of ``SHAPES`` is judged under the slot's clips and the least stretch wins, a later shape only
    by more than ``SHAPE_MARGIN``; without, the costume keeps its stored shape.

    Args:
        target: Slot costume defining available bones and baseline stretch.
        source: Incoming costume whose bones and geometry are mapped and judged.
        probes: Slot world poses used to judge stretch and hidden bones.
        fit: Whether source geometry can be rewritten, enabling placement and refit candidates.
        own_probes: Source-owned world poses for carrier/stowed-prop detection; None provides no motion evidence.
        clips: Slot clips checked for scaled-away bones beyond those hidden in probes.

    Raises:
        ServiceError: A weighted source bone is outside the prefab or a mapping/rest-matrix check fails.
    """
    s_rest = source.skeleton.world()
    flat_source = flatten(source, s_rest)
    sj, sw = flat_source.joints, flat_source.weights
    if (sj[sw > 0] < 0).any():
        raise refuse("costume bone outside the prefab")
    carried = carriers(target.skeleton, source.skeleton, flat_source, own_probes or [])
    props = stowed(target.skeleton, source.skeleton, flat_source, own_probes or [], carried) if fit else {}
    carried = {**carried, **{m: c for m, (c, _, _) in props.items()}}
    hidden = hidden_bones(target.skeleton, probes) | scaled_away(target.skeleton, clips)
    mapping = map_bones(target.skeleton, source.skeleton, source.bones(), carried, hidden)
    own_mesh = OwnMesh.of(target)
    posed_targets = [own_mesh.posed(p) for p in probes]
    own = max((stretch(own_mesh.rest, posed, own_mesh.edges) for posed in posed_targets), default=1.0)
    shapes = [stow(stored(source, s_rest, flat_source), flat_source, props)]
    if fit:
        drawn_ = stow(
            Fit(flat_source.vertices, flat_source.linear, 0.0, 0.0, 0.0, list(flat_source.starts), "drawn"),
            flat_source,
            props,
        )
        flat_source = dataclasses.replace(flat_source, vertices=drawn_.vertices, linear=drawn_.linear)
        shapes.append(drawn_)
        shapes += [
            refit(target.skeleton, source.skeleton, flat_source, carried, shape) for shape in ("moved", "refitted")
        ]
    best: Plan | None = None
    judged = []
    for shape in shapes:
        # a refit's own change of the costume counts: measured from the shape the source draws
        reference = shape.vertices if shape.shape == "stored" else flat_source.vertices
        candidate = _judge(
            target, source, probes, flat_source, shape, reference, dict(mapping), carried, own_mesh, posed_targets, own
        )
        judged.append(f"{shape.shape} {candidate.stretch:.2f}")
        if best is None or candidate.stretch < best.stretch * (1 - SHAPE_MARGIN):
            best = candidate
    assert best is not None
    logger.debug("Costume shapes judged: %s; %s chosen", ", ".join(judged), best.fit.shape if best.fit else "?")
    return best


def _judge(
    target: Costume,
    source: Costume,
    probes: list[list[np.ndarray]],
    flat_source: Flat,
    shape: Fit,
    reference: np.ndarray,
    mapping: dict[int, int],
    carried: Mapping[int, int],
    own_mesh: OwnMesh,
    posed_targets: list[np.ndarray],
    own: float,
) -> Plan:
    """One shape's plan: each group's choice, the rest pose and the stretch.

    ``mapping`` is changed in place; the stretch is measured from ``reference`` (the costume's shape before the refit).
    """
    t_rest = target.skeleton.world()
    sv, sj, sw, se = shape.vertices, flat_source.joints, flat_source.weights, flat_source.edges
    tv, te, tdom = own_mesh.rest, own_mesh.edges, own_mesh.dominant
    sdom = np.array([row[np.argmax(w)] for row, w in zip(sj, sw)])
    nodes = sorted(set(sj[sw > 0].tolist()))
    index = {n: k for k, n in enumerate(nodes)}
    joints = np.vectorize(lambda n: index.get(n, 0))(np.where(sj < 0, nodes[0], sj))
    flat = Skin(sv, joints, sw, se)

    inverses: dict[int, np.ndarray] = {}

    def rest_inverse(node: int) -> np.ndarray:
        if node not in inverses:
            inverses[node] = _inverse(t_rest[node], target.skeleton.nodes[node].name)
        return inverses[node]

    def matrices(pose: list[np.ndarray], m: Mapping[int, int]) -> np.ndarray:
        return np.array([pose[m[n]] @ rest_inverse(m[n]) for n in nodes])

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
        slot_name = source.skeleton.nodes[carried.get(anchor, anchor)].name  # a Biped bone the slot lacks: its carrier
        if not probes or not mask.any():
            follow = False
        else:
            t_members = [
                j
                for j in set(tdom.tolist())
                if not target.skeleton.nodes[j].name.startswith("Bip001")
                and target.skeleton.biped_ancestor(j) is not None
                and target.skeleton.nodes[target.skeleton.biped_ancestor(j)].name == slot_name
            ]
            t_mask = np.isin(tdom, t_members)
            t_mask = t_mask[te[:, 0]] & t_mask[te[:, 1]]
            group_own = (
                max((stretch(tv, posed, te, t_mask) for posed in posed_targets), default=1.0) if t_mask.any() else 1.0
            )
            mine = max(stretch(reference, deform(flat, matrices(p, mapping)), se, mask) for p in probes)
            follow = mine <= max(STRETCH_FLOOR, group_own)
        choices[anchor] = "follow" if follow else "rigid"
        if not follow:
            for n in members:
                mapping[n] = rigid
    rest = deform(flat, matrices(t_rest, mapping))
    if float(np.abs(rest - sv).max()) > 1e-4:
        raise refuse("rest pose not reproduced")
    mine = max((stretch(reference, deform(flat, matrices(p, mapping)), se) for p in [t_rest, *probes]), default=1.0)
    return Plan(mapping, choices, mine, own, groups, mine > stretch_limit(own), shape)


# --- skirts and hems (see model_hems) ------------------------------------------------------------------------

HEM_CLIPS = ("Formation_Idle", "Formation_Pickup", "Normal_Attack_Ing", "Exs", "Move_Ing", "Cafe_Walk", "Vital_Death")
HEM_FRAMES = 6  # frames measured per check clip
FIT_FRAMES = 12  # frames per clip of the costume's own clips, for the fit
LEG_SAMPLES = 700  # leg vertices measured, about
HEM_RULES = (("blend", 0.5), ("short", 0.5), ("short", 0.7), ("short", 1.0), ("fit", 0.5), ("fit", 0.7), ("fit", 1.0))
RAMPS = {"blend": (1.0, (1.0, 2.0)), "short": (0.3, (0.9, 1.3))}  # rise in thigh lengths, thigh -> calf change
LEG_KEYS = ("pelvis", "thigh L", "thigh R", "calf L", "calf R", "foot L", "foot R")
FIT_KEYS = LEG_KEYS[:5]
SPINE = re.compile(r"^Bip001 Spine\d*$")
REGROUP = "regroup "  # Optimize Physics: a rule built as if the leg-anchor groups set to ``follow`` were rigid


def legs_of(skeleton: Skeleton) -> dict[str, int] | None:
    """Find the Biped pelvis, thighs, calves, and feet by clip/IK names, or None if any are missing."""
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
    """Collect pelvis and leg Biped nodes whose secondary bones may carry leg cloth.

    Args:
        skeleton: Hierarchy supplying descendants below each thigh.
        legs: Complete pelvis/thigh/calf/foot map from legs_of.
    """
    out = {legs["pelvis"]}
    for side in "LR":
        thigh = legs[f"thigh {side}"]
        out |= {thigh} | {j for j in skeleton.descendants(thigh) if skeleton.nodes[j].name.startswith("Bip001")}
    return out


def followed_groups(plan_: Plan, skeleton: Skeleton, anchors: set[int]) -> dict[int, list[int]]:
    """Select followed leg-anchor groups for Optimize Physics to judge again by pass-through.

    Args:
        plan_: Group choices and member nodes from the initial mapping judgment.
        skeleton: Source hierarchy retained by the group-selection interface.
        anchors: Eligible pelvis/leg anchor nodes from lower_anchors.
    """
    return {
        anchor: members
        for anchor, members in plan_.groups.items()
        if members and anchor in anchors and plan_.choices.get(anchor) == "follow"
    }


def chain_root(skeleton: Skeleton, node: int) -> int:
    """Find the first secondary bone above node, immediately below its Biped anchor in skeleton."""
    while (parent := skeleton.nodes[node].parent) is not None and not skeleton.nodes[parent].name.startswith("Bip001"):
        node = parent
    return node


def cloth_mask(
    skeleton: Skeleton, dominant: np.ndarray, anchors: set[int], axis_distance: np.ndarray, hip_width: float
) -> np.ndarray:
    """Select leg cloth by secondary-bone anchors and median distance, excluding wings and tails.

    Args:
        skeleton: Source hierarchy supplying secondary chain roots and Biped anchors.
        dominant: Dominant skeleton node per vertex, with negative indices excluded.
        anchors: Eligible Biped anchors around the legs or lower-reaching spine cloth.
        axis_distance: Per-vertex distance to the nearest leg axis in metres.
        hip_width: Thigh-to-thigh distance in metres; chains must lie within 1.5 times this median distance.
    """
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


def fit_own(
    skeleton: Skeleton, flat: Flat, on_rigid: np.ndarray, share: np.ndarray, legs: Mapping[str, int], clips: list[Any]
) -> np.ndarray | None:
    """Fit pelvis/thigh/calf weights reproducing the costume's own keyed rigid-cloth motion.

    Weights on FIT_KEYS reproduce where the costume's
    own clips put its rigid part (the vendor's keyed skirt bones): least squares over the sampled frames of every clip
    with Biped curves. None without such clips.

    Args:
        skeleton: Source hierarchy used to sample its own clips and rest skin matrices.
        flat: Source prefab-rest vertices and skeleton-node influences.
        on_rigid: Boolean mask selecting rigid-cloth influences in flat's weight rows.
        share: Total rigid-cloth weight per vertex; only positive-share vertices are fitted.
        legs: Source pelvis/thigh/calf/foot map supplying FIT_KEYS destination columns.
        clips: Source-owned AnimationClip readers with Biped motion.
    """
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
    logger.debug(
        "Hem fit: %d vertices over %d frames, rms residual %.1f mm",
        len(rows),
        normal.frames,
        1000 * float(residual.mean()) if len(residual) else 0.0,
    )
    out = np.zeros((len(flat.vertices), len(bones)))
    out[rows] = fitted
    return out


@dataclass
class Skirt:
    """Describe added chain bones and their solver for baking slot clip curves.

    Source chain nodes come parents first. Local TRS values are relative to a chain-parent
    index, or -1 for the slot anchor; base is the slot node index assigned to the first new bone.
    """

    sources: list[int]
    names: list[str]
    parents: list[int]
    locals: list[tuple]
    anchor: int
    solver: Any  # ms.Solver, ms.Spring or FaceRetarget
    base: int
    clips: int = 0  # clips given curves
    solved: dict[tuple[str, int], tuple[list[int], list[dict[int, np.ndarray]]]] = field(default_factory=dict)
    anchors: list[int] = field(default_factory=list)  # per bone, the slot node a root hangs off (empty: ``anchor``)

    def node(self, k: int) -> int:
        """Return the destination slot node index for chain bone k."""
        return self.base + k

    def anchor_of(self, k: int) -> int:
        """Return chain bone k's individual slot anchor, falling back to the shared anchor."""
        return self.anchors[k] if self.anchors else self.anchor


@dataclass
class Hems:
    """Record the chosen hem rule, measurements, and skin/chain edits.

    Retain the rule ("rigid" when nothing moves), why nothing was measured, the stretch
    limit, the scores (rigid first) and, per source part, the rewritten vertices (rows, slot nodes, weights); for
    Rule B (rule "skirt" or "skirt smooth") the new bones. With Optimize Physics a rule may carry the ``REGROUP``
    prefix: it was built as if the leg-anchor groups the group choice set to ``follow`` were rigid (``regrouped``),
    and the first score, the default build's, is the baseline it must beat.
    """

    rule: str = "rigid"
    note: str = ""
    limit: float = 0.0
    scores: list[hm.Score] = field(default_factory=list)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = field(default_factory=list)
    skirt: Skirt | None = None
    regrouped: bool = False
    # Optimize Physics: leg vertices deep under the cloth moved toward the leg's axis (rows of the bound shape, their
    # new places in the prefab root's space), and the score that took it
    thin: tuple[np.ndarray, np.ndarray] | None = None
    thin_score: hm.Score | None = None
    springs: list[Skirt] = field(default_factory=list)  # Optimize Physics: chains that swing after their anchor
    face: Skirt | None = None  # another character's face bones following the slot's expressions (``face_retarget``)

    def score(self, rule: str) -> hm.Score | None:
        """Find the exact named hem rule's measured score, or None when it was not measured."""
        return next((s for s in self.scores if s.rule == rule), None)

    def summary(self) -> str:
        """Describe the chosen rule's pass-through, stretch, chain, and thinning results for logs."""
        if not self.scores:
            return f"rigid ({self.note})"
        rigid, chosen = self.scores[0], self.score(self.rule) or self.scores[0]
        extra = f", {len(self.skirt.solver.roots)} chains in {self.skirt.clips} clips" if self.skirt else ""
        if self.springs:
            extra += (
                f", {sum(len(s.solver.roots) for s in self.springs)} swinging chains in "
                + "/".join(str(s.clips) for s in self.springs)
                + " clips"
            )
        if self.thin is not None and self.thin_score is not None:
            extra += f", {len(self.thin[0])} leg vertices thinned under the cloth ({self.thin_score.mean:.1f}%)"
        return (
            f"{self.rule}: deep pass-through {rigid.mean:.1f}% -> {chosen.mean:.1f}%, stretch {rigid.stretch:.2f} -> "
            f"{chosen.stretch:.2f} (limit {self.limit:.2f}){extra}; per clip "
            + ", ".join(f"{clip} {rigid.deep[clip]:.0f}->{chosen.deep[clip]:.0f}" for clip in rigid.deep)
        )


@dataclass
class _SkirtCandidate:
    """A Rule B candidate.

    It holds dense weights over the measured columns and then the chains' bones, its solver, and the new bones those
    columns name (shared by the candidates of one build).
    """

    rule: str
    weights: np.ndarray
    solver: ms.Solver
    skirt: Skirt


def _skirt_candidates(
    ts: Skeleton,
    ss: Skeleton,
    plan_: Plan,
    flat: Flat,
    sv: np.ndarray,
    dominant: np.ndarray,
    cloth: np.ndarray,
    legs: np.ndarray,
    t_legs: Mapping[str, int],
    s_legs: Mapping[str, int],
    mapped: np.ndarray,
    columns: list[int],
    writable_rows: np.ndarray,
    anchors: Iterable[int] | None = None,
) -> list[_SkirtCandidate]:
    """Rule B's candidates and their new bones, for the skirt chains left rigid by the group choice.

    The candidates are "skirt" (the costume's weights) and "skirt smooth" (blended across the chains' seams). The skirt
    chains are those of the cloth around the legs that the group choice left rigid, hanging off the pelvis or another of
    ``anchors`` (source Biped bones; the pelvis alone by default): each chain's first bone hangs off the slot's bone of
    its own anchor (a coat's tails off the spine, a strap off a thigh). ``flat``: the costume in the source's space;
    ``sv``: its vertices in the shape bound on the slot, where each chain's bones go along with its cloth.
    """
    pelvis = s_legs["pelvis"]
    anchors = [
        a for a in dict.fromkeys(anchors if anchors is not None else [pelvis]) if plan_.choices.get(a) == "rigid"
    ]
    if not anchors:
        return []
    members = {n for a in anchors for n in plan_.groups.get(a, [])}
    roots = {chain_root(ss, int(n)) for n in np.unique(dominant[cloth]).tolist() if int(n) in members}
    skinned = {
        int(n)
        for n in np.unique(flat.joints[flat.weights > 0]).tolist()
        if int(n) in members and chain_root(ss, int(n)) in roots
    }
    wanted: set[int] = set()
    for n in skinned:
        while n not in wanted:
            wanted.add(n)
            if n in roots:
                break
            n = ss.nodes[n].parent
    sources = sorted(wanted)  # the skeleton lists parents first
    if not sources:
        return []
    new = {n: k for k, n in enumerate(sources)}
    anchor = plan_.mapping[pelvis]
    t_rest, s_rest = ts.world(), ss.world()
    parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
    # each root below the slot's bone of its own Biped anchor
    hangs = [plan_.mapping[ss.biped_ancestor(n)] if parents[k] < 0 else anchor for k, n in enumerate(sources)]
    # each chain moves with its cloth from the source's space to the bound shape (a refit carries it with the pelvis)
    carry = {}
    for k, n in enumerate(sources):
        if parents[k] < 0:
            share = (
                flat.weights
                * np.isin(flat.joints, [sources[j] for j in range(len(sources)) if chain_root(ss, sources[j]) == n])
            ).sum(axis=1)
            rows = np.where(share > 0.5)[0]
            carry[n] = ms.rigid_fit(flat.vertices[rows], sv[rows], share[rows]) if len(rows) >= 3 else np.eye(4)
    rest = np.array([carry[chain_root(ss, n)] @ s_rest[n] for n in sources])
    local = np.array([np.linalg.inv(rest[p] if p >= 0 else t_rest[hangs[k]]) @ rest[k] for k, p in enumerate(parents)])
    chains = ms.Chains(anchor, parents, local, rest, anchors=hangs)
    taken = {node.path for node in ts.nodes}
    names, paths, locals_ = [], [], []
    for k, n in enumerate(sources):
        name = ss.nodes[n].name
        parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[hangs[k]].path
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
    cols = np.where(
        on, len(columns) + np.maximum(chain, 0), np.vectorize(lambda n: index.get(int(n), 0), otypes=[int])(mapped)
    )
    cols = np.where(flat.weights > 0, cols, 0)
    plain = hm.densify(cols, flat.weights, width)
    smooth = plain.copy()
    smooth[:, len(columns) :] = ms.smooth_weights(plain[:, len(columns) :], flat.edges)
    smooth[~writable_rows] = plain[~writable_rows]
    leg_joints = np.where(flat.weights[legs] > 0, mapped[legs], anchor)
    leg_set = ms.Legs(sv[legs], leg_joints, flat.weights[legs])
    skirt = Skirt(sources, names, parents, locals_, anchor, None, len(ts.nodes), anchors=hangs)
    out = []
    for rule, dense in (("skirt", plain), ("skirt smooth", smooth)):
        if rule == "skirt smooth" and np.abs(smooth - plain).max() < 1e-9:
            continue
        on_chains = dense[:, len(columns) :].sum(axis=1) > 1e-9
        # with the ring of cloth around them that the chains do not carry, so that the edges across the seam keep
        # their length too (a chain's head may move)
        ring = np.zeros(len(on_chains), bool)
        seam = on_chains[flat.edges[:, 0]] != on_chains[flat.edges[:, 1]]
        ring[flat.edges[seam].ravel()] = True
        rows = np.where(on_chains | ring)[0]
        joints4, weights4 = hm.keep_four(dense[rows])
        fixed = sorted({columns[c] for c in joints4[weights4 > 0].ravel().tolist() if c < len(columns)} | {anchor})
        slot = {c: fixed.index(columns[c]) + len(sources) for c in range(len(columns)) if columns[c] in fixed}
        joints = np.vectorize(
            lambda c: c - len(columns) if c >= len(columns) else slot.get(c, slot[index[anchor]]), otypes=[int]
        )(joints4)
        position = np.full(len(flat.vertices), -1)
        position[rows] = np.arange(len(rows))
        edges = position[flat.edges]
        edges = edges[(edges >= 0).all(axis=1)]
        cloth_set = ms.Cloth(sv[rows], joints, weights4, fixed, edges)
        out.append(_SkirtCandidate(rule, dense, ms.Solver(chains, cloth_set, leg_set, t_rest, t_legs["pelvis"]), skirt))
    if out:
        skirt.solver = out[0].solver
    return out


# Cost weights for the hems phase: setup, weight-only judging, chain solving, and leg thinning. Units are about
# 0.05 s, measured across costume pairs with Optimize Physics, candidate pruning, and shared chain solutions.
HEM_UNITS = (20, 1, 24, 12)


def hems(
    target: Costume,
    source: Costume,
    plan_: Plan,
    t_clips: list[Any],
    s_clips: list[Any],
    writable: list[bool],
    physics: bool = False,
    shapes_writable: bool = False,
    meter: Meter = QUIET,
    step: Callable[[], None] = lambda: None,
) -> Hems:
    """Choose measured hem weights and optional leg-driven chain bones within the stretch limit.

    Rule A supplies new skin weights for the cloth the group choice left rigid around the legs, and, with
    ``physics`` (Optimize Physics), Rule B: the skirt chains as new bones turned by the legs in every
    frame; picked by measurement under the slot's own clips (see ``model_hems`` and ``model_skirts``). ``writable``:
    per source part, whether its mesh's skin can be rewritten.

    With ``physics`` the leg-anchor groups the group choice set to ``follow`` are judged again by pass-through: both
    rules are also built as if those groups were rigid (named with the ``REGROUP`` prefix), and ``model_hems.choose``
    compares them with the follow result, the first score, which they must beat within the same stretch ceiling. A build
    without ``physics`` never looks at them. ``meter`` is told how far the judging is, the rules weighed by their cost
    (``HEM_UNITS``; the build's "hems" phase).

    This runs in the caller's class 1 preview or class 2/3 build, with no new
    locks or workers. ``step`` yields or cancels between clips and solver
    frames (normally under 0.1 s); cache locks hold only LRU bookkeeping.

    Args:
        target: Slot costume defining leg landmarks, reference cloth, and baseline stretch.
        source: Incoming costume supplying leg vertices, cloth, skin weights, and secondary chains.
        plan_: Initial bone mapping, group choices, and fitted bound shape.
        t_clips: Slot clips under which candidate pass-through and stretch are measured.
        s_clips: Source clips whose keyed cloth motion supplies least-squares weight candidates.
        writable: Per-source-part flags permitting skin-weight rewrites.
        physics: Whether to consider new skirt bones and regroup followed leg-anchor chains.
        shapes_writable: Whether fitted geometry can also be rewritten, allowing leg thinning with physics.
        meter: Progress meter receiving cost-weighted updates in the hems phase.
        step: No-argument yield/cancellation callback between clips and solver frames.
    """
    ts, ss = target.skeleton, source.skeleton
    t_legs, s_legs = legs_of(ts), legs_of(ss)
    if t_legs is None or s_legs is None:
        return Hems(note="no Biped legs")
    s_anchors = lower_anchors(ss, s_legs)
    rigid = sorted(
        {
            n
            for anchor, members in plan_.groups.items()
            if anchor in s_anchors and plan_.choices.get(anchor) == "rigid"
            for n in members
        }
    )
    followed = followed_groups(plan_, ss, s_anchors) if physics else {}
    flat = flatten(source, ss.world())  # the source's space, for the fit to its own clips
    on_rigid = np.isin(flat.joints, rigid) & (flat.weights > 0)
    share = (flat.weights * on_rigid).sum(axis=1)
    # Optimize Physics: the same cloth with the followed groups rigid as well
    on_regroup = (
        np.isin(flat.joints, sorted({*rigid, *(n for members in followed.values() for n in members)}))
        & (flat.weights > 0)
        if followed
        else None
    )
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

    dominant = flat.joints[np.arange(len(sv)), np.argmax(flat.weights, axis=1)]
    all_legs = np.where(np.isin(dominant, sorted(s_anchors - {s_legs["pelvis"]})))[0]
    legs = all_legs[:: max(1, len(all_legs) // LEG_SAMPLES)]
    cloth = cloth_mask(ss, dominant, s_anchors, axis, hip_width)
    chain_anchors = [s_legs["pelvis"]]
    if physics:  # Optimize Physics: also the chains off the spine whose cloth reaches below the hips (a coat's tails)
        spines = {i for i, node in enumerate(ss.nodes) if SPINE.match(node.name)}
        wide = cloth_mask(ss, dominant, s_anchors | spines, axis, hip_width) & ~cloth
        hips = float(((at["thigh L"] + at["thigh R"]) / 2) @ up)
        for root in {chain_root(ss, int(n)) for n in np.unique(dominant[wide]).tolist()}:
            rows = wide & np.isin(dominant, [root, *ss.descendants(root)])
            if rows.any() and float((sv[rows] @ up).min()) < hips:
                cloth |= rows
        chain_anchors += sorted(spines) + [s_legs["thigh L"], s_legs["thigh R"]]
    triangles = flat.triangles[cloth[flat.triangles].all(axis=1)] if len(flat.triangles) else flat.triangles
    edges = flat.edges[cloth[flat.edges].all(axis=1)]
    if not len(legs) or not len(triangles):
        return Hems(note="no cloth around the legs")
    state, _ = hm.cover(sv, legs, triangles, at["pelvis"], up)
    covered = legs[state == 1]
    logger.debug(
        "Hem: %d cloth vertices (%d triangles), %d rigid-weighted, %d of %d sampled leg vertices covered",
        int(cloth.sum()),
        len(triangles),
        int((share > 1e-6).sum()),
        len(covered),
        len(legs),
    )
    if not len(covered):
        return Hems(note="no leg under the cloth")
    own = OwnMesh.of(target)
    t_axis = np.minimum(
        hm.leg_distance(own.rest, at["thigh L"], at["calf L"], at["foot L"]),
        hm.leg_distance(own.rest, at["thigh R"], at["calf R"], at["foot R"]),
    )
    t_cloth = cloth_mask(ts, own.dominant, lower_anchors(ts, t_legs), t_axis, hip_width)
    t_edges = own.edges[t_cloth[own.edges].all(axis=1)]

    # dense weights per slot node, with the rigid part apart
    mapped = np.vectorize(lambda n: plan_.mapping.get(int(n), 0), otypes=[int])(flat.joints)
    used = set(mapped[flat.weights > 0].tolist())
    if followed:  # the followed groups' secondary bones on the anchor's slot node, as a rigid group is mapped
        regroup = {
            **plan_.mapping,
            **{n: plan_.mapping[anchor] for anchor, members in followed.items() for n in members},
        }
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
        close_l, close_r = np.exp(-((left / sigma) ** 2)), np.exp(-((right / sigma) ** 2))
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
    skirt_rules = (
        _skirt_candidates(
            ts, ss, plan_, flat, sv, dominant, cloth, all_legs, t_legs, s_legs, mapped, columns, rows_ok, chain_anchors
        )
        if physics
        else []
    )
    rules += [(c.rule, c.weights, c.solver) for c in skirt_rules]
    if followed:
        # the same rules with the followed groups rigid: the pelvis group's chains only where it was followed
        cols_regroup = column_of(mapped_regroup)
        dense_regroup = hm.densify(cols_regroup, flat.weights, len(columns))
        part_regroup = hm.densify(cols_regroup, np.where(on_regroup, flat.weights, 0.0), len(columns))
        if (share_regroup > 1e-6).any():
            rules += rule_a(dense_regroup, part_regroup, share_regroup, on_regroup, REGROUP)
        if s_legs["pelvis"] in followed:
            lowered = dataclasses.replace(
                plan_, mapping=regroup, choices={**plan_.choices, **dict.fromkeys(followed, "rigid")}
            )
            # their own new bones: the chains of the groups made rigid join the default build's
            again = _skirt_candidates(
                ts,
                ss,
                lowered,
                flat,
                sv,
                dominant,
                cloth,
                all_legs,
                t_legs,
                s_legs,
                mapped_regroup,
                columns,
                rows_ok,
                chain_anchors,
            )
            again = [dataclasses.replace(c, rule=REGROUP + c.rule) for c in again]
            skirt_rules += again
            rules += [(c.rule, c.weights, c.solver) for c in again]
    if len(rules) == 1:
        return Hems(note="skin not writable")

    names = [(c.peek_name() or "").lower() for c in t_clips]
    checks = []
    inverse = np.array([np.linalg.inv(t_rest[c]) for c in columns])
    rest_rotation = np.linalg.inv(t_rest[t_legs["pelvis"]][:3, :3])
    for suffix in HEM_CLIPS:
        clip = next((c for c, n in zip(t_clips, names) if n.endswith("_" + suffix.lower())), None)
        if clip is not None:
            tree = clip.read_typetree()
            prepared = ts.clip_frames(tree, cache=True)
            times, pose_at, _ = prepared
            frames = []
            for frame in measured_frames(len(times)):
                pose = pose_at(frame)
                frames.append(
                    (
                        frame,
                        np.array([pose[c] for c in columns]) @ inverse,
                        pose[t_legs["pelvis"]][:3, 3],
                        pose[t_legs["pelvis"]][:3, :3] @ rest_rotation @ up,
                        pose,
                    )
                )
            checks.append((suffix, clip, tree, prepared, frames))
    if not checks:
        return Hems(note="no check clips")
    limit = max(
        hm.STRETCH_FLOOR,
        max(hm.stretch_p99(own.rest, own.posed(pose), t_edges) for *_, frames in checks for *_, pose in frames),
    )
    scores, four, solved = [], {}, {}

    def judged(
        rule: str,
        joints4: np.ndarray,
        weights4: np.ndarray,
        solver: ms.Solver | None,
        points: np.ndarray,
        prune: bool = False,
    ) -> hm.Score:
        deep, worst = {}, 1.0
        for suffix, clip, tree, prepared, frames in checks:
            step()
            deep[suffix] = 0.0
            if solver is not None:  # the chains' turns in time, as the clip will hold them
                if (solver, _key(clip)) not in solved:
                    solved[(solver, _key(clip))] = skirt_sequence(ts, solver, tree, prepared=prepared, step=step)
                turns = dict(zip(*solved[(solver, _key(clip))]))
            for frame, matrices, centre, carried, pose in frames:
                if solver is not None:
                    matrices = np.concatenate([matrices, solver.posed(pose, turns[frame]) @ solver.inverse])
                posed = hm.skin(points, joints4, weights4, matrices)
                state, depth = hm.cover(posed, covered, triangles, centre, carried)
                deep[suffix] = max(deep[suffix], hm.deep_share(state, depth))
                worst = max(worst, hm.stretch_p99(points, posed, edges))
            if prune and scores and hm.cannot_win(hm.Score(rule, deep, worst), scores, limit):
                return hm.Score(rule, deep, worst, complete=False)
        return hm.Score(rule, deep, worst)

    setup, plain, chained, thin = HEM_UNITS
    costs = [chained if solver is not None else plain for _, _, solver in rules]
    total, done = setup + sum(costs) + (thin if physics and shapes_writable else 0), setup
    for (rule, weights, solver), cost in zip(rules, costs):
        meter.phase("hems", done, total)
        joints4, weights4 = hm.keep_four(weights)
        scores.append(judged(rule, joints4, weights4, solver, sv, prune=True))
        four[rule] = (joints4, weights4)
        done += cost
    meter.phase("hems", done, total)
    best = hm.choose(scores, limit)
    result = Hems(best.rule, "", limit, scores)
    if physics and shapes_writable:
        tried = []

        def judged_thin(*args: Any) -> hm.Score:  # one judgement per factor tried (THIN_FACTORS)
            meter.phase("hems", done + thin * len(tried) / len(THIN_FACTORS), total)
            tried.append(1)
            return judged(*args)

        _thin_legs(
            result,
            best,
            judged_thin,
            four[best.rule],
            next(solver for rule, _, solver in rules if rule == best.rule),
            sv,
            all_legs,
            triangles,
            at,
            up,
        )
    if best.rule == "rigid":
        return result
    joints4, weights4 = four[best.rule]
    chosen = next(weights for rule, weights, _ in rules if rule == best.rule)
    bounds = [*flat.starts, len(sv)]
    result.regrouped = best.rule.startswith(REGROUP)
    if best.rule.removeprefix(REGROUP).startswith("skirt"):
        candidate = next(c for c in skirt_rules if c.rule == best.rule)
        skirt = candidate.skirt  # the chosen build's bones: a regrouped build may have more than the default one
        result.skirt = skirt
        skirt.solver = candidate.solver
        skirt.solved = {key: value for (solver, key), value in solved.items() if solver is candidate.solver}
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
        rows = np.where(changed[bounds[k] : bounds[k + 1]])[0]
        result.parts.append((rows, nodes[joints4[bounds[k] + rows]], weights4[bounds[k] + rows]) if len(rows) else None)
    return result


RIGID_ROUNDS = 8  # stiffening rounds at most
RIGID_STEPS = (0.5, 1.0)  # how far a round moves the worst vertices' weights toward their anchor
RIGID_SHARE = 99.5  # the edges past this percentile of stretch (and past the limit) are the worst


def rigidify(
    target: Costume, source: Costume, plan_: Plan, probes: list[list[np.ndarray]], hem: Hems, writable: list[bool]
) -> None:
    """Reduce excessive stretch by blending worst-edge weights toward their mapped rigid anchors.

    For a costume past the stretch limit (``plan.unfit``), vertices of its worst stretched edges under the slot's
    probes get their weights blended toward the slot bone their anchor maps to (as a rigid group is mapped), round by
    round while the stretch falls, until it is within the limit or nothing improves; kept only when it is better than
    before. The new weights join the hem's (``hem.parts``) and the plan takes the stretch reached. Parts whose skin
    cannot be rewritten and Rule B's builds are left as they are.

    Args:
        target: Slot costume supplying rest bones and destination skin matrices.
        source: Incoming costume supplying topology, influences, and anchor relationships.
        plan_: Mutable mapping/stretch verdict updated only when stiffening improves it.
        probes: Slot world poses measuring candidate edge stretch.
        hem: Mutable hem result whose existing weight edits are merged with stiffened rows.
        writable: Per-source-part flags restricting which skin rows may change.
    """
    if not plan_.unfit or plan_.fit is None or hem.skirt is not None:
        return
    ts, ss = target.skeleton, source.skeleton
    s_rest, t_rest = ss.world(), ts.world()
    flat = flatten(source, s_rest)
    sv = plan_.fit.vertices
    reference = sv if plan_.fit.shape == "stored" else flat.vertices
    mapped = np.vectorize(lambda n: plan_.mapping.get(int(n), 0), otypes=[int])(flat.joints)
    bounds = [*flat.starts, len(sv)]
    rows_ok = np.concatenate([np.full(bounds[k + 1] - bounds[k], ok) for k, ok in enumerate(writable)])
    # each vertex's anchor on the slot: its dominant bone's Biped bone (itself for a Biped bone), mapped
    dominant = flat.joints[np.arange(len(sv)), np.argmax(flat.weights, axis=1)]
    anchors = {}
    for n in set(dominant.tolist()) - {-1}:
        a = n if ss.nodes[n].name.startswith("Bip001") else ss.biped_ancestor(n)
        anchors[n] = plan_.mapping.get(a if a is not None else n, 0)
    anchor = np.array([anchors.get(int(n), 0) for n in dominant])
    hem_rows: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = list(hem.parts) + [None] * (
        len(source.parts) - len(hem.parts)
    )
    columns = sorted(
        set(mapped[flat.weights > 0].tolist())
        | set(anchor.tolist())
        | {int(n) for change in hem_rows if change is not None for n in change[1].ravel().tolist()}
    )
    index = {c: k for k, c in enumerate(columns)}
    cols = np.vectorize(lambda n: index.get(int(n), 0), otypes=[int])(np.where(flat.weights > 0, mapped, columns[0]))
    dense = hm.densify(cols, flat.weights, len(columns))
    for k, change in enumerate(hem_rows):  # the hem's weights first, where it rewrote them
        if change is not None:
            rows, nodes, weights = change
            dense[bounds[k] + rows] = hm.densify(np.vectorize(index.get, otypes=[int])(nodes), weights, len(columns))
    start = dense.copy()
    matrices = [
        np.array([pose[c] for c in columns]) @ np.array([_inverse(t_rest[c], ts.nodes[c].name) for c in columns])
        for pose in [t_rest, *probes]
    ]
    edges = flat.edges
    weighted = flat.weights.sum(axis=1) > 1e-9

    def measure(weights: np.ndarray) -> tuple[float, np.ndarray]:
        joints4, weights4 = hm.keep_four(weights)
        worst = np.ones(len(edges))
        l0 = np.linalg.norm(reference[edges[:, 0]] - reference[edges[:, 1]], axis=1)
        ok = l0 > 1e-6
        for m in matrices:
            posed = hm.skin(sv, joints4, weights4, m)
            l1 = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)
            ratio = np.where(ok, l1 / np.maximum(l0, 1e-12), 1.0)
            worst = np.maximum(worst, np.maximum(ratio, 1 / np.maximum(ratio, 1e-6)))
        return (float(np.percentile(worst[ok], 99.9)) if ok.any() else 1.0), worst

    limit = stretch_limit(plan_.own)
    before, worst = measure(dense)
    now = before
    onehot = np.zeros_like(dense)
    onehot[np.arange(len(sv)), [index[int(a)] for a in anchor]] = 1.0
    for _ in range(RIGID_ROUNDS):
        if now <= limit:
            break
        cut = max(limit, float(np.percentile(worst, RIGID_SHARE)))
        rows = np.unique(edges[worst > cut].ravel())
        rows = rows[rows_ok[rows] & weighted[rows]]
        if not len(rows):
            break
        best = None
        for step in RIGID_STEPS:
            trial = dense.copy()
            trial[rows] = (1 - step) * dense[rows] + step * onehot[rows]
            value, trial_worst = measure(trial)
            if best is None or value < best[0]:
                best = (value, trial, trial_worst)
        if best[0] >= now * (1 - 1e-3):
            break
        now, dense, worst = best
    if now >= before * (1 - 1e-3):
        plan_.repair = f"stiffening did not help ({before:.2f})"
        return
    changed = np.abs(dense - start).max(axis=1) > 1e-9
    nodes = np.array(columns)
    joints4, weights4 = hm.keep_four(dense)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = []
    for k in range(len(source.parts)):
        mine = changed[bounds[k] : bounds[k + 1]]
        if hem_rows[k] is not None:  # retain hem edits alongside stiffened weights
            mine = mine.copy()
            mine[hem_rows[k][0]] = True
        rows = np.where(mine)[0]
        parts.append((rows, nodes[joints4[bounds[k] + rows]], weights4[bounds[k] + rows]) if len(rows) else None)
    hem.parts = parts
    plan_.repair = f"worst edges stiffened, stretch {before:.2f} -> {now:.2f} (limit {limit:.2f})"
    plan_.stretch, plan_.unfit = now, now > limit


THIN_DEPTH = 0.01  # a leg vertex this far inside the cloth at rest (metres) or more may be thinned
THIN_FACTORS = (0.85, 0.7)  # how much of its distance to the leg's axis a thinned vertex keeps, tried in order
THIN_GAIN = 1.0  # the deep pass-through, in points, thinning must take off to be kept


def _thin_legs(
    result: Hems,
    best: hm.Score,
    judged: Callable,
    four: tuple[np.ndarray, np.ndarray],
    solver: ms.Solver | None,
    sv: np.ndarray,
    legs: np.ndarray,
    triangles: np.ndarray,
    at: Mapping[str, np.ndarray],
    up: np.ndarray,
) -> None:
    """Optimize Physics: move the costume's leg vertices deep inside the cloth toward their leg's axis.

    The vertices deep inside the cloth at rest (``THIN_DEPTH``) are moved toward their leg's axis, so that knees and
    shins stay under a skirt or gown; judged with the chosen rule's weights by the same pass-through measure, kept when
    it takes ``THIN_GAIN`` points off and leaves no clip worse than the rule alone (``result.thin``); cloth geometry and
    its stretch stay unchanged. Removing hidden triangles would leave a gap where the cloth lifts: thinning only.
    """
    state, depth = hm.cover(sv, legs, triangles, at["pelvis"], up)
    with np.errstate(invalid="ignore"):
        rows = legs[(state == 1) & (depth < -THIN_DEPTH)]
    if not len(rows):
        return
    points = sv[rows]
    left, right = (
        _nearest_on_legs(points, at[f"thigh {side}"], at[f"calf {side}"], at[f"foot {side}"]) for side in "LR"
    )
    nearer = np.linalg.norm(points - left, axis=1) <= np.linalg.norm(points - right, axis=1)
    closest = np.where(nearer[:, None], left, right)
    chosen = best
    for factor in THIN_FACTORS:
        moved = sv.copy()
        moved[rows] = closest + factor * (points - closest)
        score = judged(f"{best.rule} thin {factor}", *four, solver, moved)
        if score.mean <= chosen.mean - THIN_GAIN and all(score.deep[c] <= best.deep[c] for c in score.deep):
            chosen, result.thin, result.thin_score = score, (rows, moved[rows]), score
            break


def _nearest_on_legs(points: np.ndarray, hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
    """Per point, the nearest point of the leg's axis (hip to knee to ankle)."""
    out, best = np.empty_like(points), np.full(len(points), np.inf)
    for a, b in ((hip, knee), (knee, ankle)):
        d = b - a
        t = np.clip((points - a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0)
        near = a + t[:, None] * d
        distance = np.linalg.norm(points - near, axis=1)
        take = distance < best
        out[take], best[take] = near[take], distance[take]
    return out


SWING_MIN = math.radians(2)  # a chain the source's own clips turn less than this stays rigid (the artist kept it so)
SWING_LIMITS = (math.radians(10), math.radians(30))  # how far a chain may swing: 1.5 times the source's own, in these


def swing_of(skeleton: Skeleton, clips: Iterable[Any], nodes: Iterable[int]) -> dict[int, float]:
    """Measure each node's maximum quaternion-curve turn from rest in radians, or zero without turns.

    Args:
        skeleton: Source hierarchy supplying rest quaternions and binding-path hashes.
        clips: Source-owned clips whose rotation curves are sampled without composing world poses.
        nodes: Source node indices for which swing limits are needed.
    """
    wanted = set(nodes)
    out = dict.fromkeys(wanted, 0.0)
    by_hash = {zlib.crc32(skeleton.nodes[n].path.encode()): n for n in wanted}
    for clip in clips:
        tree = skeleton.graph.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] == 2 and b["path"] in by_hash for b in bindings):
            continue
        for node, attributes in bind_tracks(bindings, decode_clip(tree).curves, by_hash).items():
            if 2 not in attributes:
                continue
            q = np.array(
                [np.resize(np.asarray(part, float), max(len(p) for p in attributes[2])) for part in attributes[2]]
            ).T
            q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
            rest = np.asarray(skeleton.nodes[node].local[1], float)
            rest /= max(float(np.linalg.norm(rest)), 1e-12)
            dot = np.clip(np.abs(q @ rest), 0.0, 1.0)
            out[node] = max(out[node], float(2 * np.arccos(dot).max()))
    return out


def own_keys(skeleton: Skeleton, clips: Iterable[Any], nodes: list[int]) -> dict[tuple[str, ...], tuple]:
    """Index source rotation keys by clip-name suffixes of at least two tokens.

    Match suffixes below the
    full name (``Formation_Idle``, ``Move_Ing``, ...; the longest suffix is tried first, ``Spring.keyed``): the
    clip's frame times scaled to 0..1 and, per index into ``nodes`` it turns, its local quaternions there.

    Args:
        skeleton: Source hierarchy supplying binding paths and clip readers through its graph.
        clips: Source AnimationClips contributing local quaternion or Euler rotation curves.
        nodes: Source node order defining the bone indices stored in each suffix's rotation map.
    """
    by_hash = {zlib.crc32(skeleton.nodes[n].path.encode()): n for n in nodes}
    index = {n: k for k, n in enumerate(nodes)}
    out: dict[tuple[str, ...], tuple] = {}
    for clip in clips:
        tree = skeleton.graph.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] in (2, 4) and b["path"] in by_hash for b in bindings):
            continue
        decoded = decode_clip(tree)
        times = np.asarray(decoded.times, float)
        frames = len(times)
        orders = euler_orders(bindings, by_hash)
        rotations = {}
        for node, attributes in bind_tracks(bindings, decoded.curves, by_hash).items():
            if 2 in attributes:
                q = np.array([np.resize(np.asarray(part, float), frames) for part in attributes[2]]).T
            elif 4 in attributes:
                e = np.array([np.resize(np.asarray(part, float), frames) for part in attributes[4]]).T
                q = np.array([euler_to_quat(list(v), orders.get(node, UNITY_ORDER)) for v in e])
            else:
                continue
            rotations[index[node]] = q
        if not rotations:
            continue
        phases = (times - times[0]) / max(float(times[-1] - times[0]), 1e-9) if frames > 1 else np.zeros(1)
        tokens = [t for t in (clip.peek_name() or "").lower().split("_") if t]
        for i in range(1, len(tokens) - 1):
            out.setdefault(tuple(tokens[i:]), (phases, rotations))
    return out


def springs(target: Costume, source: Costume, plan_: Plan, s_clips: list[Any], fit: Fit) -> list[Skirt]:
    """Build swinging secondary chains away from the legs for Optimize Physics.

    Rigid hair, ribbon, and loose-coat chains become new slot bones, one Skirt per anchor,
    swinging with model_skirts.Spring in every slot clip. Leg cloth belongs to the hem rules and Rule B. A
    chain swings only when the source's own clips turn it (``SWING_MIN``), at most 1.5 times as far. The skirts' bases
    are set where they join the slot's skeleton (:func:`skirt_bones`). A slot clip whose name the source's clips share
    (``Formation_Idle``, ``Cafe_Walk``) plays the source's own keys of the chains instead, time-scaled onto it
    (:func:`own_keys`, ``Spring.keyed``).

    Args:
        target: Slot costume supplying destination anchors and existing hierarchy paths.
        source: Incoming costume supplying secondary chains and their weighted cloth.
        plan_: Mapping and rigid/follow choices selecting eligible non-leg groups.
        s_clips: Source clips defining observed swing limits and matching keyed motions.
        fit: Bound shape used to carry each chain with its fitted cloth.
    """
    ts, ss = target.skeleton, source.skeleton
    s_legs = legs_of(ss)
    lower = lower_anchors(ss, s_legs) if s_legs is not None else set()
    flat = flatten(source, ss.world())
    t_rest = ts.world()
    sv = fit.vertices
    used = set(flat.joints[flat.weights > 0].tolist())
    out = []
    for anchor, members in sorted(plan_.groups.items()):
        if plan_.choices.get(anchor) != "rigid" or anchor in lower or not members:
            continue
        member_set = set(members)
        roots = sorted({chain_root(ss, n) for n in members if n in used})
        roots = [r for r in roots if r in member_set]
        if not roots:
            continue
        swing = swing_of(ss, s_clips, [j for r in roots for j in [r, *ss.descendants(r)] if j in member_set])
        limits = {}
        for r in roots:
            turned = max(swing.get(j, 0.0) for j in [r, *ss.descendants(r)] if j in swing)
            if turned >= SWING_MIN:
                limits[r] = min(max(1.5 * turned, SWING_LIMITS[0]), SWING_LIMITS[1])
        if not limits:
            continue
        wanted: set[int] = set()
        for n in member_set & used:
            if chain_root(ss, n) not in limits:
                continue
            while n not in wanted:
                wanted.add(n)
                if n in limits:
                    break
                n = ss.nodes[n].parent
        sources = sorted(wanted)
        new = {n: k for k, n in enumerate(sources)}
        parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
        slot_anchor = plan_.mapping[anchor]
        s_rest = ss.world()
        # each chain moves with its cloth from the source's space to the bound shape (as Rule B's)
        carry = {}
        for k, n in enumerate(sources):
            if parents[k] < 0:
                share = (flat.weights * np.isin(flat.joints, [j for j in sources if chain_root(ss, j) == n])).sum(
                    axis=1
                )
                rows = np.where(share > 0.5)[0]
                carry[n] = (
                    ms.rigid_fit(flat.vertices[rows], sv[rows], share[rows])
                    if len(rows) >= 3
                    else (t_rest[slot_anchor] @ _inverse(s_rest[anchor], ss.nodes[anchor].name))
                )
        rest = np.array([carry[chain_root(ss, n)] @ s_rest[n] for n in sources])
        local = np.array(
            [np.linalg.inv(rest[p] if p >= 0 else t_rest[slot_anchor]) @ rest[k] for k, p in enumerate(parents)]
        )
        chains = ms.Chains(slot_anchor, parents, local, rest)
        taken = {node.path for node in ts.nodes}
        names, paths, locals_ = [], [], []
        for k, n in enumerate(sources):
            name = ss.nodes[n].name
            parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[slot_anchor].path
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
        # as the new bones will stand (position, rotation, scale below their parent): a chain whose rest these cannot
        # reproduce (a sheared anchor) stays rigid
        placed: list[np.ndarray] = []
        for k, p in enumerate(parents):
            placed.append((placed[p] if p >= 0 else t_rest[slot_anchor]) @ trs(*locals_[k]))
        if float(np.abs(np.array(placed) - rest).max()) > 1e-5:
            logger.debug("Swinging chains on %s left rigid: rest not reproduced", ss.nodes[anchor].name)
            continue
        spring = ms.Spring(chains, {new[r]: limit for r, limit in limits.items()})
        if not spring.roots or not (spring.lengths >= ms.MIN_LENGTH).any():
            continue
        # a slot clip the source has by name plays the source's own keys of these chains
        spring = ms.Spring(
            chains,
            {new[r]: limits[r] for r in limits if spring.lengths[spring.roots.index(new[r])] >= ms.MIN_LENGTH},
            own_keys(ss, s_clips, sources),
            np.array([trs(*ss.nodes[n].local) for n in sources]),
        )
        out.append(Skirt(sources, names, parents, locals_, slot_anchor, spring, -1))
    return out


FACE_OFFSET = 0.002  # a source face bone resting this far (metres) or more from the slot bone it maps to is retargeted


class FaceRetarget:
    """Drive added source-face bones with slot expressions around the source's facial landmarks.

    Eye, brow and mouth bones below the slot's head rest where the source's face has
    them, following the slot's expressions: in every frame of a slot clip each new bone takes the turn and the move
    its slot bone of the same name makes against the head, about its own place (a lid closes about the source's eye,
    not the slot's). slots records the slot bone each new bone follows.
    """

    def __init__(self, chains: ms.Chains, head: int, slots: list[int], t_rest: list[np.ndarray]):
        """Prepare face motion relative to the destination head's rest frame.

        Args:
            chains: Added source-face bones in their destination rest positions.
            head: Destination head node index used as the expression reference frame.
            slots: Destination face node followed by each added bone, in chain order.
            t_rest: Destination skeleton world matrices at rest.
        """
        self.chains, self.head, self.slots = chains, head, slots
        self.roots = list(range(len(slots)))
        inverse = np.linalg.inv(t_rest[head])
        self.rel0 = [inverse @ t_rest[m] for m in slots]  # the slot bones against the head at rest
        self.new0 = [inverse @ m for m in chains.rest]  # the new bones likewise

    def follow(
        self, skeleton: Skeleton, tree: Mapping[str, Any], frames: list[int], *, prepared=None, step=lambda: None
    ) -> list[dict[int, np.ndarray]]:
        """Sample scale-free local face transforms, omitting bones that stay at rest against the head.

        Args:
            skeleton: Slot hierarchy used to compose head and expression-bone world poses.
            tree: Slot AnimationClip typetree containing the expression curves.
            frames: Nonempty clip-frame indices to sample in order.
            prepared: Optional clip_frames result covering head and slot bones; None prepares only those dependencies.
            step: No-argument yield/cancellation callback before each frame sample.
        """
        _, pose, _ = prepared if prepared is not None else skeleton.clip_frames(tree, [self.head, *self.slots])
        ch = self.chains
        out: list[dict[int, np.ndarray]] = []
        for f in frames:
            step()
            world = pose(f)
            back = np.linalg.inv(world[self.head])
            placed: list[np.ndarray] = []
            for k, m in enumerate(self.slots):
                rel = back @ world[m]
                turn = _rotation(rel) @ _rotation(self.rel0[k]).T
                w = self.new0[k].copy()
                w[:3, :3] = turn @ w[:3, :3]
                w[:3, 3] = self.new0[k][:3, 3] + rel[:3, 3] - self.rel0[k][:3, 3]
                placed.append(w)
            turns = {}
            for k, p in enumerate(ch.parents):
                local = placed[k] if p < 0 else np.linalg.inv(placed[p]) @ placed[k]
                local[:3, :3] = local[:3, :3] @ np.linalg.inv(ms.scale_of(local))
                turns[k] = local
            out.append(turns)
        moving = {
            k
            for k in self.roots
            if any(not np.allclose(t[k], out[0][k], atol=1e-6) for t in out)
            or not np.allclose(out[0][k], ch.hanging(k), atol=1e-6)
        }
        return [{k: t[k] for k in sorted(moving)} for t in out]

    def posed(self, pose: Sequence[np.ndarray], turns: Mapping[int, np.ndarray]) -> np.ndarray:
        """Place added face bones under the slot pose's head using scale-free local turns."""
        return self.chains.posed(np.asarray(pose[self.chains.anchor]), turns)


def _rotation(m: np.ndarray) -> np.ndarray:
    return m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)


def face_retarget(target: Costume, source: Costume, plan_: Plan, fit: Fit) -> Skirt | None:
    """Build added face bones for source landmarks displaced from their name-matched slot bones.

    Bones displaced by at least FACE_OFFSET, together with their face-bone ancestors,
    become slot bones below the head. FaceRetarget drives expressions around the source
    landmarks. The bones follow the head into the bound shape, as flatten places the face.

    Args:
        target: Slot costume whose head and expression bones drive the new face bones.
        source: Incoming costume supplying face landmarks, skin influences, and ancestor structure.
        plan_: Bone mapping identifying corresponding named slot expression bones.
        fit: Bound costume shape carrying the source landmarks into destination space.

    Returns:
        Added face-bone Skirt, or None without displaced landmarks, shared heads, or reproducible rest transforms.
    """
    ts, ss = target.skeleton, source.skeleton
    t_head, s_head = ts.first(HEAD), ss.first(HEAD)
    if t_head is None or s_head is None:
        return None
    flat = flatten(source, ss.world())
    t_rest, s_rest = ts.world(), ss.world()
    used = set(flat.joints[flat.weights > 0].tolist())
    face = [
        n
        for n in sorted(used)
        if n >= 0 and face_bone(ss, n) and n in plan_.mapping and ts.nodes[plan_.mapping[n]].name == ss.nodes[n].name
    ]
    if not face:
        return None
    rows = np.where(np.isin(flat.joints, face).any(axis=1))[0]
    carry = (
        ms.rigid_fit(flat.vertices[rows], fit.vertices[rows], np.ones(len(rows)))
        if len(rows) >= 3
        else (t_rest[t_head] @ _inverse(s_rest[s_head], HEAD))
    )
    apart = {
        n
        for n in face
        if float(np.linalg.norm((carry @ s_rest[n])[:3, 3] - t_rest[plan_.mapping[n]][:3, 3])) >= FACE_OFFSET
    }
    if not apart:
        return None
    wanted: set[int] = set()
    for n in apart:  # with the face bones above it, so the hierarchy holds
        while n is not None and n != s_head and n not in wanted and face_bone(ss, n) and n in plan_.mapping:
            wanted.add(n)
            n = ss.nodes[n].parent
    sources = sorted(wanted)
    new = {n: k for k, n in enumerate(sources)}
    parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
    rest = np.array([carry @ s_rest[n] for n in sources])
    local = np.array([np.linalg.inv(rest[p] if p >= 0 else t_rest[t_head]) @ rest[k] for k, p in enumerate(parents)])
    taken = {node.path for node in ts.nodes}
    names, paths, locals_ = [], [], []
    for k, n in enumerate(sources):
        name = ss.nodes[n].name + "_costume"
        parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[t_head].path
        while f"{parent_path}/{name}" in taken:
            name += "_costume"
        names.append(name)
        paths.append(f"{parent_path}/{name}")
        taken.add(paths[-1])
        if parents[k] >= 0:
            locals_.append(ss.nodes[n].local)
        else:
            m = local[k]
            scale = np.linalg.norm(m[:3, :3], axis=0)
            locals_.append((tuple(m[:3, 3]), tuple(ms.quaternion(m[:3, :3] / scale)), tuple(scale)))
    placed: list[np.ndarray] = []
    for k, p in enumerate(parents):
        placed.append((placed[p] if p >= 0 else t_rest[t_head]) @ trs(*locals_[k]))
    if float(np.abs(np.array(placed) - rest).max()) > 1e-5:
        logger.debug("Face bones left on the slot's: rest not reproduced")
        return None
    chains = ms.Chains(t_head, parents, local, rest)
    solver = FaceRetarget(chains, t_head, [plan_.mapping[n] for n in sources], t_rest)
    return Skirt(sources, names, parents, locals_, t_head, solver, -1)


def skirt_bones(skeleton: Skeleton, skirt: Skirt) -> None:
    """Append chain nodes to the slot skeleton before _edit_prefabs creates Unity objects.

    Args:
        skeleton: Mutable destination hierarchy whose length must equal skirt.base.
        skirt: Added bone names, parent/anchor choices, locals, and expected solver rest matrices.

    Raises:
        ServiceError: The skeleton changed or the appended nodes fail to reproduce the solver's rest pose.
    """
    if len(skeleton.nodes) != skirt.base:
        raise refuse("skeleton changed")
    for k, (name, parent, local) in enumerate(zip(skirt.names, skirt.parents, skirt.locals)):
        father = skirt.node(parent) if parent >= 0 else skirt.anchor_of(k)
        path = f"{skeleton.nodes[father].path}/{name}" if skeleton.nodes[father].path else name
        skeleton.nodes.append(Node(name, path, father, local, None, None, True))
    world = skeleton.world()
    for k in range(len(skirt.sources)):
        if float(np.abs(world[skirt.node(k)] - skirt.solver.chains.rest[k]).max()) > 1e-4:
            raise refuse("rest pose not reproduced")


def measured_frames(count: int) -> list[int]:
    """Select unique HEM_FRAMES evenly spaced indices from a clip with count frames."""
    return sorted({min(count - 1, round(k * (count - 1) / max(1, HEM_FRAMES - 1))) for k in range(HEM_FRAMES)})


def skirt_sequence(
    skeleton: Skeleton,
    solver: ms.Solver,
    tree: Mapping[str, Any],
    *,
    prepared=None,
    step: Callable[[], None] = lambda: None,
) -> tuple[list[int], list[dict]]:
    """Solve chain turns at stride, final, and measured frames, or every frame for face motion.

    Args:
        skeleton: Slot hierarchy used to compose the solver's anchor/body dependencies.
        solver: Leg-clearance, spring, or face solver defining the chain motion.
        tree: Slot clip typetree supplying timing, loop state, and optional matching source-key name.
        prepared: Optional clip_frames result covering solver dependencies; None samples the skeleton.
        step: No-argument yield/cancellation callback during pose sampling and solver frames.

    Returns:
        Solved clip-frame indices and corresponding scale-free local bone transforms.
    """
    from .model_arm_clips import clip_times

    if isinstance(solver, FaceRetarget):  # a blink lasts a few frames
        every = list(range(len(clip_times(tree))))
        return every, solver.follow(skeleton, tree, every, prepared=prepared, step=step)
    times, pose, _ = prepared if prepared is not None else skeleton.clip_frames(tree)
    count = len(times)
    frames = sorted(set(range(0, count, ms.SOLVE_EVERY)) | {count - 1} | set(measured_frames(count)))
    if isinstance(solver, ms.Spring):
        # the source's own keys for a clip it has by name, else the swing; then gravity
        poses = []
        for f in frames:
            step()
            poses.append(pose(f))
        keyed = solver.keyed(str(tree.get("m_Name") or ""), [times[f] for f in frames])
        turns = (
            keyed
            if keyed is not None
            else solver.sequence(
                [times[f] for f in frames], lambda k: poses[k], bool(tree["m_MuscleClip"].get("m_LoopTime"))
            )
        )
        return frames, solver.settle(turns, [np.asarray(p[solver.chains.anchor]) for p in poses])
    loop = bool(tree["m_MuscleClip"].get("m_LoopTime"))
    return frames, solver.sequence([times[f] for f in frames], lambda k: pose(frames[k]), loop, step=step)


def curve_frames(skeleton: Skeleton, skirts: Sequence[Skirt], tree: Mapping[str, Any]):
    """Prepare one clip snapshot posing only the chains' anchor, body, and face dependencies.

    New curves target new bones, never these original anchors and face
    slots. Arm copies supply their corrected tree before taking this snapshot.

    Args:
        skeleton: Slot hierarchy used to compose the required node poses with memory caching.
        skirts: Added chain descriptions whose solvers determine the dependency set.
        tree: Clip typetree before added chain curves, including any corrected arm curves.
    """
    nodes = set()
    for skirt in skirts:
        solver = skirt.solver
        if isinstance(solver, FaceRetarget):
            nodes.update([solver.head, *solver.slots])
        elif isinstance(solver, ms.Spring):
            nodes.add(solver.chains.anchor)
        else:
            nodes.update([solver.pelvis, *solver.leg_nodes, *solver.cloth.fixed])
            nodes.update(solver.chains.anchor_of(r) for r in solver.roots)
    return skeleton.clip_frames(tree, nodes, cache=True)


def skirt_curves(
    skeleton: Skeleton,
    skirt: Skirt,
    tree: dict,
    key: tuple[str, int] | None = None,
    *,
    prepared=None,
    step: Callable[[], None] = lambda: None,
) -> int | None:
    """Add solved chain rotation and position curves to a slot clip's dense grid.

    Interpolate skirt_sequence turns and shifts, or reuse measured solutions. Spring.keyed
    clips receive curves for every bone their source keys turn, rather than only chain roots.

    Args:
        skeleton: Destination hierarchy including the added bones and their animation binding paths.
        skirt: Chain solver and optional cached measurement solutions.
        tree: Mutable slot AnimationClip typetree receiving added dense curves.
        key: Optional original (CAB, path ID) selecting a measured solution; None solves on demand.
        prepared: Optional dependency-only clip_frames snapshot for on-demand solving.
        step: No-argument yield/cancellation callback during on-demand pose sampling and solving.

    Returns:
        Number of bones receiving curves, or None for a clip layout the writer does not edit.
    """
    grid = ms.dense_grid(tree)
    if grid is None:
        return None
    from .model_arm_clips import clip_times

    times = clip_times(tree)
    frames, turns = skirt.solved.get(key) or skirt_sequence(skeleton, skirt.solver, tree, prepared=prepared, step=step)
    roots = sorted(turns[0]) if turns else []
    if not roots:
        return 0
    rotations = np.array([[ms.quaternion(t[root][:3, :3]) for root in roots] for t in turns])
    positions = np.array([[t[root][:3, 3] for root in roots] for t in turns])
    at = np.clip(grid, times[0], times[-1])
    columns = []
    solved_at = np.array([times[f] for f in frames])
    for r in range(len(roots)):  # per root its rotation (4 curves), then its position (3)
        if len(frames) > 1:
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
    return len(roots)


@dataclass
class ClipBefore:
    """Retain decoded curves and storage counts for checking a Rule B edit against clip_snapshot."""

    decoded: Any  # model.DecodedClip
    layout: tuple[int, int, int]  # streamed, dense and constant curve counts


def _clip_layout(tree: Mapping[str, Any]) -> tuple[int, int, int]:
    clip = tree["m_MuscleClip"]["m_Clip"]["data"]
    return (
        clip["m_StreamedClip"]["curveCount"],
        clip["m_DenseClip"]["m_CurveCount"],
        len(clip["m_ConstantClip"]["data"]),
    )


def saved_tree(obj: Any) -> dict:
    """Parse a Unity reader's save_typetree bytes; read_typetree reads the file's original bytes."""
    from UnityPy.helpers import TypeTreeHelper
    from UnityPy.streams import EndianBinaryReader

    reader = EndianBinaryReader(obj.data, endian=obj.reader.endian)
    return TypeTreeHelper.read_typetree(
        obj._get_typetree_node(), reader, as_dict=True, byte_size=len(obj.data), assetsfile=obj.assets_file
    )


def clip_snapshot(tree: Mapping[str, Any]) -> ClipBefore:
    """Capture a clip typetree's decoded curves and storage counts before adding chain curves."""
    return ClipBefore(decode_clip(tree), _clip_layout(tree))


def clip_edit_error(before: ClipBefore, tree: Mapping[str, Any], added: int) -> str | None:
    """Check that added dense curves preserve original curves and keep clip metadata consistent.

    Streamed and constant samples compare exactly; dense samples compare within 1e-6.

    Args:
        before: Snapshot of the original decoded values and streamed/dense/constant counts.
        tree: Edited clip typetree read back from serialized object bytes.
        added: Number of scalar curves inserted between dense and constant storage, shifting constant indices.

    Returns:
        A difference description, or None when curves, muscle size, value deltas, and bindings agree.
    """
    streamed, dense, constant = before.layout
    layout = _clip_layout(tree)
    if layout != (streamed, dense + added, constant):
        return f"curves {layout} for {(streamed, dense + added, constant)}"
    muscle = tree["m_MuscleClip"]
    curves = sum(layout)
    if tree.get("m_MuscleClipSize") != ms.muscle_size(tree):
        return f"muscle clip size {tree.get('m_MuscleClipSize')} for {ms.muscle_size(tree)}"
    if len(muscle["m_ValueArrayDelta"]) != curves:
        return f"{len(muscle['m_ValueArrayDelta'])} value deltas for {curves} curves"
    if sum(ms.binding_dims(b) for b in tree["m_ClipBindingConstant"]["genericBindings"]) != curves:
        return "bindings and curves differ"
    after = decode_clip(tree)
    if len(after.times) != len(before.decoded.times) or len(after.curves) != curves:
        return "times or curve count"
    for index, values in before.decoded.curves.items():
        place = index if index < streamed + dense else index + added
        got = after.curves.get(place)
        if got is None or len(got) != len(values):
            return f"curve {index} missing"
        if streamed <= index < streamed + dense:
            same = np.allclose(got, values, rtol=0.0, atol=1e-6, equal_nan=True)
        else:
            same = np.array_equal(got, values, equal_nan=True)
        if not same:
            return f"curve {index} changed"
    return None


# --- the halo ---------------------------------------------------------------------------------------------


@dataclass
class Halo:
    """Describe a prefab's halo shape and all nodes/components that draw it.

    shape "single" names the node its FxFollower keeps beside the head (HaloRoot) and the one
    unskinned renderer below it. "skinned": no follower, one halo-shaded skinned renderer bound to one ``bone`` at or
    below the ``head`` (Ayame's NPC models); ``node`` is the renderer's. "none": no halo. Anything else names
    why the halo cannot be carried (several renderers, particles, a boss's halo); ``drawn`` lists, for every shape,
    the nodes and components that draw the halo, which hiding it turns off.
    """

    shape: str
    root: int | None = None  # the followed node
    follower: Any = None  # its FxFollower
    node: int | None = None  # the renderer's node, below the followed one
    renderer: Any = None  # the MeshRenderer (SkinnedMeshRenderer for "skinned")
    mesh: Any = None  # its MeshFilter's mesh (the skinned renderer's)
    materials: list[Any] = field(default_factory=list)
    bone: int | None = None  # "skinned": the bone it is bound to
    head: int | None = None  # "skinned": the head bone
    drawn: list[tuple[int, Any]] = field(default_factory=list)  # (node, renderer or particle system)


@dataclass
class HaloChange:
    """Describe slot-prefab halo replacement, hiding, or followed-node transplantation.

    The renderer at path drawing mesh
    (the slot's halo mesh) draws ``new_mesh`` with ``materials`` at ``local`` (position, rotation, scale), and the
    follower at ``root`` takes ``follower``'s offsets. With ``new_mesh`` None the slot's halo is hidden: every renderer
    and particle system at the ``hide`` paths draws nothing; a ``transplant`` (the source's followed nodes with their
    particles) then goes below the followed node at ``root``, whose follower takes ``follower``'s offsets.
    """

    path: str | None
    root: str | None
    mesh: tuple[str, int] | None
    new_mesh: int | None = None
    materials: list[int] = field(default_factory=list)
    local: tuple | None = None
    follower: dict[str, Any] | None = None
    hide: list[str] = field(default_factory=list)
    transplant: "Transplant | None" = None


@dataclass
class Transplant:
    """Carry nodes below a source's followed node as new objects for each slot prefab.

    GameObject, Transform, and drawing-component typetrees keep pointer markers: _IN is an
    object index here, _MESH/_MATERIAL a carried asset's file-local path ID, and _ROOT the
    slot's followed node. top lists Transforms parented directly below that node.
    """

    objects: list[tuple[Any, dict]] = field(default_factory=list)  # (source object, tree)
    top: list[int] = field(default_factory=list)
    carried: list[tuple[int, int]] = field(default_factory=list)  # (marker, path id) of the carried meshes, materials


_IN, _MESH, _MATERIAL, _ROOT = -1, -2, -3, -4
# what a transplanted halo may hold besides its nodes; an Animator (Mika's halo has one, with no controller) is left out
CARRIED = ("MeshFilter", "MeshRenderer", "ParticleSystem", "ParticleSystemRenderer")
LEFT_OUT = ("Animator",)


FOLLOWER_OFFSETS = ("TargetRelativePosition", "TargetRelativeRotation")
FOLLOWER_CLAMPS = ("ClampMinOffset", "ClampMaxOffset")
HEAD = "Bip001 Head"
DRAWN = ("MeshRenderer", "SkinnedMeshRenderer", "ParticleSystemRenderer", "ParticleSystem")


def halo_of(skeleton: Skeleton) -> Halo:
    """Identify the prefab halo below a drawn FxFollower node or through halo shading.

    The halo is the node an FxFollower moves with something drawn below it.
    Renderers with halo shading outside it (Aris's weapon effect) are not its halo. Without a follower, the
    renderers with halo shading are.

    Args:
        skeleton: Prefab hierarchy/graph supplying components, materials, and the followed-node subtree.
    """
    g = skeleton.graph
    # (Rule B's new bones have no objects yet)
    components = [
        [o for c in g.tree(node.go)["m_Component"] if (o := g.deref(node.go, c["component"])) is not None]
        if node.go is not None
        else []
        for node in skeleton.nodes
    ]

    def follower(i: int) -> Any:
        return next((o for o in components[i] if o.type.name == "MonoBehaviour" and g.script(o) == "FxFollower"), None)

    def drawn(i: int) -> list[Any]:
        return [o for o in components[i] if o.type.name.endswith("Renderer") or o.type.name == "ParticleSystem"]

    def below(i: int) -> list[int]:
        return [i] + skeleton.descendants(i)

    roots = [i for i in range(len(skeleton.nodes)) if follower(i) is not None and any(drawn(j) for j in below(i))]
    if not roots:
        shaded = [
            (i, o)
            for i, objs in enumerate(components)
            for o in objs
            if o.type.name in ("MeshRenderer", "SkinnedMeshRenderer")
            and "halo" in (_shader_kind(g, g.deref(o, m)) for m in g.tree(o)["m_Materials"])
        ]
        if not shaded:
            return Halo("none")
        return _skinned_halo(skeleton, shaded) or Halo("halo without a follower", drawn=shaded)
    shown = [(j, o) for i in roots for j in below(i) for o in drawn(j)]
    if len(roots) > 1:
        return Halo("several followed nodes", drawn=shown)
    root = roots[0]
    found = [(j, o) for j in below(root) for o in drawn(j)]
    if len(found) != 1 or found[0][1].type.name != "MeshRenderer":
        return Halo(", ".join(sorted(o.type.name for _, o in found)), root, follower(root), drawn=found)
    node, renderer = found[0]
    if node == root:
        return Halo("renderer on the followed node", root, follower(root), drawn=found)
    filt = next((o for o in components[node] if o.type.name == "MeshFilter"), None)
    mesh = g.deref(filt, g.tree(filt)["m_Mesh"]) if filt is not None else None
    if mesh is None:
        return Halo("no mesh", root, follower(root), drawn=found)
    materials = [g.deref(renderer, m) for m in g.tree(renderer)["m_Materials"]]
    if not materials or any(m is None for m in materials):
        return Halo("material not found", root, follower(root), drawn=found)
    return Halo("single", root, follower(root), node, renderer, mesh, materials, drawn=found)


def _skinned_halo(skeleton: Skeleton, shaded: list[tuple[int, Any]]) -> Halo | None:
    """A followerless halo that can be carried, or None.

    It is one halo-shaded skinned renderer bound to one bone at or below the head, with its mesh and materials
    (Ayame's). A boss's halo is not: Binah's has no head, Chesed's hangs from two bones of the body.
    """
    g = skeleton.graph
    head = skeleton.first(HEAD)
    if len(shaded) != 1 or head is None or shaded[0][1].type.name != "SkinnedMeshRenderer":
        return None
    node, renderer = shaded[0]
    tree = g.tree(renderer)
    bones = [
        skeleton.by_transform.get(_key(t)) if t is not None else None
        for t in (g.deref(renderer, b) for b in tree["m_Bones"])
    ]
    if len(bones) != 1 or bones[0] is None:
        return None
    bone = bones[0]
    head_path = skeleton.nodes[head].path
    if bone != head and not skeleton.nodes[bone].path.startswith(head_path + "/"):
        return None
    mesh = g.deref(renderer, tree["m_Mesh"])
    materials = [g.deref(renderer, m) for m in tree["m_Materials"]]
    if (
        mesh is None
        or len(g.tree(mesh).get("m_BindPose") or []) != 1
        or not materials
        or any(m is None for m in materials)
    ):
        return None
    return Halo("skinned", None, None, node, renderer, mesh, materials, bone, head, drawn=list(shaded))


def _mesh_tree(mesh: Any) -> dict:
    """A mesh's tree holding its vertex data.

    A mesh that streams it from its bundle's resource file (Yukino's halo) takes it along, so the copy reads in any
    file. Costume meshes go through it too: a streamed one would name the source bundle's resource file, which the game
    need not have loaded. Data that cannot be read is a refusal.
    """
    tree = mesh.read_typetree()
    stream = tree.get("m_StreamData") or {}
    if stream.get("size"):
        from UnityPy.helpers.ResourceReader import get_resource_data

        try:
            data = get_resource_data(stream["path"], mesh.assets_file, stream["offset"], stream["size"])
        except Exception:  # a missing resource file or a range past its end: the same refusal
            data = None
        if data is None or len(data) != stream["size"]:
            raise refuse("streamed mesh", part=mesh.peek_name())
        tree["m_VertexData"]["m_DataSize"] = data
        tree["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
    return tree


def _halo_change(
    t_skeleton: Skeleton,
    s_skeleton: Skeleton,
    carry_mesh: Callable[[Any], int],
    carry_material: Callable[[Any], int],
    names: tuple[str, str],
    head: np.ndarray | None = None,
) -> HaloChange | None:
    """The slot's halo after the swap, or None when the slot has no halo.

    The source's halo is carried (its mesh and materials added by ``carry_mesh`` and ``carry_material``) and drawn by
    the slot's halo renderer where the source draws its own, or else the slot's is hidden; never the slot's own.
    ``head``: where the bound costume's head sits against the slot's Head joint (``Fit.head``); a carried halo moves
    along by it.
    """
    change = _halo_carried(t_skeleton, s_skeleton, carry_mesh, carry_material, names)
    if change is not None and change.follower and head is not None and float(np.abs(head).max()) > 1e-6:
        _halo_shift(t_skeleton, change.follower, head)
    return change


def _halo_shift(t_skeleton: Skeleton, follower: dict[str, Any], head: np.ndarray) -> None:
    """Move the follower's offset (and its clamp box, when it carries one) by ``head``.

    ``head`` is a move in the prefab root's space, as the slot's followed node (the head) sees it at rest.
    """
    t_head = t_skeleton.first(HEAD)
    if t_head is None or "TargetRelativePosition" not in follower:
        return
    local = np.linalg.inv(t_skeleton.world()[t_head])[:3, :3] @ np.asarray(head, float)
    for name in ("TargetRelativePosition", *FOLLOWER_CLAMPS):
        box = follower.get(name)
        if isinstance(box, dict) and all(k in box for k in "xyz"):
            follower[name] = {**box, **{k: float(box[k]) + float(local[i]) for i, k in enumerate("xyz")}}


def _halo_carried(
    t_skeleton: Skeleton,
    s_skeleton: Skeleton,
    carry_mesh: Callable[[Any], int],
    carry_material: Callable[[Any], int],
    names: tuple[str, str],
) -> HaloChange | None:
    """:func:`_halo_change` before the head's move."""
    t, s = halo_of(t_skeleton), halo_of(s_skeleton)
    s_name, t_name = names
    if t.shape == "none":
        return None
    placed = _halo_place(t_skeleton, s_skeleton, t, s) if t.shape == "single" else None
    if placed is None:
        hidden = sorted({t_skeleton.nodes[node].path for node, _ in t.drawn})
        transplant = (
            _halo_transplant(s_skeleton, s, carry_mesh, carry_material, t_skeleton.nodes[t.root].transform.assets_file)
            if (t.root is not None and t.follower is not None and s.root is not None and s.follower is not None)
            else None
        )
        if transplant is not None:
            follower = s_skeleton.graph.tree(s.follower)
            logger.info(
                "Costume of %s on %s: halo carried with its nodes (slot: %s, source: %s)",
                s_name,
                t_name,
                t.shape,
                s.shape,
            )
            return HaloChange(
                None,
                t_skeleton.nodes[t.root].path,
                None,
                hide=hidden,
                transplant=transplant,
                follower={name: copy.deepcopy(follower[name]) for name in FOLLOWER_OFFSETS if name in follower},
            )
        if s.shape == "none":
            logger.info("Costume of %s on %s: the source has no halo, the slot's is hidden", s_name, t_name)
        else:
            logger.info(
                "Costume of %s on %s: the source's halo cannot be carried, the slot's is hidden (slot: %s, source: %s)",
                s_name,
                t_name,
                t.shape,
                s.shape,
            )
        return HaloChange(None, None, None, hide=hidden)
    local, follower = placed
    change = HaloChange(t_skeleton.nodes[t.node].path, t_skeleton.nodes[t.root].path, _key(t.mesh))
    scale = np.linalg.norm(local[:3, :3], axis=0)
    rotation = local[:3, :3] / scale
    if np.linalg.det(rotation) < 0:  # a mirrored halo: the mirror goes into the scale
        scale[0], rotation[:, 0] = -scale[0], -rotation[:, 0]
    quaternion = ms.quaternion(rotation)
    change.materials = [carry_material(m) for m in s.materials]
    change.new_mesh = carry_mesh(s.mesh)
    change.local = (local[:3, 3], quaternion, scale)
    change.follower = follower
    logger.info("Costume of %s on %s: halo %s carried (%s)", s_name, t_name, s.mesh.peek_name(), s.shape)
    return change


def _pointers(value: Any) -> Iterable[dict]:
    """Every pointer (``m_FileID``, ``m_PathID``) in a typetree."""
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            yield value
            return
        for item in value.values():
            yield from _pointers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _pointers(item)


def _halo_transplant(
    s_skeleton: Skeleton, s: Halo, carry_mesh: Callable[[Any], int], carry_material: Callable[[Any], int], into: Any
) -> Transplant | None:
    """The source's halo as new nodes (:class:`Transplant`), or None.

    The nodes are everything below its followed node, with the meshes and materials its renderers and particle systems
    draw carried into the slot's files. None (nothing carried) when something there points elsewhere (another node of
    the model, a script, a sprite) or cannot be added to the slot's prefab file ``into``.
    """
    g = s_skeleton.graph
    below = s_skeleton.descendants(s.root)
    if not below:
        return None
    root_key = _key(s_skeleton.nodes[s.root].transform)
    plan = Transplant()
    index: dict[tuple[str, int], int] = {}
    kept: list[list[Any]] = []
    for i in below:
        node = s_skeleton.nodes[i]
        components = []
        for c in g.tree(node.go)["m_Component"]:
            obj = g.deref(node.go, c["component"])
            if obj is None:
                return None
            name = obj.type.name
            if name in ("Transform",) or name in LEFT_OUT:
                continue
            if name not in CARRIED:
                return None
            components.append(obj)
        objects = [node.go, node.transform] + components
        if any(_Writer.addable(into, obj) for obj in objects):
            return None
        kept.append(objects)
        for obj in objects:
            index[_key(obj)] = len(index)
    # what the copies point at outside them: only meshes and materials, read before anything is carried
    outside: dict[tuple[str, int], Any] = {}
    for objects in kept:
        for obj in objects:
            for pointer in _pointers(g.tree(obj)):
                if not pointer["m_PathID"]:
                    continue
                key = (_external_cab(obj, pointer["m_FileID"]), pointer["m_PathID"])
                if key in index or key == root_key:
                    continue
                target = g.deref(obj, pointer)
                if obj.type.name == "GameObject" and target is not None and target.type.name in LEFT_OUT:
                    continue
                if target is None or target.type.name not in ("Mesh", "Material"):
                    return None
                outside[key] = target
    carried: dict[tuple[str, int], tuple[int, int]] = {}
    for key, target in outside.items():
        if target.type.name == "Mesh":
            carried[key] = (_MESH, carry_mesh(target))
        else:
            carried[key] = (_MATERIAL, carry_material(target))
    plan.carried = sorted(set(carried.values()))
    for objects in kept:
        for obj in objects:
            tree = copy.deepcopy(g.tree(obj))
            if obj.type.name == "GameObject":
                tree["m_Component"] = [
                    c
                    for c in tree["m_Component"]
                    if (_external_cab(obj, c["component"]["m_FileID"]), c["component"]["m_PathID"]) in index
                ]
            for pointer in _pointers(tree):
                if not pointer["m_PathID"]:
                    pointer["m_FileID"] = 0
                    continue
                key = (_external_cab(obj, pointer["m_FileID"]), pointer["m_PathID"])
                if key in index:
                    pointer.update(m_FileID=_IN, m_PathID=index[key])
                elif key == root_key:
                    pointer.update(m_FileID=_ROOT, m_PathID=0)
                else:
                    pointer.update(m_FileID=carried[key][0], m_PathID=carried[key][1])
            if obj.type.name == "Transform" and tree["m_Father"]["m_FileID"] == _ROOT:
                plan.top.append(len(plan.objects))
            plan.objects.append((obj, tree))
    return plan


def _halo_place(t_skeleton: Skeleton, s_skeleton: Skeleton, t: Halo, s: Halo) -> tuple[np.ndarray, dict] | None:
    """Where the slot's halo renderer draws the source's halo mesh.

    That is its local matrix below its parent and the follower offsets of the slot's followed node. A followed source
    halo keeps its place below its followed node and its follower's offsets; a skinned one puts the followed node where
    its bone sits against the node the slot's follower follows (the head), the mesh below it as the bone draws it. None
    when it cannot be carried.
    """
    s_world, t_world = s_skeleton.world(), t_skeleton.world()
    parent = t_skeleton.nodes[t.node].parent
    between = np.linalg.inv(t_world[t.root]) @ t_world[parent]
    if s.shape == "single":
        placed = np.linalg.inv(s_world[s.root]) @ s_world[s.node]
        follower = s_skeleton.graph.tree(s.follower)
        return np.linalg.inv(between) @ placed, {
            name: copy.deepcopy(follower[name]) for name in FOLLOWER_OFFSETS if name in follower
        }
    if s.shape != "skinned":
        return None
    g = t_skeleton.graph
    t_follower = g.tree(t.follower)
    if not all(name in t_follower for name in FOLLOWER_OFFSETS):
        return None
    target = t_follower.get("FollowTarget")
    target = g.deref(t.follower, target) if isinstance(target, dict) else None
    anchor = (
        s_skeleton.first(t_skeleton.nodes[t_skeleton.by_transform[_key(target)]].name)
        if target is not None and _key(target) in t_skeleton.by_transform
        else None
    )
    if anchor is None:
        return None

    def rotation(m: np.ndarray) -> np.ndarray:
        return m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)

    bone = s_world[s.bone]
    relative = np.linalg.inv(s_world[anchor]) @ bone
    turn = rotation(s_world[anchor]).T @ rotation(bone)
    if np.linalg.det(turn) < 0:
        return None
    bind = np.array([[s_skeleton.graph.tree(s.mesh)["m_BindPose"][0][f"e{r}{c}"] for c in range(4)] for r in range(4)])
    # the followed node takes the bone's place and turn; its own scale stays, so the mesh below takes the difference
    resize = np.diag(list(np.linalg.norm(bone[:3, :3], axis=0) / np.linalg.norm(t_world[t.root][:3, :3], axis=0)) + [1])
    x, y, z, w = (float(v) for v in ms.quaternion(turn))
    position = relative[:3, 3]
    offsets = {
        "TargetRelativePosition": {"x": float(position[0]), "y": float(position[1]), "z": float(position[2])},
        "TargetRelativeRotation": {"x": x, "y": y, "z": z, "w": w},
    }
    # the follower clamps the followed node to a box in the head's space around its offset: the box moves with it
    old = t_follower["TargetRelativePosition"]
    for name in FOLLOWER_CLAMPS:
        box = t_follower.get(name)
        if isinstance(box, dict) and all(k in box for k in "xyz"):
            offsets[name] = {k: float(box[k]) + offsets["TargetRelativePosition"][k] - float(old[k]) for k in "xyz"}
    return np.linalg.inv(between) @ resize @ bind, offsets


# --- writing -------------------------------------------------------------------------------------------------------


def _type_index(sf: Any, source: Any) -> int | None:
    """The index of ``source``'s type among ``sf``'s types, None when ``sf`` has none of it."""
    src_type = source.serialized_type
    return next(
        (
            i
            for i, typ in enumerate(sf.types)
            if typ.class_id == source.class_id
            and typ.old_type_hash == src_type.old_type_hash
            and typ.script_type_index == src_type.script_type_index
        ),
        None,
    )


class _Writer:
    """Objects added to the slot's bundles, opened for writing."""

    def __init__(self, files: Mapping[str, Any], seed: str):
        """Prepare deterministic new object IDs for writable slot serialized files.

        Args:
            files: Lower-case CAB names mapped to (engine Bundle, SerializedFile) pairs.
            seed: Build identity whose CRC32 seeds collision-checked random path IDs.
        """
        self.files = files  # lower-case CAB -> (engine Bundle, SerializedFile)
        self.random = random.Random(zlib.crc32(seed.encode()))

    def file(self, cab: str) -> Any:
        """Return the writable SerializedFile registered under an exact lower-case CAB name."""
        return self.files[cab][1]

    def fresh(self, sf: Any) -> int:
        """Choose a nonzero signed 64-bit path ID absent from the destination serialized file."""
        while True:
            pid = self.random.randrange(-(1 << 63), 1 << 63)
            if pid and pid not in sf.objects:
                return pid

    def add(self, sf: Any, type_name: str, tree: dict) -> int:
        """Add an object using an existing destination type and return its new path ID.

        Args:
            sf: Writable destination serialized file whose objects remain sorted by path ID.
            type_name: Unity type name requiring an existing object as the serialization template.
            tree: New object's typetree to serialize immediately.

        Raises:
            ServiceError: The slot file contains no object of the requested type.
        """
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

    @staticmethod
    def addable(sf: Any, source: Any) -> str | None:
        """Check whether a source object's serialized type can be added to a destination file.

        Args:
            sf: Destination serialized file whose build/version/type-tree settings must match.
            source: Source reader supplying a serialized type and any type dependencies.

        Returns:
            Refusal reason, or None when add_like can use the type.
        """
        src_type, src_file = source.serialized_type, source.assets_file
        if (
            src_type is None
            or sf.header.version != src_file.header.version
            or sf.unity_version != src_file.unity_version
            or sf._enable_type_tree != src_file._enable_type_tree
            or sf.header.version < 16
        ):
            return "object type of another build"
        if _type_index(sf, source) is None and src_type.type_dependencies:
            return "object type with dependencies"
        return None

    def add_like(self, sf: Any, source: Any, tree: dict) -> int:
        """Add an object of the source's type, copying an absent type from the same game build.

        This permits carrying shaders whose type is absent from the destination file.

        Args:
            sf: Writable destination serialized file.
            source: Source object reader providing class/type metadata compatible with the destination build.
            tree: New object's typetree to serialize under a fresh path ID.

        Returns:
            The new object's path ID.

        Raises:
            ServiceError: The builds differ or the absent type has unsupported dependencies.
        """
        why = self.addable(sf, source)
        if why:
            raise refuse(why, type=source.type.name)
        src_type = source.serialized_type
        index = _type_index(sf, source)
        if index is None:
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
        """Register a player resource file, such as Library/unity default resources, by external path.

        Args:
            sf: Destination serialized file whose external table is extended only when needed.
            entry: Source external entry copied verbatim when its case-insensitive path is absent.

        Returns:
            The one-based external file ID.
        """
        for i, e in enumerate(sf.externals):
            if e.path.lower() == entry.path.lower():
                return i + 1
        sf.externals.append(copy.copy(entry))
        return len(sf.externals)

    def external(self, sf: Any, cab: str) -> int:
        """Find or register a CAB reference in a destination serialized file.

        Args:
            sf: Writable serialized file whose first external entry supplies new-entry metadata.
            cab: Lower-case CAB name to register with archive path spelling.

        Returns:
            Zero for the file itself, otherwise a one-based external file ID.
        """
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
class Written:
    """Retain one written costume mesh's arrays and source constraints for _sanity validation."""

    name: str
    vertices: np.ndarray  # (n, 3), mesh space, as written
    linear: np.ndarray | None  # (n, 3, 3), what turned the normals and tangents
    joints: np.ndarray  # (n, 4), indices into the mesh's bone list
    weights: np.ndarray  # (n, 4)
    bindposes: np.ndarray  # (bones, 4, 4)
    bones: list[str]  # the bones' names, as bindposes
    submeshes: int
    source_totals: np.ndarray | None = (
        None  # per vertex, the source's weight sum (no weight is allowed where it had none)
    )
    source_materials: list[int] = field(default_factory=list)  # the source renderer's materials, 0 for none
    streamed: bool = False  # the written tree still names stream data (its vertices are not inlined)


@dataclass
class Rendered:
    """Record a written renderer's mesh index into Written records, bones, materials, and bounds."""

    part: int
    bones: int
    materials: list[int]  # path ids, 0 for none
    bounds: dict  # m_AABB


def _sanity(meshes: list[Written], renderers: list[Rendered]) -> None:
    """The last check before the bundles are returned.

    What this run wrote must be numbers a player can load. A failure refuses the pair (``source_unsupported``) rather
    than saving a mesh that draws nothing or flies apart. Over arrays already in memory; nothing is read again.
    """
    for mesh in meshes:
        if mesh.streamed:
            raise refuse("streamed mesh", part=mesh.name)
        for what, array in (("bindposes", mesh.bindposes), ("vertices", mesh.vertices), ("normals", mesh.linear)):
            if array is not None and not np.isfinite(array).all():
                raise refuse("invalid data", what=what, part=mesh.name)
        if len(mesh.bindposes):
            largest = np.abs(mesh.bindposes).reshape(len(mesh.bindposes), -1).max(axis=1)
            if largest.max() > BINDPOSE_LIMIT:
                raise refuse("bindpose out of range", bone=mesh.bones[int(np.argmax(largest))], part=mesh.name)
        count, used = len(mesh.bindposes), mesh.weights > 0
        if (mesh.joints[used] >= count).any() or (mesh.joints[used] < 0).any():
            raise refuse("skin inconsistent", part=mesh.name, what="bone index past the bindposes")
        totals = mesh.weights.sum(axis=1)
        none = (
            np.zeros(len(totals), bool)
            if mesh.source_totals is None
            else (totals <= 1e-9) & (mesh.source_totals <= 1e-9)
        )
        if not (none | (np.abs(totals - 1.0) <= WEIGHT_TOLERANCE)).all():
            raise refuse("skin inconsistent", part=mesh.name, what="weights do not add up to 1")
    for renderer in renderers:
        mesh = meshes[renderer.part]
        if renderer.bones != len(mesh.bindposes):
            raise refuse(
                "skin inconsistent",
                part=mesh.name,
                what=f"{renderer.bones} renderer bones for {len(mesh.bindposes)} bindposes",
            )
        centre = np.array([renderer.bounds["m_Center"][axis] for axis in "xyz"], float)
        extent = np.array([renderer.bounds["m_Extent"][axis] for axis in "xyz"], float)
        if not (np.isfinite(centre).all() and np.isfinite(extent).all()):
            raise refuse("invalid data", what="bounds", part=mesh.name)
        if float(np.max(np.abs(centre) + np.abs(extent))) > VERTEX_LIMIT:
            raise refuse("vertices out of range", part=mesh.name)
        if len(renderer.materials) > mesh.submeshes:
            raise refuse(
                "materials inconsistent",
                part=mesh.name,
                what=f"{len(renderer.materials)} materials for {mesh.submeshes} submeshes",
            )
        if len(renderer.materials) != len(mesh.source_materials):  # a null kept in place, never dropped
            raise refuse(
                "materials inconsistent",
                part=mesh.name,
                what=f"{len(renderer.materials)} materials for the source's {len(mesh.source_materials)}",
            )


@dataclass
class Mouth:
    """Locate the source mouth's costume part, material index, and default tile.

    CharacterMouthEventReceiver or CharacterAnimationEventReceiver supplies the locator.
    The part index follows source costume order, primary first; the material index selects
    that part's eye-mouth material.
    """

    part: int
    index: int
    uv: dict


MOUTH_FIELDS = ("MouthRenderer", "MouthMaterialIndex", "MouthDefaultUV")


def mouth_of(skeleton: Skeleton, costume: list[Part]) -> Mouth | None:
    """Find a mouth receiver pointing to an eye-mouth material on a selected costume part.

    Args:
        skeleton: Source hierarchy whose mouth/animation-event behaviours supply receiver fields.
        costume: Selected source costume parts, primary first, defining the returned part index.

    Returns:
        Mouth locator, or None for absent/invalid receivers, such as index -1 or an excluded cut-in body.
    """
    g = skeleton.graph
    parts = {_key(p.renderer): k for k, p in enumerate(costume)}
    for behaviour in skeleton.behaviours:
        tree = g.tree(behaviour)
        if not all(name in tree for name in MOUTH_FIELDS):
            continue
        renderer = g.deref(behaviour, tree["MouthRenderer"])
        k = parts.get(_key(renderer)) if renderer is not None else None
        index = int(tree["MouthMaterialIndex"])
        if k is None or not 0 <= index < len(costume[k].kinds) or costume[k].kinds[index] != "eyemouth":
            continue
        return Mouth(k, index, copy.deepcopy(tree["MouthDefaultUV"]))
    return None


MOUTH_ATLAS = "_MouthTileTex"  # the eye-mouth material's mouth atlas
MOUTH_GRID = ("_MouthTileCols", "_MouthTileRows")  # its cells across and up


@dataclass
class MouthAtlas:
    """Describe an eye-mouth material's texture, scale/offset, and mouth-cell grid.

    Retain _MouthTileTex and MOUTH_GRID as the material stores them. A clip's SetMouthTile events name grid cells
    (row * 100 + column, rows from the bottom), so the same number draws another mouth on another atlas: most
    students share one atlas, others draw their own (Hina's 4 x 4 one), laid out differently.
    """

    material: Any
    env: dict
    grid: dict[str, float]
    texture: tuple[str, int] | None

    def same(self, other: "MouthAtlas") -> bool:
        """Check whether a mouth-cell number uses the same texture, scale, and grid as another atlas."""
        return (
            self.texture == other.texture
            and self.env.get("m_Scale") == other.env.get("m_Scale")
            and self.grid == other.grid
        )


def mouth_atlas(graph: BundleGraph, material: Any) -> MouthAtlas | None:
    """Read a material's mouth texture environment and grid.

    Args:
        graph: Dependency graph resolving the texture and material typetree.
        material: Eye-mouth Material reader; None or an absent mouth texture returns None.
    """
    if material is None:
        return None
    props = graph.tree(material)["m_SavedProperties"]
    env = next((value for name, value in props.get("m_TexEnvs", []) if name == MOUTH_ATLAS), None)
    if env is None or not env["m_Texture"]["m_PathID"]:
        return None
    texture = graph.deref(material, env["m_Texture"])
    grid = {name: float(value) for name, value in props.get("m_Floats", []) if name in MOUTH_GRID}
    return MouthAtlas(material, copy.deepcopy(env), grid, _key(texture) if texture is not None else None)


def retiled_mouths(
    graph: BundleGraph, t_skeleton: Skeleton, t_costume: list[Part], s_costume: list[Part], mouth: Mouth | None
) -> tuple[MouthAtlas | None, set[tuple[str, int]], Mouth | None]:
    """Identify source eye-mouth materials needing the slot's atlas and default tile.

    Slot clips retain SetMouthTile events naming the slot's atlas cells. Materials drawing
    a different atlas take its texture, scale, offset, and grid; receivers retain the slot's default tile.

    Args:
        graph: Shared dependency graph resolving slot and source materials/textures.
        t_skeleton: Slot hierarchy whose mouth receiver defines the retained atlas and default tile.
        t_costume: Slot costume part order used by its mouth locator.
        s_costume: Incoming parts whose eye-mouth materials are compared against the slot atlas.
        mouth: Incoming mouth locator; None requires no retile operation.

    Returns:
        Slot atlas, graph keys of materials requiring replacement, and the source mouth
        with the receiver's default tile. Without either mouth/atlas, return None, an empty set, and mouth.
    """
    t_mouth = mouth_of(t_skeleton, t_costume)
    slot = mouth_atlas(graph, t_costume[t_mouth.part].materials[t_mouth.index]) if t_mouth is not None else None
    if slot is None or mouth is None:
        return None, set(), mouth
    retiled = set()
    for part in s_costume:
        for material, kind in zip(part.materials, part.kinds):
            own = mouth_atlas(graph, material) if kind == "eyemouth" else None
            if own is not None and not own.same(slot):
                retiled.add(_key(material))
    if _key(s_costume[mouth.part].materials[mouth.index]) in retiled:
        mouth = dataclasses.replace(mouth, uv=copy.deepcopy(t_mouth.uv))
    return slot, retiled, mouth


# what a build cannot show in the preview and the game may draw differently (the switch's cautions)
EX_PARTS = "ex_parts"  # the slot's EX-only parts (a cut-in body, alternative faces) draw nothing
CARRIED_SHADER = "carried_shader"  # a shader carried with the costume: the game holds two of its name


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


def _model_prefabs(graph: BundleGraph, files: list[Path]) -> dict[str, Any]:
    return {name: pointer for name, pointer in _prefabs(graph, files).items()}


def own_prefabs(prefabs: Mapping[str, Any], dev: str) -> dict[str, Any]:
    """Select a model's own prefab kinds and field NPC prefabs by exact dev identity.

    Another model containing the dev name, such as ch0336_carrier beside ch0336, is excluded.

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
        prefabs: Available lower-case prefab stems for the model.
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
    """Build changed slot bundles carrying another model's costume without saving them.

    A costume past the stretch limit is built and applied all the same (``plan.unfit``).
    The slot's worn items (``model_props``: hats, glasses, bags) draw nothing and the source's are carried;
    packed items remain with the costume that encloses them (model_props).
    The graph reads every bundle from memory (``unity.load_bundle``: a path lock for each load alone), so a build
    needs no model lock and exports run beside it. ``step`` is called between the build's steps (most under 0.5 s;
    the plan, the hems and a cold first read of the idle poses up to a few seconds, measured on Hina wearing Aoba):
    the caller's job yields there (``preview_gate.step``) or stops by raising.

    Args:
        index: Lower-case CAB names mapped to readable official copies, with slot CABs pointing to target_files.
        resolver: Optional on-demand lookup for CABs absent from index.
        target_files: Official destination slot copies, including its prefab and own dependency bundles.
        target_dev: Destination model dev name selecting the slot prefab and clip hierarchy.
        source_files: Incoming model's prefab bundles or own dependency bundles containing its rendered root.
        source_dev: Incoming model dev name selecting its costume, clips, and halo.
        open_writable: Opens a slot file as an engine Bundle for object edits; the caller saves returned bundles.
        physics: Whether Optimize Physics enables arm clearance, Rule B, and secondary-chain motion.
        halo: Whether to carry the source halo; False hides the slot halo.
        step: Optional no-argument yield/cancellation callback between build steps; raising cancels the build.
        worn: Optional per-model/game-revision cache of worn-item verdicts without Unity readers.
        meter: Optional progress meter receiving COSTUME_PHASES updates; None uses QUIET.
        poses: Optional per-model/game-revision store for numerical idle/probe matrices.

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
    from . import model_props as mp

    phase = meter.phase
    phase("read")
    target_prefabs = _model_prefabs(graph, [f for f in target_files if f.name.startswith("character-")])
    source_prefabs = _model_prefabs(graph, source_files)
    t_name = choose_prefab(target_prefabs, target_dev)
    try:
        # a battle or field model, else a field NPC or a story-only or UI-only model of its own bundles
        s_root, s_name, _ = model_root(graph, source_files, source_dev)
    except AssetNotFound:
        raise refuse("no prefab", dev=source_dev) from None
    step()
    phase("skeleton")
    t_skeleton = Skeleton.of(graph, target_prefabs[t_name].deref())
    s_skeleton = Skeleton.of(graph, s_root)
    t_skeleton.pose_store, t_skeleton.dev = poses, target_dev
    s_skeleton.pose_store, s_skeleton.dev = poses, source_dev
    if not s_skeleton.renderers:
        # an unskinned model (Kei's Decagrammaton body, a static mesh): nothing to bind to the slot's bones
        raise refuse("static model", dev=source_dev)
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
    result = plan(
        target,
        source,
        probes,
        fit=all(layout is not None for layout in layouts),
        own_probes=probe_poses(s_skeleton, s_clips),
        clips=t_clips,
    )
    from . import model_arm_clips, model_arm_frames, model_arm_solve

    # Optimize Physics only: the arms pushed out of a bulkier costume cost seconds on an affected pair
    arm_clips = (
        model_arm_solve.retarget(
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
    arm_holders = model_arm_clips.receivers(graph, t_skeleton, own) if arm_clips else []
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
    arm_probes = model_arm_frames.bounds_poses(t_skeleton, arm_clips)
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
                drawn_parts = [*s_costume, *(c.part for c in carried_parts)]
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
                    [s_skeleton.nodes[p.node].name for p in drawn_parts],
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
    model_arm_clips.write_copies(writer, arm_clips, arm_destinations, arm_holders, t_skeleton, clip_skirts, step=step)
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
    return Result(opened, result, len(s_costume), hem, cautions, dropped, carried_names, len(arm_clips))


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


def field_models(graph: BundleGraph, files: Iterable[Path]) -> dict[str, list[Path]]:
    """Index Field_Player_<dev>[_NonWeapon] prefabs by lower-case dev name and containing bundle.

    Args:
        graph: Graph loading each candidate bundle's container once.
        files: Field-model bundles whose /character/ prefab container paths are inspected.
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
