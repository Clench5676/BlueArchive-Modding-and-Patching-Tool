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
