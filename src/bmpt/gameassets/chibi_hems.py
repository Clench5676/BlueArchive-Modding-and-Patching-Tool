"""Skirts and hems of a costume put on another alt's chibi.

The game keeps legs inside skirts by keying the skirt bones in every clip of the costume's own chibi. A costume moved
onto another chibi gets no curves from that chibi's clips: its skirt bones follow their Biped bone rigidly (the group
choice of ``chibi_adapt``) and the legs pass through. Fixed skin weights are what the format allows, so part of each
hem vertex's rigid weight moves to the thighs and calves by one of a few rules, and a measurement under the slot's own
clips picks one:

- ramps ("blend", "short"): by height below the hip toward the thigh, below the knee toward the calf, split between the
  legs by distance;
- "fit": weights on the pelvis, thighs and calves fitted by least squares to the costume's own keyed clips (the
  vendor's motion, where the costume has clips);
- the pick: the lowest mean share of covered leg vertices that end up more than 1 cm outside the cloth, among the rules
  whose cloth stretch stays within the slot's own range (the limit) and that make no clip more than 5 points worse
  than rigid weights.

Pure numpy; the chibi plumbing is in ``chibi_adapt``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

DEEP = 0.01  # a leg vertex this far outside the cloth passes through (metres)
TOLERANCE = 0.003  # crossings this close to a leg vertex decide nothing
NEAR_AXIS = 0.01  # leg vertices this close to the body axis have no direction
WORSE = 5.0  # no clip may get more than this many points worse than rigid weights
STRETCH_FLOOR = 1.5
RAY_CHUNK = 32


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    """Blend values from zero at a to one at b with a clamped cubic smoothstep."""
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return each 3D point's Euclidean distance to the segment from a to b."""
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(points - (a + np.outer(t, ab)), axis=1)


def leg_distance(points: np.ndarray, hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
    """Return each point's distance to the hip-knee-ankle leg axis in the same coordinate space."""
    return np.minimum(segment_distance(points, hip, knee), segment_distance(points, knee, ankle))


# --- weights -------------------------------------------------------------------------------------------------------


def densify(columns: np.ndarray, weights: np.ndarray, width: int) -> np.ndarray:
    """Sum per-vertex influences into a dense vertex-by-bone matrix.

    Bones driving the same slot bone add up, so keeping four later drops no real influence.

    Args:
        columns: Bone-column index for each stored influence, shaped like weights.
        weights: Per-vertex influence weights, shaped as vertices by influences.
        width: Number of destination bone columns.
    """
    dense = np.zeros((len(weights), width))
    rows = np.repeat(np.arange(len(weights))[:, None], columns.shape[1], axis=1)
    np.add.at(dense, (rows, columns), weights)
    return dense


def reweight(
    dense: np.ndarray, rigid: np.ndarray, fraction: np.ndarray, moves: np.ndarray, columns: list[int]
) -> np.ndarray:
    """Move a fraction of rigid influence onto the selected leg-bone columns.

    Args:
        dense: Current vertex-by-bone weights.
        rigid: Rigid portion of dense, in the same shape.
        fraction: Per-vertex fraction of rigid weight to redistribute.
        moves: Destination shares per vertex, with rows summing to one.
        columns: Destination bone columns corresponding to the columns of moves.
    """
    share = rigid.sum(axis=1)
    out = dense - rigid * fraction[:, None]
    out[:, columns] += (share * fraction)[:, None] * moves
    return out


def ramp(
    height: np.ndarray,
    near: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    alpha: float,
    top: float,
    knee: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Compute rigid-weight transfer fractions and thigh/calf shares from hem position.

    Args:
        height: Distance below the hip in thigh lengths for each vertex.
        near: Proximity factor, one beside the legs and zero from two hip widths away.
        left: Closeness to the left leg, used to divide influence between sides.
        right: Closeness to the right leg, used with left to divide influence.
        alpha: Maximum fraction of rigid weight to transfer.
        top: Thigh-length interval over which the transfer fraction rises from the hip.
        knee: Thigh-length interval over which calf influence replaces thigh influence.

    Returns:
        Per-vertex fractions and shares on left thigh, right thigh, left calf, and right calf.
    """
    fraction = alpha * smoothstep(0.0, top, height) * near
    calf = smoothstep(knee[0], knee[1], height)
    total = left + right
    side = np.where(total > 1e-12, left / np.maximum(total, 1e-12), 0.5)
    moves = np.stack([side * (1 - calf), (1 - side) * (1 - calf), side * calf, (1 - side) * calf], axis=1)
    return fraction, moves


def keep_four(dense: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Keep four largest nonnegative influences per dense weight row and normalize nonzero totals.

    Return columns and weights, largest first; unused influences repeat the first column with zero weight.

    Args:
        dense: Vertex-by-bone influences; negative values are clipped and all-zero rows retain zero total weight.
    """
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
    """Apply linear-blend skinning to rest vertices.

    Args:
        rest: Vertex positions shaped as vertices by 3 in the matrices' input space.
        joints: Matrix indices for each vertex influence.
        weights: Influence weights in the same shape as joints.
        matrices: Bone skin matrices shaped as bones by 4 by 4.
    """
    homogeneous = np.c_[rest, np.ones(len(rest))]
    out = np.zeros((len(rest), 3))
    for k in range(joints.shape[1]):
        out += weights[:, k : k + 1] * np.einsum("nij,nj->ni", matrices[joints[:, k]], homogeneous)[:, :3]
    return out


class Normal:
    """Accumulate per-vertex normal equations for least squares basis @ w ~ target across frames."""

    def __init__(self, count: int, bones: int):
        """Allocate empty equations for count vertices with bones candidate influences each."""
        self.gram = np.zeros((count, bones, bones))
        self.right = np.zeros((count, bones))
        self.square = np.zeros(count)
        self.frames = 0

    def add(self, basis: np.ndarray, target: np.ndarray) -> None:
        """Accumulate one frame's target positions and candidate bone placements.

        Args:
            basis: Positions under each bone alone, shaped as vertices by 3 by bones.
            target: Desired positions in the same space, shaped as vertices by 3.
        """
        self.gram += np.einsum("nri,nrj->nij", basis, basis)
        self.right += np.einsum("nri,nr->ni", basis, target)
        self.square += (target * target).sum(axis=1)
        self.frames += 1


def fit_simplex(normal: Normal) -> tuple[np.ndarray, np.ndarray]:
    """Fit nonnegative unit-sum weights per vertex and return weights with RMS residuals per frame.

    Solve every bone subset with the sum constraint and keep the best feasible solution.

    Args:
        normal: Equations accumulated from candidate placements and target positions across frames.
    """
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
        residual = (
            normal.square
            - 2 * (solution * normal.right[:, chosen]).sum(axis=1)
            + np.einsum("ni,nij,nj->n", solution, gram, solution)
        )
        better = feasible & (residual < best - 1e-12)
        best[better] = residual[better]
        weights = np.zeros((int(better.sum()), bones))
        weights[:, chosen] = np.clip(solution[better], 0.0, None)
        out[better] = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    return out, np.sqrt(np.maximum(best, 0.0) / max(1, normal.frames))


# --- measures ------------------------------------------------------------------------------------------------------


def stretch_p99(rest: np.ndarray, posed: np.ndarray, edges: np.ndarray) -> float:
    """Measure the 99th percentile edge-length ratio, with one meaning unchanged.

    Args:
        rest: Rest vertex positions in the same units as posed.
        posed: Corresponding vertex positions after skinning.
        edges: Vertex-index pairs to measure; empty or degenerate edges yield one.
    """
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


def cover(
    points: np.ndarray, legs: np.ndarray, triangles: np.ndarray, centre: np.ndarray, up: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Classify leg vertices against cloth along rays from the body axis.

    Args:
        points: Shared posed vertices for legs and cloth, in metres.
        legs: Indices into points for the leg vertices to classify.
        triangles: Cloth triangles indexing points.
        centre: Point on the body axis in the same space as points.
        up: Body-axis direction, normalized before ray construction.

    Returns:
        States (+1 cloth beyond, -1 all crossings nearer, 0 no decisive crossing/on axis)
        and distance outside the farthest crossing, NaN without a crossing.
    """
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
        chunk = rays[start : start + RAY_CHUNK]
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
    """Measure the percentage of rest-covered leg vertices more than DEEP metres outside cloth.

    Args:
        state: cover classifications restricted to the vertices covered at rest.
        depth: Corresponding distances outside cloth in metres; NaNs do not count as deep.
    """
    if not len(state):
        return 0.0
    with np.errstate(invalid="ignore"):
        return 100.0 * float(((state == -1) & (depth > DEEP)).sum()) / len(state)


@dataclass
class Score:
    """Record a hem rule's worst per-clip pass-through percentages and maximum stretch percentile."""

    rule: str
    deep: dict[str, float]  # per clip: worst frame's deep pass-through, percent
    stretch: float  # worst cloth stretch p99 over the clips
    complete: bool = True  # a rejected candidate may stop before its remaining clips

    @property
    def mean(self) -> float:
        """Return mean worst-clip pass-through percentage, or zero before any clip is measured."""
        return sum(self.deep.values()) / len(self.deep) if self.deep else 0.0


def choose(scores: list[Score], limit: float) -> Score:
    """Choose the lowest mean pass-through within rigid-relative stretch and per-clip limits.

    Ties prefer rigid, then less stretch; no clip may worsen by more than WORSE percentage points.

    Args:
        scores: Measured candidates, with the rigid baseline first; incomplete candidates are ineligible.
        limit: Allowed stretch ratio, raised to the rigid baseline's stretch when necessary.
    """
    rigid = scores[0]
    ceiling = max(limit, rigid.stretch)
    allowed = [
        s
        for s in scores[1:]
        if s.complete
        and s.stretch <= ceiling + 1e-9
        and all(s.deep.get(c, 0.0) <= d + WORSE for c, d in rigid.deep.items())
    ]
    return min([*allowed, rigid], key=lambda s: (round(s.mean, 1), s is not rigid, s.stretch))


def cannot_win(partial: Score, scores: list[Score], limit: float) -> bool:
    """Reject only when even zero pass-through in the remaining clips loses.

    Stretch and each measured clip's worst depth can only increase. Use the
    chooser itself for the rounded mean, rigid preference, stretch and stable
    candidate order, including the rigid score's stretch ceiling.

    Args:
        partial: Candidate with only some clip measurements, whose unmeasured clips optimistically score zero.
        scores: Completed candidates, with the rigid baseline first and its full clip set.
        limit: Allowed stretch ratio passed to choose.
    """
    optimistic = Score(partial.rule, {c: partial.deep.get(c, 0.0) for c in scores[0].deep}, partial.stretch)
    return choose([*scores, optimistic], limit) is not optimistic


# --- vertex data ---------------------------------------------------------------------------------------------------

# VertexFormat (Unity 2019+): component sizes; BlendWeight and BlendIndices are channels 12 and 13
FORMAT_SIZE = {0: 4, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2, 6: 1, 7: 1, 8: 2, 9: 2, 10: 4, 11: 4}
WEIGHT_CODES = {0: "f", 1: "e"}
INDEX_CODES = {6: "B", 7: "b", 8: "H", 9: "h", 10: "I", 11: "i"}
BLEND_WEIGHT, BLEND_INDICES = 12, 13


@dataclass
class SkinLayout:
    """Locate four plain weights and bone indices per vertex by byte offset, stride, and struct code."""

    count: int
    weights: tuple[int, int, str]  # first byte, stride, struct code
    indices: tuple[int, int, str]


def skin_layout(mesh: Mapping[str, Any]) -> SkinLayout | None:
    """Locate a Mesh typetree's plain four-float/four-index skin, or None for compressed/streamed/unsupported data."""
    compressed = (mesh.get("m_CompressedMesh") or {}).get("m_Weights") or {}
    if compressed.get("m_NumItems") or (mesh.get("m_StreamData") or {}).get("size"):
        return None
    data = mesh.get("m_VertexData") or {}
    channels, count, raw = data.get("m_Channels") or [], int(data.get("m_VertexCount") or 0), data.get("m_DataSize")
    if len(channels) <= BLEND_INDICES or not count or not isinstance(raw, (bytes, bytearray)):
        return None
    weight, index = channels[BLEND_WEIGHT], channels[BLEND_INDICES]
    if (
        (weight["dimension"] & 0xF) != 4
        or (index["dimension"] & 0xF) != 4
        or weight["format"] not in WEIGHT_CODES
        or index["format"] not in INDEX_CODES
    ):
        return None
    starts, strides, offset = [], [], 0
    for stream in range(1 + max(c["stream"] for c in channels)):
        stride = sum(
            (c["dimension"] & 0xF) * FORMAT_SIZE.get(c["format"], 0)
            for c in channels
            if c["stream"] == stream and c["dimension"]
        )
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
        return SkinLayout(
            count, place(weight, WEIGHT_CODES[weight["format"]]), place(index, INDEX_CODES[index["format"]])
        )
    except ValueError:
        return None


def read_skin(mesh: Mapping[str, Any], layout: SkinLayout) -> tuple[np.ndarray, np.ndarray]:
    """Decode plain skin rows as bone-index and weight arrays.

    Args:
        mesh: Mesh typetree with inline m_VertexData bytes.
        layout: Validated offsets and formats returned by skin_layout for this mesh.
    """
    raw = mesh["m_VertexData"]["m_DataSize"]
    (ws, wstride, wcode), (js, jstride, jcode) = layout.weights, layout.indices
    weights = np.array([struct.unpack_from("<4" + wcode, raw, ws + v * wstride) for v in range(layout.count)], float)
    joints = np.array([struct.unpack_from("<4" + jcode, raw, js + v * jstride) for v in range(layout.count)], int)
    return joints, weights


class SkinNotWritable(ValueError):
    """The skin cannot take the new bone indices in this mesh's index format: the part keeps its skin."""


INDEX_RANGE = {
    "B": (0, 1 << 8),
    "b": (-(1 << 7), 1 << 7),
    "H": (0, 1 << 16),
    "h": (-(1 << 15), 1 << 15),
    "I": (0, 1 << 32),
    "i": (-(1 << 31), 1 << 31),
}


def write_skin(mesh: dict, layout: SkinLayout, rows: np.ndarray, joints: np.ndarray, weights: np.ndarray) -> None:
    """Rewrite selected plain skin rows in place, preserving every other byte.

    Args:
        mesh: Mutable Mesh typetree containing inline vertex bytes.
        layout: Validated plain-skin offsets and formats for this mesh.
        rows: Vertex indices to rewrite, in the same order as joints and weights.
        joints: Four destination bone indices per selected row.
        weights: Four destination influence weights per selected row.

    Raises:
        SkinNotWritable: A bone index exceeds the stored format (255 for unsigned 8-bit), before mutation.
    """
    data = mesh["m_VertexData"]
    raw = bytearray(data["m_DataSize"])
    (ws, wstride, wcode), (js, jstride, jcode) = layout.weights, layout.indices
    low, high = INDEX_RANGE[jcode]
    if len(joints) and (int(np.min(joints)) < low or int(np.max(joints)) >= high):
        raise SkinNotWritable(f"bone index outside {low}..{high - 1}")
    for row, j, w in zip(rows.tolist(), joints.tolist(), weights.tolist()):
        struct.pack_into("<4" + wcode, raw, ws + row * wstride, *w)
        struct.pack_into("<4" + jcode, raw, js + row * jstride, *(int(x) for x in j))
    data["m_DataSize"] = bytes(raw)


# --- compressed meshes: 5-bit weights, a vertex's weights add up to 31 ------------------------------------------------

STEPS = 31


def unpack_bits(packed: Mapping[str, Any]) -> list[int]:
    """Unpack a Unity packed integer typetree in little-bit order, yielding zeros for zero-bit items."""
    count, bits = int(packed.get("m_NumItems") or 0), int(packed.get("m_BitSize") or 0)
    if not count or not bits:
        return [0] * count
    raw = np.frombuffer(bytes(packed["m_Data"]), np.uint8)
    flat = np.unpackbits(raw, bitorder="little")[: count * bits].reshape(count, bits).astype(np.int64)
    return (flat << np.arange(bits)).sum(axis=1).tolist()


def pack_bits(values: list[int], bits: int) -> bytes:
    """Pack integer values in Unity's little-bit order.

    Args:
        values: Nonnegative integer items whose low bits are stored in order.
        bits: Bit width allocated to each item.
    """
    if not values:
        return b""
    array = np.asarray(values, np.int64)
    flat = ((array[:, None] >> np.arange(bits)) & 1).astype(np.uint8).ravel()
    return np.packbits(flat, bitorder="little").tobytes()


def compressed_rows(weights: list[int], bones: list[int], count: int) -> list[tuple[list[int], list[int]]] | None:
    """Split compressed skin items into stored per-vertex weight and bone rows.

    Args:
        weights: Unpacked 5-bit weights with an implied fourth weight when needed, as chibi.compressed_skin reads.
        bones: Unpacked bone indices including implied fourth influences.
        count: Required vertex count; malformed or differently sized input returns None.
    """
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
    """Quantize a positive-total weight row to 1/31 steps summing to 31 by largest remainders."""
    scaled = np.clip(np.asarray(weights, float), 0.0, None) * STEPS / max(float(np.sum(weights)), 1e-12)
    steps = np.floor(scaled).astype(int)
    for k in np.argsort(-(scaled - steps), kind="stable")[: STEPS - int(steps.sum())]:
        steps[k] += 1
    return steps.tolist()


def encode_row(joints: np.ndarray, weights: np.ndarray) -> tuple[list[int], list[int]]:
    """Encode one skin row as 5-bit weights and bone indices, omitting the fourth weight.

    Args:
        joints: Up to four bone indices aligned with weights.
        weights: Influence weights to quantize; zero-weight influences are omitted.
    """
    steps = quantize(weights)
    kept = [(s, int(j)) for s, j in zip(steps, joints) if s > 0]
    if len(kept) == 4:
        return [s for s, _ in kept[:3]], [j for _, j in kept]
    return [s for s, _ in kept], [j for _, j in kept]


@dataclass
class CompressedLayout:
    """Retain a compressed skin's vertex count and original per-vertex weight/bone items."""

    count: int
    rows: list[tuple[list[int], list[int]]]


def compressed_layout(mesh: Mapping[str, Any]) -> CompressedLayout | None:
    """Locate a Mesh typetree's inline 5-bit compressed skin, or None when its layout is unsupported."""
    compressed = mesh.get("m_CompressedMesh") or {}
    weights, bones = compressed.get("m_Weights") or {}, compressed.get("m_BoneIndices") or {}
    if not weights.get("m_NumItems") or (mesh.get("m_StreamData") or {}).get("size"):
        return None
    if int(weights.get("m_BitSize") or 0) != 5:
        return None
    count = int((compressed.get("m_Vertices") or {}).get("m_NumItems") or 0) // 3
    rows = compressed_rows(unpack_bits(weights), unpack_bits(bones), count)
    return CompressedLayout(count, rows) if rows is not None else None


def write_compressed(
    mesh: dict, layout: CompressedLayout, rows: np.ndarray, joints: np.ndarray, weights: np.ndarray
) -> None:
    """Re-encode selected compressed skin rows, retaining other vertices' stored items.

    Args:
        mesh: Mutable Mesh typetree whose compressed weights and bone indices are replaced.
        layout: Validated compressed layout, updated in place with the newly stored rows.
        rows: Vertex indices to rewrite, aligned with joints and weights.
        joints: Up to four destination bone indices per selected row.
        weights: Destination influence weights to quantize to 1/31 steps.
    """
    stored = list(layout.rows)
    for row, j, w in zip(rows.tolist(), joints, weights):
        stored[row] = encode_row(np.asarray(j), np.asarray(w))
    w_items = [w for items, _ in stored for w in items]
    b_items = [b for _, items in stored for b in items]
    compressed = mesh["m_CompressedMesh"]
    for key, items, bits in (
        ("m_Weights", w_items, 5),
        (
            "m_BoneIndices",
            b_items,
            max(int(compressed["m_BoneIndices"].get("m_BitSize") or 0), max(b_items, default=0).bit_length(), 1),
        ),
    ):
        packed = compressed[key]
        data = pack_bits(items, bits)
        packed["m_NumItems"] = len(items)
        packed["m_BitSize"] = bits
        packed["m_Data"] = data if isinstance(packed.get("m_Data"), (bytes, bytearray)) else list(data)
    layout.rows = stored


def store_of(mesh: Mapping[str, Any]) -> SkinLayout | CompressedLayout | None:
    """Locate a Mesh typetree's writable skin, or None for unsupported layouts or variable per-vertex bone counts."""
    if (mesh.get("m_VariableBoneCountWeights") or {}).get("m_Data"):
        return None
    return skin_layout(mesh) or compressed_layout(mesh)


def write_weights(
    mesh: dict, store: SkinLayout | CompressedLayout, rows: np.ndarray, joints: np.ndarray, weights: np.ndarray
) -> None:
    """Rewrite selected skin rows through their plain or compressed storage layout.

    Args:
        mesh: Mutable Mesh typetree to update in place.
        store: Validated writable skin layout returned by store_of.
        rows: Vertex indices to rewrite, aligned with joints and weights.
        joints: Four destination bone indices per selected row.
        weights: Four destination influence weights per selected row.
    """
    if isinstance(store, CompressedLayout):
        write_compressed(mesh, store, rows, joints, weights)
    else:
        write_skin(mesh, store, rows, joints, weights)
