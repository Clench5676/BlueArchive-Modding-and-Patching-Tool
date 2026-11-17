"""``ba-mod.json``: the small metadata file a mod package carries next to its files.

The format is a proposal under discussion with other mod tools, written here as far as it is settled: one file per mod,
``format_version`` ``major.minor``, the tool that wrote it, the mod's ``kind`` and ``core``, the ``target`` it was built
for (server, platform and the release marks the install shows), every file with its SHA-256 and ``role`` (``mod``:
changed by the author; ``official``: an unchanged official file carried along; ``generated``: made by a tool), and for
Spine kinds which parts of each skeleton are the author's.

Only what the files cannot tell goes in. A reader trusts the file only when every listed file is there with the hash it
names; otherwise the file counts as absent and the package is a plain mod, exactly as without one. An unknown field is
ignored and an unknown value of a closed list counts as missing. Names read from the file never choose a path beyond
the folder that holds it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from ..engine import is_safe_relative_name

logger = logging.getLogger(__name__)

FILE_NAME = "ba-mod.json"
FORMAT_VERSION = "1.0"
#: the major format version this reader understands; a file of another major counts as absent
MAJOR = 1
#: a larger file is not a manifest
MAX_BYTES = 1024 * 1024

KINDS = (
    "portrait",
    "lobby",
    "spine_scene",
    "model",
    "prop",
    "stage",
    "cafe",
    "effect",
    "cutscene",
    "ui",
    "story_art",
    "voice",
    "audio",
    "video",
    "table",
    "minigame",
    "other",
)
#: kinds that name their character or scene
CORE_KINDS = ("portrait", "lobby", "spine_scene", "model")
SPINE_KINDS = ("portrait", "lobby", "spine_scene")
SERVERS = ("global", "japan", "china")
PLATFORMS = ("windows", "android", "ios")
ROLES = ("mod", "official", "generated")
PART_ROLES = ("mod", "official")
MOD, OFFICIAL, GENERATED = ROLES

_VERSION = re.compile(r"^[0-9]+\.[0-9]+$")
_CORE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_HASH = re.compile(r"^[0-9a-f]{32}$")
_MILESTONE = re.compile(r"^r[0-9]+$")
_CATALOG_ROOT = re.compile(r"^r[0-9]+_[a-z0-9]+$")

# The bundle families of each kind, by the family word of the bundle name (``assets-_mx-<family>-...`` or
# ``<family>-..._mxload-...``), as the format's kind table lists them.
_FAMILIES = {
    "spinelobbies": "lobby",
    "spinebackground": "spine_scene",
    "characters": "model",
    "character": "model",
    "npcs": "model",
    "3dobject": "prop",
    "obstacles": "prop",
    "obstacle": "prop",
    "arms": "prop",
    "battleitem": "prop",
    "scenes": "stage",
    "academy": "stage",
    "hexamaps": "stage",
    "campaign": "stage",
    "conquest": "stage",
    "cafe": "cafe",
    "effect": "effect",
    "etceffects": "effect",
    "effectsfreezed": "effect",
    "projectile": "effect",
    "skillrange": "effect",
    "timeline": "cutscene",
    "prodresources": "cutscene",
    "sccutscene": "cutscene",
    "ui": "ui",
    "audio": "audio",
    "minigame": "minigame",
    "field": "minigame",
    "undercover": "minigame",
    "iaworldraid": "minigame",
}


def family_kind(name: str) -> str:
    """The kind of a bundle by its family word, ``other`` for a family the table does not list."""
    words = name.lower().removeprefix("assets-_mx-").split("-")
    family = words[0]
    if family == "spinecharacters":
        return "portrait"
    if family == "uis":
        return "story_art" if len(words) > 1 and words[1] == "03_scenario" else "ui"
    if family == "ui" and len(words) > 1 and words[1] == "uilobbyelement":
        return "lobby"  # a memorial lobby's prefab bundle
    return _FAMILIES.get(family, "other")


def kind_of(library_kind: str, names: Iterable[str]) -> str:
    """The format's kind of a library mod: its kind (``spr``, ``lobby``, ``model``, ``other``) and its file names.
    A library ``lobby`` is a memorial lobby or, for a ``spinebackground`` core, a Spine scene."""
    names = list(names)
    if library_kind == "spr":
        return "portrait"
    if library_kind == "model":
        return "model"
    if library_kind == "lobby":
        return "spine_scene" if any(family_kind(name) == "spine_scene" for name in names) else "lobby"
    kinds = {family_kind(name) for name in names}
    return kinds.pop() if len(kinds) == 1 else "other"


def _read_text(path: Path, limit: int = 4096) -> str | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        return None
    try:
        return data.decode("utf-8-sig").strip()
    except UnicodeError:
        return None


def _catalog(resource_dirs: Iterable[Path]) -> Path | None:
    """The ``Catalog`` folder of an install: beside or above its resource folders."""
    for directory in resource_dirs:
        for parent in (directory, *directory.parents):
            catalog = parent / "Catalog"
            if catalog.is_dir():
                return catalog
    return None


def release_of(resource_dirs: Iterable[Path], server: str, platform: str) -> dict:
    """The release marks an install shows (``target.release``), only the ones it can read in their exact form.

    Global: ``bundle_revision`` (``Catalog/BundleRevision``, a positive integer that grows with every release) and
    ``catalog_hash`` (``Catalog/<Platform>/catalog_<Platform>.hash``, 32 hex digits, one per release and platform).
    The marks of Japan and China are not read yet."""
    result: dict[str, Any] = {}
    if server != "global":
        return result
    catalog = _catalog(resource_dirs)
    if catalog is None:
        return result
    revision = _read_text(catalog / "BundleRevision")
    if revision and revision.isdigit() and int(revision) > 0:
        result["bundle_revision"] = int(revision)
    folder = {"windows": "Windows", "android": "Android", "ios": "iOS"}.get(platform)
    if folder:
        value = (_read_text(catalog / folder / f"catalog_{folder}.hash") or "").lower()
        if _CATALOG_HASH.fullmatch(value):
            result["catalog_hash"] = value
    return result


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def skeleton_name(name: str) -> str:
    """A skeleton by its asset name without the file extension: ``akari_spr.skel`` -> ``akari_spr``."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".bytes", ".skel", ".json"):
        base = base.removesuffix(suffix)
    return base


def document(
    *,
    created_by: Mapping[str, str],
    kind: str,
    core: str | None,
    server: str,
    platform: str,
    release: Mapping[str, Any],
    files: list[dict],
    skeletons: list[dict],
) -> dict:
    """A manifest in the format's field order; empty optional parts are left out (never empty strings or nulls)."""
    data: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "created_by": {key: value for key, value in created_by.items() if value},
        "kind": kind if kind in KINDS else "other",
    }
    if core and _CORE.fullmatch(core):
        data["core"] = core
    target: dict[str, Any] = {"server": server, "platform": platform}
    if release:
        target["release"] = dict(release)
    data["target"] = target
    data["files"] = [{"path": item["path"], "sha256": item["sha256"], "role": item["role"]} for item in files]
    if skeletons:
        data["skeletons"] = skeletons
    return data


def dumps(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _clean_path(value: Any) -> str | None:
    """A file path of a manifest as the format allows it: ``/``-separated, below the package root."""
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return None
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or not is_safe_relative_name(value):
        return None
    return "/".join(parts)


def _release(server: str, value: Any) -> dict:
    """The release marks of a manifest in their exact form; one in another form counts as missing."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    if server == "global":
        revision = value.get("bundle_revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
            result["bundle_revision"] = revision
        catalog = value.get("catalog_hash")
        if isinstance(catalog, str) and _CATALOG_HASH.fullmatch(catalog):
            result["catalog_hash"] = catalog
    elif server == "japan":
        milestone, patch = value.get("milestone"), value.get("patch_version")
        if isinstance(milestone, str) and _MILESTONE.fullmatch(milestone):
            result["milestone"] = milestone
        if isinstance(patch, int) and not isinstance(patch, bool) and patch > 0:
            result["patch_version"] = patch
        root = value.get("catalog_root")
        if isinstance(root, str) and _CATALOG_ROOT.fullmatch(root):
            result["catalog_root"] = root
    elif server == "china":
        for key in ("resource_version", "patch_version"):
            if isinstance(value.get(key), str) and value[key]:
                result[key] = value[key]
    return result


def parse(data: bytes) -> dict | None:
    """A manifest's content as this reader takes it, None when it is no manifest this reader can use: not JSON, a
    ``format_version`` of another major, or a required field missing or malformed. Unknown fields are dropped, unknown
    values of closed lists count as missing. The files are not checked here (``read``)."""
    if len(data) > MAX_BYTES:
        return None
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("format_version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or int(version.split(".")[0]) != MAJOR:
        return None
    kind = raw.get("kind")
    target = raw.get("target")
    files = raw.get("files")
    if kind not in KINDS or not isinstance(target, dict) or not isinstance(files, list) or not files:
        return None
    server, platform = target.get("server"), target.get("platform")
    if server not in SERVERS or platform not in PLATFORMS:
        return None
    core = raw.get("core")
    if not (isinstance(core, str) and _CORE.fullmatch(core)):
        if kind in CORE_KINDS:
            return None
        core = None
    result_files, seen = [], set()
    for item in files:
        if not isinstance(item, dict):
            return None
        path = _clean_path(item.get("path"))
        digest = item.get("sha256")
        if path is None or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            return None
        key = path.casefold()
        if key in seen:
            return None
        seen.add(key)
        role = item.get("role")
        result_files.append({"path": path, "sha256": digest, "role": role if role in ROLES else None})
    skeletons = []
    listed = raw.get("skeletons")
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
            skeletons.append(
                {
                    "name": item["name"],
                    **{part: item[part] for part in ("atlas", "skel", "textures") if item.get(part) in PART_ROLES},
                }
            )
    created = raw.get("created_by") if isinstance(raw.get("created_by"), dict) else {}
    result: dict[str, Any] = {
        "format_version": version,
        "created_by": {key: created[key] for key in ("tool", "version") if isinstance(created.get(key), str)},
        "kind": kind,
        "core": core,
        "target": {"server": server, "platform": platform, "release": _release(server, target.get("release"))},
        "files": result_files,
        "skeletons": skeletons,
    }
    return result


def read(folder: Path) -> dict | None:
    """The manifest beside a mod's files in ``folder``, None when there is none or it is not to be trusted: it cannot
    be parsed (``parse``), or a file it lists is missing from the folder or has another SHA-256. Never raises."""
    path = folder / FILE_NAME
    try:
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        return None
    manifest = parse(data)
    if manifest is None:
        logger.info("Package manifest ignored: not readable")
        return None
    try:
        for item in manifest["files"]:
            file = folder.joinpath(*item["path"].split("/"))
            if not file.is_file() or sha256(file) != item["sha256"]:
                logger.info("Package manifest ignored: a listed file is missing or differs")
                return None
    except OSError:
        return None
    return manifest


def covers(manifest: Mapping[str, Any] | None, folder: Path, paths: Iterable[Path]) -> bool:
    """Whether a trusted manifest in ``folder`` lists every one of ``paths`` (the files of one import row)."""
    if not manifest:
        return False
    listed = {item["path"].casefold() for item in manifest["files"]}
    try:
        return all(path.resolve().relative_to(folder.resolve()).as_posix().casefold() in listed for path in paths)
    except (OSError, ValueError):
        return False
