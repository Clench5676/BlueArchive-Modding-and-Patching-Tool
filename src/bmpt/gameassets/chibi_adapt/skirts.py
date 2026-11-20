"""Skirts and hems (rules in ``chibi_hems``): keeping the legs inside the costume, springs, new bones' curves."""

from __future__ import annotations

import dataclasses
import logging
import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from ...core.progress_meter import QUIET, Meter
from .. import chibi_hems as hm
from .. import chibi_skirts as ms
from ..costume_cache import decode_clip
from ..chibi import _key
from ..chibi_glb import bind_tracks, euler_orders, euler_to_quat, UNITY_ORDER
from .costume import Costume, Fit, Flat, OwnMesh, Plan, flatten
from .skeleton import Node, Skeleton, _inverse, refuse, stretch_limit, trs


logger = logging.getLogger(__package__)


HEM_CLIPS = ("Formation_Idle", "Formation_Pickup", "Normal_Attack_Ing", "Exs", "Move_Ing", "Cafe_Walk", "Vital_Death")
HEM_FRAMES = 6  # frames measured per check clip
FIT_FRAMES = 12  # frames per clip of the costume's own clips, for the fit
LEG_SAMPLES = 700  # leg vertices measured, about
HEM_RULES = (("blend", 0.5), ("short", 0.5), ("short", 0.7), ("short", 1.0), ("fit", 0.5), ("fit", 0.7), ("fit", 1.0))
RAMPS = {"blend": (1.0, (1.0, 2.0)), "short": (0.3, (0.9, 1.3))}  # rise in thigh lengths, thigh -> calf change
LEG_KEYS = ("pelvis", "thigh L", "thigh R", "calf L", "calf R", "foot L", "foot R")
FIT_KEYS = LEG_KEYS[:5]
SPINE = re.compile(r"^Bip001 Spine\d*$")
REGROUP = "regroup "  # Optimize Physics: a rule built as if the leg-anchor groups set to ``follow`` were rigid


def legs_of(skeleton: Skeleton) -> dict[str, int] | None:
    """Find the Biped pelvis, thighs, calves, and feet by clip/IK names, or None if any are missing."""
    out = {}
    for key in LEG_KEYS:
        if key == "pelvis":
            name = "Bip001 Pelvis"
        else:
            bone, side = key.split()
            name = f"Bip001 {side} {bone.title()}"
        out[key] = skeleton.first(name)
    return out if None not in out.values() else None


def lower_anchors(skeleton: Skeleton, legs: Mapping[str, int]) -> set[int]:
    """Collect pelvis and leg Biped nodes whose secondary bones may carry leg cloth.

    Args:
        skeleton: Hierarchy supplying descendants below each thigh.
        legs: Complete pelvis/thigh/calf/foot map from legs_of.
    """
    out = {legs["pelvis"]}
    for side in "LR":
        thigh = legs[f"thigh {side}"]
        out |= {thigh} | {j for j in skeleton.descendants(thigh) if skeleton.nodes[j].name.startswith("Bip001")}
    return out


def followed_groups(plan_: Plan, skeleton: Skeleton, anchors: set[int]) -> dict[int, list[int]]:
    """Select followed leg-anchor groups for Optimize Physics to judge again by pass-through.

    Args:
        plan_: Group choices and member nodes from the initial mapping judgment.
        skeleton: Source hierarchy retained by the group-selection interface.
        anchors: Eligible pelvis/leg anchor nodes from lower_anchors.
    """
    return {
        anchor: members
        for anchor, members in plan_.groups.items()
        if members and anchor in anchors and plan_.choices.get(anchor) == "follow"
    }


def chain_root(skeleton: Skeleton, node: int) -> int:
    """Find the first secondary bone above node, immediately below its Biped anchor in skeleton."""
    while (parent := skeleton.nodes[node].parent) is not None and not skeleton.nodes[parent].name.startswith("Bip001"):
        node = parent
    return node


def cloth_mask(
    skeleton: Skeleton, dominant: np.ndarray, anchors: set[int], axis_distance: np.ndarray, hip_width: float
) -> np.ndarray:
    """Select leg cloth by secondary-bone anchors and median distance, excluding wings and tails.

    Args:
        skeleton: Source hierarchy supplying secondary chain roots and Biped anchors.
        dominant: Dominant skeleton node per vertex, with negative indices excluded.
        anchors: Eligible Biped anchors around the legs or lower-reaching spine cloth.
        axis_distance: Per-vertex distance to the nearest leg axis in metres.
        hip_width: Thigh-to-thigh distance in metres; chains must lie within 1.5 times this median distance.
    """
    chains = np.full(len(dominant), -1)
    for node in np.unique(dominant).tolist():
        if node < 0 or skeleton.nodes[node].name.startswith("Bip001"):
            continue
        if skeleton.biped_ancestor(node) in anchors:
            chains[dominant == node] = chain_root(skeleton, node)
    out = np.zeros(len(dominant), bool)
    for chain in np.unique(chains[chains >= 0]).tolist():
        members = chains == chain
        if float(np.median(axis_distance[members])) < 1.5 * hip_width:
            out |= members
    return out


def fit_own(
    skeleton: Skeleton, flat: Flat, on_rigid: np.ndarray, share: np.ndarray, legs: Mapping[str, int], clips: list[Any]
) -> np.ndarray | None:
    """Fit pelvis/thigh/calf weights reproducing the costume's own keyed rigid-cloth motion.

    Weights on FIT_KEYS reproduce where the costume's
    own clips put its rigid part (the vendor's keyed skirt bones): least squares over the sampled frames of every clip
    with Biped curves. None without such clips.

    Args:
        skeleton: Source hierarchy used to sample its own clips and rest skin matrices.
        flat: Source prefab-rest vertices and skeleton-node influences.
        on_rigid: Boolean mask selecting rigid-cloth influences in flat's weight rows.
        share: Total rigid-cloth weight per vertex; only positive-share vertices are fitted.
        legs: Source pelvis/thigh/calf/foot map supplying FIT_KEYS destination columns.
        clips: Source-owned AnimationClip readers with Biped motion.
    """
    rows = np.where(share > 1e-6)[0]
    rest = skeleton.world()
    bones = [legs[k] for k in FIT_KEYS]
    weights = np.where(on_rigid[rows], flat.weights[rows], 0.0)
    joints = np.where(on_rigid[rows], flat.joints[rows], bones[0])
    nodes = sorted(set(joints.ravel().tolist()) | set(bones))
    index = {n: k for k, n in enumerate(nodes)}
    local = np.vectorize(index.get, otypes=[int])(joints)
    inverse = np.array([np.linalg.inv(rest[n]) for n in nodes])
    points = flat.vertices[rows]
    homogeneous = np.c_[points, np.ones(len(points))]
    normal = hm.Normal(len(rows), len(bones))
    for clip in clips:
        poses, animated = skeleton.clip_poses(clip, FIT_FRAMES)
        if not any(skeleton.nodes[i].name.startswith("Bip001") for i in animated):
            continue
        for pose in poses:
            matrices = np.array([pose[n] for n in nodes]) @ inverse
            target = hm.skin(points, local, weights, matrices) / share[rows, None]
            basis = np.stack([(homogeneous @ matrices[index[b]].T)[:, :3] for b in bones], axis=2)
            normal.add(basis, target)
    if not normal.frames:
        return None
    fitted, residual = hm.fit_simplex(normal)
    logger.debug(
        "Hem fit: %d vertices over %d frames, rms residual %.1f mm",
        len(rows),
        normal.frames,
        1000 * float(residual.mean()) if len(residual) else 0.0,
    )
    out = np.zeros((len(flat.vertices), len(bones)))
    out[rows] = fitted
    return out


@dataclass
class Skirt:
    """Describe added chain bones and their solver for baking slot clip curves.

    Source chain nodes come parents first. Local TRS values are relative to a chain-parent
    index, or -1 for the slot anchor; base is the slot node index assigned to the first new bone.
    """

    sources: list[int]
    names: list[str]
    parents: list[int]
    locals: list[tuple]
    anchor: int
    solver: Any  # ms.Solver, ms.Spring or FaceRetarget
    base: int
    clips: int = 0  # clips given curves
    solved: dict[tuple[str, int], tuple[list[int], list[dict[int, np.ndarray]]]] = field(default_factory=dict)
    anchors: list[int] = field(default_factory=list)  # per bone, the slot node a root hangs off (empty: ``anchor``)
    riders: dict[int, int] = field(
        default_factory=dict
    )  # source node with no bone of its own -> the chain bone it goes with

    def node(self, k: int) -> int:
        """Return the destination slot node index for chain bone k."""
        return self.base + k

    def anchor_of(self, k: int) -> int:
        """Return chain bone k's individual slot anchor, falling back to the shared anchor."""
        return self.anchors[k] if self.anchors else self.anchor


@dataclass
class Hems:
    """Record the chosen hem rule, measurements, and skin/chain edits.

    Retain the rule ("rigid" when nothing moves), why nothing was measured, the stretch
    limit, the scores (rigid first) and, per source part, the rewritten vertices (rows, slot nodes, weights); for
    Rule B (rule "skirt" or "skirt smooth") the new bones. With Optimize Physics a rule may carry the ``REGROUP``
    prefix: it was built as if the leg-anchor groups the group choice set to ``follow`` were rigid (``regrouped``),
    and the first score, the default build's, is the baseline it must beat.
    """

    rule: str = "rigid"
    note: str = ""
    limit: float = 0.0
    scores: list[hm.Score] = field(default_factory=list)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = field(default_factory=list)
    skirt: Skirt | None = None
    regrouped: bool = False
    # Optimize Physics: leg vertices deep under the cloth moved toward the leg's axis (rows of the bound shape, their
    # new places in the prefab root's space), and the score that took it
    thin: tuple[np.ndarray, np.ndarray] | None = None
    thin_score: hm.Score | None = None
    springs: list[Skirt] = field(default_factory=list)  # Optimize Physics: chains that swing after their anchor
    face: Skirt | None = None  # another character's face bones following the slot's expressions (``face_retarget``)

    def score(self, rule: str) -> hm.Score | None:
        """Find the exact named hem rule's measured score, or None when it was not measured."""
        return next((s for s in self.scores if s.rule == rule), None)

    def summary(self) -> str:
        """Describe the chosen rule's pass-through, stretch, chain, and thinning results for logs."""
        if not self.scores:
            return f"rigid ({self.note})"
        rigid, chosen = self.scores[0], self.score(self.rule) or self.scores[0]
        extra = f", {len(self.skirt.solver.roots)} chains in {self.skirt.clips} clips" if self.skirt else ""
        if self.springs:
            extra += (
                f", {sum(len(s.solver.roots) for s in self.springs)} swinging chains in "
                + "/".join(str(s.clips) for s in self.springs)
                + " clips"
            )
        if self.thin is not None and self.thin_score is not None:
            extra += f", {len(self.thin[0])} leg vertices thinned under the cloth ({self.thin_score.mean:.1f}%)"
        return (
            f"{self.rule}: deep pass-through {rigid.mean:.1f}% -> {chosen.mean:.1f}%, stretch {rigid.stretch:.2f} -> "
            f"{chosen.stretch:.2f} (limit {self.limit:.2f}){extra}; per clip "
            + ", ".join(f"{clip} {rigid.deep[clip]:.0f}->{chosen.deep[clip]:.0f}" for clip in rigid.deep)
        )


@dataclass
class _SkirtCandidate:
    """A Rule B candidate.

    It holds dense weights over the measured columns and then the chains' bones, its solver, and the new bones those
    columns name (shared by the candidates of one build).
    """

    rule: str
    weights: np.ndarray
    solver: ms.Solver
    skirt: Skirt


def _skirt_candidates(
    ts: Skeleton,
    ss: Skeleton,
    plan_: Plan,
    flat: Flat,
    sv: np.ndarray,
    dominant: np.ndarray,
    cloth: np.ndarray,
    legs: np.ndarray,
    t_legs: Mapping[str, int],
    s_legs: Mapping[str, int],
    mapped: np.ndarray,
    columns: list[int],
    writable_rows: np.ndarray,
    anchors: Iterable[int] | None = None,
) -> list[_SkirtCandidate]:
    """Rule B's candidates and their new bones, for the skirt chains left rigid by the group choice.

    The candidates are "skirt" (the costume's weights) and "skirt smooth" (blended across the chains' seams). The skirt
    chains are those of the cloth around the legs that the group choice left rigid, hanging off the pelvis or another of
    ``anchors`` (source Biped bones; the pelvis alone by default): each chain's first bone hangs off the slot's bone of
    its own anchor (a coat's tails off the spine, a strap off a thigh). ``flat``: the costume in the source's space;
    ``sv``: its vertices in the shape bound on the slot, where each chain's bones go along with its cloth.
    """
    pelvis = s_legs["pelvis"]
    anchors = [
        a for a in dict.fromkeys(anchors if anchors is not None else [pelvis]) if plan_.choices.get(a) == "rigid"
    ]
    if not anchors:
        return []
    members = {n for a in anchors for n in plan_.groups.get(a, [])}
    roots = {chain_root(ss, int(n)) for n in np.unique(dominant[cloth]).tolist() if int(n) in members}
    skinned = {
        int(n)
        for n in np.unique(flat.joints[flat.weights > 0]).tolist()
        if int(n) in members and chain_root(ss, int(n)) in roots
    }
    wanted: set[int] = set()
    for n in skinned:
        while n not in wanted:
            wanted.add(n)
            if n in roots:
                break
            n = ss.nodes[n].parent
    sources = sorted(wanted)  # the skeleton lists parents first
    if not sources:
        return []
    new = {n: k for k, n in enumerate(sources)}
    anchor = plan_.mapping[pelvis]
    t_rest, s_rest = ts.world(), ss.world()
    parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
    # each root below the slot's bone of its own Biped anchor
    hangs = [plan_.mapping[ss.biped_ancestor(n)] if parents[k] < 0 else anchor for k, n in enumerate(sources)]
    # each chain moves with its cloth from the source's space to the bound shape (a refit carries it with the pelvis)
    carry = {}
    for k, n in enumerate(sources):
        if parents[k] < 0:
            share = (
                flat.weights
                * np.isin(flat.joints, [sources[j] for j in range(len(sources)) if chain_root(ss, sources[j]) == n])
            ).sum(axis=1)
            rows = np.where(share > 0.5)[0]
            carry[n] = ms.rigid_fit(flat.vertices[rows], sv[rows], share[rows]) if len(rows) >= 3 else np.eye(4)
    rest = np.array([carry[chain_root(ss, n)] @ s_rest[n] for n in sources])
    local = np.array([np.linalg.inv(rest[p] if p >= 0 else t_rest[hangs[k]]) @ rest[k] for k, p in enumerate(parents)])
    chains = ms.Chains(anchor, parents, local, rest, anchors=hangs)
    taken = {node.path for node in ts.nodes}
    names, paths, locals_ = [], [], []
    for k, n in enumerate(sources):
        name = ss.nodes[n].name
        parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[hangs[k]].path
        while f"{parent_path}/{name}" in taken:
            name += "_costume"
        names.append(name)
        paths.append(f"{parent_path}/{name}")
        taken.add(paths[-1])
        if parents[k] >= 0:
            locals_.append(ss.nodes[n].local)  # as it hangs in the costume's own skeleton
        else:
            m = local[k]
            scale = np.linalg.norm(m[:3, :3], axis=0)
            locals_.append((tuple(m[:3, 3]), tuple(ms.quaternion(m[:3, :3] / scale)), tuple(scale)))
    # dense weights over the columns, then the chains' bones
    width = len(columns) + len(sources)
    index = {c: k for k, c in enumerate(columns)}
    chain = np.vectorize(lambda n: new.get(int(n), -1), otypes=[int])(flat.joints)
    on = (chain >= 0) & (flat.weights > 0)
    cols = np.where(
        on, len(columns) + np.maximum(chain, 0), np.vectorize(lambda n: index.get(int(n), 0), otypes=[int])(mapped)
    )
    cols = np.where(flat.weights > 0, cols, 0)
    plain = hm.densify(cols, flat.weights, width)
    smooth = plain.copy()
    smooth[:, len(columns) :] = ms.smooth_weights(plain[:, len(columns) :], flat.edges)
    smooth[~writable_rows] = plain[~writable_rows]
    leg_joints = np.where(flat.weights[legs] > 0, mapped[legs], anchor)
    leg_set = ms.Legs(sv[legs], leg_joints, flat.weights[legs])
    skirt = Skirt(sources, names, parents, locals_, anchor, None, len(ts.nodes), anchors=hangs)
    out = []
    for rule, dense in (("skirt", plain), ("skirt smooth", smooth)):
        if rule == "skirt smooth" and np.abs(smooth - plain).max() < 1e-9:
            continue
        on_chains = dense[:, len(columns) :].sum(axis=1) > 1e-9
        # with the ring of cloth around them that the chains do not carry, so that the edges across the seam keep
        # their length too (a chain's head may move)
        ring = np.zeros(len(on_chains), bool)
        seam = on_chains[flat.edges[:, 0]] != on_chains[flat.edges[:, 1]]
        ring[flat.edges[seam].ravel()] = True
        rows = np.where(on_chains | ring)[0]
        joints4, weights4 = hm.keep_four(dense[rows])
        fixed = sorted({columns[c] for c in joints4[weights4 > 0].ravel().tolist() if c < len(columns)} | {anchor})
        slot = {c: fixed.index(columns[c]) + len(sources) for c in range(len(columns)) if columns[c] in fixed}
        joints = np.vectorize(
            lambda c: c - len(columns) if c >= len(columns) else slot.get(c, slot[index[anchor]]), otypes=[int]
        )(joints4)
        position = np.full(len(flat.vertices), -1)
        position[rows] = np.arange(len(rows))
        edges = position[flat.edges]
        edges = edges[(edges >= 0).all(axis=1)]
        cloth_set = ms.Cloth(sv[rows], joints, weights4, fixed, edges)
        out.append(_SkirtCandidate(rule, dense, ms.Solver(chains, cloth_set, leg_set, t_rest, t_legs["pelvis"]), skirt))
    if out:
        skirt.solver = out[0].solver
    return out


# Cost weights for the hems phase: setup, weight-only judging, chain solving, and leg thinning. Units are about
# 0.05 s, measured across costume pairs with Optimize Physics, candidate pruning, and shared chain solutions.
HEM_UNITS = (20, 1, 24, 12)


def hems(
    target: Costume,
    source: Costume,
    plan_: Plan,
    t_clips: list[Any],
    s_clips: list[Any],
    writable: list[bool],
    physics: bool = False,
    shapes_writable: bool = False,
    meter: Meter = QUIET,
    step: Callable[[], None] = lambda: None,
) -> Hems:
    """Choose measured hem weights and optional leg-driven chain bones within the stretch limit.

    Rule A supplies new skin weights for the cloth the group choice left rigid around the legs, and, with
    ``physics`` (Optimize Physics), Rule B: the skirt chains as new bones turned by the legs in every
    frame; picked by measurement under the slot's own clips (see ``chibi_hems`` and ``chibi_skirts``). ``writable``:
    per source part, whether its mesh's skin can be rewritten.

    With ``physics`` the leg-anchor groups the group choice set to ``follow`` are judged again by pass-through: both
    rules are also built as if those groups were rigid (named with the ``REGROUP`` prefix), and ``chibi_hems.choose``
    compares them with the follow result, the first score, which they must beat within the same stretch ceiling. A build
    without ``physics`` never looks at them. ``meter`` is told how far the judging is, the rules weighed by their cost
    (``HEM_UNITS``; the build's "hems" phase).

    This runs in the caller's class 1 preview or class 2/3 build, with no new
    locks or workers. ``step`` yields or cancels between clips and solver
    frames (normally under 0.1 s); cache locks hold only LRU bookkeeping.

    Args:
        target: Slot costume defining leg landmarks, reference cloth, and baseline stretch.
        source: Incoming costume supplying leg vertices, cloth, skin weights, and secondary chains.
        plan_: Initial bone mapping, group choices, and fitted bound shape.
        t_clips: Slot clips under which candidate pass-through and stretch are measured.
        s_clips: Source clips whose keyed cloth motion supplies least-squares weight candidates.
        writable: Per-source-part flags permitting skin-weight rewrites.
        physics: Whether to consider new skirt bones and regroup followed leg-anchor chains.
        shapes_writable: Whether fitted geometry can also be rewritten, allowing leg thinning with physics.
        meter: Progress meter receiving cost-weighted updates in the hems phase.
        step: No-argument yield/cancellation callback between clips and solver frames.
    """
    ts, ss = target.skeleton, source.skeleton
    t_legs, s_legs = legs_of(ts), legs_of(ss)
    if t_legs is None or s_legs is None:
        return Hems(note="no Biped legs")
    s_anchors = lower_anchors(ss, s_legs)
    rigid = sorted(
        {
            n
            for anchor, members in plan_.groups.items()
            if anchor in s_anchors and plan_.choices.get(anchor) == "rigid"
            for n in members
        }
    )
    followed = followed_groups(plan_, ss, s_anchors) if physics else {}
    flat = flatten(source, ss.world())  # the source's space, for the fit to its own clips
    on_rigid = np.isin(flat.joints, rigid) & (flat.weights > 0)
    share = (flat.weights * on_rigid).sum(axis=1)
    # Optimize Physics: the same cloth with the followed groups rigid as well
    on_regroup = (
        np.isin(flat.joints, sorted({*rigid, *(n for members in followed.values() for n in members)}))
        & (flat.weights > 0)
        if followed
        else None
    )
    share_regroup = (flat.weights * on_regroup).sum(axis=1) if followed else share
    if not (share > 1e-6).any() and not (share_regroup > 1e-6).any():
        return Hems(note="no rigid cloth")
    rows_ok = np.concatenate([np.full(len(skin.vertices), ok) for skin, ok in zip(source.skins, writable)])
    on_rigid &= rows_ok[:, None]
    share = np.where(rows_ok, share, 0.0)
    if followed:
        on_regroup &= rows_ok[:, None]
        share_regroup = np.where(rows_ok, share_regroup, 0.0)

    # the slot's legs at rest; heights along Unity's vertical (+Y of the prefab's space), from the pelvis
    t_rest = ts.world()
    at = {key: t_rest[node][:3, 3] for key, node in t_legs.items()}
    hip_width = float(np.linalg.norm(at["thigh L"] - at["thigh R"]))
    up = np.array([0.0, 1.0, 0.0])
    if hip_width < 1e-4 or float((at["pelvis"] - (at["foot L"] + at["foot R"]) / 2) @ up) < 1e-4:
        return Hems(note="no legs")
    hip = float(((at["thigh L"] + at["thigh R"]) / 2 - at["pelvis"]) @ up)
    knee = float(((at["calf L"] + at["calf R"]) / 2 - at["pelvis"]) @ up)
    if hip - knee < 1e-4:
        return Hems(note="no legs")
    sv = plan_.fit.vertices if plan_.fit is not None else flat.vertices  # the shape bound on the slot
    left = hm.leg_distance(sv, at["thigh L"], at["calf L"], at["foot L"])
    right = hm.leg_distance(sv, at["thigh R"], at["calf R"], at["foot R"])
    axis = np.minimum(left, right)

    dominant = flat.joints[np.arange(len(sv)), np.argmax(flat.weights, axis=1)]
    all_legs = np.where(np.isin(dominant, sorted(s_anchors - {s_legs["pelvis"]})))[0]
    legs = all_legs[:: max(1, len(all_legs) // LEG_SAMPLES)]
    cloth = cloth_mask(ss, dominant, s_anchors, axis, hip_width)
    chain_anchors = [s_legs["pelvis"]]
    if physics:  # Optimize Physics: also the chains off the spine whose cloth reaches below the hips (a coat's tails)
        spines = {i for i, node in enumerate(ss.nodes) if SPINE.match(node.name)}
        wide = cloth_mask(ss, dominant, s_anchors | spines, axis, hip_width) & ~cloth
        hips = float(((at["thigh L"] + at["thigh R"]) / 2) @ up)
        for root in {chain_root(ss, int(n)) for n in np.unique(dominant[wide]).tolist()}:
            rows = wide & np.isin(dominant, [root, *ss.descendants(root)])
            if rows.any() and float((sv[rows] @ up).min()) < hips:
                cloth |= rows
        chain_anchors += sorted(spines) + [s_legs["thigh L"], s_legs["thigh R"]]
    triangles = flat.triangles[cloth[flat.triangles].all(axis=1)] if len(flat.triangles) else flat.triangles
    edges = flat.edges[cloth[flat.edges].all(axis=1)]
    if not len(legs) or not len(triangles):
        return Hems(note="no cloth around the legs")
    state, _ = hm.cover(sv, legs, triangles, at["pelvis"], up)
    covered = legs[state == 1]
    logger.debug(
        "Hem: %d cloth vertices (%d triangles), %d rigid-weighted, %d of %d sampled leg vertices covered",
        int(cloth.sum()),
        len(triangles),
        int((share > 1e-6).sum()),
        len(covered),
        len(legs),
    )
    if not len(covered):
        return Hems(note="no leg under the cloth")
    own = OwnMesh.of(target)
    t_axis = np.minimum(
        hm.leg_distance(own.rest, at["thigh L"], at["calf L"], at["foot L"]),
        hm.leg_distance(own.rest, at["thigh R"], at["calf R"], at["foot R"]),
    )
    t_cloth = cloth_mask(ts, own.dominant, lower_anchors(ts, t_legs), t_axis, hip_width)
    t_edges = own.edges[t_cloth[own.edges].all(axis=1)]

    # dense weights per slot node, with the rigid part apart
    mapped = np.vectorize(lambda n: plan_.mapping.get(int(n), 0), otypes=[int])(flat.joints)
    used = set(mapped[flat.weights > 0].tolist())
    if followed:  # the followed groups' secondary bones on the anchor's slot node, as a rigid group is mapped
        regroup = {
            **plan_.mapping,
            **{n: plan_.mapping[anchor] for anchor, members in followed.items() for n in members},
        }
        mapped_regroup = np.vectorize(lambda n: regroup.get(int(n), 0), otypes=[int])(flat.joints)
        used |= set(mapped_regroup[flat.weights > 0].tolist())
    columns = sorted(used | {t_legs[k] for k in FIT_KEYS})
    index = {n: k for k, n in enumerate(columns)}
    column_of = lambda nodes: np.vectorize(index.get, otypes=[int])(np.where(flat.weights > 0, nodes, columns[0]))  # noqa: E731
    cols = column_of(mapped)
    dense = hm.densify(cols, flat.weights, len(columns))
    rigid_part = hm.densify(cols, np.where(on_rigid, flat.weights, 0.0), len(columns))

    # the rules: Rule A where a part's skin can be rewritten, Rule B (its smooth variant likewise)
    rules: list[tuple[str, np.ndarray, ms.Solver | None]] = [("rigid", dense, None)]
    if (share > 1e-6).any() or (followed and (share_regroup > 1e-6).any()):
        height = (hip - (sv - at["pelvis"]) @ up) / (hip - knee)
        near = 1 - hm.smoothstep(hip_width, 2 * hip_width, axis)
        sigma = hip_width / 2
        close_l, close_r = np.exp(-((left / sigma) ** 2)), np.exp(-((right / sigma) ** 2))
        ramp_columns = [index[t_legs[k]] for k in ("thigh L", "thigh R", "calf L", "calf R")]
        fit_columns = [index[t_legs[k]] for k in FIT_KEYS]

        def rule_a(base: np.ndarray, part: np.ndarray, covered_share: np.ndarray, on: np.ndarray, prefix: str):
            fitted = fit_own(ss, flat, on, covered_share, s_legs, s_clips)
            out = []
            for name, alpha in HEM_RULES:
                if name == "fit":
                    if fitted is None:
                        continue
                    weights = hm.reweight(base, part, alpha * (covered_share > 1e-6), fitted, fit_columns)
                else:
                    top, change = RAMPS[name]
                    fraction, moves = hm.ramp(height, near, close_l, close_r, alpha, top, change)
                    weights = hm.reweight(base, part, fraction, moves, ramp_columns)
                out.append((f"{prefix}{name} {alpha}", weights, None))
            return out

        if (share > 1e-6).any():
            rules += rule_a(dense, rigid_part, share, on_rigid, "")
    skirt_rules = (
        _skirt_candidates(
            ts, ss, plan_, flat, sv, dominant, cloth, all_legs, t_legs, s_legs, mapped, columns, rows_ok, chain_anchors
        )
        if physics
        else []
    )
    rules += [(c.rule, c.weights, c.solver) for c in skirt_rules]
    if followed:
        # the same rules with the followed groups rigid: the pelvis group's chains only where it was followed
        cols_regroup = column_of(mapped_regroup)
        dense_regroup = hm.densify(cols_regroup, flat.weights, len(columns))
        part_regroup = hm.densify(cols_regroup, np.where(on_regroup, flat.weights, 0.0), len(columns))
        if (share_regroup > 1e-6).any():
            rules += rule_a(dense_regroup, part_regroup, share_regroup, on_regroup, REGROUP)
        if s_legs["pelvis"] in followed:
            lowered = dataclasses.replace(
                plan_, mapping=regroup, choices={**plan_.choices, **dict.fromkeys(followed, "rigid")}
            )
            # their own new bones: the chains of the groups made rigid join the default build's
            again = _skirt_candidates(
                ts,
                ss,
                lowered,
                flat,
                sv,
                dominant,
                cloth,
                all_legs,
                t_legs,
                s_legs,
                mapped_regroup,
                columns,
                rows_ok,
                chain_anchors,
            )
            again = [dataclasses.replace(c, rule=REGROUP + c.rule) for c in again]
            skirt_rules += again
            rules += [(c.rule, c.weights, c.solver) for c in again]
    if len(rules) == 1:
        return Hems(note="skin not writable")

    names = [(c.peek_name() or "").lower() for c in t_clips]
    checks = []
    inverse = np.array([np.linalg.inv(t_rest[c]) for c in columns])
    rest_rotation = np.linalg.inv(t_rest[t_legs["pelvis"]][:3, :3])
    for suffix in HEM_CLIPS:
        clip = next((c for c, n in zip(t_clips, names) if n.endswith("_" + suffix.lower())), None)
        if clip is not None:
            tree = clip.read_typetree()
            prepared = ts.clip_frames(tree, cache=True)
            times, pose_at, _ = prepared
            frames = []
            for frame in measured_frames(len(times)):
                pose = pose_at(frame)
                frames.append(
                    (
                        frame,
                        np.array([pose[c] for c in columns]) @ inverse,
                        pose[t_legs["pelvis"]][:3, 3],
                        pose[t_legs["pelvis"]][:3, :3] @ rest_rotation @ up,
                        pose,
                    )
                )
            checks.append((suffix, clip, tree, prepared, frames))
    if not checks:
        return Hems(note="no check clips")
    limit = max(
        hm.STRETCH_FLOOR,
        max(hm.stretch_p99(own.rest, own.posed(pose), t_edges) for *_, frames in checks for *_, pose in frames),
    )
    scores, four, solved = [], {}, {}

    def judged(
        rule: str,
        joints4: np.ndarray,
        weights4: np.ndarray,
        solver: ms.Solver | None,
        points: np.ndarray,
        prune: bool = False,
    ) -> hm.Score:
        deep, worst = {}, 1.0
        for suffix, clip, tree, prepared, frames in checks:
            step()
            deep[suffix] = 0.0
            if solver is not None:  # the chains' turns in time, as the clip will hold them
                if (solver, _key(clip)) not in solved:
                    solved[(solver, _key(clip))] = skirt_sequence(ts, solver, tree, prepared=prepared, step=step)
                turns = dict(zip(*solved[(solver, _key(clip))]))
            for frame, matrices, centre, carried, pose in frames:
                if solver is not None:
                    matrices = np.concatenate([matrices, solver.posed(pose, turns[frame]) @ solver.inverse])
                posed = hm.skin(points, joints4, weights4, matrices)
                state, depth = hm.cover(posed, covered, triangles, centre, carried)
                deep[suffix] = max(deep[suffix], hm.deep_share(state, depth))
                worst = max(worst, hm.stretch_p99(points, posed, edges))
            if prune and scores and hm.cannot_win(hm.Score(rule, deep, worst), scores, limit):
                return hm.Score(rule, deep, worst, complete=False)
        return hm.Score(rule, deep, worst)

    setup, plain, chained, thin = HEM_UNITS
    costs = [chained if solver is not None else plain for _, _, solver in rules]
    total, done = setup + sum(costs) + (thin if physics and shapes_writable else 0), setup
    for (rule, weights, solver), cost in zip(rules, costs):
        meter.phase("hems", done, total)
        joints4, weights4 = hm.keep_four(weights)
        scores.append(judged(rule, joints4, weights4, solver, sv, prune=True))
        four[rule] = (joints4, weights4)
        done += cost
    meter.phase("hems", done, total)
    best = hm.choose(scores, limit)
    result = Hems(best.rule, "", limit, scores)
    if physics and shapes_writable:
        tried = []

        def judged_thin(*args: Any) -> hm.Score:  # one judgement per factor tried (THIN_FACTORS)
            meter.phase("hems", done + thin * len(tried) / len(THIN_FACTORS), total)
            tried.append(1)
            return judged(*args)

        _thin_legs(
            result,
            best,
            judged_thin,
            four[best.rule],
            next(solver for rule, _, solver in rules if rule == best.rule),
            sv,
            all_legs,
            triangles,
            at,
            up,
        )
    if best.rule == "rigid":
        return result
    joints4, weights4 = four[best.rule]
    chosen = next(weights for rule, weights, _ in rules if rule == best.rule)
    bounds = [*flat.starts, len(sv)]
    result.regrouped = best.rule.startswith(REGROUP)
    if best.rule.removeprefix(REGROUP).startswith("skirt"):
        candidate = next(c for c in skirt_rules if c.rule == best.rule)
        skirt = candidate.skirt  # the chosen build's bones: a regrouped build may have more than the default one
        result.skirt = skirt
        skirt.solver = candidate.solver
        skirt.solved = {key: value for (solver, key), value in solved.items() if solver is candidate.solver}
        nodes = np.array(columns + [skirt.node(k) for k in range(len(skirt.sources))])
        if result.regrouped:  # every vertex that differs from the follow result is written: the groups' other bones too
            changed = np.abs(chosen - np.pad(dense, ((0, 0), (0, len(skirt.sources))))).max(axis=1) > 1e-9
        else:
            plain = skirt_rules[0].weights
            changed = np.abs(chosen - plain).max(axis=1) > 1e-9  # the smooth variant rewrites the blended vertices
    else:
        nodes = np.array(columns)
        changed = np.abs(chosen - dense).max(axis=1) > 1e-9
    for k in range(len(source.parts)):
        rows = np.where(changed[bounds[k] : bounds[k + 1]])[0]
        result.parts.append((rows, nodes[joints4[bounds[k] + rows]], weights4[bounds[k] + rows]) if len(rows) else None)
    return result


RIGID_ROUNDS = 8  # stiffening rounds at most
RIGID_STEPS = (0.5, 1.0)  # how far a round moves the worst vertices' weights toward their anchor
RIGID_SHARE = 99.5  # the edges past this percentile of stretch (and past the limit) are the worst


def rigidify(
    target: Costume, source: Costume, plan_: Plan, probes: list[list[np.ndarray]], hem: Hems, writable: list[bool]
) -> None:
    """Reduce excessive stretch by blending worst-edge weights toward their mapped rigid anchors.

    For a costume past the stretch limit (``plan.unfit``), vertices of its worst stretched edges under the slot's
    probes get their weights blended toward the slot bone their anchor maps to (as a rigid group is mapped), round by
    round while the stretch falls, until it is within the limit or nothing improves; kept only when it is better than
    before. The new weights join the hem's (``hem.parts``) and the plan takes the stretch reached. Parts whose skin
    cannot be rewritten and Rule B's builds are left as they are.

    Args:
        target: Slot costume supplying rest bones and destination skin matrices.
        source: Incoming costume supplying topology, influences, and anchor relationships.
        plan_: Mutable mapping/stretch verdict updated only when stiffening improves it.
        probes: Slot world poses measuring candidate edge stretch.
        hem: Mutable hem result whose existing weight edits are merged with stiffened rows.
        writable: Per-source-part flags restricting which skin rows may change.
    """
    if not plan_.unfit or plan_.fit is None or hem.skirt is not None:
        return
    ts, ss = target.skeleton, source.skeleton
    s_rest, t_rest = ss.world(), ts.world()
    flat = flatten(source, s_rest)
    sv = plan_.fit.vertices
    reference = sv if plan_.fit.shape == "stored" else flat.vertices
    mapped = np.vectorize(lambda n: plan_.mapping.get(int(n), 0), otypes=[int])(flat.joints)
    bounds = [*flat.starts, len(sv)]
    rows_ok = np.concatenate([np.full(bounds[k + 1] - bounds[k], ok) for k, ok in enumerate(writable)])
    # each vertex's anchor on the slot: its dominant bone's Biped bone (itself for a Biped bone), mapped
    dominant = flat.joints[np.arange(len(sv)), np.argmax(flat.weights, axis=1)]
    anchors = {}
    for n in set(dominant.tolist()) - {-1}:
        a = n if ss.nodes[n].name.startswith("Bip001") else ss.biped_ancestor(n)
        anchors[n] = plan_.mapping.get(a if a is not None else n, 0)
    anchor = np.array([anchors.get(int(n), 0) for n in dominant])
    hem_rows: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = list(hem.parts) + [None] * (
        len(source.parts) - len(hem.parts)
    )
    columns = sorted(
        set(mapped[flat.weights > 0].tolist())
        | set(anchor.tolist())
        | {int(n) for change in hem_rows if change is not None for n in change[1].ravel().tolist()}
    )
    index = {c: k for k, c in enumerate(columns)}
    cols = np.vectorize(lambda n: index.get(int(n), 0), otypes=[int])(np.where(flat.weights > 0, mapped, columns[0]))
    dense = hm.densify(cols, flat.weights, len(columns))
    for k, change in enumerate(hem_rows):  # the hem's weights first, where it rewrote them
        if change is not None:
            rows, nodes, weights = change
            dense[bounds[k] + rows] = hm.densify(np.vectorize(index.get, otypes=[int])(nodes), weights, len(columns))
    start = dense.copy()
    matrices = [
        np.array([pose[c] for c in columns]) @ np.array([_inverse(t_rest[c], ts.nodes[c].name) for c in columns])
        for pose in [t_rest, *probes]
    ]
    edges = flat.edges
    weighted = flat.weights.sum(axis=1) > 1e-9

    def measure(weights: np.ndarray) -> tuple[float, np.ndarray]:
        joints4, weights4 = hm.keep_four(weights)
        worst = np.ones(len(edges))
        l0 = np.linalg.norm(reference[edges[:, 0]] - reference[edges[:, 1]], axis=1)
        ok = l0 > 1e-6
        for m in matrices:
            posed = hm.skin(sv, joints4, weights4, m)
            l1 = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)
            ratio = np.where(ok, l1 / np.maximum(l0, 1e-12), 1.0)
            worst = np.maximum(worst, np.maximum(ratio, 1 / np.maximum(ratio, 1e-6)))
        return (float(np.percentile(worst[ok], 99.9)) if ok.any() else 1.0), worst

    limit = stretch_limit(plan_.own)
    before, worst = measure(dense)
    now = before
    onehot = np.zeros_like(dense)
    onehot[np.arange(len(sv)), [index[int(a)] for a in anchor]] = 1.0
    for _ in range(RIGID_ROUNDS):
        if now <= limit:
            break
        cut = max(limit, float(np.percentile(worst, RIGID_SHARE)))
        rows = np.unique(edges[worst > cut].ravel())
        rows = rows[rows_ok[rows] & weighted[rows]]
        if not len(rows):
            break
        best = None
        for step in RIGID_STEPS:
            trial = dense.copy()
            trial[rows] = (1 - step) * dense[rows] + step * onehot[rows]
            value, trial_worst = measure(trial)
            if best is None or value < best[0]:
                best = (value, trial, trial_worst)
        if best[0] >= now * (1 - 1e-3):
            break
        now, dense, worst = best
    if now >= before * (1 - 1e-3):
        plan_.repair = f"stiffening did not help ({before:.2f})"
        return
    changed = np.abs(dense - start).max(axis=1) > 1e-9
    nodes = np.array(columns)
    joints4, weights4 = hm.keep_four(dense)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = []
    for k in range(len(source.parts)):
        mine = changed[bounds[k] : bounds[k + 1]]
        if hem_rows[k] is not None:  # retain hem edits alongside stiffened weights
            mine = mine.copy()
            mine[hem_rows[k][0]] = True
        rows = np.where(mine)[0]
        parts.append((rows, nodes[joints4[bounds[k] + rows]], weights4[bounds[k] + rows]) if len(rows) else None)
    hem.parts = parts
    plan_.repair = f"worst edges stiffened, stretch {before:.2f} -> {now:.2f} (limit {limit:.2f})"
    plan_.stretch, plan_.unfit = now, now > limit


THIN_DEPTH = 0.01  # a leg vertex this far inside the cloth at rest (metres) or more may be thinned
THIN_FACTORS = (0.85, 0.7)  # how much of its distance to the leg's axis a thinned vertex keeps, tried in order
THIN_GAIN = 1.0  # the deep pass-through, in points, thinning must take off to be kept


def _thin_legs(
    result: Hems,
    best: hm.Score,
    judged: Callable,
    four: tuple[np.ndarray, np.ndarray],
    solver: ms.Solver | None,
    sv: np.ndarray,
    legs: np.ndarray,
    triangles: np.ndarray,
    at: Mapping[str, np.ndarray],
    up: np.ndarray,
) -> None:
    """Optimize Physics: move the costume's leg vertices deep inside the cloth toward their leg's axis.

    The vertices deep inside the cloth at rest (``THIN_DEPTH``) are moved toward their leg's axis, so that knees and
    shins stay under a skirt or gown; judged with the chosen rule's weights by the same pass-through measure, kept when
    it takes ``THIN_GAIN`` points off and leaves no clip worse than the rule alone (``result.thin``); cloth geometry and
    its stretch stay unchanged. Removing hidden triangles would leave a gap where the cloth lifts: thinning only.
    """
    state, depth = hm.cover(sv, legs, triangles, at["pelvis"], up)
    with np.errstate(invalid="ignore"):
        rows = legs[(state == 1) & (depth < -THIN_DEPTH)]
    if not len(rows):
        return
    points = sv[rows]
    left, right = (
        _nearest_on_legs(points, at[f"thigh {side}"], at[f"calf {side}"], at[f"foot {side}"]) for side in "LR"
    )
    nearer = np.linalg.norm(points - left, axis=1) <= np.linalg.norm(points - right, axis=1)
    closest = np.where(nearer[:, None], left, right)
    chosen = best
    for factor in THIN_FACTORS:
        moved = sv.copy()
        moved[rows] = closest + factor * (points - closest)
        score = judged(f"{best.rule} thin {factor}", *four, solver, moved)
        if score.mean <= chosen.mean - THIN_GAIN and all(score.deep[c] <= best.deep[c] for c in score.deep):
            chosen, result.thin, result.thin_score = score, (rows, moved[rows]), score
            break


def _nearest_on_legs(points: np.ndarray, hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
    """Per point, the nearest point of the leg's axis (hip to knee to ankle)."""
    out, best = np.empty_like(points), np.full(len(points), np.inf)
    for a, b in ((hip, knee), (knee, ankle)):
        d = b - a
        t = np.clip((points - a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0)
        near = a + t[:, None] * d
        distance = np.linalg.norm(points - near, axis=1)
        take = distance < best
        out[take], best[take] = near[take], distance[take]
    return out


SWING_MIN = math.radians(2)  # a chain the source's own clips turn less than this stays rigid (the artist kept it so)
SWING_LIMITS = (math.radians(10), math.radians(30))  # how far a chain may swing: 1.5 times the source's own, in these


def swing_of(skeleton: Skeleton, clips: Iterable[Any], nodes: Iterable[int]) -> dict[int, float]:
    """Measure each node's maximum quaternion-curve turn from rest in radians, or zero without turns.

    Args:
        skeleton: Source hierarchy supplying rest quaternions and binding-path hashes.
        clips: Source-owned clips whose rotation curves are sampled without composing world poses.
        nodes: Source node indices for which swing limits are needed.
    """
    wanted = set(nodes)
    out = dict.fromkeys(wanted, 0.0)
    by_hash = {zlib.crc32(skeleton.nodes[n].path.encode()): n for n in wanted}
    for clip in clips:
        tree = skeleton.graph.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] == 2 and b["path"] in by_hash for b in bindings):
            continue
        for node, attributes in bind_tracks(bindings, decode_clip(tree).curves, by_hash).items():
            if 2 not in attributes:
                continue
            q = np.array(
                [np.resize(np.asarray(part, float), max(len(p) for p in attributes[2])) for part in attributes[2]]
            ).T
            q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
            rest = np.asarray(skeleton.nodes[node].local[1], float)
            rest /= max(float(np.linalg.norm(rest)), 1e-12)
            dot = np.clip(np.abs(q @ rest), 0.0, 1.0)
            out[node] = max(out[node], float(2 * np.arccos(dot).max()))
    return out


def own_keys(skeleton: Skeleton, clips: Iterable[Any], nodes: list[int]) -> dict[tuple[str, ...], tuple]:
    """Index source rotation keys by clip-name suffixes of at least two tokens.

    Match suffixes below the
    full name (``Formation_Idle``, ``Move_Ing``, ...; the longest suffix is tried first, ``Spring.keyed``): the
    clip's frame times scaled to 0..1 and, per index into ``nodes`` it turns, its local quaternions there.

    Args:
        skeleton: Source hierarchy supplying binding paths and clip readers through its graph.
        clips: Source AnimationClips contributing local quaternion or Euler rotation curves.
        nodes: Source node order defining the bone indices stored in each suffix's rotation map.
    """
    by_hash = {zlib.crc32(skeleton.nodes[n].path.encode()): n for n in nodes}
    index = {n: k for k, n in enumerate(nodes)}
    out: dict[tuple[str, ...], tuple] = {}
    for clip in clips:
        tree = skeleton.graph.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] in (2, 4) and b["path"] in by_hash for b in bindings):
            continue
        decoded = decode_clip(tree)
        times = np.asarray(decoded.times, float)
        frames = len(times)
        orders = euler_orders(bindings, by_hash)
        rotations = {}
        for node, attributes in bind_tracks(bindings, decoded.curves, by_hash).items():
            if 2 in attributes:
                q = np.array([np.resize(np.asarray(part, float), frames) for part in attributes[2]]).T
            elif 4 in attributes:
                e = np.array([np.resize(np.asarray(part, float), frames) for part in attributes[4]]).T
                q = np.array([euler_to_quat(list(v), orders.get(node, UNITY_ORDER)) for v in e])
            else:
                continue
            rotations[index[node]] = q
        if not rotations:
            continue
        phases = (times - times[0]) / max(float(times[-1] - times[0]), 1e-9) if frames > 1 else np.zeros(1)
        tokens = [t for t in (clip.peek_name() or "").lower().split("_") if t]
        for i in range(1, len(tokens) - 1):
            out.setdefault(tuple(tokens[i:]), (phases, rotations))
    return out


SCALE_KEYED = 1e-4  # a scale curve varying less than this is no motion


def own_scales(skeleton: Skeleton, clips: Iterable[Any], nodes: list[int]) -> dict[tuple[str, ...], tuple]:
    """Index source scale keys by clip-name suffixes, as own_keys indexes rotation keys.

    Args:
        skeleton: Source hierarchy supplying binding paths and clip readers through its graph.
        clips: Source AnimationClips contributing local scale curves.
        nodes: Source node order defining the bone indices stored in each suffix's scale map.

    Returns:
        Per suffix, the clip's frame times scaled to 0..1 and, per index into ``nodes`` whose scale it varies, the
        local scales there (frames by 3).
    """
    by_hash = {zlib.crc32(skeleton.nodes[n].path.encode()): n for n in nodes}
    index = {n: k for k, n in enumerate(nodes)}
    out: dict[tuple[str, ...], tuple] = {}
    for clip in clips:
        tree = skeleton.graph.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] == 3 and b["path"] in by_hash for b in bindings):
            continue
        decoded = decode_clip(tree)
        times = np.asarray(decoded.times, float)
        frames = len(times)
        scales = {}
        for node, attributes in bind_tracks(bindings, decoded.curves, by_hash).items():
            if 3 not in attributes:
                continue
            v = np.array([np.resize(np.asarray(part, float), frames) for part in attributes[3]]).T
            if float(np.ptp(v, axis=0).max()) > SCALE_KEYED:
                scales[index[node]] = v
        if not scales:
            continue
        phases = (times - times[0]) / max(float(times[-1] - times[0]), 1e-9) if frames > 1 else np.zeros(1)
        tokens = [t for t in (clip.peek_name() or "").lower().split("_") if t]
        for i in range(0 if len(tokens) == 2 else 1, len(tokens) - 1):  # a shared clip's whole name too (Normal_Idle)
            out.setdefault(tuple(tokens[i:]), (phases, scales))
    return out


def springs(target: Costume, source: Costume, plan_: Plan, s_clips: list[Any], fit: Fit) -> list[Skirt]:
    """Build swinging secondary chains away from the legs for Optimize Physics.

    Rigid hair, ribbon, and loose-coat chains become new slot bones, one Skirt per anchor,
    swinging with chibi_skirts.Spring in every slot clip. Leg cloth belongs to the hem rules and Rule B. A
    chain swings only when the source's own clips turn it (``SWING_MIN``), at most 1.5 times as far. The skirts' bases
    are set where they join the slot's skeleton (:func:`skirt_bones`). A slot clip whose name the source's clips share
    (``Formation_Idle``, ``Cafe_Walk``) plays the source's own keys of the chains instead, time-scaled onto it
    (:func:`own_keys`, ``Spring.keyed``).

    Args:
        target: Slot costume supplying destination anchors and existing hierarchy paths.
        source: Incoming costume supplying secondary chains and their weighted cloth.
        plan_: Mapping and rigid/follow choices selecting eligible non-leg groups.
        s_clips: Source clips defining observed swing limits and matching keyed motions.
        fit: Bound shape used to carry each chain with its fitted cloth.
    """
    ts, ss = target.skeleton, source.skeleton
    s_legs = legs_of(ss)
    lower = lower_anchors(ss, s_legs) if s_legs is not None else set()
    flat = flatten(source, ss.world())
    t_rest = ts.world()
    sv = fit.vertices
    used = set(flat.joints[flat.weights > 0].tolist())
    out = []
    for anchor, members in sorted(plan_.groups.items()):
        if plan_.choices.get(anchor) != "rigid" or anchor in lower or not members:
            continue
        member_set = set(members)
        roots = sorted({chain_root(ss, n) for n in members if n in used})
        roots = [r for r in roots if r in member_set]
        if not roots:
            continue
        swing = swing_of(ss, s_clips, [j for r in roots for j in [r, *ss.descendants(r)] if j in member_set])
        limits = {}
        for r in roots:
            turned = max(swing.get(j, 0.0) for j in [r, *ss.descendants(r)] if j in swing)
            if turned >= SWING_MIN:
                limits[r] = min(max(1.5 * turned, SWING_LIMITS[0]), SWING_LIMITS[1])
        if not limits:
            continue
        wanted: set[int] = set()
        for n in member_set & used:
            if chain_root(ss, n) not in limits:
                continue
            while n not in wanted:
                wanted.add(n)
                if n in limits:
                    break
                n = ss.nodes[n].parent
        sources = sorted(wanted)
        new = {n: k for k, n in enumerate(sources)}
        parents = [new.get(ss.nodes[n].parent, -1) for n in sources]
        slot_anchor = plan_.mapping[anchor]
        s_rest = ss.world()
        # each chain moves with its cloth from the source's space to the bound shape (as Rule B's)
        carry = {}
        for k, n in enumerate(sources):
            if parents[k] < 0:
                share = (flat.weights * np.isin(flat.joints, [j for j in sources if chain_root(ss, j) == n])).sum(
                    axis=1
                )
                rows = np.where(share > 0.5)[0]
                carry[n] = (
                    ms.rigid_fit(flat.vertices[rows], sv[rows], share[rows])
                    if len(rows) >= 3
                    else (t_rest[slot_anchor] @ _inverse(s_rest[anchor], ss.nodes[anchor].name))
                )
        rest = np.array([carry[chain_root(ss, n)] @ s_rest[n] for n in sources])
        local = np.array(
            [np.linalg.inv(rest[p] if p >= 0 else t_rest[slot_anchor]) @ rest[k] for k, p in enumerate(parents)]
        )
        chains = ms.Chains(slot_anchor, parents, local, rest)
        taken = {node.path for node in ts.nodes}
        names, paths, locals_ = [], [], []
        for k, n in enumerate(sources):
            name = ss.nodes[n].name
            parent_path = paths[parents[k]] if parents[k] >= 0 else ts.nodes[slot_anchor].path
            while f"{parent_path}/{name}" in taken:
                name += "_costume"
            names.append(name)
            paths.append(f"{parent_path}/{name}")
            taken.add(paths[-1])
            if parents[k] >= 0:
                locals_.append(ss.nodes[n].local)  # as it hangs in the costume's own skeleton
            else:
                m = local[k]
                scale = np.linalg.norm(m[:3, :3], axis=0)
                locals_.append((tuple(m[:3, 3]), tuple(ms.quaternion(m[:3, :3] / scale)), tuple(scale)))
        # as the new bones will stand (position, rotation, scale below their parent): a chain whose rest these cannot
        # reproduce (a sheared anchor) stays rigid
        placed: list[np.ndarray] = []
        for k, p in enumerate(parents):
            placed.append((placed[p] if p >= 0 else t_rest[slot_anchor]) @ trs(*locals_[k]))
        if float(np.abs(np.array(placed) - rest).max()) > 1e-5:
            logger.debug("Swinging chains on %s left rigid: rest not reproduced", ss.nodes[anchor].name)
            continue
        spring = ms.Spring(chains, {new[r]: limit for r, limit in limits.items()})
        if not spring.roots or not (spring.lengths >= ms.MIN_LENGTH).any():
            continue
        # a slot clip the source has by name plays the source's own keys of these chains
        spring = ms.Spring(
            chains,
            {new[r]: limits[r] for r in limits if spring.lengths[spring.roots.index(new[r])] >= ms.MIN_LENGTH},
            own_keys(ss, s_clips, sources),
            np.array([trs(*ss.nodes[n].local) for n in sources]),
        )
        out.append(Skirt(sources, names, parents, locals_, slot_anchor, spring, -1))
    return out


FACE_OFFSET = 0.002  # a source face bone resting this far (metres) or more from the slot bone it maps to is retargeted


class FaceRetarget:
    """Drive added source-face bones with slot expressions around the source's facial landmarks.

    Eye, brow and mouth bones below the slot's head rest where the source's face has
    them, following the slot's expressions: in every frame of a slot clip each new bone takes the turn and the move
    its slot bone of the same name makes against the head, about its own place (a lid closes about the source's eye,
    not the slot's). slots records the slot bone each new bone follows.

    Bones in ``rigid`` are what the source hangs below a face bone and animates itself (Aris's eye rings): they keep
    their rest place below their parent, and ``scales`` holds the scale keys of the source's own clips for them, by
    clip-name suffix (``own_scales``), which a slot clip of that name takes over its own length (``scaled``).
    """

    def __init__(
        self,
        chains: ms.Chains,
        head: int,
        slots: list[int],
        t_rest: list[np.ndarray],
        rigid: Iterable[int] = (),
        scales: Mapping[tuple[str, ...], tuple] | None = None,
    ):
        """Prepare face motion relative to the destination head's rest frame.

        Args:
            chains: Added source-face bones in their destination rest positions.
            head: Destination head node index used as the expression reference frame.
            slots: Destination face node followed by each added bone, in chain order.
            t_rest: Destination skeleton world matrices at rest.
            rigid: Chain bones that stay at rest below their parent, a chain bone before them.
            scales: Source scale keys by clip-name suffix, their bone indices those of the chain.
        """
        self.chains, self.head, self.slots = chains, head, slots
        self.rigid, self.scales = set(rigid), dict(scales or {})
        self.roots = list(range(len(slots)))
        inverse = np.linalg.inv(t_rest[head])
        self.rel0 = [inverse @ t_rest[m] for m in slots]  # the slot bones against the head at rest
        self.new0 = [inverse @ m for m in chains.rest]  # the new bones likewise

    def follow(
        self, skeleton: Skeleton, tree: Mapping[str, Any], frames: list[int], *, prepared=None, step=lambda: None
    ) -> list[dict[int, np.ndarray]]:
        """Sample scale-free local face transforms, omitting bones that stay at rest against the head.

        Args:
            skeleton: Slot hierarchy used to compose head and expression-bone world poses.
            tree: Slot AnimationClip typetree containing the expression curves.
            frames: Nonempty clip-frame indices to sample in order.
            prepared: Optional clip_frames result covering head and slot bones; None prepares only those dependencies.
            step: No-argument yield/cancellation callback before each frame sample.
        """
        _, pose, _ = prepared if prepared is not None else skeleton.clip_frames(tree, [self.head, *self.slots])
        ch = self.chains
        out: list[dict[int, np.ndarray]] = []
        for f in frames:
            step()
            world = pose(f)
            back = np.linalg.inv(world[self.head])
            placed: list[np.ndarray] = []
            for k, m in enumerate(self.slots):
                if k in self.rigid:
                    placed.append(placed[ch.parents[k]] @ ch.local[k])
                    continue
                rel = back @ world[m]
                turn = _rotation(rel) @ _rotation(self.rel0[k]).T
                w = self.new0[k].copy()
                w[:3, :3] = turn @ w[:3, :3]
                w[:3, 3] = self.new0[k][:3, 3] + rel[:3, 3] - self.rel0[k][:3, 3]
                placed.append(w)
            turns = {}
            for k, p in enumerate(ch.parents):
                local = placed[k] if p < 0 else np.linalg.inv(placed[p]) @ placed[k]
                local[:3, :3] = local[:3, :3] @ np.linalg.inv(ms.scale_of(local))
                turns[k] = local
            out.append(turns)
        moving = {
            k
            for k in self.roots
            if k not in self.rigid
            and (
                any(not np.allclose(t[k], out[0][k], atol=1e-6) for t in out)
                or not np.allclose(out[0][k], ch.hanging(k), atol=1e-6)
            )
        }
        return [{k: t[k] for k in sorted(moving)} for t in out]

    def scaled(self, name: str, times: Sequence[float]) -> dict[int, np.ndarray]:
        """Take the source's scale keys of the clip a slot clip's name matches, over the slot clip's span.

        Args:
            name: Slot clip name whose trailing lower-case tokens select the source clip.
            times: Slot sample times in seconds, normalized across their first-to-last span.

        Returns:
            Local scales (times by 3) per chain bone the matching source clip scales; empty without a match.
        """
        tokens = [t for t in name.lower().split("_") if t]
        match = next(
            (self.scales[tuple(tokens[i:])] for i in range(len(tokens) - 1) if tuple(tokens[i:]) in self.scales),
            None,
        )
        if match is None or not len(times):
            return {}
        phases, scales = match
        start, span = float(times[0]), max(float(times[-1]) - float(times[0]), 1e-9)
        at = np.clip((np.asarray(times, float) - start) / span, 0.0, 1.0)
        return {k: np.stack([np.interp(at, phases, v[:, c]) for c in range(3)], axis=1) for k, v in scales.items()}

    def posed(self, pose: Sequence[np.ndarray], turns: Mapping[int, np.ndarray]) -> np.ndarray:
        """Place added face bones under the slot pose's head using scale-free local turns."""
        return self.chains.posed(np.asarray(pose[self.chains.anchor]), turns)


def _rotation(m: np.ndarray) -> np.ndarray:
    return m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)


def skirt_bones(skeleton: Skeleton, skirt: Skirt) -> None:
    """Append chain nodes to the slot skeleton before _edit_prefabs creates Unity objects.

    Args:
        skeleton: Mutable destination hierarchy whose length must equal skirt.base.
        skirt: Added bone names, parent/anchor choices, locals, and expected solver rest matrices.

    Raises:
        ServiceError: The skeleton changed or the appended nodes fail to reproduce the solver's rest pose.
    """
    if len(skeleton.nodes) != skirt.base:
        raise refuse("skeleton changed")
    for k, (name, parent, local) in enumerate(zip(skirt.names, skirt.parents, skirt.locals)):
        father = skirt.node(parent) if parent >= 0 else skirt.anchor_of(k)
        path = f"{skeleton.nodes[father].path}/{name}" if skeleton.nodes[father].path else name
        skeleton.nodes.append(Node(name, path, father, local, None, None, True))
    world = skeleton.world()
    for k in range(len(skirt.sources)):
        if float(np.abs(world[skirt.node(k)] - skirt.solver.chains.rest[k]).max()) > 1e-4:
            raise refuse("rest pose not reproduced")


def measured_frames(count: int) -> list[int]:
    """Select unique HEM_FRAMES evenly spaced indices from a clip with count frames."""
    return sorted({min(count - 1, round(k * (count - 1) / max(1, HEM_FRAMES - 1))) for k in range(HEM_FRAMES)})


def skirt_sequence(
    skeleton: Skeleton,
    solver: ms.Solver,
    tree: Mapping[str, Any],
    *,
    prepared=None,
    step: Callable[[], None] = lambda: None,
) -> tuple[list[int], list[dict]]:
    """Solve chain turns at stride, final, and measured frames, or every frame for face motion.

    Args:
        skeleton: Slot hierarchy used to compose the solver's anchor/body dependencies.
        solver: Leg-clearance, spring, or face solver defining the chain motion.
        tree: Slot clip typetree supplying timing, loop state, and optional matching source-key name.
        prepared: Optional clip_frames result covering solver dependencies; None samples the skeleton.
        step: No-argument yield/cancellation callback during pose sampling and solver frames.

    Returns:
        Solved clip-frame indices and corresponding scale-free local bone transforms.
    """
    from ..chibi_arm_clips import clip_times

    if isinstance(solver, FaceRetarget):  # a blink lasts a few frames
        every = list(range(len(clip_times(tree))))
        return every, solver.follow(skeleton, tree, every, prepared=prepared, step=step)
    times, pose, _ = prepared if prepared is not None else skeleton.clip_frames(tree)
    count = len(times)
    frames = sorted(set(range(0, count, ms.SOLVE_EVERY)) | {count - 1} | set(measured_frames(count)))
    if isinstance(solver, ms.Spring):
        # the source's own keys for a clip it has by name, else the swing; then gravity
        poses = []
        for f in frames:
            step()
            poses.append(pose(f))
        keyed = solver.keyed(str(tree.get("m_Name") or ""), [times[f] for f in frames])
        turns = (
            keyed
            if keyed is not None
            else solver.sequence(
                [times[f] for f in frames], lambda k: poses[k], bool(tree["m_MuscleClip"].get("m_LoopTime"))
            )
        )
        return frames, solver.settle(turns, [np.asarray(p[solver.chains.anchor]) for p in poses])
    loop = bool(tree["m_MuscleClip"].get("m_LoopTime"))
    return frames, solver.sequence([times[f] for f in frames], lambda k: pose(frames[k]), loop, step=step)


def curve_frames(skeleton: Skeleton, skirts: Sequence[Skirt], tree: Mapping[str, Any]):
    """Prepare one clip snapshot posing only the chains' anchor, body, and face dependencies.

    New curves target new bones, never these original anchors and face
    slots. Arm copies supply their corrected tree before taking this snapshot.

    Args:
        skeleton: Slot hierarchy used to compose the required node poses with memory caching.
        skirts: Added chain descriptions whose solvers determine the dependency set.
        tree: Clip typetree before added chain curves, including any corrected arm curves.
    """
    nodes = set()
    for skirt in skirts:
        solver = skirt.solver
        if isinstance(solver, FaceRetarget):
            nodes.update([solver.head, *solver.slots])
        elif isinstance(solver, ms.Spring):
            nodes.add(solver.chains.anchor)
        else:
            nodes.update([solver.pelvis, *solver.leg_nodes, *solver.cloth.fixed])
            nodes.update(solver.chains.anchor_of(r) for r in solver.roots)
    return skeleton.clip_frames(tree, nodes, cache=True)


def skirt_curves(
    skeleton: Skeleton,
    skirt: Skirt,
    tree: dict,
    key: tuple[str, int] | None = None,
    *,
    prepared=None,
    step: Callable[[], None] = lambda: None,
) -> int | None:
    """Add solved chain rotation and position curves to a slot clip's dense grid.

    Interpolate skirt_sequence turns and shifts, or reuse measured solutions. Spring.keyed
    clips receive curves for every bone their source keys turn, rather than only chain roots. A face's bones with
    scale keys of the source's own (FaceRetarget.scaled) receive scale curves.

    Args:
        skeleton: Destination hierarchy including the added bones and their animation binding paths.
        skirt: Chain solver and optional cached measurement solutions.
        tree: Mutable slot AnimationClip typetree receiving added dense curves.
        key: Optional original (CAB, path ID) selecting a measured solution; None solves on demand.
        prepared: Optional dependency-only clip_frames snapshot for on-demand solving.
        step: No-argument yield/cancellation callback during on-demand pose sampling and solving.

    Returns:
        Number of scalar curves added (7 per turned bone: rotation and position; 3 per scaled bone), or None for
        a clip layout the writer does not edit.
    """
    grid = ms.dense_grid(tree)
    if grid is None:
        return None
    from ..chibi_arm_clips import clip_times

    times = clip_times(tree)
    frames, turns = skirt.solved.get(key) or skirt_sequence(skeleton, skirt.solver, tree, prepared=prepared, step=step)
    roots = sorted(turns[0]) if turns else []
    template = tree["m_ClipBindingConstant"]["genericBindings"][0]
    scaled = skirt.solver.scaled(str(tree.get("m_Name") or ""), grid) if isinstance(skirt.solver, FaceRetarget) else {}
    if scaled:  # the source's own scale keys for a clip it has by name
        bones = sorted(scaled)
        ms.add_dense_curves(
            tree,
            [ms.scale_binding(template, skeleton.nodes[skirt.node(k)].path) for k in bones],
            np.concatenate([scaled[k] for k in bones], axis=1),
        )
    if not roots:
        return 3 * len(scaled)
    rotations = np.array([[ms.quaternion(t[root][:3, :3]) for root in roots] for t in turns])
    positions = np.array([[t[root][:3, 3] for root in roots] for t in turns])
    at = np.clip(grid, times[0], times[-1])
    columns = []
    solved_at = np.array([times[f] for f in frames])
    for r in range(len(roots)):  # per root its rotation (4 curves), then its position (3)
        if len(frames) > 1:
            columns.append(ms.resample(solved_at, ms.continuous(rotations[:, r]), at))
            columns.append(np.stack([np.interp(at, solved_at, positions[:, r, k]) for k in range(3)], axis=1))
        else:
            columns.append(np.repeat(rotations[:1, r], len(grid), axis=0))
            columns.append(np.repeat(positions[:1, r], len(grid), axis=0))
    samples = np.concatenate(columns, axis=1)
    bindings = []
    for root in roots:
        path = skeleton.nodes[skirt.node(root)].path
        bindings += [ms.rotation_binding(template, path), ms.position_binding(template, path)]
    ms.add_dense_curves(tree, bindings, samples)
    return 7 * len(roots) + 3 * len(scaled)
