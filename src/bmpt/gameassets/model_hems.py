"""Skirts and hems of a costume put on another variant's model (BL-118).

The game keeps legs inside skirts by keying the skirt bones in every clip of the costume's own model. A costume moved
onto another model gets no curves from that model's clips: its skirt bones follow their Biped bone rigidly (the group
choice of ``model_adapt``) and the legs pass through. Fixed skin weights are what the format allows, so part of each
hem vertex's rigid weight moves to the thighs and calves by one of a few rules, and a measurement under the slot's own
clips picks one:

- ramps ("blend", "short"): by height below the hip toward the thigh, below the knee toward the calf, split between the
  legs by distance;
- "fit": weights on the pelvis, thighs and calves fitted by least squares to the costume's own keyed clips (the
  vendor's motion, where the costume has clips);
- the pick: the lowest mean share of covered leg vertices that end up more than 1 cm outside the cloth, among the rules
  whose cloth stretch stays within the slot's own range (the limit) and that make no clip more than 5 points worse
  than rigid weights.

Pure numpy; the model plumbing is in ``model_adapt``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

DEEP = 0.01        # a leg vertex this far outside the cloth passes through (metres)
TOLERANCE = 0.003  # crossings this close to a leg vertex decide nothing
NEAR_AXIS = 0.01   # leg vertices this close to the body axis have no direction
WORSE = 5.0        # no clip may get more than this many points worse than rigid weights
STRETCH_FLOOR = 1.5
RAY_CHUNK = 32


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(points - (a + np.outer(t, ab)), axis=1)


def leg_distance(points: np.ndarray, hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
    """Distance of each point to a leg's axis (hip -> knee -> ankle)."""
    return np.minimum(segment_distance(points, hip, knee), segment_distance(points, knee, ankle))


# --- weights -------------------------------------------------------------------------------------------------------

def densify(columns: np.ndarray, weights: np.ndarray, width: int) -> np.ndarray:
    """Per-vertex influences (column, weight) as a dense (vertices x ``width``) matrix; influences on the same column
    (several bones driving the same slot bone) add up, so keeping four later drops no real influence."""
    dense = np.zeros((len(weights), width))
    rows = np.repeat(np.arange(len(weights))[:, None], columns.shape[1], axis=1)
    np.add.at(dense, (rows, columns), weights)
    return dense


def reweight(dense: np.ndarray, rigid: np.ndarray, fraction: np.ndarray, moves: np.ndarray,
             columns: list[int]) -> np.ndarray:
    """``dense`` (vertices x bones) with ``fraction`` of each vertex's rigid part (``rigid``, part of ``dense``) moved
    to ``columns``, shared by ``moves`` (rows summing to 1)."""
    share = rigid.sum(axis=1)
    out = dense - rigid * fraction[:, None]
    out[:, columns] += (share * fraction)[:, None] * moves
    return out


def ramp(height: np.ndarray, near: np.ndarray, left: np.ndarray, right: np.ndarray, alpha: float, top: float,
         knee: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """The fraction of the rigid part to move and its shares on (left thigh, right thigh, left calf, right calf).
    ``height``: below the hip in thigh lengths; ``near``: 1 next to the legs, 0 from two hip widths away; ``left`` and
    ``right``: closeness to each leg. The fraction rises over ``top`` thigh lengths, the calf takes over across
    ``knee``."""
    fraction = alpha * smoothstep(0.0, top, height) * near
    calf = smoothstep(knee[0], knee[1], height)
    total = left + right
    side = np.where(total > 1e-12, left / np.maximum(total, 1e-12), 0.5)
    moves = np.stack([side * (1 - calf), (1 - side) * (1 - calf), side * calf, (1 - side) * calf], axis=1)
    return fraction, moves


def keep_four(dense: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The four largest influences per vertex (columns, weights summing to 1), largest first; unused ones repeat the
    first column with weight 0."""
    dense = np.clip(dense, 0.0, None)
    joints = np.argsort(-dense, axis=1, kind="stable")[:, :4]
    weights = np.take_along_axis(dense, joints, axis=1)
    if joints.shape[1] < 4:
        pad = 4 - joints.shape[1]
        joints = np.c_[joints, np.repeat(joints[:, :1], pad, axis=1)]
        weights = np.c_[weights, np.zeros((len(weights), pad))]
    weights = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    joints = np.where(weights > 0, joints, joints[:, :1])
    return joints, weights


def skin(rest: np.ndarray, joints: np.ndarray, weights: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    homogeneous = np.c_[rest, np.ones(len(rest))]
    out = np.zeros((len(rest), 3))
    for k in range(joints.shape[1]):
        out += weights[:, k:k + 1] * np.einsum("nij,nj->ni", matrices[joints[:, k]], homogeneous)[:, :3]
    return out


class Normal:
    """Per vertex, the normal equations of the least squares ``basis @ w ~ target`` over many frames."""

    def __init__(self, count: int, bones: int):
        self.gram = np.zeros((count, bones, bones))
        self.right = np.zeros((count, bones))
        self.square = np.zeros(count)
        self.frames = 0

    def add(self, basis: np.ndarray, target: np.ndarray) -> None:
        """``basis``: (vertices, 3, bones) where each bone alone puts the vertex; ``target``: (vertices, 3)."""
        self.gram += np.einsum("nri,nrj->nij", basis, basis)
        self.right += np.einsum("nri,nr->ni", basis, target)
        self.square += (target * target).sum(axis=1)
        self.frames += 1


def fit_simplex(normal: Normal) -> tuple[np.ndarray, np.ndarray]:
    """Per vertex, the non-negative weights summing to 1 that best fit the frames (exact: every subset of the bones
    solved with the sum constraint, the best feasible one kept), and the rms residual per frame."""
    count, bones = normal.right.shape
    best = np.full(count, np.inf)
    out = np.zeros((count, bones))
    for mask in range(1, 1 << bones):
        chosen = [i for i in range(bones) if mask >> i & 1]
        k = len(chosen)
        gram = normal.gram[:, chosen][:, :, chosen] + np.eye(k)[None] * 1e-10
        system = np.zeros((count, k + 1, k + 1))
        system[:, :k, :k] = gram
        system[:, :k, k] = 1
        system[:, k, :k] = 1
        rhs = np.concatenate([normal.right[:, chosen], np.ones((count, 1))], axis=1)
        try:
            solution = np.linalg.solve(system, rhs[..., None])[..., 0][:, :k]
        except np.linalg.LinAlgError:
            continue
        feasible = (solution >= -1e-9).all(axis=1)
        residual = (normal.square - 2 * (solution * normal.right[:, chosen]).sum(axis=1)
                    + np.einsum("ni,nij,nj->n", solution, gram, solution))
        better = feasible & (residual < best - 1e-12)
        best[better] = residual[better]
        weights = np.zeros((int(better.sum()), bones))
        weights[:, chosen] = np.clip(solution[better], 0.0, None)
        out[better] = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    return out, np.sqrt(np.maximum(best, 0.0) / max(1, normal.frames))


# --- measures ------------------------------------------------------------------------------------------------------

def stretch_p99(rest: np.ndarray, posed: np.ndarray, edges: np.ndarray) -> float:
    """The 99th percentile of how much the edges grow (1 = unchanged)."""
    if not len(edges):
        return 1.0
    l0 = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    l1 = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)
    ok = l0 > 1e-6
    return float(np.percentile(l1[ok] / l0[ok], 99)) if ok.any() else 1.0


def _far_hits(origins: np.ndarray, directions: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """For rays ``origins + t * directions``, the largest ``t`` at which they cross the triangles (nan without)."""
    if not len(corners):
        return np.full(len(origins), np.nan)
    v0, e1, e2 = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    o, d = origins[:, None, :], directions[:, None, :]
    p = np.cross(d, e2[None])
    det = np.einsum("nmk,mk->nm", p, e1)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / det
        s = o - v0[None]
        u = np.einsum("nmk,nmk->nm", s, p) * inv
        q = np.cross(s, e1[None])
        v = np.einsum("nmk,nmk->nm", np.broadcast_to(d, q.shape), q) * inv
        t = np.einsum("nmk,mk->nm", q, e2) * inv
    hit = (np.abs(det) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4)
    t = np.where(hit, t, -np.inf).max(axis=1)
    return np.where(np.isfinite(t), t, np.nan)


def cover(points: np.ndarray, legs: np.ndarray, triangles: np.ndarray, centre: np.ndarray,
          up: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For the leg vertices ``legs`` (indices into ``points``), on the ray from the body axis (through ``centre``
    along ``up``) through each: +1 when cloth (``triangles``, indices into ``points``) lies beyond it, -1 when every
    crossing lies nearer, 0 without a crossing or on the axis; and how far it lies outside the farthest crossing."""
    up = up / np.linalg.norm(up)
    v = points[legs]
    height = (v - centre) @ up
    origins = centre + np.outer(height, up)
    directions = v - origins
    distance = np.linalg.norm(directions, axis=1)
    keep = distance > NEAR_AXIS
    directions[keep] /= distance[keep, None]
    far = np.full(len(v), np.nan)
    corners = points[triangles]
    t_height = (corners - centre) @ up
    low, high = t_height.min(axis=1), t_height.max(axis=1)
    # a ray runs at its vertex's height: only triangles spanning that height can cross it
    rays = np.where(keep)[0]
    rays = rays[np.argsort(height[rays], kind="stable")]
    for start in range(0, len(rays), RAY_CHUNK):
        chunk = rays[start:start + RAY_CHUNK]
        lo, hi = height[chunk].min(), height[chunk].max()
        near = (high >= lo - 1e-6) & (low <= hi + 1e-6)
        if near.any():
            far[chunk] = _far_hits(origins[chunk], directions[chunk], corners[near])
    state = np.zeros(len(v), int)
    with np.errstate(invalid="ignore"):
        state[keep & (far > distance + TOLERANCE)] = 1
        state[keep & (far < distance - TOLERANCE)] = -1
    return state, np.where(np.isfinite(far), distance - far, np.nan)


def deep_share(state: np.ndarray, depth: np.ndarray) -> float:
    """Percent of the (covered at rest) leg vertices more than ``DEEP`` outside the cloth."""
    if not len(state):
        return 0.0
    with np.errstate(invalid="ignore"):
        return 100.0 * float(((state == -1) & (depth > DEEP)).sum()) / len(state)


@dataclass
class Score:
    rule: str
    deep: dict[str, float]  # per clip: worst frame's deep pass-through, percent
    stretch: float          # worst cloth stretch p99 over the clips

    @property
    def mean(self) -> float:
        return sum(self.deep.values()) / len(self.deep) if self.deep else 0.0


def choose(scores: list[Score], limit: float) -> Score:
    """``scores[0]`` is rigid. A rule is allowed when its stretch stays within ``max(limit, rigid's)`` and no clip
    gets more than ``WORSE`` points worse than rigid; the lowest mean wins, ties go to rigid, then to less stretch."""
    rigid = scores[0]
    ceiling = max(limit, rigid.stretch)
    allowed = [s for s in scores[1:] if s.stretch <= ceiling + 1e-9
               and all(s.deep.get(c, 0.0) <= d + WORSE for c, d in rigid.deep.items())]
    return min([*allowed, rigid], key=lambda s: (round(s.mean, 1), s is not rigid, s.stretch))


# --- vertex data ---------------------------------------------------------------------------------------------------

# VertexFormat (Unity 2019+): component sizes; BlendWeight and BlendIndices are channels 12 and 13
FORMAT_SIZE = {0: 4, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2, 6: 1, 7: 1, 8: 2, 9: 2, 10: 4, 11: 4}
WEIGHT_CODES = {0: "f", 1: "e"}
INDEX_CODES = {6: "B", 7: "b", 8: "H", 9: "h", 10: "I", 11: "i"}
BLEND_WEIGHT, BLEND_INDICES = 12, 13


@dataclass
class SkinLayout:
    count: int
    weights: tuple[int, int, str]  # first byte, stride, struct code
    indices: tuple[int, int, str]


def skin_layout(mesh: Mapping[str, Any]) -> SkinLayout | None:
    """Where a Mesh typetree keeps its skin in ``m_VertexData``, when it is written plainly: no compressed mesh, no
    stream data, four float weights and four integer indices per vertex. None otherwise."""
    compressed = (mesh.get("m_CompressedMesh") or {}).get("m_Weights") or {}
    if compressed.get("m_NumItems") or (mesh.get("m_StreamData") or {}).get("size"):
        return None
    data = mesh.get("m_VertexData") or {}
    channels, count, raw = data.get("m_Channels") or [], int(data.get("m_VertexCount") or 0), data.get("m_DataSize")
    if len(channels) <= BLEND_INDICES or not count or not isinstance(raw, (bytes, bytearray)):
        return None
    weight, index = channels[BLEND_WEIGHT], channels[BLEND_INDICES]
    if (weight["dimension"] & 0xF) != 4 or (index["dimension"] & 0xF) != 4 or \
            weight["format"] not in WEIGHT_CODES or index["format"] not in INDEX_CODES:
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

    def place(channel: Mapping[str, int], code: str) -> tuple[int, int, str]:
        s, size = channel["stream"], FORMAT_SIZE[channel["format"]]
        if channel["offset"] + 4 * size > strides[s] or starts[s] + count * strides[s] > len(raw):
            raise ValueError("channel outside its stream")
        return starts[s] + channel["offset"], strides[s], code

    try:
        return SkinLayout(count, place(weight, WEIGHT_CODES[weight["format"]]),
                          place(index, INDEX_CODES[index["format"]]))
    except ValueError:
        return None


def read_skin(mesh: Mapping[str, Any], layout: SkinLayout) -> tuple[np.ndarray, np.ndarray]:
    raw = mesh["m_VertexData"]["m_DataSize"]
    (ws, wstride, wcode), (js, jstride, jcode) = layout.weights, layout.indices
    weights = np.array([struct.unpack_from("<4" + wcode, raw, ws + v * wstride) for v in range(layout.count)], float)
    joints = np.array([struct.unpack_from("<4" + jcode, raw, js + v * jstride) for v in range(layout.count)], int)
    return joints, weights


def write_skin(mesh: dict, layout: SkinLayout, rows: np.ndarray, joints: np.ndarray, weights: np.ndarray) -> None:
    """Rewrite the skin of the vertices ``rows`` in place; every other byte stays as it is."""
    data = mesh["m_VertexData"]
    raw = bytearray(data["m_DataSize"])
    (ws, wstride, wcode), (js, jstride, jcode) = layout.weights, layout.indices
    for row, j, w in zip(rows.tolist(), joints.tolist(), weights.tolist()):
        struct.pack_into("<4" + wcode, raw, ws + row * wstride, *w)
        struct.pack_into("<4" + jcode, raw, js + row * jstride, *(int(x) for x in j))
    data["m_DataSize"] = bytes(raw)


# --- compressed meshes: 5-bit weights, a vertex's weights add up to 31 ------------------------------------------------

STEPS = 31


def unpack_bits(packed: Mapping[str, Any]) -> list[int]:
    count, bits = int(packed.get("m_NumItems") or 0), int(packed.get("m_BitSize") or 0)
    if not count or not bits:
        return [0] * count
    raw = np.frombuffer(bytes(packed["m_Data"]), np.uint8)
    flat = np.unpackbits(raw, bitorder="little")[:count * bits].reshape(count, bits).astype(np.int64)
    return (flat << np.arange(bits)).sum(axis=1).tolist()


def pack_bits(values: list[int], bits: int) -> bytes:
    if not values:
        return b""
    array = np.asarray(values, np.int64)
    flat = ((array[:, None] >> np.arange(bits)) & 1).astype(np.uint8).ravel()
    return np.packbits(flat, bitorder="little").tobytes()


def compressed_rows(weights: list[int], bones: list[int], count: int) -> list[tuple[list[int], list[int]]] | None:
    """The weight and bone index items of each vertex as stored (the reading of ``model.compressed_skin``); None when
    the items do not make exactly ``count`` vertices."""
    rows: list[tuple[list[int], list[int]]] = []
    w_items: list[int] = []
    b_items: list[int] = []
    total, b = 0, 0
    for w in weights:
        if b >= len(bones):
            return None
        w_items.append(w)
        b_items.append(bones[b])
        b += 1
        total += w
        if total >= STEPS or len(w_items) == 3:
            if total < STEPS:  # the fourth weight is implied, only its bone is stored
                if b >= len(bones):
                    return None
                b_items.append(bones[b])
                b += 1
            rows.append((w_items, b_items))
            w_items, b_items, total = [], [], 0
    if w_items or b != len(bones) or len(rows) != count:
        return None
    return rows


def quantize(weights: np.ndarray) -> list[int]:
    """Weights (summing to 1) as steps of 1/31 summing to 31, by the largest remainders."""
    scaled = np.clip(np.asarray(weights, float), 0.0, None) * STEPS / max(float(np.sum(weights)), 1e-12)
    steps = np.floor(scaled).astype(int)
    for k in np.argsort(-(scaled - steps), kind="stable")[:STEPS - int(steps.sum())]:
        steps[k] += 1
    return steps.tolist()


def encode_row(joints: np.ndarray, weights: np.ndarray) -> tuple[list[int], list[int]]:
    steps = quantize(weights)
    kept = [(s, int(j)) for s, j in zip(steps, joints) if s > 0]
    if len(kept) == 4:
        return [s for s, _ in kept[:3]], [j for _, j in kept]
    return [s for s, _ in kept], [j for _, j in kept]


@dataclass
class CompressedLayout:
    count: int
    rows: list[tuple[list[int], list[int]]]


def compressed_layout(mesh: Mapping[str, Any]) -> CompressedLayout | None:
    compressed = mesh.get("m_CompressedMesh") or {}
    weights, bones = compressed.get("m_Weights") or {}, compressed.get("m_BoneIndices") or {}
    if not weights.get("m_NumItems") or (mesh.get("m_StreamData") or {}).get("size"):
        return None
    if int(weights.get("m_BitSize") or 0) != 5:
        return None
    count = int((compressed.get("m_Vertices") or {}).get("m_NumItems") or 0) // 3
    rows = compressed_rows(unpack_bits(weights), unpack_bits(bones), count)
    return CompressedLayout(count, rows) if rows is not None else None


def write_compressed(mesh: dict, layout: CompressedLayout, rows: np.ndarray, joints: np.ndarray,
                     weights: np.ndarray) -> None:
    """Re-encode the skin of the vertices ``rows``; the other vertices keep their stored items."""
    stored = list(layout.rows)
    for row, j, w in zip(rows.tolist(), joints, weights):
        stored[row] = encode_row(np.asarray(j), np.asarray(w))
    w_items = [w for items, _ in stored for w in items]
    b_items = [b for _, items in stored for b in items]
    compressed = mesh["m_CompressedMesh"]
    for key, items, bits in (("m_Weights", w_items, 5),
                             ("m_BoneIndices", b_items, max(int(compressed["m_BoneIndices"].get("m_BitSize") or 0),
                                                            max(b_items, default=0).bit_length(), 1))):
        packed = compressed[key]
        data = pack_bits(items, bits)
        packed["m_NumItems"] = len(items)
        packed["m_BitSize"] = bits
        packed["m_Data"] = data if isinstance(packed.get("m_Data"), (bytes, bytearray)) else list(data)
    layout.rows = stored


def store_of(mesh: Mapping[str, Any]) -> SkinLayout | CompressedLayout | None:
    """Where a Mesh typetree keeps its skin, when this module can rewrite it (not with per-vertex bone counts)."""
    if (mesh.get("m_VariableBoneCountWeights") or {}).get("m_Data"):
        return None
    return skin_layout(mesh) or compressed_layout(mesh)


def write_weights(mesh: dict, store: SkinLayout | CompressedLayout, rows: np.ndarray, joints: np.ndarray,
                  weights: np.ndarray) -> None:
    if isinstance(store, CompressedLayout):
        write_compressed(mesh, store, rows, joints, weights)
    else:
        write_skin(mesh, store, rows, joints, weights)
