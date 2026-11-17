"""The limits and checks every part of a costume switch shares, and the skeleton of a model."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

import numpy as np

from ...core.errors import ServiceError
from ..costume_cache import PoseStore, clip_digest, decode_clip, memoize_pose
from ..model import (
    BundleGraph,
    _key,
)
from ..model_glb import bind_tracks, euler_orders, euler_to_quat, UNITY_ORDER

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__package__)


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


HEAD = "Bip001 Head"
