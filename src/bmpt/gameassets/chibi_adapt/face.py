"""Another character's face on the slot, and the check that a Rule B edit kept every clip curve."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .. import chibi_skirts as ms
from ..costume_cache import decode_clip
from .costume import Costume, Fit, Plan, face_bone, flatten
from .skeleton import HEAD, _inverse, trs
from .skirts import FACE_OFFSET, FaceRetarget, Skirt


logger = logging.getLogger(__package__)


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


@dataclass
class ClipBefore:
    """Retain decoded curves and storage counts for checking a Rule B edit against clip_snapshot."""

    decoded: Any  # chibi.DecodedClip
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
