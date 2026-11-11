"""Test rigs for the graft (bmpt.graft): an official skeleton with parts taken out, to be grafted back."""

from __future__ import annotations

from bmpt.spine_binary import Skeleton, draw_order, draw_order_bytes, varint_bytes


def remove_animations(data: bytes, names) -> bytes:
    """The skeleton without the animations ``names`` and the region and mesh attachments of the default skin only
    they show."""
    sk = Skeleton(data)
    names = set(names)
    gone = [a for a in sk.animations if a["name"] in names]
    kept = [a for a in sk.animations if a["name"] not in names]
    patches = {(a["start"], a["end"]): b"" for a in gone}
    patches[(sk.anim_count_at, sk.anim_at)] = varint_bytes(len(kept))

    def shown(animations):
        out = set()
        for an in animations:
            slot = None
            for _a, _b, kind, value in sk.refs_in(an["body"], an["end"]):
                if kind == "slot":
                    slot = value
                elif kind == "str" and value:
                    out.add((slot, value))
        return out

    still = shown(kept) | {(i, s["attachment"]) for i, s in enumerate(sk.slots) if s["attachment"]}
    drop = shown(gone) - still
    for entry in sk.skins[0]["entries"]:
        out = [a for a in entry["atts"] if (entry["slot"], a["key"]) in drop and a["kind"] in (0, 2)]
        for a in out:
            patches[(a["entry_start"], a["end"])] = b""
        if out:
            patches[(entry["count_at"], entry["count_end"])] = varint_bytes(len(entry["atts"]) - len(out))
    return sk.emit(0, sk.size, None, patches)


def remove_slot(data: bytes, name: str):
    """(bytes, removed animation names) without the slot ``name``, its skin entries and the animations that name
    it (draw order of the others rewritten); None when something outside animations and skins needs it."""
    sk = Skeleton(data)
    i = sk.slot_index(name)
    n = len(sk.slots)
    inside = [(a["body"], a["end"]) for a in sk.animations]
    entries = [e["start"] for s in sk.skins for e in s["entries"]]
    for a, _b, kind, value in sk.refs:
        if kind == "slot" and value == i and not any(x <= a < y for x, y in inside) and a not in entries:
            return None
    gone = []
    for an in sk.animations:
        explicit = any(kind == "slot" and value == i for _, _, kind, value in sk.refs_in(an["body"], an["end"]))
        listed = any(slot == i for _, _, offsets in an["draworder"] for slot, _ in offsets)
        if explicit or listed:
            gone.append(an)
    patches = {(a["start"], a["end"]): b"" for a in gone}
    patches[(sk.anim_count_at, sk.anim_at)] = varint_bytes(len(sk.animations) - len(gone))
    patches[(sk.slots_count_at, sk.slots[0]["start"])] = varint_bytes(n - 1)
    patches[(sk.slots[i]["start"], sk.slots[i]["end"])] = b""
    for skin in sk.skins:
        mine = [e for e in skin["entries"] if e["slot"] == i]
        for e in mine:
            patches[(e["start"], e["end"])] = b""
        if mine:
            patches[(skin["count_at"], skin["count_end"])] = varint_bytes(len(skin["entries"]) - len(mine))
    for an in sk.animations:
        if an in gone:
            continue
        for a, b, offsets in an["draworder"]:
            order = [s - (s > i) for s in draw_order(offsets, n) if s != i]
            patches[(a, b)] = draw_order_bytes(order)
    out = sk.emit(0, sk.size, lambda kind, v: v - (v > i) if kind == "slot" else v, patches)
    return out, [a["name"] for a in gone]


# --- synthetic binary skeletons (the 4.2 layout bmpt.spine_binary walks)

def _varint(value: int) -> bytes:
    return varint_bytes(value)


def _text(value: str | None) -> bytes:
    if value is None:
        return b"\x00"
    data = value.encode()
    return _varint(len(data) + 1) + data


def _floats(*values: float) -> bytes:
    import struct
    return struct.pack(f">{len(values)}f", *values)


def region(x=0.0, y=0.0, w=4.0, h=4.0, rotation=None, path=None) -> dict:
    return {"kind": "region", "floats": (x, y, 1.0, 1.0, w, h), "rotation": rotation, "path": path}


def mesh(vertices, uvs, triangles, hull=None, path=None) -> dict:
    return {"kind": "mesh", "vertices": vertices, "uvs": uvs, "triangles": triangles,
            "hull": len(vertices) // 2 if hull is None else hull, "path": path}


def skeleton(*, bones=(("root", None),), slots=(("face", "root", None),), skin=None, animations=(),
             nonessential=False, bone_floats=None) -> bytes:
    """A binary 4.2 skeleton: ``bones`` (name, parent), ``slots`` (name, bone, setup attachment), ``skin`` {slot:
    [(key, attachment)]} for the default skin, ``animations`` [(name, {slot: [attachment, ...]}, [draw-order frames as
    slot orders] )]."""
    strings: list[str] = []

    def ref(text):
        if text is None:
            return _varint(0)
        if text not in strings:
            strings.append(text)
        return _varint(strings.index(text) + 1)

    bone_names = [name for name, _ in bones]
    slot_names = [name for name, _b, _a in slots]
    out = bytearray()
    for i, (name, parent) in enumerate(bones):
        out += _text(name)
        if i:
            out += _varint(bone_names.index(parent))
        out += _floats(*((bone_floats or {}).get(name, (0, 0, 0, 1, 1, 0, 0, 0))))
        out += b"\x00\x00"
        if nonessential:
            out += b"\xff\xff\xff\xff" + _text(None) + b"\x01"
    body = _varint(len(bones)) + bytes(out)
    out = bytearray(_varint(len(slots)))
    for name, bone, attachment in slots:
        out += _text(name) + _varint(bone_names.index(bone)) + b"\xff\xff\xff\xff" + b"\xff\xff\xff\xff"
        out += ref(attachment) + _varint(0)
        if nonessential:
            out += b"\x01"
    body += bytes(out) + _varint(0) * 4  # no constraints
    entries = list((skin or {}).items())
    out = bytearray(_varint(len(entries)))
    for slot, atts in entries:
        out += _varint(slot_names.index(slot)) + _varint(len(atts))
        for key, a in atts:
            out += ref(key)
            path = a.get("path")
            if a["kind"] == "region":
                flags = (128 if a["rotation"] is not None else 0) | (16 if path else 0)
                out += bytes([flags]) + (ref(path) if path else b"")
                out += _floats(*(((a["rotation"],) if a["rotation"] is not None else ()) + a["floats"]))
            else:
                flags = 2 | (16 if path else 0)
                out += bytes([flags]) + (ref(path) if path else b"")
                count = len(a["vertices"]) // 2
                out += _varint(a["hull"]) + _varint(count) + _floats(*a["vertices"]) + _floats(*a["uvs"])
                out += b"".join(_varint(i) for i in a["triangles"])
                if nonessential:
                    out += _varint(0) + _floats(0, 0)
    body += bytes(out) + _varint(0) + _varint(0)  # no other skins, no events
    out = bytearray(_varint(len(animations)))
    for name, timelines, orders in animations:
        out += _text(name) + _varint(len(timelines) + (1 if orders else 0))
        out += _varint(len(timelines))
        for slot, names in timelines.items():
            out += _varint(slot_names.index(slot)) + _varint(1) + b"\x00" + _varint(len(names))
            for i, attachment in enumerate(names):
                out += _floats(i / 10) + ref(attachment)
        out += _varint(0) * 6  # bones, IK, transform, path, physics, attachment timelines
        out += _varint(len(orders))
        for i, order in enumerate(orders):
            out += _floats(i / 10) + draw_order_bytes([slot_names.index(slot) for slot in order])
        out += _varint(0)  # events
    body += bytes(out)
    head = b"\x01" * 8 + _text("4.2.33") + _floats(0, 0, 100, 100, 100) + (b"\x01" if nonessential else b"\x00")
    if nonessential:
        head += _floats(30) + _text(None) + _text(None)
    return head + _varint(len(strings)) + b"".join(_text(s) for s in strings) + body
