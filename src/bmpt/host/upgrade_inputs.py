"""Fingerprint the legacy inputs and resolve their committed data and library locations."""

from __future__ import annotations

import copy
import hashlib
import re
import stat
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import devmode
from . import upgrade_files as files
from .data_paths import APP_DIR_NAME, OLD_APP_DIR_NAME
from .format_manifest import DATA_MANIFEST, NewerManifest, encode_manifest, read_manifest
from .store_formats import (
    GENERATION,
    NAMESPACE,
    STORES,
    Document,
    FutureFormat,
    InvalidStore,
    json_bytes,
    load_document,
    parse_json,
)

ID = re.compile(r"[0-9a-f]{32}")
_MARKER = ".data-folder-migration.json"


class UpgradeBlocked(Exception):
    """An upgrade must wait for its environment or for a user decision."""

    def __init__(self, reason: str, detail: str) -> None:
        """Record the bootstrap reason and the corresponding log detail."""
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class InputFile:
    """One verified file from a source directory."""

    store: str
    path: Path
    relative: str
    size: int
    sha256: str
    file_id: tuple[int, int]
    mtime: int

    def receipt(self) -> Document:
        """Record the source identity, timestamp and digest used to verify a stage."""
        return {
            "path": self.relative,
            "length": self.size,
            "sha256": self.sha256,
            "fileId": list(self.file_id),
            "mtime": self.mtime,
            "store": self.store,
        }


@dataclass(frozen=True)
class Snapshot:
    """The complete observed legacy input set."""

    root: Path
    items: tuple[InputFile, ...]

    @property
    def fingerprint(self) -> str:
        """Fingerprint source paths and bytes independently of their file IDs."""
        content = [(item.relative, item.size, item.sha256) for item in self.items]
        return hashlib.sha256(json_bytes({"files": content})).hexdigest()

    def item(self, relative: str) -> InputFile | None:
        """Look up a file by its path in the legacy namespace."""
        return next((item for item in self.items if item.relative == relative), None)


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise InvalidStore("Invalid upgrade identity")
    return value


def path_record(path: Path, base: Path) -> str:
    """Prefer a relative path for data kept beside its manifest."""
    return path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)


def resolve_record(value: Any, base: Path) -> Path:
    """Resolve a manifest path within the authorized read roots."""
    if not isinstance(value, str) or not value:
        raise InvalidStore("Invalid upgrade path")
    path = Path(value)
    if ".." in path.parts:
        raise InvalidStore("Upgrade path traverses a parent")
    resolved = (base / path).resolve()
    devmode.check_read(resolved)
    return resolved


def read_record(path: Path) -> Document:
    """Load a private JSON record without following a link."""
    files.owned_stat(path)
    return parse_json(path.read_bytes())


def maybe_read_record(path: Path) -> Document | None:
    """Read a private record when its file exists."""
    return read_record(path) if files.present(path) else None


def namespace(root: Path) -> Path:
    """Resolve the directory reserved for generation and runtime files."""
    path = root / NAMESPACE
    if files.present(path) and not path.is_dir():
        raise InvalidStore(f"Upgrade namespace is not a directory: {path}")
    return path


def epoch_path(root: Path, owner: str, key: str) -> Path:
    """Address one data or library epoch by validated ID."""
    return namespace(root) / owner / f"g{GENERATION}" / _identifier(key)


def read_head(library: Path) -> Document | None:
    """Read and validate the selected library manifest."""
    head = read_manifest(library, "library")
    if head is None:
        return None
    if head.get("format") != GENERATION:
        raise FutureFormat("Unsupported library head format")
    _identifier(head.get("libraryId"))
    _identifier(head.get("epoch"))
    if type(head.get("sequence")) is not int or head["sequence"] < 1:
        raise InvalidStore("Invalid library head sequence")
    if not isinstance(head.get("members"), dict) or not isinstance(head.get("backups"), list):
        raise InvalidStore("Invalid library head participants")
    for owner, member in head["members"].items():
        _identifier(owner)
        _identifier(member["token"])
        _identifier(member["epoch"])
    return head


def read_route(data: Path) -> Document | None:
    """Read and validate the data directory's library route."""
    route = read_manifest(data, "data")
    if route is not None:
        if route.get("format") != GENERATION:
            raise FutureFormat("Unsupported data route format")
        for field in ("dataId", "libraryId", "token"):
            _identifier(route.get(field))
        resolve_record(route.get("library"), data)
    return route


def write_route(data: Path, route: Document, epoch: str) -> None:
    """Durably publish the route to the active data epoch."""
    route["epoch"] = epoch
    files.write_document(data / DATA_MANIFEST, encode_manifest("data", route))


def discover_macos_data(parent: Path, *, strict: bool = True) -> Path:
    """Select a macOS data directory without migrating either default folder."""
    old, new = parent / OLD_APP_DIR_NAME, parent / APP_DIR_NAME
    routed = []
    for folder in (new, old):
        if not files.present(folder / DATA_MANIFEST):
            continue
        try:
            route = read_route(folder)
            head = read_head(resolve_record(route["library"], folder))
            if head and head["libraryId"] == route["libraryId"]:
                member = head["members"].get(route["dataId"])
                if member and member["token"] == route["token"]:
                    routed.append(folder)
        except NewerManifest:
            return folder
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if len(routed) == 1:
        return routed[0]
    if len(routed) > 1 and strict:
        raise InvalidStore("Both macOS data locations hold an active route")
    return new if files.present(new) or not files.present(old) else old


def inventory(root: Path, owner: str) -> Snapshot:
    """Hash the files covered by the legacy data or library format."""
    if not files.present(root):
        return Snapshot(root, ())
    if not root.is_dir():
        raise InvalidStore(f"Expected a directory: {root}")
    found = {}

    def add(path: Path, store_id: str) -> None:
        info = files.owned_stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise InvalidStore(f"Expected a file: {path}")
        relative = path.relative_to(root).as_posix()
        found[relative] = InputFile(
            store_id, path, relative, info.st_size, files.digest(path), (info.st_dev, info.st_ino), info.st_mtime_ns
        )

    for store_id, store in STORES.items():
        if store.owner != owner or "**" in store.legacy:
            continue
        path = root / store.legacy
        if store.tree:
            for child in files.tree_files(path, skip=frozenset(("previews",)) if store_id == "BB" else frozenset()):
                child_store = "A" if store_id == "B" and child.name == "built-against.json" else store_id
                add(child, child_store)
        else:
            if path.parent != root and not files.present(path.parent):
                continue
            if files.present(path):
                add(path, store_id)
            if store.previous and files.present(path.with_name(f"{path.name}.prev")):
                add(path.with_name(f"{path.name}.prev"), store_id)
            for bad in sorted(path.parent.glob(f"{path.name}.bad*")):
                add(bad, store_id)
    if owner == "data" and files.present(root / _MARKER):
        add(root / _MARKER, "path-migration")
    return Snapshot(root, tuple(found[key] for key in sorted(found)))


def input_document(snapshot: Snapshot, store_id: str) -> tuple[Document | None, str | None]:
    """Read an authoritative legacy document, including its recovery copy."""
    store = STORES[store_id]
    errors = []
    for path in (store.legacy, f"{store.legacy}.prev") if store.previous else (store.legacy,):
        if source := snapshot.item(path):
            try:
                return load_document(store_id, source.path, generation=1), path
            except FutureFormat:
                raise
            except ValueError as error:
                errors.append(error)
    if errors:
        raise InvalidStore(f"{store_id} has no readable primary or recovery copy: {errors[0]}") from errors[0]
    return None, None


def moved_prefix(snapshot: Snapshot) -> tuple[Path, Path] | None:
    """Identify the original data root from a pending legacy move."""
    marker = snapshot.item(_MARKER)
    if marker is None:
        return None
    record = read_record(marker.path)
    old, new = Path(record["old"]), Path(record["new"])
    if old.resolve() == snapshot.root and new.parent == old.parent and not files.present(new):
        return None
    if (
        old.name != OLD_APP_DIR_NAME
        or new.name != APP_DIR_NAME
        or old.parent != new.parent
        or not old.is_absolute()
        or new.resolve() != snapshot.root
        or files.present(old)
    ):
        raise UpgradeBlocked("recoveryPending", f"Cannot establish which data folder moved: {marker.path}")
    return old, new


def rebase(document: Document, store: str, moved: tuple[Path, Path] | None) -> Document:
    """Translate paths and keyed records from a proven legacy folder move."""
    if moved is None:
        return document
    old, new = moved

    def path_value(value: Any) -> Any:
        if isinstance(value, str):
            path = Path(value)
            if path.is_absolute() and path.is_relative_to(old):
                moved_path = new / path.relative_to(old)
                if not files.present(moved_path) and not files.present(moved_path.parent):
                    raise InvalidStore(f"Cannot validate rebased path: {value}")
                return str(moved_path)
        return value

    def rekey(node: Document, key: str) -> None:
        if isinstance(node.get(key), dict):
            entries = {path_value(name): value for name, value in node[key].items()}
            if len(entries) != len(node[key]):
                raise InvalidStore(f"Conflicting rebased {key} paths")
            node[key] = entries

    result = copy.deepcopy(document)
    if store == "C":
        for group, keys in (
            ("Directories", ("game_resource_dir", "game_resource_dir_japan", "mod_backup_path")),
            ("AppSettings", ("output_dir",)),
            ("SpineConverter", ("spine_converter_path",)),
            ("SpineViewer", ("spine_viewer_path",)),
        ):
            node = result.get(group, {})
            for key in keys:
                if key in node:
                    node[key] = path_value(node[key])
    elif store == "U":
        prefs = result.get("prefs", {})
        for key in ("library_dir", "cache_parent", "data_path"):
            if key in prefs:
                prefs[key] = path_value(prefs[key])
        rekey(result, "asset_previews")
    elif store == "S":
        rekey(result, "applied")
    elif store == "P":
        rekey(result, "folders")
        rekey(result.get("troubleshoot", {}), "folders")
        for pending in result.get("unfinished", ()):
            pending["install"] = path_value(pending["install"])
    elif store in ("T", "D"):
        if store == "T":
            rekey(result, "old_applied")
            pin = result["pin"]
            for key in ("root", "key"):
                pin[key] = path_value(pin[key])
            pin["dirs"] = [path_value(item) for item in pin["dirs"]]
            for item in result["files"]:
                item["target"] = path_value(item["target"])
        else:
            rekey(result, "applied")
            if isinstance(result.get("profiles"), dict):
                result["profiles"] = rebase(result["profiles"], "P", moved)
            for entry in result["games"]:
                entry["target"] = path_value(entry["target"])
                for key in ("root", "key"):
                    entry["pin"][key] = path_value(entry["pin"][key])
    return result


def legacy_library(snapshot: Snapshot, ui: Document | None, moved: tuple[Path, Path] | None) -> Path:
    """Resolve the library selected by the legacy preferences."""
    prefs = rebase(ui or {}, "U", moved).get("prefs", {})
    location = prefs.get("library_dir")
    if location in (None, ""):
        return snapshot.root / "library"
    library = resolve_record(location, snapshot.root)
    if not files.present(library):
        raise UpgradeBlocked("unavailable", f"Configured legacy library is unavailable: {library}")
    return library


def recovery_data_id(document: Document, data_root: Path, *, copied: bool) -> Document:
    """Preserve the data identity or issue one when a copy is attached."""
    result = copy.deepcopy(document)
    prefs = result.setdefault("prefs", {})
    key = prefs.get("data_id")
    previous = prefs.get("data_path")
    if isinstance(previous, str) and Path(previous).is_absolute() and Path(previous).resolve() != data_root:
        if files.present(Path(previous) / "webui.json"):
            source = inventory(Path(previous), "data")
            older, _ = input_document(source, "U")
            copied = copied or (older or {}).get("prefs", {}).get("data_id") == key
    if copied or not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{12}", key):
        prefs["data_id"] = uuid.uuid4().hex[:12]
    prefs["data_path"] = str(data_root)
    return result


def legacy_fences(data: Path, library: Path) -> Iterator[Path]:
    """Yield file locks shared with older writers during staging."""
    if files.present(data):
        yield data / "webui.json.lock"
    if files.present(library):
        for name in ("transactions.lock", "mods.lock", "state.json.lock", "profiles.json.lock"):
            yield library / name
        if files.present(library / "photographs"):
            yield library / "photographs" / "index.lock"
