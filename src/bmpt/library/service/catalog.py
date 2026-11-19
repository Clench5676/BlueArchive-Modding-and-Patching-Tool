"""Keep, generate and journal swaps of a pinned install's game catalog."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import shutil
import uuid
import zlib
from collections.abc import Mapping
from functools import lru_cache
from os import stat_result
from pathlib import Path
from typing import Literal

from ...core import devmode
from ...core.errors import ServiceError, log_service_error
from ...core.filelock import replace as replace_file
from ...host.format_manifest import fence_catalog_stores
from ...host.store_formats import CATALOG_JOURNALS, Document, InvalidStore, json_bytes, parse_json, read_runtime
from ...host.upgrade_files import digest
from ...unity import ccdb
from .. import toggle_timing
from ..catalog_switch import MODE_CATALOG, MODE_FILES, MODS_DIR, CatalogPaths, catalog_paths, placed_name
from ..judge import core_of, crc_state
from ..records import Pin
from ..safety import check_resource_path
from ..storage import atomic_bytes, atomic_clone, atomic_copy, atomic_json, sync_directory, verify_file
from .base import _errors

logger = logging.getLogger(__name__)
_SWAP_ID = re.compile(r"[0-9a-f]{32}")
_RESOURCE_VERSION = re.compile(r"[0-9a-f]{40}")
_SIDE_COPY = re.compile(r"(?:official|mods)-([0-9a-f]{12})\.bytes")
_PLACED = re.compile(r"[0-9a-f]{40}\.bundle")
_LOAD_PREFIX = "{PlatformUtils.AddressableLoadPath}"
CatalogKind = Literal["official", "mods"]


def _stamp(path: Path, sha1: str) -> Document:
    stat = path.stat()
    return {"sha1": sha1, "size": stat.st_size, "mtime": stat.st_mtime_ns}


def _matches_stat(stat: stat_result, stamp: Document) -> bool:
    return (stat.st_size, stat.st_mtime_ns) == (stamp["size"], stamp["mtime"])


def _redirects_sha1(redirects: Mapping[str, Document]) -> str:
    placed = {name: item["placed"] for name, item in sorted(redirects.items())}
    return hashlib.sha1(json_bytes(placed)).hexdigest()


@lru_cache(maxsize=8)
def _load_names(path: Path, sha1: str, size: int, mtime: int) -> frozenset[str]:
    raw = path.read_bytes()
    if hashlib.sha1(raw).hexdigest() != sha1:
        raise ServiceError("official_invalid")
    return frozenset(name for name, (_, prefix) in ccdb.bundle_ids(ccdb.parse(raw)).items() if prefix == _LOAD_PREFIX)


def _load_catalog_names(path: Path, sha1: str) -> frozenset[str]:
    stat = path.stat()
    return _load_names(path, sha1, stat.st_size, stat.st_mtime_ns)


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

    @contextlib.contextmanager
    def _catalog_transaction(self):
        """Protect staged placements and publication in a class 2 or 3 job.

        Take the library lock to enter and leave the transaction. The caller holds it for one core at a time
        and releases it for generation and swaps. Other transactions waiting for this one release it too.
        """
        with contextlib.ExitStack() as transaction:
            with self._lock:
                transaction.enter_context(self._transaction_lock())
                outermost = self._catalog_idle.is_set()
                self._catalog_idle.clear()
            try:
                yield
            finally:
                with self._lock:
                    try:
                        transaction.close()
                    finally:
                        if outermost:
                            self._catalog_idle.set()

    def _enter_catalog_mode(self, pin: Pin) -> None:
        """Keep the official catalog and select catalog mode in a class 3 transaction.

        Validation and copying leave the library lock free. The store saves take it briefly.
        """
        with self._catalog_transaction(), self._lock.unlocked():
            if self._catalog_record(pin)["mode"] != MODE_CATALOG:
                record = self._keep_official_catalog(pin)
                self._save_catalog_record(pin, {**record, "mode": MODE_CATALOG, "reason": None})

    def _catalog_targets(self, pin: Pin, targets: list[Path], record: Document | None = None) -> bool:
        """Check a core's whole set of targets against the kept catalog's load-path IDs."""
        record = self._catalog_record(pin) if record is None else record
        if record["mode"] != MODE_CATALOG or not targets:
            return False
        paths = self._catalog_paths(pin, recovering=True)
        if any(path.parent != paths.mods_dir.parent for path in targets):
            return False
        official = self._catalog_library_copy(pin, record["rv"])
        sha1 = record["official"]["sha1"]
        try:
            names = _load_catalog_names(official, sha1)
        except FileNotFoundError as error:
            # Matching official bytes can still identify cores that use file replacement.
            side = _side_copy(paths.live, record["rv"], "official")
            for candidate in (side, paths.live):
                try:
                    names = _load_catalog_names(candidate, sha1)
                except (OSError, ServiceError, ccdb.CatalogFormatError):
                    continue
                if any(path.name not in names for path in targets):
                    return False
                break
            raise ServiceError("catalog_changed") from error
        except ccdb.CatalogFormatError as error:
            raise _guard_error(error) from error
        return all(path.name in names for path in targets)

    def _placed_path(self, pin: Pin, redirect: Document) -> Path:
        bundles = self._catalog_paths(pin, recovering=True).mods_dir.parent
        directory = bundles.parent.parent / redirect["dir"]
        return directory / MODS_DIR / redirect["placed"]

    def _placed_stamp(self, pin: Pin, redirect: Document, *, full: bool) -> tuple[Path, Document, bool]:
        path = self._placed_path(pin, redirect)
        if path.resolve() != path:
            raise ServiceError("game_file_outside_resources", file=path)
        stat = path.stat()
        sha1 = path.stem
        stamp = {"sha1": sha1, "size": stat.st_size, "mtime": stat.st_mtime_ns}
        match = _matches_stat(stat, redirect)
        if not match and full and stat.st_size == redirect["size"]:
            match = digest(path, "sha1") == sha1
        return path, stamp, match

    @toggle_timing.stage("place")
    def _place(self, pin: Pin, name: str, source_file: Path) -> Document:
        """Place one prepared bundle during a class 2 entry step under the library and transaction locks.

        Copy and verify one bundle; the caller yields the library lock at the next entry boundary.
        """
        with _errors("place bundle"):
            fingerprint = self._hashes.get(source_file)
            if fingerprint.trailing is None:
                raise ServiceError("bundle_invalid", file=source_file)
            sha1 = fingerprint.sha1
            placed = placed_name(sha1)
            redirect = {"placed": placed, "dir": "GameData/Windows"}
            path = self._placed_path(pin, redirect)
            if path.resolve() != path:
                raise ServiceError("game_file_outside_resources", file=path)
            devmode.check_write(path)
            try:
                verify_file(path, sha1)
            except OSError:
                if not atomic_clone(source_file, path):
                    atomic_copy(source_file, path)
                verify_file(path, sha1)
            stat = path.stat()
            return {
                **redirect,
                "size": stat.st_size,
                "mtime": stat.st_mtime_ns,
                "crc": crc_state(fingerprint.crc, fingerprint.trailing, name),
            }

    def _catalog_live(self, pin: Pin, record: Document) -> CatalogKind | None:
        """Identify the live catalog by stat and hash only when its stamp differs."""
        try:
            return self._live_kind(self._catalog_paths(pin, recovering=True).live, record)
        except (OSError, ServiceError):
            return None

    def _live_kind(self, live: Path, record: Document, stat: stat_result | None = None) -> CatalogKind | None:
        stat = live.stat() if stat is None else stat
        official, mods = record["official"], record["mods"]
        official_match = _matches_stat(stat, official)
        mods_match = mods is not None and _matches_stat(stat, mods)
        if official_match != mods_match:
            return "official" if official_match else "mods"
        mods_sha1 = mods["sha1"] if mods is not None else None
        key = (stat.st_size, stat.st_mtime_ns, official["sha1"], mods_sha1)
        cached = self._catalog_identifications.get(live)
        if cached is not None and cached[0] == key:
            return cached[1]
        sha1 = digest(live.resolve(), "sha1")
        kind = "official" if sha1 == official["sha1"] else "mods" if sha1 == mods_sha1 else None
        self._catalog_identifications[live] = (key, kind)
        return kind

    def _reusable_redirects(
        self, pin: Pin, core: str, targets: list[Path], source: str, resource_type: str
    ) -> dict[str, Document] | None:
        """Check a class 2 entry's placed files and current build under the library lock for one core."""
        record = self._catalog_record_for(pin)
        saved = {target.name: record["redirects"].get(target.name) for target in targets}
        if any(not item or item["source"] != source for item in saved.values()):
            return None
        if source.startswith("mod:"):
            builds = self._current_builds(pin, core, self._record(source[4:]), targets)
        elif source.startswith("alt:"):
            try:
                inputs = self._switch_alt_sources(pin, source[4:], resource_type)
                builds = {self._build_signature(inputs, targets, pin)}
            except (OSError, ServiceError):
                return None
        else:
            return None
        redirects = {}
        for name, redirect in saved.items():
            if redirect["build"] not in builds:
                return None
            try:
                _, stamp, match = self._placed_stamp(pin, redirect, full=True)
            except (OSError, ServiceError):
                return None
            if not match:
                return None
            redirects[name] = {**redirect, "size": stamp["size"], "mtime": stamp["mtime"]}
        return redirects

    def _place_entry(
        self, pin: Pin, replacements: Mapping[Path, Path], source: str, build_keys: Mapping[Path, str]
    ) -> dict[str, Document]:
        """Place one class 2 entry under the library lock, yielding at the caller's next entry boundary."""
        self._check_stopping(pin)
        for target in replacements:
            check_resource_path(target, list(pin.dirs))
        self._check_references(replacements, pin)
        return {
            target.name: {
                **self._place(pin, target.name, prepared),
                "source": source,
                "build": build_keys.get(target, ""),
            }
            for target, prepared in replacements.items()
        }

    def _regenerate(
        self,
        pin: Pin,
        staged: Mapping[str, Document] | None = None,
        *,
        drop: set[str] | None = None,
        replace_all: bool = False,
    ) -> bool:
        """Publish the configured redirects in a class 2 transaction.

        Snapshot configuration under the library lock. Verification, generation and each atomic swap leave
        the lock free for foreground reads. Each catalog store save takes it briefly. Return whether the
        live catalog changed.
        """
        staged = staged or {}
        with self._catalog_transaction():
            with self._lock:
                record = self._catalog_record_for(pin)
                cores = self._core_map(pin)
                folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
                configured = folder.get("configured") or {}
                candidates = {} if replace_all else dict(record["redirects"])
                for name in tuple(candidates):
                    if drop and cores.slot(core_of(name)) in drop:
                        del candidates[name]
                candidates.update(staged)
                by_core: dict[str, dict[str, Document]] = {}
                for name, item in candidates.items():
                    by_core.setdefault(cores.slot(core_of(name)), {})[name] = item
                groups = [
                    group
                    for core, group in by_core.items()
                    if group.keys() == {path.name for path in self._spine_parts(core, pin)}
                    and all(item["source"] == (configured.get(core) or {}).get("source") for item in group.values())
                ]
            with self._lock.unlocked(), _errors("regenerate catalog"):
                redirects = {}
                for group in groups:
                    try:
                        if all(self._placed_stamp(pin, item, full=True)[2] for item in group.values()):
                            redirects.update(group)
                    except (OSError, ServiceError):
                        if group.keys() & staged.keys():
                            raise
                if not staged.keys() <= redirects.keys():
                    raise ServiceError("entry_changed")
                placed = [item["placed"] for item in redirects.values()]
                if len(set(placed)) != len(placed):
                    raise ServiceError("catalog_invalid", guard="ambiguous")
                live = self._catalog_paths(pin).live
                current = self._live_kind(live, record)
                if current is None:
                    raise ServiceError("catalog_changed")
                mods = record["mods"]
                changed = mods is None or mods["redirects_sha1"] != _redirects_sha1(redirects)
                missing_copy = False
                if not changed and current == "official":
                    try:
                        side = _side_copy(live, record["rv"], "mods")
                        if not _matches_stat(side.stat(), mods):
                            verify_file(side, mods["sha1"])
                    except OSError:
                        missing_copy = True
                if changed or missing_copy:
                    if current == "mods":
                        self._timed_swap(pin, "official")
                    with toggle_timing.stage("generate"):
                        self._generate_catalog(pin, redirects)
                elif redirects != record["redirects"]:
                    self._save_catalog_record(pin, {**record, "redirects": redirects})
                self._timed_swap(pin, "mods")
                return changed or current == "official"

    def _timed_swap(self, pin: Pin, want: CatalogKind) -> None:
        """Run one class 2 swap outside the library lock, apart from its store save."""
        with self._lock.unlocked(), toggle_timing.stage("swap"):
            self._swap_catalog(pin, want)

    def _prune_catalogs(self, pins: list[Pin], installs: Mapping[str, Document]) -> None:
        """Prune catalog side copies and orphan placements in class 4 under the transaction lock.

        Yield the library lock between directory entries. The first orphan for a live catalog stamp
        can also require reading and parsing that catalog under the lock.
        """
        for pin in pins:
            record = installs.get(pin.key)
            if not record:
                continue
            paths = catalog_paths(pin, allow_missing=True)
            if paths is None:
                continue
            kept = {self._placed_path(pin, item) for item in record["redirects"].values()}
            side = paths.live.parent / MODS_DIR
            directories = [folder / MODS_DIR for folder in pin.dirs]
            if record["mode"] == MODE_CATALOG:
                directories.append(side)
            live_stamp = live_kept = None
            for directory in directories:
                if directory.resolve() != directory:
                    continue
                for path in directory.iterdir() if directory.is_dir() else ():
                    self._lock.step_aside()
                    if directory == side:
                        match = _SIDE_COPY.fullmatch(path.name)
                        stale = match and match[1] != record["rv"][:12]
                    else:
                        stale = _PLACED.fullmatch(path.name) and path not in kept
                    if stale and not path.is_symlink() and path.is_file():
                        if directory != side:
                            try:
                                devmode.check_read(paths.live)
                                stat = paths.live.stat()
                                stamp = (stat.st_size, stat.st_mtime_ns)
                                if stamp != live_stamp:
                                    live_stamp, live_kept = stamp, None
                                    referenced = self._foreign_placements(paths.live, record, stat)
                                    current = paths.live.stat()
                                    if stamp != (current.st_size, current.st_mtime_ns):
                                        continue
                                    live_kept = referenced
                            except (OSError, ServiceError, ccdb.CatalogFormatError):
                                continue
                            if live_kept is None or path.name in live_kept:
                                continue
                        devmode.check_write(path)
                        path.unlink(missing_ok=True)

    def _foreign_placements(self, live: Path, record: Document, stat: stat_result) -> set[str]:
        if self._live_kind(live, record, stat) is not None:
            return set()
        raw = live.read_bytes()
        try:
            catalog = ccdb.parse(raw)
        except ccdb.CatalogFormatError as error:
            if error.reason != "trailing":
                raise
            catalog = ccdb.parse(memoryview(raw)[:-4])
        folder = MODS_DIR.lower().encode()
        return {
            parts[-1].decode("utf-8", errors="surrogateescape").lower()
            for identifier in catalog.ids
            if len(parts := identifier.replace(b"/", b"\\").split(b"\\")) >= 2 and parts[-2].lower() == folder
        }

    def _save_catalog_record(self, pin: Pin, record: Document) -> None:
        """Save one install under the transaction lock, holding the library lock for the JSON write."""
        document = self._catalog_document()
        document["installs"][pin.key] = record
        with self._lock:
            atomic_json(self.root / "catalogs.json", document)
            self._version += 1

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
                redirects_sha1 = _redirects_sha1(redirects)
                if (
                    record["mods"]
                    and redirects_sha1 != record["mods"]["redirects_sha1"]
                    and self._live_kind(live, record) != "official"
                ):
                    # Keep the only recorded mods fingerprint as long as those bytes could still be live.
                    raise ServiceError("catalog_changed")
                try:
                    official = self._catalog_library_copy(pin, record["rv"]).read_bytes()
                except FileNotFoundError as error:
                    raise ServiceError("catalog_changed") from error
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
                record = self._catalog_record(pin)
                if not record.get("official"):
                    raise ServiceError("catalog_changed")
                current = self._live_kind(live, record) if record["rv"] == self.resource_version(pin) else None
                if current is None:
                    if want == "official" and self._other_official_catalog(live):
                        if record["live"] != "official":
                            self._save_catalog_record(pin, {**record, "live": "official"})
                        return
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
