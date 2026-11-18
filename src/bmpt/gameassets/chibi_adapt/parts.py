"""What a chibi is made of: the roles of its renderers, the clips that probe it and its skinned meshes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np

from ..chibi import (
    BundleGraph,
    _key,
    ex_only_renderers,
    shader_kind,
)
from ..chibi_glb import read_mesh
from .skeleton import BIPED_CORE, CHARACTER_KINDS, PROBE_CLIPS, PROBE_SCAN, Skeleton, refuse


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
    """Collect field-controller clips, otherwise the clips of the chibi's prefab Animators.

    Args:
        graph: Dependency graph resolving controllers and effective override/original clips.
        skeleton: Chosen prefab hierarchy whose FieldAnimancerController takes precedence when present.
        prefabs: One chibi's prefab stems mapped to root pointers for Animator traversal.
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
    """Pick the first clip for each suffix, preferring <chibi>_<suffix> over a bare Battle NPC state name.

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
        skeleton: Chibi hierarchy whose Biped movement determines whether a clip contributes probes.
        clips: Available chibi-owned AnimationClip readers.
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
