"""Contact-aware arm clearance for a costume bound to another slot.

All distances are metres. Only the torso core counts as cover; contacts and the
clip gate keep intentional poses, secondary cloth and small differences intact.

This module holds the drawn arms, the rig and their measures; the frames and depth queries are in
``model_arm_frames``, the solve and ``retarget`` in ``model_arm_solve``.
"""

from __future__ import annotations

import re

import numpy as np

from . import model_adapt as ma


ARM = re.compile(r"^Bip001 ([LR]) (UpperArm|Forearm|Hand|Finger\d+)$")
CAP = 0.15  # a body surface farther out than this from the bone does not count (wings, a slung bag)
ARM_CAP = 0.08  # the arm's own surface is looked for this far out
SAMPLES = ("U50", "U75", "E", "F25", "F50", "F75", "W", "P", "T")
SEGMENTS = ((0,), (0,), (0, 1), (1,), (1,), (1,), (1, 2), (2,), (2,))  # arm segments whose surface is the sample's own
LOWER = slice(2, 9)  # elbow to fingertips


def chain_name(sk, n):
    """Find the nearest Biped-chain ancestor's name, or an empty string without one.

    Args:
        sk: Model hierarchy supplying node names and parents.
        n: Starting skeleton node index; None has no ancestor.
    """
    while n is not None and not ma.BIPED_CHAIN.match(sk.nodes[n].name):
        n = sk.nodes[n].parent
    return sk.nodes[n].name if n is not None else ""


def region(sk, n):
    """Classify a node's Biped region: body 0, head 1, or arm 2 + 3 * side + segment.

    Args:
        sk: Skeleton used to find the nearest Biped-chain ancestor.
        n: Node index whose dominant influence is classified; None belongs to the body.

    Returns:
        Region code with left/right sides 0/1 and upper-arm/forearm/hand-finger segments 0/1/2.
    """
    name = chain_name(sk, n)
    m = ARM.match(name)
    if m:
        seg = 0 if m.group(2) == "UpperArm" else 1 if m.group(2) == "Forearm" else 2
        return 2 + 3 * "LR".index(m.group(1)) + seg
    return 1 if name == "Bip001 Head" else 0


class Drawn:
    """A costume on a skeleton: posed vertices by frame, triangles, and each vertex's region."""

    def __init__(self, vertices, joints, weights, triangles, regions, matrices, loose=None):
        """Prepare costume skinning and region-specific triangle views.

        Args:
            vertices: Rest positions in metres, shaped as vertices by 3.
            joints: Matrix-column indices for each vertex influence.
            weights: Influence weights in the same shape as joints.
            triangles: Triples indexing vertices across costume parts.
            regions: Per-vertex body/head/arm codes from region, based on dominant influences.
            matrices: Pose callback returning skin matrices for the influence columns.
            loose: Per-vertex secondary-bone flags excluded from torso core; None marks none loose.
        """
        self.h = np.c_[vertices, np.ones(len(vertices))]
        self.loose = np.zeros(len(vertices), bool) if loose is None else loose
        self.joints, self.weights, self.triangles, self.regions, self.matrices = (
            joints,
            weights,
            triangles,
            regions,
            matrices,
        )
        tri_regions = regions[triangles]
        same = (tri_regions[:, 0] == tri_regions[:, 1]) & (tri_regions[:, 1] == tri_regions[:, 2])
        self.tri_region = np.where(same, tri_regions[:, 0], -1)
        self.body = triangles[self.tri_region == 0]
        # the body's core: triangles no vertex of which a secondary bone dominates (cloth chains, capes, ribbons,
        # skirts, bags: every bone that is not a Bip001 bone)
        self.body_core = ~self.loose[self.body].any(axis=1)
        self.head = triangles[self.tri_region == 1]
        self.arm = {(s, seg): triangles[self.tri_region == 2 + 3 * s + seg] for s in range(2) for seg in range(3)}
        self._views = {}
        self._bound = None

    def posed(self, pose, rows=None):
        """Skin a costume or selected vertices under one skeleton pose.

        Args:
            pose: Node world matrices consumed by the skin-matrix callback or prepared bindings.
            rows: Optional vertex indices; None skins the full mesh.
        """
        m = (
            pose[self.pose_bones] @ self.bindings
            if hasattr(self, "bindings") and isinstance(pose, np.ndarray)
            else self.matrices(pose)
        )
        h, joints, weights = self.h, self.joints, self.weights
        if rows is not None:
            h, joints, weights = h[rows], joints[rows], weights[rows]
        out = np.zeros((len(h), 3))
        for k in range(self.joints.shape[1]):
            used = weights[:, k] != 0
            if used.all():
                out += weights[:, k : k + 1] * np.einsum("nij,nj->ni", m[joints[:, k]], h)[:, :3]
            elif used.any():
                out[used] += weights[used, k : k + 1] * np.einsum("nij,nj->ni", m[joints[used, k]], h[used])[:, :3]
        return out

    def posed_batch(self, poses, rows):
        """Skin selected rows across a frame batch using shared sparse influence gathers.

        Args:
            poses: Frame-by-node world matrices.
            rows: Vertex indices to return in each frame, in their requested order.
        """
        if not hasattr(self, "bindings"):
            return np.array([self.posed(w, rows) for w in poses])
        matrices = np.asarray(poses)[:, self.pose_bones] @ self.bindings
        joints, weights, h = self.joints[rows], self.weights[rows], self.h[rows]
        vertices = np.zeros((len(poses), len(rows), 3))
        for k in range(joints.shape[1]):
            used = weights[:, k] != 0
            if used.any():
                vertices[:, used] += (
                    weights[None, used, k : k + 1]
                    * np.einsum("fvij,vj->fvi", matrices[:, joints[used, k]], h[used])[..., :3]
                )
        return vertices

    def measure_mesh(self, core, arms):
        """Build compact torso/arm triangle views excluding hair, face, and unused vertices.

        Args:
            core: Whether torso triangles dominated by secondary bones are excluded.
            arms: Whether arm vertices are included in the compact vertex set.

        Returns:
            Original vertex rows, remapped torso triangles, and arm triangles by side/segment.
        """
        key = core, arms
        if key not in self._views:
            body = self.body[self.body_core] if core else self.body
            triangles = [body, *self.arm.values()] if arms else [body]
            rows = np.unique(np.concatenate(triangles).ravel())
            index = np.zeros(len(self.h), int)
            index[rows] = np.arange(len(rows))
            self._views[key] = rows, index[body], {k: index[t] for k, t in self.arm.items()}
        return self._views[key]

    def core_bounds(self, pose):
        """Conservative bounds from each influencing bone's vertex box.

        Skinning is a weighted sum of these transformed boxes. Including the
        measured weight-sum range also covers quantized, unnormalized weights.
        No posed vertices or triangle intersections are needed for this test.

        Args:
            pose: Slot-node world matrices used to transform influencing bones' vertex boxes.

        Returns:
            Conservative lower and upper torso-core bounds in metres.
        """
        if self._bound is None:
            rows = self.measure_mesh(True, False)[0]
            joints, corners = [], []
            for joint in np.unique(self.joints[rows][self.weights[rows] > 0]):
                hit = ((self.joints[rows] == joint) & (self.weights[rows] > 0)).any(axis=1)
                lo, hi = self.h[rows[hit], :3].min(axis=0), self.h[rows[hit], :3].max(axis=0)
                corners.append([[hi[k] if bits & (1 << k) else lo[k] for k in range(3)] + [1.0] for bits in range(8)])
                joints.append(joint)
            sums = self.weights[rows].sum(axis=1)
            self._bound = np.array(joints), np.array(corners), np.array([sums.min(initial=1), sums.max(initial=1)])
        joints, corners, sums = self._bound
        if not len(joints):
            return np.zeros(3), np.zeros(3)
        world = np.einsum("nij,nkj->nki", self.matrices(pose)[joints], corners)[..., :3]
        extremes = np.array([world.min(axis=(0, 1)), world.max(axis=(0, 1))])
        scaled = extremes[:, None] * sums[None, :, None]
        return scaled.min(axis=(0, 1)) - 1e-6, scaled.max(axis=(0, 1)) + 1e-6


def own_drawn(costume):
    """Build a Drawn view of a costume using its skeleton, part skins, and original bindposes."""
    sk = costume.skeleton
    g = sk.graph
    verts, joints, weights, tris, regs, bones_all, bps, off_v, off_m = [], [], [], [], [], [], [], 0, 0
    loose = []
    cache = {}
    for part, skin in zip(costume.parts, costume.skins):
        bp = ma.bindposes(g, part.mesh)
        bones = [b if b is not None else 0 for b in part.bones]
        j = np.minimum(skin.joints, len(bones) - 1)
        dom = np.array(bones)[j[np.arange(len(j)), skin.weights.argmax(1)]]
        for n in np.unique(dom):
            if n not in cache:
                cache[n] = region(sk, n)
        regs.append(np.array([cache[n] for n in dom]))
        loose.append(np.array([not sk.nodes[n].name.startswith("Bip001") for n in dom]))
        verts.append(skin.vertices)
        joints.append(j + off_m)
        weights.append(skin.weights)
        tris.append((skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)) + off_v)
        bones_all += bones
        bps.append(bp[: len(bones)])
        off_v += len(skin.vertices)
        off_m += len(bones)
    bp_all = np.concatenate(bps)
    out = Drawn(
        np.vstack(verts),
        np.vstack(joints),
        np.vstack(weights),
        np.vstack(tris),
        np.concatenate(regs),
        lambda pose: np.array([pose[b] for b in bones_all]) @ bp_all,
        np.concatenate(loose),
    )
    out.pose_bones = np.array(bones_all)
    out.bindings = bp_all
    return out


def adapted_drawn(target, source, plan):
    """Build the source costume as bound to the slot before hem/stiffening weight adjustments.

    Args:
        target: Slot costume supplying destination rest matrices and skeleton nodes.
        source: Incoming costume supplying topology, influences, and body-region labels.
        plan: Adaptation mapping and fitted vertex positions defining the bound shape.
    """
    ss, ts = source.skeleton, target.skeleton
    flat = ma.flatten(source, ss.world())
    sj, sw = flat.joints, flat.weights
    nodes = sorted(set(sj[sw > 0].tolist()))
    index = {n: k for k, n in enumerate(nodes)}
    joints = np.vectorize(lambda n: index.get(n, 0))(np.where(sj < 0, nodes[0], sj))
    t_rest = ts.world()
    mapped = [plan.mapping[n] for n in nodes]
    inverse = np.array([np.linalg.inv(t_rest[t]) for t in mapped])
    dom = sj[np.arange(len(sj)), sw.argmax(1)]
    cache = {n: region(ss, n) for n in np.unique(dom)}
    regions = np.array([cache[n] for n in dom])
    loose = np.array([not ss.nodes[n].name.startswith("Bip001") for n in dom])
    out = Drawn(
        plan.fit.vertices,
        joints,
        sw,
        flat.triangles,
        regions,
        lambda pose: np.array([pose[t] for t in mapped]) @ inverse,
        loose,
    )
    out.pose_bones = np.array(mapped)
    out.bindings = inverse
    return out


class Rig:
    """The slot's arms and body axis."""

    def __init__(self, sk):
        """Find a slot skeleton's arm chains, spine, feet, weapon mount, and hand sample landmarks."""
        f = sk.first
        self.sk = sk
        self.arm = [[f(f"Bip001 {s} {b}") for b in ("Clavicle", "UpperArm", "Forearm", "Hand")] for s in "LR"]
        self.ok = all(n is not None for a in self.arm for n in a[1:])
        spine = (
            [f("Bip001 Pelvis")]
            + [f(f"Bip001 Spine{k}") for k in ("", "1", "2", "3")]
            + [f("Bip001 Neck"), f("Bip001 Head")]
        )
        self.spine = [n for n in spine if n is not None]
        self.feet = [n for n in (f("Bip001 L Foot"), f("Bip001 R Foot")) if n is not None]
        self.weapon = f("Bip001_Weapon")
        self.head = f("Bip001 Head")
        rest = sk.world()
        self.rest = rest
        self.palm, self.tip = [], []
        if not self.ok:
            return
        for a in self.arm:
            hand = a[3]
            inv = np.linalg.inv(rest[hand])
            kids = [
                i
                for i, n in enumerate(sk.nodes)
                if n.parent == hand and n.name.startswith("Bip001 ") and "Finger" in n.name
            ]
            below = [
                i
                for i in sk.descendants(hand)
                if sk.nodes[i].name.startswith("Bip001 ") and "Finger" in sk.nodes[i].name
            ]
            leaves = [i for i in below if not any(sk.nodes[j].parent == i for j in below)]
            fore = rest[hand][:3, 3] - rest[a[2]][:3, 3]
            fallback = rest[hand][:3, 3] + 0.3 * fore
            palm = np.mean([rest[i][:3, 3] for i in kids], axis=0) if kids else fallback
            tip = np.mean([rest[i][:3, 3] for i in leaves], axis=0) if leaves else rest[hand][:3, 3] + 0.6 * fore
            self.palm.append(inv @ np.r_[palm, 1.0])
            self.tip.append(inv @ np.r_[tip, 1.0])

    def joints(self, pose, s):
        """Return upper-arm, elbow, and wrist positions for side 0 (left) or 1 (right) in a world-matrix pose."""
        return [pose[n][:3, 3] for n in self.arm[s][1:]]

    def samples(self, pose):
        """Sample a world-matrix pose as 2 by 9 by 3 arm positions: U50, U75, E, F25, F50, F75, W, P, T."""
        out = np.zeros((2, len(SAMPLES), 3))
        for s in range(2):
            u, e, w = self.joints(pose, s)
            hand = pose[self.arm[s][3]]
            out[s] = [
                u + 0.5 * (e - u),
                u + 0.75 * (e - u),
                e,
                e + 0.25 * (w - e),
                e + 0.5 * (w - e),
                e + 0.75 * (w - e),
                w,
                (hand @ self.palm[s])[:3],
                (hand @ self.tip[s])[:3],
            ]
        return out

    def samples_many(self, poses):
        """Sample frame-by-node world matrices as frames by 2 by 9 by 3 using samples' per-frame arithmetic."""
        poses = np.asarray(poses)
        out = np.zeros((len(poses), 2, len(SAMPLES), 3))
        for s in range(2):
            u, e, w = (poses[:, n, :3, 3] for n in self.arm[s][1:])
            hand = poses[:, self.arm[s][3]]
            for k, value in enumerate(
                (
                    u + 0.5 * (e - u),
                    u + 0.75 * (e - u),
                    e,
                    e + 0.25 * (w - e),
                    e + 0.5 * (w - e),
                    e + 0.75 * (w - e),
                    w,
                    (hand @ self.palm[s])[:, :3],
                    (hand @ self.tip[s])[:, :3],
                )
            ):
                out[:, s, k] = value
        return out

    def axis(self, pose):
        """Return the posed spine-to-head axis polyline, prepending the feet's center when available."""
        pts = [pose[n][:3, 3] for n in self.spine]
        if self.feet:
            pts = [np.mean([pose[n][:3, 3] for n in self.feet], axis=0)] + pts
        return np.array(pts)


def radial(axis, points):
    """Find each point's closest axis position, outward unit direction, and distance.

    Args:
        axis: Ordered 3D body-axis polyline with at least two joints.
        points: Test positions in the same metre-based coordinate space.
    """
    a, b = axis[:-1], axis[1:]
    ab = b - a
    t = np.clip(((points[:, None] - a[None]) * ab[None]).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12)[None], 0, 1)
    near = a[None] + t[..., None] * ab[None]
    dist = np.linalg.norm(points[:, None] - near, axis=-1)
    k = dist.argmin(1)
    q = near[np.arange(len(points)), k]
    rho = dist[np.arange(len(points)), k]
    d = (points - q) / np.maximum(rho, 1e-9)[:, None]
    return q, d, rho


def cast(orig, dirs, tri, low=None):
    """Intersect lines with culled triangles, returning ray-by-triangle t values and NaN misses.

    Args:
        orig: Ray origins shaped as rays by 3, in metres.
        dirs: Corresponding unit ray directions for p + t * d.
        tri: Triangle corner positions shaped as triangles by 3 by 3.
        low: Optional per-ray lower t bounds for broad-phase culling against the CAP upper bound.
    """
    if not len(tri):
        return np.full((len(orig), 0), np.nan, np.float32)
    if low is not None:
        ends = np.stack((orig + np.asarray(low)[:, None] * dirs, orig + CAP * dirs))
        lo, hi = ends.min(axis=(0, 1)) - 1e-7, ends.max(axis=(0, 1)) + 1e-7
        tri = tri[(tri.max(axis=1) >= lo).all(axis=1) & (tri.min(axis=1) <= hi).all(axis=1)]
        if not len(tri):
            return np.full((len(orig), 0), np.nan, np.float32)
    # Broad phase: a ray must meet a triangle's bounding sphere. This excludes
    # distant torso triangles before allocating intersection arrays.
    centre = tri.mean(axis=1)
    radius = np.linalg.norm(tri - centre[:, None], axis=2).max(axis=1)
    delta = centre[None] - orig[:, None]
    along = (delta * dirs[:, None]).sum(axis=2)
    distance2 = (delta * delta).sum(axis=2) - along * along
    tri = tri[((distance2 <= radius[None] ** 2 + 1e-9) & (along - radius[None] <= CAP)).any(axis=0)]
    orig, dirs, tri = orig.astype(np.float32), dirs.astype(np.float32), tri.astype(np.float32)
    v0 = tri[:, 0]
    e1, e2 = tri[:, 1] - v0, tri[:, 2] - v0
    h = np.cross(dirs[:, None, :], e2[None])
    a = (e1[None] * h).sum(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        f = 1.0 / a
        s = orig[:, None, :] - v0[None]
        u = f * (s * h).sum(-1)
        q = np.cross(s, e1[None])
        v = f * (dirs[:, None, :] * q).sum(-1)
        t = f * (e2[None] * q).sum(-1)
        ok = (np.abs(a) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1)
    return np.where(ok, t, np.nan)


def outer(t, low, high, none):
    """Select each ray's farthest hit within (low, high], or the supplied miss value.

    Args:
        t: Ray-by-triangle intersection distances with NaN misses.
        low: Scalar or per-ray exclusive lower bound.
        high: Inclusive upper distance bound.
        none: Scalar or per-ray result for no eligible hit.
    """
    low = np.broadcast_to(np.asarray(low, np.float32), (t.shape[0],))
    ok = (t > low[:, None]) & (t <= high)
    best = np.where(ok, t, -np.inf).max(axis=1, initial=-np.inf)
    return np.where(np.isfinite(best), best, none)


def measure(drawn, rig, pose, verts=None, points=None, other=True, core=False, radii=True, sample_indices=None):
    """Measure torso depth, sleeve radius, and body-axis distance at arm samples in metres.

    Args:
        drawn: Skinned costume with body and arm triangle partitions.
        rig: Slot arm landmarks and body-axis provider.
        pose: Slot-node world matrices for this frame.
        verts: Optional full posed costume vertices; None skins only the compact measurement view.
        points: Optional arm sample positions; None uses rig.samples(pose).
        other: Whether the opposite sleeve also counts as cover, as when a hand rests on it.
        core: Whether to exclude secondary cloth from torso cover.
        radii: Whether to cast sleeve radii in the torso-only path.
        sample_indices: Optional sample subset for the path with other=False; None measures all nine.

    Returns:
        S_body, S_arm, and rho arrays shaped as two arms by selected samples.
    """
    if verts is None:
        rows, torso, arm = drawn.measure_mesh(core, other or radii)
        v = drawn.posed(pose, rows)
    else:
        v, torso, arm = verts, drawn.body[drawn.body_core] if core else drawn.body, drawn.arm
    pts = rig.samples(pose) if points is None else points
    indices = np.arange(len(SAMPLES)) if sample_indices is None else np.asarray(sample_indices)
    pts = pts[:, indices]
    flat = pts.reshape(-1, 3)
    _, d, rho = radial(rig.axis(pose), flat)
    n = len(indices)
    body = cast(flat, d, v[torso], -rho)
    if not other:
        s_body = outer(body, -rho, CAP, -rho).reshape(2, n)
        s_arm = np.zeros((2, n))
        if radii:
            for s in range(2):
                for seg in range(3):
                    at = np.array([k for k, index in enumerate(indices) if seg in SEGMENTS[index]], int)
                    if not len(at):
                        continue
                    rows = s * n + at
                    hits = cast(flat[rows], d[rows], v[arm[(s, seg)]])
                    s_arm[s, at] = np.maximum(s_arm[s, at], outer(hits, 0.0, ARM_CAP, 0.0))
        return s_body, s_arm, rho.reshape(2, n)
    hits = {(s, seg): cast(flat, d, v[arm[(s, seg)]]) for s in range(2) for seg in range(3)}
    s_body, s_arm = np.zeros((2, n)), np.zeros((2, n))
    for s in range(2):
        rows = slice(s * n, (s + 1) * n)
        occluders = [body[rows]] + ([hits[(1 - s, seg)][rows] for seg in range(3)] if other else [])
        s_body[s] = outer(np.concatenate(occluders, axis=1), -rho[rows], CAP, -rho[rows])
        for k, segs in enumerate(SEGMENTS):
            s_arm[s, k] = max(
                float(outer(hits[(s, seg)][s * n + k : s * n + k + 1], 0.0, ARM_CAP, 0.0)[0]) for seg in segs
            )
    return s_body, s_arm, rho.reshape(2, -1)


def push_of(d_own, d_new, r_new, clear=1.0):
    """Compute the outward move restoring a bone's original costume-relative depth.

    D is S_body, the depth of the
    bone below the outermost surface that covers it (negative outside). Where the bone stood clear of its own costume
    by more than ``clear`` times the new sleeve's radius, the bone only has to come that far out (1: the new sleeve
    clears the surface; 0.5: three quarters of it show).

    Args:
        d_own: Slot costume's original S_body depth in metres, negative outside its surface.
        d_new: Incoming costume's S_body depth at the same arm samples.
        r_new: Incoming sleeve radii in metres.
        clear: Required exposed-radius multiplier for bones already clear of the original costume.
    """
    return np.maximum(0.0, d_new - np.maximum(d_own, -clear * r_new))


def girth(drawn, rig):
    """Measure a slot costume's rest sleeve and chest bulk in millimetres.

    Report sleeve radius around the forearm and upper arm bones (median distance
    of the segment's vertices from the bone, both arms, mm) and the chest front (the largest forward reach of the body
    vertices within 4 cm of the middle, from Spine1 up 8 cm, before Spine1; mm). Forward is the root's +z.

    Args:
        drawn: Costume skinning and per-vertex region labels to measure.
        rig: Slot rest pose, arm chains, and Spine1 lookup.
    """
    rest = rig.rest
    v = drawn.posed(rest)
    out = {}
    for seg, name in ((0, "upper"), (1, "fore")):
        ds = []
        for s in range(2):
            a, b = rest[rig.arm[s][1 + seg]][:3, 3], rest[rig.arm[s][2 + seg]][:3, 3]
            p = v[drawn.regions == 2 + 3 * s + seg]
            if len(p):
                t = np.clip((p - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1)
                ds.append(np.linalg.norm(p - (a + t[:, None] * (b - a)), axis=1))
        out[name] = float(np.median(np.concatenate(ds)) * 1000) if ds else float("nan")
    spine1 = rig.sk.first("Bip001 Spine1")
    if spine1 is not None:
        ch = rest[spine1][:3, 3]
        body = v[drawn.regions == 0]
        band = (np.abs(body[:, 0] - ch[0]) < 0.04) & (body[:, 1] > ch[1]) & (body[:, 1] < ch[1] + 0.08)
        out["chest"] = float((body[band, 2].max() - ch[2]) * 1000) if band.any() else float("nan")
        ring = (body[:, 1] > ch[1]) & (body[:, 1] < ch[1] + 0.08)
        out["chest_radius"] = (
            float(np.median(np.linalg.norm(body[ring][:, [0, 2]] - ch[[0, 2]], axis=1)) * 1000)
            if ring.any()
            else float("nan")
        )
    return out


# the samples that are held at their own depth, as (index, share of the wrist's move, share of the elbow's):
# E F25 F50 F75 W P (the palm moves with the wrist: the hand keeps its orientation)
DRIVEN = ((2, 0.0, 1.0), (3, 0.25, 0.75), (4, 0.5, 0.5), (5, 0.75, 0.25), (6, 1.0, 0.0), (7, 1.0, 0.0))
ITERATIONS = 4  # measure, solve, measure again
STEPS = 120  # gradient steps per solve
STEP = 0.08  # their size
MOVE_COST = 0.03  # the cost of moving the elbow and the wrist, against 1 for an unmet need
PAIR_WEIGHT = 0.3  # the weight of keeping the sleeves apart
PIN_COST = 10.0  # the cost of moving the wrist of a hand that touches the head
PIN_NEAR, PIN_FAR = 0.03, 0.06  # a hand this near the head's own surface is pinned fully, ... not at all (metres)
NEED_CAP = 0.09  # a sample is never asked to move out more than this (metres)
CLEAR = 0.5  # a bone that stood clear of its own costume comes out to this share of the new sleeve's radius
DEAD = 0.005  # a need below this changes nothing (the measure's noise between two meshes)
CORE = True  # depths are taken against the body's core (no cloth on secondary bones: a cape may cover an arm)
DILATE = 2  # frames each side a need is held before blending (at 30 fps)
SIGMA = 2.5  # frames, the blend
GUARD_TOLERANCE = 0.001  # all nine samples, penetration max(core depth, 0)


def min_rotation(a, b):
    """Return the smallest 3 by 3 rotation taking direction a to b, normalizing both inputs."""
    a, b = a / max(np.linalg.norm(a), 1e-12), b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(a @ b)
    if c < -1 + 1e-9:
        return _half_turn(a)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _half_turn(a):
    """A half turn about an axis normal to the unit direction a."""
    axis = np.cross(a, [1.0, 0, 0])
    if np.linalg.norm(axis) < 1e-6:
        axis = np.cross(a, [0, 1.0, 0])
    axis /= np.linalg.norm(axis)
    return 2 * np.outer(axis, axis) - np.eye(3)


def min_rotations(a, b):
    """Apply min_rotation to corresponding rows of direction arrays with the same per-row arithmetic."""
    a = a / np.maximum(np.linalg.norm(a, axis=1), 1e-12)[:, None]
    b = b / np.maximum(np.linalg.norm(b, axis=1), 1e-12)[:, None]
    v = np.cross(a, b)
    c = (a * b).sum(axis=1)
    k = np.zeros((len(a), 3, 3))
    k[:, 0, 1], k[:, 0, 2] = -v[:, 2], v[:, 1]
    k[:, 1, 0], k[:, 1, 2] = v[:, 2], -v[:, 0]
    k[:, 2, 0], k[:, 2, 1] = -v[:, 1], v[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):  # opposite rows take the half turn below
        out = np.eye(3) + k + k @ k / (1 + c)[:, None, None]
    for row in np.flatnonzero(c < -1 + 1e-9):
        out[row] = _half_turn(a[row])
    return out


def _pad(values, k, loop):
    return np.pad(values, ((k, k), (0, 0)), mode="wrap" if loop else "edge")


def maximum_filter1d(values, size, loop):
    """Take a temporal maximum over each sample channel.

    Args:
        values: Frame-by-channel measurements.
        size: Window width in frames, centered using size // 2 padding.
        loop: Whether endpoints wrap; False repeats endpoint values.
    """
    k = size // 2
    p = _pad(values, k, loop)
    return np.max([p[i : i + len(values)] for i in range(size)], axis=0)


def gaussian_filter1d(values, sigma, loop):
    """Smooth each sample channel over time with a normalized Gaussian window.

    Args:
        values: Nonempty frame-by-channel measurements.
        sigma: Gaussian standard deviation in frames; the window extends about three sigma.
        loop: Whether endpoints wrap; False repeats endpoint values.
    """
    k = max(1, int(round(3 * sigma)))
    k = min(k, len(values))
    x = np.arange(-k, k + 1)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    g /= g.sum()
    p = _pad(values, k, loop)
    return sum(g[i] * p[i : i + len(values)] for i in range(2 * k + 1))


def blend(values, loop):
    """Hold correction needs DILATE frames each side and smooth without falling below a light smoothing.

    Args:
        values: Frame-first correction needs with any trailing sample dimensions.
        loop: Whether temporal filtering wraps across the clip seam.
    """
    shape = values.shape
    flat = values.reshape(shape[0], -1)
    held = maximum_filter1d(flat, 2 * DILATE + 1, loop)
    smooth = gaussian_filter1d(held, SIGMA, loop)
    return np.maximum(smooth, gaussian_filter1d(flat, 1.0, loop)).reshape(shape)


class Arms:
    """Apply minimal arm-chain turns while preserving hand orientation and descendant locals."""

    def __init__(self, rig):
        """Partition each valid slot Rig arm into upper-arm, forearm, and hand subtrees."""
        self.rig = rig
        self.sub, self.groups = [], []
        for arm in rig.arm:
            upper, fore, hand = arm[1:]
            all_upper = {upper, *rig.sk.descendants(upper)}
            all_fore = {fore, *rig.sk.descendants(fore)}
            all_hand = {hand, *rig.sk.descendants(hand)}
            self.sub.append(sorted(all_upper))
            self.groups.append(
                [
                    np.array(sorted(all_upper - all_fore)),
                    np.array(sorted(all_fore - all_hand)),
                    np.array(sorted(all_hand)),
                ]
            )

    def repose(self, poses, source, side, rows, elbow, wrist):
        """Apply one arm's least bone turns while retaining hand orientation and lower locals.

        Each unchanged subtree receives one rigid world transform. A hand that
        keeps its world orientation translates its entire subtree, so no per-
        descendant inverse matrices or hierarchy recomposition are necessary.

        Args:
            poses: Mutable frame-by-node 4 by 4 world matrices receiving the corrected subtree transforms.
            source: Unturned world matrices for the same frames and skeleton.
            side: Arm index, zero for left or one for right.
            rows: Frame indices to update, aligned with elbow and wrist targets.
            elbow: Target elbow world positions in metres for the selected frames.
            wrist: Target wrist world positions in metres for the selected frames.
        """
        _, upper, fore, hand = self.rig.arm[side]
        source = source[rows]
        shoulder = source[:, upper, :3, 3]
        turn_upper = np.repeat(np.eye(4)[None], len(rows), axis=0)
        turn_upper[:, :3, :3] = min_rotations(source[:, fore, :3, 3] - shoulder, elbow - shoulder)
        turn_upper[:, :3, 3] = shoulder - (turn_upper[:, :3, :3] @ shoulder[..., None])[..., 0]
        fore_world = turn_upper @ source[:, fore]
        hand_world = turn_upper @ source[:, hand]
        anchor = fore_world[:, :3, 3]
        turn_fore = np.repeat(np.eye(4)[None], len(rows), axis=0)
        turn_fore[:, :3, :3] = min_rotations(hand_world[:, :3, 3] - anchor, wrist - anchor)
        turn_fore[:, :3, 3] = anchor - (turn_fore[:, :3, :3] @ anchor[..., None])[..., 0]
        combined = turn_fore @ turn_upper
        upper_rows, fore_rows, hand_rows = self.groups[side]
        poses[rows[:, None], upper_rows[None]] = turn_upper[:, None] @ source[:, upper_rows]
        poses[rows[:, None], fore_rows[None]] = combined[:, None] @ source[:, fore_rows]
        hand_delta = (combined @ source[:, hand])[:, :3, 3] - source[:, hand, :3, 3]
        poses[rows[:, None], hand_rows[None], :3, 3] += hand_delta[:, None]


def head_core(costume):
    """Find own_drawn vertex rows dominated by the costume's head bone, excluding hair chains."""
    sk = costume.skeleton
    head = sk.first("Bip001 Head")
    rows, off = [], 0
    for part, skin in zip(costume.parts, costume.skins):
        bones = np.array([b if b is not None else 0 for b in part.bones])
        j = np.minimum(skin.joints, len(bones) - 1)
        dom = bones[j[np.arange(len(j)), skin.weights.argmax(1)]]
        rows.append(np.where(dom == head)[0] + off)
        off += len(skin.vertices)
    return np.concatenate(rows)


def head_pins(rig, own, poses, rows, loop):
    """Measure time-blended head-contact pin strength from zero to one per frame and hand.

    Args:
        rig: Slot landmarks supplying wrist, palm, and fingertip positions.
        own: Slot's original costume skinning view.
        poses: Frame-by-node world matrices used to skin the head and sample hands.
        rows: Head-dominated vertex rows from head_core; empty rows yield no pins.
        loop: Whether temporal blending wraps over the clip seam.
    """
    out = np.zeros((len(poses), 2))
    if not len(rows):
        return out
    rows = rows[::2]
    for begin in range(0, len(poses), 16):
        chunk = poses[begin : begin + 16]
        hv = own.posed_batch(chunk, rows)
        pts = rig.samples_many(chunk)[:, :, 6:9].reshape(len(chunk), -1, 3)
        dist = np.linalg.norm(pts[:, :, None] - hv[:, None], axis=3).min(axis=2).reshape(-1, 2, 3).min(axis=2)
        out[begin : begin + len(chunk)] = np.clip((PIN_FAR - dist) / (PIN_FAR - PIN_NEAR), 0.0, 1.0)
    return np.clip(blend(out, loop), 0.0, 1.0)
