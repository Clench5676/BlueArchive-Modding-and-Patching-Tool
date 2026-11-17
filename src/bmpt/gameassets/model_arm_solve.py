"""The arm pass's solve: contacts, the solve and guard of a clip, and ``retarget``, the pass over a costume's clips."""

from __future__ import annotations

import logging
import os
import threading
import time
import zlib
from concurrent import futures
from dataclasses import dataclass

import numpy as np

from . import model_adapt as ma
from . import model_skirts as ms
from .model_arm_frames import (
    GATE_DEPTH,
    GATE_FRACTION,
    GATE_SAMPLES,
    BodyFrame,
    BodyFrames,
    ClipFrames,
    _own_depths,
    _repose,
    arm_radii_many,
    clip_gate,
    depth_gradients,
    depths,
    penetrations,
    repair_clearance,
)
from .model_arms import (
    CLEAR,
    DEAD,
    DRIVEN,
    GUARD_TOLERANCE,
    ITERATIONS,
    MOVE_COST,
    NEED_CAP,
    PAIR_WEIGHT,
    PIN_COST,
    SIGMA,
    STEP,
    STEPS,
    Rig,
    adapted_drawn,
    blend,
    gaussian_filter1d,
    girth,
    head_core,
    head_pins,
    own_drawn,
    push_of,
    radial,
)


logger = logging.getLogger("bmpt.gameassets.model_arms")  # the arm pass logs under one name

SOLVE_EVERY = 3
WRIST_SPEED_RATIO = 1.3


class _Progress:
    def __init__(self, step):
        self.step, self.last = step, time.perf_counter()

    def __call__(self):
        if self.step is not None and time.perf_counter() - self.last >= 0.5:
            self.step()
            self.last = time.perf_counter()


def gated(need):
    """Check whether at least a fifth of frame-by-arm-by-sample needs exceed 20 mm at gate samples."""
    return bool(len(need) and np.mean(np.max(need[:, :, GATE_SAMPLES], axis=(1, 2)) > GATE_DEPTH) >= GATE_FRACTION)


def weapon_holds(relative, distance, loop=False):
    """An 11-frame stable grip in mount space, within 5 cm of the shown mesh.

    A hidden mount is represented by NaNs; it invalidates the whole window.
    At non-loop endpoints the window is clipped.

    Args:
        relative: Frame-by-hand palm positions in weapon-mount space, with NaNs for hidden mounts.
        distance: Frame-by-hand distances to the shown weapon mesh, in metres.
        loop: Whether the 11-frame stability window wraps across the clip seam.
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
    """Measure stable weapon grips and head-contact pins across a clip.

    Args:
        rig: Slot landmarks including the weapon mount and hand sample positions.
        own: Original costume view supplying the head surface.
        weapons: Skinning views of the slot's weapon parts.
        poses: Frame-by-node world matrices for contact sampling.
        rows: Head-dominated vertex indices in own.
        loop: Whether grip windows and pin blending wrap across the clip seam.
        shown: Optional frame-by-weapon visibility mask; None treats all weapons as visible.

    Returns:
        Frame-by-hand grip booleans and head-pin strengths from zero to one.
    """
    relative = np.full((len(poses), 2, 3), np.nan)
    distance = np.full((len(poses), 2), np.inf)
    if weapons and rig.weapon is not None and len(poses):
        stack = np.asarray(poses)
        mounts = stack[:, rig.weapon]
        valid = ~(np.abs(np.linalg.det(mounts[:, :3, :3])) < 1e-9)
        palms = rig.samples_many(stack)[:, :, 7]
        seen = np.zeros(len(poses), bool)
        for k, weapon in enumerate(weapons):
            frames = np.flatnonzero(valid & (shown[:, k] if shown is not None else True))
            seen[frames] = True
            sampled = np.arange(0, len(weapon.h), 5)
            for begin in range(0, len(frames), 16):
                at = frames[begin : begin + 16]
                vertices = weapon.posed_batch(stack[at], sampled)
                near = np.linalg.norm(palms[at, :, None] - vertices[:, None], axis=3).min(axis=2)
                distance[at] = np.minimum(distance[at], near)
        for f in np.flatnonzero(seen):
            relative[f] = (np.c_[palms[f], np.ones(2)] @ np.linalg.inv(mounts[f]).T)[:, :3]
    return weapon_holds(relative, distance, loop), head_pins(rig, own, poses, rows, loop)


def weapon_visibility(rig, tree, parts, poses):
    """Evaluate weapon renderer visibility including animated ancestor and renderer switches.

    Args:
        rig: Slot hierarchy supplying rest activity and binding-path hashes.
        tree: AnimationClip typetree supplying switch curves.
        parts: Weapon renderer parts, one column in the returned mask per part.
        poses: Sampled clip poses whose count sets the frame dimension.
    """
    from .model_glb import bind_toggles, TOGGLE_TYPES
    from .model_arm_clips import sample_curves

    by_hash = {zlib.crc32(n.path.encode()): i for i, n in enumerate(rig.sk.nodes)}
    bindings = tree["m_ClipBindingConstant"]["genericBindings"]
    wanted, index = set(), 0
    for binding in bindings:
        if binding["typeID"] in TOGGLE_TYPES:
            wanted.add(index)
        index += ms.binding_dims(binding)
    _, curves = sample_curves(tree, wanted)
    switches = {(node, kind): values for node, kind, values in bind_toggles(bindings, curves, by_hash)}
    shown = np.zeros((len(poses), len(parts)), bool)
    for k, part in enumerate(parts):
        renderer = bool(rig.sk.graph.tree(part.renderer).get("m_Enabled", 1))
        for f in range(len(poses)):
            values = switches.get((part.node, "renderer"))
            enabled = values[min(f, len(values) - 1)] >= 0.5 if values is not None else renderer
            node = part.node
            while enabled and node is not None:
                values = switches.get((node, "active"))
                # Node.active includes ancestors; local active state is read
                # from the GameObject so an animated parent can show a child.
                go = rig.sk.nodes[node].go
                active = (
                    bool(rig.sk.graph.tree(go).get("m_IsActive", 1)) if go is not None else rig.sk.nodes[node].active
                )
                enabled &= values[min(f, len(values) - 1)] >= 0.5 if values is not None else active
                node = rig.sk.nodes[node].parent
            shown[f, k] = enabled
    return shown


def push_directions(rig, w, pts):
    """Find outward arm directions projected perpendicular to chest up when spine landmarks exist.

    Args:
        rig: Slot body-axis and spine/neck landmark provider.
        w: Current slot-node world matrices.
        pts: Two-arm sample positions shaped as arms by samples by 3.
    """
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
    """Solve coupled elbow/wrist offsets with projected gradients over sparse frames.

    Args:
        rig: Slot arm landmarks and bone lengths.
        poses: Original world-matrix poses for the selected frames.
        needs: Frame-by-arm-by-sample outward distances in metres, capped by NEED_CAP.
        hold: Frame-by-arm grip flags preventing any correction.
        pins: Frame-by-arm head-contact strengths penalizing wrist motion.
        sleeves: Original forearm diameter and incoming diameter growth in metres for sleeve separation.
        guard: Optional outward directions and target distances preserving existing sample clearance.
        initial: Optional preceding elbow/wrist offsets, scaled down to seed the solve.

    Returns:
        Frame-by-arm-by-joint XYZ offsets, with elbow then wrist joints.
    """
    idx = [k for k, _, _ in DRIVEN]
    tw = np.array([x for _, x, _ in DRIVEN])[None, None, :, None]
    te = 1 - tw
    a = np.minimum(needs[:, :, idx], NEED_CAP).copy()
    a[:, :, 4:] *= (1 - pins)[..., None]
    free = (~hold)[..., None]
    pts = rig.samples_many(poses)
    p = pts[:, :, idx]
    diff = p[:, 0, :, None] - p[:, 1, None, :]
    c = np.linalg.norm(diff, axis=3)
    reach, grown = sleeves
    near = (
        np.clip((reach - c) / 0.02, 0, 1) * np.exp(-(c - c.min(axis=(1, 2))[:, None, None]) / 0.015)
        if grown > 0
        else np.zeros_like(c)
    )
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
        de, dw = 0.25 * initial[:, :, 0], 0.25 * initial[:, :, 1]
    guard_tw = np.array([0, 0, 0, 0.25, 0.5, 0.75, 1, 1, 1])[None, None, :, None]
    guard_te = np.array([0.5, 0.75, 1, 0.75, 0.5, 0.25, 0, 0, 0])[None, None, :, None]
    step = STEP if guard is None else 0.025
    steps = STEPS if initial is None else 3 * STEPS // 4
    pair = PAIR_WEIGHT * near
    wrist_cost = MOVE_COST + PIN_COST * pins[..., None]
    for iteration in range(steps + (60 if guard is not None else 0)):
        if guard is not None:
            direction, target = guard
            all_dp = guard_tw * dw[:, :, None] + guard_te * de[:, :, None]
            penalty = 4 * np.maximum(0, target - (direction * all_dp).sum(axis=3))
            gradient = -penalty[..., None] * direction
        if iteration >= steps:
            # The closing guard-only steps: the need and sleeve terms are not used.
            gw = (guard_tw * gradient).sum(axis=2)
            ge = (guard_te * gradient).sum(axis=2)
        else:
            dp = tw * dw[:, :, None] + te * de[:, :, None]
            out = (d * dp).sum(axis=3)
            v = np.maximum(0, a - out)
            # Penalize inward moves too; separating sleeves must not bury an arm.
            v += 4 * np.maximum(0, -out)
            g = -v[..., None] * d
            rel = dp[:, 0, :, None] - dp[:, 1, None, :]
            vp = pair * np.maximum(0, grown - (normal * rel).sum(axis=3))
            g[:, 0] -= (vp[..., None] * normal).sum(axis=2)
            g[:, 1] += (vp[..., None] * normal).sum(axis=1)
            gw = (tw * g).sum(axis=2)
            ge = (te * g).sum(axis=2)
            if guard is not None:
                gw += (guard_tw * gradient).sum(axis=2)
                ge += (guard_te * gradient).sum(axis=2)
            gw += wrist_cost * dw
            ge += MOVE_COST * de
        # Optimize the two unit bone directions. The upper-arm derivative
        # includes both joint gradients because turning it moves the wrist too.
        # Joint optimization avoids biasing other samples inward during projection.
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

    Args:
        values: Frame-by-arm-by-sample needs in metres, copied before limiting.
        peak: Original maximum wrist displacement per frame for each arm, in metres.
        hold: Frame-by-arm contact flags forcing needs to zero.
        loop: Whether forward/backward limits propagate across the clip seam.
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
        (count,) + values.shape[1:]
    )


def wrist_peak(rig, poses, loop):
    """Measure each wrist's largest frame-to-frame displacement in metres.

    Args:
        rig: Arm landmarks supplying wrist positions.
        poses: Ordered slot-node world-matrix frames.
        loop: Whether the last-to-first displacement contributes to the peak.
    """
    wrists = np.array([[rig.joints(w, s)[2] for s in range(2)] for w in poses])
    delta = np.diff(wrists, axis=0)
    if loop:
        delta = np.concatenate((delta, wrists[:1] - wrists[-1:]))
    return np.linalg.norm(delta, axis=2).max(axis=0, initial=0)


def limit_motion(deltas, peak, hold, loop, pins=None):
    """Project correction differences into the wrist's remaining speed budget.

    Args:
        deltas: Frame-by-arm-by-elbow/wrist XYZ offsets in metres.
        peak: Original maximum wrist displacement per frame for each arm.
        hold: Frame-by-arm grip flags fixing both corrections to zero.
        loop: Whether smoothing and speed projection wrap across the clip seam.
        pins: Optional head-contact strengths reducing wrist motion and anchoring fully pinned wrists.
    """
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
                radius = 0.3 * peak[:, None]
                length = np.linalg.norm(diff, axis=2)
                scale = np.minimum(1, radius / np.maximum(length, 1e-12))
                out[f] = out[j] + diff * scale[..., None]
                out[f][anchors[f]] = 0
        if np.max(np.abs(previous - out)) < 1e-10:
            break
    return out


def solve_clip(rig, own, new, poses, loop, own_depth, before, frames, hold, pins, sleeves, tick=lambda: None):
    """Correct a clip with four sparse measure/solve rounds, blending, and full-frame guards.

    Args:
        rig: Slot arm/body landmarks and original bone lengths.
        own: Original costume view retained by the solve interface.
        new: Incoming costume view used for torso depths and sleeve radii.
        poses: Original slot-node world matrices for every clip frame.
        loop: Whether temporal blending and speed checks wrap over the seam.
        own_depth: Original-costume depths aligned with the sparse frames.
        before: Optional premeasured depth/radius pairs for sparse frames; None measures them.
        frames: Frame indices selected for spatial solves, including the final frame.
        hold: Full-frame arm grip flags excluding held arms from correction.
        pins: Full-frame head-contact strengths limiting wrist movement.
        sleeves: Original forearm diameter and incoming diameter growth in metres.
        tick: No-argument cancellation/yield callback between solve rounds and body batches.

    Returns:
        Corrected world-matrix frames, falling back to original frames when guards fail.
    """
    n = len(poses)
    measured = [poses[f] for f in frames]
    bodies = BodyFrames(new, rig, poses, tick)
    bodies.ensure(frames)
    if before is None:
        d_before = depths([bodies[f] for f in frames], [poses[f] for f in frames])
        radius = arm_radii_many(new, rig, [poses[f] for f in frames])
    else:
        d_before = np.array([m[0] for m in before])
        radius = np.array([m[1] for m in before])
    first = np.maximum(0, push_of(own_depth, d_before, radius, CLEAR) - DEAD)
    need = np.zeros_like(first)
    current = measured
    sparse = None
    peak = wrist_peak(rig, poses, loop)
    deltas = np.zeros((n, 2, 2, 3))
    measured_points = rig.samples_many(measured)
    for it in range(ITERATIONS):
        tick()
        current_depth, gradient = depth_gradients([bodies[f] for f in frames], current)
        rest = first if not it else push_of(own_depth, current_depth, radius, CLEAR)
        need = np.minimum(
            np.minimum(need + np.where(rest > 0.002, rest, 0), 1.25 * first + 0.005 * (first > 0)), NEED_CAP
        )
        full = blend(_interpolate(frames, need, n), loop)
        full = rate_limit(full, peak, hold, loop)
        pts = rig.samples_many(current)
        magnitude = np.maximum(np.linalg.norm(gradient, axis=3), 1e-6)
        directions = -gradient / magnitude[..., None]
        displacement = pts - measured_points
        target = (directions * displacement).sum(axis=3) + (current_depth - np.maximum(d_before, 0) + 0.001) / magnitude
        regressed = np.maximum(current_depth, 0) - np.maximum(d_before, 0) > 0.0002
        target = np.where(regressed, target, -1e6)
        guard = (directions, target) if regressed.any() else None
        sparse = solve_many(rig, measured, full[frames], hold[frames], pins[frames], sleeves, guard, sparse)
        deltas = _interpolate(frames, sparse, n)
        deltas[hold] = 0
        current = _repose(rig, measured, deltas[frames], hold[frames])
    # Enforce the actual wrist-speed bound after the nonlinear length projection.
    # A single gain per arm preserves smooth motion, contacts and bone lengths.
    deltas = limit_motion(deltas, peak, hold, loop, pins)
    repair = repair_clearance(
        rig, measured, deltas[frames], hold[frames], pins[frames], [bodies[f] for f in frames], tick
    )
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
    """Guard positive torso depth at all nine samples after temporal and spatial corrections.

    Both arms share a frame gain: one arm must not worsen the other's cover.
    Gains only decrease; smoothing is followed by another spatial check because
    a smaller turn is not necessarily safe across a ray silhouette. The final
    fallback is the original clip, which satisfies both spatial and speed bounds.

    Args:
        rig: Slot arm landmarks used to apply corrections and measure wrist speed.
        drawn: Incoming costume view used when body frames have not been prepared.
        poses: Original full-frame world matrices forming the clearance baseline.
        deltas: Proposed frame-by-arm-by-elbow/wrist XYZ offsets in metres.
        hold: Frame-by-arm contact flags fixing held arms.
        loop: Whether gain smoothing and speed checks include the clip seam.
        peak: Original per-arm maximum wrist displacement per frame.
        bodies: Optional prepared torso frames; None prepares them from drawn and poses.
        tick: No-argument cancellation/yield callback before each guard attempt.
        frames: Optional initial sparse check indices; acceptance still requires all frames.
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
        updated = _repose(
            rig, [poses[f] for f in changed], deltas[changed] * gains[changed, None, None, None], hold[changed]
        )
        bad = np.zeros(len(poses), bool)
        growth = np.zeros(len(poses))
        for f, w in zip(changed, updated):
            fixed[f] = w
            checked[f] = gains[f]
        live = [k for k, f in enumerate(changed) if gains[f]]
        fresh = [changed[k] for k in live if changed[k] not in baseline]
        if fresh:
            baseline.update(zip(fresh, penetrations([bodies[f] for f in fresh], [poses[f] for f in fresh])))
        if live:
            depth = penetrations([bodies[changed[k]] for k in live], [updated[k] for k in live])
            for k, value in zip(live, depth):
                f = changed[k]
                growth[f] = np.max(value - baseline[f])
                bad[f] = growth[f] > GUARD_TOLERANCE - 2e-5
        if bad.any():
            gains[bad] *= np.minimum(0.5, 0.0007 / np.maximum(growth[bad], 1e-9))
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
            scale = np.where(added > 1e-9, 0.3 * peak / np.maximum(added, 1e-9), 1.0)
            gains *= min(1.0, float(scale.min()))
            continue
        return fixed
    return list(poses)


@dataclass
class ArmClip:
    """Carry an edited arm clip, expected rotations, gate share, and grip/head-contact measurements."""

    clip: object
    tree: dict
    rotations: dict
    gate_share: float
    held: np.ndarray
    pins: np.ndarray


# Solves of independent clips run on this many threads beside the caller's (numpy releases the GIL in the larger
# array operations); at least two cores stay free for the foreground preview and the rest of the App.
ARM_WORKERS = max(1, min(3, (os.cpu_count() or 2) - 2))


class _Stopped(Exception):
    """The caller's step stopped the arm pass while solves were running."""


@dataclass
class _Solve:
    clip: object
    tree: dict
    poses: list
    hold: np.ndarray
    pins: np.ndarray
    share: float
    future: object


def retarget(target, source, plan, clips, parts, *, step=None, progress=None):
    """Retarget only clips needing arm clearance, returning no edits for unchanged builds.

    Runs in the caller's class 1 preview / class 2 check, outside the library
    lock. Gate five forearm/hand samples every third frame; solve gated clips every third frame;
    held arms are excluded from the spatial solve. ``progress(done, count)`` is told how many clips are done, for
    the progress line's percentage.

    Gates, contacts and clip encoding run on the calling thread in clip order;
    the solves of gated clips run on up to ``ARM_WORKERS`` threads meanwhile.
    Only the calling thread calls ``step``: when it raises, the solves stop at
    their next round and the exception is passed on. Every result is the one a
    single thread computes: solves share no mutable state, and the lazily built
    sleeve partitions are built first from the pose the first solve would use.

    Args:
        target: Original slot costume defining the baseline surface and hierarchy.
        source: Incoming costume whose fitted shape may require arm clearance.
        plan: Adaptation mapping and fitted geometry used to bind source onto target.
        clips: Slot AnimationClip readers processed and encoded in their existing order.
        parts: Slot renderer parts, including weapons needed for contact detection.
        step: Optional no-argument caller-thread yield/cancellation callback; raising stops pending solves.
        progress: Optional callback receiving completed and total clip counts.

    Returns:
        Original clip graph keys mapped to ArmClip edits; an empty mapping leaves build bytes unchanged.

    Raises:
        ServiceError: Encoded arm curves fail read-back verification.
    """
    from .model_arm_clips import clone_tree, encode_rotations, edit_error

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
    pending = []
    done = 0
    stop = threading.Event()
    pool = None

    def stopped():
        if stop.is_set():
            raise _Stopped

    def report():
        if progress is not None:
            progress(done, len(clips))

    def finish(block):
        """Encode solved clips in clip order: the first one when it is ready, or waiting for it."""
        nonlocal done
        while pending and (block or pending[0].future.done()):
            job = pending.pop(0)
            while not job.future.done():
                futures.wait([job.future], timeout=0.1)
                tick()
            fixed = job.future.result()
            done += 1
            report()
            rotations = {}
            for a in rig.arm:
                for bone in a[1:]:
                    parent = rig.sk.nodes[bone].parent
                    values = np.array([ms.quaternion(ma._rotation(np.linalg.inv(w[parent]) @ w[bone])) for w in fixed])
                    rotations[zlib.crc32(rig.sk.nodes[bone].path.encode())] = ms.continuous(values)
            moved = zip(rig.samples_many(job.poses), rig.samples_many(fixed))
            if max(np.linalg.norm(w - v, axis=2).max() for w, v in moved) < 1e-6:
                continue
            edited = clone_tree(job.tree)
            encode_rotations(edited, rotations)
            problem = edit_error(job.tree, edited, rotations)
            if problem:
                raise ma.refuse("arm clip edit not verified", clip=job.clip.peek_name(), what=problem)
            changed[ma._key(job.clip)] = ArmClip(job.clip, edited, rotations, job.share, job.hold, job.pins)

    try:
        for clip in clips:
            tick()
            finish(False)
            report()
            done += 1  # submitted solves defer this count until encoding finishes
            tree = rig.sk.graph.tree(clip)
            if not any(
                b["typeID"] == 4 and b["path"] in hashes for b in tree["m_ClipBindingConstant"]["genericBindings"]
            ):
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
            if pool is None:
                arm_radii_many(new, rig, [poses[frames[0]]])
                pool = futures.ThreadPoolExecutor(ARM_WORKERS, thread_name_prefix="arm-solve")
            done -= 1
            future = pool.submit(
                solve_clip, rig, own, new, poses, loop, depth, None, frames, hold, pins, sleeves, stopped
            )
            pending.append(_Solve(clip, tree, poses, hold, pins, share, future))
            while len(pending) > ARM_WORKERS:
                finish(True)
        finish(True)
    except BaseException:
        stop.set()
        raise
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    logger.info(
        "Arm clearance: %d/%d clips corrected in %.3fs", len(changed), len(clips), time.perf_counter() - started
    )
    return changed
