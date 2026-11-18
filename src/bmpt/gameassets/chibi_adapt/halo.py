"""The halo after a costume switch: the source's, carried with its nodes, or none."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import numpy as np

from .. import chibi_skirts as ms
from ..chibi import (
    _external_cab,
    _key,
)
from .parts import _shader_kind
from .skeleton import HEAD, Skeleton, refuse
from .writing import _Writer


logger = logging.getLogger(__package__)


@dataclass
class Halo:
    """Describe a prefab's halo shape and all nodes/components that draw it.

    shape "single" names the node its FxFollower keeps beside the head (HaloRoot) and the one
    unskinned renderer below it. "skinned": no follower, one halo-shaded skinned renderer bound to one ``bone`` at or
    below the ``head`` (Ayame's NPC chibis); ``node`` is the renderer's. "none": no halo. Anything else names
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
    the chibi, a script, a sprite) or cannot be added to the slot's prefab file ``into``.
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
