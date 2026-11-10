"""What a mod was built against: a small fingerprint of the official parts of its core, kept beside the mod's files.

A game update rebuilds a part in place and the library releases the official file it kept of the release before, so
the mod's own baseline is gone by the time anyone asks what changed under it. The fingerprint keeps what that takes:
per part its name, the hash of its structure, per atlas a layout hash, the region names and the atlas text (the
layout a texture-only mod was painted on, which moving its pixels to a repacked atlas needs), per skeleton its hash,
a rig hash and the animation names, and the texture sizes. It is made from headers and text assets (no image is
decoded), and two fingerprints are compared as plain data (``diff``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import atlas_remap, spine_names
from . import integrity
from .storage import atomic_json

VERSION = 1
#: the file beside a mod's bundles; never a bundle name, so it is no part of the mod
FILE_NAME = "built-against.json"
#: a larger file is not one this module wrote
MAX_BYTES = 8 * 1024 * 1024

#: how a mod's own files stood to an official part when the fingerprint was taken
SAME, DIFFERS, ABSENT = "same", "differs", "absent"


def _digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def atlas(data: bytes) -> dict:
    """An atlas as a layout hash (pages with their sizes, regions with their places), its region names and its
    text. An atlas that cannot be read keeps the hash of its bytes alone."""
    try:
        parsed = atlas_remap.parse_atlas(data)
    except (atlas_remap.RemapError, ValueError, UnicodeError):
        return {"layout": hashlib.sha1(data).hexdigest(), "regions": None}
    pages = sorted([page.name, list(page.size)] for page in parsed.pages.values())
    regions = sorted([region.page, region.name, region.index, list(region.xy), list(region.size),
                      list(region.original), list(region.offset), region.degrees]
                     for region in parsed.regions.values())
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        text = None
    return {"layout": _digest([pages, regions]),
            "regions": sorted({region.label for region in parsed.regions.values()}), "text": text}


def skeleton(data: bytes) -> dict:
    """A skeleton as the hash of its bytes, its version, a rig hash (bones with parents, slots with bones) and its
    animation names. One this build cannot walk keeps the hash and version alone."""
    result: dict[str, Any] = {"sha1": hashlib.sha1(data).hexdigest(), "version": spine_names.version(data),
                              "rig": None, "animations": None}
    try:
        names = spine_names.read(data)
    except ValueError:
        return result
    result["version"] = names.version or result["version"]
    result["rig"] = _digest([names.bones, names.slots])
    result["animations"] = list(names.animations)
    return result


def _assets(contents: integrity.Contents) -> dict:
    return {"atlases": {integrity.basename(name): atlas(data) for name, data in sorted(contents.atlases.items())},
            "skeletons": {integrity.basename(name): skeleton(data)
                          for name, data in sorted(contents.skeletons.items())},
            "textures": {name: list(size) for name, size in sorted(contents.textures.items())}}


def part(inspection: integrity.Inspection, path: Path, *, name: str, sha1: str | None = None) -> dict:
    """The fingerprint of one official part, read from ``path`` and recorded under the part's file ``name``."""
    contents = inspection.inspect(path, allow_unknown=True)
    try:
        files, layout, externals = inspection.structure(path)
        structure = _digest([sorted(files), layout, externals])
    except Exception:  # noqa: BLE001 - headers that cannot be read leave the structure unknown
        structure = None
    return {"name": name, "sha1": sha1, "structure": structure, **_assets(contents)}


def carried(inspection: integrity.Inspection, paths: list[Path]) -> dict:
    """The atlases, skeletons and texture sizes a mod's own files carry, for ``standing``."""
    merged = integrity.Contents()
    for path in paths:
        merged.merge(inspection.inspect(path, allow_unknown=True))
    return _assets(merged)


def standing(official: dict, mod: dict) -> str:
    """How a mod's own files stand to an official part: ``same`` when every atlas, skeleton and texture of the part
    the mod carries is the part's (layout, bytes, size), ``differs`` when one is not, ``absent`` when it carries
    none of them."""
    found = False
    for kind, key in (("atlases", "layout"), ("skeletons", "sha1"), ("textures", None)):
        for name, value in official[kind].items():
            other = mod[kind].get(name)
            if other is None:
                continue
            found = True
            if (other != value) if key is None else (other[key] != value[key]):
                return DIFFERS
    return SAME if found else ABSENT


def painted(parts: list[dict], mod: dict, skeletons: dict[str, bytes] | None = None) -> dict:
    """The official atlases a mod's textures are drawn on without the mod carrying them (``carried``): per atlas
    name its entry in ``parts``, with the hull outlines of the official skeleton that draws it (``hulls``, from
    ``skeletons``: the parts' own, by name; None when it cannot be read). For a mod known to be made for these
    parts; a later repack of such an atlas leaves the mod's texture on the old layout, which this keeps, with what
    it takes to move each region's own pixels to the new one."""
    result = {}
    for item in parts:
        for name, entry in item["atlases"].items():
            if name in mod["atlases"] or not entry.get("text"):
                continue
            try:
                pages = atlas_remap.parse_atlas(entry["text"]).pages
            except (atlas_remap.RemapError, ValueError):
                continue
            if any(integrity.texture_key(page) in mod["textures"] for page in pages):
                result[name] = dict(entry)
    drawn_by = atlas_remap.pair_atlases(skeletons or {}, dict.fromkeys(result))
    for name, entry in result.items():
        try:
            entry["hulls"] = spine_names.hulls(skeletons[drawn_by[name]]) if name in drawn_by else None
        except ValueError:
            entry["hulls"] = None
    return result


def changed_layouts(data: dict | None, atlases: dict[str, bytes]) -> dict[str, dict]:
    """Of the official ``atlases`` as they are now, the ones repacked since a mod's textures were drawn on them
    (``painted`` in its fingerprint): per atlas name the entry of the layout the textures follow (``text``,
    ``hulls``)."""
    known = (data or {}).get("painted") or {}
    result = {}
    for name, current in atlases.items():
        entry = known.get(integrity.basename(name))
        if entry and isinstance(entry.get("text"), str) and entry.get("layout") != atlas(current)["layout"]:
            result[name] = entry
    return result


def document(event: str, parts: list[dict], *, complete: bool, painted: dict | None = None) -> dict:
    """A fingerprint to store: ``event`` is what made it (import, update, remap, backfill); ``complete`` says the
    parts are every part the mod's core had then; ``painted`` (``painted``) is left out when nothing tells which
    layout the mod's textures follow."""
    data = {"version": VERSION, "event": event, "recorded": datetime.now(timezone.utc).isoformat(),
            "complete": complete, "parts": parts}
    if painted is not None:
        data["painted"] = painted
    return data


def write(directory: Path, data: dict, files: dict[str, str]) -> None:
    """Store a fingerprint beside the mod files it describes (``files``: name to SHA-1)."""
    atomic_json(directory / FILE_NAME, {**data, "files": dict(sorted(files.items()))})


def read(directory: Path, files: dict[str, str]) -> dict | None:
    """The stored fingerprint of the mod files ``files`` (name to SHA-1). None when there is none, it describes
    other files (the mod was rebuilt since) or it is not one this build reads."""
    path = directory / FILE_NAME
    try:
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if (not isinstance(data, dict) or data.get("version") != VERSION or data.get("files") != files
            or not isinstance(data.get("parts"), list)):
        return None
    known = data.get("painted")
    if known is not None and (not isinstance(known, dict)
                              or any(not isinstance(entry, dict) for entry in known.values())):
        return None
    for item in data["parts"]:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or any(not isinstance(item.get(kind), dict) for kind in ("atlases", "skeletons", "textures"))):
            return None
    return data


def diff(before: dict, after: dict) -> dict:
    """What changed between two fingerprints of one part (``part``), as plain data: ``structure`` (None when either
    is unknown), and per atlas, skeleton and texture name what differs. Unchanged assets are left out; an empty
    result but for ``structure: False`` means the part is the one the mod was built against.

    An atlas: ``layout`` changed, regions ``added`` and ``removed``. A skeleton: ``changed`` bytes, ``rig`` changed
    (None when either is unknown), animations ``added`` and ``removed``. A texture: its sizes ``before`` and
    ``after``. An asset only one side has is reported with ``present`` naming that side."""

    def names(value: dict, key: str) -> set[str]:
        return set(value.get(key) or ())

    def unknown_or(old, new):
        return None if old is None or new is None else old != new

    result: dict[str, Any] = {"structure": unknown_or(before.get("structure"), after.get("structure")),
                              "atlases": {}, "skeletons": {}, "textures": {}}
    for kind in ("atlases", "skeletons", "textures"):
        old, new = before[kind], after[kind]
        for name in sorted(old.keys() | new.keys()):
            if name not in old or name not in new:
                result[kind][name] = {"present": "before" if name in old else "after"}
                continue
            a, b = old[name], new[name]
            if a == b or (kind == "atlases" and a["layout"] == b["layout"]
                          and names(a, "regions") == names(b, "regions")):
                # the atlas text is kept for the remap, not compared: older fingerprints lack it
                continue
            if kind == "atlases":
                result[kind][name] = {"layout": a["layout"] != b["layout"],
                                      "added": sorted(names(b, "regions") - names(a, "regions")),
                                      "removed": sorted(names(a, "regions") - names(b, "regions"))}
            elif kind == "skeletons":
                result[kind][name] = {"changed": a["sha1"] != b["sha1"], "rig": unknown_or(a["rig"], b["rig"]),
                                      "added": sorted(names(b, "animations") - names(a, "animations")),
                                      "removed": sorted(names(a, "animations") - names(b, "animations"))}
            else:
                result[kind][name] = {"before": a, "after": b}
    return result


#: what a game update did to the official parts a mod was built against (``impact``), from the least to the most
#: that needs the user: the same files; rebuilt with the rig the mod was made for; new official parts beside those the
#: mod knows; the rig itself changed (atlas layout or regions, skeleton bones, slots or animations, texture sizes, an
#: asset gone); no official part of the core left. ``unknown``: nothing tells (no fingerprint, unreadable parts).
UNCHANGED, REBUILT, NEW_PARTS, RIG_CHANGED, GONE, UNKNOWN = (
    "unchanged", "rebuilt", "new_parts", "rig_changed", "gone", "unknown")
IMPACTS = (UNCHANGED, REBUILT, NEW_PARTS, RIG_CHANGED, GONE, UNKNOWN)


def _merged(parts: list[dict], kinds: tuple[str, ...] = ("atlases", "skeletons", "textures")) -> dict:
    merged: dict[str, Any] = {"structure": None, **{kind: {} for kind in kinds}}
    for item in parts:
        for kind in kinds:
            merged[kind].update(item.get(kind) or {})
    return merged


def _rig_changed(changes: dict) -> bool:
    """Whether a ``diff`` of a core's assets names a change of its rig: an asset gone, an atlas repacked or with
    other regions, a skeleton with other bones, slots or animations (or other bytes where its rig cannot be read),
    a texture of another size. A new asset alone is a new part, not a change of the rig."""
    for name, change in changes["atlases"].items():
        if change.get("present") == "before" or change.get("layout") or change.get("added") or change.get("removed"):
            return True
    for name, change in changes["skeletons"].items():
        if change.get("present") == "before" or change.get("rig") or change.get("added") or change.get("removed"):
            return True
        if change.get("changed") and change.get("rig") is None and "present" not in change:
            return True
    return any(change.get("present") != "after" for change in changes["textures"].values())


def impact(built: dict | None, now: dict | None) -> str:
    """What a game update did under a mod (``IMPACTS``): the fingerprint it was built against (``built``) held
    against the one its core's official parts give now (``now``: None when the core has no official part left, which
    is ``gone``). Parts are matched by the assets they hold, so a part renamed by the update compares with the one
    it replaced. A fingerprint that does not know every part of the core then (``complete`` False) tells no new
    parts."""
    if not built or not built.get("parts"):
        return UNKNOWN
    if now is None:
        return GONE
    if not now.get("parts"):
        return UNKNOWN
    before = {item["name"] for item in built["parts"]}
    after = {item["name"] for item in now["parts"]}
    complete = bool(built.get("complete"))
    if before == after or (not complete and before <= after):
        return UNCHANGED
    changes = diff(_merged(built["parts"]), _merged(now["parts"]))
    if _rig_changed(changes):
        return RIG_CHANGED
    added = any(change.get("present") == "after" for kind in ("atlases", "skeletons", "textures")
                for change in changes[kind].values())
    if complete and (added or len(after) > len(before)):
        return NEW_PARTS
    return REBUILT
