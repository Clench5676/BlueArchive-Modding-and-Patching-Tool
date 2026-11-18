"""Describe memorial lobby prefab MeshRenderers and their looping Animators for web/js/memorial-lobby/mesh-fx.js.

Besides its skeletons and particle systems a memorial lobby prefab can carry meshes drawn with effect shaders: lens dirt
and light quads (FX/FX_SHADER_Lobby_Order, One One; DSFX/FX_SHADER_Additive_0) in Akane's, Eimi's, Karin's, Shiroko's,
Sumire's and Shun's memorial lobbies, a water surface, clouds and a window glow
(DSFX/FX_SHADER_AlphaBlend_Add_Distort_0) in Hanako's, Maki's and Utaha's. Most are Unity's built-in Quad (unity default
resources, path id 10210), placed like the particle systems: under a SkeletonUtilityBone (Follow) they move with the
bone. Four memorial lobbies loop them with an Animator: its controller's default state plays one clip, which scales,
moves and turns the quads and animates their material colours.

A renderer is {"name", "path", "material", "mesh" ("quad" or a memorial-lobby.json fx.meshes id), "queue",
"sortingLayer", "order", "layer" (the sorting layer's id), "z", "anchor", "chain", "static"}: drawn like a particle
system with that material, sorted with the systems and the skeletons' atlas pages, placed by its anchor's bone (as
particles.anchor_of) times the transforms in "chain" (each {"node", "p", "q", "s"}: local position, rotation quaternion
and scale), or by "static" without an anchor (then the chain runs from the prefab root). A clip is {"duration", "rate",
"loop", "tracks"}, a track one of {"node", "attr": "p" | "q" | "s" | "e", "order", "v"} (a transform's position,
rotation, scale or Euler angles in degrees, in the rotation order "order") and {"renderer", "prop", "ch", "rgba", "v"}
(one channel of a material property: Color channels "rgba" are converted to linear light like the material's colours,
Vector channels are not), "v" holding the values at 0, 1 / rate, ... seconds (one value: constant). Nodes are indexes
into the prefab walk.

Everything here works on plain typetrees, so it runs without game files; the service resolves the materials, meshes and
animator controllers.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable, Mapping
from typing import Any

from . import particles

logger = logging.getLogger(__name__)

QUAD = "quad"  # Unity's built-in Quad, which mesh-fx.js builds itself
# Transform curve attributes (GenericBinding.attribute for typeID 4) and their component counts
_TRANSFORM = {1: ("p", 3), 2: ("q", 4), 3: ("s", 3), 4: ("e", 3)}
_MATERIAL_BINDING = 22  # GenericBinding.customType of a renderer's material property
_RENDERERS = (23, 25, 137)  # MeshRenderer, Renderer, SkinnedMeshRenderer


def _r(value: float, digits: int = 5) -> float:
    return particles._r(value, digits)


def _trs(t: Mapping[str, Any]) -> dict:
    p, q, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
    return {
        "p": [_r(p.get(k, 0.0)) for k in "xyz"],
        "q": [_r(q.get(k, 0.0 if k != "w" else 1.0), 7) for k in "xyzw"],
        "s": [_r(s.get(k, 1.0)) for k in "xyz"],
    }


def binding_dims(binding: Mapping[str, Any]) -> int:
    """Count curves in a generic binding: three for position/scale/Euler, four for rotation, otherwise one."""
    if binding.get("typeID") == 4:
        return _TRANSFORM.get(binding.get("attribute"), ("", 1))[1]
    return 1


def material_property(attribute: int, names: list[str]) -> tuple[str, int, int] | None:
    """Resolve a packed material-property binding to its name, channel, and color flag.

    Args:
        attribute: Property CRC32 in bits 0-27, channel in bits 28-29, and Color-versus-Vector flag in bit 30.
        names: Candidate property names whose truncated CRC32 may match.

    Returns:
        Property name, channel index, and RGBA flag, or None when no name matches.
    """
    crc = attribute & 0x0FFFFFFF
    for name in names:
        if zlib.crc32(name.encode()) & 0x0FFFFFFF == crc:
            return name, (attribute >> 28) & 3, (attribute >> 30) & 1
    return None


def clip(
    decoded: Any,
    bindings: list[Mapping[str, Any]],
    loop: bool,
    paths: Mapping[int, int],
    renderer_of: Mapping[int, tuple[int, list[str]]],
) -> dict | None:
    """Export Animator transform and material tracks using the module's clip schema.

    Args:
        decoded: chibi.decode_clip result with second-based timing and sampled scalar curves.
        bindings: Generic bindings in their stored curve order; unsupported components are omitted.
        loop: Whether the controller's clip repeats.
        paths: CRC32 of paths below the Animator GameObject (empty for itself) mapped to prefab node indices.
        renderer_of: Node indices mapped to renderer indices and their candidate material property names.

    Returns:
        Duration, rate, loop flag, and tracks, or None when no supported track remains.
    """
    tracks, index = [], 0
    for b in bindings:
        dims = binding_dims(b)
        node = paths.get(b.get("path", 0) & 0xFFFFFFFF)
        values = [decoded.curves.get(index + k) for k in range(dims)]
        index += dims
        if node is None or any(v is None for v in values) or b.get("isPPtrCurve"):
            continue
        if b.get("typeID") == 4 and b.get("attribute") in _TRANSFORM:
            attr = _TRANSFORM[b["attribute"]][0]
            n = max(len(v) for v in values)
            rows = [[_r(v[i] if len(v) > 1 else v[0], 5) for v in values] for i in range(n)]
            track = {"node": node, "attr": attr, "v": rows}
            if attr == "e":
                track["order"] = int(b.get("customType", 4) or 4)
            tracks.append(track)
        elif b.get("typeID") in _RENDERERS and b.get("customType") == _MATERIAL_BINDING and node in renderer_of:
            ri, names = renderer_of[node]
            found = material_property(int(b.get("attribute", 0)), names)
            if found is None:
                logger.debug("Material binding %s not found on %s", b.get("attribute"), names)
                continue
            prop, ch, rgba = found
            tracks.append({"renderer": ri, "prop": prop, "ch": ch, "rgba": rgba, "v": [_r(x, 5) for x in values[0]]})
        else:
            logger.debug("Animator binding left out: type %s attribute %s", b.get("typeID"), b.get("attribute"))
    if not tracks:
        return None
    times = decoded.times
    rate = 1 / (times[1] - times[0]) if len(times) > 1 and times[1] > times[0] else 30.0
    return {"duration": _r(decoded.duration, 5), "rate": _r(rate, 4), "loop": 1 if loop else 0, "tracks": tracks}


def build(
    nodes: list[dict],
    skeleton_of_go: Callable[[int], str | None],
    data_scale: Callable[[str], float],
    resolve: Callable[[str, int, dict], Any] | None,
) -> dict:
    """Build mesh renderers and their Animator clips from a memorial lobby prefab walk.

    Args:
        nodes: memorial_lobby.read_prefab nodes in hierarchy order with first components, path IDs, and transforms.
        skeleton_of_go: Maps a skeleton GameObject path ID to its exported skeleton name, or None.
        data_scale: Returns Unity units per skeleton unit for each skeleton name.
        resolve: Reads material, mesh, or animator by owner path ID and pointer; None returns empty collections.
            Meshes may be ("quad", None); animators carry decoded default-state clips, bindings, and loop flags.

    Returns:
        Renderers and clips plus materials, textures, and meshes in the particles.build schema.
    """
    out: dict[str, Any] = {"renderers": [], "clips": [], "materials": {}, "textures": {}, "meshes": {}}
    if resolve is None:
        return out
    by_go = {n["go"]: n for n in nodes}
    index_of = {n["go"]: i for i, n in enumerate(nodes)}
    renderer_of: dict[int, tuple[int, list[str]]] = {}
    for i, n in enumerate(nodes):
        mr = n["first"].get("MeshRenderer")
        if mr is None or "SkeletonAnimation" in n["first"] or not n["active"] or not mr.get("m_Enabled", 1):
            continue
        mr_pid = n["pids"]["MeshRenderer"]
        mf = n["first"].get("MeshFilter") or {}
        pointer = mf.get("m_Mesh") or {}
        mesh = resolve("mesh", n["pids"].get("MeshFilter", 0), pointer) if pointer.get("m_PathID") else None
        materials = mr.get("m_Materials") or []
        mat = resolve("material", mr_pid, materials[0]) if materials and materials[0].get("m_PathID") else None
        if mesh is None or mat is None or not mat[1].get("program"):
            logger.debug(
                "Mesh renderer %s left out (mesh %s, material %s)",
                n["path"],
                mesh and mesh[0],
                mat and mat[1].get("shader"),
            )
            continue
        mesh_id, mesh_data = mesh
        if mesh_data is not None:
            out["meshes"][mesh_id] = mesh_data
        mat_id, material = mat
        out["materials"][mat_id] = material
        for tex in material.get("tex", {}).values():
            if "id" in tex:
                out["textures"][tex["id"]] = {k: v for k, v in tex.items() if k != "st"}
        anchor, chain = particles.anchor_of(n, by_go, skeleton_of_go, data_scale)
        if anchor is None:  # placed from the prefab root
            chain, cur = [], n
            while cur is not None:
                chain.append(cur)
                cur = by_go.get(cur["parent"])
            chain.reverse()
        renderer_of[i] = (
            len(out["renderers"]),
            sorted(set(material.get("floats", {})) | set(material.get("colors", {}))),
        )
        out["renderers"].append(
            {
                "name": n["name"],
                "path": n["path"],
                "material": mat_id,
                "mesh": mesh_id,
                "queue": material.get("queue", 3000),
                # the sorting layer's value (particles.build "sortingLayer"), which Unity sorts by first; its id
                "sortingLayer": int(mr.get("m_SortingLayer", 0)),
                "order": int(mr.get("m_SortingOrder", 0)),
                "layer": int(mr.get("m_SortingLayerID", 0)),
                "z": _r(n["m3"][11]),
                "anchor": anchor,
                "static": particles.rounded(n["m3"]),
                "chain": [{"node": index_of[c["go"]], **_trs(c["t"])} for c in chain],
            }
        )
    if not out["renderers"]:
        return out
    # the Animators over them: paths below the Animator's GameObject, as crc32 of "child/grandchild" ("" itself)
    children: dict[int, list[dict]] = {}
    for n in nodes:
        children.setdefault(n["parent"], []).append(n)
    for n in nodes:
        animator = n["first"].get("Animator")
        if animator is None or not n["active"] or not animator.get("m_Enabled", 1):
            continue
        pointer = animator.get("m_Controller") or {}
        found = resolve("animator", n["pids"]["Animator"], pointer) if pointer.get("m_PathID") else None
        if not found:
            continue
        paths, stack = {}, [(n, "")]
        while stack:
            cur, rel = stack.pop()
            paths[zlib.crc32(rel.encode()) & 0xFFFFFFFF] = index_of[cur["go"]]
            for c in children.get(cur["go"], []):
                stack.append((c, f"{rel}/{c['name']}" if rel else c["name"]))
        data = found[1]
        made = clip(data["decoded"], data["bindings"], data["loop"], paths, renderer_of)
        if made:
            out["clips"].append(made)
    return out
