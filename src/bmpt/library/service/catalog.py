"""Keep, generate and journal swaps of a pinned install's game catalog."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from ...core import devmode
from ...core.errors import ServiceError, log_service_error
from ...core.filelock import replace as replace_file
from ...host.format_manifest import fence_catalog_stores
from ...host.store_formats import CATALOG_JOURNALS, Document, InvalidStore, json_bytes, parse_json, read_runtime
from ...host.upgrade_files import digest
from ...unity import ccdb
from ..catalog_switch import MODE_FILES, MODS_DIR, CatalogPaths, catalog_paths
from ..records import Pin
from ..safety import check_resource_path
from ..storage import atomic_bytes, atomic_clone, atomic_copy, atomic_json, sync_directory, verify_file
from .base import _errors

logger = logging.getLogger(__name__)
_SWAP_ID = re.compile(r"[0-9a-f]{32}")
_RESOURCE_VERSION = re.compile(r"[0-9a-f]{40}")
CatalogKind = Literal["official", "mods"]


def _stamp(path: Path, sha1: str) -> Document:
    stat = path.stat()
    return {"sha1": sha1, "size": stat.st_size, "mtime": stat.st_mtime_ns}


def _live_kind(live: Path, record: Document) -> CatalogKind | None:
    stat = live.stat()
    known = {kind: record[kind] for kind in ("official", "mods") if record[kind] is not None}
    matches = [
        kind for kind, stamp in known.items() if (stat.st_size, stat.st_mtime_ns) == (stamp["size"], stamp["mtime"])
    ]
    if len(matches) == 1:
        return matches[0]
    sha1 = digest(live.resolve(), "sha1")
    return next((kind for kind, stamp in known.items() if stamp["sha1"] == sha1), None)


def _side_copy(live: Path, rv: str, kind: CatalogKind) -> Path:
    path = live.parent / MODS_DIR / f"{kind}-{rv[:12]}.bytes"
    check_resource_path(path, [live.parent])
    return path


def _temp_copy(live: Path, swap_id: str) -> Path:
    return live.with_name(f"{live.name}.{swap_id}.tmp")


def _validate_official(raw: bytes) -> None:
    if ccdb.write(ccdb.parse(raw)) != raw:
        raise ccdb.CatalogFormatError("roundtrip", "Serializing the official catalog changed its bytes.")


def _guard_error(error: ccdb.CatalogFormatError) -> ServiceError:
    code = "catalog_unsupported" if error.reason == "version" else "catalog_invalid"
    return ServiceError(code, guard=error.reason)


class CatalogMixin:
    """Persist catalog copies and settle interrupted catalog swaps."""

    def _catalog_document(self) -> Document:
        try:
            return read_runtime(self.root / "catalogs.json")
        except FileNotFoundError:
            return {"version": 1, "installs": {}}

    def _catalog_record(self, pin: Pin) -> Document:
        """Read the pinned install's record, defaulting to files mode before migration."""
        return self._catalog_document()["installs"].get(pin.key, {"mode": MODE_FILES})

    def _save_catalog_record(self, pin: Pin, record: Document) -> None:
        """Save one install under the transaction lock, holding the library lock for the JSON write."""
        document = self._catalog_document()
        document["installs"][pin.key] = record
        with self._lock:
            atomic_json(self.root / "catalogs.json", document)

    def _catalog_library_copy(self, pin: Pin, rv: str) -> Path:
        if not _RESOURCE_VERSION.fullmatch(rv):
            raise InvalidStore("Invalid catalog resource version")
        path = self.root / "catalogs" / pin.id / f"{rv}.bytes"
        if path.resolve() != path:
            raise InvalidStore("Linked library catalog path")
        return path

    def _catalog_paths(self, pin: Pin, *, recovering: bool = False) -> CatalogPaths:
        devmode.check_read(pin.root)
        paths = catalog_paths(pin, allow_missing=recovering)
        if paths is None:
            raise ServiceError("catalog_unsupported")
        check_resource_path(paths.live, [pin.root])
        return paths

    def _catalog_record_for(self, pin: Pin) -> Document:
        record = self._catalog_record(pin)
        if not record.get("official") or record["rv"] != self.resource_version(pin):
            raise ServiceError("catalog_changed")
        return record

    def _keep_official_catalog(self, pin: Pin) -> Document:
        """Keep verified official bytes in a class 2 or 3 step under the transaction lock.

        The library lock is held for one store save. Copying and validation leave it free for foreground reads.
        """
        with _errors("keep official catalog"), self._transaction_lock():
            live = self._catalog_paths(pin).live
            with self._writable([live], pin, resource_dirs=[pin.root]):
                rv = self.resource_version(pin)
                if rv is None:
                    raise ServiceError("catalog_unsupported")
                library_copy = self._catalog_library_copy(pin, rv)
                side_copy = _side_copy(live, rv, "official")
                devmode.check_write(side_copy)
                previous = self._catalog_record(pin)
                raw = live.read_bytes()
                sha1 = hashlib.sha1(raw).hexdigest()
                same_version = previous.get("rv") == rv
                if same_version and sha1 != previous["official"]["sha1"]:
                    raise ServiceError("catalog_changed")
                try:
                    _validate_official(raw)
                except ccdb.CatalogFormatError as error:
                    raise _guard_error(error) from error
                if self._managed:
                    fence_catalog_stores(self.root)
                atomic_bytes(library_copy, raw)
                verify_file(library_copy, sha1)
                atomic_copy(library_copy, side_copy)
                verify_file(side_copy, sha1)
                record = (
                    previous
                    if same_version
                    else {
                        "mode": previous["mode"],
                        "reason": None,
                        "migrated": previous.get("migrated"),
                        "rv": rv,
                        "mods": None,
                        "redirects": {},
                    }
                )
                record["official"] = {**_stamp(live, sha1), "crc32": zlib.crc32(raw)}
                record["live"] = "official"
                self._save_catalog_record(pin, record)
                if not same_version and previous.get("rv"):
                    old_copy = self._catalog_library_copy(pin, previous["rv"])
                    old_copy.unlink(missing_ok=True)
                    sync_directory(old_copy.parent)
                return record

    def _generate_catalog(self, pin: Pin, redirects: Mapping[str, Document]) -> Document:
        """Generate from recorded official bytes in a class 2 or 3 step under the transaction lock.

        Each redirect supplies its placed filename and store metadata. Generation leaves the library lock free
        for foreground reads and takes it for one store save.
        """
        with _errors("generate catalog"), self._transaction_lock():
            live = self._catalog_paths(pin).live
            with self._writable([live], pin, resource_dirs=[pin.root]):
                record = self._catalog_record_for(pin)
                target = _side_copy(live, record["rv"], "mods")
                devmode.check_write(target)
                placed = {name: item["placed"] for name, item in redirects.items()}
                redirects_sha1 = hashlib.sha1(json_bytes(dict(sorted(placed.items())))).hexdigest()
                if (
                    record["mods"]
                    and redirects_sha1 != record["mods"]["redirects_sha1"]
                    and _live_kind(live, record) != "official"
                ):
                    # Keep the only recorded mods fingerprint as long as those bytes could still be live.
                    raise ServiceError("catalog_changed")
                official = self._catalog_library_copy(pin, record["rv"]).read_bytes()
                if hashlib.sha1(official).hexdigest() != record["official"]["sha1"]:
                    raise ServiceError("official_invalid")
                try:
                    raw = ccdb.redirect(official, placed, MODS_DIR)
                except ccdb.CatalogFormatError as error:
                    refusal = _guard_error(error)
                    self._save_catalog_record(pin, {**record, "mode": MODE_FILES, "reason": refusal.code})
                    raise refusal from error
                sha1 = hashlib.sha1(raw).hexdigest()
                atomic_bytes(target, raw)
                record["mods"] = {
                    **_stamp(target, sha1),
                    "redirects_sha1": redirects_sha1,
                }
                record["redirects"] = dict(redirects)
                self._save_catalog_record(pin, record)
                return record

    def _swap_catalog(self, pin: Pin, want: CatalogKind) -> None:
        """Swap catalogs in one non-yielding class 2 or 3 transaction step.

        Checking, cloning and replacing leave the library lock free for foreground reads. One store save holds it.
        """
        if want not in ("official", "mods"):
            raise ValueError("Unknown catalog swap destination")
        with _errors("swap catalog"), self._transaction_lock():
            live = self._catalog_paths(pin).live
            with self._writable([live], pin, resource_dirs=[pin.root]):
                record = self._catalog_record_for(pin)
                current = _live_kind(live, record)
                if current is None:
                    raise ServiceError("catalog_changed")
                self._clean_catalog_temps(live)
                if current == want:
                    if record["live"] != want:
                        self._save_catalog_live(pin, live, record, want)
                    return
                if record[want] is None:
                    raise ServiceError("catalog_invalid")
                source = _side_copy(live, record["rv"], want)
                library_copy = self._catalog_library_copy(pin, record["rv"])
                sha1 = record[want]["sha1"]
                if want == "official":
                    try:
                        verify_file(source, sha1)
                    except OSError:
                        source = library_copy
                swap_id = uuid.uuid4().hex
                journal = self.root / CATALOG_JOURNALS / swap_id / "journal.json"
                transaction = {
                    "version": 1,
                    "pin": {"root": str(pin.root), "server": pin.server, "key": pin.key},
                    "from": {"sha1": record[current]["sha1"]},
                    "to": {"sha1": sha1, "kind": want},
                    "official": {
                        "sha1": record["official"]["sha1"],
                        "library_copy": library_copy.relative_to(self.root).as_posix(),
                    },
                    "phase": "preparing",
                }
                atomic_json(journal, transaction)
                temporary = _temp_copy(live, swap_id)
                self._prepare_catalog_swap(source, temporary, live, sha1)
                atomic_json(journal, {**transaction, "phase": "swapping"})
                replace_file(temporary, live)
                sync_directory(live.parent)
                self._save_catalog_live(pin, live, record, want)
                self._close_catalog_journal(journal, live)

    def _prepare_catalog_swap(self, source: Path, temporary: Path, live: Path, sha1: str) -> None:
        source = source.resolve()
        if not atomic_clone(source, temporary):
            atomic_copy(source, temporary)
        if live.exists():
            temporary.chmod(live.stat().st_mode & 0o777)
        try:
            verify_file(temporary, sha1)
        except OSError as error:
            raise ServiceError("catalog_invalid", file=source) from error

    def _save_catalog_live(self, pin: Pin, live: Path, record: Document, kind: CatalogKind) -> None:
        stamp = record[kind]
        updated = {**stamp, **_stamp(live, stamp["sha1"])}
        self._save_catalog_record(pin, {**record, kind: updated, "live": kind})

    def _clean_catalog_temps(self, live: Path) -> None:
        journals = self.root / CATALOG_JOURNALS
        for path in live.parent.glob(f"{live.name}.*.tmp"):
            swap_id = path.name[len(live.name) + 1 : -4]
            if _SWAP_ID.fullmatch(swap_id) and not (journals / swap_id / "journal.json").is_file():
                devmode.check_write(live.parent)
                path.unlink(missing_ok=True)
                sync_directory(live.parent)

    def _close_catalog_journal(self, journal: Path, live: Path) -> None:
        temporary = _temp_copy(live, journal.parent.name)
        if temporary.exists() or temporary.is_symlink():
            devmode.check_write(live.parent)
            temporary.unlink(missing_ok=True)
            sync_directory(live.parent)
        shutil.rmtree(journal.parent)
        sync_directory(journal.parent.parent)

    def _recover_catalog(self) -> None:
        """Settle catalog journals under the transaction lock at startup (class 3) or in the writer's job class.

        Each journal leaves the library lock free for reads until a short store save. With no journal there is
        no install to inspect. A catalog swap also clears orphaned temps for its own install.
        """
        journals = self.root / CATALOG_JOURNALS
        for journal in sorted(journals.glob("*/journal.json")):
            try:
                self._recover_catalog_journal(journal)
            except (ValueError, KeyError, TypeError) as error:
                raise ServiceError("library_recovery_pending", file=journal) from error

    def _recover_catalog_journal(self, journal: Path) -> None:
        if not _SWAP_ID.fullmatch(journal.parent.name) or journal.resolve() != journal:
            raise InvalidStore("Invalid catalog journal path")
        data = parse_json(journal.read_bytes())
        if type(data["version"]) is not int or data["version"] != 1 or data["phase"] not in ("preparing", "swapping"):
            raise InvalidStore("Invalid catalog journal version or phase")
        info = data["pin"]
        root = Path(info["root"])
        devmode.check_read(root)
        pin = self._pin(root, info["server"])
        if pin is None or pin.key != info["key"]:
            if journal.parent.name not in self._waiting:
                self._waiting.add(journal.parent.name)
                logger.warning("Catalog journal %s waits for its install", journal.parent.name)
            return
        record = self._catalog_record(pin)
        live = self._catalog_paths(pin, recovering=True).live
        library_copy = self._catalog_library_copy(pin, record["rv"])
        want = data["to"]["kind"]
        if want not in ("official", "mods") or record["mods"] is None:
            raise InvalidStore("Invalid catalog journal destination")
        source = "mods" if want == "official" else "official"
        official_sha1 = record["official"]["sha1"]
        to_sha1, from_sha1 = record[want]["sha1"], record[source]["sha1"]
        if (
            data["official"]["library_copy"] != library_copy.relative_to(self.root).as_posix()
            or data["official"]["sha1"] != official_sha1
            or data["to"]["sha1"] != to_sha1
            or data["from"]["sha1"] != from_sha1
        ):
            raise InvalidStore("Catalog journal fingerprints disagree with the store")
        self._clean_catalog_temps(live)
        try:
            sha1 = digest(live.resolve(), "sha1")
        except FileNotFoundError:
            sha1 = None
        if sha1 == to_sha1:
            self._save_catalog_live(pin, live, record, want)
        elif sha1 != from_sha1 and not self._other_official_catalog(live):
            with self._writable([live], pin, recovering=True, resource_dirs=[pin.root]):
                temporary = _temp_copy(live, journal.parent.name)
                temporary.unlink(missing_ok=True)
                self._prepare_catalog_swap(library_copy, temporary, live, official_sha1)
                replace_file(temporary, live)
                sync_directory(live.parent)
                self._save_catalog_live(pin, live, record, "official")
            log_service_error(logger, ServiceError("catalog_restored"))
        self._close_catalog_journal(journal, live)
        self._waiting.discard(journal.parent.name)

    def _other_official_catalog(self, live: Path) -> bool:
        # A game update replaces the catalog with a newer official one, which must stay in place.
        try:
            _validate_official(live.read_bytes())
        except (FileNotFoundError, ccdb.CatalogFormatError):
            return False
        return True
