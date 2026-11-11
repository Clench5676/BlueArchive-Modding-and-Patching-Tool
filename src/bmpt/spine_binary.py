# Spine Runtimes License Agreement
# Last updated April 5, 2025. Replaces all prior versions.
#
# Copyright (c) 2013-2025, Esoteric Software LLC
#
# Integration of the Spine Runtimes into software or otherwise creating
# derivative works of the Spine Runtimes is permitted under the terms and
# conditions of Section 2 of the Spine Editor License Agreement:
# http://esotericsoftware.com/spine-editor-license
#
# Otherwise, it is permitted to integrate the Spine Runtimes into software
# or otherwise create derivative works of the Spine Runtimes (collectively,
# "Products"), provided that each user of the Products must obtain their own
# Spine Editor license and redistribution of the Products in any form must
# include this license and copyright notice.
#
# THE SPINE RUNTIMES ARE PROVIDED BY ESOTERIC SOFTWARE LLC "AS IS" AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL ESOTERIC SOFTWARE LLC BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES,
# BUSINESS INTERRUPTION, OR LOSS OF USE, DATA, OR PROFITS) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
# THE SPINE RUNTIMES, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""A Spine 4.2 binary skeleton walked in the layout the Spine runtime reads (spine-runtimes, branch 4.2,
``SkeletonBinary`` of spine-ts and spine-csharp), keeping where every part is.

This module is a direct derivative of the Spine Runtimes and is licensed under the Spine Runtimes License Agreement
above, not under the GPLv3 (the README's GPLv3 section 7 additional permission covers the combination). It runs only
after the user has confirmed holding a Spine licence. The App's own design on top of it (what to carry between two
skeletons, by name) is in ``bmpt.graft``, under the GPLv3.

What it gives: the sections (shared strings, bones, slots, constraints, skins with their attachments, events,
animations) with their byte positions, every varint that refers to a shared string, bone, slot, skin, event or
constraint, the spans that exist only with nonessential data, and the draw-order frames. ``emit`` writes any byte range
back with its references mapped and given spans replaced; the record helpers write attachment and slot records for a
file with or without nonessential data. Skeleton data comes from mods: every read is bounded by the data, and anything
that does not fit the layout raises ``ValueError``.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping

_FLOAT = 4
_CURVE = 4 * _FLOAT  # one Bezier segment: cx1, cy1, cx2, cy2

#: attachment types, the low three bits of an attachment's flags
REGION, BOUNDING_BOX, MESH, LINKED_MESH, PATH, POINT, CLIPPING = range(7)

Remap = Callable[[str, int], int]


def varint_bytes(value: int) -> bytes:
    """A varint as ``readInt(true)`` reads it; a negative value goes out as five bytes."""
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def signed_varint_bytes(value: int) -> bytes:
    return varint_bytes(value & 0xFFFFFFFF)


def string_bytes(text: str | None) -> bytes:
    if text is None:
        return b"\x00"
    raw = text.encode("utf-8")
    return varint_bytes(len(raw) + 1) + raw


def floats_bytes(*values: float) -> bytes:
    return struct.pack(f">{len(values)}f", *values)


class Skeleton:
    """The walk over one binary skeleton. Positions are byte offsets into ``data``; ``refs`` lists (start, end, kind,
    value) of every reference varint, kinds ``str`` (shared string, 0 for none), ``bone``, ``slot``, ``skin``,
    ``event``, ``ik``, ``transform``, ``path``, ``physics`` and ``physics1`` (a physics constraint index plus one, 0
    for all)."""

    def __init__(self, data: bytes):
        self.data = bytes(data)
        self.pos = 0
        self.size = len(self.data)
        self.refs: list[tuple[int, int, str, int]] = []
        self.ness: list[tuple[int, int]] = []
        self._walk()

    # --- primitives

    def _skip(self, count: int) -> None:
        if count < 0 or self.pos + count > self.size:
            raise ValueError("skeleton data ends early")
        self.pos += count

    def _byte(self) -> int:
        if self.pos >= self.size:
            raise ValueError("skeleton data ends early")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def _varint(self) -> int:
        value = shift = 0
        while True:
            byte = self._byte()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80 or shift == 28:
                return value
            shift += 7

    def _signed(self) -> int:
        value = self._varint() & 0xFFFFFFFF
        return value - (1 << 32) if value & (1 << 31) else value

    def _ref(self, kind: str) -> int:
        start = self.pos
        value = self._varint()
        self.refs.append((start, self.pos, kind, value))
        return value

    def _varints(self, count: int) -> None:
        for _ in range(count):
            self._varint()

    def _string(self) -> str | None:
        length = self._varint()
        if length == 0:
            return None
        if length == 1:
            return ""
        start = self.pos
        self._skip(length - 1)
        return self.data[start:self.pos].decode("utf-8", errors="replace")

    def _floats(self, count: int) -> tuple[float, ...]:
        start = self.pos
        self._skip(count * _FLOAT)
        return struct.unpack_from(f">{count}f", self.data, start)

    def _flagged(self, flags: int, bits: tuple[int, ...]) -> None:
        self._skip(_FLOAT * sum(1 for bit in bits if flags & bit))

    def _curves(self, frames: int, frame: int, curves: int) -> None:
        if frames < 1:
            raise ValueError("skeleton timeline without frames")
        self._skip(frame)
        for _ in range(frames - 1):
            self._skip(frame)
            if self._byte() == 2:
                self._skip(curves * _CURVE)

    def _vertices(self, weighted: bool) -> int:
        count = self._varint()
        if not weighted:
            self._skip(count * 2 * _FLOAT)
            return count
        for _ in range(count):
            for _ in range(self._varint()):
                self._ref("bone")
                self._skip(3 * _FLOAT)
        return count

    def s(self, index: int) -> str | None:
        """The shared string a ``str`` reference names (None for 0)."""
        if index == 0:
            return None
        if not 0 < index <= len(self.strings):
            raise ValueError("skeleton string reference out of range")
        return self.strings[index - 1]

    # --- sections

    def _attachment(self, key: int) -> dict:
        a: dict = {"key": key, "start": self.pos}
        flags = a["flags"] = self._byte()
        a["name"] = self.s(self._ref("str")) if flags & 8 else self.s(key)
        kind = a["kind"] = flags & 7
        a["path"] = a["name"]
        if kind in (REGION, MESH, LINKED_MESH):
            if flags & 16:
                a["path"] = self.s(self._ref("str"))
            if flags & 32:
                self._skip(4)
            if flags & 64:
                self._varints(4)
        a["refs_end"] = self.pos  # after the name, path, colour and sequence
        tail = None
        if kind == REGION:
            a["floats_at"] = self.pos
            a["floats"] = self._floats(7 if flags & 128 else 6)
        elif kind == BOUNDING_BOX:
            self._vertices(bool(flags & 16))
            tail = b"\x00" * 4
            if self.nonessential:
                at = self.pos
                self._skip(4)
                self.ness.append((at, self.pos))
        elif kind == MESH:
            a["hull"] = self._varint()
            a["weighted"] = bool(flags & 128)
            a["vertices_at"] = self.pos
            count = self._vertices(a["weighted"]) * 2
            a["vertices_end"] = self.pos
            a["vertex_floats"] = count
            a["uvs_at"] = self.pos
            self._skip(count * _FLOAT)
            triangles = count - a["hull"] - 2
            if triangles < 0:
                raise ValueError("skeleton mesh hull longer than its vertices")
            self._varints(triangles * 3)
            tail = b"\x00" + b"\x00" * 8  # no edges, width and height 0
            if self.nonessential:
                at = self.pos
                self._varints(self._varint())
                self._skip(2 * _FLOAT)
                self.ness.append((at, self.pos))
        elif kind == LINKED_MESH:
            self._ref("skin")
            self._ref("str")
            tail = b"\x00" * 8
            if self.nonessential:
                at = self.pos
                self._skip(2 * _FLOAT)
                self.ness.append((at, self.pos))
        elif kind == PATH:
            count = self._vertices(bool(flags & 64)) * 2
            self._skip(count // 6 * _FLOAT)
            tail = b"\x00" * 4
            if self.nonessential:
                at = self.pos
                self._skip(4)
                self.ness.append((at, self.pos))
        elif kind == POINT:
            self._skip(3 * _FLOAT)
            tail = b"\x00" * 4
            if self.nonessential:
                at = self.pos
                self._skip(4)
                self.ness.append((at, self.pos))
        elif kind == CLIPPING:
            self._ref("slot")
            self._vertices(bool(flags & 16))
            tail = b"\x00" * 4
            if self.nonessential:
                at = self.pos
                self._skip(4)
                self.ness.append((at, self.pos))
        else:
            raise ValueError("skeleton attachment type unknown")
        if tail is not None:
            a["tail"] = tail
            a["tail_at"] = self.ness[-1][0] if self.nonessential else self.pos
        a["end"] = self.pos
        return a

    def _skin(self, default: bool) -> dict:
        skin: dict = {"start": self.pos, "entries": []}
        if default:
            skin["name"] = "default"
            skin["count_at"] = self.pos
            slots = self._varint()
            skin["count_end"] = self.pos
        else:
            skin["name"] = self._string()
            if self.nonessential:
                self.ness.append((self.pos, self.pos + 4))
                self._skip(4)
            for kind in ("bone", "ik", "transform", "path", "physics"):
                for _ in range(self._varint()):
                    self._ref(kind)
            skin["count_at"] = self.pos
            slots = self._varint()
            skin["count_end"] = self.pos
        for _ in range(slots):
            entry: dict = {"start": self.pos, "slot": self._ref("slot"), "atts": []}
            entry["count_at"] = self.pos
            count = self._varint()
            entry["count_end"] = self.pos
            for _ in range(count):
                at = self.pos
                attachment = self._attachment(self._ref("str"))
                attachment["entry_start"] = at
                entry["atts"].append(attachment)
            entry["end"] = self.pos
            skin["entries"].append(entry)
        skin["end"] = self.pos
        return skin

    def _ik_frame(self, flags: int) -> None:
        self._skip(_FLOAT * (1 + (1 if flags & 1 and flags & 2 else 0) + (1 if flags & 4 else 0)))

    def _animation(self) -> dict:
        an: dict = {"start": self.pos, "name": self._string() or "", "deforms": [], "draworder": [], "timelines": set()}
        an["body"] = self.pos
        kinds = an["timelines"]
        self._varint()  # timeline count
        for _ in range(self._varint()):
            self._ref("slot")
            for _ in range(self._varint()):
                kind, frames = self._byte(), self._varint()
                if kind == 0:
                    kinds.add("attachment")
                    for _ in range(frames):
                        self._skip(_FLOAT)
                        self._ref("str")
                elif kind in (1, 2, 3, 4, 5):
                    kinds.add("color")
                    values = {1: 4, 2: 3, 3: 7, 4: 6, 5: 1}[kind]
                    self._varint()
                    self._curves(frames, _FLOAT + values, values)
                else:
                    raise ValueError("skeleton slot timeline unknown")
        for _ in range(self._varint()):
            self._ref("bone")
            for _ in range(self._varint()):
                kind, frames = self._byte(), self._varint()
                kinds.add("bone")
                if kind == 10:  # inherit
                    self._skip(frames * (_FLOAT + 1))
                    continue
                if kind > 10:
                    raise ValueError("skeleton bone timeline unknown")
                self._varint()
                values = 2 if kind in (1, 4, 7) else 1
                self._curves(frames, _FLOAT * (1 + values), values)
        for _ in range(self._varint()):
            kinds.add("ik")
            self._ref("ik")
            frames = self._varint()
            if frames < 1:
                raise ValueError("skeleton timeline without frames")
            self._varint()
            self._ik_frame(self._byte())
            for _ in range(frames - 1):
                flags = self._byte()
                self._ik_frame(flags)
                if not flags & 64 and flags & 128:
                    self._skip(2 * _CURVE)
        for _ in range(self._varint()):
            kinds.add("transform")
            self._ref("transform")
            frames = self._varint()
            self._varint()
            self._curves(frames, 7 * _FLOAT, 6)
        for _ in range(self._varint()):
            kinds.add("path")
            self._ref("path")
            for _ in range(self._varint()):
                kind, frames = self._byte(), self._varint()
                self._varint()
                if kind in (0, 1):
                    self._curves(frames, 2 * _FLOAT, 1)
                elif kind == 2:
                    self._curves(frames, 4 * _FLOAT, 3)
                else:
                    raise ValueError("skeleton path timeline unknown")
        for _ in range(self._varint()):
            kinds.add("physics")
            self._ref("physics1")
            for _ in range(self._varint()):
                kind, frames = self._byte(), self._varint()
                if kind == 8:  # reset
                    self._skip(frames * _FLOAT)
                    continue
                if kind > 8 or kind == 3:
                    raise ValueError("skeleton physics timeline unknown")
                self._varint()
                self._curves(frames, 2 * _FLOAT, 1)
        for _ in range(self._varint()):
            skin = self._ref("skin")
            for _ in range(self._varint()):
                slot = self._ref("slot")
                for _ in range(self._varint()):
                    attachment = self._ref("str")
                    kind, frames = self._byte(), self._varint()
                    if kind == 0:
                        kinds.add("deform")
                        an["deforms"].append((skin, slot, attachment))
                        self._varint()
                        self._skip(_FLOAT)
                        for frame in range(frames):
                            count = self._varint()
                            if count:
                                self._varint()
                                self._skip(count * _FLOAT)
                            if frame == frames - 1:
                                break
                            self._skip(_FLOAT)
                            if self._byte() == 2:
                                self._skip(_CURVE)
                    elif kind == 1:
                        kinds.add("sequence")
                        self._skip(frames * (2 * _FLOAT + 4))
                    else:
                        raise ValueError("skeleton attachment timeline unknown")
        for _ in range(self._varint()):
            kinds.add("drawOrder")
            self._skip(_FLOAT)
            start, offsets = self.pos, []
            for _ in range(self._varint()):
                slot = self._varint()
                offsets.append((slot, self._signed()))
            an["draworder"].append((start, self.pos, offsets))
        for _ in range(self._varint()):
            kinds.add("event")
            self._skip(_FLOAT)
            event = self._ref("event")
            self._varint()
            self._skip(_FLOAT)
            self._string()
            if not 0 <= event < len(self.audio):
                raise ValueError("skeleton event reference out of range")
            if self.audio[event]:
                self._skip(2 * _FLOAT)
        an["end"] = self.pos
        return an

    def _walk(self) -> None:
        self._skip(8)  # hash
        self.version = self._string() or ""
        if not self.version.startswith("4.2."):
            raise ValueError(f"skeleton version {self.version!r} is not 4.2")
        self._skip(5 * _FLOAT)
        self.ness_flag_at = self.pos
        self.nonessential = self._byte() != 0
        if self.nonessential:
            at = self.pos
            self._skip(_FLOAT)
            self._string()
            self._string()
            self.ness.append((at, self.pos))
        self.strings_count_at = self.pos
        count = self._varint()
        self.strings_at = self.pos
        self.strings: list[str | None] = []
        for _ in range(count):
            self.strings.append(self._string())
        self.strings_end = self.pos
        self.bones: list[dict] = []
        self.bones_count_at = self.pos
        for index in range(self._varint()):
            bone: dict = {"start": self.pos, "name": self._string() or "", "parent": None}
            if index:
                bone["parent"] = self._ref("bone")
            bone["floats_at"] = self.pos
            bone["floats"] = self._floats(8)  # rotation, x, y, scaleX, scaleY, shearX, shearY, length
            self._skip(2)  # inherit, skin required
            if self.nonessential:
                at = self.pos
                self._skip(4)
                self._string()
                self._skip(1)
                self.ness.append((at, self.pos))
            bone["end"] = self.pos
            self.bones.append(bone)
        self.slots: list[dict] = []
        self.slots_count_at = self.pos
        for _ in range(self._varint()):
            slot: dict = {"start": self.pos, "name": self._string() or "", "bone": self._ref("bone")}
            self._skip(8)  # colour, dark colour
            slot["attachment"] = self._ref("str")
            slot["blend"] = self._varint()
            if self.nonessential:
                self.ness.append((self.pos, self.pos + 1))
                self._skip(1)
            slot["end"] = self.pos
            self.slots.append(slot)
        self.slots_end = self.pos
        self.constraints: dict[str, list[str | None]] = {"ik": [], "transform": [], "path": [], "physics": []}
        for _ in range(self._varint()):
            self.constraints["ik"].append(self._string())
            self._varint()
            for _ in range(self._varint() + 1):
                self._ref("bone")
            flags = self._byte()
            if flags & 32 and flags & 64:
                self._skip(_FLOAT)
            if flags & 128:
                self._skip(_FLOAT)
        for _ in range(self._varint()):
            self.constraints["transform"].append(self._string())
            self._varint()
            for _ in range(self._varint() + 1):
                self._ref("bone")
            self._flagged(self._byte(), (8, 16, 32, 64, 128))
            self._flagged(self._byte(), (1, 2, 4, 8, 16, 32, 64))
        for _ in range(self._varint()):
            self.constraints["path"].append(self._string())
            self._varint()
            self._skip(1)
            for _ in range(self._varint()):
                self._ref("bone")
            self._ref("slot")
            self._flagged(self._byte(), (128,))
            self._skip(5 * _FLOAT)
        for _ in range(self._varint()):
            self.constraints["physics"].append(self._string())
            self._varint()
            self._ref("bone")
            self._flagged(self._byte(), (2, 4, 8, 16, 32, 64, 128))
            self._skip(1 + 5 * _FLOAT)
            self._flagged(self._byte(), (128,))
        self.skins: list[dict] = [self._skin(True)]
        self.skins_count_at = self.pos
        for _ in range(self._varint()):
            self.skins.append(self._skin(False))
        self.events: list[str | None] = []
        self.audio: list[bool] = []
        for _ in range(self._varint()):
            self.events.append(self._string())
            self._varint()
            self._skip(_FLOAT)
            self._string()
            self.audio.append(bool(self._string()))
            if self.audio[-1]:
                self._skip(2 * _FLOAT)
        self.anim_count_at = self.pos
        count = self._varint()
        self.anim_at = self.pos
        self.animations: list[dict] = [self._animation() for _ in range(count)]
        if self.pos != self.size:
            raise ValueError("skeleton data longer than its sections")
        self.refs.sort()
        self._check_refs()

    def _check_refs(self) -> None:
        limits = {"str": len(self.strings) + 1, "bone": len(self.bones), "slot": len(self.slots),
                  "skin": len(self.skins), "event": len(self.events), "ik": len(self.constraints["ik"]),
                  "transform": len(self.constraints["transform"]), "path": len(self.constraints["path"]),
                  "physics": len(self.constraints["physics"]), "physics1": len(self.constraints["physics"]) + 1}
        for _start, _end, kind, value in self.refs:
            if not 0 <= value < limits[kind]:
                raise ValueError(f"skeleton {kind} reference out of range")

    # --- writing

    def emit(self, start: int, end: int, remap: Remap | None = None,
             patches: Mapping[tuple[int, int], bytes] | None = None) -> bytes:
        """Bytes ``start``..``end`` with every reference in them mapped (``remap(kind, value)``) and every span of
        ``patches`` ({(a, b): bytes}, a == b to insert) inside them replaced."""
        marks = [(a, b, None, data) for (a, b), data in (patches or {}).items() if start <= a and b <= end]
        marks += [(a, b, kind, value) for a, b, kind, value in self.refs if start <= a < end]
        marks.sort(key=lambda m: (m[0], m[2] is not None, m[1]))  # at one position a patch goes first
        out = bytearray()
        pos = start
        for a, b, kind, value in marks:
            if a < pos:
                continue  # inside a patch already written
            out += self.data[pos:a]
            out += value if kind is None else varint_bytes(remap(kind, value) if remap else value)
            pos = b
        out += self.data[pos:end]
        return bytes(out)

    def slot_index(self, name: str) -> int | None:
        for index, slot in enumerate(self.slots):
            if slot["name"] == name:
                return index
        return None

    def refs_in(self, start: int, end: int) -> list[tuple[int, int, str, int]]:
        return [ref for ref in self.refs if start <= ref[0] < end]

    def attachments(self) -> dict[tuple[str, str, str], dict]:
        """{(skin name, slot name, key): attachment} over every skin."""
        out = {}
        for skin in self.skins:
            for entry in skin["entries"]:
                for attachment in entry["atts"]:
                    out[(skin["name"], self.slots[entry["slot"]]["name"], self.s(attachment["key"]))] = attachment
        return out


# --- draw order

def draw_order(offsets: list[tuple[int, int]], count: int) -> list[int]:
    """A draw-order frame as the runtime decodes it: the slot index at each position."""
    order = [-1] * count
    unchanged: list[int] = []
    original = 0
    for slot, shift in offsets:
        if not 0 <= slot < count or slot < original:
            raise ValueError("skeleton draw order out of range")
        while original != slot:
            unchanged.append(original)
            original += 1
        target = original + shift
        if not 0 <= target < count or order[target] != -1:
            raise ValueError("skeleton draw order out of range")
        order[target] = original
        original += 1
    while original < count:
        unchanged.append(original)
        original += 1
    for index in range(count - 1, -1, -1):
        if order[index] == -1:
            if not unchanged:
                raise ValueError("skeleton draw order out of range")
            order[index] = unchanged.pop()
    return order


def draw_order_bytes(order: list[int]) -> bytes:
    """A draw-order frame's offsets (count, then slot and shift pairs) that decode to ``order``: the slots off their
    own index, or every slot when the unlisted ones would not fall into place."""
    position = {slot: index for index, slot in enumerate(order)}
    moved = [(slot, position[slot] - slot) for slot in range(len(order))]
    listed = [(slot, shift) for slot, shift in moved if shift]
    if draw_order(listed, len(order)) != list(order):
        listed = moved
    return varint_bytes(len(listed)) + b"".join(varint_bytes(slot) + signed_varint_bytes(shift)
                                                for slot, shift in listed)


# --- records

def strings_patch(skeleton: Skeleton, added: list[str]) -> dict[tuple[int, int], bytes]:
    """Patches that append ``added`` to the shared strings."""
    if not added:
        return {}
    return {(skeleton.strings_count_at, skeleton.strings_at): varint_bytes(len(skeleton.strings) + len(added)),
            (skeleton.strings_end, skeleton.strings_end): b"".join(string_bytes(text) for text in added)}


def count_patch(at: int, end: int, count: int) -> dict[tuple[int, int], bytes]:
    return {(at, end): varint_bytes(count)}


def _adapt(source: Skeleton, attachment: dict, raw: bytes, nonessential: bool) -> bytes:
    if "tail" not in attachment or nonessential == source.nonessential:
        return raw
    if source.nonessential:
        return raw[:len(raw) - (attachment["end"] - attachment["tail_at"])]
    return raw + attachment["tail"]


def attachment_record(source: Skeleton, attachment: dict, remap: Remap, nonessential: bool, *,
                      name: int | None = None, path: int | None = None,
                      region: tuple[float, ...] | None = None, vertices: list[float] | None = None) -> bytes:
    """An attachment's record (after its key) for a file that does (``nonessential``) or does not hold nonessential
    data, references mapped by ``remap``. ``name``/``path``: shared-string references written in place of the
    attachment's own (the flags follow). ``region``: a region attachment's seven floats (rotation, x, y, scale x,
    scale y, width, height). ``vertices``: an unweighted mesh's vertex positions."""
    flags = attachment["flags"]
    kind = attachment["kind"]
    patches: dict[tuple[int, int], bytes] = {}
    if name is not None or path is not None or region is not None:
        refs = source.refs_in(attachment["start"] + 1, attachment["refs_end"])
        own = [ref for ref in refs if ref[2] == "str"]
        name_ref = own.pop(0) if flags & 8 else None
        path_ref = own.pop(0) if flags & 16 and kind in (REGION, MESH, LINKED_MESH) else None
        head = bytearray()
        new_flags = flags
        if name is not None:
            new_flags |= 8
        if path is not None:
            new_flags |= 16
        if region is not None:
            new_flags |= 128
        head.append(new_flags)
        if name is not None:
            head += varint_bytes(name)
        elif name_ref is not None:
            head += varint_bytes(remap("str", name_ref[3]))
        if path is not None:
            head += varint_bytes(path)
        elif path_ref is not None:
            head += varint_bytes(remap("str", path_ref[3]))
        # colour and sequence follow the name and path unchanged
        rest_at = (path_ref or name_ref or (attachment["start"], attachment["start"] + 1))[1]
        head += source.emit(rest_at, attachment["refs_end"], remap)
        patches[(attachment["start"], attachment["refs_end"])] = bytes(head)
    if region is not None:
        if kind != REGION:
            raise ValueError("not a region attachment")
        patches[(attachment["floats_at"], attachment["end"])] = floats_bytes(*region)
    if vertices is not None:
        if kind != MESH or attachment["weighted"] or len(vertices) != attachment["vertex_floats"]:
            raise ValueError("not an unweighted mesh of that size")
        at = attachment["vertices_at"]
        patches[(at, attachment["vertices_end"])] = varint_bytes(len(vertices) // 2) + floats_bytes(*vertices)
    raw = source.emit(attachment["start"], attachment["end"], remap, patches)
    return _adapt(source, attachment, raw, nonessential)


def mesh_record(source: Skeleton, attachment: dict, remap: Remap, nonessential: bool, vertices: list[float],
                uvs: list[float], triangles: list[int], hull: int, size: tuple[float, float],
                path: int | None = None) -> bytes:
    """A region attachment as an unweighted mesh record over the same pixels: its name, path, colour and sequence
    kept, ``vertices`` (bone space), ``uvs`` (over the region, v down) and ``triangles`` given, no edges."""
    if attachment["kind"] != REGION:
        raise ValueError("not a region attachment")
    flags = attachment["flags"]
    refs = [ref for ref in source.refs_in(attachment["start"] + 1, attachment["refs_end"]) if ref[2] == "str"]
    head = bytearray([(flags & (8 | 16 | 32 | 64) | (16 if path is not None else 0)) | MESH])
    pos = attachment["start"] + 1
    if flags & 8:
        head += varint_bytes(remap("str", refs[0][3]))
        pos = refs[0][1]
    if path is not None:
        head += varint_bytes(path)
        if flags & 16:
            pos = refs[-1][1]
    head += source.emit(pos, attachment["refs_end"], remap)
    count = len(vertices) // 2
    body = varint_bytes(hull) + varint_bytes(count) + floats_bytes(*vertices) + floats_bytes(*uvs)
    body += b"".join(varint_bytes(index) for index in triangles)
    if nonessential:
        body += varint_bytes(0) + floats_bytes(*size)
    return bytes(head) + body


def slot_record(source: Skeleton, slot: dict, remap: Remap, nonessential: bool) -> bytes:
    raw = source.emit(slot["start"], slot["end"], remap)
    if nonessential == source.nonessential:
        return raw
    return raw[:-1] if source.nonessential else raw + b"\x01"


def strip_nonessential(data: bytes) -> bytes:
    """The skeleton as an export without nonessential data has it."""
    skeleton = Skeleton(data)
    if not skeleton.nonessential:
        return skeleton.data
    patches = {span: b"" for span in skeleton.ness}
    patches[(skeleton.ness_flag_at, skeleton.ness_flag_at + 1)] = b"\x00"
    return skeleton.emit(0, skeleton.size, None, patches)
