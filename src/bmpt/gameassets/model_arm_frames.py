"""The frames of the arm pass: clip and body frames, the triangle index, depth queries and the clip gate."""

from __future__ import annotations

import hashlib
import threading
import zlib
from collections import OrderedDict

import numpy as np

from . import model_adapt as ma
from . import model_skirts as ms
from .model_arms import ARM_CAP, CAP, CLEAR, SAMPLES, SEGMENTS, Arms, push_of, radial


GATE_DEPTH = 0.020
GATE_FRACTION = 0.20
GATE_EVERY = 3
OWN_CACHE_BYTES = 24 * 1024 * 1024
_own_cache = OrderedDict()
_own_lock = threading.Lock()


def _repose(rig, poses, deltas, hold):
    """The poses with each arm turned to its corrected elbow and wrist, all frames at once."""
    if not hasattr(rig, "_arms"):
        rig._arms = Arms(rig)
    arms = rig._arms
    out = np.array(poses)
    if not len(out):
        return []
    source = out.copy()
    for s in range(2):
        rows = np.flatnonzero(~hold[:, s] & ~(np.abs(deltas[:, s]).max(axis=(1, 2)) < 1e-7))
        if not len(rows):
            continue
        u, e, wr = (source[rows, n, :3, 3] for n in rig.arm[s][1:])
        l1, l2 = np.linalg.norm(e - u, axis=1), np.linalg.norm(wr - e, axis=1)
        e2, w2 = e + deltas[rows, s, 0], wr + deltas[rows, s, 1]
        # The interpolated wrist is fixed; choose the nearest elbow on the
        # two bone spheres' circle. This keeps lengths without amplifying
        # the interpolated wrist's speed during the final rotation solve.
        reach = np.linalg.norm(w2 - u, axis=1)
        axis = (w2 - u) / np.maximum(reach, 1e-9)[:, None]
        reach = np.clip(reach, np.abs(l1 - l2) + 1e-8, l1 + l2 - 1e-8)
        w2 = u + reach[:, None] * axis
        along = (l1 * l1 - l2 * l2 + reach * reach) / (2 * reach)
        centre = u + along[:, None] * axis
        normal = e2 - centre - ((e2 - centre) * axis).sum(axis=1)[:, None] * axis
        flat = np.linalg.norm(normal, axis=1) < 1e-9
        fallback = e - centre - ((e - centre) * axis).sum(axis=1)[:, None] * axis
        normal[flat] = fallback[flat]
        normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-9)[:, None]
        height = l1 * l1 - along * along
        e2 = centre + np.sqrt(np.where(height > 0, height, 0.0))[:, None] * normal
        arms.repose(out, source, s, rows, e2, w2)
    return list(out)


def _depth_key(target, rig, clip):
    # Cache only numbers, never graphs or open bundle readers. Content hashes
    # invalidate both bindposes and curves when the official slot changes.
    digest = hashlib.sha256(clip.get_raw_data())
    digest.update(np.asarray(rig.rest).tobytes())
    for part in target.parts:
        digest.update(part.mesh.get_raw_data())
    return digest.digest()


def _own_depths(target, rig, own, clip, poses, frames, key=None, indices=None):
    key = _depth_key(target, rig, clip) if key is None else key
    with _own_lock:
        found = _own_cache.get(key, {})
        at = np.arange(9) if indices is None else np.asarray(indices)
        missing = [f for f in frames if f not in found or not np.isfinite(found[f][:, at]).all()]
        if not missing:
            _own_cache.move_to_end(key)
            return np.array([found[f] for f in frames])
    measured = {}
    values = depth_batch(own, rig, np.array([poses[f] for f in missing]), at)
    for f, depth in zip(missing, values):
        value = np.full((2, 9), np.nan)
        value[:, at] = depth
        measured[f] = value
    with _own_lock:
        found = _own_cache.setdefault(key, {})
        for f, value in measured.items():
            found[f] = np.where(np.isfinite(value), value, found.get(f, value))
        _own_cache.move_to_end(key)
        value = np.array([found[f] for f in frames])
        while sum(v.nbytes for cache in _own_cache.values() for v in cache.values()) > OWN_CACHE_BYTES:
            _own_cache.popitem(last=False)
    return value


def bounds_poses(skeleton, changes):
    """Sample corrected clips' start, midpoint, and end for renderer culling bounds.

    Args:
        skeleton: Slot hierarchy used to compose world transforms from edited clips.
        changes: Original clip keys mapped to ArmClip edits.
    """
    out = []
    for change in changes.values():
        times, pose, _ = skeleton.clip_frames(change.tree)
        out.extend(pose(f) for f in sorted({0, len(times) // 2, len(times) - 1}))
    return out


class ClipFrames:
    """Sample Transform curves in batches without decoding shader or material curves.

    This samples the same cubics and dense grid as ``model.decode_clip``, with
    its endpoint epsilon, and composes a frame batch one hierarchy level at a
    time. No graph or reader is retained in the host-depth cache.
    """

    def __init__(self, skeleton, tree, nodes=None):
        """Decode only Transform curves needed by selected nodes and their ancestors.

        Args:
            skeleton: Slot hierarchy supplying local rest transforms and binding paths.
            tree: AnimationClip typetree to sample on its own time grid.
            nodes: Optional node indices needed for a gate; None includes the entire hierarchy.
        """
        self.sk = skeleton
        self.nodes = set(range(len(skeleton.nodes))) if nodes is None else set(nodes)
        for n in list(self.nodes):
            while n is not None:
                self.nodes.add(n)
                n = skeleton.nodes[n].parent
        by_hash = {zlib.crc32(skeleton.nodes[i].path.encode()): i for i in self.nodes}
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        wanted, index = set(), 0
        for b in bindings:
            dims = ms.binding_dims(b)
            if b["typeID"] == 4 and b["path"] in by_hash and b["attribute"] in (1, 2, 3, 4):
                wanted.update(range(index, index + dims))
            index += dims
        from .model_arm_clips import sample_curves

        self.times, curves = sample_curves(tree, wanted)
        self.tracks = ma.bind_tracks(bindings, curves, by_hash)
        self.orders = ma.euler_orders(bindings, by_hash)
        self.local = np.array([ma.trs(*n.local) for n in skeleton.nodes])

    def world(self, frames):
        """Compose selected sample frames into world matrices, leaving unrequested nodes at local rest.

        Args:
            frames: Integer indices into this sampler's second-based times array.
        """
        frames = np.asarray(frames, int)
        local = np.repeat(self.local[None], len(frames), axis=0)
        for node, attrs in self.tracks.items():
            p, r, s = (np.repeat(np.array(v, float)[None], len(frames), axis=0) for v in self.sk.nodes[node].local)
            for attribute, parts in attrs.items():
                values = np.array([part[frames] for part in parts]).T
                if attribute == 1:
                    p = values
                elif attribute == 2:
                    r = values
                elif attribute == 3:
                    s = values
                elif attribute == 4:
                    r = np.array([ma.euler_to_quat(v, self.orders.get(node, ma.UNITY_ORDER)) for v in values])
            r /= np.maximum(np.linalg.norm(r, axis=1), 1e-30)[:, None]
            x, y, z, w = r.T
            m = local[:, node]
            m[:, 0, :3] = np.array([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)]).T
            m[:, 1, :3] = np.array([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)]).T
            m[:, 2, :3] = np.array([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]).T
            m[:, :3, :3] *= s[:, None]
            m[:, :3, 3] = p
        for i, node in enumerate(self.sk.nodes):
            if i in self.nodes and node.parent is not None:
                local[:, i] = local[:, node.parent] @ local[:, i]
        return local


GATE_SAMPLES = (3, 4, 5, 6, 7)


def near_core(drawn, rig, pose):
    """Check whether conservative torso bounds allow any driven sample to need over 20 mm.

    Args:
        drawn: Incoming costume whose core bounds are tested without posing all vertices.
        rig: Slot arm samples and body-axis provider.
        pose: Slot-node world matrices for the candidate frame.
    """
    points = rig.samples(pose)[:, GATE_SAMPLES].reshape(-1, 3)
    _, dirs, rho = radial(rig.axis(pose), points)
    lo, hi = drawn.core_bounds(pose)
    # D_new <= -20 mm implies need <= 20 mm because r_new <= ARM_CAP.
    ends = np.stack((points - (0.5 * ARM_CAP - GATE_DEPTH) * dirs, points + CAP * dirs))
    ray_lo, ray_hi = ends.min(axis=0), ends.max(axis=0)
    return bool(((ray_hi >= lo).all(axis=1) & (ray_lo <= hi).all(axis=1) | (rho < 0.02)).any())


def clip_gate(target, rig, own, new, clip, sampler, tick=lambda: None):
    """Gate clips with conservative bounds before casting exact candidate sleeve radii.

    A clip exits as soon as the fifth is reached or cannot be reached. Only
    forearm/wrist/palm rays participate; upper arm and tip samples belong
    to the final guard and cannot start an arm correction by themselves.

    Args:
        target: Slot costume identifying the revision-scoped original-depth cache.
        rig: Slot landmarks used for the five forearm/wrist/palm gate samples.
        own: Original costume skinning view supplying baseline depths.
        new: Fitted incoming costume supplying candidate depths and sleeve radii.
        clip: Slot AnimationClip reader identifying cached original measurements.
        sampler: ClipFrames sampler capable of composing gate-node poses.
        tick: No-argument yield/cancellation callback between candidate batches.

    Returns:
        Gate decision, sampled poses, original depths, and a lower-bound hit share;
        an early rejection returns empty maps and zero share.
    """
    frames = np.arange(0, len(sampler.times), GATE_EVERY)
    required = int(np.ceil(GATE_FRACTION * len(frames)))
    poses_batch = sampler.world(frames)
    possible = [f for f, w in zip(frames, poses_batch) if near_core(new, rig, w)]
    if len(possible) < required:
        return False, {}, {}, 0.0
    poses = {int(f): w for f, w in zip(frames, poses_batch)}
    own_values = {}
    key = _depth_key(target, rig, clip)
    hits = 0
    for begin in range(0, len(possible), 8):
        tick()
        batch = possible[begin : begin + 8]
        bodies = depth_batch(new, rig, np.array([poses[int(f)] for f in batch]), GATE_SAMPLES)
        candidates = [int(f) for f, body in zip(batch, bodies) if (body + 0.5 * ARM_CAP).max() > GATE_DEPTH]
        if candidates:
            values = _own_depths(target, rig, own, clip, poses, candidates, key, GATE_SAMPLES)
            own_values.update(zip(candidates, values))
        for offset, (f, body) in enumerate(zip(batch, bodies)):
            f = int(f)
            count = begin + offset
            if f in candidates:
                depth = own_values[f]
                upper = push_of(depth[:, GATE_SAMPLES], body, ARM_CAP, CLEAR)
                if upper.max() > GATE_DEPTH:
                    radius = arm_radii(new, rig, poses[f], GATE_SAMPLES)
                    if push_of(depth[:, GATE_SAMPLES], body, radius, CLEAR).max() > GATE_DEPTH:
                        hits += 1
            if hits >= required:
                # The remaining frames cannot change the gate decision. The
                # returned share is a lower bound, not a full-clip census.
                return True, poses, own_values, hits / len(frames)
            if hits + len(possible) - count - 1 < required:
                return False, {}, {}, 0.0
    return hits >= required, poses, own_values, hits / len(frames)


class BodyFrames:
    """Lazy torso poses: sparse trials do not skin intermediate frames."""

    def __init__(self, drawn, rig, poses, tick):
        """Prepare lazy torso skinning for a sequence of original clip poses.

        Args:
            drawn: Incoming costume view supplying compact torso-core vertices.
            rig: Slot landmarks used by each BodyFrame.
            poses: Full-frame world matrices indexed by requested sample frames.
            tick: No-argument yield/cancellation callback between eight-frame skinning batches.
        """
        self.drawn, self.rig, self.poses, self.tick = drawn, rig, poses, tick
        self.frames = {}
        self.rows = drawn.measure_mesh(True, False)[0]

    def __len__(self):
        """Return the number of clip poses, including frames not yet skinned."""
        return len(self.poses)

    def ensure(self, frames):
        """Prepare missing frame indices in bounded batches, sharing triangle geometry and leaf partitions."""
        missing = [f for f in frames if f not in self.frames]
        for begin in range(0, len(missing), 8):
            self.tick()
            batch = missing[begin : begin + 8]
            vertices = self.drawn.posed_batch([self.poses[f] for f in batch], self.rows)
            first = BodyFrame(self.drawn, self.rig, self.poses[batch[0]], vertices[0])
            self.frames[batch[0]] = first
            if len(batch) < 2:
                continue
            prepared = BodyFrame.prepare(vertices[1:, first.tris])
            index, leaf_lo, leaf_hi = leaf_bounds(self.drawn._body_index, prepared[3], prepared[4])
            for k, f in enumerate(batch[1:]):
                self.frames[f] = BodyFrame(
                    self.drawn,
                    self.rig,
                    self.poses[f],
                    vertices[k + 1],
                    [a[k] for a in prepared] + [(index, leaf_lo[k], leaf_hi[k])],
                )

    def __getitem__(self, frame):
        """Return the torso BodyFrame at a clip-frame index, preparing it on demand."""
        self.ensure([frame])
        return self.frames[frame]


class TriangleIndex:
    """Partition triangles into fixed spatial leaves with pose-dependent conservative bounds.

    Partition once per costume. A query expands overlapping leaves into sparse
    ray/triangle pairs, then tests the individual boxes and exact triangles.
    Bounds follow the posed triangles, so spine/arm turns never approximate a
    surface intersection or invalidate the partition.
    """

    def __init__(self, triangles):
        """Partition triangle corner positions into leaves of at most sixteen entries."""
        centers = np.asarray(triangles).mean(axis=1)
        leaves = []

        def split(rows):
            if len(rows) <= 16:
                leaves.append(rows)
                return
            axis = np.ptp(centers[rows], axis=0).argmax()
            rows = rows[np.argsort(centers[rows, axis], kind="stable")]
            middle = len(rows) // 2
            split(rows[:middle])
            split(rows[middle:])

        if len(centers):
            split(np.arange(len(centers)))
        self.rows = np.array([np.pad(rows, (0, 16 - len(rows)), mode="edge") for rows in leaves], int).reshape(-1, 16)
        self.leaf_of = np.empty(len(centers), int)
        self.leaf_of[self.rows.ravel()] = np.repeat(np.arange(len(self.rows)), 16)

    def bounds(self, prepared, previous=None, leaves=None):
        """Compute conservative leaf boxes, optionally updating only changed leaves.

        Args:
            prepared: BodyFrame.prepare arrays whose fourth/fifth entries are triangle lower/upper bounds.
            previous: Optional (index, lower, upper) tuple to copy before partial updates.
            leaves: Leaf indices to refresh when previous is supplied; unused for a full calculation.
        """
        lo, hi = prepared[3:5]
        if previous is not None:
            leaf_lo, leaf_hi = previous[1].copy(), previous[2].copy()
            rows = self.rows[leaves]
            leaf_lo[leaves], leaf_hi[leaves] = lo[rows].min(axis=1), hi[rows].max(axis=1)
            return self, leaf_lo, leaf_hi
        return self, lo[self.rows].min(axis=1), hi[self.rows].max(axis=1)


class BodyFrame:
    """A frame's torso triangles, shared by the rounds and final guard.

    Most torso vertices do not depend on either arm. Only the vertices with an
    arm influence and their triangles need re-skinning after an arm turn.
    Bounds and triangle edges are prepared once and queries visit the torso
    triangles overlapping the finite arm rays, without sleeve or leg casts.
    """

    def __init__(self, drawn, rig, pose, vertices=None, prepared=None):
        """Prepare torso intersections and track the triangles affected by arm turns.

        Args:
            drawn: Incoming costume view sharing torso layout and spatial partition caches.
            rig: Slot landmarks defining arm samples, descendants, and the body axis.
            pose: Original frame's slot-node world matrices.
            vertices: Optional already-skinned compact core vertices; None skins them from pose.
            prepared: Optional triangle arrays and leaf bounds; None derives them from vertices.
        """
        self.drawn, self.rig, self.pose = drawn, rig, pose
        self.rows, self.tris, _ = drawn.measure_mesh(True, False)
        self.vertices = drawn.posed(pose, self.rows) if vertices is None else vertices
        self.axis = rig.axis(pose)
        if not hasattr(drawn, "_body_layout"):
            if hasattr(drawn, "pose_bones"):
                affected = {n for a in rig.arm for n in [a[1], *rig.sk.descendants(a[1])]}
                joint_affected = np.array([n in affected for n in drawn.pose_bones])
                changed = np.flatnonzero(
                    (joint_affected[drawn.joints[self.rows]] & (drawn.weights[self.rows] > 0)).any(axis=1)
                )
            else:
                changed = np.arange(len(self.rows))
            drawn._body_layout = changed, np.flatnonzero(np.isin(self.tris, changed).any(axis=1))
        self.changed, self.tri_changed = drawn._body_layout
        if not hasattr(drawn, "_body_index"):
            drawn._body_index = TriangleIndex(self.vertices[self.tris])
        if prepared is None:
            prepared = self.prepare(self.vertices[self.tris])
            prepared.append(drawn._body_index.bounds(prepared))
        self.prepared = prepared
        self.leaves_changed = np.unique(drawn._body_index.leaf_of[self.tri_changed])
        self.baseline = None

    @staticmethod
    def prepare(triangles):
        """Prepare float32 origins, two edges, and bounds for triangle corners with optional frame dimensions."""
        t = np.asarray(triangles, np.float32)
        a, b, c = t[..., 0, :], t[..., 1, :], t[..., 2, :]
        return [a, b - a, c - a, np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)]

    def geometry(self, pose):
        """Return prepared torso geometry, re-skinning arm-influenced vertices for a changed world-matrix pose."""
        prepared = self.prepared
        if len(self.changed) and pose is not self.pose:
            vertices = self.vertices.copy()
            vertices[self.changed] = self.drawn.posed(pose, self.rows[self.changed])
            update = self.prepare(vertices[self.tris[self.tri_changed]])
            prepared = [a.copy() for a in self.prepared[:5]]
            for a, b in zip(prepared, update):
                a[self.tri_changed] = b
            prepared.append(self.drawn._body_index.bounds(prepared, self.prepared[5], self.leaves_changed))
        return prepared

    def depth(self, pose, indices=None):
        """Measure signed torso depth in metres at arm samples, caching the unchanged full baseline.

        Args:
            pose: Slot-node world matrices, optionally with corrected arm subtrees.
            indices: Optional sample indices from SAMPLES; None measures all nine.
        """
        if pose is self.pose and indices is None and self.baseline is not None:
            return self.baseline
        points = self.rig.samples(pose)
        if indices is not None:
            points = points[:, indices]
        flat = points.reshape(-1, 3)
        _, dirs, rho = radial(self.axis, flat)
        result = self.query(flat, dirs, rho, self.geometry(pose)).reshape(points.shape[:2])
        if pose is self.pose and indices is None:
            self.baseline = result
        return result

    def depth_gradient(self, pose):
        """Return signed depths (2 by 9) and XYZ sample-position gradients (2 by 9 by 3) for a world-matrix pose."""
        points = self.rig.samples(pose).reshape(-1, 3)
        q, dirs, rho = radial(self.axis, points)
        depth, normals = self.query(points, dirs, rho, self.geometry(pose), normals=True)
        a, ab = self.axis[:-1], np.diff(self.axis, axis=0)
        length2 = np.maximum((ab * ab).sum(axis=1), 1e-12)
        t = np.clip(((points[:, None] - a) * ab).sum(axis=2) / length2, 0, 1)
        distances = np.linalg.norm(points[:, None] - a - t[..., None] * ab, axis=2)
        nearest = distances.argmin(axis=1)
        along = ab[nearest] / np.sqrt(length2[nearest])[:, None]
        interior = (t[np.arange(len(points)), nearest] > 0) & (t[np.arange(len(points)), nearest] < 1)
        project = along[:, :, None] * along[:, None, :] * interior[:, None, None]
        pn = np.einsum("nij,nj->ni", project, normals)
        nd = (normals * dirs).sum(axis=1)
        tangent = normals - nd[:, None] * dirs
        jt = (tangent - np.einsum("nij,nj->ni", project, tangent)) / np.maximum(rho, 1e-9)[:, None]
        hit = np.linalg.norm(normals, axis=1) > 0
        grad = -dirs.copy()
        grad[hit] -= (pn[hit] + (depth[hit] + rho[hit])[:, None] * jt[hit]) / nd[hit, None]
        return depth.reshape(2, 9), grad.reshape(2, 9, 3)

    def penetration(self, pose):
        """Return positive torso depths in metres at all arm samples for a world-matrix pose."""
        # The guard only needs positive depth. Rays start at each arm sample,
        # omitting the axis-to-sample segment and all exterior-clearance hits.
        if pose is self.pose and self.baseline is not None:
            return np.maximum(0, self.baseline)
        points = self.rig.samples(pose).reshape(-1, 3)
        _, dirs, _ = radial(self.axis, points)
        return self.query(points, dirs, np.zeros(len(points)), self.geometry(pose)).reshape(2, 9)

    @staticmethod
    def query(points, dirs, rho, prepared, normals=False, high=CAP):
        """Cast finite outward sample rays through prepared torso triangles.

        Args:
            points: Arm sample positions shaped as rays by 3, in metres.
            dirs: Corresponding outward unit directions from the body axis.
            rho: Axis-to-sample distances, setting each ray's lower bound to -rho.
            prepared: Triangle origins, edges, bounds, and optional spatial-index leaf boxes.
            normals: Whether to return hit normals alongside signed depths.
            high: Maximum outward hit distance in metres.

        Returns:
            Farthest hit distances (or -rho on misses), optionally paired with hit normals.
        """
        v0, e1, e2, lo, hi = prepared[:5]
        if not len(v0):
            return (-rho, np.zeros_like(points)) if normals else -rho
        ends = np.stack((points - rho[:, None] * dirs, points + high * dirs))
        ray_lo, ray_hi = ends.min(axis=0) - 1e-7, ends.max(axis=0) + 1e-7
        # Keep ray/triangle pairs, rather than casting every ray against the
        # union of all their candidates. Opposite arms otherwise multiply the
        # narrow-phase work even though their rays visit different torso slabs.
        if len(prepared) > 5:
            index, leaf_lo, leaf_hi = prepared[5]
            ray, leaf = np.nonzero(
                (leaf_hi[None] >= ray_lo[:, None]).all(axis=2) & (leaf_lo[None] <= ray_hi[:, None]).all(axis=2)
            )
            ray = np.repeat(ray, index.rows.shape[1])
            at = index.rows[leaf].ravel()
            near = (hi[at] >= ray_lo[ray]).all(axis=1) & (lo[at] <= ray_hi[ray]).all(axis=1)
            ray, at = ray[near], at[near]
        else:
            ray, at = np.nonzero((hi[None] >= ray_lo[:, None]).all(axis=2) & (lo[None] <= ray_hi[:, None]).all(axis=2))
        return _narrow(points, dirs, rho, v0, e1, e2, ray, at, normals, high)


def _narrow(points, dirs, rho, v0, e1, e2, ray, at, normals, high):
    """Exact float32 line/triangle tests of the candidate pairs (ray, at); the outermost hit per ray."""
    all_e1, all_e2 = e1, e2
    v0, e1, e2 = v0[at], e1[at], e2[at]
    p, d = points.astype(np.float32)[ray], dirs.astype(np.float32)[ray]
    dx, dy, dz = d.T
    ax, ay, az = e1.T
    bx, by, bz = e2.T
    hx, hy, hz = dy * bz - dz * by, dz * bx - dx * bz, dx * by - dy * bx
    determinant = ax * hx + ay * hy + az * hz
    with np.errstate(divide="ignore", invalid="ignore"):
        f = 1.0 / determinant
        sx, sy, sz = (p - v0).T
        u = f * (sx * hx + sy * hy + sz * hz)
        qx, qy, qz = sy * az - sz * ay, sz * ax - sx * az, sx * ay - sy * ax
        v = f * (dx * qx + dy * qy + dz * qz)
        t = f * (bx * qx + by * qy + bz * qz)
        valid = (np.abs(determinant) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1)
    accepted = valid & (t > -rho[ray]) & (t <= high)
    result = -rho.copy()
    np.maximum.at(result, ray[accepted], t[accepted])
    if not normals:
        return result
    normal = np.zeros_like(points)
    # Preserve first-triangle tie breaking at shared edges, as in argmax.
    selected = np.full(len(points), len(all_e1), int)
    best = np.flatnonzero(accepted & (t == result[ray]))
    np.minimum.at(selected, ray[best], at[best])
    hit = selected < len(all_e1)
    if hit.any():
        normal[hit] = np.cross(all_e1[selected[hit]], all_e2[selected[hit]])
    return result, normal


def query_many(points, dirs, rho, prepared, normals=False, high=CAP):
    """Apply BodyFrame.query across frames with bit-identical per-frame depths and normals.

    A ray meets only its own frame's triangles, visited in the order the single-frame query visits them, with
    the same arithmetic: the depths and normals are bit for bit those of one frame at a time.

    Args:
        points: Frame-by-ray-by-3 arm sample positions in metres.
        dirs: Corresponding outward unit directions in the same shape as points.
        rho: Frame-by-ray axis distances setting lower ray bounds to -rho.
        prepared: Frame-by-triangle origins/edges/bounds plus one partition's frame-by-leaf boxes.
        normals: Whether to return hit normals with the depths.
        high: Maximum outward hit distance in metres.
    """
    count, rays = rho.shape
    triangles = prepared[0].shape[1]
    if not triangles:
        return (-rho, np.zeros_like(points)) if normals else -rho
    v0, e1, e2, lo, hi = (a.reshape(-1, 3) for a in prepared[:5])
    ends = np.stack((points - rho[..., None] * dirs, points + high * dirs))
    ray_lo, ray_hi = ends.min(axis=0) - 1e-7, ends.max(axis=0) + 1e-7
    index, leaf_lo, leaf_hi = prepared[5]
    # Box overlap one axis at a time: the same tests without three-wide reductions.
    overlap = leaf_hi[:, None, :, 0] >= ray_lo[:, :, None, 0]
    for k in range(3):
        if k:
            overlap &= leaf_hi[:, None, :, k] >= ray_lo[:, :, None, k]
        overlap &= leaf_lo[:, None, :, k] <= ray_hi[:, :, None, k]
    frame, ray, leaf = np.nonzero(overlap)
    ray = np.repeat(frame * rays + ray, index.rows.shape[1])
    at = (index.rows[leaf] + (frame * triangles)[:, None]).ravel()
    ray_lo, ray_hi = ray_lo.reshape(-1, 3)[ray], ray_hi.reshape(-1, 3)[ray]
    box_lo, box_hi = lo[at], hi[at]
    near = box_hi[:, 0] >= ray_lo[:, 0]
    for k in range(3):
        if k:
            near &= box_hi[:, k] >= ray_lo[:, k]
        near &= box_lo[:, k] <= ray_hi[:, k]
    out = _narrow(
        points.reshape(-1, 3), dirs.reshape(-1, 3), rho.ravel(), v0, e1, e2, ray[near], at[near], normals, high
    )
    if not normals:
        return out.reshape(count, rays)
    return out[0].reshape(count, rays), out[1].reshape(count, rays, 3)


def leaf_bounds(index, lo, hi):
    """Compute leaf boxes for every frame in a shared triangle partition.

    Args:
        index: TriangleIndex whose leaves identify the triangle groups.
        lo: Lower triangle bounds shaped as frames by triangles by 3.
        hi: Corresponding upper triangle bounds.
    """
    return index, lo[:, index.rows].min(axis=2), hi[:, index.rows].max(axis=2)


def radial_many(axes, points):
    """Compute nearest axis points, outward directions, and distances across frames.

    Args:
        axes: Body-axis polylines shaped as frames by joints by 3.
        points: Test positions shaped as frames by samples by 3, in the same metre-based space.
    """
    a, b = axes[:, :-1], axes[:, 1:]
    ab = b - a
    t = np.clip(
        ((points[:, :, None] - a[:, None]) * ab[:, None]).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12)[:, None], 0, 1
    )
    near = a[:, None] + t[..., None] * ab[:, None]
    dist = np.linalg.norm(points[:, :, None] - near, axis=-1)
    k = dist.argmin(-1)
    q = np.take_along_axis(near, k[..., None, None], axis=2)[:, :, 0]
    rho = np.take_along_axis(dist, k[..., None], axis=2)[..., 0]
    d = (points - q) / np.maximum(rho, 1e-9)[..., None]
    return q, d, rho


def _geometry_many(bodies, poses):
    """The frames' ``geometry`` stacked; arm-turned frames re-skin only their arm-influenced torso vertices."""
    first = bodies[0]
    stacked = [np.stack([b.prepared[k] for b in bodies]) for k in range(5)]
    index = first.drawn._body_index
    leaf_lo = np.stack([b.prepared[5][1] for b in bodies])
    leaf_hi = np.stack([b.prepared[5][2] for b in bodies])
    turned = np.array([k for k, (b, w) in enumerate(zip(bodies, poses)) if len(b.changed) and w is not b.pose], int)
    if len(turned):
        changed, tri_changed = first.changed, first.tri_changed
        vertices = np.stack([bodies[k].vertices for k in turned])
        vertices[:, changed] = first.drawn.posed_batch([poses[k] for k in turned], first.rows[changed])
        update = BodyFrame.prepare(vertices[:, first.tris[tri_changed]])
        for a, b in zip(stacked, update):
            a[turned[:, None], tri_changed[None]] = b
        rows = index.rows[first.leaves_changed]
        leaf_lo[turned[:, None], first.leaves_changed[None]] = stacked[3][turned[:, None, None], rows[None]].min(axis=2)
        leaf_hi[turned[:, None], first.leaves_changed[None]] = stacked[4][turned[:, None, None], rows[None]].max(axis=2)
    return stacked + [(index, leaf_lo, leaf_hi)]


def _samples(rig, poses):
    return rig.samples_many(poses)


def depths(bodies, poses):
    """Measure BodyFrame.depth for frame/pose pairs together, returning frames by 2 by 9 depths.

    Args:
        bodies: Prepared torso frames sharing one costume layout and spatial partition.
        poses: Corresponding slot-node world matrices, optionally with corrected arms.
    """
    out = np.empty((len(bodies), 2, len(SAMPLES)))
    todo = []
    for k, (b, w) in enumerate(zip(bodies, poses)):
        if w is b.pose and b.baseline is not None:
            out[k] = b.baseline
        else:
            todo.append(k)
    if todo:
        bodies_, poses_ = [bodies[k] for k in todo], [poses[k] for k in todo]
        points = _samples(bodies_[0].rig, poses_).reshape(len(todo), -1, 3)
        _, dirs, rho = radial_many(np.array([b.axis for b in bodies_]), points)
        values = query_many(points, dirs, rho, _geometry_many(bodies_, poses_)).reshape(len(todo), 2, -1)
        for k, b, w, value in zip(todo, bodies_, poses_, values):
            out[k] = value
            if w is b.pose:
                b.baseline = value
    return out


def penetrations(bodies, poses):
    """Measure positive torso depths for frame/pose pairs, returning frames by 2 by 9 values.

    Args:
        bodies: Prepared torso frames sharing one costume layout and spatial partition.
        poses: Corresponding slot-node world matrices checked by the rollback guard.
    """
    out = np.empty((len(bodies), 2, len(SAMPLES)))
    todo = []
    for k, (b, w) in enumerate(zip(bodies, poses)):
        if w is b.pose and b.baseline is not None:
            out[k] = np.maximum(0, b.baseline)
        else:
            todo.append(k)
    if todo:
        bodies_, poses_ = [bodies[k] for k in todo], [poses[k] for k in todo]
        points = _samples(bodies_[0].rig, poses_).reshape(len(todo), -1, 3)
        _, dirs, _ = radial_many(np.array([b.axis for b in bodies_]), points)
        out[todo] = query_many(points, dirs, np.zeros(points.shape[:2]), _geometry_many(bodies_, poses_)).reshape(
            len(todo), 2, -1
        )
    return out


def depth_gradients(bodies, poses):
    """Measure batched signed depths (n by 2 by 9) and XYZ gradients (n by 2 by 9 by 3).

    Args:
        bodies: Prepared torso frames sharing one costume layout and spatial partition.
        poses: Corresponding slot-node world matrices used to locate arm samples and changed torso triangles.
    """
    count = len(bodies)
    if not count:
        return np.empty((0, 2, len(SAMPLES))), np.empty((0, 2, len(SAMPLES), 3))
    points = _samples(bodies[0].rig, poses).reshape(count, -1, 3)
    axes = np.array([b.axis for b in bodies])
    _, dirs, rho = radial_many(axes, points)
    depth, normals = query_many(points, dirs, rho, _geometry_many(bodies, poses), normals=True)
    a, ab = axes[:, :-1], np.diff(axes, axis=1)
    length2 = np.maximum((ab * ab).sum(axis=2), 1e-12)
    t = np.clip(((points[:, :, None] - a[:, None]) * ab[:, None]).sum(axis=3) / length2[:, None], 0, 1)
    distances = np.linalg.norm(points[:, :, None] - a[:, None] - t[..., None] * ab[:, None], axis=3)
    nearest = distances.argmin(axis=2)
    along = (
        np.take_along_axis(ab, nearest[..., None], axis=1)
        / np.sqrt(np.take_along_axis(length2, nearest, axis=1))[..., None]
    ).reshape(-1, 3)
    at = np.take_along_axis(t, nearest[..., None], axis=2).ravel()
    interior = (at > 0) & (at < 1)
    points, dirs, rho = points.reshape(-1, 3), dirs.reshape(-1, 3), rho.ravel()
    depth, normals = depth.ravel(), normals.reshape(-1, 3)
    project = along[:, :, None] * along[:, None, :] * interior[:, None, None]
    pn = np.einsum("nij,nj->ni", project, normals)
    nd = (normals * dirs).sum(axis=1)
    tangent = normals - nd[:, None] * dirs
    jt = (tangent - np.einsum("nij,nj->ni", project, tangent)) / np.maximum(rho, 1e-9)[:, None]
    hit = np.linalg.norm(normals, axis=1) > 0
    grad = -dirs.copy()
    grad[hit] -= (pn[hit] + (depth[hit] + rho[hit])[:, None] * jt[hit]) / nd[hit, None]
    return depth.reshape(count, 2, -1), grad.reshape(count, 2, -1, 3)


def arm_radii(drawn, rig, pose, indices=None):
    """Cast exact sleeve radii without skinning or casting the torso.

    Args:
        drawn: Costume view with side/segment arm triangle partitions.
        rig: Slot sample positions and body-axis provider.
        pose: Slot-node world matrices for this frame.
        indices: Optional sample indices; None measures all nine per arm.
    """
    indices = np.arange(9) if indices is None else np.asarray(indices)
    points = rig.samples(pose)[:, indices]
    _, directions, _ = radial(rig.axis(pose), points.reshape(-1, 3))
    directions = directions.reshape(points.shape)
    if not hasattr(drawn, "_arm_views"):
        drawn._arm_views = {}
    radii = np.zeros(points.shape[:2])
    for side in range(2):
        for segment in range(3):
            at = np.array([k for k, sample in enumerate(indices) if segment in SEGMENTS[sample]], int)
            triangles = drawn.arm[side, segment]
            if not len(at) or not len(triangles):
                continue
            key = side, segment
            if key not in drawn._arm_views:
                rows, remap = np.unique(triangles, return_inverse=True)
                local = remap.reshape(-1, 3)
                index = TriangleIndex(drawn.posed(pose, rows)[local])
                drawn._arm_views[key] = rows, local, index
            rows, local, index = drawn._arm_views[key]
            prepared = BodyFrame.prepare(drawn.posed(pose, rows)[local])
            prepared.append(index.bounds(prepared))
            values = BodyFrame.query(points[side, at], directions[side, at], np.zeros(len(at)), prepared, high=ARM_CAP)
            radii[side, at] = np.maximum(radii[side, at], values)
    return radii


def arm_radii_many(drawn, rig, poses, indices=None):
    """Cast each sleeve segment across frames, returning frames by 2 by samples radii in metres.

    Args:
        drawn: Costume view sharing sleeve partitions and sparse skinning views across frames.
        rig: Slot arm samples and body-axis provider.
        poses: Frame-by-node world matrices; an empty sequence yields an empty radius array.
        indices: Optional sample indices; None measures all nine per arm.
    """
    indices = np.arange(9) if indices is None else np.asarray(indices)
    radii = np.zeros((len(poses), 2, len(indices)))
    if not len(poses):
        return radii
    points = _samples(rig, poses)[:, :, indices]
    _, directions, _ = radial_many(np.array([rig.axis(w) for w in poses]), points.reshape(len(poses), -1, 3))
    directions = directions.reshape(points.shape)
    if not hasattr(drawn, "_arm_views"):
        drawn._arm_views = {}
    for side in range(2):
        for segment in range(3):
            at = np.array([k for k, sample in enumerate(indices) if segment in SEGMENTS[sample]], int)
            triangles = drawn.arm[side, segment]
            if not len(at) or not len(triangles):
                continue
            key = side, segment
            if key not in drawn._arm_views:
                rows, remap = np.unique(triangles, return_inverse=True)
                local = remap.reshape(-1, 3)
                index = TriangleIndex(drawn.posed(poses[0], rows)[local])
                drawn._arm_views[key] = rows, local, index
            rows, local, index = drawn._arm_views[key]
            prepared = BodyFrame.prepare(drawn.posed_batch(poses, rows)[:, local])
            prepared.append(leaf_bounds(index, prepared[3], prepared[4]))
            values = query_many(
                points[:, side, at], directions[:, side, at], np.zeros((len(poses), len(at))), prepared, high=ARM_CAP
            )
            radii[:, side, at] = np.maximum(radii[:, side, at], values)
    return radii


def repair_clearance(rig, poses, deltas, hold, pins, bodies, tick=lambda: None):
    """Apply small shared bone turns to resolve non-driven sample constraints.

    Corrections are rotations of the actual bone directions. Projecting an XYZ
    elbow offset onto its reach circle can undo clearance repair.
    Shared turns preserve smoothness; contacts and unreachable cases are left
    to the per-frame rollback guard. Repair displacement is capped at 30 mm.

    Args:
        rig: Slot arm landmarks and bone lengths.
        poses: Original world-matrix frames used as the clearance baseline.
        deltas: Elbow/wrist XYZ offsets, updated in place during repair attempts.
        hold: Frame-by-arm grip flags; any hold bypasses repair.
        pins: Head-contact strengths; strengths above 0.01 bypass repair.
        bodies: Prepared torso frames corresponding to poses.
        tick: No-argument yield/cancellation callback between repair rounds.
    """
    if hold.any() or (pins > 0.01).any():
        return deltas
    baseline = np.maximum(0, depths(bodies, poses))
    original = deltas.copy()
    ew = np.array([0.5, 0.75, 1, 0.75, 0.5, 0.25, 0, 0, 0])
    ww = np.array([0, 0, 0, 0.25, 0.5, 0.75, 1, 1, 1])
    joints0 = np.array([[rig.joints(w, side) for side in range(2)] for w in poses])
    for _ in range(8):
        tick()
        fixed = _repose(rig, poses, deltas, hold)
        depth, gradient = depth_gradients(bodies, fixed)
        if (np.maximum(depth, 0) - baseline).max() < 0.0007:
            return deltas
        joints = np.array([[rig.joints(w, side) for side in range(2)] for w in fixed])
        upper = joints[:, :, 1] - joints[:, :, 0]
        fore = joints[:, :, 2] - joints[:, :, 1]
        lengths = np.stack((np.linalg.norm(upper, axis=2), np.linalg.norm(fore, axis=2)), axis=2).mean(axis=0)
        lever_u = ew[None, None, :, None] * upper[:, :, None] + ww[None, None, :, None] * (upper + fore)[:, :, None]
        lever_f = ww[None, None, :, None] * fore[:, :, None]
        coefficients = np.concatenate(
            (
                np.cross(gradient, lever_u) / lengths[None, :, 0, None, None],
                np.cross(gradient, lever_f) / lengths[None, :, 1, None, None],
            ),
            axis=3,
        )
        rhs = depth - baseline + 0.0003
        for side in range(2):
            a = coefficients[:, side].reshape(-1, 6)
            target = rhs[:, side].ravel()
            shift = np.zeros(6)
            for step in range(160):
                violation = target - a @ shift
                k = violation.argmax()
                if violation[k] <= 1e-5:
                    break
                shift += violation[k] / max(a[k] @ a[k], 1e-9) * a[k]
                shift *= 0.999
            turns = shift.reshape(2, 3) / lengths[side, :, None]
            matrices = []
            for turn in turns:
                angle = np.linalg.norm(turn)
                angle = min(angle, 0.1)
                axis = turn / max(np.linalg.norm(turn), 1e-9)
                x, y, z = axis
                k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
                matrices.append(np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * k @ k)
            e2 = joints[:, side, 0] + upper[:, side] @ matrices[0].T
            w2 = e2 + fore[:, side] @ matrices[0].T @ matrices[1].T
            deltas[:, side, 0] = e2 - joints0[:, side, 1]
            deltas[:, side, 1] = w2 - joints0[:, side, 2]
        adjustment = deltas - original
        amount = np.linalg.norm(adjustment, axis=3).max(initial=0)
        if amount > 0.03:
            deltas = original + adjustment * (0.03 / amount)
    return deltas


def depth_batch(drawn, rig, poses, indices):
    """Batch gate-depth skinning and triangle preparation, typically over eight frames.

    Only core vertices are read. Ray/triangle work is still culled separately
    for each frame, so a wider batch never widens the torso casting region.

    Args:
        drawn: Incoming costume view whose rigid torso core supplies cover.
        rig: Slot samples and body-axis provider.
        poses: Frame-by-node world matrices for a bounded gate batch.
        indices: Gate sample indices to measure for both arms.

    Returns:
        Signed depths in metres shaped as frames by 2 by selected samples.
    """
    if not len(poses):
        return np.empty((0, 2, len(indices)))
    if not hasattr(drawn, "bindings"):
        return np.array([BodyFrame(drawn, rig, w).depth(w, indices) for w in poses])
    rows, triangles, _ = drawn.measure_mesh(True, False)
    matrices = (poses[:, drawn.pose_bones] @ drawn.bindings).astype(np.float32)
    joints, weights = drawn.joints[rows], drawn.weights[rows].astype(np.float32)
    h = drawn.h[rows].astype(np.float32)
    vertices = np.zeros((len(poses), len(rows), 3), np.float32)
    for k in range(joints.shape[1]):
        used = weights[:, k] != 0
        if used.any():
            vertices[:, used] += (
                weights[None, used, k : k + 1]
                * np.einsum("fvij,vj->fvi", matrices[:, joints[used, k]], h[used])[..., :3]
            )
    tri = vertices[:, triangles]
    a, b, c = tri[:, :, 0], tri[:, :, 1], tri[:, :, 2]
    prepared = [a, b - a, c - a, np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)]
    if not hasattr(drawn, "_body_index"):
        drawn._body_index = TriangleIndex(tri[0])
    points = _samples(rig, poses)[:, :, indices].reshape(len(poses), -1, 3)
    _, direction, rho = radial_many(np.array([rig.axis(pose) for pose in poses]), points)
    prepared.append(leaf_bounds(drawn._body_index, prepared[3], prepared[4]))
    return query_many(points, direction, rho, prepared).reshape(len(poses), 2, len(indices))
