"""Identify small items worn with a 3D costume, such as hats, glasses, masks, bags, and belt bottles.

The prefab draws them with renderers of their own, beside the costume's; nothing in the files marks them (names,
shaders, layers and tags say nothing), so they are recognised by how the model's own clips treat them. An item is the
bones of one renderer that is not a costume part, joined by the triangles they skin (one mesh often holds several
things the clips show and hide one by one: a drill, a remote and a towel). An item is worn when:

1. it is not gear: not below the aim IK's weapons (``MxAimIK`` ``mainWeapon``, ``subWeapon``), not holding a bone
   effects attach to off the body (``fxParentBones``: a muzzle), not on a ``Bip001_Weapon*`` node, and its renderer
   holds no such item; nor the halo, nor a costume part;
2. its GameObject is active and its renderer enabled in the prefab;
3. the clips that move the body show it (at a quarter of its modelled size or more, within ``REACH`` of the body) in
   every sampled frame of ``SHARE`` of them or more, and some frame draws it at a quarter of the size its mesh holds;
4. it is not parked inside the body: its farthest point lies ``INSIDE`` or more from the skeleton (median over frames);
5. it follows a body bone: the bone it moves least against (its centre in that bone's frame) is no upper arm, forearm,
   hand or finger (a thing held in a hand stays with the slot), and it moves against it less than ``FOLLOW`` times its
   motion in the prefab's space.

Things the clips show only at times (magazines, EX skill props, food), guns and effect items belong to the slot's
animation and stay. On a costume switch (``model_adapt``) the slot's worn items draw nothing and the incoming
costume's worn items are carried: their mesh, materials and textures added like a costume part, bound rigidly to the
slot's bone of the name they follow, where the source's idle holds them against it.

A thing the costume encloses is packed: bottles in a cool bag, a handgun in its holster. The clips show it all the
time, yet nobody sees it; it belongs inside its costume. An item is packed when it is no gear, the clips show it like
a worn one (rules 2 to 4: shown in ``SHARE`` of the clips, outside the skeleton) and, in ``PACKED`` or more of the
clips that show it (their first frame showing it), ``ENCLOSED`` or more of its sampled points are enclosed: a ray from
the point in each of 26 directions meets a costume triangle within ``RAY``. Things drawn with the eye, mouth or
eyebrow shading are never packed: the game draws them over the face and hair (sparkles in the eyes). The slot's
packed items draw nothing, like its worn ones (their costume leaves), and the source's are not carried (carried
rigidly, they would stick out of a costume that moves around them otherwise). Things inside the skeleton (a coin,
smoke shells) and things shown only at times stay with the slot: any body hides the first, the slot's
clips take the second out. The verdict of a model is
cached by the caller (:class:`WornStore`), per model and game revision.
"""

from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import numpy as np

from . import model_adapt as ma
from . import model_geometry as mg
from .model import _key
from .model_hems import pack_bits, unpack_bits

logger = logging.getLogger(__name__)

FRAMES = 7  # frames sampled per clip
SAMPLES = 60  # vertices sampled per item, about
SHOWN = 0.25  # drawn at this share of its modelled size or more: shown
REACH = 2.0  # ... and within this far of the body (metres): some clips park a thing far away instead
SHARE = 0.75  # shown in every sampled frame of this share of the clips that move the body, or more
INSIDE = 0.05  # its farthest point at least this far from the skeleton (median over frames), else parked inside
FOLLOW = 0.8  # moves against the bone it follows less than this times its motion in the prefab's space
PACKED = 0.75  # enclosed by the costume in this share of the clips showing it, or more: packed
ENCLOSED = 0.75  # ... a frame encloses it when this share of its sampled points is enclosed
RAY = 0.4  # a point is enclosed when a ray in every direction meets a costume triangle this near (metres)
PACK_POINTS = 12  # points sampled per item for that
OVER_FACE = {"eyemouth", "eyebrow"}  # shading the game draws over the face and hair
EDGE = 1e-6  # a ray through a shared edge counts for both triangles
ARM = re.compile(r"(UpperArm|Forearm|Hand|Finger\d*)$")  # bones a thing held in a hand follows
WEAPON_NODE = "Bip001_Weapon"
VERSION = 2  # the layout of a stored verdict


class WornStore(Protocol):
    """Cache Worn.saved verdicts per model dev name and game revision."""

    def get(self, dev: str) -> Mapping | None:
        """Read a model dev name's saved verdict for the cache's game revision, or None on a miss."""
        ...

    def put(self, dev: str, verdict: Mapping) -> None:
        """Store a model's worn-item verdict for the cache's game revision.

        Args:
            dev: Model dev name identifying the measured prefab.
            verdict: Path-based Worn.saved record to retain without Unity readers.
        """
        ...


@dataclass
class Item:
    """One thing a renderer draws: the bones of its branch(es) joined by triangles, with what the clips do to it."""

    part: ma.Part
    top: int  # the node standing for it (its first branch's top)
    bones: frozenset[int]
    rows: np.ndarray  # the mesh's vertices it dominates
    triangles: np.ndarray  # its triangles, numbered as ``read_mesh`` lists them (submeshes in order)
    native: float  # its longest side in the mesh's space
    gear: bool = False
    # what the clips do to it
    clips: int = 0  # clips that move the body
    full: int = 0  # of them, those showing it in every sampled frame
    some: int = 0  # those showing it in some frame
    size: float | None = None  # its longest side where the reference pose draws it
    follow: str | None = None  # the body bone it moves least against
    f_mean: float | None = None  # its mean motion against that bone (metres)
    w_mean: float | None = None  # its mean motion in the prefab's space
    reach: float | None = None  # its farthest point from the skeleton, median over the frames showing it
    ref: tuple[str, int] | None = None  # the pose to place it from: None for the idle, else (clip, frame)
    enclosed: float | None = None  # share of the clips showing it whose first frame showing it encloses it
    carry: bool = True  # (in a verdict) carried onto another slot; packed items are not
    _active: bool = True  # its GameObject active and its renderer enabled in the prefab
    _shown: list[int] = field(default_factory=list, repr=False)  # the measured frames showing it

    def visible(self) -> bool:
        """Check that the prefab enables the item and some clip draws it at least a quarter of its modelled size."""
        return bool(
            self._active and self.clips and self.some and self.size is not None and self.size >= SHOWN * self.native
        )

    def worn(self) -> bool:
        """Apply the module's worn-item rule for persistent visibility, body clearance, and non-arm following."""
        if self.gear or not self.visible() or self.full < SHARE * self.clips:
            return False
        if self.reach is None or self.reach < INSIDE:
            return False  # parked inside the body (a coin, smoke shells)
        ratio = (self.f_mean or 0.0) / max(self.w_mean or 0.0, 1e-6) if self.w_mean is not None else 9.0
        return ratio < FOLLOW and not ARM.search(self.follow or "")

    def packable(self) -> bool:
        """Check worn-item rules 1 to 4 and exclude face overlays before testing enclosure."""
        return bool(
            not self.gear
            and self.visible()
            and self.full >= SHARE * self.clips
            and self.reach is not None
            and self.reach >= INSIDE
            and not set(self.part.kinds) & OVER_FACE
        )

    def packed(self) -> bool:
        """Check that a packable item is enclosed in at least PACKED of the clips showing it."""
        return bool(self.packable() and self.enclosed is not None and self.enclosed >= PACKED)


@dataclass
class Worn:
    """Group the worn and packed items that belong to a model's costume.

    whole holds renderers drawing no other visible items; packed items have carry False.
    """

    items: list[Item] = field(default_factory=list)
    whole: set[int] = field(default_factory=set)  # renderer nodes

    def by_renderer(self) -> dict[int, list[Item]]:
        """Group measured items by their renderer's skeleton node index."""
        out: dict[int, list[Item]] = {}
        for item in self.items:
            out.setdefault(item.part.node, []).append(item)
        return out

    def saved(self, skeleton: ma.Skeleton) -> dict:
        """Serialize the verdict with hierarchy paths rather than transient node indices.

        Args:
            skeleton: Measured model hierarchy supplying prefab, renderer, and item paths.
        """
        nodes = skeleton.nodes
        return {
            "version": VERSION,
            "prefab": nodes[0].name if nodes else "",
            "renderers": [
                {
                    "renderer": nodes[node].path,
                    "whole": node in self.whole,
                    "items": [
                        {
                            "top": nodes[i.top].path,
                            "follow": i.follow,
                            "ref": list(i.ref) if i.ref else None,
                            "carry": i.carry,
                        }
                        for i in items
                    ],
                }
                for node, items in sorted(self.by_renderer().items())
            ],
        }

    @classmethod
    def restored(cls, skeleton: ma.Skeleton, parts: list[ma.Part], data: Mapping) -> "Worn | None":
        """Restore a saved verdict against the same model and prefab.

        Args:
            skeleton: Current hierarchy whose prefab and paths must match the saved record.
            parts: Current renderer parts used to reconstruct each item's mesh/bone grouping.
            data: Worn.saved mapping containing the schema, prefab, and path-based verdicts.

        Returns:
            The restored verdict, or None for invalid data, a different prefab, or a missing renderer/item.
        """
        by_path = {skeleton.nodes[p.node].path: p for p in parts}
        out = cls()
        try:
            if data["version"] != VERSION or data["prefab"] != (skeleton.nodes[0].name if skeleton.nodes else ""):
                return None
            for entry in data["renderers"]:
                part = by_path.get(entry["renderer"])
                if part is None or part.mesh is None:
                    return None
                split = {skeleton.nodes[i.top].path: i for i in split_items(skeleton, part)}
                for saved in entry["items"]:
                    item = split.get(saved["top"])
                    if item is None:
                        return None
                    item.follow, item.ref = saved["follow"], tuple(saved["ref"]) if saved["ref"] else None
                    item.carry = bool(saved["carry"])
                    out.items.append(item)
                if entry["whole"]:
                    out.whole.add(part.node)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return out


# --- the items -----------------------------------------------------------------------------------------------------


def _under(skeleton: ma.Skeleton, i: int, top: int) -> bool:
    p, q = skeleton.nodes[i].path, skeleton.nodes[top].path
    return p == q or p.startswith(q + "/")


def _ancestors(skeleton: ma.Skeleton, i: int) -> Iterable[int]:
    p = skeleton.nodes[i].parent
    while p is not None:
        yield p
        p = skeleton.nodes[p].parent


def split_items(skeleton: ma.Skeleton, part: ma.Part) -> list[Item]:
    """Split a renderer into bone branches joined into items by mesh triangles.

    Each skinned bone stands for the top of its branch among the renderer's bones.

    Args:
        skeleton: Model hierarchy defining ancestor relationships between skin bones.
        part: Renderer part whose dominant skin influences and triangles define items.

    Returns:
        Items with vertex/triangle indices, or an empty list for unreadable or unskinned meshes.
    """
    try:
        skin = ma.skin_of(part.mesh)
    except Exception:  # an unskinned mesh or one that cannot be read: nothing to split
        return []
    bone_set = {b for b in part.bones if b is not None}
    count = len(skin.vertices)
    if not count:
        return []
    dom = skin.joints[np.arange(count), np.argmax(skin.weights, axis=1)]
    used = {part.bones[j] for j in set(dom.tolist()) if j < len(part.bones) and part.bones[j] is not None}
    branch_of = {}
    for b in sorted(used):
        top = b
        for a in _ancestors(skeleton, b):
            if a in bone_set:
                top = a
        branch_of[b] = top
    link = {t: t for t in set(branch_of.values())}

    def find(t: int) -> int:
        while link[t] != t:
            link[t] = link[link[t]]
            t = link[t]
        return t

    vertex_branch = np.array([branch_of.get(part.bones[j] if j < len(part.bones) else None, -1) for j in dom])
    tris = skin.triangles if skin.triangles is not None else np.zeros((0, 3), int)
    for a, b in ((0, 1), (1, 2)):
        x, y = vertex_branch[tris[:, a]], vertex_branch[tris[:, b]]
        keep = (x != y) & (x >= 0) & (y >= 0)
        for p, q in set(zip(x[keep].tolist(), y[keep].tolist())):
            rp, rq = find(p), find(q)
            if rp != rq:
                link[max(rp, rq)] = min(rp, rq)
    groups: dict[int, set[int]] = {}
    for b, t in branch_of.items():
        groups.setdefault(find(t), set()).add(b)
    # each triangle goes with the item of its first vertex that has one
    tri_root = np.full(len(tris), -1)
    for k in range(3):
        unset = tri_root < 0
        branch = vertex_branch[tris[unset, k]] if len(tris) else np.zeros(0, int)
        tri_root[np.where(unset)[0]] = [find(t) if t >= 0 else -1 for t in branch.tolist()]
    items = []
    for root, bones in sorted(groups.items()):
        js = [j for j, b in enumerate(part.bones) if b in bones]
        rows = np.where(np.isin(dom, js))[0]
        native = skin.vertices[rows]
        items.append(
            Item(
                part,
                root,
                frozenset(bones),
                rows,
                np.where(tri_root == root)[0],
                float(np.ptp(native, axis=0).max()) if len(rows) else 0.0,
            )
        )
    return items


def _gear_nodes(skeleton: ma.Skeleton, body: set[int]) -> tuple[list[int], list[int]]:
    """The aim IK's weapons, and the bones effects attach to off the body (a muzzle on a gun)."""
    g = skeleton.graph
    mounts, fx = [], []
    for behaviour in skeleton.behaviours:
        script = g.script(behaviour)
        tree = g.tree(behaviour)
        if script == "MxAimIK":
            for name in ("mainWeapon", "subWeapon"):
                obj = g.deref(behaviour, tree.get(name))
                n = skeleton.by_transform.get(_key(obj)) if obj is not None else None
                if n is not None:
                    mounts.append(n)
        elif script and "AnimationEventReceiver" in script:
            for pointer in tree.get("fxParentBones", []) or []:
                obj = g.deref(behaviour, pointer)
                n = skeleton.by_transform.get(_key(obj)) if obj is not None else None
                if (
                    n is not None
                    and n not in body
                    and skeleton.nodes[n].parent is not None
                    and not skeleton.nodes[n].name.startswith("Bip001 ")
                    and skeleton.nodes[n].name not in ("Bip001", "bone_root")
                ):
                    fx.append(n)
    return mounts, fx


def _is_gear(skeleton: ma.Skeleton, item: Item, mounts: list[int], fx: list[int]) -> bool:
    top = item.top
    if any(_under(skeleton, top, m) or _under(skeleton, m, top) for m in mounts):
        return True
    if any(i in item.bones or _under(skeleton, i, top) for i in fx):
        return True
    names = [skeleton.nodes[b].name for b in item.bones] + [skeleton.nodes[a].name for a in _ancestors(skeleton, top)]
    return any(name.startswith(WEAPON_NODE) for name in names)


def _lin(m: np.ndarray) -> float:
    return float(abs(np.linalg.det(m[:3, :3])) ** (1 / 3))


def _segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance of each point to each segment a[k]..b[k]: (points, segments)."""
    ab = b - a
    t = np.einsum("pkj,kj->pk", points[:, None, :] - a[None], ab) / np.maximum((ab * ab).sum(axis=1), 1e-12)
    t = np.clip(t, 0, 1)
    closest = a[None] + t[:, :, None] * ab[None]
    return np.linalg.norm(points[:, None, :] - closest, axis=2)


def measure(
    skeleton: ma.Skeleton,
    parts: list[ma.Part],
    clips: Sequence[Any],
    idle: list[np.ndarray] | None,
    step: Callable[[], None] = lambda: None,
) -> list[Item]:
    """Measure non-costume, non-halo items under the model's own clips.

    Args:
        skeleton: Model hierarchy supplying body bones, transforms, and clip sampling.
        parts: Prefab renderer parts; costume parts and halo renderers are excluded.
        clips: Model-owned clip readers; only readable clips moving the body contribute.
        idle: First idle-frame world matrices used as the placement reference; None uses visible clip frames.
        step: No-argument yield/cancellation callback after clip reads, each item, and enclosure measurements.

    Returns:
        Items with visibility, body following, reference-pose, and enclosure measurements.
    """
    nodes = skeleton.nodes
    g = skeleton.graph
    rig = skeleton.main_rig()
    body = [
        i for i, n in enumerate(nodes) if ma.BIPED_CHAIN.match(n.name) and (rig is None or _under(skeleton, i, rig))
    ]
    body_set = set(body)
    mounts, fx = _gear_nodes(skeleton, body_set)
    items: list[Item] = []
    for part in parts:
        if part.role in ("primary", "costume", "alternate", "copy", "halo") or part.mesh is None:
            continue
        found = split_items(skeleton, part)
        gear = any(_is_gear(skeleton, item, mounts, fx) for item in found)
        active = nodes[part.node].active and bool(g.tree(part.renderer)["m_Enabled"])
        for item in found:
            item.gear, item._active = gear, active
        items += found
    if not items or not body:
        return items
    rest = skeleton.world()
    segs = [(c, nodes[c].parent) for c in body if nodes[c].parent in body_set]
    # the clips that move the body, FRAMES poses each
    rows: list[tuple[str, list[list[np.ndarray]]]] = []
    for clip in clips:
        try:
            poses, animated = skeleton.clip_poses(clip, FRAMES)
        except Exception:  # a clip this reader cannot decode is left out, as the probes do
            continue
        if not any(b in animated for b in body):
            continue  # a shared clip that binds nothing in this body (an override's original)
        rows.append((clip.peek_name() or "", poses))
    frames = [(k, f, pose) for k, (_, poses) in enumerate(rows) for f, pose in enumerate(poses)]
    if not frames:
        return items
    seg_a = np.array([[pose[i][:3, 3] for i, _ in segs] for _, _, pose in frames]) if segs else None
    seg_b = np.array([[pose[j][:3, 3] for _, j in segs] for _, _, pose in frames]) if segs else None
    body_inverse = np.linalg.inv(np.array([[pose[c] for c in body] for _, _, pose in frames]))  # (frames, body, 4, 4)
    body_scale = np.array([_lin(rest[c]) for c in body])
    idle_segs = (
        (np.array([idle[i][:3, 3] for i, _ in segs]), np.array([idle[j][:3, 3] for _, j in segs]))
        if idle is not None and segs
        else None
    )
    step()
    for item in items:
        _measure_item(skeleton, item, rows, frames, seg_a, seg_b, body, body_inverse, body_scale, idle, idle_segs)
        step()
    _enclosure(skeleton, parts, items, frames, step)
    return items


def _measure_item(
    skeleton: ma.Skeleton,
    item: Item,
    rows: list,
    frames: list,
    seg_a: np.ndarray | None,
    seg_b: np.ndarray | None,
    body: list[int],
    body_inverse: np.ndarray,
    body_scale: np.ndarray,
    idle: list[np.ndarray] | None,
    idle_segs: tuple[np.ndarray, np.ndarray] | None,
) -> None:
    part = item.part
    skin = ma.skin_of(part.mesh)
    bp = ma.bindposes(skeleton.graph, part.mesh)
    count = len(skin.vertices)
    dom = skin.joints[np.arange(count), np.argmax(skin.weights, axis=1)]
    js = [j for j, b in enumerate(part.bones) if b in item.bones and j < len(bp)]
    if not js or not len(item.rows):
        return
    sample = item.rows[:: max(1, len(item.rows) // SAMPLES)]
    vj = dom[sample]
    local = np.einsum("nij,nj->ni", bp[vj], np.c_[skin.vertices[sample], np.ones(len(sample))])
    vb = np.array([part.bones[j] for j in vj])
    pairs = [(part.bones[j], bp[j]) for j in js]

    def points_at(pose: list[np.ndarray]) -> np.ndarray:
        return np.einsum("nij,nj->ni", np.array([pose[b] for b in vb]), local)[:, :3]

    def scale_of(pose: list[np.ndarray]) -> float:
        return max(_lin(pose[b] @ m) for b, m in pairs)

    def near(points: np.ndarray, a: np.ndarray | None, b: np.ndarray | None) -> np.ndarray | None:
        return None if a is None else _segment_distance(points, a, b)

    shown = []
    points = []
    for n, (_, _, pose) in enumerate(frames):
        here = points_at(pose)
        points.append(here)
        d = near(here, seg_a[n] if seg_a is not None else None, seg_b[n] if seg_b is not None else None)
        shown.append(scale_of(pose) >= SHOWN and (d is None or float(d.min()) <= REACH))
    per_clip: dict[int, list[bool]] = {}
    for (k, _, _), s in zip(frames, shown):
        per_clip.setdefault(k, []).append(s)
    item._shown = [n for n, s in enumerate(shown) if s]
    item.clips = len(per_clip)
    item.full = sum(1 for flags in per_clip.values() if all(flags))
    item.some = sum(1 for flags in per_clip.values() if any(flags))
    chosen = [n for n, s in enumerate(shown) if s]
    if not chosen:
        return
    idle_shown = False
    if idle is not None:
        here = points_at(idle)
        d = near(here, *idle_segs) if idle_segs is not None else None
        idle_shown = scale_of(idle) >= SHOWN and (d is None or float(d.min()) <= REACH)
    if idle_shown:
        ref_points, item.ref = points_at(idle), None
    else:
        k, f, _ = frames[chosen[0]]
        ref_points, item.ref = points[chosen[0]], (rows[k][0], f)
    item.size = float(np.ptp(ref_points, axis=0).max())
    centre = np.array([points[n].mean(axis=0) for n in chosen])
    homogeneous = np.c_[centre, np.ones(len(centre))]
    q = np.einsum("fbij,fj->fbi", body_inverse[chosen], homogeneous)[:, :, :3] * body_scale[None, :, None]
    d = np.linalg.norm(q - np.median(q, axis=0)[None], axis=2)  # (frames, body)
    means = d.mean(axis=0)
    best = int(np.argmin(means))
    item.follow, item.f_mean = skeleton.nodes[body[best]].name, float(means[best])
    item.w_mean = float(np.linalg.norm(centre - np.median(centre, axis=0), axis=1).mean())
    if seg_a is not None:
        reach = [float(_segment_distance(points[n], seg_a[n], seg_b[n]).min(axis=1).max()) for n in chosen]
        item.reach = float(np.median(reach))


_DIRECTIONS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1) if x or y or z], float)
_DIRECTIONS /= np.linalg.norm(_DIRECTIONS, axis=1, keepdims=True)


def enclosed_points(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Test whether cloth surrounds each point within RAY metres in all 26 sampled directions.

    Args:
        points: Test positions in prefab-root space, in metres.
        triangles: Cloth corner positions in the same space, shaped as triangles by 3 by 3.
    """
    out = np.zeros(len(points), bool)
    if not len(triangles):
        return out
    low, high = triangles.min(axis=1), triangles.max(axis=1)
    for k, origin in enumerate(points):
        near = ((high >= origin - RAY) & (low <= origin + RAY)).all(axis=1)
        if not near.any():
            continue
        a = triangles[near, 0]
        e1, e2 = triangles[near, 1] - a, triangles[near, 2] - a
        p = np.cross(_DIRECTIONS[:, None, :], e2[None])  # (directions, triangles, 3)
        det = np.einsum("tj,dtj->dt", e1, p)
        ok = np.abs(det) > 1e-12
        inverse = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = origin[None] - a
        u = np.einsum("tj,dtj->dt", s, p) * inverse
        q = np.cross(s, e1)
        v = np.einsum("dj,tj->dt", _DIRECTIONS, q) * inverse
        t = np.einsum("tj,tj->t", e2, q)[None] * inverse
        hit = (
            ok & (u >= -EDGE) & (v >= -EDGE) & (u + v <= 1 + EDGE) & (t > 1e-4) & (t < RAY)
        )  # an edge meets both sides
        out[k] = bool(hit.any(axis=1).all())
    return out


def _enclosure(
    skeleton: ma.Skeleton, parts: list[ma.Part], items: list[Item], frames: list, step: Callable[[], None]
) -> None:
    """Measure ``Item.enclosed`` for every item that may be packed.

    Of the clips showing the item, this is the share whose first frame showing it has the costume (its skinned
    triangles at that pose) enclose ``ENCLOSED`` of its points.
    """
    wanted = [item for item in items if item.packable() and item._shown]
    if not wanted:
        return
    costume = []
    for part in parts:
        if part.role not in ("primary", "costume", "alternate") or part.mesh is None:
            continue
        try:
            skin = ma.skin_of(part.mesh)
            costume.append((part, skin, ma.bindposes(skeleton.graph, part.mesh)))
        except Exception:  # a part that cannot be read encloses nothing
            continue
    if not costume:
        return

    def matrices(part: ma.Part, bp: np.ndarray, pose: list[np.ndarray]) -> np.ndarray:
        return np.array(
            [pose[b] @ bp[j] if b is not None and j < len(bp) else pose[part.node] for j, b in enumerate(part.bones)]
            or [pose[part.node]]
        )

    def skinned(skin: ma.Skin, carry: np.ndarray) -> np.ndarray:
        return ma.deform(
            ma.Skin(skin.vertices, np.minimum(skin.joints, len(carry) - 1), skin.weights, skin.edges), carry
        )

    posed: dict[int, np.ndarray] = {}

    def costume_at(n: int) -> np.ndarray:
        if n not in posed:
            pose = frames[n][2]
            posed[n] = np.concatenate(
                [
                    skinned(skin, matrices(part, bp, pose))[skin.triangles]
                    for part, skin, bp in costume
                    if skin.triangles is not None and len(skin.triangles)
                ]
                or [np.zeros((0, 3, 3))]
            )
        return posed[n]

    for item in wanted:
        try:
            skin = ma.skin_of(item.part.mesh)
            bp = ma.bindposes(skeleton.graph, item.part.mesh)
        except Exception:  # read by the measure already; a failure leaves it unjudged
            continue
        rows = item.rows[np.linspace(0, len(item.rows) - 1, min(PACK_POINTS, len(item.rows))).astype(int)]
        sub = ma.Skin(skin.vertices[rows], skin.joints[rows], skin.weights[rows], skin.edges)
        first: dict[int, int] = {}
        for n in item._shown:
            first.setdefault(frames[n][0], n)
        inside = 0
        for n in first.values():
            points = skinned(sub, matrices(item.part, bp, frames[n][2]))
            inside += float(enclosed_points(points, costume_at(n)).mean()) >= ENCLOSED
        item.enclosed = inside / len(first)
        step()


def worn_items(
    skeleton: ma.Skeleton,
    parts: list[ma.Part],
    clips: Sequence[Any],
    idle: list[np.ndarray] | None,
    step: Callable[[], None] = lambda: None,
) -> Worn:
    """Identify worn/packed items and renderers drawing no other visible items.

    Args:
        skeleton: Model hierarchy used to measure body following and skinning.
        parts: Renderer parts to classify by the module's worn and packed rules.
        clips: Model-owned AnimationClip readers used for visibility and enclosure sampling.
        idle: First idle-frame world matrices for item placement; None uses sampled visible poses.
        step: No-argument yield/cancellation callback during clip and item measurements.
    """
    items = measure(skeleton, parts, clips, idle, step)
    goes = [item for item in items if item.worn() or item.packed()]
    for item in goes:
        item.carry = not item.packed()
    worn = Worn(goes)
    going = {id(item) for item in goes}
    for node in {item.part.node for item in goes}:
        if all(id(item) in going or not item.visible() for item in items if item.part.node == node):
            worn.whole.add(node)
    return worn


def worn_of(
    skeleton: ma.Skeleton,
    parts: list[ma.Part],
    clips: Sequence[Any],
    idle: list[np.ndarray] | None,
    dev: str,
    store: WornStore | None = None,
    step: Callable[[], None] = lambda: None,
) -> Worn:
    """Read or measure a model's worn-item verdict through the caller's revision-scoped cache.

    Args:
        skeleton: Current model hierarchy used to validate saved paths or measure items.
        parts: Current renderer parts for reconstructing or measuring item groups.
        clips: Model-owned AnimationClip readers used on a cache miss.
        idle: First idle-frame world matrices; None uses sampled visible poses when measuring.
        dev: Model dev name identifying the cache entry.
        store: Optional revision-scoped verdict cache; read failures are logged and treated as misses.
        step: No-argument yield/cancellation callback used by measure on cache misses.
    """
    if store is not None:
        try:
            data = store.get(dev)
        except Exception as error:  # a cache that cannot be read: measured again
            logger.debug("Worn items of %s: cache not read (%s)", dev, error)
            data = None
        if isinstance(data, Mapping):
            restored = Worn.restored(skeleton, parts, data)
            if restored is not None:
                return restored
    worn = worn_items(skeleton, parts, clips, idle, step)
    if store is not None:
        try:
            store.put(dev, worn.saved(skeleton))
        except Exception as error:  # the verdict is used all the same; measured again next time
            logger.debug("Worn items of %s: cache not written (%s)", dev, error)
    return worn


# --- mesh edits ----------------------------------------------------------------------------------------------------

INDEX_CODES = {0: ("H", 2), 1: ("I", 4)}


def drop_triangles(tree: dict, drop: np.ndarray) -> bool:
    """Make selected triangles degenerate by repeating their first corner, preserving counts and offsets.

    Args:
        tree: Mutable Mesh typetree with plain or packed triangle-list indices.
        drop: Triangle numbers in read_mesh order, concatenating submeshes in order.

    Returns:
        True after rewriting; False without mutation for non-triangle topology or unreadable indices.
    """
    subs = tree.get("m_SubMeshes") or []
    if any(int(s.get("topology", 0)) != 0 for s in subs):
        return False
    compressed = (tree.get("m_CompressedMesh") or {}).get("m_Triangles") or {}
    packed = bool(int(compressed.get("m_NumItems") or 0))
    if packed:
        indices = list(unpack_bits(compressed))
        size = 4 if int(tree.get("m_IndexFormat", 0)) == 1 else 2
    else:
        raw = tree.get("m_IndexBuffer")
        fmt = INDEX_CODES.get(int(tree.get("m_IndexFormat", 0)))
        if raw is None or fmt is None:
            return False
        code, size = fmt
        data = bytes(raw)
        indices = list(struct.unpack(f"<{len(data) // size}{code}", data[: len(data) // size * size]))
    starts, number = [], 0
    for s in subs:
        first = int(s["firstByte"]) // size
        count = int(s["indexCount"]) // 3
        if first + 3 * count > len(indices):
            return False
        starts.append((number, first, count))
        number += count
    wanted = set(int(t) for t in drop)
    for number, first, count in starts:
        for t in range(count):
            if number + t in wanted:
                at = first + 3 * t
                indices[at + 1] = indices[at + 2] = indices[at]
    if packed:
        data = pack_bits(indices, int(compressed["m_BitSize"]))
        compressed["m_Data"] = data if isinstance(compressed.get("m_Data"), (bytes, bytearray)) else list(data)
    else:
        code, size = INDEX_CODES[int(tree.get("m_IndexFormat", 0))]
        data = struct.pack(f"<{len(indices)}{code}", *indices) + bytes(raw)[len(indices) * size :]
        tree["m_IndexBuffer"] = data if isinstance(raw, (bytes, bytearray)) else list(data)
    return True


# --- the switch ----------------------------------------------------------------------------------------------------


def host_meshes(worn: Worn, add_mesh: Callable[[dict], int], mesh_tree: Callable[[Any], dict]) -> dict:
    """Build slot mesh replacements that remove worn and packed items.

    Args:
        worn: Slot verdict grouping items by renderer and marking wholly removable renderers.
        add_mesh: Writes an edited mesh copy and returns its new path ID.
        mesh_tree: Supplies a mutable mesh typetree copy, preserving the original asset.

    Returns:
        Original graph mesh keys mapped to new IDs, or None to clear wholly removable mesh pointers.
    """
    out: dict = {}
    for node, items in worn.by_renderer().items():
        part = items[0].part
        if node in worn.whole:
            out[_key(part.mesh)] = None
            continue
        tree = mesh_tree(part.mesh)
        drop = np.concatenate([item.triangles for item in items]) if items else np.zeros(0, int)
        if not drop_triangles(tree, drop):
            logger.info("Worn items of %s kept: its mesh's triangles cannot be edited", part.mesh.peek_name())
            continue
        out[_key(part.mesh)] = add_mesh(tree)
    return out


@dataclass
class Carried:
    """Carry a source renderer's worn items as a new mesh in prefab-root space.

    Bind it to slot bones with the names the items follow,
    every bone entry at a bindpose that puts the mesh where the source's reference pose holds the item against that
    bone (so the rest pose draws it there whatever the weights).
    """

    part: ma.Part
    tree: dict
    nodes: list[int]  # the slot node per bone entry
    vertices: np.ndarray  # as written, the prefab root's space
    linear: np.ndarray
    bindposes: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    items: list[Item]


def carried(
    t_skeleton: ma.Skeleton,
    s_skeleton: ma.Skeleton,
    worn: Worn,
    clips: Sequence[Any],
    idle: list[np.ndarray] | None,
    mesh_tree: Callable[[Any], dict],
) -> list[Carried]:
    """Build one carried-item mesh per source renderer, skipping unsupported items with a log entry.

    Skip items whose following bone the slot lacks and meshes whose geometry or triangles cannot be rewritten.

    Args:
        t_skeleton: Destination slot hierarchy supplying named following bones and rest transforms.
        s_skeleton: Source costume hierarchy supplying the measured item reference poses.
        worn: Source worn/packed verdict; only items marked carry are transferred.
        clips: Source clip readers used to reconstruct non-idle item reference poses.
        idle: Source first idle-frame world matrices, or None when unavailable.
        mesh_tree: Supplies mutable source mesh typetree copies for geometry and triangle rewrites.
    """
    t_rest = t_skeleton.world()
    by_name = {(c.peek_name() or ""): c for c in clips}
    poses: dict[tuple[str, int] | None, list[np.ndarray] | None] = {None: idle}

    def pose_of(ref: tuple[str, int] | None) -> list[np.ndarray] | None:
        if ref not in poses:
            clip = by_name.get(ref[0])
            try:
                got = s_skeleton.clip_poses(clip, FRAMES)[0] if clip is not None else []
            except Exception:  # read the same way the measure did: a failure leaves the item out
                got = []
            poses[ref] = got[ref[1]] if ref[1] < len(got) else None
        return poses[ref]

    out = []
    for node, items in worn.by_renderer().items():
        items = [item for item in items if item.carry]  # packed items stay inside the costume they came with
        if not items:
            continue
        part = items[0].part
        name = part.mesh.peek_name()
        tree = mesh_tree(part.mesh)
        layout = mg.geometry_of(tree)
        if layout is None:
            logger.info("Worn items of %s not carried: its geometry cannot be rewritten", name)
            continue
        skin = ma.skin_of(part.mesh)
        bp = ma.bindposes(s_skeleton.graph, part.mesh)
        count = len(skin.vertices)
        vertices = np.zeros((count, 3))
        linear = np.repeat(np.eye(3)[None], count, axis=0)
        kept = np.zeros(count, bool)
        entry_node: dict[int, int] = {}
        placed = []
        for item in items:
            pose = pose_of(item.ref)
            s_bone = s_skeleton.first(item.follow) if item.follow else None
            t_bone = t_skeleton.first(item.follow) if item.follow else None
            if pose is None or s_bone is None or t_bone is None:
                logger.info(
                    "Worn item %s of %s not carried: no %s on the slot",
                    s_skeleton.nodes[item.top].name,
                    name,
                    item.follow,
                )
                continue
            rows = item.rows
            carry = np.array(
                [
                    pose[b] @ bp[j] if b is not None and j < len(bp) else pose[part.node]
                    for j, b in enumerate(part.bones)
                ]
                or [pose[part.node]]
            )
            sub = ma.Skin(
                skin.vertices[rows],
                np.minimum(skin.joints[rows], len(carry) - 1),
                skin.weights[rows],
                np.zeros((0, 2), int),
            )
            blended = ma.drawn(sub, carry, pose[part.node])
            move = t_rest[t_bone] @ np.linalg.inv(pose[s_bone])  # from where the pose holds it to the slot's rest
            vertices[rows] = np.einsum(
                "ij,nj->ni", move, np.einsum("nij,nj->ni", blended, np.c_[skin.vertices[rows], np.ones(len(rows))])
            )[:, :3]
            linear[rows] = move[:3, :3] @ blended[:, :3, :3]
            kept[rows] = True
            for j, b in enumerate(part.bones):
                if b in item.bones:
                    entry_node[j] = t_bone
            placed.append(item)
        if not placed:
            continue
        # the other things the renderer draws stay behind: their triangles draw nothing, their vertices sit at the
        # carried items' centre (no far point widens the bounds)
        vertices[~kept] = vertices[kept].mean(axis=0)
        drop = np.setdiff1d(
            np.arange(len(skin.triangles) if skin.triangles is not None else 0),
            np.concatenate([item.triangles for item in placed]),
        )
        if len(drop) and not drop_triangles(tree, drop):
            logger.info("Worn items of %s not carried: its triangles cannot be edited", name)
            continue
        # what the sanity pass over a build asks of every mesh it writes: a mesh failing it stays behind instead of
        # refusing the costume
        totals = skin.weights.sum(axis=1)
        if not ((np.abs(totals - 1.0) <= ma.WEIGHT_TOLERANCE) | (totals <= 1e-9)).all():
            logger.info("Worn items of %s not carried: weights do not add up to 1", name)
            continue
        if (skin.joints[skin.weights > 0] >= max(len(part.bones), 1)).any() or len(part.materials) > len(
            tree.get("m_SubMeshes") or []
        ):
            logger.info("Worn items of %s not carried: its skin or materials do not match its mesh", name)
            continue
        if float(np.abs(vertices).max(initial=0.0)) > ma.VERTEX_LIMIT:
            logger.info("Worn items of %s not carried: out of range", name)
            continue
        default = entry_node[min(entry_node)]
        nodes = [entry_node.get(j, default) for j in range(max(len(part.bones), 1))]
        bindposes = np.array([np.linalg.inv(t_rest[n]) for n in nodes])
        try:
            mg.write_geometry(tree, layout, vertices, linear)
        except OverflowError:  # past what the mesh's number format holds
            logger.info("Worn items of %s not carried: vertices out of range", name)
            continue
        out.append(Carried(part, tree, nodes, vertices, linear, bindposes, skin.joints, skin.weights, placed))
    return out
