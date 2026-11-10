"""What a mod was built against: a small fingerprint of the official parts of its core, kept beside the mod's files.

A game update rebuilds a part in place and the library releases the official file it kept of the release before, so
the mod's own baseline is gone by the time anyone asks what changed under it. The fingerprint keeps what that takes,
and nothing of the game's content: per part its name, the hash of its structure, per atlas a layout hash and the
region names, per skeleton its hash, a rig hash and the animation names, and the texture sizes. It is made from
headers and text assets (no image is decoded), and two fingerprints are compared as plain data (``diff``).
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
    """An atlas as a layout hash (pages with their sizes, regions with their places) and its region names. An atlas
    that cannot be read keeps the hash of its bytes and no names."""
    try:
        parsed = atlas_remap.parse_atlas(data)
    except (atlas_remap.RemapError, ValueError, UnicodeError):
        return {"layout": hashlib.sha1(data).hexdigest(), "regions": None}
    pages = sorted([page.name, list(page.size)] for page in parsed.pages.values())
    regions = sorted([region.page, region.name, region.index, list(region.xy), list(region.size),
                      list(region.original), list(region.offset), region.degrees]
                     for region in parsed.regions.values())
    return {"layout": _digest([pages, regions]),
            "regions": sorted({region.label for region in parsed.regions.values()})}


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


def document(event: str, parts: list[dict], *, complete: bool) -> dict:
    """A fingerprint to store: ``event`` is what made it (import, update, remap, backfill); ``complete`` says the
    parts are every part the mod's core had then."""
    return {"version": VERSION, "event": event, "recorded": datetime.now(timezone.utc).isoformat(),
            "complete": complete, "parts": parts}


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
            if a == b:
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
