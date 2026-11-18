"""Schemas and reversible translations for the data generation boundary."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import toml

Document = dict[str, Any]
RESOURCE_TYPES = {"spr": "portrait", "lobby": "memorialLobby", "model": "chibi", "other": "other"}
ZOOM_MODES = {"art": "fullArt", "game": "gameView", "unlocked": "unlocked", "custom": "custom"}
IMPORT_OPTIONS = {
    "output": "outputFolder",
    "crc": "crcCorrection",
    "backup": "createBackup",
    "strategy": "matchStrategy",
    "workers": "threads",
    "types": "assetTypes",
}
NAMESPACE = ".bmpt-upgrade"
GENERATION = 2


class InvalidStore(ValueError):
    """A document cannot be interpreted safely in its declared format."""


class FutureFormat(InvalidStore):
    """A newer document must never be treated as a corrupt primary."""


class _Number(float):
    def __new__(cls, token: str) -> _Number:
        value = super().__new__(cls, token)
        value.token = token
        return value

    def __reduce__(self):
        return _Number, (self.token,)


def _object(pairs: list[tuple[str, Any]]) -> Document:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidStore(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(token: str) -> None:
    raise InvalidStore(f"Invalid JSON number {token}")


def parse_json(raw: bytes | str) -> Document:
    """Read a JSON object with unique keys and lossless opaque decimal values."""
    value = json.loads(raw, object_pairs_hook=_object, parse_float=_Number, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise InvalidStore("Expected a JSON object")
    return value


def _encode(value: Any) -> Iterator[str]:
    if isinstance(value, _Number):
        yield value.token
    elif isinstance(value, dict):
        yield "{"
        for index, (key, member) in enumerate(value.items()):
            if index:
                yield ","
            yield json.dumps(key)
            yield ":"
            yield from _encode(member)
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        for index, member in enumerate(value):
            if index:
                yield ","
            yield from _encode(member)
        yield "]"
    else:
        yield json.dumps(value, ensure_ascii=True, allow_nan=False)


def json_bytes(document: Document) -> bytes:
    """Serialize a document without rounding decimals in opaque records."""
    return ("".join(_encode(document)) + "\n").encode("ascii")


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, _Number) or isinstance(right, _Number):
        if type(left) not in (int, float, _Number) or type(right) not in (int, float, _Number):
            return False
        return Decimal(getattr(left, "token", str(left))) == Decimal(getattr(right, "token", str(right)))
    return type(left) is type(right) and left == right


def documents_equal(left: Document, right: Document) -> bool:
    """Compare two parsed documents, including the exact values of opaque numbers."""
    return _equal(left, right)


def source_value(value: Any, *, forward: bool = True) -> Any:
    """Translate only the typed student-alt source prefix."""
    old, new = ("variant:", "alt:") if forward else ("alt:", "variant:")
    return new + value[len(old) :] if isinstance(value, str) and value.startswith(old) else value


def _objects(value: Any) -> Iterator[Document]:
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    yield from (item for item in items if isinstance(item, dict))


def _member(node: Any, name: str) -> Document:
    value = node.get(name) if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class _Names:
    forward: bool

    def key(self, old: str, new: str) -> str:
        return new if self.forward else old

    def move(self, node: Document, old: str, new: str, convert: Callable[[Any], Any] = lambda value: value) -> None:
        if not self.forward:
            old, new = new, old
        if old in node:
            converted = convert(node[old])
            if new in node and not _equal(node[new], converted):
                raise InvalidStore(f"Conflicting fields {old!r} and {new!r}")
            del node[old]
            node[new] = converted

    def enum(self, values: dict[str, str], value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if not self.forward:
            values = {new: old for old, new in values.items()}
        return values.get(value, value)

    def sources(self, node: Document, *keys: str) -> None:
        for key in keys:
            if key in node:
                node[key] = source_value(node[key], forward=self.forward)

    def source_fields(self, data: Any) -> None:
        for node in _objects(data):
            self.sources(node, "source", "applied")


def _zoom(names: _Names, prefs: Document) -> None:
    old, new = "lobby_zoom", "memorial_lobby_zoom_out_mode"
    if names.forward:
        legacy = prefs.pop("lobby_zoom_out", None)
        if old in prefs or legacy is not None:
            mode = prefs.get(old)
            if not isinstance(mode, str) or mode not in ZOOM_MODES:
                prefs[old] = "game" if legacy is False else "art"
    names.move(prefs, old, new, lambda value: names.enum(ZOOM_MODES, value))
    old, new = "lobby_zoom_custom", "memorial_lobby_zoom_out_custom_limit"
    if names.forward and old in prefs:
        value = prefs[old]
        prefs[old] = (
            round(min(4.0, max(1.0, float(value))), 1)
            if type(value) in (int, float, _Number) and math.isfinite(value)
            else 2.0
        )
    names.move(prefs, old, new)
    for old, new in (
        ("lobby_post", "memorial_lobby_post_processing"),
        ("lobby_intro", "memorial_lobby_intro"),
    ):
        names.move(prefs, old, new)


def _hall(names: _Names, hall: Document) -> None:
    if "mode" in hall:
        hall["mode"] = names.enum({"memorial": "memorialLobby"}, hall["mode"])
    names.move(hall, "memorials", "memorialLobbies")
    for group in ("portraits", names.key("memorials", "memorialLobbies")):
        for entry in _objects(hall.get(group)):
            names.move(entry, "variant", "alt")
            names.move(entry, "mirror", "flipHorizontal")
            names.sources(entry, "source")


def _viewed(value: Any, *, forward: bool) -> Any:
    if isinstance(value, str):
        alt, sep, source = value.partition(":")
        return alt + sep + source_value(source, forward=forward)
    return value


def _ui(names: _Names, data: Document) -> None:
    for install in _objects(data.get("asset_previews")):
        for entries in _objects(install):
            for old, new in RESOURCE_TYPES.items():
                if old != new:
                    names.move(entries, old, new)
            names.sources(entries, *(RESOURCE_TYPES.values() if names.forward else RESOURCE_TYPES))
    prefs = _member(data, "prefs")
    _zoom(names, prefs)
    names.move(prefs, "roster", "student_list")
    student_list = _member(prefs, names.key("roster", "student_list"))
    names.move(student_list, "merged", "group_alts")
    names.move(student_list, "recruit", "releaseStatus")
    filters = _member(student_list, "filters")
    names.move(filters, "lobby", "memorialLobby")
    names.move(filters, "model", "chibi")
    _hall(names, _member(prefs, "student_hall"))
    names.move(prefs, "hall_viewed", "student_hall_viewed")
    viewed_key = names.key("hall_viewed", "student_hall_viewed")
    if isinstance(prefs.get(viewed_key), list):
        prefs[viewed_key] = [_viewed(entry, forward=names.forward) for entry in prefs[viewed_key]]
    batch = _member(prefs, "batch_import")
    for old, new in IMPORT_OPTIONS.items():
        options = {"cont_name_type": "containerNameType"} if old == "strategy" else {}
        names.move(batch, old, new, lambda value: names.enum(options, value))


def _mod(names: _Names, record: Document) -> None:
    if names.forward:
        target = record.get("target")
        field = "generic_target" if isinstance(target, str) and target.startswith("other:") else "target_alt"
    else:
        if "generic_target" in record and "target_alt" in record:
            raise InvalidStore("A Mod has two target identities")
        field = "generic_target" if "generic_target" in record else "target_alt"
    names.move(record, "target", field)
    for old, new in (
        ("label", "name"),
        ("assignment", "assigned_alt"),
        ("repaired_from", "repair_source_id"),
        ("repaired_id", "repaired_copy_id"),
        ("label_base", "base_name"),
    ):
        names.move(record, old, new)
    names.move(record, "kind", "resource_type", lambda value: names.enum(RESOURCE_TYPES, value))
    names.sources(record, "optimized_from")
    names.move(record, "lobby_camera", "memorial_lobby_camera")
    camera = _member(record, names.key("lobby_camera", "memorial_lobby_camera"))
    names.move(camera, "variant", "alt")
    names.move(camera, "slot", "configuration_entry")
    names.sources(camera, "source", "base")
    names.move(record, "roster_photography", "roster_thumbnails")
    recipe = _member(record, names.key("roster_photography", "roster_thumbnails"))
    for entry in _objects(recipe.get("choices")):
        names.move(entry, "variant", "alt")
        names.move(entry, "photo", "thumbnail")


def _state(names: _Names, data: Document) -> None:
    for record in _objects(data.get("mods")):
        _mod(names, record)
    names.source_fields(data.get("applied"))


def _choice(names: _Names, record: Document) -> None:
    names.move(record, "variant", "alt")
    names.move(record, "kind", "resource_type", lambda value: names.enum(RESOURCE_TYPES, value))
    names.move(record, "photo_defaults", "thumbnail_defaults")
    names.sources(record, "source", "applied")


def _profiles(names: _Names, data: Document) -> None:
    for folder in _objects(data.get("folders")):
        for choice in _objects(folder.get("configured")):
            _choice(names, choice)
        for built in _objects(folder.get("built")):
            _choice(names, built)
    for entries in _objects(_member(data, "troubleshoot").get("folders")):
        for choice in _objects(entries):
            _choice(names, choice)


def _capture(names: _Names, record: Document) -> None:
    names.move(record, "variant", "alt")
    if "mode" in record:
        record["mode"] = names.enum({"lobby": "memorialLobby"}, record["mode"])
    names.sources(record, "source")


def _thumbnails(names: _Names, data: Document) -> None:
    names.move(data, "photos", "thumbnails")
    names.move(data, "next_by_variant", "next_by_alt")
    for record in _objects(data.get(names.key("photos", "thumbnails"))):
        _capture(names, record)


def _official_checks(names: _Names, data: Document) -> None:
    names.move(data, "originals", "officials")


def _write(names: _Names, data: Document) -> None:
    names.source_fields(data.get("old_applied"))


def _import(names: _Names, data: Document) -> None:
    if data.get("operation", "import") == "import":
        _mod(names, _member(data, "record"))
        names.move(data, "repaired_from", "repair_source_id")
        names.move(data, "old_repaired_id", "old_repaired_copy_id")


def _deletion(names: _Names, data: Document) -> None:
    for record in _objects(data.get("records")):
        _mod(names, record)
    names.source_fields(data.get("applied"))
    profile = data.get("profiles")
    if isinstance(profile, dict):
        data["profiles"] = _convert(STORES["P"], profile, forward=names.forward)
    names.move(data, "photo", "thumbnail")
    snapshot = _member(data, names.key("photo", "thumbnail"))
    names.move(snapshot, "variant", "alt")
    _capture(names, _member(snapshot, "entry"))


@dataclass(frozen=True)
class Store:
    """The ownership, location and codec of one data class."""

    owner: Literal["data", "library", "cache", "runtime", "artifact"]
    legacy: str
    current: str
    convert: Callable[[_Names, Document], None] | None = None
    version: int | None = None
    previous: bool = False
    tree: bool = False
    policy: Literal["translate", "preserve", "invalidate"] = "translate"


STORES: dict[str, Store] = {
    "C": Store("data", "config.toml", "config.toml", policy="preserve"),
    "U": Store("data", "webui.json", "webui.json", _ui, previous=True),
    "S": Store("library", "state.json", "state.json", _state, 2, previous=True),
    "P": Store("library", "profiles.json", "profiles.json", _profiles, 2, previous=True),
    "F": Store("library", "photographs/index.json", "thumbnails/index.json", _thumbnails, 2),
    "B": Store("library", "mods", "mods", tree=True, policy="preserve"),
    "BO": Store("library", "originals", "originals", tree=True, policy="preserve"),
    "BB": Store("library", "built", "built", tree=True, policy="preserve"),
    "BF": Store("library", "photographs/images", "thumbnails/images", tree=True, policy="preserve"),
    "R": Store("library", "rigs", "rigs", version=1, tree=True, policy="preserve"),
    "A": Store("library", "mods/**/built-against.json", "mods/**/built-against.json", version=1, policy="preserve"),
    "O": Store("library", "originals.json", "originals.json", _official_checks, 2),
    "T": Store("library", "transactions", "transactions", _write, 2, tree=True),
    "I": Store("library", "import-transactions", "import-transactions", _import, 2, tree=True),
    "D": Store("library", "asset-deletions", "asset-deletions", _deletion, 2, tree=True),
    "K": Store("cache", "cache", "cache-bmpt-g2", policy="invalidate"),
    "V": Store("cache", "costume-verdicts.json", "costume-verdicts.json", policy="invalidate"),
    "W": Store("cache", "worn-items.json", "worn-items.json", policy="invalidate"),
    "Q": Store("cache", "costume-poses", "costume-poses", policy="invalidate"),
    "X": Store("runtime", "instance.json", "instance.json", policy="invalidate"),
    "Y": Store("artifact", "output", "output", policy="preserve"),
    "Z": Store("runtime", "webview", "webview", policy="invalidate"),
}


def _require(document: Document, key: str, expected: type, *, optional: bool = False) -> None:
    if key not in document and optional:
        return
    if type(document.get(key)) is not expected:
        raise InvalidStore(f"Invalid {key!r} field")


def _check(store_id: str, data: Document, generation: int) -> None:
    store = STORES[store_id]
    if store.version is not None:
        version = min(store.version, generation)
        actual = data.get("version")
        if isinstance(actual, (int, float)) and actual > version:
            raise FutureFormat(f"{store_id} version {actual} exceeds {version}")
        if type(actual) is not int or actual != version:
            raise InvalidStore(f"{store_id} requires document version {version}")
    if store_id == "U":
        for field in ("prefs", "aliases", "asset_previews", "tours"):
            _require(data, field, dict, optional=True)
    elif store_id == "S":
        for field, expected in (("mods", list), ("favorites", list), ("applied", dict), ("migrations", list)):
            _require(data, field, expected, optional=True)
        if any(
            not isinstance(record, dict)
            or not isinstance(record.get("source"), str)
            or not isinstance(record.get("sha1"), str)
            for record in data.get("applied", {}).values()
        ):
            raise InvalidStore("Invalid applied entry")
    elif store_id == "P":
        _require(data, "enabled", bool, optional=True)
        _require(data, "folders", dict, optional=True)
        _require(data, "unfinished", list, optional=True)
        for row in data.get("unfinished", []):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("install"), str)
                or not isinstance(row.get("core"), str)
                or row.get("why") not in ("pending", "left", "stuck", "held")
            ):
                raise InvalidStore("Invalid unfinished profile entry")
        for folder in data.get("folders", {}).values():
            if not isinstance(folder, dict):
                raise InvalidStore("Invalid profile folder")
            for key in ("configured", "built", "named_by", "updated", "dismissed"):
                _require(folder, key, dict, optional=True)
            _require(folder, "hidden", list, optional=True)
            if any(
                not isinstance(entry, dict) or not isinstance(entry.get("source"), str)
                for entry in folder.get("configured", {}).values()
            ):
                raise InvalidStore("Invalid configured entry")
    elif store_id == "F":
        _require(data, "photos" if generation == 1 else "thumbnails", dict)
        _require(data, "selected", dict)
        _require(data, "next", int)
        counters = data.get("next_by_variant" if generation == 1 else "next_by_alt", {})
        if (
            data["next"] < 1
            or not isinstance(counters, dict)
            or any(type(count) is not int or count < 1 for count in counters.values())
        ):
            raise InvalidStore("Invalid thumbnail counters")
    elif store_id == "O":
        _require(data, "originals" if generation == 1 else "officials", dict)
        if generation == GENERATION and "digests" in data:
            _require(data, "digests", dict)
            if any(
                not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
                for digest in data["digests"].values()
            ):
                raise InvalidStore("Invalid kept official digest")
    elif store_id == "T":
        if data.get("phase") not in ("preparing", "writing", "committed"):
            raise InvalidStore("Invalid game-write phase")
        for field, expected in (("pin", dict), ("old_applied", dict), ("files", list), ("kept", list)):
            _require(data, field, expected)
    elif store_id == "I":
        operation = data.get("operation", "import")
        phases = {"import": ("preparing", "publishing", "rollback"), "update": ("copying", "replacing")}
        if not isinstance(operation, str) or operation not in phases or data.get("phase") not in phases[operation]:
            raise InvalidStore("Invalid import journal operation or phase")
        _require(data, "id", str)
        for field in ("record", "hashes") if operation == "import" else ("old", "new"):
            _require(data, field, dict)
        if operation == "update":
            _require(data, "previous", str)
    elif store_id == "D":
        if data.get("phase") not in ("active", "committed"):
            raise InvalidStore("Invalid deletion phase")
        for field, expected in (("records", dict), ("applied", dict), ("mods", list), ("built", list), ("games", list)):
            _require(data, field, expected)
        if data.get("profiles") is not None:
            _require(data, "profiles", dict)
            _check("P", data["profiles"], generation)
        _require(data, "held", list, optional=True)


def load_document(store_id: str, path: Path, generation: int = GENERATION) -> Document:
    """Load an authoritative document with the selected generation's strict reader."""
    content = path.read_bytes()
    if store_id == "C":
        return toml.loads(content.decode("utf-8"))
    data = parse_json(content)
    _check(store_id, data, generation)
    return data


def _convert(store: Store, data: Document, *, forward: bool) -> Document:
    result = copy.deepcopy(data)
    if store.convert is not None:
        store.convert(_Names(forward), result)
    if store.version == GENERATION:
        result["version"] = GENERATION if forward else 1
    return result


def _difference(current: Any, original: Any, path: tuple[str | int, ...] = ()) -> Iterator[Document]:
    if isinstance(current, dict) and isinstance(original, dict):
        for key in current.keys() - original.keys():
            yield {"path": [*path, key], "absent": True}
        for key in original:
            if key in current:
                yield from _difference(current[key], original[key], (*path, key))
            else:
                yield {"path": [*path, key], "value": original[key]}
    elif isinstance(current, list) and isinstance(original, list) and len(current) == len(original):
        for index, (left, right) in enumerate(zip(current, original)):
            yield from _difference(left, right, (*path, index))
    elif not _equal(current, original):
        yield {"path": list(path), "value": original}


def translate(store_id: str, data: Document) -> tuple[Document, list[Document]]:
    """Translate a legacy document and record optional presence and lossy normalizations for its inverse."""
    _check(store_id, data, 1)
    translated = _convert(STORES[store_id], data, forward=True)
    _check(store_id, translated, GENERATION)
    inverse = _convert(STORES[store_id], translated, forward=False)
    return translated, list(_difference(inverse, data))


def inverse_translate(store_id: str, current: Document, shape: list[Document] | None = None) -> Document:
    """Reconstruct the legacy document using its inverse shape when available."""
    _check(store_id, current, GENERATION)
    restored = _convert(STORES[store_id], current, forward=False)
    for edit in shape or ():
        *parents, key = edit["path"]
        node = restored
        for parent in parents:
            node = node[parent]
        if edit.get("absent"):
            del node[key]
        else:
            node[key] = edit["value"]
    return restored


def managed_document(store_id: str, path: Path) -> Document:
    """Decode a current stored document to the library's internal representation."""
    return inverse_translate(store_id, load_document(store_id, path))


def managed_bytes(store_id: str, document: Document) -> bytes:
    """Encode the library's internal representation in the current stored format."""
    translated, _ = translate(store_id, document)
    return json_bytes(translated)


def count_records(store_id: str, document: Document, *, generation: int = GENERATION) -> dict[str, int]:
    """Return comparable record counts for a parsed current document."""
    if store_id == "S":
        return {key: len(document.get(key, ())) for key in ("mods", "favorites", "applied")}
    if store_id == "P":
        folders = document.get("folders", {})
        return {
            "installs": len(folders),
            "configured": sum(len(folder.get("configured", {})) for folder in folders.values()),
            "unfinished": len(document.get("unfinished", [])),
        }
    if store_id == "F":
        items = "photos" if generation == 1 else "thumbnails"
        counters = "next_by_variant" if generation == 1 else "next_by_alt"
        return {
            "thumbnails": len(document.get(items, {})),
            "selected": len(document.get("selected", {})),
            "next_by_alt": len(document.get(counters, {})),
        }
    if store_id == "U":
        hall = _member(_member(document, "prefs"), "student_hall")
        memorials = "memorials" if generation == 1 else "memorialLobbies"
        return {"portraits": len(hall.get("portraits", [])), "memorialLobbies": len(hall.get(memorials, []))}

    return {"documents": 1}


def epoch_root(path: Path, owner: str) -> Path | None:
    """Find the enclosing generation directory for an explicit managed path."""
    for candidate in (path, *path.parents):
        if (
            candidate.parent.name == f"g{GENERATION}"
            and candidate.parent.parent.name == owner
            and candidate.parent.parent.parent.name == NAMESPACE
        ):
            return candidate
    return None


def runtime_store(path: Path) -> str | None:
    """Identify an authoritative path passed to a live persistence helper."""
    data = epoch_root(path, "data")
    library = epoch_root(path, "library")
    if data is not None and path.parent == data and path.name in ("webui.json", "webui.json.prev"):
        return "U"
    if library is None:
        return None
    relative = path.relative_to(library)
    if len(relative.parts) == 3 and path.name == "journal.json":
        return {"transactions": "T", "import-transactions": "I", "asset-deletions": "D"}.get(relative.parts[0])
    if len(relative.parts) == 1:
        return {
            "state.json": "S",
            "state.json.prev": "S",
            "profiles.json": "P",
            "profiles.json.prev": "P",
            "originals.json": "O",
        }.get(path.name)
    if relative.as_posix() == "thumbnails/index.json":
        return "F"
    return None


def read_runtime(path: Path) -> Document:
    """Read live storage through the committed generation codec when applicable."""
    store = runtime_store(path)
    return managed_document(store, path) if store else json.loads(path.read_text(encoding="utf-8"))
