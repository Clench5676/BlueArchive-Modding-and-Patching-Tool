"""Neutral on-disk format manifests and the version fence used during discovery."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from uuid import UUID

from . import upgrade_files as files
from .store_formats import GENERATION, NAMESPACE, STORES, Document, FutureFormat, InvalidStore, parse_json

DATA_MANIFEST = "data.json"
LIBRARY_MANIFEST = "library.json"
FORMAT_NAMES = {"data": "app_data", "library": "mod_library"}
FORMAT_VERSION = f"{GENERATION}.0"
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_NAMES = {
    "C": "config",
    "U": "preferences",
    "S": "state",
    "P": "profiles",
    "F": "thumbnails",
    "B": "mods",
    "BO": "originals",
    "BB": "built",
    "BF": "thumbnail_images",
    "R": "rigs",
    "A": "build_fingerprints",
    "O": "official_checks",
    "T": "transactions",
    "I": "import_transactions",
    "D": "asset_deletions",
}
_COMMON = {"libraryId": "library_id", "epoch": "active_epoch"}
_DATA = {**_COMMON, "dataId": "data_id", "token": "route_token"}
_LIBRARY = {**_COMMON, "members": "memberships"}
_MEMBER = {"epoch": "data_epoch", "legacySetting": "legacy_setting"}
_BACKUP = {"dataId": "data_id", "dataEpoch": "data_epoch", "libraryEpoch": "library_epoch"}


class NewerManifest(FutureFormat):
    """A manifest needs a reader beyond the supported format."""

    def __init__(self, path: Path, owner: str) -> None:
        """Record which directory has advanced beyond this reader."""
        super().__init__(f"Unsupported format version in {path}")
        self.owner = owner


def _version(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not (match := _VERSION.fullmatch(value)):
        raise InvalidStore("Format versions must be major.minor strings")
    return int(match[1]), int(match[2])


_EXTENSIONS = "_manifest_extensions"


def _map(record: Document, names: dict[str, str], *, encode: bool) -> Document:
    if encode:
        result = dict(record.get(_EXTENSIONS, {}))
        for key, item in record.items():
            if key != _EXTENSIONS:
                result[names.get(key, key)] = item
        return result
    aliases = {new: old for old, new in names.items()}
    extensions = {key: item for key, item in record.items() if key in names or key == _EXTENSIONS}
    result = {aliases.get(key, key): item for key, item in record.items() if key not in extensions}
    if extensions:
        result[_EXTENSIONS] = extensions
    return result


def _uuid(value: object, *, encode: bool) -> str:
    if not isinstance(value, str):
        raise InvalidStore("Missing format identity")
    try:
        identity = UUID(value)
    except ValueError as error:
        raise InvalidStore("Invalid format identity") from error
    return str(identity) if encode else identity.hex


def _library_detail(result: Document, *, encode: bool) -> None:
    members = "memberships" if encode else "members"
    if not isinstance(result.get(members), dict) or not isinstance(result.get("backups"), list):
        raise InvalidStore("Invalid format memberships or backups")
    result[members] = {
        _uuid(key, encode=encode): _map(member, _MEMBER, encode=encode) for key, member in result[members].items()
    }
    for key in ("backups", "locations"):
        if key not in result:
            continue
        for entry in result[key]:
            if key == "backups":
                name = "dataId" if encode else "data_id"
                entry[name] = _uuid(entry[name], encode=encode)
            elif "route" in entry:
                route = entry["route"]
                for name in ("libraryId", "dataId") if encode else ("library_id", "data_id"):
                    route[name] = _uuid(route[name], encode=encode)
                entry["route"] = _map(route, _DATA, encode=encode)
                entry["route"]["format"] = FORMAT_NAMES["data"] if encode else GENERATION
        if key == "backups":
            result[key] = [_map(entry, _BACKUP, encode=encode) for entry in result[key]]
    if "prerequisite" in result:
        entry = result["prerequisite"]
        name = "libraryId" if encode else "library_id"
        entry[name] = _uuid(entry[name], encode=encode)
        result["prerequisite"] = _map(entry, {"libraryId": "library_id"}, encode=encode)


def store_descriptors(owner: str, epoch: str) -> Document:
    """Describe the authoritative stores at an active epoch without embedding their schemas."""
    if not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{32}", epoch):
        raise InvalidStore("Invalid active epoch in format manifest")
    prefix = f"{NAMESPACE}/{owner}/g{GENERATION}/{epoch}"
    return {
        name: {
            "path": f"{prefix}/{STORES[key].current}",
            "version": f"{STORES[key].version or (1 if key == 'C' else GENERATION)}.0",
        }
        for key, name in _NAMES.items()
        if STORES[key].owner == owner
    }


def read_manifest(root: Path, owner: str) -> Document | None:
    """Read and fence the neutral manifest before accessing any store."""
    path = root / (DATA_MANIFEST if owner == "data" else LIBRARY_MANIFEST)
    if not files.present(path):
        return None
    raw = parse_json(path.read_bytes())
    if raw.get("format") != FORMAT_NAMES[owner]:
        raise InvalidStore(f"Unexpected format name in {path}")
    version = _version(raw.get("format_version"))
    required = _version(raw.get("min_reader_version"))
    if version[0] > GENERATION or required > _version(FORMAT_VERSION):
        raise NewerManifest(path, owner)
    if version[0] != GENERATION or required > version:
        raise InvalidStore(f"Invalid format version in {path}")
    written_by = raw.get("written_by")
    if not isinstance(written_by, dict) or not all(isinstance(written_by.get(key), str) for key in ("name", "version")):
        raise InvalidStore(f"Invalid writer metadata in {path}")
    declared = raw.get("stores")
    if not isinstance(declared, dict):
        raise InvalidStore(f"Missing store manifest in {path}")
    expected = store_descriptors(owner, raw.get("active_epoch"))
    for name, fields in expected.items():
        entry = declared.get(name)
        if not isinstance(entry, dict) or entry.get("path") != fields["path"]:
            raise InvalidStore(f"Invalid {name} store location in {path}")
        found, supported = _version(entry.get("version")), _version(fields["version"])
        if found[0] > supported[0]:
            raise NewerManifest(path, owner)
        if found[0] != supported[0]:
            raise InvalidStore(f"Invalid {name} store version in {path}")
    result = _map(raw, _DATA if owner == "data" else _LIBRARY, encode=False)
    result["format"] = GENERATION
    result["libraryId"] = _uuid(result.get("libraryId"), encode=False)
    if owner == "data":
        result["dataId"] = _uuid(result.get("dataId"), encode=False)
    else:
        _library_detail(result, encode=False)
    return result


def encode_manifest(owner: str, record: Document) -> Document:
    """Write the active format version while retaining compatible manifest extensions."""
    from .updater import current_version

    result = _map(copy.deepcopy(record), _DATA if owner == "data" else _LIBRARY, encode=True)
    result["format"] = FORMAT_NAMES[owner]
    result.setdefault("format_version", FORMAT_VERSION)
    result.setdefault("min_reader_version", FORMAT_VERSION)
    result["written_by"] = {**result.get("written_by", {}), "name": "BMPT", "version": current_version()}
    result["library_id"] = _uuid(result.get("library_id"), encode=True)
    stores = result.setdefault("stores", {})
    for name, fields in store_descriptors(owner, result["active_epoch"]).items():
        stores[name] = {**fields, **stores.get(name, {}), "path": fields["path"]}
    if owner == "data":
        result["data_id"] = _uuid(result.get("data_id"), encode=True)
    else:
        _library_detail(result, encode=True)
    return result
