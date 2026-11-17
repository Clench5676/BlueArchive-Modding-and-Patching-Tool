"""What a mod was built against: a small fingerprint of the official parts of its core, kept beside the mod's files.

A game update rebuilds a part in place and the library releases the official file it kept of the release before, so
the mod's own baseline is gone by the time anyone asks what changed under it. The fingerprint keeps what that takes:
per part its name, the hash of its structure, per atlas a layout hash, the region names and the atlas text (the
layout a texture-only mod was painted on, which moving its pixels to a repacked atlas needs), per skeleton its hash,
a rig hash and the animation names, and the texture sizes. It is made from headers and text assets (no image is
decoded), and two fingerprints are compared as plain data (``diff``).
"""

from __future__ import annotations

import base64
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
    """Fingerprint atlas page sizes, region placement, region names, and source text.

    Args:
        data: Atlas text bytes; unreadable atlases retain only a byte hash and unknown regions.
    """
    try:
        parsed = atlas_remap.parse_atlas(data)
    except (atlas_remap.RemapError, ValueError, UnicodeError):
        return {"layout": hashlib.sha1(data).hexdigest(), "regions": None}
    pages = sorted([page.name, list(page.size)] for page in parsed.pages.values())
    regions = sorted(
        [
            region.page,
            region.name,
            region.index,
            list(region.xy),
            list(region.size),
            list(region.original),
            list(region.offset),
            region.degrees,
        ]
        for region in parsed.regions.values()
    )
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        text = None
    return {
        "layout": _digest([pages, regions]),
        "regions": sorted({region.label for region in parsed.regions.values()}),
        "text": text,
    }


def skeleton(data: bytes) -> dict:
    """Fingerprint skeleton bytes, version, bone/slot relationships, and animation names.

    Args:
        data: Spine skeleton bytes; an unreadable rig retains only its byte hash and version.
    """
    result: dict[str, Any] = {
        "sha1": hashlib.sha1(data).hexdigest(),
        "version": spine_names.version(data),
        "rig": None,
        "animations": None,
    }
    try:
        names = spine_names.read(data)
    except ValueError:
        return result
    result["version"] = names.version or result["version"]
    result["rig"] = _digest([names.bones, names.slots])
    result["animations"] = list(names.animations)
    return result


def _assets(contents: integrity.Contents) -> dict:
    return {
        "atlases": {integrity.basename(name): atlas(data) for name, data in sorted(contents.atlases.items())},
        "skeletons": {integrity.basename(name): skeleton(data) for name, data in sorted(contents.skeletons.items())},
        "textures": {name: list(size) for name, size in sorted(contents.textures.items())},
    }


def part(inspection: integrity.Inspection, path: Path, *, name: str, sha1: str | None = None) -> dict:
    """Fingerprint an official part's structure and assets without decoding images.

    Args:
        inspection: Analysis snapshot providing consistent metadata and header reads.
        path: Official bundle bytes, which may be a kept copy rather than the live target.
        name: Live part filename to record independently of the copy's location.
        sha1: Known whole-file digest to retain, or None when not supplied.
    """
    contents = inspection.inspect(path, allow_unknown=True)
    try:
        files, layout, externals = inspection.structure(path)
        structure = _digest([sorted(files), layout, externals])
    except Exception:  # noqa: BLE001 - headers that cannot be read leave the structure unknown
        structure = None
    return {"name": name, "sha1": sha1, "structure": structure, **_assets(contents)}


def carried(inspection: integrity.Inspection, paths: list[Path]) -> dict:
    """Merge the atlases, skeletons, and texture sizes a mod carries for standing comparisons.

    Args:
        inspection: Analysis snapshot providing consistent bundle reads.
        paths: Mod bundles whose carried assets are fingerprinted together.
    """
    merged = integrity.Contents()
    for path in paths:
        merged.merge(inspection.inspect(path, allow_unknown=True))
    return _assets(merged)


def standing(official: dict, mod: dict) -> str:
    """Compare a mod's carried assets with one official part.

    Args:
        official: Part fingerprint defining the atlas layouts, skeleton bytes, and texture sizes.
        mod: Merged carried fingerprint of the mod's own files.

    Returns:
        same when all shared assets match, differs when any differ, or absent when none are shared.
    """
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
    """Keep official atlas layouts and rigs needed by a texture-only mod's painted pages.

    Retain each atlas entry by name, with its paired official skeleton encoded as
    base64 when available. A later repack leaves the mod's textures on their original
    layout; the retained layout and rig provide the source for bmpt.rebake.

    Args:
        parts: Official part fingerprints the mod is known to have been made for.
        mod: Carried fingerprint identifying textures present and atlases absent from the mod.
        skeletons: Official skeleton bytes by asset name; None omits paired skeleton payloads.
    """
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
        entry.pop("hulls", None)
        if name in drawn_by:
            entry["skeleton"] = base64.b64encode(skeletons[drawn_by[name]]).decode("ascii")
    return result


def changed_layouts(data: dict | None, atlases: dict[str, bytes]) -> dict[str, dict]:
    """Find official atlases repacked relative to a mod's recorded painted layouts.

    Args:
        data: Mod fingerprint containing painted entries, or None when no baseline is known.
        atlases: Current official atlas bytes indexed by asset name.

    Returns:
        Changed atlas names mapped to their recorded layout entries, including text and any skeleton.
    """
    known = (data or {}).get("painted") or {}
    result = {}
    for name, current in atlases.items():
        entry = known.get(integrity.basename(name))
        if entry and isinstance(entry.get("text"), str) and entry.get("layout") != atlas(current)["layout"]:
            result[name] = entry
    return result


def drawn_on(painted: dict[str, dict], files: dict[str, bytes]) -> dict[str, bytes]:
    """Restore recorded atlas/rig pairs so a texture-only mod's preview shows its intended layout.

    Args:
        painted: Recorded painted-layout entries that include their base64 skeletons.
        files: Preview Spine bytes by asset name, with mod assets over official assets.

    Returns:
        Replacement atlas text and paired skeleton bytes under names from files;
        empty when no usable recorded pair differs from the preview's layout.
    """
    known = {
        name: entry
        for name, entry in painted.items()
        if isinstance(entry, dict) and isinstance(entry.get("skeleton"), str)
    }
    atlases = {name: data for name, data in files.items() if name.lower().endswith(".atlas")}
    skeletons = {name: data for name, data in files.items() if name.lower().endswith(".skel")}
    changed = changed_layouts({"painted": known}, atlases)
    drawn_by = atlas_remap.pair_atlases(skeletons, dict.fromkeys(changed))
    result = {}
    for name, entry in changed.items():
        if name not in drawn_by:
            continue
        try:
            skeleton = base64.b64decode(entry["skeleton"], validate=True)
            text = entry["text"].encode("utf-8")
        except (ValueError, UnicodeError):
            continue
        result[name], result[drawn_by[name]] = text, skeleton
    return result


def document(event: str, parts: list[dict], *, complete: bool, painted: dict | None = None) -> dict:
    """Build a timestamped fingerprint document for storage beside a mod.

    Args:
        event: Operation establishing the baseline, such as import, update, remap, or backfill.
        parts: Fingerprints of the official parts the mod was built against.
        complete: Whether parts includes every official part of the core at recording time.
        painted: Known layouts followed by the mod's textures; None omits uncertain painted provenance.
    """
    data = {
        "version": VERSION,
        "event": event,
        "recorded": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "parts": parts,
    }
    if painted is not None:
        data["painted"] = painted
    return data


def write(directory: Path, data: dict, files: dict[str, str]) -> None:
    """Store a fingerprint bound to the exact mod files it describes.

    Args:
        directory: Mod folder receiving built-against.json beside its bundles.
        data: Fingerprint document containing the official baseline and any painted layouts.
        files: Mod bundle filenames mapped to their SHA-1 digests.
    """
    atomic_json(directory / FILE_NAME, {**data, "files": dict(sorted(files.items()))})


def read(directory: Path, files: dict[str, str]) -> dict | None:
    """Read a supported fingerprint only when it describes the exact mod files supplied.

    Args:
        directory: Mod folder containing built-against.json.
        files: Current mod bundle filenames mapped to SHA-1 digests for exact comparison.

    Returns:
        The fingerprint, or None if missing, unreadable, malformed, unsupported, or bound to different bytes.
    """
    path = directory / FILE_NAME
    try:
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != VERSION
        or data.get("files") != files
        or not isinstance(data.get("parts"), list)
    ):
        return None
    known = data.get("painted")
    if known is not None and (
        not isinstance(known, dict) or any(not isinstance(entry, dict) for entry in known.values())
    ):
        return None
    for item in data["parts"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or any(not isinstance(item.get(kind), dict) for kind in ("atlases", "skeletons", "textures"))
        ):
            return None
    return data


def diff(before: dict, after: dict) -> dict:
    """Compare two part fingerprints as plain data, omitting unchanged assets.

    The result contains ``structure`` (None when either is unknown), and differences
    by atlas, skeleton, and texture name. Unchanged assets are left out; an empty
    result but for ``structure: False`` means the part is the one the mod was built against.

    An atlas: ``layout`` changed, regions ``added`` and ``removed``. A skeleton: ``changed`` bytes, ``rig`` changed
    (None when either is unknown), animations ``added`` and ``removed``. A texture: its sizes ``before`` and
    ``after``. An asset only one side has is reported with ``present`` naming that side.

    Args:
        before: Recorded part fingerprint serving as the mod's baseline.
        after: Current official part fingerprint to compare with the baseline.
    """

    def names(value: dict, key: str) -> set[str]:
        return set(value.get(key) or ())

    def unknown_or(old, new):
        return None if old is None or new is None else old != new

    result: dict[str, Any] = {
        "structure": unknown_or(before.get("structure"), after.get("structure")),
        "atlases": {},
        "skeletons": {},
        "textures": {},
    }
    for kind in ("atlases", "skeletons", "textures"):
        old, new = before[kind], after[kind]
        for name in sorted(old.keys() | new.keys()):
            if name not in old or name not in new:
                result[kind][name] = {"present": "before" if name in old else "after"}
                continue
            a, b = old[name], new[name]
            if a == b or (
                kind == "atlases" and a["layout"] == b["layout"] and names(a, "regions") == names(b, "regions")
            ):
                # Atlas text supports remapping; layout and region hashes determine equality.
                continue
            if kind == "atlases":
                result[kind][name] = {
                    "layout": a["layout"] != b["layout"],
                    "added": sorted(names(b, "regions") - names(a, "regions")),
                    "removed": sorted(names(a, "regions") - names(b, "regions")),
                }
            elif kind == "skeletons":
                result[kind][name] = {
                    "changed": a["sha1"] != b["sha1"],
                    "rig": unknown_or(a["rig"], b["rig"]),
                    "added": sorted(names(b, "animations") - names(a, "animations")),
                    "removed": sorted(names(a, "animations") - names(b, "animations")),
                }
            else:
                result[kind][name] = {"before": a, "after": b}
    return result


#: what a game update did to the official parts a mod was built against (``impact``), from the least to the most
#: that needs the user: the same files; rebuilt with the rig the mod was made for; new official parts beside those the
#: mod knows; the rig itself changed (atlas layout or regions, skeleton bones, slots or animations, texture sizes, an
#: asset gone); no official part of the core left. ``unknown``: nothing tells (no fingerprint, unreadable parts).
UNCHANGED, REBUILT, NEW_PARTS, RIG_CHANGED, GONE, UNKNOWN = (
    "unchanged",
    "rebuilt",
    "new_parts",
    "rig_changed",
    "gone",
    "unknown",
)
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


def changes(built: dict | None, now: dict | None) -> dict[str, Any]:
    """Summarize changes to the official assets underlying a mod.

    The summary names an atlas ``repacked`` (other places or regions), ``animations`` added (count),
    the ``rig`` itself (bones, slots,
    animations removed, or a skeleton that cannot be read), ``textures`` of another size, assets ``removed`` and
    ``added``. Only what is true is kept; ``{"gone": True}`` when the core has no official part left, ``{}`` when
    nothing tells.

    Args:
        built: Recorded baseline fingerprint; None or no recorded parts provides no evidence.
        now: Current official fingerprint, or None when the core has no official parts left.
    """
    if not built or not built.get("parts"):
        return {}
    if now is None:
        return {"gone": True}
    if not now.get("parts"):
        return {}
    found = diff(_merged(built["parts"]), _merged(now["parts"]))
    atlases, skeletons, textures = found["atlases"].values(), found["skeletons"].values(), found["textures"].values()
    every = [*atlases, *skeletons, *textures]
    result: dict[str, Any] = {
        "repacked": any("present" not in c and (c["layout"] or c["added"] or c["removed"]) for c in atlases),
        "animations": sum(len(c.get("added") or ()) for c in skeletons if "present" not in c),
        "rig": any(
            "present" not in c and (c["rig"] or c["removed"] or (c["changed"] and c["rig"] is None)) for c in skeletons
        ),
        "textures": any("present" not in c for c in textures),
        "removed": any(c.get("present") == "before" for c in every),
        "added": any(c.get("present") == "after" for c in every),
    }
    return {key: value for key, value in result.items() if value}


def impact(built: dict | None, now: dict | None) -> str:
    """Classify a game update's impact on a mod using IMPACTS.

    Parts are matched by the assets they hold, so a part renamed by the update compares with the one
    it replaced. A fingerprint that does not know every part of the core then (``complete`` False) tells no new
    parts.

    Args:
        built: Baseline fingerprint of the mod's official parts; incomplete baselines cannot prove new parts.
        now: Installed core's official fingerprint; None classifies the core as gone.

    Returns:
        The impact category, including unknown when either baseline lacks usable parts.
    """
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
    added = any(
        change.get("present") == "after"
        for kind in ("atlases", "skeletons", "textures")
        for change in changes[kind].values()
    )
    if complete and (added or len(after) > len(before)):
        return NEW_PARTS
    return REBUILT
