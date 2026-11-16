"""Contact-aware arm clearance for a costume bound to another slot.

The measure and projected arm solver follow the numeric costume-retarget prototype.
All distances are metres. Only the torso core counts as cover; contacts and the
clip gate keep intentional poses, secondary cloth and small differences intact.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from . import model_adapt as ma
from . import model_skirts as ms

logger = logging.getLogger(__name__)

ARM = re.compile(r"^Bip001 ([LR]) (UpperArm|Forearm|Hand|Finger\d+)$")
CAP = 0.15       # a body surface farther out than this from the bone does not count (wings, a slung bag)
ARM_CAP = 0.08   # the arm's own surface is looked for this far out
SAMPLES = ("U50", "U75", "E", "F25", "F50", "F75", "W", "P", "T")
SEGMENTS = ((0,), (0,), (0, 1), (1,), (1,), (1,), (1, 2), (2,), (2,))  # arm segments whose surface is the sample's own
LOWER = slice(2, 9)  # elbow to fingertips


def chain_name(sk, n):
    while n is not None and not ma.BIPED_CHAIN.match(sk.nodes[n].name):
        n = sk.nodes[n].parent
    return sk.nodes[n].name if n is not None else ""


def region(sk, n):
    """0 body, 1 head, 2.. an arm segment: 2 + 3 * side + (0 upper arm, 1 forearm, 2 hand and fingers)."""
    name = chain_name(sk, n)
    m = ARM.match(name)
    if m:
        seg = 0 if m.group(2) == "UpperArm" else 1 if m.group(2) == "Forearm" else 2
        return 2 + 3 * "LR".index(m.group(1)) + seg
    return 1 if name == "Bip001 Head" else 0


class Drawn:
    """A costume on a skeleton: posed vertices by frame, triangles, and each vertex's region."""

    def __init__(self, vertices, joints, weights, triangles, regions, matrices, loose=None):
        self.h = np.c_[vertices, np.ones(len(vertices))]
        self.loose = np.zeros(len(vertices), bool) if loose is None else loose
        self.joints, self.weights, self.triangles, self.regions, self.matrices = joints, weights, triangles, regions, matrices
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
        m = pose[self.pose_bones] @ self.bindings if hasattr(self, 'bindings') and isinstance(pose, np.ndarray) else self.matrices(pose)
        h, joints, weights = self.h, self.joints, self.weights
        if rows is not None:
            h, joints, weights = h[rows], joints[rows], weights[rows]
        out = np.zeros((len(h), 3))
        for k in range(self.joints.shape[1]):
            used = weights[:, k] != 0
            if used.all():
                out += weights[:, k:k + 1] * np.einsum("nij,nj->ni", m[joints[:, k]], h)[:, :3]
            elif used.any():
                out[used] += weights[used, k:k + 1] * np.einsum("nij,nj->ni", m[joints[used, k]], h[used])[:, :3]
        return out

    def posed_batch(self, poses, rows):
        """Bounded frame batches share sparse influence gathers and skinning."""
        if not hasattr(self, 'bindings'):
            return np.array([self.posed(w, rows) for w in poses])
        matrices = np.asarray(poses)[:, self.pose_bones] @ self.bindings
        joints, weights, h = self.joints[rows], self.weights[rows], self.h[rows]
        vertices = np.zeros((len(poses), len(rows), 3))
        for k in range(joints.shape[1]):
            used = weights[:, k] != 0
            if used.any():
                vertices[:, used] += weights[None, used, k:k + 1] * np.einsum(
                    'fvij,vj->fvi', matrices[:, joints[used, k]], h[used])[..., :3]
        return vertices

    def measure_mesh(self, core, arms):
        """Compact skinning views exclude hair, face and unused arm vertices."""
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
        """
        if self._bound is None:
            rows = self.measure_mesh(True, False)[0]
            joints, corners = [], []
            for joint in np.unique(self.joints[rows][self.weights[rows] > 0]):
                hit = ((self.joints[rows] == joint) & (self.weights[rows] > 0)).any(axis=1)
                lo, hi = self.h[rows[hit], :3].min(axis=0), self.h[rows[hit], :3].max(axis=0)
                corners.append([[hi[k] if bits & (1 << k) else lo[k] for k in range(3)] + [1.] for bits in range(8)])
                joints.append(joint)
            sums = self.weights[rows].sum(axis=1)
            self._bound = np.array(joints), np.array(corners), np.array([sums.min(initial=1), sums.max(initial=1)])
        joints, corners, sums = self._bound
        if not len(joints):
            return np.zeros(3), np.zeros(3)
        world = np.einsum('nij,nkj->nki', self.matrices(pose)[joints], corners)[..., :3]
        extremes = np.array([world.min(axis=(0, 1)), world.max(axis=(0, 1))])
        scaled = extremes[:, None] * sums[None, :, None]
        return scaled.min(axis=(0, 1)) - 1e-6, scaled.max(axis=(0, 1)) + 1e-6


def own_drawn(costume):
    """The slot's own costume, skinned by its own bindposes."""
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
        regs.append(np.array([cache.setdefault(n, region(sk, n)) for n in dom]))
        loose.append(np.array([not sk.nodes[n].name.startswith("Bip001") for n in dom]))
        verts.append(skin.vertices); joints.append(j + off_m); weights.append(skin.weights)
        tris.append((skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)) + off_v)
        bones_all += bones; bps.append(bp[:len(bones)])
        off_v += len(skin.vertices); off_m += len(bones)
    bp_all = np.concatenate(bps)
    out = Drawn(np.vstack(verts), np.vstack(joints), np.vstack(weights), np.vstack(tris), np.concatenate(regs),
                lambda pose: np.array([pose[b] for b in bones_all]) @ bp_all, np.concatenate(loose))
    out.pose_bones = np.array(bones_all)
    out.bindings = bp_all
    return out


def adapted_drawn(target, source, plan):
    """The source's costume as the plan binds it to the slot's skeleton (its bound shape and bone mapping; the hem
    rules' and the stiffening's later weight changes around the legs are left out)."""
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
    cache = {}
    regions = np.array([cache.setdefault(n, region(ss, n)) for n in dom])
    loose = np.array([not ss.nodes[n].name.startswith("Bip001") for n in dom])
    out = Drawn(plan.fit.vertices, joints, sw, flat.triangles, regions,
                lambda pose: np.array([pose[t] for t in mapped]) @ inverse, loose)
    out.pose_bones = np.array(mapped)
    out.bindings = inverse
    return out


class Rig:
    """The slot's arms and body axis."""

    def __init__(self, sk):
        f = sk.first
        self.sk = sk
        self.arm = [[f(f"Bip001 {s} {b}") for b in ("Clavicle", "UpperArm", "Forearm", "Hand")] for s in "LR"]
        self.ok = all(n is not None for a in self.arm for n in a[1:])
        spine = [f("Bip001 Pelvis")] + [f(f"Bip001 Spine{k}") for k in ("", "1", "2", "3")] + [f("Bip001 Neck"),
                                                                                             f("Bip001 Head")]
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
            kids = [i for i, n in enumerate(sk.nodes) if n.parent == hand and n.name.startswith("Bip001 ")
                    and "Finger" in n.name]
            below = [i for i in sk.descendants(hand) if sk.nodes[i].name.startswith("Bip001 ") and "Finger" in sk.nodes[i].name]
            leaves = [i for i in below if not any(sk.nodes[j].parent == i for j in below)]
            fore = rest[hand][:3, 3] - rest[a[2]][:3, 3]
            fallback = rest[hand][:3, 3] + 0.3 * fore
            palm = np.mean([rest[i][:3, 3] for i in kids], axis=0) if kids else fallback
            tip = np.mean([rest[i][:3, 3] for i in leaves], axis=0) if leaves else rest[hand][:3, 3] + 0.6 * fore
            self.palm.append(inv @ np.r_[palm, 1.0])
            self.tip.append(inv @ np.r_[tip, 1.0])

    def joints(self, pose, s):
        return [pose[n][:3, 3] for n in self.arm[s][1:]]

    def samples(self, pose):
        """(2, 9, 3): U50 U75 E F25 F50 F75 W P T per arm."""
        out = np.zeros((2, len(SAMPLES), 3))
        for s in range(2):
            u, e, w = self.joints(pose, s)
            hand = pose[self.arm[s][3]]
            out[s] = [u + 0.5 * (e - u), u + 0.75 * (e - u), e, e + 0.25 * (w - e), e + 0.5 * (w - e),
                      e + 0.75 * (w - e), w, (hand @ self.palm[s])[:3], (hand @ self.tip[s])[:3]]
        return out

    def axis(self, pose):
        pts = [pose[n][:3, 3] for n in self.spine]
        if self.feet:
            pts = [np.mean([pose[n][:3, 3] for n in self.feet], axis=0)] + pts
        return np.array(pts)


def radial(axis, points):
    """Per point: its nearest point on the polyline ``axis``, the unit direction away from it and the distance."""
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
    """Line-triangle intersections: t (rays x triangles) with nan where the line p + t d misses; tri (T, 3, 3)."""
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
    """Per ray the largest t within (low, high]; ``none`` where no hit."""
    low = np.broadcast_to(np.asarray(low, np.float32), (t.shape[0],))
    ok = (t > low[:, None]) & (t <= high)
    best = np.where(ok, t, -np.inf).max(axis=1, initial=-np.inf)
    return np.where(np.isfinite(best), best, none)


def measure(drawn, rig, pose, verts=None, points=None, other=True, core=False, radii=True, sample_indices=None):
    """S_body, S_arm and rho per arm sample: three (2, 9) arrays (metres). With ``other`` the other arm counts as
    body for an arm's samples (a hand resting on the other sleeve)."""
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
                    s_arm[s, at] = np.maximum(s_arm[s, at], outer(hits, 0., ARM_CAP, 0.))
        return s_body, s_arm, rho.reshape(2, n)
    hits = {(s, seg): cast(flat, d, v[arm[(s, seg)]]) for s in range(2) for seg in range(3)}
    s_body, s_arm = np.zeros((2, n)), np.zeros((2, n))
    for s in range(2):
        rows = slice(s * n, (s + 1) * n)
        occluders = [body[rows]] + ([hits[(1 - s, seg)][rows] for seg in range(3)] if other else [])
        s_body[s] = outer(np.concatenate(occluders, axis=1), -rho[rows], CAP, -rho[rows])
        for k, segs in enumerate(SEGMENTS):
            s_arm[s, k] = max(float(outer(hits[(s, seg)][s * n + k:s * n + k + 1], 0.0, ARM_CAP, 0.0)[0]) for seg in segs)
    return s_body, s_arm, rho.reshape(2, -1)


def push_of(d_own, d_new, r_new, clear=1.0):
    """The outward move that brings a bone back to its depth on the slot's own costume: D is S_body, the depth of the
    bone below the outermost surface that covers it (negative outside). Where the bone stood clear of its own costume
    by more than ``clear`` times the new sleeve's radius, the bone only has to come that far out (1: the new sleeve
    clears the surface; 0.5: three quarters of it show)."""
    return np.maximum(0.0, d_new - np.maximum(d_own, -clear * r_new))


def girth(drawn, rig):
    """Rest bulk of a costume on the slot: the sleeve radius around the forearm and upper arm bones (median distance
    of the segment's vertices from the bone, both arms, mm) and the chest front (the largest forward reach of the body
    vertices within 4 cm of the middle, from Spine1 up 8 cm, before Spine1; mm). Forward is the root's +z."""
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
        out["chest_radius"] = float(np.median(np.linalg.norm(body[ring][:, [0, 2]] - ch[[0, 2]], axis=1)) * 1000) \
            if ring.any() else float("nan")
    return out



# the samples that are held at their own depth, as (index, share of the wrist's move, share of the elbow's):
# E F25 F50 F75 W P (the palm moves with the wrist: the hand keeps its orientation)
DRIVEN = ((2, 0.0, 1.0), (3, 0.25, 0.75), (4, 0.5, 0.5), (5, 0.75, 0.25), (6, 1.0, 0.0), (7, 1.0, 0.0))
ITERATIONS = 4    # measure, solve, measure again
STEPS = 120       # gradient steps per solve
STEP = 0.08       # their size
MOVE_COST = 0.03  # the cost of moving the elbow and the wrist, against 1 for an unmet need
PAIR_WEIGHT = 0.3 # the weight of keeping the sleeves apart
PIN_COST = 10.0   # the cost of moving the wrist of a hand that touches the head
PIN_NEAR, PIN_FAR = 0.03, 0.06  # a hand this near the head's own surface is pinned fully, ... not at all (metres)
NEED_CAP = 0.09   # a sample is never asked to move out more than this (metres)
CLEAR = 0.5       # a bone that stood clear of its own costume comes out to this share of the new sleeve's radius
DEAD = 0.005      # a need below this changes nothing (the measure's noise between two meshes)
CORE = True       # depths are taken against the body's core (no cloth on secondary bones: a cape may cover an arm)
DILATE = 2        # frames each side a need is held before blending (at 30 fps)
SIGMA = 2.5       # frames, the blend
GUARD_TOLERANCE = 0.001  # all nine samples, penetration max(core depth, 0)


def min_rotation(a, b):
    """The smallest rotation taking direction a to direction b."""
    a, b = a / max(np.linalg.norm(a), 1e-12), b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(a @ b)
    if c < -1 + 1e-9:
        axis = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _pad(values, k, loop):
    return np.pad(values, ((k, k), (0, 0)), mode="wrap" if loop else "edge")


def maximum_filter1d(values, size, loop):
    k = size // 2
    p = _pad(values, k, loop)
    return np.max([p[i:i + len(values)] for i in range(size)], axis=0)


def gaussian_filter1d(values, sigma, loop):
    k = max(1, int(round(3 * sigma)))
    k = min(k, len(values))
    x = np.arange(-k, k + 1)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    g /= g.sum()
    p = _pad(values, k, loop)
    return sum(g[i] * p[i:i + len(values)] for i in range(2 * k + 1))


def blend(values, loop):
    """Needs over a clip's frames (frames x ...) held DILATE frames each side and blended (never below what a light
    smoothing of the need leaves)."""
    shape = values.shape
    flat = values.reshape(shape[0], -1)
    held = maximum_filter1d(flat, 2 * DILATE + 1, loop)
    smooth = gaussian_filter1d(held, SIGMA, loop)
    return np.maximum(smooth, gaussian_filter1d(flat, 1.0, loop)).reshape(shape)


class Arms:
    def __init__(self, rig):
        self.rig = rig
        self.sub, self.groups = [], []
        for arm in rig.arm:
            upper, fore, hand = arm[1:]
            all_upper = {upper, *rig.sk.descendants(upper)}
            all_fore = {fore, *rig.sk.descendants(fore)}
            all_hand = {hand, *rig.sk.descendants(hand)}
            self.sub.append(sorted(all_upper))
            self.groups.append([np.array(sorted(all_upper - all_fore)), np.array(sorted(all_fore - all_hand)),
                                np.array(sorted(all_hand))])

    def repose(self, w, targets):
        """Least bone turns; hand orientation and every lower local stay exact.

        Each unchanged subtree receives one rigid world transform. A hand that
        keeps its world orientation translates its entire subtree, so no per-
        descendant inverse matrices or hierarchy recomposition are necessary.
        """
        source = np.asarray(w)
        out = source.copy()
        for side, target in enumerate(targets):
            if target is None:
                continue
            _, upper, fore, hand = self.rig.arm[side]
            elbow, wrist = target
            shoulder = source[upper, :3, 3]
            turn_upper = np.eye(4)
            turn_upper[:3, :3] = min_rotation(source[fore, :3, 3] - shoulder, elbow - shoulder)
            turn_upper[:3, 3] = shoulder - turn_upper[:3, :3] @ shoulder
            fore_world = turn_upper @ source[fore]
            hand_world = turn_upper @ source[hand]
            anchor = fore_world[:3, 3]
            turn_fore = np.eye(4)
            turn_fore[:3, :3] = min_rotation(hand_world[:3, 3] - anchor, wrist - anchor)
            turn_fore[:3, 3] = anchor - turn_fore[:3, :3] @ anchor
            combined = turn_fore @ turn_upper
            upper_rows, fore_rows, hand_rows = self.groups[side]
            out[upper_rows] = turn_upper @ source[upper_rows]
            out[fore_rows] = combined @ source[fore_rows]
            hand_delta = (combined @ source[hand])[:3, 3] - source[hand, :3, 3]
            out[hand_rows, :3, 3] += hand_delta
        return out



def head_core(costume):
    """Per costume part the rows of the vertices the head bone itself dominates (the face and skull, not the hair's
    chains), as rows of ``own_drawn``'s vertices."""
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
    """Per frame and hand how firmly the hand is pinned (0..1): by the distance of its wrist, palm and fingertips to
    the head's own surface on the slot's own costume, blended in time."""
    out = np.zeros((len(poses), 2))
    if not len(rows):
        return out
    rows = rows[::2]
    for f, w in enumerate(poses):
        hv = own.posed(w, rows)
        pts = rig.samples(w)[:, 6:9].reshape(-1, 3)
        dist = np.linalg.norm(pts[:, None] - hv[None], axis=2).min(axis=1).reshape(2, 3).min(axis=1)
        out[f] = np.clip((PIN_FAR - dist) / (PIN_FAR - PIN_NEAR), 0.0, 1.0)
    return np.clip(blend(out, loop), 0.0, 1.0)



GATE_DEPTH = 0.020
GATE_FRACTION = 0.20
GATE_EVERY = 3
SOLVE_EVERY = 3
WRIST_SPEED_RATIO = 1.3
OWN_CACHE_BYTES = 24 * 1024 * 1024
_own_cache = OrderedDict()
_own_lock = threading.Lock()


class _Progress:
    def __init__(self, step):
        self.step, self.last = step, time.perf_counter()

    def __call__(self):
        if self.step is not None and time.perf_counter() - self.last >= .5:
            self.step()
            self.last = time.perf_counter()


def gated(need):
    """At least a fifth of sampled frames have an arm needing more than 20 mm."""
    return bool(len(need) and np.mean(np.max(need[:, :, GATE_SAMPLES], axis=(1, 2)) > GATE_DEPTH) >= GATE_FRACTION)


def weapon_holds(relative, distance, loop=False):
    """An 11-frame stable grip in mount space, within 5 cm of the shown mesh.

    A hidden mount is represented by NaNs; it invalidates the whole window.
    At non-loop endpoints the window is clipped, as in the prototype.
    """
    out = np.zeros(distance.shape, bool)
    for f in range(len(relative)):
        rows = np.arange(f - 5, f + 6)
        rows = rows % len(relative) if loop else rows[(rows >= 0) & (rows < len(relative))]
        win = relative[rows]
        still = np.isfinite(win).all(axis=(0, 2)) & (np.linalg.norm(win - win.mean(axis=0), axis=2).max(axis=0) < 0.004)
        out[f] = still & (distance[f] < 0.05)
    return out


def contacts(rig, own, weapons, poses, rows, loop, shown=None):
    relative = np.full((len(poses), 2, 3), np.nan)
    distance = np.full((len(poses), 2), np.inf)
    if weapons and rig.weapon is not None:
        for f, w in enumerate(poses):
            mount = w[rig.weapon]
            if abs(np.linalg.det(mount[:3, :3])) < 1e-9:
                continue
            palms = rig.samples(w)[:, 7]
            visible = [weapon.posed(w, np.arange(0, len(weapon.h), 5)) for k, weapon in enumerate(weapons)
                       if shown is None or shown[f, k]]
            if not visible:
                continue
            vertices = np.concatenate(visible)
            distance[f] = np.linalg.norm(palms[:, None] - vertices[None], axis=2).min(axis=1)
            relative[f] = (np.c_[palms, np.ones(2)] @ np.linalg.inv(mount).T)[:, :3]
    return weapon_holds(relative, distance, loop), head_pins(rig, own, poses, rows, loop)


def weapon_visibility(rig, tree, parts, poses):
    """Weapon renderers enabled on active GameObjects, including clip switches."""
    from .model import bind_toggles, TOGGLE_TYPES
    from .model_arm_clips import sample_curves
    by_hash = {zlib.crc32(n.path.encode()): i for i, n in enumerate(rig.sk.nodes)}
    bindings = tree["m_ClipBindingConstant"]["genericBindings"]
    wanted, index = set(), 0
    for binding in bindings:
        if binding['typeID'] in TOGGLE_TYPES:
            wanted.add(index)
        index += ms.binding_dims(binding)
    _, curves = sample_curves(tree, wanted)
    switches = {(node, kind): values for node, kind, values in bind_toggles(
        bindings, curves, by_hash)}
    shown = np.zeros((len(poses), len(parts)), bool)
    for k, part in enumerate(parts):
        renderer = bool(rig.sk.graph.tree(part.renderer).get("m_Enabled", 1))
        for f in range(len(poses)):
            values = switches.get((part.node, "renderer"))
            enabled = values[min(f, len(values) - 1)] >= .5 if values is not None else renderer
            node = part.node
            while enabled and node is not None:
                values = switches.get((node, "active"))
                # Node.active includes ancestors; local active state is read
                # from the GameObject so an animated parent can show a child.
                go = rig.sk.nodes[node].go
                active = bool(rig.sk.graph.tree(go).get("m_IsActive", 1)) if go is not None else rig.sk.nodes[node].active
                enabled &= values[min(f, len(values) - 1)] >= .5 if values is not None else active
                node = rig.sk.nodes[node].parent
            shown[f, k] = enabled
    return shown


def push_directions(rig, w, pts):
    d = radial(rig.axis(w), pts.reshape(-1, 3))[1].reshape(2, -1, 3)
    # Biped bone coordinates do not have a universal up axis. Read the upper
    # chest's up direction from the spine to the neck instead.
    lower = rig.sk.first("Bip001 Spine1")
    upper = rig.sk.first("Bip001 Neck")
    if lower is not None and upper is not None:
        up = w[upper][:3, 3] - w[lower][:3, 3]
        up /= max(np.linalg.norm(up), 1e-9)
        horizontal = d - (d @ up)[..., None] * up
        d = horizontal / np.maximum(np.linalg.norm(horizontal, axis=2), 1e-9)[..., None]
    return d


def solve_many(rig, poses, needs, hold, pins, sleeves, guard=None, initial=None):
    """The prototype's coupled projected gradient, vectorized over sparse frames."""
    idx = [k for k, _, _ in DRIVEN]
    tw = np.array([x for _, x, _ in DRIVEN])[None, None, :, None]
    te = 1 - tw
    a = np.minimum(needs[:, :, idx], NEED_CAP).copy()
    a[:, :, 4:] *= (1 - pins)[..., None]
    free = (~hold)[..., None]
    pts = np.array([rig.samples(w) for w in poses])
    p = pts[:, :, idx]
    diff = p[:, 0, :, None] - p[:, 1, None, :]
    c = np.linalg.norm(diff, axis=3)
    reach, grown = sleeves
    near = (np.clip((reach - c) / 0.02, 0, 1) * np.exp(-(c - c.min(axis=(1, 2))[:, None, None]) / 0.015)
            if grown > 0 else np.zeros_like(c))
    # A sleeve term alone never starts a correction on an otherwise clear arm.
    active = (a.max(axis=2) > 1e-5) & ~hold
    near *= active.any(axis=1)[:, None, None]
    normal = diff / np.maximum(c, 1e-9)[..., None]
    d = np.array([push_directions(rig, w, pt)[:, idx] for w, pt in zip(poses, pts)])
    joints = np.array([[rig.joints(w, s) for s in range(2)] for w in poses])
    u, e, wr = (joints[:, :, k] for k in range(3))
    l1 = np.linalg.norm(e - u, axis=2)[..., None]
    l2 = np.linalg.norm(wr - e, axis=2)[..., None]
    de, dw = np.zeros_like(e), np.zeros_like(wr)
    if initial is not None:
        de, dw = .25 * initial[:, :, 0], .25 * initial[:, :, 1]
    guard_tw = np.array([0, 0, 0, .25, .5, .75, 1, 1, 1])[None, None, :, None]
    guard_te = np.array([.5, .75, 1, .75, .5, .25, 0, 0, 0])[None, None, :, None]
    step = STEP if guard is None else .025
    steps = STEPS if initial is None else 3 * STEPS // 4
    for iteration in range(steps + (60 if guard is not None else 0)):
        dp = tw * dw[:, :, None] + te * de[:, :, None]
        v = np.maximum(0, a - (d * dp).sum(axis=3))
        # Penalize inward moves too; separating sleeves must not bury an arm.
        v += 4 * np.maximum(0, -(d * dp).sum(axis=3))
        g = -v[..., None] * d
        rel = dp[:, 0, :, None] - dp[:, 1, None, :]
        vp = PAIR_WEIGHT * near * np.maximum(0, grown - (normal * rel).sum(axis=3))
        g[:, 0] -= (vp[..., None] * normal).sum(axis=2)
        g[:, 1] += (vp[..., None] * normal).sum(axis=1)
        gw = (tw * g).sum(axis=2)
        ge = (te * g).sum(axis=2)
        if guard is not None:
            direction, target = guard
            all_dp = guard_tw * dw[:, :, None] + guard_te * de[:, :, None]
            penalty = 4 * np.maximum(0, target - (direction * all_dp).sum(axis=3))
            gradient = -penalty[..., None] * direction
            gw += (guard_tw * gradient).sum(axis=2)
            ge += (guard_te * gradient).sum(axis=2)
        if iteration >= steps:
            gw = (guard_tw * gradient).sum(axis=2)
            ge = (guard_te * gradient).sum(axis=2)
        else:
            gw += (MOVE_COST + PIN_COST * pins[..., None]) * dw
            ge += MOVE_COST * de
        # Optimize the two unit bone directions. The upper-arm derivative
        # includes both joint gradients because turning it moves the wrist too.
        # Sequentially projecting elbow then wrist biased the other samples in.
        v1, v2 = e + de - u, wr + dw - e - de
        a1, a2 = v1 / l1, v2 / l2
        gu = ge + gw
        v1 -= step * (gu - (gu * a1).sum(axis=2)[..., None] * a1) * free
        v2 -= step * (gw - (gw * a2).sum(axis=2)[..., None] * a2) * free
        v1 *= l1 / np.maximum(np.linalg.norm(v1, axis=2), 1e-9)[..., None]
        v2 *= l2 / np.maximum(np.linalg.norm(v2, axis=2), 1e-9)[..., None]
        de = (u + v1 - e) * free
        dw = (u + v1 + v2 - wr) * free
    return np.stack((de, dw), axis=2)


def rate_limit(values, peak, hold, loop):
    """A Lipschitz lower envelope: smoothing cannot increase a reach-capped need.

    Reserve 30% of the original peak speed for the correction. Contact frames
    are zero anchors. Forward/backward passes also propagate across loop seams.
    """
    out = values.copy()
    out[hold] = 0
    limit = 0.3 * peak
    for _ in range(2 if loop else 1):
        for f in range(len(out)):
            if f or loop:
                out[f] = np.minimum(out[f], out[f - 1] + limit[:, None])
        for f in range(len(out) - 1, -1, -1):
            if f + 1 < len(out) or loop:
                out[f] = np.minimum(out[f], out[(f + 1) % len(out)] + limit[:, None])
    return out


def _interpolate(frames, values, count):
    flat = values.reshape(len(frames), -1)
    return np.stack([np.interp(np.arange(count), frames, flat[:, k]) for k in range(flat.shape[1])], axis=1).reshape(
        (count,) + values.shape[1:])


def _repose(rig, poses, deltas, hold):
    if not hasattr(rig, "_arms"):
        rig._arms = Arms(rig)
    arms = rig._arms
    out = []
    for f, w in enumerate(poses):
        targets = []
        for s in range(2):
            if hold[f, s] or np.abs(deltas[f, s]).max() < 1e-7:
                targets.append(None)
                continue
            u, e, wr = rig.joints(w, s)
            l1, l2 = np.linalg.norm(e - u), np.linalg.norm(wr - e)
            e2, w2 = np.array([e, wr]) + deltas[f, s]
            # The interpolated wrist is fixed; choose the nearest elbow on the
            # two bone spheres' circle. This keeps lengths without amplifying
            # the interpolated wrist's speed during the final rotation solve.
            reach = np.linalg.norm(w2 - u)
            axis = (w2 - u) / max(reach, 1e-9)
            reach = np.clip(reach, abs(l1 - l2) + 1e-8, l1 + l2 - 1e-8)
            w2 = u + reach * axis
            along = (l1 * l1 - l2 * l2 + reach * reach) / (2 * reach)
            centre = u + along * axis
            normal = e2 - centre - np.dot(e2 - centre, axis) * axis
            if np.linalg.norm(normal) < 1e-9:
                normal = e - centre - np.dot(e - centre, axis) * axis
            normal /= max(np.linalg.norm(normal), 1e-9)
            e2 = centre + np.sqrt(max(0, l1 * l1 - along * along)) * normal
            targets.append((e2, w2))
        out.append(arms.repose(w, targets))
    return out


def wrist_peak(rig, poses, loop):
    wrists = np.array([[rig.joints(w, s)[2] for s in range(2)] for w in poses])
    delta = np.diff(wrists, axis=0)
    if loop:
        delta = np.concatenate((delta, wrists[:1] - wrists[-1:]))
    return np.linalg.norm(delta, axis=2).max(axis=0, initial=0)


def limit_motion(deltas, peak, hold, loop, pins=None):
    """Project correction differences into the wrist's remaining speed budget."""
    shape = deltas.shape
    out = gaussian_filter1d(deltas.reshape(len(deltas), -1), 2.5, loop).reshape(shape)
    anchors = np.repeat(hold[..., None], 2, axis=2)
    if pins is not None:
        out[:, :, 1] *= (1 - pins)[..., None]
        anchors[:, :, 1] |= pins >= 1 - 1e-6
    out[anchors] = 0
    for _ in range(24):
        previous = out.copy()
        for forward in (True, False):
            order = range(len(out)) if forward else range(len(out) - 1, -1, -1)
            for f in order:
                j = f - 1 if forward else f + 1
                if not loop and not 0 <= j < len(out):
                    continue
                j %= len(out)
                diff = out[f] - out[j]
                radius = .3 * peak[:, None]
                length = np.linalg.norm(diff, axis=2)
                scale = np.minimum(1, radius / np.maximum(length, 1e-12))
                out[f] = out[j] + diff * scale[..., None]
                out[f][anchors[f]] = 0
        if np.max(np.abs(previous - out)) < 1e-10:
            break
    return out


def solve_clip(rig, own, new, poses, loop, own_depth, before, frames, hold, pins, sleeves, tick=lambda: None):
    """Four measure/solve rounds, sparse spatial solves and full-frame blending."""
    n = len(poses)
    measured = [poses[f] for f in frames]
    bodies = BodyFrames(new, rig, poses, tick)
    bodies.ensure(frames)
    if before is None:
        before = [(bodies[f].depth(poses[f]), arm_radii(new, rig, poses[f]), None) for f in frames]
    d_before = np.array([m[0] for m in before])
    radius = np.array([m[1] for m in before])
    first = np.maximum(0, push_of(own_depth, d_before, radius, CLEAR) - DEAD)
    need = np.zeros_like(first)
    current = measured
    sparse = None
    peak = wrist_peak(rig, poses, loop)
    deltas = np.zeros((n, 2, 2, 3))
    for it in range(ITERATIONS):
        tick()
        depth_gradient = [bodies[f].depth_gradient(w) for f, w in zip(frames, current)]
        current_depth = np.array([m[0] for m in depth_gradient])
        rest = first if not it else push_of(own_depth, current_depth, radius, CLEAR)
        need = np.minimum(np.minimum(need + np.where(rest > 0.002, rest, 0),
                                     1.25 * first + 0.005 * (first > 0)), NEED_CAP)
        full = blend(_interpolate(frames, need, n), loop)
        full = rate_limit(full, peak, hold, loop)
        pts = np.array([rig.samples(w) for w in current])
        gradient = np.array([m[1] for m in depth_gradient])
        magnitude = np.maximum(np.linalg.norm(gradient, axis=3), 1e-6)
        directions = -gradient / magnitude[..., None]
        displacement = pts - np.array([rig.samples(w) for w in measured])
        target = ((directions * displacement).sum(axis=3)
                  + (current_depth - np.maximum(d_before, 0) + .001) / magnitude)
        regressed = np.maximum(current_depth, 0) - np.maximum(d_before, 0) > .0002
        target = np.where(regressed, target, -1e6)
        guard = (directions, target) if regressed.any() else None
        sparse = solve_many(rig, measured, full[frames], hold[frames], pins[frames], sleeves, guard, sparse)
        deltas = _interpolate(frames, sparse, n)
        deltas[hold] = 0
        current = _repose(rig, measured, deltas[frames], hold[frames])
    # Enforce the actual wrist-speed bound after the nonlinear length projection.
    # A single gain per arm preserves smooth motion, contacts and bone lengths.
    deltas = limit_motion(deltas, peak, hold, loop, pins)
    repair = repair_clearance(rig, measured, deltas[frames], hold[frames], pins[frames],
                              [bodies[f] for f in frames], tick)
    deltas += _interpolate(frames, repair - deltas[frames], n)
    fixed = _repose(rig, poses, deltas, hold)
    for _ in range(12):
        speed = wrist_peak(rig, fixed, loop)
        bad = speed > WRIST_SPEED_RATIO * peak + 1e-7
        if not bad.any():
            break
        deltas[:, bad] *= 0.5
        fixed = _repose(rig, poses, deltas, hold)
    else:
        deltas[:, bad] = 0
        fixed = _repose(rig, poses, deltas, hold)
    return guard_clip(rig, new, poses, deltas, hold, loop, peak, bodies, tick, frames)


def guard_clip(rig, drawn, poses, deltas, hold, loop, peak, bodies=None, tick=lambda: None, frames=None):
    """Guard max(D, 0) on all nine samples after every temporal/spatial step.

    Both arms share a frame gain: one arm must not worsen the other's cover.
    Gains only decrease; smoothing is followed by another spatial check because
    a smaller turn is not necessarily safe across a ray silhouette. The final
    fallback is the original clip, which satisfies both spatial and speed bounds.
    """
    bodies = bodies or [BodyFrame(drawn, rig, w) for w in poses]
    baseline = {}
    gains = np.ones(len(poses))
    gains[np.max(np.abs(deltas), axis=(1, 2, 3)) < 1e-7] = 0
    fixed = list(poses)
    checked = np.full(len(poses), np.nan)
    active = np.array(frames if frames is not None else range(len(poses)))
    for attempt in range(10):
        tick()
        changed = active[gains[active] != checked[active]]
        if isinstance(bodies, BodyFrames):
            bodies.ensure([f for f in changed if gains[f]])
        updated = _repose(rig, [poses[f] for f in changed],
                          deltas[changed] * gains[changed, None, None, None], hold[changed])
        bad = np.zeros(len(poses), bool)
        growth = np.zeros(len(poses))
        for f, w in zip(changed, updated):
            fixed[f] = w
            checked[f] = gains[f]
            if not gains[f]:
                continue
            if f not in baseline:
                baseline[f] = bodies[f].penetration(poses[f])
            depth = bodies[f].penetration(w)
            growth[f] = np.max(depth - baseline[f])
            bad[f] = growth[f] > GUARD_TOLERANCE - 2e-5
        if bad.any():
            gains[bad] *= np.minimum(.5, .0007 / np.maximum(growth[bad], 1e-9))
            gains[gains < 1 / 256] = 0
            if len(active) < len(poses):
                gains = np.minimum(gains, np.interp(np.arange(len(poses)), active, gains[active]))
            gains = np.minimum(gains, gaussian_filter1d(gains[:, None], SIGMA, loop)[:, 0])
            continue
        if len(active) < len(poses):
            # Try rollback gains on solve frames first. Acceptance still needs
            # an exact full-frame check after interpolation and smoothing.
            active = np.arange(len(poses))
            continue
        speed = wrist_peak(rig, fixed, loop)
        if (speed > WRIST_SPEED_RATIO * peak + 1e-7).any():
            wr0 = np.array([[rig.joints(w, s)[2] for s in range(2)] for w in poses])
            wr1 = np.array([[rig.joints(w, s)[2] for s in range(2)] for w in fixed])
            correction = wr1 - wr0
            steps = np.diff(correction, axis=0)
            if loop:
                steps = np.concatenate((steps, correction[:1] - correction[-1:]))
            added = np.linalg.norm(steps, axis=2).max(axis=0, initial=0)
            scale = np.where(added > 1e-9, .3 * peak / np.maximum(added, 1e-9), 1.)
            gains *= min(1., float(scale.min()))
            continue
        return fixed
    return list(poses)


@dataclass
class ArmClip:
    clip: object
    tree: dict
    rotations: dict
    gate_share: float
    held: np.ndarray
    pins: np.ndarray


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


def retarget(target, source, plan, clips, parts, *, step=None):
    """Changed clips only; an empty result leaves the existing build byte-identical.

    Runs in the caller's class 1 preview / class 2 check, outside the library
    lock. Gate five forearm/hand samples every third frame; solve gated clips every third frame;
    held arms are excluded from the spatial solve.
    """
    from .model_arm_clips import encode_rotations, edit_error

    started = time.perf_counter()
    tick = _Progress(step)
    rig = Rig(target.skeleton)
    if not rig.ok or len(rig.spine) < 2:
        return {}
    own, new = own_drawn(target), adapted_drawn(target, source, plan)
    contacts_data = None
    hashes = {zlib.crc32(rig.sk.nodes[n].path.encode()) for a in rig.arm for n in a[1:3]}
    gate_nodes = {n for a in rig.arm for n in a[1:]} | set(rig.spine) | set(rig.feet)
    for drawn in (own, new):
        rows = drawn.measure_mesh(True, drawn is new)[0]
        joints = drawn.joints[rows][drawn.weights[rows] > 0]
        gate_nodes.update(drawn.pose_bones[np.unique(joints)].tolist())
    changed = {}
    for clip in clips:
        tick()
        tree = rig.sk.graph.tree(clip)
        if not any(b["typeID"] == 4 and b["path"] in hashes for b in tree["m_ClipBindingConstant"]["genericBindings"]):
            continue
        sampler = ClipFrames(rig.sk, tree, gate_nodes)
        times = sampler.times
        if len(times) < 2:
            continue
        passes, sampled, _, share = clip_gate(target, rig, own, new, clip, sampler, tick)
        if not passes:
            continue
        if contacts_data is None:
            weapon_parts = [p for p in parts if p.role == "weapon" and p.mesh is not None]
            weapons = [own_drawn(ma.Costume(rig.sk, [p], [ma.skin_of(p.mesh)])) for p in weapon_parts]
            head_rows = head_core(target)
            old_girth, new_girth = girth(own, rig), girth(new, rig)
            sleeves = (2 * old_girth["fore"] / 1000, max(0, 2 * (new_girth["fore"] - old_girth["fore"]) / 1000))
            if not np.isfinite(sleeves).all():
                sleeves = (0, 0)
            contacts_data = weapons, head_rows, sleeves
        weapons, head_rows, sleeves = contacts_data
        sampler = ClipFrames(rig.sk, tree)
        poses = list(sampler.world(np.arange(len(times))))
        loop = bool(tree["m_MuscleClip"].get("m_LoopTime"))
        shown = weapon_visibility(rig, tree, weapon_parts, poses) if weapons else None
        hold, pins = contacts(rig, own, weapons, poses, head_rows, loop, shown)
        if hold.all():
            continue
        frames = sorted(set(range(0, len(times), SOLVE_EVERY)) | {len(times) - 1})
        depth = _own_depths(target, rig, own, clip, poses, frames)
        fixed = solve_clip(rig, own, new, poses, loop, depth, None, frames, hold, pins, sleeves, tick)
        rotations = {}
        for a in rig.arm:
            for bone in a[1:]:
                parent = rig.sk.nodes[bone].parent
                values = np.array([ms.quaternion(ma._rotation(np.linalg.inv(w[parent]) @ w[bone])) for w in fixed])
                rotations[zlib.crc32(rig.sk.nodes[bone].path.encode())] = ms.continuous(values)
        if max(np.linalg.norm(rig.samples(w) - rig.samples(v), axis=2).max() for w, v in zip(poses, fixed)) < 1e-6:
            continue
        import copy
        edited = copy.deepcopy(tree)
        encode_rotations(edited, rotations)
        problem = edit_error(tree, edited, rotations)
        if problem:
            raise ma.refuse("arm clip edit not verified", clip=clip.peek_name(), what=problem)
        changed[ma._key(clip)] = ArmClip(clip, edited, rotations, share,
                                         hold, pins)
    logger.info("Arm clearance: %d/%d clips corrected in %.3fs", len(changed), len(clips), time.perf_counter() - started)
    return changed


def bounds_poses(skeleton, changes):
    """Corrected arm probes also belong in the renderer's culling bounds."""
    out = []
    for change in changes.values():
        times, pose, _ = skeleton.clip_frames(change.tree)
        out.extend(pose(f) for f in sorted({0, len(times) // 2, len(times) - 1}))
    return out


class ClipFrames:
    """Vectorized Transform sampling; shader/material curves need no decoding.

    This samples the same cubics and dense grid as ``model.decode_clip``, with
    its endpoint epsilon, and composes a frame batch one hierarchy level at a
    time. No graph or reader is retained in the host-depth cache.
    """

    def __init__(self, skeleton, tree, nodes=None):
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
            m[:, 0, :3] = np.array([1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)]).T
            m[:, 1, :3] = np.array([2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)]).T
            m[:, 2, :3] = np.array([2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)]).T
            m[:, :3, :3] *= s[:, None]
            m[:, :3, 3] = p
        for i, node in enumerate(self.sk.nodes):
            if i in self.nodes and node.parent is not None:
                local[:, i] = local[:, node.parent] @ local[:, i]
        return local


GATE_SAMPLES = (3, 4, 5, 6, 7)


def near_core(drawn, rig, pose):
    """Can any driven sample need >20 mm even with the largest sleeve radius?"""
    points = rig.samples(pose)[:, GATE_SAMPLES].reshape(-1, 3)
    _, dirs, rho = radial(rig.axis(pose), points)
    lo, hi = drawn.core_bounds(pose)
    # D_new <= -20 mm implies need <= 20 mm because r_new <= ARM_CAP.
    ends = np.stack((points - (.5 * ARM_CAP - GATE_DEPTH) * dirs, points + CAP * dirs))
    ray_lo, ray_hi = ends.min(axis=0), ends.max(axis=0)
    return bool(((ray_hi >= lo).all(axis=1) & (ray_lo <= hi).all(axis=1) | (rho < .02)).any())


def clip_gate(target, rig, own, new, clip, sampler, tick=lambda: None):
    """Conservative gate bounds first, exact sleeve radii only for candidates.

    A clip exits as soon as the fifth is reached or cannot be reached. Only
    forearm/wrist/palm rays participate; upper arm and tip samples belong
    to the final guard and cannot start an arm correction by themselves.
    """
    frames = np.arange(0, len(sampler.times), GATE_EVERY)
    required = int(np.ceil(GATE_FRACTION * len(frames)))
    poses_batch = sampler.world(frames)
    possible = [f for f, w in zip(frames, poses_batch) if near_core(new, rig, w)]
    if len(possible) < required:
        return False, {}, {}, 0.
    poses = {int(f): w for f, w in zip(frames, poses_batch)}
    own_values = {}
    key = _depth_key(target, rig, clip)
    hits = 0
    for begin in range(0, len(possible), 8):
        tick()
        batch = possible[begin:begin + 8]
        bodies = depth_batch(new, rig, np.array([poses[int(f)] for f in batch]), GATE_SAMPLES)
        candidates = [int(f) for f, body in zip(batch, bodies) if (body + .5 * ARM_CAP).max() > GATE_DEPTH]
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
                return False, {}, {}, 0.
    return hits >= required, poses, own_values, hits / len(frames)


class BodyFrames:
    """Lazy torso poses: sparse trials do not skin intermediate frames."""

    def __init__(self, drawn, rig, poses, tick):
        self.drawn, self.rig, self.poses, self.tick = drawn, rig, poses, tick
        self.frames = {}
        self.rows = drawn.measure_mesh(True, False)[0]

    def __len__(self):
        return len(self.poses)

    def ensure(self, frames):
        missing = [f for f in frames if f not in self.frames]
        for begin in range(0, len(missing), 8):
            self.tick()
            batch = missing[begin:begin + 8]
            vertices = self.drawn.posed_batch([self.poses[f] for f in batch], self.rows)
            for f, v in zip(batch, vertices):
                self.frames[f] = BodyFrame(self.drawn, self.rig, self.poses[f], v)

    def __getitem__(self, frame):
        self.ensure([frame])
        return self.frames[frame]


class TriangleIndex:
    """Fixed spatial leaves; only their conservative bounds change with skinning.

    Partition once per costume. A query expands overlapping leaves into sparse
    ray/triangle pairs, then tests the individual boxes and exact triangles.
    Bounds follow the posed triangles, so spine/arm turns never approximate a
    surface intersection or invalidate the partition.
    """

    def __init__(self, triangles):
        centers = np.asarray(triangles).mean(axis=1)
        leaves = []

        def split(rows):
            if len(rows) <= 16:
                leaves.append(rows)
                return
            axis = np.ptp(centers[rows], axis=0).argmax()
            rows = rows[np.argsort(centers[rows, axis], kind='stable')]
            middle = len(rows) // 2
            split(rows[:middle])
            split(rows[middle:])

        if len(centers):
            split(np.arange(len(centers)))
        self.rows = np.array([np.pad(rows, (0, 16 - len(rows)), mode='edge') for rows in leaves], int).reshape(-1, 16)
        self.leaf_of = np.empty(len(centers), int)
        self.leaf_of[self.rows.ravel()] = np.repeat(np.arange(len(self.rows)), 16)

    def bounds(self, prepared, previous=None, leaves=None):
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

    def __init__(self, drawn, rig, pose, vertices=None):
        self.drawn, self.rig, self.pose = drawn, rig, pose
        self.rows, self.tris, _ = drawn.measure_mesh(True, False)
        self.vertices = drawn.posed(pose, self.rows) if vertices is None else vertices
        self.axis = rig.axis(pose)
        if not hasattr(drawn, '_body_layout'):
            if hasattr(drawn, 'pose_bones'):
                affected = {n for a in rig.arm for n in [a[1], *rig.sk.descendants(a[1])]}
                joint_affected = np.array([n in affected for n in drawn.pose_bones])
                changed = np.flatnonzero((joint_affected[drawn.joints[self.rows]] & (drawn.weights[self.rows] > 0)).any(axis=1))
            else:
                changed = np.arange(len(self.rows))
            drawn._body_layout = changed, np.flatnonzero(np.isin(self.tris, changed).any(axis=1))
        self.changed, self.tri_changed = drawn._body_layout
        self.prepared = self.prepare(self.vertices[self.tris])
        if not hasattr(drawn, '_body_index'):
            drawn._body_index = TriangleIndex(self.vertices[self.tris])
        self.leaves_changed = np.unique(drawn._body_index.leaf_of[self.tri_changed])
        self.prepared.append(drawn._body_index.bounds(self.prepared))
        self.baseline = None

    @staticmethod
    def prepare(triangles):
        t = np.asarray(triangles, np.float32)
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        return [a, b - a, c - a, np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)]

    def geometry(self, pose):
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
        pn = np.einsum('nij,nj->ni', project, normals)
        nd = (normals * dirs).sum(axis=1)
        tangent = normals - nd[:, None] * dirs
        jt = (tangent - np.einsum('nij,nj->ni', project, tangent)) / np.maximum(rho, 1e-9)[:, None]
        hit = np.linalg.norm(normals, axis=1) > 0
        grad = -dirs.copy()
        grad[hit] -= (pn[hit] + (depth[hit] + rho[hit])[:, None] * jt[hit]) / nd[hit, None]
        return depth.reshape(2, 9), grad.reshape(2, 9, 3)

    def penetration(self, pose):
        # The guard only needs positive depth. Rays start at each arm sample,
        # omitting the axis-to-sample segment and all exterior-clearance hits.
        if pose is self.pose and self.baseline is not None:
            return np.maximum(0, self.baseline)
        points = self.rig.samples(pose).reshape(-1, 3)
        _, dirs, _ = radial(self.axis, points)
        return self.query(points, dirs, np.zeros(len(points)), self.geometry(pose)).reshape(2, 9)

    @staticmethod
    def query(points, dirs, rho, prepared, normals=False, high=CAP):
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
            ray, leaf = np.nonzero((leaf_hi[None] >= ray_lo[:, None]).all(axis=2)
                                   & (leaf_lo[None] <= ray_hi[:, None]).all(axis=2))
            ray = np.repeat(ray, index.rows.shape[1])
            at = index.rows[leaf].ravel()
            near = (hi[at] >= ray_lo[ray]).all(axis=1) & (lo[at] <= ray_hi[ray]).all(axis=1)
            ray, at = ray[near], at[near]
        else:
            ray, at = np.nonzero((hi[None] >= ray_lo[:, None]).all(axis=2)
                                  & (lo[None] <= ray_hi[:, None]).all(axis=2))
        all_e1, all_e2 = e1, e2
        v0, e1, e2 = v0[at], e1[at], e2[at]
        p, d = points.astype(np.float32)[ray], dirs.astype(np.float32)[ray]
        dx, dy, dz = d.T
        ax, ay, az = e1.T
        bx, by, bz = e2.T
        hx, hy, hz = dy * bz - dz * by, dz * bx - dx * bz, dx * by - dy * bx
        determinant = ax * hx + ay * hy + az * hz
        with np.errstate(divide='ignore', invalid='ignore'):
            f = 1. / determinant
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



def arm_radii(drawn, rig, pose, indices=None):
    """Exact sleeve casts without re-skinning or re-casting the torso."""
    indices = np.arange(9) if indices is None else np.asarray(indices)
    points = rig.samples(pose)[:, indices]
    _, directions, _ = radial(rig.axis(pose), points.reshape(-1, 3))
    directions = directions.reshape(points.shape)
    if not hasattr(drawn, '_arm_views'):
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


def repair_clearance(rig, poses, deltas, hold, pins, bodies, tick=lambda: None):
    """A small shared bone turn resolves the non-driven sample constraints.

    Corrections are rotations of the actual bone directions. Projecting an XYZ
    elbow offset back onto its reach circle used to undo the clearance repair.
    Shared turns preserve smoothness; contacts and unreachable cases are left
    to the per-frame rollback guard. Repair displacement is capped at 30 mm.
    """
    if hold.any() or (pins > .01).any():
        return deltas
    baseline = np.maximum(0, np.array([b.depth(w) for b, w in zip(bodies, poses)]))
    original = deltas.copy()
    ew = np.array([.5, .75, 1, .75, .5, .25, 0, 0, 0])
    ww = np.array([0, 0, 0, .25, .5, .75, 1, 1, 1])
    joints0 = np.array([[rig.joints(w, side) for side in range(2)] for w in poses])
    for _ in range(8):
        tick()
        fixed = _repose(rig, poses, deltas, hold)
        measured = [b.depth_gradient(w) for b, w in zip(bodies, fixed)]
        depth = np.array([m[0] for m in measured])
        if (np.maximum(depth, 0) - baseline).max() < .0007:
            return deltas
        gradient = np.array([m[1] for m in measured])
        joints = np.array([[rig.joints(w, side) for side in range(2)] for w in fixed])
        upper = joints[:, :, 1] - joints[:, :, 0]
        fore = joints[:, :, 2] - joints[:, :, 1]
        lengths = np.stack((np.linalg.norm(upper, axis=2), np.linalg.norm(fore, axis=2)), axis=2).mean(axis=0)
        lever_u = ew[None, None, :, None] * upper[:, :, None] + ww[None, None, :, None] * (upper + fore)[:, :, None]
        lever_f = ww[None, None, :, None] * fore[:, :, None]
        coefficients = np.concatenate((np.cross(gradient, lever_u) / lengths[None, :, 0, None, None],
                                       np.cross(gradient, lever_f) / lengths[None, :, 1, None, None]), axis=3)
        rhs = depth - baseline + .0003
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
                shift *= .999
            turns = shift.reshape(2, 3) / lengths[side, :, None]
            matrices = []
            for turn in turns:
                angle = np.linalg.norm(turn)
                angle = min(angle, .1)
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
        if amount > .03:
            deltas = original + adjustment * (.03 / amount)
    return deltas


def depth_batch(drawn, rig, poses, indices):
    """Eight-frame gate batches amortize skinning and triangle preparation.

    Only core vertices are read. Ray/triangle work is still culled separately
    for each frame, so a wider batch never widens the torso casting region.
    """
    if not len(poses):
        return np.empty((0, 2, len(indices)))
    if not hasattr(drawn, 'bindings'):
        return np.array([BodyFrame(drawn, rig, w).depth(w, indices) for w in poses])
    rows, triangles, _ = drawn.measure_mesh(True, False)
    matrices = (poses[:, drawn.pose_bones] @ drawn.bindings).astype(np.float32)
    joints, weights = drawn.joints[rows], drawn.weights[rows].astype(np.float32)
    h = drawn.h[rows].astype(np.float32)
    vertices = np.zeros((len(poses), len(rows), 3), np.float32)
    for k in range(joints.shape[1]):
        used = weights[:, k] != 0
        if used.any():
            vertices[:, used] += weights[None, used, k:k + 1] * np.einsum(
                'fvij,vj->fvi', matrices[:, joints[used, k]], h[used])[..., :3]
    tri = vertices[:, triangles]
    a, b, c = tri[:, :, 0], tri[:, :, 1], tri[:, :, 2]
    prepared = [a, b - a, c - a, np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)]
    if not hasattr(drawn, '_body_index'):
        drawn._body_index = TriangleIndex(tri[0])
    out = []
    for f, pose in enumerate(poses):
        points = rig.samples(pose)[:, indices].reshape(-1, 3)
        _, direction, rho = radial(rig.axis(pose), points)
        frame = [p[f] for p in prepared]
        frame.append(drawn._body_index.bounds(frame))
        out.append(BodyFrame.query(points, direction, rho, frame).reshape(2, len(indices)))
    return np.array(out)
