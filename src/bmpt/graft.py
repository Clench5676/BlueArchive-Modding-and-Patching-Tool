"""Graft the animations a newer official portrait rig has into a mod's own rig, keeping the mod's rig.

The project's own design (GPLv3), built on the binary walk of ``bmpt.spine_binary`` (a Spine runtime derivative): every
reference is mapped by name; nothing of the mod's skeleton changes but what the graft adds.

- Skeleton (``SkeletonGraft``): every animation the official rig has and the mod lacks, with all its timelines; the
  attachments its timelines name that the mod's skins lack; slots the mod lacks inserted where the official rig has
  them (every slot index and draw-order frame of the mod follows); draw-order frames of carried animations recomputed
  for the mod's slot list; carried records adapted to whether the mod's file holds nonessential data. Not carried, with
  the reason: an animation that needs a bone, event, constraint or skin the mod lacks, a deform of a mesh the mod
  changed, a linked mesh.
- Atlas and pages (``pack``): the regions the carried attachments draw appended in the mod's atlas syntax, with rotate
  and offsets, their pixels copied as packed into page space no attachment of the mod's rig samples, else the page grows
  downward in whole 4-pixel rows and the atlas's page size follows; a page larger than its atlas declares is scaled.
- Placement (``placement``): each carried attachment re-expressed for the mod's bone of its slot through the setup bone
  matrices the runtime gives, and through an anchor affine fitted over the attachments the mod and the official rig it
  was made from both have in that slot, so new expressions follow a face the mod moved. A region attachment under a
  sheared or mirrored bone goes as a four-vertex mesh.
- Check (``off``): each grafted expression's part (what its slots draw) rendered against the official part taken
  through the anchor; ``BOUND`` of the visible pixels may differ. Flagged parts try ``CANDIDATES`` (atlas rotate,
  attachment turned, placement verbatim or by world) in one test rig the page poses once; the comparison decides.

The page poses every rig with the runtime (``web/js/rig-geometry.js``); this module only maps names, bytes and pixels.
"""

from __future__ import annotations

import itertools
import math
import re
import struct
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import atlas_remap, rebake
from .spine_binary import (MESH, REGION, Skeleton, attachment_record, draw_order, draw_order_bytes, mesh_record,
                           slot_record, string_bytes, strings_patch, varint_bytes)

#: share of the visible pixels of a part that may differ from the official part
BOUND = 0.02
#: page pixels kept clear around a carried region
PAD = 2
#: a grown page's height is a multiple of this
ROWS = 4
#: pixels per world unit of a rendered part, and the most pixels a part may take
PART_SCALE = 1.0
MAX_PART_PIXELS = 4_000_000
#: the largest page (declared and grown) a graft packs; official portrait pages are at most 4096 square
MAX_PAGE_PIXELS = 8192 * 8192
#: an anchor fit whose worst point is further off than this share of the slot's extent is not used
ANCHOR_SLACK = 0.01
#: candidate corrections of a flagged part: placement, atlas rotate, attachment turned
CANDIDATES = tuple(itertools.product(("world", "verbatim"), (0, 90, 180, 270), (0, 90, 180, 270)))
#: prefix of the names the test rig adds; never in a delivered rig
TEST = "bmpt~"

Matrix = np.ndarray


class GraftError(ValueError):
    def __init__(self, code: str, **params):
        super().__init__(f"{code}: {params}")
        self.code, self.params = code, params


def bad() -> GraftError:
    return GraftError("bad_params", key="geometry")


# ---------------------------------------------------------------- the skeleton

class Strings:
    """A skeleton's shared strings with the ones a graft appends."""

    def __init__(self, skeleton: Skeleton):
        self.all = list(skeleton.strings)
        self.base = len(self.all)
        self.index: dict[str | None, int] = {}
        for i, text in enumerate(self.all):
            self.index.setdefault(text, i + 1)

    def ref(self, text: str | None) -> int:
        if text is None:
            return 0
        if text not in self.index:
            self.all.append(text)
            self.index[text] = len(self.all)
        return self.index[text]

    @property
    def added(self) -> list[str]:
        return self.all[self.base:]


def _names(skeleton: Skeleton) -> dict[str, list]:
    return {"bone": [b["name"] for b in skeleton.bones], "event": list(skeleton.events),
            "skin": [s["name"] for s in skeleton.skins], **{k: list(v) for k, v in skeleton.constraints.items()}}


def insert_slot(mod_data: bytes, official: Skeleton, name: str) -> bytes:
    """The mod's skeleton with the official slot ``name`` added after the last slot both have that the official rig
    draws before it (first when there is none); every slot index and every draw-order frame of the mod follows."""
    mod = Skeleton(mod_data)
    count = len(mod.slots)
    source_index = official.slot_index(name)
    source = official.slots[source_index]
    before = None
    for slot in official.slots[:source_index]:
        found = mod.slot_index(slot["name"])
        if found is not None:
            before = found
    at = 0 if before is None else before + 1
    strings = Strings(mod)
    bones = {bone["name"]: i for i, bone in enumerate(mod.bones)}

    def into_mod(kind: str, value: int) -> int:
        if kind == "bone":
            return bones[official.bones[value]["name"]]
        if kind == "str":
            return strings.ref(official.s(value))
        raise ValueError(kind)

    record = slot_record(official, source, into_mod, mod.nonessential)
    patches = strings_patch(mod, strings.added)
    patches[(mod.slots_count_at, mod.slots[0]["start"] if count else mod.slots_end)] = varint_bytes(count + 1)
    where = mod.slots[at]["start"] if at < count else mod.slots_end
    patches[(where, where)] = record
    for animation in mod.animations:
        for start, end, offsets in animation["draworder"]:
            order = [slot + (slot >= at) for slot in draw_order(offsets, count)]
            # the new slot follows the slot it follows in the setup order
            order.insert(0 if at == 0 else order.index(at - 1) + 1, at)
            patches[(start, end)] = draw_order_bytes(order)
    return mod.emit(0, mod.size, lambda kind, v: v + (v >= at) if kind == "slot" else v, patches)


@dataclass
class Carried:
    """An attachment the graft carries: where it goes (skin, slot, key) and its official record."""
    skin: str
    slot: str
    key: str
    attachment: dict

    @property
    def region(self) -> str | None:
        return self.attachment["path"] if self.attachment["kind"] in (REGION, MESH) else None


class SkeletonGraft:
    """The plan of a skeleton graft and the writer of its result (``build``)."""

    def __init__(self, mod_data: bytes, official_data: bytes, only: set[str] | None = None):
        official = self.official = Skeleton(official_data)
        mod = Skeleton(mod_data)
        have = {a["name"] for a in mod.animations}
        wanted = [a for a in official.animations if a["name"] not in have and (only is None or a["name"] in only)]
        self.refused: dict[str, list[str]] = {}
        mod_names = {kind: set(values) for kind, values in _names(mod).items()}
        new_names = self.new_names = _names(official)
        accepted, slots_needed = [], []
        for animation in wanted:
            why, slots = [], []
            for _a, _b, kind, value in official.refs_in(animation["body"], animation["end"]):
                if kind == "slot":
                    if mod.slot_index(official.slots[value]["name"]) is None and value not in slots:
                        slots.append(value)
                elif kind == "physics1":
                    if value and new_names["physics"][value - 1] not in mod_names["physics"]:
                        why.append(("constraint", new_names["physics"][value - 1]))
                elif kind in ("bone", "event", "skin", "ik", "transform", "path", "physics") \
                        and new_names[kind][value] not in mod_names[kind]:
                    why.append(({"ik": "constraint", "transform": "constraint", "path": "constraint",
                                 "physics": "constraint"}.get(kind, kind), new_names[kind][value]))
            for value in slots:
                bone = official.bones[official.slots[value]["bone"]]["name"]
                if bone not in mod_names["bone"]:
                    why.append(("bone", bone))
            if why:
                self.refused[animation["name"]] = sorted({f"{kind}:{name}" for kind, name in why})
                continue
            accepted.append(animation)
            slots_needed += [value for value in slots if value not in slots_needed]
        self.inserted = []
        for value in sorted(slots_needed):
            mod_data = insert_slot(mod_data, official, official.slots[value]["name"])
            self.inserted.append(official.slots[value]["name"])
        self.mod = mod = Skeleton(mod_data)
        mod_atts, new_atts = mod.attachments(), official.attachments()
        carry: dict[tuple[str, str, str], dict] = {}
        for key, attachment in new_atts.items():
            if key[1] in self.inserted and key[0] in mod_names["skin"]:
                carry[key] = attachment
        self.animations: list[dict] = []
        self.shown: dict[str, list[tuple[str, str, str]]] = {}
        for animation in accepted:
            why, need = [], {}
            slot = None
            deforms = {(official.skins[skin]["name"], official.slots[s]["name"], official.s(att))
                       for skin, s, att in animation["deforms"]}
            shown = []
            for _a, _b, kind, value in official.refs_in(animation["body"], animation["end"]):
                if kind == "slot":
                    slot = official.slots[value]["name"]
                elif kind == "str" and value and slot is not None:
                    text = official.s(value)
                    for skin in [k[0] for k in deforms if k[1:] == (slot, text)] or ["default"]:
                        key = (skin, slot, text)
                        if key in new_atts and key not in shown:
                            shown.append(key)
                        if key not in mod_atts and key in new_atts:
                            need[key] = new_atts[key]
            for key in deforms:
                if key in mod_atts and key not in need and key not in carry:
                    a, b = mod_atts[key], new_atts.get(key)
                    if b is None or a["kind"] != b["kind"] or a.get("vertex_floats") != b.get("vertex_floats") \
                            or a.get("weighted") != b.get("weighted"):
                        why.append(f"deform:{key[1]}/{key[2]}")
            for key, attachment in need.items():
                if attachment["kind"] == 3:
                    why.append(f"linked:{key[2]}")
                for _x, _y, kind, value in official.refs_in(attachment["start"], attachment["end"]):
                    if kind == "bone" and new_names["bone"][value] not in mod_names["bone"]:
                        why.append(f"bone:{new_names['bone'][value]}")
            if why:
                self.refused[animation["name"]] = sorted(set(why))
                continue
            self.animations.append(animation)
            self.shown[animation["name"]] = [key for key in shown if key in need or key in carry]
            carry.update(need)
        self.carried = {key: Carried(*key, attachment) for key, attachment in carry.items()}
        self.strings = Strings(mod)
        index = {
            "slot": lambda v: mod.slot_index(official.slots[v]["name"]),
            "bone": lambda v: [b["name"] for b in mod.bones].index(new_names["bone"][v]),
            "skin": lambda v: [s["name"] for s in mod.skins].index(new_names["skin"][v]),
            "event": lambda v: mod.events.index(official.events[v]),
            "ik": lambda v: mod.constraints["ik"].index(new_names["ik"][v]),
            "transform": lambda v: mod.constraints["transform"].index(new_names["transform"][v]),
            "path": lambda v: mod.constraints["path"].index(new_names["path"][v]),
            "physics": lambda v: mod.constraints["physics"].index(new_names["physics"][v]),
            "physics1": lambda v: 0 if v == 0 else mod.constraints["physics"].index(new_names["physics"][v - 1]) + 1,
            "str": lambda v: self.strings.ref(official.s(v)),
        }
        self.remap = lambda kind, value: index[kind](value)
        self.same_slots = [s["name"] for s in mod.slots] == [s["name"] for s in official.slots]

    def _order_for_mod(self, offsets: list) -> list[int]:
        """An official draw-order frame as an order of the mod's slots: shared slots in the official order, each slot
        only the mod has right after the slot it follows in the mod's setup order."""
        mod, official = self.mod, self.official
        order = [mod.slot_index(official.slots[s]["name"]) for s in draw_order(offsets, len(official.slots))]
        order = [s for s in order if s is not None]
        for i in range(len(mod.slots)):
            if i not in order:
                order.insert(0 if i == 0 else order.index(i - 1) + 1, i)
        return order

    def record(self, carried: Carried, **overrides) -> bytes:
        """A carried attachment's record for the mod's file (``attachment_record`` overrides)."""
        return attachment_record(self.official, carried.attachment, self.remap, self.mod.nonessential, **overrides)

    def animation_bytes(self, animation: dict, rename: dict[str, str] | None = None, name: str | None = None) -> bytes:
        """A carried animation for the mod's file; ``rename`` maps attachment names its timelines name to others and
        ``name`` renames it (the test rig's candidates)."""
        patches = {}
        if not self.same_slots:
            for start, end, offsets in animation["draworder"]:
                patches[(start, end)] = draw_order_bytes(self._order_for_mod(offsets))
        official = self.official

        def remap(kind: str, value: int) -> int:
            if kind == "str" and rename and official.s(value) in rename:
                return self.strings.ref(rename[official.s(value)])
            return self.remap(kind, value)

        if name is not None:
            # the name is the animation's first field: a string of its own, not a shared one
            patches[(animation["start"], animation["body"])] = string_bytes(name)
        return official.emit(animation["start"], animation["end"], remap, patches)

    def build(self, records: dict[tuple[str, str, str], bytes | None] | None = None,
              extra_records: list[tuple[str, str, str, bytes]] = (), extra_animations: list[bytes] = ()) -> bytes:
        """The mod's skeleton with the carried animations and attachments. ``records``: a carried attachment's record
        in place of ``record``; ``extra_records`` (skin, slot, key, record) and ``extra_animations`` go in too."""
        mod = self.mod
        added = bytearray()
        for animation in self.animations:
            added += self.animation_bytes(animation)
        for data in extra_animations:
            added += data
        per_entry: dict[tuple[int, int], list[bytes]] = {}
        skins = [s["name"] for s in mod.skins]

        def put(skin: str, slot: str, key: str, body: bytes) -> None:
            entry = (skins.index(skin), mod.slot_index(slot))
            per_entry.setdefault(entry, []).append(varint_bytes(self.strings.ref(key)) + body)

        for key, carried in self.carried.items():
            body = (records or {}).get(key)
            put(*key, body if body is not None else self.record(carried))
        for skin, slot, key, body in extra_records:
            put(skin, slot, key, body)
        patches: dict[tuple[int, int], bytes] = {}
        for si, skin in enumerate(mod.skins):
            entries = {e["slot"]: e for e in skin["entries"]}
            fresh = []
            for (skin_index, slot_index), bodies in sorted(per_entry.items()):
                if skin_index != si:
                    continue
                if slot_index in entries:
                    entry = entries[slot_index]
                    patches[(entry["count_at"], entry["count_end"])] = varint_bytes(len(entry["atts"]) + len(bodies))
                    patches[(entry["end"], entry["end"])] = b"".join(bodies)
                else:
                    fresh.append(varint_bytes(slot_index) + varint_bytes(len(bodies)) + b"".join(bodies))
            if fresh:
                patches[(skin["count_at"], skin["count_end"])] = varint_bytes(len(skin["entries"]) + len(fresh))
                patches[(skin["end"], skin["end"])] = patches.get((skin["end"], skin["end"]), b"") + b"".join(fresh)
        count = len(mod.animations) + len(self.animations) + len(extra_animations)
        patches[(mod.anim_count_at, mod.anim_at)] = varint_bytes(count)
        patches.update(strings_patch(mod, self.strings.added))
        return mod.emit(0, mod.size, None, patches) + bytes(added)


# ---------------------------------------------------------------- posted setup geometry

MAX_BONES = 2000
MAX_SKIN = 4000


def _matrix(value) -> Matrix:
    if not isinstance(value, list) or len(value) != 6 or not all(
            type(v) in (int, float) and math.isfinite(v) and abs(v) < rebake.MAX_COORDINATE for v in value):
        raise bad()
    a, b, c, d, x, y = value
    return np.array([[a, b, x], [c, d, y], [0.0, 0.0, 1.0]])


@dataclass
class Setup:
    """A rig's setup pose as the runtime gives it: per bone its world matrix, per slot its bone, and per skin
    attachment (slot, name) its page, world vertices, page UVs and triangles."""
    bones: dict[str, Matrix] = field(default_factory=dict)
    slot_bones: dict[str, str] = field(default_factory=dict)
    skin: dict[tuple[str, str], tuple[str, np.ndarray, np.ndarray, np.ndarray]] = field(default_factory=dict)


def setup(value, atlas: atlas_remap.Atlas | None = None) -> Setup:
    """Posted setup geometry (``rig-geometry.js`` ``setupOf``), checked (untrusted: counts capped, numbers finite,
    names strings, UVs on the page, indices in range, pages of ``atlas`` when given)."""
    if not isinstance(value, dict):
        raise bad()
    bones, slot_bones, skin = value.get("bones"), value.get("slotBones"), value.get("skin", [])
    if not isinstance(bones, dict) or not isinstance(slot_bones, dict) or not isinstance(skin, list) \
            or len(bones) > MAX_BONES or len(slot_bones) > rebake.MAX_SLOTS or len(skin) > MAX_SKIN:
        raise bad()
    result = Setup()
    for name, matrix in bones.items():
        if not isinstance(name, str):
            raise bad()
        result.bones[name] = _matrix(matrix)
    for slot, bone in slot_bones.items():
        if not isinstance(slot, str) or bone not in result.bones:
            raise bad()
        result.slot_bones[slot] = bone
    pages = {atlas_remap.texture_key(page) for page in atlas.pages} if atlas is not None else None
    total = 0
    for item in skin:
        if not isinstance(item, dict):
            raise bad()
        slot, name, page = item.get("slot"), item.get("name"), item.get("page")
        if not (isinstance(slot, str) and isinstance(name, str) and isinstance(page, str)):
            raise bad()
        page = atlas_remap.texture_key(page)
        if pages is not None and page not in pages:
            raise bad()
        world = rebake._numbers(item.get("world"), rebake.MAX_VERTICES)
        uvs = rebake._numbers(item.get("uvs"), rebake.MAX_VERTICES)
        tris = rebake._numbers(item.get("tris"), rebake.MAX_TRIANGLES, integer=True)
        count = len(world) // 2
        if len(world) % 2 or len(uvs) != len(world) or count < 3 or len(tris) % 3:
            raise bad()
        total += len(world) + len(uvs) + len(tris)
        if total > rebake.MAX_NUMBERS:
            raise bad()
        w = np.asarray(world, dtype=np.float64).reshape(-1, 2)
        uv = np.asarray(uvs, dtype=np.float64).reshape(-1, 2)
        t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        if np.abs(w).max() > rebake.MAX_COORDINATE or uv.min() < -rebake.UV_SLACK or uv.max() > 1 + rebake.UV_SLACK \
                or (t.size and (t.min() < 0 or t.max() >= count)):
            raise bad()
        result.skin[(slot, name)] = (page, w, uv, t)
    return result


# ---------------------------------------------------------------- placement

def fit_anchor(mod: Setup, base: Setup, slot: str) -> tuple[Matrix, bool]:
    """(T, fitted): the affine that takes the base rig's picture of ``slot`` to the mod's, fitted over the skin
    attachments both have there (same name, same vertex count). The identity, not fitted, without such points, or when
    the worst point is off by more than ``ANCHOR_SLACK`` of the slot's extent (the mod changed the face otherwise
    than by moving, turning and scaling it)."""
    a, b = [], []
    for (s, name), (_page, world, _uv, _t) in mod.skin.items():
        other = base.skin.get((s, name))
        if s == slot and other is not None and len(other[1]) == len(world):
            a.append(other[1])
            b.append(world)
    if not a:
        return np.eye(3), False
    a, b = np.concatenate(a), np.concatenate(b)
    if len(a) < 3 or np.linalg.matrix_rank(np.c_[a - a.mean(0)]) < 2:
        return np.eye(3), False
    x = np.c_[a, np.ones(len(a))]
    solution, *_ = np.linalg.lstsq(x, b, rcond=None)
    extent = float(np.ptp(b, axis=0).max()) or 1.0
    if float(np.abs(x @ solution - b).max()) > max(1e-3, ANCHOR_SLACK * extent):
        return np.eye(3), False
    t = np.eye(3)
    t[:2, :] = solution.T
    if np.allclose(t, np.eye(3), atol=1e-6):
        return np.eye(3), True
    return t, True


def _turned(values: np.ndarray, degrees: float) -> np.ndarray:
    if not degrees:
        return values
    centre = (values.min(0) + values.max(0)) / 2
    t = math.radians(degrees)
    return (values - centre) @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]]) + centre


def region_corners(floats: tuple[float, ...], flags: int, region: atlas_remap.Region, turn: float = 0.0) -> np.ndarray:
    """A region attachment's four corners in its bone's space (bottom left, top left, top right, bottom right), as
    the runtime lays them out, trim included."""
    rotation, (x, y, sx, sy, w, h) = (floats[0], floats[1:]) if flags & 128 else (0.0, floats)
    rotation += turn
    ox, oy = region.offset
    ow, oh = region.original
    rw, rh = region.size
    kx, ky = w / ow * sx, h / oh * sy
    x0, y0 = -w / 2 * sx + ox * kx, -h / 2 * sy + oy * ky
    x1, y1 = x0 + rw * kx, y0 + rh * ky
    c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    corners = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
    return np.array([(px * c - py * s + x, py * c + px * s + y) for px, py in corners])


def placed_record(graft: SkeletonGraft, carried: Carried, m: Matrix, turn: int, region: atlas_remap.Region | None,
                  path: int | None = None) -> tuple[bytes | None, str]:
    """(record, note) of a carried attachment re-expressed by the affine ``m`` (official bone space to the mod's)
    and turned by ``turn`` degrees about its own centre. None (the official record) when nothing changes; a region
    under a sheared or mirrored ``m`` goes as a four-vertex mesh (note ``mesh``); a record that cannot take it stays
    as it is (note ``verbatim``)."""
    a = carried.attachment
    identity = np.allclose(m, np.eye(3), atol=1e-6)
    if identity and not turn:
        return (graft.record(carried, path=path) if path is not None else None), ""
    if a["kind"] == REGION:
        linear = m[:2, :2]
        scale = math.hypot(linear[0, 0], linear[1, 0])
        theta = math.degrees(math.atan2(linear[1, 0], linear[0, 0]))
        similar = scale * np.array([[math.cos(math.radians(theta)), -math.sin(math.radians(theta))],
                                    [math.sin(math.radians(theta)), math.cos(math.radians(theta))]])
        if np.allclose(linear, similar, atol=1e-4 * max(1.0, scale)):
            f = a["floats"]
            rotation, (x, y, sx, sy, w, h) = (f[0], f[1:]) if a["flags"] & 128 else (0.0, f)
            nx, ny, _ = m @ np.array([x, y, 1.0])
            floats = (rotation + theta + turn, nx, ny, sx * scale, sy * scale, w, h)
            return graft.record(carried, region=floats, path=path), ""
        if region is None:
            return None, "verbatim"
        corners = region_corners(a["floats"], a["flags"], region, turn) @ linear.T + m[:2, 2]
        ox, oy = region.offset
        ow, oh = region.original
        rw, rh = region.size
        u0, u1 = ox / ow, (ox + rw) / ow
        v0, v1 = (oh - oy - rh) / oh, (oh - oy) / oh
        f = a["floats"]
        size = (f[-2], f[-1])
        record = mesh_record(graft.official, a, graft.remap, graft.mod.nonessential, corners.reshape(-1).tolist(),
                             [u0, v1, u0, v0, u1, v0, u1, v1], [0, 1, 2, 2, 3, 0], 4, size, path=path)
        return record, "mesh"
    if a["kind"] == MESH and not a["weighted"]:
        official = graft.official
        n = a["vertex_floats"]
        at = a["vertices_at"] + len(varint_bytes(n // 2))
        v = np.array(struct.unpack_from(f">{n}f", official.data, at), dtype=np.float64).reshape(-1, 2)
        v = _turned(v, turn) @ m[:2, :2].T + m[:2, 2]
        return graft.record(carried, vertices=v.reshape(-1).tolist(), path=path), ""
    return (graft.record(carried, path=path) if path is not None else None), "verbatim"


# ---------------------------------------------------------------- atlas and pages

def _legacy(text: str) -> bool:
    return re.search(r"^\s*xy\s*:", text, re.M) is not None


def region_lines(name: str, xy: tuple[int, int], size: tuple[int, int], degrees: int,
                 offset: tuple[int, int], original: tuple[int, int], legacy: bool) -> list[str]:
    (x, y), (w, h) = xy, size
    if legacy:
        rotate = {0: "false", 90: "true"}.get(degrees, str(degrees))
        return [name, f"  rotate: {rotate}", f"  xy: {x}, {y}", f"  size: {w}, {h}",
                f"  orig: {original[0]}, {original[1]}", f"  offset: {offset[0]}, {offset[1]}", "  index: -1"]
    lines = [name, f"bounds:{x},{y},{w},{h}"]
    if (offset, original) != ((0, 0), (w, h)):
        lines.append(f"offsets:{offset[0]},{offset[1]},{original[0]},{original[1]}")
    if degrees:
        lines.append(f"rotate:{degrees}")
    return lines


def packed(region: atlas_remap.Region) -> tuple[int, int]:
    """The region's footprint on its page."""
    w, h = region.size
    return (h, w) if region.degrees in (90, 270) else (w, h)


def free_spot(occupied: np.ndarray, width: int, height: int) -> tuple[int, int] | None:
    """The first clear spot, row by row, for a box with ``PAD`` around it."""
    rows, columns = occupied.shape
    w, h = width + 2 * PAD, height + 2 * PAD
    if w > columns or h > rows:
        return None
    integral = np.zeros((rows + 1, columns + 1), dtype=np.int32)
    integral[1:, 1:] = occupied.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32)
    total = integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
    spots = np.argwhere(total == 0)
    if not len(spots):
        return None
    y, x = spots[0]
    return int(x) + PAD, int(y) + PAD


def sampled(skin: Setup, page: str, size: tuple[int, int]) -> np.ndarray:
    """Where the rig's skin attachments sample a page (texel centres in their triangles), widened by ``PAD``."""
    from PIL import ImageDraw, ImageFilter
    image = Image.new("L", size, 0)
    draw = ImageDraw.Draw(image)
    for (_slot, _name), (key, _world, uv, tris) in skin.skin.items():
        if key != page:
            continue
        points = uv * size
        for triangle in tris:
            draw.polygon([tuple(points[i]) for i in triangle], fill=255, outline=255)
    if PAD:
        image = image.filter(ImageFilter.MaxFilter(2 * PAD + 1))
    return np.asarray(image) > 0


@dataclass
class Placed:
    page: str  # the mod's page name
    xy: tuple[int, int]
    region: atlas_remap.Region  # the official region


@dataclass
class Pages:
    """The mod's atlas text and page images with the carried regions placed (``pack``)."""
    placed: dict[str, Placed]
    images: dict[str, Image.Image]  # texture key -> image, the pages that changed
    sizes: dict[str, tuple[int, int]]  # page name -> declared size after growing
    grown: dict[str, int]  # page name -> rows added
    missing: list[str]  # regions the official atlas lacks


def pack(mod_text: str, official_text: str, regions: list[str], mod_pages: dict[str, Image.Image],
         official_pages: dict[str, Image.Image], mod_setup: Setup) -> Pages:
    """Place the official regions ``regions`` on the mod's pages: into space the mod's rig does not sample, page by
    page, else the first page grows downward (whole ``ROWS``); pixels copied as packed."""
    mod_atlas = atlas_remap.parse_atlas(mod_text, "mod")
    official_atlas = atlas_remap.parse_atlas(official_text, "official")
    by_name = {}
    for region in official_atlas.regions.values():
        by_name.setdefault(region.name, region)
    have = {region.name for region in mod_atlas.regions.values()}
    wanted = [name for name in dict.fromkeys(regions) if name not in have]
    missing = [name for name in wanted if name not in by_name]
    wanted = [name for name in wanted if name in by_name]
    pages = list(mod_atlas.pages.values())
    occupancy, scales = {}, {}
    for page in pages:
        key = atlas_remap.texture_key(page.name)
        image = mod_pages.get(key)
        if image is None or not page.size[0] or not page.size[1]:
            continue
        if page.size[0] * page.size[1] > MAX_PAGE_PIXELS:
            raise GraftError("graft_region_too_large", region=page.name)
        scales[page.name] = (image.size[0] / page.size[0], image.size[1] / page.size[1])
        occupancy[page.name] = sampled(mod_setup, key, page.size)
    if not occupancy:
        raise GraftError("graft_page_missing", page=", ".join(p.name for p in pages) or "—")
    placed: dict[str, Placed] = {}
    grown: dict[str, int] = {}
    order = sorted(wanted, key=lambda n: -packed(by_name[n])[0] * packed(by_name[n])[1])
    for name in order:
        w, h = packed(by_name[name])
        spot = None
        for page_name, occupied in occupancy.items():
            spot = free_spot(occupied, w, h)
            if spot is not None:
                break
        if spot is None:
            page_name = next(iter(occupancy))
            occupied = occupancy[page_name]
            if (occupied.shape[0] + h + 2 * PAD + ROWS) * occupied.shape[1] > MAX_PAGE_PIXELS:
                raise GraftError("graft_region_too_large", region=name)
            taller = np.zeros((occupied.shape[0] + h + 2 * PAD + ROWS, occupied.shape[1]), dtype=bool)
            taller[:occupied.shape[0]] = occupied
            occupancy[page_name] = occupied = taller
            spot = free_spot(occupied, w, h)
            if spot is None:
                raise GraftError("graft_region_too_large", region=name)
        x, y = spot
        occupancy[page_name][y - PAD:y + h + PAD, x - PAD:x + w + PAD] = True
        placed[name] = Placed(page_name, (x, y), by_name[name])
    sizes = {page.name: page.size for page in pages}
    images: dict[str, Image.Image] = {}
    for page in pages:
        if page.name not in occupancy:
            continue
        used = max((p.xy[1] + packed(p.region)[1] + PAD for p in placed.values() if p.page == page.name), default=0)
        height = page.size[1] if used <= page.size[1] else -(-used // ROWS) * ROWS
        if height != page.size[1]:
            grown[page.name] = height - page.size[1]
            sizes[page.name] = (page.size[0], height)
    for page in pages:
        mine = [(name, p) for name, p in placed.items() if p.page == page.name]
        if not mine:
            continue
        key = atlas_remap.texture_key(page.name)
        sx, sy = scales[page.name]
        image = mod_pages[key].convert("RGBA")
        if page.name in grown:
            taller = Image.new("RGBA", (image.size[0], round(sizes[page.name][1] * sy)), (0, 0, 0, 0))
            taller.paste(image, (0, 0))
            image = taller
        else:
            image = image.copy()
        for name, p in mine:
            region = p.region
            source_page = official_atlas.pages[region.page]
            source = official_pages.get(atlas_remap.texture_key(region.page))
            if source is None:
                raise GraftError("graft_page_missing", page=region.page)
            ox, oy = (source.size[0] / source_page.size[0], source.size[1] / source_page.size[1])
            w, h = packed(region)
            x, y = region.xy
            tile = source.convert("RGBA").crop((round(x * ox), round(y * oy), round((x + w) * ox), round((y + h) * oy)))
            target = (max(1, round(w * sx)), max(1, round(h * sy)))
            if tile.size != target:
                tile = tile.resize(target, Image.Resampling.BICUBIC)
            image.paste(tile, (round(p.xy[0] * sx), round(p.xy[1] * sy)))
        images[key] = image
    return Pages(placed, images, sizes, grown, missing)


def atlas_text(mod_text: str, pages: Pages, extra: list[tuple[str, Placed, int]] = (),
               rotate: dict[str, int] | None = None) -> str:
    """The mod's atlas with the placed regions (and ``extra`` (name, placement, degrees) ones, the test rig's)
    appended to their pages, in its own syntax, and the page sizes that grew. ``rotate``: a region written with
    another rotate than the official one (a candidate correction): its size and offsets as packed."""
    legacy = _legacy(mod_text)
    lines = mod_text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    blocks: list[list[str]] = [[]]
    for line in lines:
        if not line.strip():
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    rows = {**{name: (p, (rotate or {}).get(name)) for name, p in pages.placed.items()},
            **{name: (p, degrees) for name, p, degrees in extra}}
    out = []
    for block in blocks:
        if not block:
            continue
        page = block[0].strip()
        if page in pages.sizes and pages.sizes[page] != tuple(block_size(block)):
            block = resize_block(block, pages.sizes[page], legacy)
        for name, (p, degrees) in rows.items():
            if p.page != page:
                continue
            region = p.region
            size, offset, original, written = region.size, region.offset, region.original, region.degrees
            if degrees is not None and degrees != region.degrees:
                turned = (degrees in (90, 270)) != (region.degrees in (90, 270))
                size = (size[1], size[0]) if turned else size
                offset, original, written = ((0, 0), size, degrees) if turned else (offset, original, degrees)
            block = block + region_lines(name, p.xy, size, written, offset, original, legacy)
        out.append("\n".join(block))
    return "\n\n".join(out) + "\n"


def block_size(block: list[str]) -> tuple[int, int]:
    for line in block[1:]:
        key, _, value = line.partition(":")
        if key.strip() == "size":
            return tuple(int(v) for v in value.split(","))
        if key.strip() == "bounds" or ":" not in line:
            break
    return (0, 0)


def resize_block(block: list[str], size: tuple[int, int], legacy: bool) -> list[str]:
    out = list(block)
    for i, line in enumerate(out[1:], 1):
        key, _, _value = line.partition(":")
        if key.strip() == "size":
            out[i] = f"size: {size[0]},{size[1]}" if legacy else f"size:{size[0]},{size[1]}"
            return out
        if ":" not in line:
            break
    return out


# ---------------------------------------------------------------- the check

def _premultiplied(image: Image.Image) -> np.ndarray:
    return rebake._premultiplied(image)


def render(items: list[rebake.Drawn], pages: dict[str, np.ndarray], frame: tuple[float, float, int, int],
           scale: float) -> np.ndarray:
    """A part as the pixels its posed triangles map (premultiplied RGBA, normal and additive blending)."""
    left, top, width, height = frame
    out = np.zeros((height, width, 4), dtype=np.float32)
    for item in items:
        page = pages.get(item.page)
        if page is None:
            continue
        sx, sy = (item.vertices[:, 0] - left) * scale, (top - item.vertices[:, 1]) * scale
        for triangle in item.triangles:
            px, py = sx[triangle], sy[triangle]
            a0, a1 = max(0, int(np.floor(px.min()))), min(width, int(np.ceil(px.max())))
            b0, b1 = max(0, int(np.floor(py.min()))), min(height, int(np.ceil(py.max())))
            if a1 <= a0 or b1 <= b0:
                continue
            gx, gy = np.meshgrid(np.arange(a0, a1), np.arange(b0, b1))
            weights = rebake._bary(gx + 0.5, gy + 0.5, (px[0], py[0]), (px[1], py[1]), (px[2], py[2]))
            if weights is None:
                continue
            inside = (weights[0] >= 0) & (weights[1] >= 0) & (weights[2] >= 0)
            if not inside.any():
                continue
            l0, l1, l2 = (w[inside] for w in weights)
            q = item.uvs[triangle]
            colour = rebake._sample(page, l0 * q[0, 0] + l1 * q[1, 0] + l2 * q[2, 0],
                                    l0 * q[0, 1] + l1 * q[1, 1] + l2 * q[2, 1]) * item.alpha
            yy, xx = gy[inside], gx[inside]
            if item.blend == 1:
                out[yy, xx, :3] += colour[:, :3]
            else:
                out[yy, xx] = colour + (1 - colour[:, 3:4]) * out[yy, xx]
    return np.clip(out, 0, 1)


def _shown(image: np.ndarray) -> np.ndarray:
    return image[..., :3] + (1 - image[..., 3:4]) * 0.5  # on mid grey, as a viewer sees it


def off(grafted: list[rebake.Drawn], grafted_pages: dict[str, np.ndarray], official: list[rebake.Drawn],
        official_pages: dict[str, np.ndarray]) -> float:
    """The share of the visible pixels where two parts differ by more than 40/255 on mid grey; 1 when only one side
    draws anything."""
    if not grafted or not official:
        return 0.0 if not grafted and not official else 1.0
    every = np.concatenate([item.vertices for item in [*grafted, *official]])
    x0, y0 = every.min(axis=0) - 20
    x1, y1 = every.max(axis=0) + 20
    scale = PART_SCALE
    while (x1 - x0) * (y1 - y0) * scale * scale > MAX_PART_PIXELS:
        scale /= 2
    frame = (float(x0), float(y1), int(np.ceil((x1 - x0) * scale)), int(np.ceil((y1 - y0) * scale)))
    a, b = render(grafted, grafted_pages, frame, scale), render(official, official_pages, frame, scale)
    visible = np.maximum(a[..., 3], b[..., 3]) > 0.06
    if not visible.any():
        return 0.0
    return float(((np.abs(_shown(a) - _shown(b)).max(axis=2) > 40 / 255) & visible).sum() / visible.sum())


def moved(items: tuple[rebake.Drawn, ...], anchors: dict[str, Matrix]) -> list[rebake.Drawn]:
    """Official drawn items taken through their slot's anchor."""
    out = []
    for item in items:
        t = anchors.get(item.slot)
        if t is None or np.allclose(t, np.eye(3)):
            out.append(item)
            continue
        v = item.vertices @ t[:2, :2].T + t[:2, 2]
        out.append(rebake.Drawn(item.slot, item.region, item.page, item.blend, item.alpha, v, item.uvs,
                                item.triangles))
    return out


# ---------------------------------------------------------------- one atlas, end to end

@dataclass
class Prepared:
    """A graft of one atlas's rig, ready to be posed (``test``) and checked (``choose``)."""
    graft: SkeletonGraft
    mod_skeleton: bytes
    official_skeleton: bytes
    mod_text: str
    official_text: str
    pages: Pages
    mod_setup: Setup
    official_setup: Setup
    anchors: dict[str, Matrix]
    unanchored: list[str]  # slots placed without a fitted anchor: no base rig, or the fit did not hold
    notes: dict[str, str]
    skeleton: bytes  # the primary graft
    atlas: str
    test_skeleton: bytes
    test_atlas: str
    candidates: dict[str, tuple[str, tuple]]  # test animation -> (animation, candidate)
    slots: dict[str, list[str]]  # animation -> the slots its part is drawn in


def _primary(region: atlas_remap.Region | None) -> tuple:
    return ("world", region.degrees if region else 0, 0)


def prepare(mod_skeleton: bytes, mod_atlas: str, mod_pages: dict[str, Image.Image], official_skeleton: bytes,
            official_atlas: str, official_pages: dict[str, Image.Image], mod_setup: Setup, official_setup: Setup,
            base_setup: Setup | None = None) -> Prepared:
    """Plan, splice and pack one rig, and build the test rig: per carried animation one copy per candidate."""
    graft = SkeletonGraft(mod_skeleton, official_skeleton)
    if not graft.animations:
        raise GraftError("graft_refused", names=", ".join(sorted(graft.refused)) or "—")
    regions = [c.region for c in graft.carried.values() if c.region]
    pages = pack(mod_atlas, official_atlas, regions, mod_pages, official_pages, mod_setup)
    anchors, unanchored = {}, []
    for slot in sorted({c.slot for c in graft.carried.values()}):
        if base_setup is None:  # placed as if the mod had not moved the face: the review rings it for a look
            anchors[slot] = np.eye(3)
            unanchored.append(slot)
            continue
        anchors[slot], fitted = fit_anchor(mod_setup, base_setup, slot)
        if not fitted:
            unanchored.append(slot)
    official_regions = {}
    for region in atlas_remap.parse_atlas(official_atlas, "official").regions.values():
        official_regions.setdefault(region.name, region)
    notes: dict[str, str] = {}

    def matrix(carried: Carried, place: str) -> Matrix:
        if place == "verbatim":
            return np.eye(3)
        bone_mod = mod_setup.slot_bones.get(carried.slot)
        bone_new = official_setup.slot_bones.get(carried.slot)
        if bone_mod is None or bone_new is None:
            raise bad()
        return np.linalg.inv(mod_setup.bones[bone_mod]) @ anchors[carried.slot] @ official_setup.bones[bone_new]

    def record(carried: Carried, candidate: tuple, path: int | None = None) -> bytes | None:
        place, _degrees, turn = candidate
        body, note = placed_record(graft, carried, matrix(carried, place), turn,
                                   official_regions.get(carried.region) if carried.region else None, path)
        if note and candidate == _primary(official_regions.get(carried.region)):
            notes[f"{carried.slot}/{carried.key}"] = note
        return body

    primary = {key: record(c, _primary(official_regions.get(c.region))) for key, c in graft.carried.items()}
    skeleton = graft.build(primary)
    atlas = atlas_text(mod_atlas, pages)
    # the test rig: every candidate of every carried animation as an animation of its own showing its own copies
    extra_records, extra_animations, extra_regions, candidates = [], [], {}, {}
    slots = {an["name"]: sorted({key[1] for key in graft.shown[an["name"]]}) for an in graft.animations}
    for i, candidate in enumerate(CANDIDATES):
        place, degrees, turn = candidate
        for an in graft.animations:
            shown = [graft.carried[key] for key in graft.shown[an["name"]] if key in graft.carried]
            if not shown:
                continue
            rename = {}
            for carried in shown:
                copy = f"{TEST}{i}~{carried.key}"
                path = None
                if carried.region:
                    region_name = f"{TEST}r{degrees}~{carried.region}"
                    if carried.region in pages.placed:
                        extra_regions[region_name] = (pages.placed[carried.region], degrees)
                    path = graft.strings.ref(region_name if carried.region in pages.placed else carried.region)
                body = record(carried, candidate, path)
                if body is None:
                    body = graft.record(carried, path=path) if path is not None else graft.record(carried)
                extra_records.append((carried.skin, carried.slot, copy, body))
                rename[carried.key] = copy
            name = f"{TEST}{i}~{an['name']}"
            extra_animations.append(graft.animation_bytes(an, rename, name))
            candidates[name] = (an["name"], candidate)
    test_skeleton = graft.build(primary, extra_records, extra_animations)
    test_atlas = atlas_text(mod_atlas, pages, [(name, p, d) for name, (p, d) in extra_regions.items()])
    return Prepared(graft, mod_skeleton, official_skeleton, mod_atlas, official_atlas, pages, mod_setup, official_setup, anchors, unanchored, notes,
                    skeleton, atlas, test_skeleton, test_atlas, candidates, slots)


@dataclass
class Checked:
    """The delivered rig of one atlas and how each grafted expression came out."""
    skeleton: bytes
    atlas: str
    pages: dict[str, Image.Image]
    results: dict[str, dict]  # animation -> {"off", "candidate", "fixed"}


def choose(prepared: Prepared, official: rebake.Rig, test: rebake.Rig, official_pages: dict[str, Image.Image],
           grafted_pages: dict[str, Image.Image]) -> Checked:
    """Check every grafted expression's part against the official part (through the anchors); for one over
    ``BOUND`` keep the candidate with the smallest difference under it. ``official``/``test``: the posed official
    and test rigs (``rebake.rig``)."""
    graft = prepared.graft
    gp = {key: _premultiplied(image) for key, image in grafted_pages.items()}
    op = {key: _premultiplied(image) for key, image in official_pages.items()}
    results: dict[str, dict] = {}
    choice: dict[str, tuple] = {}
    by_animation: dict[str, list[tuple[str, tuple]]] = {}
    for name, (animation, candidate) in prepared.candidates.items():
        by_animation.setdefault(animation, []).append((name, candidate))
    for an in graft.animations:
        name = an["name"]
        slots = set(prepared.slots[name])
        if name not in official.poses or name not in test.poses:
            raise bad()
        reference = moved(tuple(i for i in official.poses[name] if i.slot in slots), prepared.anchors)
        part = [i for i in test.poses[name] if i.slot in slots]
        primary = off(part, gp, reference, op)
        results[name] = {"off": round(primary, 4), "candidate": None, "fixed": primary <= BOUND}
        if primary <= BOUND or not by_animation.get(name):
            continue
        best = (primary, None)
        for test_name, candidate in by_animation[name]:
            items = test.poses.get(test_name)
            if items is None:
                continue
            value = off([i for i in items if i.slot in slots], gp, reference, op)
            if value < best[0]:
                best = (value, candidate)
            if value == 0:
                break
        if best[1] is not None and best[0] <= BOUND:
            results[name] = {"off": round(best[0], 4), "candidate": list(best[1]), "fixed": True}
            choice[name] = best[1]
        else:
            results[name] = {"off": round(min(primary, best[0]), 4), "candidate": None, "fixed": False}
    if not choice:
        return Checked(prepared.skeleton, prepared.atlas, grafted_pages, results)
    # the chosen candidates in the delivered rig: per carried attachment the first choice of an animation showing it,
    # written by a graft of its own (the test rig's names stay out of its strings)
    graft = SkeletonGraft(prepared.mod_skeleton, prepared.official_skeleton)
    official_regions = {}
    for region in atlas_remap.parse_atlas(prepared.official_text, "official").regions.values():
        official_regions.setdefault(region.name, region)
    mod_setup, official_setup = prepared.mod_setup, prepared.official_setup
    records, rotate = {}, {}
    for an in graft.animations:
        candidate = choice.get(an["name"])
        if candidate is None:
            continue
        place, degrees, turn = candidate
        for key in graft.shown[an["name"]]:
            carried = graft.carried.get(key)
            if carried is None or key in records:
                continue
            if place == "verbatim":
                m = np.eye(3)
            else:
                m = (np.linalg.inv(mod_setup.bones[mod_setup.slot_bones[carried.slot]])
                     @ prepared.anchors[carried.slot] @ official_setup.bones[official_setup.slot_bones[carried.slot]])
            region = official_regions.get(carried.region) if carried.region else None
            records[key], _note = placed_record(graft, carried, m, turn, region)
            if carried.region and carried.region in prepared.pages.placed:
                rotate.setdefault(carried.region, degrees)
    for key, carried in graft.carried.items():
        if key not in records:
            region = official_regions.get(carried.region) if carried.region else None
            m = (np.linalg.inv(mod_setup.bones[mod_setup.slot_bones[carried.slot]])
                 @ prepared.anchors[carried.slot] @ official_setup.bones[official_setup.slot_bones[carried.slot]])
            records[key], _note = placed_record(graft, carried, m, 0, region)
    return Checked(graft.build(records), atlas_text(prepared.mod_text, prepared.pages, rotate=rotate),
                   grafted_pages, results)
