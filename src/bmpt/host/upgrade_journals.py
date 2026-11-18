"""Read-only validation of recovery authority and its current inputs."""

from __future__ import annotations

import re
from pathlib import Path

from ..core import devmode
from .store_formats import Document, InvalidStore, load_document
from .upgrade_files import digest, present

_ID = re.compile(r"[0-9a-f]{32}")
_SHA1 = re.compile(r"[0-9a-f]{40}")


class RecoveryPending(InvalidStore):
    """Journal recovery needs evidence that is currently unavailable or inconclusive."""


def _match(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def bundle_name(value: object) -> bool:
    """Check a single portable bundle basename, with no path separators."""
    return isinstance(value, str) and bool(re.fullmatch(r"[^\\/:\x00]+\.bundle", value))


def _check_digest(path: Path, expected: str, *, optional: bool = False) -> bool:
    if not _match(_SHA1, expected):
        raise InvalidStore(f"Invalid digest for {path}")
    if not present(path):
        if optional:
            return False
        raise RecoveryPending(f"Missing recovery payload: {path}")
    if digest(path, "sha1") != expected:
        raise RecoveryPending(f"Recovery payload digest differs: {path}")
    return True


def _target(item: Document, pin: Document) -> Path:
    target = Path(item["target"])
    root = Path(pin["root"])
    if (
        not target.is_absolute()
        or not root.is_absolute()
        or not bundle_name(target.name)
        or not target.is_relative_to(root)
        or ".." in target.parts
        or root == target
        or target.resolve() != target
        or root.resolve() != root
    ):
        raise InvalidStore(f"Unsafe recovery target: {target}")
    if not devmode.allows_read(target):
        raise RecoveryPending(f"Recovery target lies outside the permitted data roots: {target}")
    return target


def _writes(root: Path, path: Path, document: Document, *, check_game: bool) -> None:
    if document["phase"] == "committed":
        return
    pin = document["pin"]
    if type(pin.get("dirs")) is not list:
        raise InvalidStore("Game-write journal is missing its resource folders")
    seen = set()
    for index, item in enumerate(document["files"]):
        target = _target(item, pin)
        if target in seen or not any(target.parent == Path(folder) for folder in pin["dirs"]):
            raise InvalidStore(f"Repeated or unowned recovery target: {target}")
        seen.add(target)
        original = root / "originals" / target.name
        rollback = original if item["official"] is True else path.parent / f"{index}.bundle"
        if item["rollback"] != rollback.relative_to(root).as_posix() and (
            item["rollback"] != str(rollback.relative_to(root))
        ):
            raise InvalidStore(f"Invalid rollback path in {path}")
        restore = item.get("restore")
        if restore is not None and Path(restore) != original.relative_to(root):
            raise InvalidStore(f"Invalid restore path in {path}")
        before, after = item["before"], item["after"]
        if not (_match(_SHA1, before) and _match(_SHA1, after)):
            raise InvalidStore(f"Invalid game-write digests in {path}")
        current = digest(target, "sha1") if check_game and present(target) else None
        if check_game and current not in (before, after):
            raise RecoveryPending(f"Cannot prove the game-write recovery outcome: {target}")
        if restore and present(original):
            _check_digest(original, after)
        elif check_game and restore and current != after:
            raise RecoveryPending(f"Missing official recovery payload: {original}")
        _check_digest(rollback, before, optional=not check_game or current == before)


def _imports(root: Path, path: Path, document: Document, records: dict) -> None:
    key = document["id"]
    if not _match(_ID, key) or path.parent.name != key:
        raise InvalidStore(f"Invalid Mod identity in {path}")
    folder = root / "mods" / key
    if document.get("operation") == "update":
        previous = document["previous"]
        if not _match(_ID, previous):
            raise InvalidStore(f"Invalid previous-version identity in {path}")
        old, new = document["old"], document["new"]
        for files in (old, new):
            if not files or any(not bundle_name(name) or not _match(_SHA1, sha1) for name, sha1 in files.items()):
                raise InvalidStore(f"Invalid update file list in {path}")
        committed = (
            document["phase"] == "replacing"
            and records.get(key, {}).get("files") == sorted(new)
            and all(present(folder / name) and digest(folder / name, "sha1") == sha1 for name, sha1 in new.items())
        )
        for name, sha1 in old.items():
            saved = folder / "previous" / previous / name
            if present(saved):
                _check_digest(saved, sha1)
            elif document["phase"] == "replacing" and not committed:
                _check_digest(folder / name, sha1)
        return
    record, hashes = document["record"], document["hashes"]
    if (
        record.get("id") != key
        or not isinstance(record.get("files"), list)
        or not hashes
        or set(record["files"]) != set(hashes)
    ):
        raise InvalidStore(f"Invalid import file list in {path}")
    repair = document.get("repair_source_id")
    # The import envelope uses an empty string when there is no repair source.
    if repair not in (None, "") and not _match(_ID, repair):
        raise InvalidStore(f"Invalid repair source in {path}")
    for name, sha1 in hashes.items():
        if not bundle_name(name) or not _match(_SHA1, sha1):
            raise InvalidStore(f"Invalid import payload in {path}")
        if document["phase"] == "publishing" and records.get(key, {}).get("files") == record["files"]:
            _check_digest(folder / name, sha1)
        else:
            _check_digest(folder / name, sha1, optional=True)
            _check_digest(path.parent / "files" / name, sha1, optional=True)


def _deletions(path: Path, document: Document, *, check_game: bool) -> None:
    if document["phase"] == "committed":
        return
    for key in document["mods"]:
        if not _match(_ID, key) or key not in document["records"]:
            raise InvalidStore(f"Invalid deletion backup identity in {path}")
        for name in document["records"][key]["files"]:
            if not bundle_name(name) or not present(path.parent / "mods" / key / name):
                raise RecoveryPending(f"Missing or unsafe deletion backup in {path}")
    for name in document["built"]:
        if not bundle_name(name):
            raise InvalidStore(f"Invalid built file in {path}")
        _check_digest(path.parent / "built" / name, name.removesuffix(".bundle"))
    if snapshot := document.get("thumbnail"):
        key = snapshot["key"]
        if not _match(_ID, key) or digest(path.parent / "photo.png") != snapshot["entry"]["sha256"]:
            raise RecoveryPending(f"Invalid thumbnail rollback payload in {path}")
    targets = set()
    for index, item in enumerate(document["games"]):
        target = _target(item, item["pin"])
        if target in targets or Path(item["backup"]) != Path(f"game/{index}.bundle"):
            raise InvalidStore(f"Invalid game rollback path in {path}")
        targets.add(target)
        _check_digest(path.parent / item["backup"], item["before"])
        if not isinstance(item["after"], list) or any(not _match(_SHA1, sha1) for sha1 in item["after"]):
            raise InvalidStore(f"Invalid game rollback digest list in {path}")
        if check_game and "held" not in document:
            current = digest(target, "sha1") if present(target) else None
            if current not in [item["before"], *item["after"]]:
                raise RecoveryPending(f"Cannot prove the deletion recovery outcome: {target}")


def verify_journals(root: Path, records: dict, *, check_game: bool) -> None:
    """Validate staged or live journal inputs without applying any recovery write."""
    writes: dict[Path, str] = {}
    pending_deletion = False
    for directory, store in (("import-transactions", "I"), ("transactions", "T"), ("asset-deletions", "D")):
        for path in sorted((root / directory).glob("*/journal.json")):
            if not _match(_ID, path.parent.name):
                raise InvalidStore(f"Invalid journal directory: {path}")
            document = load_document(store, path)
            try:
                if store == "I":
                    _imports(root, path, document, records)
                elif store == "T":
                    _writes(root, path, document, check_game=check_game)
                    if document["phase"] != "committed":
                        for item in document["files"]:
                            target = Path(item["target"])
                            if target in writes:
                                raise RecoveryPending(f"Overlapping pending game writes: {target}")
                            writes[target] = item["before"]
                else:
                    _deletions(path, document, check_game=check_game)
                    if document["phase"] == "active":
                        if pending_deletion:
                            raise RecoveryPending("Multiple deletion snapshots need an explicit recovery order")
                        pending_deletion = True
                        for item in document["games"]:
                            target = Path(item["target"])
                            if target in writes and writes[target] not in [item["before"], *item["after"]]:
                                raise RecoveryPending(f"Conflicting rollback inputs: {target}")
            except (KeyError, TypeError) as error:
                raise InvalidStore(f"Invalid recovery document: {path}") from error


def game_recovery_inputs(root: Path, document: Document) -> list[tuple[Path, str]]:
    """Identify verified files still held only by the game side of a pending write."""
    if document["phase"] == "committed":
        return []
    inputs = []
    for item in document["files"]:
        target = _target(item, document["pin"])
        original = root / "originals" / target.name
        expected = None
        if item.get("restore") and not present(original):
            expected = item["after"]
        elif item.get("official") is True and not present(original):
            expected = item["before"]
        if expected is not None and present(target) and digest(target, "sha1") == expected:
            inputs.append((target, expected))
    return inputs
