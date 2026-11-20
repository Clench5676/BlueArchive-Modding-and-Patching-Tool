"""The costume of another chibi on a slot: bone mapping, carriers, stowed items and the fitted shape."""

from __future__ import annotations

import dataclasses
import logging
import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np

from ..costume_cache import decode_clip
from ..chibi_glb import bind_tracks
from .parts import Part, Skin, bindposes, deform, stretch
from .skeleton import (
    BIPED_CHAIN,
    BIPED_ROOT,
    CARRIER_SAMPLES,
    CARRIER_SHARE,
    STOWED_SHOWN,
    HEAD,
    HIDDEN,
    NEAR,
    SHAPE_MARGIN,
    STRETCH_FLOOR,
    Skeleton,
    _inverse,
    refuse,
    stretch_limit,
)


logger = logging.getLogger(__package__)


@dataclass
class Costume:
    """Costume parts of one prefab, flattened for measuring: every part's skin in the prefab root's space."""

    skeleton: Skeleton
    parts: list[Part]
    skins: list[Skin]

    def bones(self) -> list[int]:
        """Return sorted unique skeleton nodes referenced by the costume parts' skin bones."""
        return sorted({b for p in self.parts for b in p.bones if b is not None})


def unmatched(target: Skeleton, source: Skeleton, flat: Flat) -> list[int]:
    """Find source Biped bones missing by name on the slot and bones outside the Biped.

    A bag strap below the prefab root has no Biped ancestor and needs a carrier too.

    Args:
        target: Slot hierarchy searched for corresponding Biped names.
        source: Costume hierarchy supplying bone ancestry and names.
        flat: Flattened costume whose positive-weight influences identify used bones and anchors.
    """
    out = set()
    for n in set(flat.joints[flat.weights > 0].tolist()) - {-1}:
        anchor = source.biped_ancestor(n)
        if not source.nodes[n].name.startswith("Bip001") and anchor is None and source.nodes[n].parent is not None:
            out.add(n)
        for b in (n, anchor):
            if (
                b is not None
                and source.nodes[b].name.startswith("Bip001")
                and target.first(source.nodes[b].name) is None
            ):
                out.add(b)
    return sorted(out)


def carriers(target: Skeleton, source: Skeleton, flat: Flat, poses: list[list[np.ndarray]]) -> dict[int, int]:
    """Choose matched source bones that best carry unmatched costume branches under source clips.

    Find the source bone the slot has by name that moves unmatched vertices
    most alike under the source's own clips (``poses``): a Biped's ForeTwist hangs off the UpperArm but turns with the
    forearm, and where the slot has no such bone the costume's sleeve goes with the forearm, not with the upper arm;
    a bag's strap keyed to the body goes with the body. Measured on the vertices it or its secondary bones dominate
    (else its own frame): the least mean distance between where the two carry them, which must be under
    ``CARRIER_SHARE`` of the distance from where its parent carries them (else it stays with its parent).
    A bone outside the Biped is judged with its whole branch (``group``: Sena (Casual)'s bag under a prop node the slot
    lacks), against the nearest ancestor the slot has, where the mapping would leave it; the result is keyed by the
    branch's top node.

    Args:
        target: Slot skeleton defining which Biped carrier names are available.
        source: Incoming costume skeleton whose unmatched branches need carriers.
        flat: Source prefab-rest geometry and skeleton-node influences used for distance measurements.
        poses: Source-owned world poses used to compare branch movement with candidate carriers.
    """
    missing = unmatched(target, source, flat)
    if not missing or not poses:
        return {}
    rest = source.world()
    present = [
        i for i, node in enumerate(source.nodes) if BIPED_CHAIN.match(node.name) and target.first(node.name) is not None
    ]
    if not present:
        return {}
    dominant = flat.joints[np.arange(len(flat.vertices)), np.argmax(flat.weights, axis=1)]

    def group(n: int) -> int:
        """The group a bone stands for.

        A Biped bone stands for itself; a bone outside the Biped for its branch: the node right under the nearest
        ancestor the slot has by name (a prop's whole chain, never torn across the body).
        """
        return n if source.nodes[n].name.startswith("Bip001") else _branch_top(target, source, n)

    groups = sorted({group(m) for m in missing})
    anchor = {
        n: (group(n) if n in missing else n if source.nodes[n].name.startswith("Bip001") else source.biped_ancestor(n))
        for n in set(dominant.tolist()) - {-1}
    }
    # a Biped bone against its parent (a finger's last joint bends with the one before); a prop's branch against
    # where the mapping would leave it, the nearest ancestor the slot has
    parents = {
        m: source.nodes[m].parent if source.nodes[m].name.startswith("Bip001") else _kept_ancestor(target, source, m)
        for m in groups
    }
    inverse = {i: np.linalg.inv(rest[i]) for i in {*present, *groups, *(p for p in parents.values() if p is not None)}}
    out = {}
    for m in groups:
        rows = np.where(np.isin(dominant, [n for n, a in anchor.items() if a == m]))[0]
        if len(rows):
            rows = rows[:: max(1, len(rows) // CARRIER_SAMPLES)]
            points, own = flat.vertices[rows], dominant[rows]
        else:  # its frame: the joint and a point along each axis
            points = rest[m][:3, 3] + np.vstack([np.zeros(3), 0.05 * rest[m][:3, :3].T])
            own = np.full(len(points), m)
        homogeneous = np.c_[points, np.ones(len(points))]
        bones = sorted(set(own.tolist()))
        column = np.searchsorted(bones, own)
        inverse.update({b: np.linalg.inv(rest[b]) for b in bones if b not in inverse})
        errors = np.zeros(len(present))
        stay = 0.0  # with its parent
        parent = parents[m]
        for pose in poses:
            moves = np.array([pose[b] @ inverse[b] for b in bones])[column]
            here = np.einsum("nij,nj->ni", moves, homogeneous)
            carried_by_parent = homogeneous @ (pose[parent] @ inverse[parent]).T if parent is not None else homogeneous
            stay += float(np.linalg.norm(carried_by_parent - here, axis=1).mean())
            stack = np.array([pose[c] @ inverse[c] for c in present])
            there = np.einsum("cij,nj->cni", stack, homogeneous)
            errors += np.linalg.norm(there - here[None], axis=2).mean(axis=1)
        best = int(np.argmin(errors))
        if stay > 1e-4 * len(poses) and errors[best] < CARRIER_SHARE * stay:
            out[m] = present[best]
        if (shoulder := _shoulder_mount(target, source, m, dominant)) is not None:
            out[m] = shoulder
    if out:
        logger.debug(
            "Costume bones the slot lacks, carried by: %s",
            {source.nodes[m].name: source.nodes[c].name for m, c in out.items()},
        )
    return out


UPPER_ARM = re.compile(r"^Bip001 ([LR]) UpperArm$")


def _shoulder_mount(target: Skeleton, source: Skeleton, node: int, dominant: np.ndarray) -> int | None:
    """The slot's node a mount on an upper arm the slot lacks hangs from, or None.

    A mount on an upper arm the slot lacks is a Biped-named bone below ``Bip001 L/R UpperArm`` that skins nothing itself
    and holds only props (Airi (Band)'s shoulder bag on its strap): the slot's arm moves otherwise than the source's,
    whose own keys held the prop at her side, so the prop hangs from the shoulder (that side's clavicle) instead of
    following the arm. None for anything else.
    """
    parent = source.nodes[node].parent
    side = UPPER_ARM.match(source.nodes[parent].name) if parent is not None else None
    if side is None or not source.nodes[node].name.startswith("Bip001") or (dominant == node).any():
        return None
    children = [i for i, n in enumerate(source.nodes) if n.parent == node]
    if not children or any(source.nodes[c].name.startswith("Bip001") for c in children):
        return None
    clavicle = source.first(f"Bip001 {side.group(1)} Clavicle")
    return clavicle if clavicle is not None and target.first(source.nodes[clavicle].name) is not None else None


def stowed(
    target: Skeleton, source: Skeleton, flat: Flat, poses: list[list[np.ndarray]], carried: Mapping[int, int]
) -> dict[int, tuple[int, np.ndarray, np.ndarray]]:
    """Find loose props keyed onto the body from a distant costume rest position.

    Aoba's pipe
    wrench, skinned in her body mesh below a ``Prop_root`` the slot lacks, rests 40 cm beside her, and every clip of
    hers keys it to her hip. ``carriers`` measures from the rest and finds no bone that explains it, so the slot's
    clips, which never key it, leave it where it rests while her body moves away. For such a branch (outside the
    Biped, no carrier, its vertices skinned to it alone, shown by the first probe pose, the idle, and
    more than ``NEAR`` away from where its parent would carry its rest; branches skinning a vertex together count as
    one, whatever ``carriers`` found for one of them): the bone the slot has by name that moves it
    most alike measured from the idle, under the share ``carriers`` asks. Keyed by the branch's top node: the
    carrier, the branch's vertex rows and per row the move that puts it where the idle holds it against that carrier,
    at the carrier's rest, at the size the idle draws it (some clips hold a prop smaller than it is modelled). A
    prop the idle hides (drawn under ``STOWED_SHOWN`` of its size) retains its existing placement.

    Args:
        target: Slot skeleton defining available Biped carrier names.
        source: Costume skeleton supplying branch structure, rest matrices, and candidate carrier nodes.
        flat: Source prefab-rest vertices and positive-weight bone influences.
        poses: Source probe world poses, with the idle/reference pose first.
        carried: Branch-top to source-carrier mapping already resolved by carriers; those branches are skipped.

    Returns:
        Branch tops mapped to source carrier, vertex rows, and per-row rest-placement transforms.
    """
    if not poses:
        return {}
    rest = source.world()
    ref = poses[0]
    present = [
        i for i, node in enumerate(source.nodes) if BIPED_CHAIN.match(node.name) and target.first(node.name) is not None
    ]
    weighted = flat.weights > 0
    out = {}
    # branches skinning a vertex together are one prop (a strap on a row of bones below the root)
    tops = sorted(
        {
            _branch_top(target, source, n)
            for n in unmatched(target, source, flat)
            if not source.nodes[n].name.startswith("Bip001")
        }
    )
    top_of = np.full(len(source.nodes), -1)
    for m in tops:
        top_of[[m, *source.descendants(m)]] = m
    link = {m: m for m in tops}

    def find(m: int) -> int:
        while link[m] != m:
            link[m] = link[link[m]]
            m = link[m]
        return m

    per_vertex = np.where(weighted & (flat.joints >= 0), top_of[np.maximum(flat.joints, 0)], -1)
    for row in np.unique(per_vertex[(per_vertex >= 0).sum(axis=1) > 1], axis=0).tolist():
        hit = sorted({find(m) for m in row if m >= 0})
        for other in hit[1:]:
            link[other] = hit[0]
    groups: dict[int, list[int]] = {}
    for m in tops:
        groups.setdefault(find(m), []).append(m)
    for m, members in sorted(groups.items()):
        if all(member in carried for member in members):
            continue
        branch = {b for member in members for b in (member, *source.descendants(member))}
        inside = np.isin(flat.joints, sorted(branch)) & weighted
        rows = np.where(inside.any(axis=1))[0]
        if not len(rows) or (inside[rows] != weighted[rows]).any():  # skinned to the body too: not a loose prop
            continue
        bones = sorted(set(flat.joints[rows][weighted[rows]].tolist()))
        if any(
            abs(np.linalg.det(ref[b][:3, :3])) / max(abs(np.linalg.det(rest[b][:3, :3])), 1e-12) < STOWED_SHOWN**3
            for b in bones
        ):
            continue  # the idle hides it: a prop the clips show and hide, not one they carry
        parent = _kept_ancestor(target, source, m)
        moves = {b: ref[b] @ np.linalg.inv(rest[b]) for b in bones}
        dominant = flat.joints[rows, np.argmax(flat.weights[rows], axis=1)]
        sample = np.arange(len(rows))[:: max(1, len(rows) // CARRIER_SAMPLES)]
        points = np.c_[flat.vertices[rows[sample]], np.ones(len(sample))]
        at_rest = points @ (ref[parent] @ np.linalg.inv(rest[parent])).T if parent is not None else points
        held = np.einsum("nij,nj->ni", np.array([moves[b] for b in dominant[sample]]), points)
        if len(members) == 1 and float(np.linalg.norm(held - at_rest, axis=1).mean()) <= NEAR:
            continue  # it rests where the clips hold it: ``carriers`` judged it from there
        errors = np.zeros(len(present))
        stay = 0.0
        for pose in poses:
            here = np.einsum(
                "nij,nj->ni", np.array([pose[b] @ np.linalg.inv(rest[b]) for b in dominant[sample]]), points
            )
            kept = held @ (pose[parent] @ np.linalg.inv(ref[parent])).T if parent is not None else held
            stay += float(np.linalg.norm(kept - here, axis=1).mean())
            stack = np.array([pose[c] @ np.linalg.inv(ref[c]) for c in present])
            errors += np.linalg.norm(np.einsum("cij,nj->cni", stack, held) - here[None], axis=2).mean(axis=1)
        best = int(np.argmin(errors)) if len(present) else -1
        if best < 0 or not (stay > 1e-4 * len(poses) and errors[best] < CARRIER_SHARE * stay):
            continue
        c = present[best]
        back = rest[c] @ np.linalg.inv(ref[c])  # from where the idle holds the carrier to its rest
        row_moves = drawn(
            Skin(
                flat.vertices[rows],
                np.searchsorted(bones, np.where(weighted[rows], flat.joints[rows], bones[0])),
                flat.weights[rows],
                np.zeros((0, 2), int),
            ),
            np.array([back @ moves[b] for b in bones]),
            np.eye(4),
        )
        out[m] = (c, rows, row_moves)
        for member in members[1:]:
            out[member] = (c, rows[:0], row_moves[:0])
    if out:
        logger.debug(
            "Costume props stowed by the source's clips, carried by: %s",
            {source.nodes[m].name: source.nodes[c].name for m, (c, _, _) in out.items()},
        )
    return out


def stow(fit: Fit, flat: Flat, props: Mapping[int, tuple[int, np.ndarray, np.ndarray]]) -> Fit:
    """Place stowed props against their carriers consistently across candidate shapes.

    Args:
        fit: Candidate bound shape to copy with corrected prop positions and linear transforms.
        flat: Original prefab-rest shape supplying the prop vertices before placement.
        props: stowed branch records containing carrier, vertex rows, and per-row placement transforms.
    """
    if not props:
        return fit
    vertices, linear = fit.vertices.copy(), fit.linear.copy()
    for _, rows, moves in props.values():
        vertices[rows] = np.einsum("nij,nj->ni", moves, np.c_[flat.vertices[rows], np.ones(len(rows))])[:, :3]
        linear[rows] = moves[:, :3, :3] @ flat.linear[rows]
    return dataclasses.replace(fit, vertices=vertices, linear=linear)


def _branch_top(target: Skeleton, source: Skeleton, n: int) -> int:
    """The node that stands for the branch of a bone outside the Biped.

    A bone outside the Biped stands for its branch: the node right under the nearest ancestor the slot has by name
    (``carriers``'s groups).
    """
    kept = _kept_ancestor(target, source, n)
    while (
        source.nodes[n].parent is not None
        and source.nodes[n].parent != kept
        and source.nodes[source.nodes[n].parent].parent is not None
    ):
        n = source.nodes[n].parent
    return n


def _kept_ancestor(target: Skeleton, source: Skeleton, node: int) -> int | None:
    """The nearest ancestor of ``node`` the slot has a bone of the name for.

    This is where the mapping leaves an unmatched bone that has no carrier (a bag hung off a prop node the slot lacks
    stays with the prefab's root, None).
    """
    p = source.nodes[node].parent
    while p is not None and source.nodes[p].parent is not None and target.first(source.nodes[p].name) is None:
        p = source.nodes[p].parent
    return p if p is not None and source.nodes[p].parent is not None else None


def hidden_bones(skeleton: Skeleton, poses: list[list[np.ndarray]]) -> set[int]:
    """Find bones scaled almost to zero at rest or in probes so costume vertices never follow them.

    Sena's Formation_Pickup hides a prop this way.

    Args:
        skeleton: Slot hierarchy supplying rest volumes and node indices.
        poses: Slot world poses whose bone volumes are compared with rest using HIDDEN.
    """
    if not skeleton.nodes:
        return set()
    rest = np.array(skeleton.world())
    base = np.abs(np.linalg.det(rest[:, :3, :3]))
    out = set(np.where(base < 1e-9)[0].tolist())
    for pose in poses:
        scale = np.abs(np.linalg.det(np.array(pose)[:, :3, :3]))
        out |= set(np.where(scale < HIDDEN * np.maximum(base, 1e-9))[0].tolist())
    return out


def scaled_away(skeleton: Skeleton, clips: Iterable[Any]) -> set[int]:
    """Find bones scaled below HIDDEN of their rest volume anywhere in the slot clips.

    This supplements hidden_bones probes for a magazine hidden in reload, a book in an EX
    skill, or a hair ornament in the cafe. A bone's volume includes its ancestors' scales, so a
    node scaled down above another scaled up (a tank's hull at 0.01, its body at 100) hides neither; it is read from
    the clips' scale curves alone, no pose is computed. Frames that hide the body (a pop-in) hide no bone.

    Args:
        skeleton: Slot hierarchy supplying local rest scales, ancestors, and body root.
        clips: AnimationClip readers whose full scale curves supplement the sparse probe poses.
    """
    g = skeleton.graph
    nodes = skeleton.nodes
    if not nodes:
        return set()
    by_hash = {zlib.crc32(node.path.encode()): i for i, node in enumerate(nodes)}
    rest = np.array([math.log(max(abs(float(np.prod(node.local[2]))), 1e-30)) for node in nodes])
    rest_world = rest.copy()
    for i, node in enumerate(nodes):  # parents come first
        if node.parent is not None:
            rest_world[i] += rest_world[node.parent]
    body = skeleton.first(BIPED_ROOT)
    limit = math.log(HIDDEN)
    out: set[int] = set()
    for clip in clips:
        tree = g.tree(clip)
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        if not any(b["typeID"] == 4 and b["attribute"] == 3 and b["path"] in by_hash for b in bindings):
            continue
        decoded = decode_clip(tree)
        frames = len(decoded.times)
        local: dict[int, np.ndarray] = {}
        for node, attributes in bind_tracks(bindings, decoded.curves, by_hash).items():
            if 3 in attributes:
                x, y, z = (np.asarray(part, float) for part in attributes[3])
                local[node] = np.log(np.maximum(np.abs(x * y * z), 1e-30)) * np.ones(frames)
        if not local:
            continue
        world = np.empty((len(nodes), frames))
        for i, node in enumerate(nodes):
            world[i] = local.get(i, rest[i]) + (world[node.parent] if node.parent is not None else 0.0)
        shrunk = world - rest_world[:, None] < limit
        if body is not None:
            shrunk[:, shrunk[body]] = False
        out.update(np.where(shrunk.any(axis=1))[0].tolist())
    return out


def map_bones(
    target: Skeleton,
    source: Skeleton,
    bones: Iterable[int],
    carried: Mapping[int, int] | None = None,
    hidden: set[int] | None = None,
) -> dict[int, int]:
    """Map source bones and ancestors to slot nodes before per-group follow/rigid choices.

    An unmatched
    bone the slot has no bone near goes to the slot's bone of its carrier's name (``carriers``); no secondary bone goes
    to one the slot's clips hide (``hidden``).

    Args:
        target: Slot skeleton providing named Biped and nearby secondary bones.
        source: Incoming skeleton whose hierarchy is traversed in parent order.
        bones: Source node indices used by the costume; their ancestors are included.
        carried: Optional unmatched-branch to source-carrier map from carriers/stowed.
        hidden: Slot nodes excluded as secondary-bone destinations; None excludes none.
    """
    rest_t, rest_s = target.world(), source.world()
    carried = carried or {}
    hidden = hidden or set()
    mapping: dict[int, int] = {}
    root = 0
    wanted = set()
    for b in bones:
        while b is not None and b not in wanted:
            wanted.add(b)
            b = source.nodes[b].parent
    for i, node in enumerate(source.nodes):
        if i not in wanted:
            continue
        if node.parent is None:
            mapping[i] = root
            continue
        if node.name.startswith("Bip001"):
            same = target.first(node.name)
            if same is not None:
                mapping[i] = same
                continue
        parent = mapping.get(node.parent, root)
        anchor = source.biped_ancestor(i)
        if anchor is None:  # a bone outside the Biped matches near its branch's carrier's bones only
            b: int | None = i
            while b is not None and b not in carried:
                b = source.nodes[b].parent
            anchor = b
        anchor = carried.get(anchor, anchor) if anchor is not None else None
        anchor_name = source.nodes[anchor].name if anchor is not None else None
        position = rest_s[i][:3, 3]
        best, distance = None, NEAR
        for c in [parent, *target.descendants(parent)]:
            if target.nodes[c].name.startswith("Bip001") or c in hidden:
                continue
            a = target.biped_ancestor(c)
            if (target.nodes[a].name if a is not None else None) != anchor_name:
                continue
            d = float(np.linalg.norm(rest_t[c][:3, 3] - position))
            if d < distance:
                best, distance = c, d
        if best is None and i in carried and (same := target.first(source.nodes[carried[i]].name)) is not None:
            best = same  # an unmatched bone the slot has nothing near: its carrier's
        mapping[i] = best if best is not None else parent
    return mapping


@dataclass
class Fit:
    """Hold a candidate costume shape in prefab-root space.

    Each SHAPES candidate retains per-vertex positions and blended mesh-to-root linear
    transforms for normals/tangents. Refit metrics record costume size and vertex motion
    beyond the common move, as the 99th percentile and maximum.
    """

    vertices: np.ndarray
    linear: np.ndarray
    shift: float
    largest: float
    size: float
    starts: list[int] = field(default_factory=list)  # each part's first vertex
    shape: str = "stored"
    # a refit: where the source's head joint lands against the slot's Head joint, in the prefab root's space (the
    # head goes with the neck, ``_anchor_move``); what hangs off the head (the halo) moves along by it
    head: np.ndarray | None = None

    def part(self, k: int, world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Convert one part's fitted positions and linear transforms back to its mesh space.

        Args:
            k: Costume part index selecting its vertex range from starts.
            world: Part mesh-to-prefab-root 4 by 4 transform.
        """
        end = self.starts[k + 1] if k + 1 < len(self.starts) else len(self.vertices)
        inverse = np.linalg.inv(world)
        points = (np.c_[self.vertices[self.starts[k] : end], np.ones(end - self.starts[k])] @ inverse.T)[:, :3]
        return points, inverse[:3, :3] @ self.linear[self.starts[k] : end]


@dataclass
class Plan:
    """Record bone mapping, group choices, fitted shape, and stretch verdict for a costume pair."""

    mapping: dict[int, int]
    choices: dict[int, str]  # source Biped node -> "follow" | "rigid"
    stretch: float
    own: float
    groups: dict[int, list[int]] = field(default_factory=dict)  # source Biped node -> its secondary bones
    unfit: bool = False  # past the stretch limit: may clip or distort, still built and applied
    fit: Fit | None = None  # the shape the costume is bound and judged in (``Fit``)
    repair: str = ""  # past the limit: what stiffening its worst edges did (:func:`rigidify`), for the log


@dataclass
class Flat:
    """Combine costume parts in prefab-root space at rest, retaining topology and influence nodes.

    Bone influences use skeleton node indices (-1 outside the prefab). starts records
    each part's first vertex; linear records each vertex's blended mesh-to-root linear transform.
    """

    vertices: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    edges: np.ndarray
    triangles: np.ndarray
    starts: list[int]
    linear: np.ndarray | None = None


def drawn(skin: Skin, carry: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    """Blend per-bone 4 by 4 transforms with normalized vertex weights.

    Args:
        skin: Vertex influence indices and weights selecting matrices from carry.
        carry: Bone transforms in the destination coordinate space.
        fallback: Transform assigned to vertices with zero total weight.
    """
    total = skin.weights.sum(axis=1, keepdims=True)
    blended = np.einsum("nk,nkij->nij", skin.weights / np.maximum(total, 1e-12), carry[skin.joints])
    blended[total[:, 0] <= 1e-12] = fallback
    return blended


def flatten(costume: Costume, rest: list[np.ndarray]) -> Flat:
    """Flatten costume parts as the prefab draws them at rest using their own bindposes.

    A mesh's vertex data need
    not sit in its renderer's space (some lie a metre or more off, their bindposes bringing them back), and a mesh
    bound in another pose than the prefab's rest is drawn in that rest.

    Args:
        costume: Source parts/skins and hierarchy to combine, keeping facial expression bones head-rigid.
        rest: Source skeleton's rest world matrices defining prefab-root placement.
    """
    vertices, linear, joints, weights, edges, triangles, starts, offset = [], [], [], [], [], [], [], 0
    skeleton = costume.skeleton
    graph = skeleton.graph
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        bp = bindposes(graph, part.mesh)
        carry = [rest[b] @ bp[j] if b is not None and j < len(bp) else node for j, b in enumerate(part.bones)]
        # a face's eye, brow and mouth bones may rest off their bind (a default expression the clips replace): the
        # face stays head-rigid to avoid carrying a default expression (Midori (Battle)'s brows sit 1.4 cm up).
        head = {b: j for j, b in enumerate(part.bones) if b is not None and skeleton.nodes[b].name == "Bip001 Head"}
        for j, b in enumerate(part.bones):
            if b is None or j >= len(bp) or not face_bone(skeleton, b):
                continue
            h = skeleton.chain_ancestor(b)
            if h in head and head[h] < len(bp):
                carry[j] = rest[h] @ bp[head[h]]
        carry = np.array(carry or [node])
        blended = drawn(
            Skin(skin.vertices, np.minimum(skin.joints, len(carry) - 1), skin.weights, skin.edges), carry, node
        )
        vertices.append(np.einsum("nij,nj->ni", blended, np.c_[skin.vertices, np.ones(len(skin.vertices))])[:, :3])
        linear.append(blended[:, :3, :3])
        joints.append(
            np.array(
                [[part.bones[j] if part.bones[j] is not None else -1 for j in row] for row in skin.joints]
            ).reshape(-1, skin.joints.shape[1])
        )
        weights.append(skin.weights)
        edges.append(skin.edges + offset)
        tris = skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)
        triangles.append(tris + offset)
        starts.append(offset)
        offset += len(skin.vertices)
    return Flat(
        np.vstack(vertices),
        np.vstack(joints),
        np.vstack(weights),
        np.vstack(edges),
        np.vstack(triangles),
        starts,
        np.concatenate(linear),
    )


@dataclass
class OwnMesh:
    """A prefab's costume drawn by its own bindposes: rest vertices, edges, each vertex's dominant node."""

    rest: np.ndarray
    edges: np.ndarray
    dominant: np.ndarray
    parts: list[tuple[Skin, np.ndarray, list[int]]]

    @classmethod
    def of(cls, costume: Costume) -> "OwnMesh":
        """Build reference geometry, edge indices, and dominant bones from a costume's original bindposes."""
        rest_world = costume.skeleton.world()
        vertices, edges, dominant, parts, offset = [], [], [], [], 0
        for part, skin in zip(costume.parts, costume.skins):
            bp = bindposes(costume.skeleton.graph, part.mesh)
            bones = [b if b is not None else 0 for b in part.bones]
            vertices.append(deform(skin, np.array([rest_world[b] for b in bones]) @ bp))
            edges.append(skin.edges + offset)
            dominant.append(np.array([bones[row[np.argmax(w)]] for row, w in zip(skin.joints, skin.weights)]))
            parts.append((skin, bp, bones))
            offset += len(skin.vertices)
        return cls(np.vstack(vertices), np.vstack(edges), np.concatenate(dominant), parts)

    def posed(self, pose: list[np.ndarray]) -> np.ndarray:
        """Skin all reference parts under node world matrices and concatenate their positions."""
        return np.vstack([deform(skin, np.array([pose[b] for b in bones]) @ bp) for skin, bp, bones in self.parts])


def face_bone(skeleton: Skeleton, node: int) -> bool:
    """Recognize an eye, brow, or mouth bone below the head, excluding body-chain and twist bones.

    Args:
        skeleton: Source hierarchy used to find the nearest Biped-chain ancestor.
        node: Node index whose Bip001-prefixed name is tested.
    """
    name = skeleton.nodes[node].name
    if not name.startswith("Bip001") or BIPED_CHAIN.match(name) or "Twist" in name:
        return False
    h = skeleton.chain_ancestor(node)
    return h is not None and skeleton.nodes[h].name == "Bip001 Head"


def fit_anchor(
    target: Skeleton, source: Skeleton, node: int, carried: Mapping[int, int] | None = None
) -> tuple[int, int] | None:
    """Find a source node's refit anchor and the matching slot bone.

    Use the node or nearest named ancestor, substituting carriers for unmatched bones.
    Eligible anchors are the Biped body chain, hands, feet, twist bones, and root. A skirt
    below Bip001, such as Sena (Casual)'s, follows the body even when the prefab rests aside.
    Facial bones follow the head rigidly because rigs differ: Midori (Battle)'s lower
    eyelids rest 3.5 cm from Midori's, enough to pull the eye out.

    Args:
        target: Slot hierarchy searched for available anchor names.
        source: Costume hierarchy whose node ancestry and carriers determine the anchor.
        node: Source node index to refit.
        carried: Optional unmatched-branch to source-carrier map overriding missing named anchors.

    Returns:
        Source/slot anchor indices, or None when no eligible anchor is shared.
    """
    carried = carried or {}
    n: int | None = node
    while n is not None:
        name = source.nodes[n].name
        if (BIPED_CHAIN.match(name) or name == BIPED_ROOT or (name.startswith("Bip001") and "Twist" in name)) and (
            same := target.first(name)
        ) is not None:
            return n, same
        if n in carried and (same := target.first(source.nodes[carried[n]].name)) is not None:
            return carried[n], same
        n = source.nodes[n].parent
    return None


def stored(costume: Costume, rest: list[np.ndarray], flat: Flat) -> Fit:
    """Build the stored shape by treating each part's vertex data as renderer-local coordinates.

    Args:
        costume: Source costume parts with their original mesh-space vertex arrays.
        rest: Source rest world matrices placing each renderer in prefab-root space.
        flat: Flattened costume supplying part vertex offsets for the resulting Fit.
    """
    vertices, linear = [], []
    for part, skin in zip(costume.parts, costume.skins):
        node = rest[part.node]
        vertices.append((np.c_[skin.vertices, np.ones(len(skin.vertices))] @ node.T)[:, :3])
        linear.append(np.repeat(node[None, :3, :3], len(skin.vertices), axis=0))
    return Fit(np.vstack(vertices), np.concatenate(linear), 0.0, 0.0, 0.0, list(flat.starts), "stored")


def _anchor_move(
    target: Skeleton,
    source: Skeleton,
    a: int,
    t: int,
    shape: str,
    t_rest: list[np.ndarray],
    s_rest: list[np.ndarray],
    carried: Mapping[int, int] | None = None,
) -> np.ndarray:
    """How a refit carries what anchors on the source's ``a`` to the slot's ``t`` (``refit``).

    "moved" is the joint's move alone; "refitted", its move and turn. The head goes with the neck: it keeps its own
    turn, but its joint goes where the neck's move takes it, not onto the slot's Head joint. Two rigs put the Head joint
    at different heights inside the head (Aris's 3.5 cm above her chin, Yuzu's 1.6 cm), so a whole head carried onto the
    slot's joint rises or sinks against the collar that the neck and spine carry, stretching the neck between (Yuzu's
    costume on Aris: her head 1.8 cm up out of her high collar) or vanished. The head then turns about the slot's joint.
    """
    m = t_rest[t] @ _inverse(s_rest[a], source.nodes[a].name)
    if shape == "moved":  # the joint's move alone, no turn
        m = np.eye(4)
        m[:3, 3] = t_rest[t][:3, 3] - s_rest[a][:3, 3]
    parent = source.nodes[a].parent
    if (
        source.nodes[a].name == HEAD
        and parent is not None
        and (hit := fit_anchor(target, source, parent, carried)) is not None
        and hit[0] != a
    ):
        joint = s_rest[a][:, 3]
        m[:3, 3] += (_anchor_move(target, source, *hit, shape, t_rest, s_rest, carried) @ joint - m @ joint)[:3]
    return m


def refit(
    target: Skeleton, source: Skeleton, flat: Flat, carried: Mapping[int, int] | None = None, shape: str = "refitted"
) -> Fit:
    """Refit a costume from its source rest skeleton to the slot's joints.

    Each vertex follows its bones by
    their anchors' transforms (``fit_anchor``, ``_anchor_move``), so the Biped's proportions change and hair, skirts
    and props move rigidly with their anchor; the skin weights stay. ``shape`` "moved": each anchor's joint move
    alone, "refitted": its move and turn. ``shift`` and ``largest``: how far the vertices move past the costume's
    common move (a slot whose prefab rests a metre aside, or lying, takes the costume along as a whole), for the log;
    what the refit does to the costume's edges counts in its stretch (``plan``). ``head``: where the source's head
    joint lands against the slot's.

    Args:
        target: Slot skeleton supplying destination rest joints and orientations.
        source: Incoming skeleton supplying source rest anchors.
        flat: Source prefab-rest geometry, influence nodes, and mesh-to-root linear transforms.
        carried: Optional unmatched-branch to source-carrier map used by fit_anchor.
        shape: "moved" translates anchors only; "refitted" also turns them to destination orientations.
    """
    t_rest, s_rest = target.world(), source.world()
    used = sorted(set(flat.joints[flat.weights > 0].tolist()) - {-1})
    matrices = [np.eye(4)]  # column 0: a bone outside the prefab or without weight
    columns = {}
    moves: dict[int, np.ndarray] = {}  # per source anchor
    for n in used:
        hit = fit_anchor(target, source, n, carried)
        if hit is None:
            columns[n] = 0
            continue
        a, t = hit
        if a not in moves:
            moves[a] = _anchor_move(target, source, a, t, shape, t_rest, s_rest, carried)
        columns[n] = len(matrices)
        matrices.append(moves[a])
    head = None
    s_head, t_head = source.first(HEAD), target.first(HEAD)
    if s_head is not None and t_head is not None:
        move = _anchor_move(target, source, s_head, t_head, shape, t_rest, s_rest, carried)
        head = (move @ s_rest[s_head][:, 3])[:3] - t_rest[t_head][:3, 3]
    stack = np.array(matrices)
    local = np.vectorize(lambda n: columns.get(int(n), 0), otypes=[int])(np.where(flat.weights > 0, flat.joints, -1))
    blended = drawn(Skin(flat.vertices, local, flat.weights, flat.edges), stack, np.eye(4))
    vertices = np.einsum("nij,nj->ni", blended, np.c_[flat.vertices, np.ones(len(flat.vertices))])[:, :3]
    delta = vertices - flat.vertices
    changed = np.linalg.norm(delta - np.median(delta, axis=0), axis=1) if len(delta) else np.zeros(0)
    size = float(np.ptp(flat.vertices, axis=0).max()) if len(flat.vertices) else 0.0
    linear = blended[:, :3, :3] @ flat.linear if flat.linear is not None else blended[:, :3, :3]
    return Fit(
        vertices,
        linear,
        float(np.percentile(changed, 99)) if len(changed) else 0.0,
        float(changed.max()) if len(changed) else 0.0,
        size,
        list(flat.starts),
        shape,
        head,
    )


def plan(
    target: Costume,
    source: Costume,
    probes: list[list[np.ndarray]],
    *,
    fit: bool = True,
    own_probes: list[list[np.ndarray]] | None = None,
    clips: Iterable[Any] = (),
) -> Plan:
    """Choose bone mapping, follow/rigid groups, and the least-stretched supported bound shape.

    The mapping (``map_bones``) takes the carriers of the unmatched bones from the source's own clips
    (``own_probes``, see ``carriers``) and leaves out the slot's hidden bones: those the probes hide, and those any of
    the slot's ``clips`` scales to nothing (:func:`scaled_away`). With ``fit`` (the costume's geometry can
    be rewritten), a prop the costume rests aside and its own clips hold on the body is moved there and carried
    (``stowed``), and each of ``SHAPES`` is judged under the slot's clips and the least stretch wins, a later shape only
    by more than ``SHAPE_MARGIN``; without, the costume keeps its stored shape.

    Args:
        target: Slot costume defining available bones and baseline stretch.
        source: Incoming costume whose bones and geometry are mapped and judged.
        probes: Slot world poses used to judge stretch and hidden bones.
        fit: Whether source geometry can be rewritten, enabling placement and refit candidates.
        own_probes: Source-owned world poses for carrier/stowed-prop detection; None provides no motion evidence.
        clips: Slot clips checked for scaled-away bones beyond those hidden in probes.

    Raises:
        ServiceError: A weighted source bone is outside the prefab or a mapping/rest-matrix check fails.
    """
    s_rest = source.skeleton.world()
    flat_source = flatten(source, s_rest)
    sj, sw = flat_source.joints, flat_source.weights
    if (sj[sw > 0] < 0).any():
        raise refuse("costume bone outside the prefab")
    carried = carriers(target.skeleton, source.skeleton, flat_source, own_probes or [])
    props = stowed(target.skeleton, source.skeleton, flat_source, own_probes or [], carried) if fit else {}
    carried = {**carried, **{m: c for m, (c, _, _) in props.items()}}
    hidden = hidden_bones(target.skeleton, probes) | scaled_away(target.skeleton, clips)
    mapping = map_bones(target.skeleton, source.skeleton, source.bones(), carried, hidden)
    own_mesh = OwnMesh.of(target)
    posed_targets = [own_mesh.posed(p) for p in probes]
    own = max((stretch(own_mesh.rest, posed, own_mesh.edges) for posed in posed_targets), default=1.0)
    shapes = [stow(stored(source, s_rest, flat_source), flat_source, props)]
    if fit:
        drawn_ = stow(
            Fit(flat_source.vertices, flat_source.linear, 0.0, 0.0, 0.0, list(flat_source.starts), "drawn"),
            flat_source,
            props,
        )
        flat_source = dataclasses.replace(flat_source, vertices=drawn_.vertices, linear=drawn_.linear)
        shapes.append(drawn_)
        shapes += [
            refit(target.skeleton, source.skeleton, flat_source, carried, shape) for shape in ("moved", "refitted")
        ]
    best: Plan | None = None
    judged = []
    for shape in shapes:
        # a refit's own change of the costume counts: measured from the shape the source draws
        reference = shape.vertices if shape.shape == "stored" else flat_source.vertices
        candidate = _judge(
            target, source, probes, flat_source, shape, reference, dict(mapping), carried, own_mesh, posed_targets, own
        )
        judged.append(f"{shape.shape} {candidate.stretch:.2f}")
        if best is None or candidate.stretch < best.stretch * (1 - SHAPE_MARGIN):
            best = candidate
    assert best is not None
    logger.debug("Costume shapes judged: %s; %s chosen", ", ".join(judged), best.fit.shape if best.fit else "?")
    return best


def _judge(
    target: Costume,
    source: Costume,
    probes: list[list[np.ndarray]],
    flat_source: Flat,
    shape: Fit,
    reference: np.ndarray,
    mapping: dict[int, int],
    carried: Mapping[int, int],
    own_mesh: OwnMesh,
    posed_targets: list[np.ndarray],
    own: float,
) -> Plan:
    """One shape's plan: each group's choice, the rest pose and the stretch.

    ``mapping`` is changed in place; the stretch is measured from ``reference`` (the costume's shape before the refit).
    """
    t_rest = target.skeleton.world()
    sv, sj, sw, se = shape.vertices, flat_source.joints, flat_source.weights, flat_source.edges
    tv, te, tdom = own_mesh.rest, own_mesh.edges, own_mesh.dominant
    sdom = np.array([row[np.argmax(w)] for row, w in zip(sj, sw)])
    nodes = sorted(set(sj[sw > 0].tolist()))
    index = {n: k for k, n in enumerate(nodes)}
    joints = np.vectorize(lambda n: index.get(n, 0))(np.where(sj < 0, nodes[0], sj))
    flat = Skin(sv, joints, sw, se)

    inverses: dict[int, np.ndarray] = {}

    def rest_inverse(node: int) -> np.ndarray:
        if node not in inverses:
            inverses[node] = _inverse(t_rest[node], target.skeleton.nodes[node].name)
        return inverses[node]

    def matrices(pose: list[np.ndarray], m: Mapping[int, int]) -> np.ndarray:
        return np.array([pose[m[n]] @ rest_inverse(m[n]) for n in nodes])

    groups: dict[int, list[int]] = {}
    for n in nodes:
        if not source.skeleton.nodes[n].name.startswith("Bip001"):
            anchor = source.skeleton.biped_ancestor(n)
            if anchor is not None:
                groups.setdefault(anchor, []).append(n)
    choices = {}
    for anchor, members in groups.items():
        rigid = mapping[anchor]
        mask = np.isin(sdom, members)
        mask = mask[se[:, 0]] & mask[se[:, 1]]
        slot_name = source.skeleton.nodes[carried.get(anchor, anchor)].name  # a Biped bone the slot lacks: its carrier
        if not probes or not mask.any():
            follow = False
        else:
            t_members = [
                j
                for j in set(tdom.tolist())
                if not target.skeleton.nodes[j].name.startswith("Bip001")
                and target.skeleton.biped_ancestor(j) is not None
                and target.skeleton.nodes[target.skeleton.biped_ancestor(j)].name == slot_name
            ]
            t_mask = np.isin(tdom, t_members)
            t_mask = t_mask[te[:, 0]] & t_mask[te[:, 1]]
            group_own = (
                max((stretch(tv, posed, te, t_mask) for posed in posed_targets), default=1.0) if t_mask.any() else 1.0
            )
            mine = max(stretch(reference, deform(flat, matrices(p, mapping)), se, mask) for p in probes)
            follow = mine <= max(STRETCH_FLOOR, group_own)
        choices[anchor] = "follow" if follow else "rigid"
        if not follow:
            for n in members:
                mapping[n] = rigid
    rest = deform(flat, matrices(t_rest, mapping))
    if float(np.abs(rest - sv).max()) > 1e-4:
        raise refuse("rest pose not reproduced")
    mine = max((stretch(reference, deform(flat, matrices(p, mapping)), se) for p in [t_rest, *probes]), default=1.0)
    return Plan(mapping, choices, mine, own, groups, mine > stretch_limit(own), shape)
