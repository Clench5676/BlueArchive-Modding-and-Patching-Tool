"""Vertex positions, normals and tangents of a Mesh typetree, rewritten in place (BL-128).

A costume refitted to another skeleton changes shape: every vertex moves, and its normal and tangent turn with the
blended transform that moved it. Meshes keep these either plainly in ``m_VertexData`` (channels 0, 1 and 2, float32 or
float16) or in ``m_CompressedMesh`` (positions as packed floats, normals and tangents as two packed components with
sign bits). Every other byte stays as it is. Pure numpy and struct; ``model_adapt`` decides what to write.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .model_hems import FORMAT_SIZE, pack_bits, unpack_bits

POSITION, NORMAL, TANGENT = 0, 1, 2
FLOAT_CODES = {0: "f", 1: "e"}


@dataclass
class Plain:
    count: int
    channels: dict[int, tuple[int, int, str, int]]  # channel -> (first byte, stride, struct code, dimension)


@dataclass
class Packed:
    count: int


def _plain(mesh: Mapping[str, Any]) -> Plain | None:
    data = mesh.get("m_VertexData") or {}
    channels, count, raw = data.get("m_Channels") or [], int(data.get("m_VertexCount") or 0), data.get("m_DataSize")
    if not channels or not count or not isinstance(raw, (bytes, bytearray)):
        return None
    starts, strides, offset = [], [], 0
    for stream in range(1 + max(c["stream"] for c in channels)):
        stride = sum((c["dimension"] & 0xF) * FORMAT_SIZE.get(c["format"], 0)
                     for c in channels if c["stream"] == stream and c["dimension"])
        starts.append(offset)
        strides.append(stride)
        offset = (offset + count * stride + 15) & ~15
    if offset < len(raw) or offset - len(raw) >= 16:
        return None  # the channels do not describe this data
    out: dict[int, tuple[int, int, str, int]] = {}
    for channel in (POSITION, NORMAL, TANGENT):
        if channel >= len(channels) or not channels[channel]["dimension"]:
            continue
        info = channels[channel]
        dimension = info["dimension"] & 0xF
        code = FLOAT_CODES.get(info["format"])
        wanted = 4 if channel == TANGENT else 3
        if code is None or dimension != wanted:
            return None
        s = info["stream"]
        if info["offset"] + dimension * FORMAT_SIZE[info["format"]] > strides[s] or starts[s] + count * strides[s] > len(raw):
            return None
        out[channel] = (starts[s] + info["offset"], strides[s], code, dimension)
    return Plain(count, out) if POSITION in out else None


def _packed(mesh: Mapping[str, Any]) -> Packed | None:
    compressed = mesh.get("m_CompressedMesh") or {}
    vertices = compressed.get("m_Vertices") or {}
    count = int(vertices.get("m_NumItems") or 0) // 3
    if not count or not int(vertices.get("m_BitSize") or 0):
        return None
    for key, per in (("m_Normals", 2), ("m_Tangents", 2)):
        packed = compressed.get(key) or {}
        items = int(packed.get("m_NumItems") or 0)
        if items and (items != per * count or not int(packed.get("m_BitSize") or 0)):
            return None
    signs = compressed.get("m_NormalSigns") or {}
    if int((compressed.get("m_Normals") or {}).get("m_NumItems") or 0) and int(signs.get("m_NumItems") or 0) != count:
        return None
    signs = compressed.get("m_TangentSigns") or {}
    if int((compressed.get("m_Tangents") or {}).get("m_NumItems") or 0) and int(signs.get("m_NumItems") or 0) != 2 * count:
        return None
    return Packed(count)


def geometry_of(mesh: Mapping[str, Any]) -> Plain | Packed | None:
    """Where a Mesh typetree keeps its positions, normals and tangents, when this module can rewrite them (no
    streamed data); None otherwise."""
    if (mesh.get("m_StreamData") or {}).get("size"):
        return None
    if int(((mesh.get("m_CompressedMesh") or {}).get("m_Vertices") or {}).get("m_NumItems") or 0):
        return _packed(mesh)
    return _plain(mesh)


# --- plain vertex data ---------------------------------------------------------------------------------------------

def _read_plain(raw: bytes, place: tuple[int, int, str, int], count: int) -> np.ndarray:
    first, stride, code, dimension = place
    return np.array([struct.unpack_from(f"<{dimension}{code}", raw, first + v * stride) for v in range(count)], float)


def _write_plain(raw: bytearray, place: tuple[int, int, str, int], values: np.ndarray) -> None:
    first, stride, code, dimension = place
    for v, row in enumerate(values.tolist()):
        struct.pack_into(f"<{dimension}{code}", raw, first + v * stride, *row)


# --- packed floats -------------------------------------------------------------------------------------------------

def _unpack_floats(packed: Mapping[str, Any]) -> np.ndarray:
    bits = int(packed["m_BitSize"])
    scale = float(packed["m_Range"]) / ((1 << bits) - 1)
    return np.array(unpack_bits(packed), float) * scale + float(packed["m_Start"])


def _pack_floats(packed: dict, values: np.ndarray, *, keep_range: bool) -> None:
    """``values`` quantized into ``packed`` at its bit size; its start and range kept (normals, tangents: [-1, 1]) or
    taken from the values (positions)."""
    bits = int(packed["m_BitSize"])
    top = (1 << bits) - 1
    if keep_range:
        start, span = float(packed["m_Start"]), float(packed["m_Range"])
    else:
        start = float(values.min()) if len(values) else 0.0
        span = float(values.max()) - start if len(values) else 0.0
        packed["m_Start"], packed["m_Range"] = start, span
    steps = np.zeros(len(values), np.int64) if span <= 0 else np.clip(
        np.rint((values - start) / span * top), 0, top).astype(np.int64)
    data = pack_bits(steps.tolist(), bits)
    packed["m_NumItems"] = len(values)
    packed["m_Data"] = data if isinstance(packed.get("m_Data"), (bytes, bytearray)) else list(data)


def _write_signs(packed: dict, signs: np.ndarray) -> None:
    data = pack_bits([int(s) for s in signs.tolist()], 1)
    packed["m_NumItems"], packed["m_BitSize"] = len(signs), 1
    packed["m_Data"] = data if isinstance(packed.get("m_Data"), (bytes, bytearray)) else list(data)


def _unit_z(xy: np.ndarray, positive: np.ndarray) -> np.ndarray:
    """The third component of unit vectors from two (UnityPy's reading: normalised when x² + y² > 1)."""
    zsqr = 1.0 - (xy ** 2).sum(axis=1)
    out = np.c_[xy, np.sqrt(np.maximum(zsqr, 0.0))]
    over = zsqr < 0
    out[over] /= np.maximum(np.linalg.norm(out[over], axis=1, keepdims=True), 1e-12)
    out[~positive, 2] *= -1
    return out


# --- the rewrite ---------------------------------------------------------------------------------------------------

def _turned(vectors: np.ndarray, linear: np.ndarray) -> np.ndarray:
    out = np.einsum("nij,nj->ni", linear, vectors)
    length = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(length > 1e-12, out / np.maximum(length, 1e-12), vectors)


def _aabb(positions: np.ndarray) -> dict:
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    centre, extent = (lo + hi) / 2, (hi - lo) / 2
    return {"m_Center": dict(zip("xyz", map(float, centre))), "m_Extent": dict(zip("xyz", map(float, extent)))}


def write_geometry(mesh: dict, layout: Plain | Packed, positions: np.ndarray, linear: np.ndarray) -> None:
    """New ``positions`` (mesh space, one per vertex); each normal and tangent turned by its vertex's ``linear``
    (3 x 3, mesh space), a tangent's handedness flipped where that mirrors. The local bounds follow; so do blend
    shape deltas, turned by their vertex's ``linear``."""
    if len(positions) != layout.count or len(linear) != layout.count:
        raise ValueError("vertex count")
    mirrored = np.linalg.det(linear) < 0
    if isinstance(layout, Plain):
        data = mesh["m_VertexData"]
        raw = bytearray(data["m_DataSize"])
        _write_plain(raw, layout.channels[POSITION], positions)
        if NORMAL in layout.channels:
            normals = _read_plain(bytes(raw), layout.channels[NORMAL], layout.count)
            _write_plain(raw, layout.channels[NORMAL], _turned(normals, linear))
        if TANGENT in layout.channels:
            tangents = _read_plain(bytes(raw), layout.channels[TANGENT], layout.count)
            w = np.where(mirrored, -tangents[:, 3], tangents[:, 3])
            _write_plain(raw, layout.channels[TANGENT], np.c_[_turned(tangents[:, :3], linear), w])
        data["m_DataSize"] = bytes(raw)
    else:
        compressed = mesh["m_CompressedMesh"]
        _pack_floats(compressed["m_Vertices"], positions.reshape(-1), keep_range=False)
        normals = compressed.get("m_Normals") or {}
        if int(normals.get("m_NumItems") or 0):
            signs = np.array(unpack_bits(compressed["m_NormalSigns"]), int) > 0
            turned = _turned(_unit_z(_unpack_floats(normals).reshape(-1, 2), signs), linear)
            _pack_floats(normals, turned[:, :2].reshape(-1), keep_range=True)
            _write_signs(compressed["m_NormalSigns"], (turned[:, 2] >= 0).astype(int))
        tangents = compressed.get("m_Tangents") or {}
        if int(tangents.get("m_NumItems") or 0):
            signs = np.array(unpack_bits(compressed["m_TangentSigns"]), int).reshape(-1, 2) > 0
            turned = _turned(_unit_z(_unpack_floats(tangents).reshape(-1, 2), signs[:, 0]), linear)
            w = signs[:, 1] ^ mirrored
            _pack_floats(tangents, turned[:, :2].reshape(-1), keep_range=True)
            _write_signs(compressed["m_TangentSigns"], np.c_[turned[:, 2] >= 0, w].astype(int).reshape(-1))
    mesh["m_LocalAABB"] = _aabb(positions)
    for delta in (mesh.get("m_Shapes") or {}).get("vertices") or []:
        turn = linear[int(delta["index"])] if int(delta["index"]) < len(linear) else np.eye(3)
        for key in ("vertex", "normal", "tangent"):
            if isinstance(delta.get(key), dict):
                value = turn @ np.array([delta[key][axis] for axis in "xyz"], float)
                delta[key] = dict(zip("xyz", map(float, value)))
