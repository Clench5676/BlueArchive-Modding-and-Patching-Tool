"""The names a Spine skeleton declares, read without a Spine runtime: bones, slots, skins and animations.

Binary skeletons are read in the 4.2 layout (the game's), walked only as far as it takes to step over each section;
JSON skeletons through their keys. Skeleton data comes from mods too: every read is bounded by the data, and anything
that does not fit the layout raises ``ValueError``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: binary layouts this reader walks, by the major.minor of the skeleton's version string
BINARY_VERSIONS = ("4.2",)

_FLOAT = 4
_CURVE = 4 * _FLOAT  # one Bezier segment: cx1, cy1, cx2, cy2


@dataclass
class Names:
    version: str = ""
    # (name, parent name) in file order; the root's parent is ""
    bones: list[tuple[str, str]] = field(default_factory=list)
    # (name, bone name) in file order
    slots: list[tuple[str, str]] = field(default_factory=list)
    skins: list[str] = field(default_factory=list)
    animations: list[str] = field(default_factory=list)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.size = len(data)

    def skip(self, count: int) -> None:
        if count < 0 or self.pos + count > self.size:
            raise ValueError("skeleton data ends early")
        self.pos += count

    def byte(self) -> int:
        if self.pos >= self.size:
            raise ValueError("skeleton data ends early")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def varint(self) -> int:
        data, pos, size = self.data, self.pos, self.size
        value = shift = 0
        while True:
            if pos >= size:
                raise ValueError("skeleton data ends early")
            byte = data[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80 or shift == 28:
                break
            shift += 7
        self.pos = pos
        return value

    def varints(self, count: int) -> None:
        for _ in range(count):
            self.varint()

    def string(self) -> str | None:
        length = self.varint()
        if length == 0:
            return None
        if length == 1:
            return ""
        start = self.pos
        self.skip(length - 1)
        return self.data[start:self.pos].decode("utf-8", errors="replace")

    def name(self) -> str:
        value = self.string()
        if not value:
            raise ValueError("skeleton name missing")
        return value

    def flagged(self, flags: int, bits: tuple[int, ...]) -> None:
        """One float for every bit of ``bits`` set in ``flags``."""
        self.skip(_FLOAT * sum(1 for bit in bits if flags & bit))

    def curves(self, frames: int, frame: int, curves: int) -> None:
        """A curve timeline's frames: ``frame`` bytes each, and between two frames the curve type with, for a Bezier,
        ``curves`` segments."""
        if frames < 1:
            raise ValueError("skeleton timeline without frames")
        self.skip(frame)
        for _ in range(frames - 1):
            self.skip(frame)
            if self.byte() == 2:
                self.skip(curves * _CURVE)

    def vertices(self, weighted: bool) -> int:
        count = self.varint()
        if not weighted:
            self.skip(count * 2 * _FLOAT)
            return count
        for _ in range(count):
            for _ in range(self.varint()):
                self.varint()
                self.skip(3 * _FLOAT)
        return count


def _attachment(reader: _Reader, nonessential: bool) -> None:
    flags = reader.byte()
    if flags & 8:
        reader.varint()  # name
    kind = flags & 7
    if kind in (0, 2, 3):  # region, mesh, linked mesh: path, colour, sequence
        if flags & 16:
            reader.varint()
        if flags & 32:
            reader.skip(4)
        if flags & 64:
            reader.varints(4)
    if kind == 0:
        reader.skip(_FLOAT * (7 if flags & 128 else 6))
    elif kind == 1:
        reader.vertices(bool(flags & 16))
        if nonessential:
            reader.skip(4)
    elif kind == 2:
        hull = reader.varint()
        count = reader.vertices(bool(flags & 128)) * 2
        reader.skip(count * _FLOAT)
        reader.varints((count - hull - 2) * 3)
        if nonessential:
            reader.varints(reader.varint())
            reader.skip(2 * _FLOAT)
    elif kind == 3:
        reader.varints(2)
        if nonessential:
            reader.skip(2 * _FLOAT)
    elif kind == 4:
        count = reader.vertices(bool(flags & 64)) * 2
        reader.skip(count // 6 * _FLOAT)
        if nonessential:
            reader.skip(4)
    elif kind == 5:
        reader.skip(3 * _FLOAT)
        if nonessential:
            reader.skip(4)
    elif kind == 6:
        reader.varint()
        reader.vertices(bool(flags & 16))
        if nonessential:
            reader.skip(4)
    else:
        raise ValueError("skeleton attachment type unknown")


def _skin(reader: _Reader, default: bool, nonessential: bool) -> str | None:
    if default:
        slots = reader.varint()
        if slots == 0:
            return None
        name = "default"
    else:
        name = reader.name()
        if nonessential:
            reader.skip(4)
        for _ in range(5):  # bones, IK, transform, path and physics constraints
            reader.varints(reader.varint())
        slots = reader.varint()
    for _ in range(slots):
        reader.varint()
        for _ in range(reader.varint()):
            reader.varint()
            _attachment(reader, nonessential)
    return name


#: slot timelines: bytes after the time of a frame, and Bezier segments (RGBA, RGB, RGBA2, RGB2, alpha)
_SLOT_TIMELINES = {1: (4, 4), 2: (3, 3), 3: (7, 7), 4: (6, 6), 5: (1, 1)}
#: bone timelines with two values per frame (translate, scale, shear); the others have one
_BONE_PAIRS = {1, 4, 7}


def _ik_frame(reader: _Reader, flags: int) -> None:
    """An IK frame's time, mix (when it is not 0 or 1) and softness (when it is not 0)."""
    reader.skip(_FLOAT * (1 + (1 if flags & 1 and flags & 2 else 0) + (1 if flags & 4 else 0)))


def _animation(reader: _Reader, audio: list[bool]) -> None:
    reader.varint()  # timeline count
    for _ in range(reader.varint()):  # slots
        reader.varint()
        for _ in range(reader.varint()):
            kind, frames = reader.byte(), reader.varint()
            if kind == 0:
                for _ in range(frames):
                    reader.skip(_FLOAT)
                    reader.varint()
            elif kind in _SLOT_TIMELINES:
                values, curves = _SLOT_TIMELINES[kind]
                reader.varint()
                reader.curves(frames, _FLOAT + values, curves)
            else:
                raise ValueError("skeleton slot timeline unknown")
    for _ in range(reader.varint()):  # bones
        reader.varint()
        for _ in range(reader.varint()):
            kind, frames = reader.byte(), reader.varint()
            if kind == 10:
                reader.skip(frames * (_FLOAT + 1))
                continue
            if kind > 10:
                raise ValueError("skeleton bone timeline unknown")
            reader.varint()
            values = 2 if kind in _BONE_PAIRS else 1
            reader.curves(frames, _FLOAT * (1 + values), values)
    for _ in range(reader.varint()):  # IK constraints
        reader.varint()
        frames = reader.varint()
        reader.varint()
        if frames < 1:
            raise ValueError("skeleton timeline without frames")
        _ik_frame(reader, reader.byte())
        for _ in range(frames - 1):
            flags = reader.byte()
            _ik_frame(reader, flags)
            if not flags & 64 and flags & 128:
                reader.skip(2 * _CURVE)
    for _ in range(reader.varint()):  # transform constraints
        reader.varint()
        frames = reader.varint()
        reader.varint()
        reader.curves(frames, 7 * _FLOAT, 6)
    for _ in range(reader.varint()):  # path constraints
        reader.varint()
        for _ in range(reader.varint()):
            kind, frames = reader.byte(), reader.varint()
            reader.varint()
            if kind in (0, 1):
                reader.curves(frames, 2 * _FLOAT, 1)
            elif kind == 2:
                reader.curves(frames, 4 * _FLOAT, 3)
            else:
                raise ValueError("skeleton path timeline unknown")
    for _ in range(reader.varint()):  # physics constraints
        reader.varint()
        for _ in range(reader.varint()):
            kind, frames = reader.byte(), reader.varint()
            if kind == 8:
                reader.skip(frames * _FLOAT)
                continue
            if kind > 8 or kind == 3:
                raise ValueError("skeleton physics timeline unknown")
            reader.varint()
            reader.curves(frames, 2 * _FLOAT, 1)
    for _ in range(reader.varint()):  # attachments, per skin and slot
        reader.varint()
        for _ in range(reader.varint()):
            reader.varint()
            for _ in range(reader.varint()):
                reader.varint()
                kind, frames = reader.byte(), reader.varint()
                if kind == 0:
                    reader.varint()
                    if frames < 1:
                        raise ValueError("skeleton timeline without frames")
                    reader.skip(_FLOAT)
                    for frame in range(frames):
                        count = reader.varint()
                        if count:
                            reader.varint()
                            reader.skip(count * _FLOAT)
                        if frame == frames - 1:
                            break
                        reader.skip(_FLOAT)
                        if reader.byte() == 2:
                            reader.skip(_CURVE)
                elif kind == 1:
                    reader.skip(frames * (2 * _FLOAT + 4))
                else:
                    raise ValueError("skeleton attachment timeline unknown")
    for _ in range(reader.varint()):  # draw order
        reader.skip(_FLOAT)
        reader.varints(reader.varint() * 2)
    for _ in range(reader.varint()):  # events
        reader.skip(_FLOAT)
        event = reader.varint()
        if event >= len(audio):
            raise ValueError("skeleton event unknown")
        reader.varint()
        reader.skip(_FLOAT)
        reader.string()
        if audio[event]:
            reader.skip(2 * _FLOAT)


def _binary(data: bytes) -> Names:
    reader = _Reader(data)
    reader.skip(8)  # hash
    names = Names(version=reader.string() or "")
    if not names.version.startswith(tuple(version + "." for version in BINARY_VERSIONS)):
        raise ValueError(f"skeleton version {names.version!r} not read")
    reader.skip(5 * _FLOAT)
    nonessential = reader.byte() != 0
    if nonessential:
        reader.skip(_FLOAT)
        reader.string()
        reader.string()
    for _ in range(reader.varint()):  # shared strings
        reader.string()
    for index in range(reader.varint()):
        name = reader.name()
        parent = ""
        if index:
            parent_index = reader.varint()
            if parent_index >= index:
                raise ValueError("skeleton bone parent unknown")
            parent = names.bones[parent_index][0]
        reader.skip(8 * _FLOAT + 2)
        if nonessential:
            reader.skip(4)
            reader.string()
            reader.skip(1)
        names.bones.append((name, parent))
    for _ in range(reader.varint()):
        name = reader.name()
        bone = reader.varint()
        if bone >= len(names.bones):
            raise ValueError("skeleton slot bone unknown")
        reader.skip(8)
        reader.varints(2)
        if nonessential:
            reader.skip(1)
        names.slots.append((name, names.bones[bone][0]))
    for _ in range(reader.varint()):  # IK constraints
        reader.name()
        reader.varint()
        reader.varints(reader.varint() + 1)
        flags = reader.byte()
        if flags & 32 and flags & 64:
            reader.skip(_FLOAT)
        if flags & 128:
            reader.skip(_FLOAT)
    for _ in range(reader.varint()):  # transform constraints
        reader.name()
        reader.varint()
        reader.varints(reader.varint() + 1)
        reader.flagged(reader.byte(), (8, 16, 32, 64, 128))
        reader.flagged(reader.byte(), (1, 2, 4, 8, 16, 32, 64))
    for _ in range(reader.varint()):  # path constraints
        reader.name()
        reader.varint()
        reader.skip(1)
        reader.varints(reader.varint() + 1)
        reader.flagged(reader.byte(), (128,))
        reader.skip(5 * _FLOAT)
    for _ in range(reader.varint()):  # physics constraints
        reader.name()
        reader.varints(2)
        reader.flagged(reader.byte(), (2, 4, 8, 16, 32, 64, 128))
        reader.skip(1 + 5 * _FLOAT)
        reader.flagged(reader.byte(), (128,))
    skin = _skin(reader, True, nonessential)
    if skin:
        names.skins.append(skin)
    for _ in range(reader.varint()):
        names.skins.append(_skin(reader, False, nonessential) or "")
    audio = []
    for _ in range(reader.varint()):  # events
        reader.name()
        reader.varint()
        reader.skip(_FLOAT)
        reader.string()
        audio.append(bool(reader.string()))
        if audio[-1]:
            reader.skip(2 * _FLOAT)
    for _ in range(reader.varint()):
        names.animations.append(reader.name())
        _animation(reader, audio)
    if reader.pos != reader.size:
        raise ValueError("skeleton data longer than its sections")
    return names


def _json(data: bytes) -> Names:
    try:
        root = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("skeleton JSON unreadable") from error
    if not isinstance(root, dict):
        raise ValueError("skeleton JSON unreadable")

    def text(value) -> str:
        return value if isinstance(value, str) else ""

    def items(key: str) -> list[dict]:
        value = root.get(key)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    skeleton = root.get("skeleton")
    names = Names(version=text(skeleton.get("spine")) if isinstance(skeleton, dict) else "")
    names.bones = [(text(bone.get("name")), text(bone.get("parent"))) for bone in items("bones")]
    names.slots = [(text(slot.get("name")), text(slot.get("bone"))) for slot in items("slots")]
    skins = root.get("skins")
    if isinstance(skins, dict):  # 3.x: by name
        names.skins = [text(name) for name in skins]
    else:
        names.skins = [text(skin.get("name")) for skin in items("skins")]
    animations = root.get("animations")
    if isinstance(animations, dict):
        names.animations = [text(name) for name in animations]
    return names


def read(data: bytes) -> Names:
    """The names of a skeleton. ``ValueError`` for data that is not a skeleton this reader walks (another binary
    version, damaged or cut data)."""
    if data.lstrip()[:1] == b"{":
        # a binary skeleton's hash may start with the same byte
        try:
            return _json(data)
        except ValueError:
            pass
    try:
        return _binary(data)
    except (IndexError, UnicodeError, OverflowError, MemoryError) as error:
        raise ValueError("skeleton data unreadable") from error


def version(data: bytes) -> str:
    """The version string at the head of a binary skeleton, or '' when there is none."""
    match = re.search(rb"\d+\.\d+\.\d+", data[:48])
    return match.group().decode("ascii") if match else ""
