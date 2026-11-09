"""Skirt bones for a costume put on another variant's model (BL-124, Rule B).

The game keeps legs inside skirts by keying the skirt bones in every clip of the costume's own model. A costume moved
onto another model has no such curves in that model's clips. Rule B rebuilds them: the costume's own skirt chains (the
secondary bones hanging off the pelvis around the legs, which the group choice left rigid) become new bones of the
slot's skeleton, and every clip of the slot that moves the body gets rotation curves for the chains' first bones,
solved frame by frame:

- the legs: how far the costume's own leg vertices, on the slot's leg bones in that frame, reach from the body axis
  (the pelvis-carried vertical), per height and direction around it;
- all chains at once turn about their first bones' heads (Gauss-Newton on their rotation vectors) to carry the cloth
  inside the legs' reach straight out, away from the body axis, while the cloth already clear holds its place and no
  edge of the cloth grows (the cloth between two chains pulls the one turned less along); never farther out than it
  was at rest, at most ``MAX_ANGLE`` from the pelvis; the rest of each chain follows as it hangs at rest;
- in time: each frame starts from the turns of the frame before, let back toward rest (``RELEASE``), so that a chain
  lifted by a leg falls back over a few frames instead of jumping; a looping clip is run twice, so that its end leads
  into its start;
- optionally (``smooth_weights``) the cloth's weights blend across the seams between chains, so that chains turned
  apart pull the cloth between them instead of tearing it; at rest every bone sits where it was, so the rest pose does
  not change.

A chain follows the pelvis wherever no leg pushes it. ``model_adapt`` measures the result like the hem rules
(``model_hems``) and picks it only where it keeps the legs inside best within the same stretch limit.

Clips: the curves go into the clips' dense part (``add_dense_curves``), which the game reads beside the streamed and
constant parts; nothing else of a clip changes.

Pure numpy; the model plumbing is in ``model_adapt``.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

MARGIN = 0.015          # cloth keeps this far outside the legs (metres)
STEP = 0.01             # height of a cell of the legs' reach along the body axis (metres)
SECTORS = 64            # cells around the body axis
LOW, HIGH = -1.6, 0.6   # heights the cells cover, from the pelvis (metres)
ITERATIONS = 4          # Gauss-Newton steps per frame
HOLD = 0.1              # weight of cloth already clear of the legs, against the cloth pushed out
MIN_ANGLE = 1e-4        # a smaller step ends a frame's iterations (radians)
MAX_ANGLE = math.radians(45)  # a chain turns at most this far from where the pelvis carries it
SMOOTH_SWEEPS = 6       # weight blending sweeps across the seams (the "smooth" variant)
SOLVE_EVERY = 3         # clip frames between solved frames; the frames between are interpolated
RELEASE = 0.3           # share of its turn a chain keeps after 0.1 s without a push (it falls back toward rest)
LENGTH_WEIGHT = 10.0    # weight of an edge's length against a pushed vertex's place
DAMPING = 1e-4          # keeps each update small where nothing pins a chain

UP, FRONT = np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])  # Unity's vertical and forward


# --- rotations -----------------------------------------------------------------------------------------------------

def rigid_fit(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The rigid transform (4x4) that best carries the points ``a`` onto ``b`` (weights ``w``)."""
    total = float(w.sum())
    ca, cb = (a * w[:, None]).sum(axis=0) / total, (b * w[:, None]).sum(axis=0) / total
    h = ((a - ca) * w[:, None]).T @ (b - cb)
    u, _, vt = np.linalg.svd(h)
    d = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    out = np.eye(4)
    out[:3, :3] = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    out[:3, 3] = cb - out[:3, :3] @ ca
    return out


def angle_of(r: np.ndarray) -> float:
    return math.acos(max(-1.0, min(1.0, (float(np.trace(r)) - 1) / 2)))


def rotation_axis(r: np.ndarray) -> np.ndarray:
    axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]])
    n = float(np.linalg.norm(axis))
    if n < 1e-9:  # no turn, or half a turn: the axis is the eigenvector of eigenvalue 1
        values, vectors = np.linalg.eigh((r + r.T) / 2)
        return vectors[:, int(np.argmax(values))]
    return axis / n


def axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    x, y, z = axis
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


def rotation_vector(r: np.ndarray) -> np.ndarray:
    angle = angle_of(r)
    return rotation_axis(r) * angle if angle > 1e-9 else np.zeros(3)


def from_vector(v: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(v))
    return axis_angle(v / angle, angle) if angle > 1e-9 else np.eye(3)


def scale_of(m: np.ndarray) -> np.ndarray:
    return np.diag(np.linalg.norm(m[:3, :3], axis=0))


def quaternion(r: np.ndarray) -> np.ndarray:
    """The unit quaternion (x, y, z, w) of a rotation matrix, as ``model_adapt.trs`` builds matrices from them."""
    m = r
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def continuous(quaternions: np.ndarray) -> np.ndarray:
    """``quaternions`` (frames x 4) with signs flipped so that each frame lies nearest the one before."""
    out = quaternions.copy()
    for i in range(1, len(out)):
        if float(out[i] @ out[i - 1]) < 0:
            out[i] = -out[i]
    return out


def resample(times: np.ndarray, quaternions: np.ndarray, at: np.ndarray) -> np.ndarray:
    """Quaternions (continuous, frames x 4) at ``times`` interpolated to ``at`` (normalised linear blend)."""
    out = np.stack([np.interp(at, times, quaternions[:, k]) for k in range(4)], axis=1)
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


# --- the legs' reach -----------------------------------------------------------------------------------------------

@dataclass
class Frame:
    """The body axis of a pose: through ``centre`` along ``up``, angles measured from ``front``."""

    centre: np.ndarray
    up: np.ndarray
    front: np.ndarray

    def cylinder(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Height, angle, distance from the axis and the unit direction away from it, per point."""
        d = points - self.centre
        height = d @ self.up
        radial = d - np.outer(height, self.up)
        distance = np.linalg.norm(radial, axis=1)
        side = np.cross(self.up, self.front)
        angle = np.arctan2(radial @ side, radial @ self.front)
        direction = np.where(distance[:, None] > 1e-9, radial / np.maximum(distance, 1e-9)[:, None], self.front)
        return height, angle, distance, direction


ROWS = round((HIGH - LOW) / STEP)


def cells(height: np.ndarray, angle: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = np.floor((height - LOW) / STEP).astype(int)
    columns = np.floor((angle + math.pi) / (2 * math.pi) * SECTORS).astype(int) % SECTORS
    inside = (rows >= 0) & (rows < ROWS)
    return np.clip(rows, 0, ROWS - 1), columns, inside


def envelope(frame: Frame, legs: np.ndarray) -> np.ndarray:
    """How far the legs reach from the body axis in each cell (height x angle; -inf where no leg is), widened by one
    cell each way so that the gaps between leg vertices count as leg."""
    height, angle, distance, _ = frame.cylinder(legs)
    rows, columns, inside = cells(height, angle)
    grid = np.full((ROWS, SECTORS), -np.inf)
    np.maximum.at(grid, (rows[inside], columns[inside]), distance[inside])
    wide = grid.copy()
    for dr in (-1, 0, 1):
        shifted = np.roll(grid, dr, axis=0)
        if dr == 1:
            shifted[0] = -np.inf
        elif dr == -1:
            shifted[-1] = -np.inf
        for dc in (-1, 0, 1):
            wide = np.maximum(wide, np.roll(shifted, dc, axis=1))
    return wide


def reach(grid: np.ndarray, frame: Frame, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each point: how far the legs reach in its cell, its distance from the axis and its direction away from it."""
    height, angle, distance, direction = frame.cylinder(points)
    rows, columns, inside = cells(height, angle)
    return np.where(inside, grid[rows, columns], -np.inf), distance, direction


def _skew(v: np.ndarray) -> np.ndarray:
    """Per row, the matrix of the cross product with it (``_skew(v) @ w == v x w``)."""
    out = np.zeros((len(v), 3, 3))
    out[:, 0, 1], out[:, 0, 2] = -v[:, 2], v[:, 1]
    out[:, 1, 0], out[:, 1, 2] = v[:, 2], -v[:, 0]
    out[:, 2, 0], out[:, 2, 1] = -v[:, 1], v[:, 0]
    return out


def skin(rest: np.ndarray, joints: np.ndarray, weights: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    homogeneous = np.c_[rest, np.ones(len(rest))]
    out = np.zeros((len(rest), 3))
    for k in range(joints.shape[1]):
        out += weights[:, k:k + 1] * np.einsum("nij,nj->ni", matrices[joints[:, k]], homogeneous)[:, :3]
    return out


@dataclass
class Legs:
    """The costume's leg vertices (the prefab root's space) and their influences as slot nodes."""

    rest: np.ndarray
    joints: np.ndarray
    weights: np.ndarray


# --- chains --------------------------------------------------------------------------------------------------------

@dataclass
class Chains:
    """The new bones: per bone its parent among them (-1: the slot's ``anchor``), its local and world matrices at rest
    (the prefab root's space) and its subtree (itself first, parents before children)."""

    anchor: int
    parents: list[int]
    local: np.ndarray
    rest: np.ndarray
    subtree: list[list[int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.subtree:
            children: dict[int, list[int]] = {}
            for i, p in enumerate(self.parents):
                children.setdefault(p, []).append(i)
            for i in range(len(self.parents)):
                out, todo = [], [i]
                while todo:
                    j = todo.pop(0)
                    out.append(j)
                    todo.extend(children.get(j, []))
                self.subtree.append(out)

    @property
    def roots(self) -> list[int]:
        return [i for i, p in enumerate(self.parents) if p < 0]

    def posed(self, anchor: np.ndarray, turns: Mapping[int, np.ndarray] | None = None) -> np.ndarray:
        """World matrices with the anchor at ``anchor``; a root in ``turns`` takes that local rotation (3x3)."""
        out = np.zeros((len(self.parents), 4, 4))
        for i, p in enumerate(self.parents):  # parents come first
            local = self.local[i]
            if turns is not None and i in turns:
                local = local.copy()
                local[:3, :3] = turns[i] @ scale_of(self.local[i])
            out[i] = (out[p] if p >= 0 else anchor) @ local
        return out


@dataclass
class Cloth:
    """The cloth the chains carry: rest vertices (the prefab root's space), their influences as columns into the
    chains' bones (``0 .. len(chains) - 1``) and then ``fixed`` slot nodes, and the edges between them."""

    rest: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    fixed: list[int]
    edges: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), int))


def smooth_weights(chain: np.ndarray, edges: np.ndarray, sweeps: int | None = None) -> np.ndarray:
    """``chain`` (vertices x chain bones: each vertex's weights on the chains) blended over ``edges`` with the
    neighbours that hang on the chains, ``sweeps`` times; each vertex keeps its total weight on the chains."""
    sweeps = SMOOTH_SWEEPS if sweeps is None else sweeps
    share = chain.sum(axis=1)
    on = share > 1e-9
    out = chain.copy()
    if not len(edges):
        return out
    a, b = edges[:, 0], edges[:, 1]
    for _ in range(sweeps):
        unit = out / np.where(on, share, 1.0)[:, None]
        total = np.zeros_like(unit)
        count = np.zeros(len(unit))
        np.add.at(total, a, unit[b] * on[b][:, None])
        np.add.at(total, b, unit[a] * on[a][:, None])
        np.add.at(count, a, on[b])
        np.add.at(count, b, on[a])
        mixed = np.where((count > 0)[:, None], 0.5 * unit + 0.5 * total / np.maximum(count, 1)[:, None], unit)
        out = np.where(on[:, None], mixed * share[:, None], out)
    return out


class Solver:
    """The chains' turns for a pose of the slot's skeleton (see the module's description)."""

    def __init__(self, chains: Chains, cloth: Cloth, legs: Legs, t_rest: Sequence[np.ndarray], pelvis: int):
        self.chains, self.cloth, self.legs, self.pelvis = chains, cloth, legs, pelvis
        n = len(chains.parents)
        rest = [np.asarray(m) for m in t_rest]
        self.inverse = np.array([np.linalg.inv(m) for m in chains.rest])
        self.fixed_inverse = np.array([np.linalg.inv(rest[f]) for f in cloth.fixed]).reshape(-1, 4, 4)
        self.leg_nodes = sorted(set(legs.joints.ravel().tolist()))
        lookup = {node: k for k, node in enumerate(self.leg_nodes)}
        self.leg_columns = np.vectorize(lookup.get, otypes=[int])(legs.joints)
        self.leg_inverse = np.array([np.linalg.inv(rest[f]) for f in self.leg_nodes]).reshape(-1, 4, 4)
        self.pelvis_rest = np.linalg.inv(rest[pelvis][:3, :3])
        self.roots = chains.roots
        on_chain = cloth.joints < n
        self.masks = {i: (np.isin(cloth.joints, chains.subtree[i]) & on_chain & (cloth.weights > 0)).astype(float)
                      for i in self.roots}
        # each influence's share per root (vertices x influences x roots), the roots' total share, the edges' lengths
        self.parts = np.stack([self.masks[r] * cloth.weights for r in self.roots], axis=2) if self.roots else np.zeros(
            cloth.weights.shape + (0,))
        self.part_share = self.parts.sum(axis=1)
        e = cloth.edges
        self.lengths = np.linalg.norm(cloth.rest[e[:, 1]] - cloth.rest[e[:, 0]], axis=1) if len(e) else np.zeros(0)
        # how far outside the legs each cloth vertex was at rest: it never has to be farther out than that
        frame = self.frame(rest)
        legs_reach, distance, _ = reach(envelope(frame, legs.rest), frame, cloth.rest)
        self.allowance = np.minimum(MARGIN, np.where(np.isfinite(legs_reach), distance - legs_reach, np.inf))

    def frame(self, pose: Sequence[np.ndarray]) -> Frame:
        m = np.asarray(pose[self.pelvis])
        turn = m[:3, :3] @ self.pelvis_rest
        up, front = turn @ UP, turn @ FRONT
        return Frame(m[:3, 3].copy(), up / np.linalg.norm(up), front / np.linalg.norm(front))

    def legs_at(self, pose: Sequence[np.ndarray]) -> np.ndarray:
        matrices = np.array([pose[f] for f in self.leg_nodes]) @ self.leg_inverse
        return skin(self.legs.rest, self.leg_columns, self.legs.weights, matrices)

    def push(self, points: np.ndarray, rows: np.ndarray, grid: np.ndarray,
             frame: Frame) -> tuple[np.ndarray, np.ndarray]:
        """Where each point must go to clear the legs (straight away from the body axis), and whether it must move."""
        legs_reach, distance, direction = reach(grid, frame, points)
        needed = legs_reach + self.allowance[rows]
        moved = np.isfinite(legs_reach) & (distance < needed - 1e-6)
        target = points.copy()
        target[moved] += direction[moved] * (needed[moved] - distance[moved])[:, None]
        return target, moved

    def solve(self, pose: Sequence[np.ndarray], start: Mapping[int, np.ndarray] | None = None) -> dict[int, np.ndarray]:
        """Each chain root's local rotation (3x3) for ``pose`` (world matrices of the slot's nodes), starting from the
        roots' local rotations ``start`` (None: as they hang at rest)."""
        ch = self.chains
        anchor = np.asarray(pose[ch.anchor])
        fixed = np.array([np.asarray(pose[f]) for f in self.cloth.fixed]).reshape(-1, 4, 4) @ self.fixed_inverse
        frame = self.frame(pose)
        grid = envelope(frame, self.legs_at(pose))
        base = ch.posed(anchor)
        world = ch.posed(anchor, start) if start else base.copy()
        self._joint(world, base, fixed, grid, frame)
        out = {}
        for i in self.roots:
            local = np.linalg.inv(anchor) @ world[i]
            out[i] = local[:3, :3] @ np.linalg.inv(scale_of(local))
        return out

    def released(self, turns: Mapping[int, np.ndarray], seconds: float) -> dict[int, np.ndarray]:
        """``turns`` let back toward rest over ``seconds``."""
        keep = RELEASE ** (max(seconds, 0.0) / 0.1)
        out = {}
        for i, r in turns.items():
            rest = self.chains.local[i][:3, :3] @ np.linalg.inv(scale_of(self.chains.local[i]))
            out[i] = from_vector(rotation_vector(r @ rest.T) * keep) @ rest
        return out

    def sequence(self, times: Sequence[float], pose: Any, loop: bool = False) -> list[dict[int, np.ndarray]]:
        """The roots' turns for consecutive frames (``pose(k)``: the slot's world matrices at ``times[k]``), each
        starting from the frame before let back toward rest; a looping clip runs twice, the second time from the
        first's end."""
        poses = [pose(k) for k in range(len(times))]
        out: list[dict[int, np.ndarray]] = []
        previous, last = None, None
        for run in range(2 if loop and len(times) > 1 else 1):
            out = []
            for k, t in enumerate(times):
                if previous is not None:
                    step = t - last if k else (times[1] - times[0] if len(times) > 1 else 0.0)
                    previous = self.released(previous, step)
                previous = self.solve(poses[k], previous)
                last = t
                out.append(previous)
        return out

    def posed(self, pose: Sequence[np.ndarray], turns: Mapping[int, np.ndarray]) -> np.ndarray:
        """The chains' world matrices for ``pose`` and the roots' ``turns``."""
        return self.chains.posed(np.asarray(pose[self.chains.anchor]), turns)

    def _carry(self, world: np.ndarray, root: int) -> None:
        ch = self.chains
        for j in ch.subtree[root][1:]:  # the chain follows its first bone as it hangs at rest
            world[j] = world[ch.parents[j]] @ ch.local[j]

    def _joint(self, world: np.ndarray, base: np.ndarray, fixed: np.ndarray, grid: np.ndarray, frame: Frame) -> None:
        """All chains at once: Gauss-Newton steps on the roots' turns (rotation vectors about their heads) that carry
        the cloth inside the legs out, hold the cloth already clear, and keep the cloth's edges from growing."""
        c, roots = self.cloth, self.roots
        if not roots:
            return
        count, width = len(c.rest), 3 * len(roots)
        everything = np.arange(count)
        homogeneous = np.c_[c.rest, np.ones(count)]
        edges = c.edges
        for _ in range(ITERATIONS):
            matrices = world @ self.inverse
            if len(fixed):
                matrices = np.concatenate([matrices, fixed])
            # each influence's place, the vertices, and each root's share of them
            places = np.einsum("vkij,vj->vki", matrices[c.joints], homogeneous, optimize=True)[..., :3]
            points = (c.weights[..., None] * places).sum(axis=1)
            target, moved = self.push(points, everything, grid, frame)
            if not moved.any():
                break
            partial = np.einsum("vkr,vki->rvi", self.parts, places, optimize=True)
            heads = np.array([world[r][:3, 3] for r in roots])
            arm = partial - self.part_share.T[:, :, None] * heads[:, None, :]  # (roots, vertices, 3)
            # a vertex moves by sum_r omega_r x arm_r = -sum_r [arm_r]x omega_r
            jacobian = np.zeros((count, 3, len(roots), 3))
            jacobian[:, 0, :, 1], jacobian[:, 0, :, 2] = arm[:, :, 2].T, -arm[:, :, 1].T
            jacobian[:, 1, :, 0], jacobian[:, 1, :, 2] = -arm[:, :, 2].T, arm[:, :, 0].T
            jacobian[:, 2, :, 0], jacobian[:, 2, :, 1] = arm[:, :, 1].T, -arm[:, :, 0].T
            jacobian = jacobian.reshape(3 * count, width)
            weight = np.repeat(np.where(moved, 1.0, HOLD), 3)
            residual = np.where(moved[:, None], target - points, 0.0).ravel()
            a = (jacobian * weight[:, None]).T @ jacobian
            b = jacobian.T @ (weight * residual)
            if len(edges):
                d = points[edges[:, 1]] - points[edges[:, 0]]
                length = np.linalg.norm(d, axis=1)
                unit = d / np.maximum(length, 1e-12)[:, None]
                # an edge grows by unit . (move_b - move_a) = sum_r omega_r . ((arm_rb - arm_ra) x unit)
                rows = np.cross(arm[:, edges[:, 1]] - arm[:, edges[:, 0]], unit[None])
                rows = rows.transpose(1, 0, 2).reshape(len(edges), width)
                stretch = np.minimum(0.0, self.lengths - length)  # pull grown edges back, keep the others
                a += LENGTH_WEIGHT * rows.T @ rows
                b += LENGTH_WEIGHT * rows.T @ stretch
            a += DAMPING * np.eye(width)
            step = np.linalg.solve(a, b).reshape(len(roots), 3)
            if float(np.abs(step).max()) < MIN_ANGLE:
                break
            for k, r in enumerate(roots):
                turned = from_vector(step[k]) @ world[r][:3, :3]
                total = turned @ base[r][:3, :3].T
                if angle_of(total) > MAX_ANGLE:
                    turned = axis_angle(rotation_axis(total), MAX_ANGLE) @ base[r][:3, :3]
                world[r][:3, :3] = turned
                self._carry(world, r)


# --- clips ---------------------------------------------------------------------------------------------------------

def binding_dims(binding: Mapping[str, int]) -> int:
    """How many curves a generic binding takes (as ``model.binding_dims``)."""
    if binding["typeID"] == 4:
        return 4 if binding["attribute"] == 2 else 3 if binding["attribute"] in (1, 3, 4) else 1
    return 1


MUSCLE_FIXED = 2528  # bytes of a generic clip's muscle constant besides its arrays (checked on 5,882 official clips)


def muscle_size(tree: Mapping[str, Any]) -> int:
    """The muscle clip's byte size as the game's clips state it: its fixed part and 4 bytes per streamed word, dense
    sample and constant, 8 per value delta and 4 per reference pose value."""
    muscle = tree["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    return (MUSCLE_FIXED + 4 * (len(clip["m_StreamedClip"]["data"]) + len(clip["m_DenseClip"]["m_SampleArray"])
                                + len(clip["m_ConstantClip"]["data"]))
            + 8 * len(muscle["m_ValueArrayDelta"]) + 4 * len(muscle["m_ValueArrayReferencePose"]))


def dense_grid(tree: Mapping[str, Any]) -> np.ndarray | None:
    """The times of the clip's dense samples (its own grid when it has dense curves or a frame count, else the clip's
    rate from its start), or None for a clip this module does not edit (another layout, legacy, no time)."""
    grid = _grid(tree)
    return None if grid is None else grid[0] + np.arange(grid[2]) / grid[1]


def _grid(tree: Mapping[str, Any]) -> tuple[float, float, int] | None:
    """(begin, rate, frames) of :func:`dense_grid`."""
    if tree.get("m_Legacy") or "m_MuscleClip" not in tree:
        return None
    muscle = tree["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    streamed, dense, constant = clip["m_StreamedClip"], clip["m_DenseClip"], clip["m_ConstantClip"]
    curves = streamed["curveCount"] + dense["m_CurveCount"] + len(constant["data"])
    if tree.get("m_MuscleClipSize") != muscle_size(tree) or len(muscle["m_ValueArrayDelta"]) != curves:
        return None
    if len(dense["m_SampleArray"]) != dense["m_CurveCount"] * dense["m_FrameCount"]:
        return None
    # the new curves go between the dense and the constant ones: a binding must end there
    position, total, boundary = streamed["curveCount"] + dense["m_CurveCount"], 0, False
    for binding in tree["m_ClipBindingConstant"]["genericBindings"]:
        boundary |= total == position
        total += binding_dims(binding)
    boundary |= total == position
    if total != curves or not boundary:
        return None
    start, stop = float(muscle["m_StartTime"]), float(muscle["m_StopTime"])
    if dense["m_CurveCount"] or dense["m_FrameCount"] > 1:
        rate, begin, frames = float(dense["m_SampleRate"]), float(dense["m_BeginTime"]), int(dense["m_FrameCount"])
    else:
        rate, begin = float(tree.get("m_SampleRate") or 30.0), start
        frames = int(math.ceil((stop - start) * rate - 1e-3)) + 1
    if rate <= 0 or frames < 2 or not stop > start:
        return None
    return begin, rate, frames


def add_dense_curves(tree: dict, bindings: list[dict], samples: np.ndarray) -> None:
    """Append dense curves to a clip typetree: ``bindings`` (generic bindings, in curve order) and ``samples`` (frames
    of :func:`dense_grid` x curves). They go after the clip's streamed and dense curves, before its constant ones,
    with their value deltas (first and last sample) and the muscle clip's size."""
    grid = _grid(tree)
    frames, added = samples.shape
    if grid is None or grid[2] != frames:
        raise ValueError("clip layout not supported")
    if sum(binding_dims(b) for b in bindings) != added:
        raise ValueError("bindings and samples differ")
    muscle = tree["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    streamed, dense = clip["m_StreamedClip"], clip["m_DenseClip"]
    old = dense["m_CurveCount"]
    position = streamed["curveCount"] + old
    generic = tree["m_ClipBindingConstant"]["genericBindings"]
    index, total = 0, 0
    while index < len(generic) and total < position:
        total += binding_dims(generic[index])
        index += 1
    generic[index:index] = bindings
    previous = np.array(dense["m_SampleArray"], dtype=np.float32).reshape(frames, old)
    dense["m_SampleArray"] = np.concatenate([previous, samples.astype(np.float32)], axis=1).ravel().tolist()
    dense["m_BeginTime"], dense["m_SampleRate"], dense["m_FrameCount"] = grid
    dense["m_CurveCount"] = old + added
    first, last = samples[0].astype(np.float32), samples[-1].astype(np.float32)
    deltas = [{"m_Start": float(a), "m_Stop": float(b)} for a, b in zip(first, last)]
    muscle["m_ValueArrayDelta"][position:position] = deltas
    tree["m_MuscleClipSize"] = muscle_size(tree)


def rotation_binding(template: Mapping[str, Any], path: str) -> dict:
    """A generic binding of a Transform's local rotation (four curves) at ``path`` below the animated root."""
    binding = dict(template)
    binding.update(path=zlib.crc32(path.encode()), attribute=2, typeID=4, customType=0, isPPtrCurve=0)
    if "script" in binding:
        binding["script"] = {"m_FileID": 0, "m_PathID": 0}
    return binding
