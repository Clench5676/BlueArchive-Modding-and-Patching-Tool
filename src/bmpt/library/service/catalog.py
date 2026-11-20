"""Keep, generate and journal swaps of a pinned install's game catalog."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import time
import uuid
import zlib
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from os import stat_result
from pathlib import Path
from stat import S_ISREG
from typing import Literal

from ...core import devmode, filelock
from ...core.errors import ServiceError, log_service_error
from ...host import game_paths
from ...core.filelock import replace as replace_file
from ...host.format_manifest import fence_catalog_stores
from ...host.store_formats import CATALOG_JOURNALS, Document, InvalidStore, json_bytes, parse_json, read_runtime
from ...host.upgrade_files import digest
from ...unity import ccdb
from .. import mod_progress, profiles, toggle_timing
from ..catalog_switch import MODE_CATALOG, MODE_FILES, MODS_DIR, CatalogPaths, catalog_paths, mode_of, placed_name
from ..judge import LIBRARY_SOURCES, core_of, crc_state, now
from ..records import Pin
from ..safety import check_resource_path
from ..storage import atomic_bytes, atomic_clone, atomic_copy, atomic_json, file_revision, sync_directory, verify_file
from .base import SAVE_EVERY, STOPPING, _Entry, _background, _errors

logger = logging.getLogger(__name__)
_SWAP_ID = re.compile(r"[0-9a-f]{32}")
_RESOURCE_VERSION = re.compile(r"[0-9a-f]{40}")
_SIDE_COPY = re.compile(r"(?:official|mods)-([0-9a-f]{12})\.bytes")
_PLACED = re.compile(r"[0-9a-f]{40}\.bundle")
_LOAD_PREFIX = "{PlatformUtils.AddressableLoadPath}"
_GUARD_REFUSALS = ("catalog_unsupported", "catalog_invalid")
_PATHS_KEPT = 2.0  # seconds an install's checked catalog paths are reused
CatalogKind = Literal["official", "mods"]


def _stamp(path: Path, sha1: str) -> Document:
    stat = path.stat()
    return {"sha1": sha1, "size": stat.st_size, "mtime": stat.st_mtime_ns}


def _matches_stat(stat: stat_result, stamp: Document) -> bool:
    return (stat.st_size, stat.st_mtime_ns) == (stamp["size"], stamp["mtime"])


def _file_revision(path: Path) -> tuple[int, ...] | None:
    try:
        return file_revision(path.stat())
    except FileNotFoundError:
        return None


def _has_mod_ids(catalog: ccdb.Catalog) -> bool:
    folder = MODS_DIR.lower().encode()
    return any(folder in identifier.replace(b"/", b"\\").lower().split(b"\\")[:-1] for identifier in catalog.ids)


@contextlib.contextmanager
def _paced_catalog():
    previous = getattr(_background, "paced", False)
    _background.paced = True
    try:
        yield
    finally:
        _background.paced = previous


def _redirects_sha1(redirects: Mapping[str, Document]) -> str:
    placed = {name: item["placed"] for name, item in sorted(redirects.items())}
    return hashlib.sha1(json_bytes(placed)).hexdigest()


@lru_cache(maxsize=8)
def _load_names(path: Path, sha1: str, size: int, mtime: int) -> frozenset[str]:
    raw = path.read_bytes()
    if hashlib.sha1(raw).hexdigest() != sha1:
        raise ServiceError("official_invalid", file=path)
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


def _validate_official(raw: bytes) -> ccdb.Catalog:
    catalog = ccdb.parse(raw)
    if ccdb.write(catalog) != raw:
        raise ccdb.CatalogFormatError("roundtrip", "Serializing the official catalog changed its bytes.")
    return catalog


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

    def _catalog_records_for_read(self) -> Document:
        try:
            return self._catalog_document()["installs"]
        except (OSError, InvalidStore) as error:
            logger.warning("Catalog records could not be read: %s", error)
            return {}

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
            record = self._catalog_record(pin)
            if record["mode"] != MODE_CATALOG:
                record = self._keep_official_catalog(pin)
                self._save_catalog_record(pin, {**record, "mode": MODE_CATALOG, "reason": None})

    def _catalog_migration_due(self, pin: Pin, record: Document, rv: str | None) -> bool:
        return (
            rv is not None
            and mode_of(pin)[0] == MODE_CATALOG
            and not (record["mode"] == MODE_CATALOG and record.get("migrated"))
            and not (record.get("reason") in _GUARD_REFUSALS and record.get("rv") == rv)
        )

    def _catalog_refusal(self, pin: Pin, rv: str, error: ServiceError) -> None:
        record = self._catalog_record(pin)
        if record.get("rv") != rv:
            sha1 = error.params.get("official_sha1")
            if sha1:
                live = self._catalog_paths(pin).live
                self._check_catalog_revision(pin, live, rv, error.params["revision"])
            self._mark_catalog_update(
                pin, record, self._catalog_cores(pin, record).keys() | record.get("pending", {}).keys()
            )
            record = {"rv": rv, "migrated": None, "official": None, "mods": None, "live": "unknown", "redirects": {}}
            if sha1:
                record["refused_sha1"] = sha1
        if self._managed and not (self.root / "catalogs.json").exists():
            fence_catalog_stores(self.root)
        self._save_catalog_record(pin, {**record, "mode": MODE_FILES, "reason": error.code})

    def _migrate_catalog(self, pin: Pin, progress: mod_progress.ProgressCallback | None = None) -> None:
        """Migrate in class 3 under the start cover or class 2 at a toggle.

        Snapshot the plan and each entry under the library lock. Reading, hashing, copying and swapping
        leave it free. The transaction protects writes, with a yield between entries and short store saves.
        """
        with _errors("migrate catalog"), _paced_catalog():
            rv = self.resource_version(pin)
            if not self._catalog_migration_due(pin, self._catalog_record(pin), rv):
                return
            line = mod_progress.ItemProgress(progress, 1, "process")
            line.item(1, step=mod_progress.STEP_CHECK)
            with self._catalog_transaction(), self._lock.unlocked():
                record = self._catalog_record(pin)
                if rv != self.resource_version(pin) or not self._catalog_migration_due(pin, record, rv):
                    return
                if self._catalog_version_changed(record, rv) or record.get("reapply"):
                    return
                try:
                    self._check_stopping(pin)
                    self._enter_catalog_mode(pin)
                    record = self._catalog_record_for(pin)
                    live = self._catalog_live(pin, record)
                    if live is None:
                        raise ServiceError("catalog_changed")
                    if live == "official" and not self._catalog_library_copy(pin, rv).is_file():
                        self._keep_official_catalog(pin)
                    self._migrate_catalog_entries(pin, progress)
                except ServiceError as error:
                    if error.code in _GUARD_REFUSALS and error.params.get("guard") and self.resource_version(pin) == rv:
                        self._catalog_refusal(pin, rv, error)
                    else:
                        raise

    def _defer_catalog_migration(self, pin: Pin) -> None:
        """Schedule a catalog retry after a toggle fell back to file replacement."""
        try:
            with _errors("defer catalog migration"), self._catalog_transaction(), self._lock.unlocked():
                record = self._catalog_record(pin)
                if record["mode"] == MODE_CATALOG and record.get("migrated"):
                    self._save_catalog_record(pin, {**record, "migrated": None})
        except ServiceError as error:
            logger.info("Could not schedule catalog retry: %s", error.code)

    def _migrate_catalog_entries(self, pin: Pin, progress: mod_progress.ProgressCallback | None) -> None:
        record = self._catalog_record_for(pin)
        with self._lock:
            config = self.profiles.read() or {}
            enabled = profiles.enabled(config)
            view = self._view([pin], "recorded", config, snapshot=True, official_only=True)
            mapped = view.maps[pin.key]
            cores = {
                mapped.slot(core_of(path.name))
                for folder in pin.dirs
                for path, item in view.by_folder.get(folder, ())
                if item.get("source", "").startswith(LIBRARY_SOURCES) and item.get("official") is not True
            }
            saved = self._catalog_cores(pin, record)
            cores.update(saved)
            if not enabled:
                cores.update((view.folder(pin.key).get("built") or {}).keys())
        view.allow = "full"
        planned = sorted(cores)
        started = placed_at = time.perf_counter()
        if planned:
            logger.info("Moving %d entries of %s to the catalog switch", len(planned), pin.key)
        line = mod_progress.ItemProgress(progress, len(planned) * (2 if enabled else 1), "process")
        groups = {}
        identities: dict[str, dict[str, str]] = {}
        for i, core in enumerate(planned, 1):
            self._let_others_in()
            with self._lock:
                identity = identities[core] = self._entry_identity(pin, core, config)
                targets = self._spine_parts(core, pin)
            line.item(i, **identity, step=mod_progress.STEP_WRITE)
            try:
                if targets and self._catalog_targets(pin, targets, record):
                    group = self._verify_migration_placements(pin, targets, saved.get(core, {}))
                    if not group:
                        with self._lock:
                            # The snapshot isolates the judgement from other callers while it hashes one core.
                            with self._lock.unlocked():
                                entry = view.entry(pin, core)
                            group = self._migration_placements(pin, targets, entry, enabled)
                    if group:
                        groups[core] = group
            except (OSError, InvalidStore, ServiceError) as error:
                if isinstance(error, ServiceError) and error.code in (*STOPPING, "catalog_changed", *_GUARD_REFUSALS):
                    raise
                logger.info("Catalog migration deferred for %s: %s", core, error)
            line.done()
        redirects = {name: item for group in groups.values() for name, item in group.items()}
        placed_at = time.perf_counter()
        if redirects != record["redirects"] or (redirects and not self._migration_catalog_available(pin, record)):
            self._publish_migration_catalog(pin, record, redirects)
        if enabled and redirects:
            if self._catalog_check_pending(pin, record["rv"]):
                return
            self._swap_migration_catalog(pin, groups, redirects)
            outcomes = self._migration_outcomes = {}
            saved_at = time.monotonic()
            try:
                for i, core in enumerate(planned, len(planned) + 1):
                    self._let_others_in()
                    line.item(i, **identities[core], step=mod_progress.STEP_RESTORE)
                    if core in groups and not self._restore_migrated_entry(pin, core, groups[core]):
                        for name in groups[core]:
                            del redirects[name]
                        self._publish_migration_catalog(pin, self._catalog_record_for(pin), redirects)
                        if redirects:
                            self._timed_swap(pin, "mods")
                    if len(outcomes) >= SAVE_EVERY[0] or time.monotonic() - saved_at >= SAVE_EVERY[1]:
                        self._save_outcomes(outcomes)
                        outcomes.clear()
                        saved_at = time.monotonic()
                    line.done()
            finally:
                # an interrupted pass keeps what it reached; the next start skips entries that are official again
                self._migration_outcomes = None
                self._save_outcomes(outcomes)
        self._save_catalog_record(pin, {**self._catalog_record_for(pin), "migrated": now()})
        if planned:
            logger.info(
                "Moved %d entries to the catalog switch: %d redirects, placed in %.1f s, restored in %.1f s",
                len(groups),
                len(redirects),
                placed_at - started,
                time.perf_counter() - placed_at,
            )
        self._prune_due = True  # the kept originals of the entries moved are no longer needed

    def _swap_migration_catalog(
        self, pin: Pin, groups: dict[str, dict[str, Document]], redirects: dict[str, Document]
    ) -> None:
        def verify_entries() -> None:
            dropped = []
            for core, group in groups.items():
                self._let_others_in()
                with self._lock:
                    targets = self._spine_parts(core, pin)
                if not self._verify_migration_placements(pin, targets, group):
                    dropped.append(core)
            if dropped:
                raise ServiceError("entry_changed", cores=dropped)

        while redirects:
            try:
                self._timed_swap(pin, "mods", before_replace=verify_entries)
                return
            except ServiceError as error:
                if error.code != "entry_changed" or not error.params.get("cores"):
                    raise
                for core in error.params["cores"]:
                    for name in groups.pop(core):
                        del redirects[name]
                self._publish_migration_catalog(pin, self._catalog_record_for(pin), redirects)

    def _migration_catalog_available(self, pin: Pin, record: Document) -> bool:
        if not record["mods"]:
            return False
        live = self._catalog_paths(pin).live
        if self._live_kind(live, record) == "mods":
            return True
        try:
            verify_file(_side_copy(live, record["rv"], "mods"), record["mods"]["sha1"])
        except OSError:
            return False
        return True

    def _publish_migration_catalog(self, pin: Pin, record: Document, redirects: Mapping[str, Document]) -> None:
        if self._catalog_live(pin, record) == "mods":
            self._timed_swap(pin, "official")
        if redirects:
            self._generate_catalog(pin, redirects)
        else:
            self._save_catalog_record(pin, {**self._catalog_record_for(pin), "redirects": {}, "mods": None})

    def _verify_migration_placements(
        self, pin: Pin, targets: list[Path], saved: Mapping[str, Document]
    ) -> dict[str, Document]:
        if saved.keys() != {path.name for path in targets}:
            return {}
        verified = {}
        for target in targets:
            item = saved[target.name]
            try:
                _, stamp, match = self._placed_stamp(pin, item, full=True)
            except (OSError, InvalidStore, ServiceError):
                match = False
            if match:
                verified[target.name] = {**item, "size": stamp["size"], "mtime": stamp["mtime"]}
                continue
            sha1 = Path(item["placed"]).stem
            source = self._migration_source(target, item["source"], sha1)
            if source is None:
                return {}
            try:
                verified[target.name] = {**item, **self._place(pin, target.name, source, expected=sha1)}
            except ServiceError as error:
                if error.code in STOPPING:
                    raise
                return {}
        return verified

    def _migration_source(self, target: Path, source: str, sha1: str) -> Path | None:
        with self._lock:
            mod = self._records.get(source[4:]) if source.startswith("mod:") else None
            mod_files = self._paths(mod) if mod else []
        for candidate in (self.root / "built" / placed_name(sha1), *mod_files, target):
            if candidate.is_file() and self._hashes.get(candidate).sha1 == sha1:
                return candidate
        return None

    def _migration_placements(self, pin: Pin, targets: list[Path], entry: _Entry, enabled: bool) -> dict[str, Document]:
        source = entry.carried
        build_keys: dict[Path, str] = {}
        expected: dict[Path, str] = {}
        applied = False
        with self._lock.unlocked():
            if source.startswith(LIBRARY_SOURCES):
                applied = True  # every part below is what the game loads now: the record's bytes or the official part
                paths = {part.path: part for part in entry.parts}
                replacements = {}
                for target in targets:
                    part = paths[target]
                    if part.matched:
                        sha1 = part.record["sha1"]
                        prepared = self._migration_source(target, source, sha1)
                        if prepared is None:
                            return {}
                        build_keys[target] = part.record.get("build", "")
                    elif part.official:
                        prepared, sha1 = target, self._hashes.get(target).sha1
                    else:
                        return {}
                    replacements[target], expected[target] = prepared, sha1
            elif not enabled and entry.configured and entry.configured.startswith(LIBRARY_SOURCES):
                source = entry.configured
                replacements = self._built_replacements(pin, entry.core, source, targets, build_keys, placed=True)
                if replacements:
                    parts = self._view_built_parts(pin, entry.core)
                    expected = {
                        target: parts[target.name]["sha1"] if target.name in parts else self._hashes.get(prepared).sha1
                        for target, prepared in replacements.items()
                    }
            else:
                return {}
            if not replacements:
                return {}
            return self._place_entry(pin, replacements, source, build_keys, expected=expected, applied=applied)

    def _view_built_parts(self, pin: Pin, core: str) -> Document:
        folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
        return ((folder.get("built") or {}).get(core) or {}).get("parts") or {}

    def _restore_migrated_entry(self, pin: Pin, core: str, redirects: Mapping[str, Document]) -> bool:
        with self._lock:
            targets = self._spine_parts(core, pin)
            recorded = {
                path: item
                for path in targets
                if (item := self._applied.get(str(path))) and item.get("official") is not True
            }

        def check_live() -> None:
            record = self._catalog_record_for(pin)
            if self._catalog_live(pin, record) != "mods":
                raise ServiceError("catalog_changed")
            self._check_stopping(pin)

        check_live()
        try:
            verified = self._verify_migration_placements(pin, targets, redirects)
        except (OSError, InvalidStore):
            return False
        if not verified:
            return False
        if not recorded:
            return True
        expected = {path: self._hashes.get(path).sha1 for path in targets}
        if not all(
            item["sha1"] == Path(verified[path.name]["placed"]).stem == expected[path]
            for path, item in recorded.items()
        ):
            return True
        if any(
            expected[path] != Path(verified[path.name]["placed"]).stem and not self._is_official(path)
            for path in targets
            if path not in recorded
        ):
            return True

        def check_placements() -> None:
            check_live()
            try:
                if any(not self._placed_stamp(pin, item, full=True)[2] for item in verified.values()):
                    raise ServiceError("entry_changed")
            except (OSError, InvalidStore) as error:
                raise ServiceError("entry_changed") from error

        try:
            originals = {path: self._official_for(path, pin) for path in targets}
            self._commit(
                originals,
                "official",
                lambda _: None,
                pin=pin,
                prune=False,
                expected=expected,
                before_write=check_placements,
                # every replaced file is a recorded one whose bytes are its verified placed copy, or official
                replaced_kept=True,
            )
        except ServiceError as error:
            if error.code == "entry_changed":
                return False
            if error.code != "official_unavailable":
                raise
            self._migration_outcome(pin, core, "stuck")
        else:
            self._migration_outcome(pin, core, None)
        return True

    def _migration_outcome(self, pin: Pin, core: str, why: str | None) -> None:
        """Note an entry the move to the catalog finished: in the pass's batch, else saved at once."""
        if self._migration_outcomes is None:
            self._save_outcomes({(pin.key, core): why})
        else:
            self._migration_outcomes[(pin.key, core)] = why

    def _catalog_start_due(self) -> bool:
        """Check the known installs' versions and live catalog stats before scheduling class 3 work."""
        enabled = profiles.enabled(self.profiles.read())
        records = self._catalog_records_for_read()
        if any((self.root / CATALOG_JOURNALS).glob("*/journal.json")):
            return True
        for pin in self.installs():
            record = records.get(pin.key, {"mode": MODE_FILES})
            rv = self.resource_version(pin)
            if self._catalog_migration_due(pin, record, rv) or self._catalog_version_changed(record, rv):
                return True
            if (
                enabled
                and record["mode"] == MODE_CATALOG
                and record.get("reapply")
                and not self._catalog_check_pending(pin, rv)
            ):
                return True
            if record.get("official") and record["rv"] == rv and self._catalog_stamp_changed(pin, record):
                return True
        return False

    def _catalog_reload_due(self, pins: list[Pin]) -> bool:
        records = self._catalog_records_for_read()
        return any(
            self._catalog_version_changed(records.get(pin.key, {"mode": MODE_FILES}), self.resource_version(pin))
            for pin in pins
        )

    def _catalog_stamp_changed(self, pin: Pin, record: Document) -> bool:
        try:
            stat = self._catalog_paths(pin, recovering=True).live.stat()
        except (OSError, ServiceError):
            return True
        stamp = record.get(record["live"])
        return stamp is None or not _matches_stat(stat, stamp)

    @staticmethod
    def _catalog_version_changed(record: Document, rv: str | None) -> bool:
        return record["mode"] == MODE_CATALOG and rv is not None and rv != record["rv"]

    def _refresh_catalog_version(self, pin: Pin) -> Document:
        """Accept a changed catalog version in a class 2 or 3 step without swapping.

        Validation and copies hold the transaction lock. Only the profile markers and store save take the
        library lock. A write refusal leaves the version for the next attempt.
        """
        record = self._catalog_record(pin)
        rv = self.resource_version(pin)
        if not self._catalog_version_changed(record, rv):
            return record
        with _errors("refresh catalog"), self._catalog_transaction(), self._lock.unlocked():
            record = self._catalog_record(pin)
            rv = self.resource_version(pin)
            if not self._catalog_version_changed(record, rv):
                return record
            try:
                return self._keep_official_catalog(pin)
            except ServiceError as error:
                if error.code in _GUARD_REFUSALS and self.resource_version(pin) == rv:
                    self._catalog_refusal(pin, rv, error)
                    return self._catalog_record(pin)
                raise

    def _check_catalog_at_start(self, pin: Pin) -> bool:
        """Check a changed stamp in class 3, locking only for saves and yielding between installs."""
        with _errors("check live catalog"):
            record = self._catalog_record(pin)
            if not record.get("official") or record["rv"] != self.resource_version(pin):
                return False
            if not self._catalog_stamp_changed(pin, record):
                return False
            with self._catalog_transaction(), self._lock.unlocked():
                record = self._catalog_record_for(pin)
                live = self._catalog_paths(pin, recovering=True).live
                revision = _file_revision(live)
                sha1 = digest(live.resolve(), "sha1") if revision is not None else None
                self._check_catalog_revision(pin, live, record["rv"], revision)
                for kind in ("official", "mods"):
                    if record[kind] and sha1 == record[kind]["sha1"]:
                        self._save_catalog_live(pin, live, record, kind)
                        return False
                if sha1 is not None and (record["live"] != "mods" or self._other_official_catalog(live)):
                    return False
                self._swap_catalog(pin, "official", repair=True, revision=revision)
                self._catalog_restored(pin)
                return True

    def _catalog_restored(self, pin: Pin) -> None:
        self._catalog_restores.add(pin.key)
        log_service_error(logger, ServiceError("catalog_restored"))

    def _check_catalog_revision(self, pin: Pin, live: Path, rv: str, revision: tuple[int, ...] | None) -> None:
        self._check_idle([pin])
        if self.resource_version(pin) != rv or _file_revision(live) != revision:
            raise ServiceError("catalog_changed")

    def _catalog_targets(self, pin: Pin, targets: list[Path], record: Document | None = None) -> bool:
        """Check a core's whole set of targets against the kept catalog's load-path IDs."""
        record = self._catalog_records_for_read().get(pin.key, {"mode": MODE_FILES}) if record is None else record
        if record["mode"] != MODE_CATALOG or not targets or record["rv"] != self.resource_version(pin):
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

    def _placed_stamps(self, pin: Pin) -> Callable[[Document, bool], tuple[Document, bool]]:
        """Give a reader of placed files' stamps that resolves each placed folder once.

        The reader takes a redirect and whether a changed date may be settled by the file's SHA-1, and gives what
        ``_placed_stamp`` gives without the path. A placed folder that resolves elsewhere, or a placed file that is a
        link, is refused as there. It reads no bundle; a pass over every redirect of an install is one short step.
        """
        bundles = self._catalog_paths(pin, recovering=True).mods_dir.parent
        folders: dict[str, Path] = {}

        def stamp_of(redirect: Document, full: bool) -> tuple[Document, bool]:
            folder = folders.get(redirect["dir"])
            if folder is None:
                folder = bundles.parent.parent / redirect["dir"] / MODS_DIR
                if folder.resolve() != folder:
                    raise ServiceError("game_file_outside_resources", file=folder)
                folders[redirect["dir"]] = folder
            path = folder / redirect["placed"]
            stat = os.lstat(path)
            if not S_ISREG(stat.st_mode):
                raise ServiceError("game_file_outside_resources", file=path)
            match = _matches_stat(stat, redirect)
            if not match and full and stat.st_size == redirect["size"]:
                match = digest(path, "sha1") == path.stem
            return {"sha1": path.stem, "size": stat.st_size, "mtime": stat.st_mtime_ns}, match

        return stamp_of

    @toggle_timing.stage("place")
    def _place(self, pin: Pin, name: str, source_file: Path, *, expected: str | None = None) -> Document:
        """Place one prepared bundle during a class 2 entry step under the library and transaction locks.

        Copy and verify one bundle; the caller yields the library lock at the next entry boundary.
        """
        with _errors("place bundle"):
            fingerprint = self._hashes.get(source_file)
            if fingerprint.trailing is None:
                raise ServiceError("bundle_invalid", file=source_file)
            sha1 = fingerprint.sha1 if expected is None else expected
            if fingerprint.sha1 != sha1:
                raise ServiceError("entry_changed", file=source_file)
            placed = placed_name(sha1)
            redirect = {"placed": placed, "dir": "GameData/Windows"}
            path = self._placed_path(pin, redirect)
            if path.resolve() != path:
                raise ServiceError("game_file_outside_resources", file=path)
            devmode.check_write(path)
            try:
                verify_file(path, sha1)
            except OSError:
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    if not atomic_clone(source_file, temporary):
                        atomic_copy(source_file, temporary)
                    try:
                        verify_file(temporary, sha1)
                    except OSError as error:
                        raise ServiceError("entry_changed", file=source_file) from error
                    self._check_stopping(pin)
                    filelock.replace(temporary, path)
                    sync_directory(path.parent)
                finally:
                    temporary.unlink(missing_ok=True)
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
        except (OSError, InvalidStore, ServiceError):
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
        self,
        pin: Pin,
        core: str,
        targets: list[Path],
        source: str,
        resource_type: str,
        *,
        record: Document | None = None,
        stamp_of: Callable[[Document, bool], tuple[Document, bool]] | None = None,
    ) -> dict[str, Document] | None:
        """Check a class 2 entry's placed files and current build under the library lock for one core.

        A placed file is reused when it carries a current build key, or when its bytes are what a mod made for these
        parts places as it is: the mod's own file of the target's name, else the target's official copy (compared by
        the SHA-1 in the placed name against the hash cache, no bundle read). Such a file does not depend on the
        program that placed it. ``record`` and ``stamp_of`` let a pass over many entries read them once.
        """
        record = self._catalog_record_for(pin) if record is None else record
        saved = {target.name: record["redirects"].get(target.name) for target in targets}
        if any(not item or item["source"] != source for item in saved.values()):
            return None
        own: dict[str, Path] | None = None
        builds: set[str] | None = None
        if source.startswith("mod:"):
            mod = self._records.get(source[4:])
            if mod is None:
                return None
            if mod["resource_type"] in ("other", "chibi") or core_of(mod["files"][0]) == core:
                own = {path.name: path for path in self._paths(mod)}
        elif not source.startswith("alt:"):
            return None
        stamp_of = self._placed_stamps(pin) if stamp_of is None else stamp_of
        redirects = {}
        for target in targets:
            redirect = saved[target.name]
            if not (own is not None and self._placed_as_is(pin, target, redirect, own)):
                if builds is None:
                    builds = self._redirect_builds(pin, core, targets, source, resource_type)
                if not any(self._build_fits(redirect["build"], build) for build in builds):
                    return None
            try:
                stamp, match = stamp_of(redirect, True)
            except (OSError, ServiceError):
                return None
            if not match:
                return None
            redirects[target.name] = {**redirect, "size": stamp["size"], "mtime": stamp["mtime"]}
        return redirects

    def _redirect_builds(self, pin: Pin, core: str, targets: list[Path], source: str, resource_type: str) -> set[str]:
        """Give the build keys a placement of ``source`` over a core's parts signs now (none when it cannot be told)."""
        if source.startswith("mod:"):
            return self._current_builds(pin, core, self._record(source[4:]), targets)
        try:
            inputs = self._switch_alt_sources(pin, source[4:], resource_type)
            builder = self._costume_builder() if resource_type == "chibi" else None
            return {self._build_signature(inputs, targets, pin, builder=builder)}
        except (OSError, ServiceError):
            return set()

    def _placed_as_is(self, pin: Pin, target: Path, redirect: Document, own: Mapping[str, Path]) -> bool:
        """Tell whether a placed file holds the bytes a mod made for this part places unchanged.

        Those are the mod's own file of the target's name, or the target's official copy when the mod has no file
        of that name. Hashes come from the cache by file revision; a file that cannot be read is no match.
        """
        try:
            sha1 = Path(redirect["placed"]).stem
            origin = own.get(target.name)
            if origin is None:
                # the game file itself is the official copy while nothing wrote over it
                if self._hashes.get(target).sha1 == sha1 and self._untouched_official(target):
                    return True
                origin = self._official_for(target, pin)
            return self._hashes.get(origin).sha1 == sha1
        except (OSError, ServiceError):
            return False

    def _place_entry(
        self,
        pin: Pin,
        replacements: Mapping[Path, Path],
        source: str,
        build_keys: Mapping[Path, str],
        *,
        expected: Mapping[Path, str] | None = None,
        applied: bool = False,
    ) -> dict[str, Document]:
        """Place one class 2 entry under the library lock, yielding at the caller's next entry boundary.

        ``applied``: the bytes are the ones the game already loads under the official names (a move to the catalog),
        so their references are not read again; the placement changes nothing of what the game sees.
        """
        self._check_stopping(pin)
        for target in replacements:
            check_resource_path(target, list(pin.dirs))
        if not applied:
            self._check_references(replacements, pin)
        return {
            target.name: {
                **self._place(pin, target.name, prepared, **({"expected": expected[target]} if expected else {})),
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
        keep_official: bool = False,
    ) -> bool:
        """Publish the configured redirects in a class 2 transaction.

        Snapshot configuration under the library lock. Verification, generation and each atomic swap leave
        the lock free for foreground reads. Each catalog store save takes it briefly. Return whether the
        live catalog changed. ``keep_official``: an official live catalog stays live (mods are off); the redirect
        set is generated beside it for the next On.
        """
        staged = staged or {}
        with self._catalog_transaction():
            with self._lock:
                record = self._catalog_record_for(pin)
                if self._catalog_check_pending(pin, record["rv"]):
                    raise ServiceError("catalog_changed")
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
                stamp_of = self._placed_stamps(pin)
                for group in groups:
                    try:
                        if all(stamp_of(item, True)[1] for item in group.values()):
                            redirects.update(group)
                    except (OSError, ServiceError):
                        if group.keys() & staged.keys():
                            raise
                if not staged.keys() <= redirects.keys():
                    raise ServiceError("entry_changed")
                placed = [item["placed"] for item in redirects.values()]
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
                    if len(set(placed)) != len(placed):
                        error = ServiceError("catalog_invalid", guard="ambiguous")
                        self._catalog_refusal(pin, record["rv"], error)
                        raise error
                    with toggle_timing.stage("generate"):
                        self._generate_catalog(pin, redirects)
                elif redirects != record["redirects"]:
                    self._save_catalog_record(pin, {**record, "redirects": redirects})
                if keep_official and current == "official":
                    return False
                self._timed_swap(pin, "mods")
                return changed or current == "official"

    def _timed_swap(self, pin: Pin, want: CatalogKind, *, before_replace: Callable[[], None] | None = None) -> None:
        """Run a class 2 or 3 swap outside the library lock, apart from its store save."""
        with self._lock.unlocked(), toggle_timing.stage("swap"):
            self._swap_catalog(pin, want, **({"before_replace": before_replace} if before_replace else {}))

    def _prune_catalogs(self, pins: list[Pin], installs: Mapping[str, Document]) -> None:
        """Prune owned copies in class 4, leaving the library lock free for game-side inspection and deletion."""
        for pin in pins:
            record = installs.get(pin.key)
            if not record or pin.key in self._catalog_recovery_blocked:
                continue
            with self._lock.unlocked():
                try:
                    self._prune_install_catalog(pin, record)
                except (OSError, ValueError, ServiceError, ccdb.CatalogFormatError) as error:
                    logger.info("Catalog cleanup deferred for %s: %s", pin.root, error)

    def _prune_install_catalog(self, pin: Pin, record: Document) -> None:
        paths = catalog_paths(pin, allow_missing=True)
        if paths is None or record["rv"] != self.resource_version(pin):
            return
        live = paths.live
        revision = _file_revision(live)
        if revision is None:
            return
        self._check_stopping(pin)
        if record.get("refused_sha1") and digest(live, "sha1") == record["refused_sha1"]:
            referenced = set()
        else:
            referenced = self._foreign_placements(live, record, live.stat())
        self._check_catalog_revision(pin, live, record["rv"], revision)
        kept = {self._placed_path(pin, item) for item in record["redirects"].values()}
        side = live.parent / MODS_DIR
        library_dir = self._catalog_library_copy(pin, record["rv"]).parent
        for directory in [side, *(folder / MODS_DIR for folder in pin.dirs), library_dir]:
            if directory.resolve() != directory:
                continue
            for path in directory.iterdir() if directory.is_dir() else ():
                if directory == library_dir:
                    stale = (
                        _RESOURCE_VERSION.fullmatch(path.stem)
                        and path.suffix == ".bytes"
                        and (not record.get("official") or path.stem != record["rv"])
                    )
                elif directory == side:
                    match = _SIDE_COPY.fullmatch(path.name)
                    stale = match and (not record.get("official") or match[1] != record["rv"][:12])
                else:
                    stale = _PLACED.fullmatch(path.name) and path not in kept and path.name not in referenced
                if not stale or path.is_symlink() or not path.is_file():
                    continue
                if directory != library_dir:
                    self._let_others_in()
                    self._check_stopping(pin)
                self._check_catalog_revision(pin, live, record["rv"], revision)
                devmode.check_write(path)
                path.unlink(missing_ok=True)

    def _foreign_placements(self, live: Path, record: Document, stat: stat_result) -> set[str]:
        if record.get("official") and self._live_kind(live, record, stat) is not None:
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
        """Give an install's catalog paths, checked to lie inside it.

        The layout and its path checks are kept for ``_PATHS_KEPT`` seconds per install: a pass over every redirect
        asks once per file. Whether the live catalog is there is read each time.
        """
        kept = self._catalog_paths_kept.get(pin.key)
        if kept is None or kept[0] != pin or time.monotonic() - kept[1] > _PATHS_KEPT:
            devmode.check_read(pin.root)
            paths = catalog_paths(pin, allow_missing=True)
            if paths is None:
                raise ServiceError("catalog_unsupported")
            check_resource_path(paths.live, [pin.root])
            self._catalog_paths_kept[pin.key] = (pin, time.monotonic(), paths)
        else:
            paths = kept[2]
        if not recovering and not paths.live.is_file():
            raise ServiceError("catalog_changed")
        return paths

    def _catalog_record_for(self, pin: Pin) -> Document:
        if pin.key in self._catalog_recovery_blocked:
            raise ServiceError("catalog_changed")
        record = self._catalog_record(pin)
        if not record.get("official") or record["rv"] != self.resource_version(pin):
            raise ServiceError("catalog_changed")
        return record

    def _keep_official_catalog(self, pin: Pin) -> Document:
        """Keep official bytes in class 2 or 3, locking the library only for record saves."""
        with _errors("keep official catalog"), self._transaction_lock():
            if pin.key in self._catalog_recovery_blocked:
                raise ServiceError("catalog_changed")
            live = self._catalog_paths(pin).live
            with self._writable([live], pin, resource_dirs=[pin.root], catalog=(None, None)):
                rv = self.resource_version(pin)
                if rv is None:
                    raise ServiceError("catalog_unsupported")
                library_copy = self._catalog_library_copy(pin, rv)
                side_copy = _side_copy(live, rv, "official")
                devmode.check_write(side_copy)
                previous = self._catalog_record(pin)
                revision = _file_revision(live)
                raw = live.read_bytes()
                self._check_catalog_revision(pin, live, rv, revision)
                sha1 = hashlib.sha1(raw).hexdigest()
                official = previous.get("official")
                same_version = previous.get("rv") == rv and official is not None
                if official and (sha1 == official["sha1"]) != same_version:
                    raise ServiceError("catalog_changed")
                scan = self._scan_allowed()
                steam = self._steam_files.catalog(pin.root) if scan else None
                if steam is not None:
                    if (steam.size, steam.sha1) != (len(raw), sha1):
                        raise ServiceError("catalog_changed")
                elif scan and (
                    (official is not None and not same_version) or game_paths.steam_app_manifest(pin.root) is not None
                ):
                    raise ServiceError("catalog_changed")
                if not same_version and any(
                    item["mods"] and item["mods"]["sha1"] == sha1
                    for item in self._catalog_document()["installs"].values()
                ):
                    raise ServiceError("catalog_changed")
                try:
                    catalog = ccdb.parse(raw)
                    if _has_mod_ids(catalog):
                        raise ServiceError("catalog_changed")
                    if ccdb.write(catalog) != raw:
                        raise ccdb.CatalogFormatError(
                            "roundtrip", "Serializing the official catalog changed its bytes."
                        )
                    ccdb.bundle_ids(catalog)
                except ccdb.CatalogFormatError as error:
                    self._check_catalog_revision(pin, live, rv, revision)
                    # A partial update can look like a format error until Steam publishes its catalog.
                    if (
                        official
                        and not same_version
                        and steam is None
                        and error.reason in ("version", "trailing", "roundtrip")
                    ):
                        raise ServiceError("catalog_changed") from error
                    refusal = _guard_error(error)
                    if steam is not None or error.reason == "ambiguous":
                        refusal.params.update(official_sha1=sha1, revision=revision)
                    raise refusal from error
                if self._managed:
                    fence_catalog_stores(self.root)
                atomic_bytes(library_copy, raw)
                verify_file(library_copy, sha1)
                atomic_copy(library_copy, side_copy)
                verify_file(side_copy, sha1)
                self._check_catalog_revision(pin, live, rv, revision)
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
                if not same_version and previous["mode"] == MODE_CATALOG:
                    self._carry_catalog_update(pin, previous, record, catalog)
                self._check_catalog_revision(pin, live, rv, revision)
                record["official"] = {"sha1": sha1, "size": revision[2], "mtime": revision[3], "crc32": zlib.crc32(raw)}
                record["live"] = "official"
                self._save_catalog_record(pin, record)
                if not same_version and previous.get("rv"):
                    old_copy = self._catalog_library_copy(pin, previous["rv"])
                    with contextlib.suppress(OSError):
                        old_copy.unlink(missing_ok=True)
                        sync_directory(old_copy.parent)
                return record

    def _catalog_cores(self, pin: Pin, record: Document) -> dict[str, dict[str, Document]]:
        mapped = self._core_map(pin)
        cores: dict[str, dict[str, Document]] = {}
        for name, redirect in record.get("redirects", {}).items():
            cores.setdefault(mapped.slot(core_of(name)), {})[name] = redirect
        return cores

    def _mark_catalog_update(self, pin: Pin, previous: Document, cores: Iterable[str]) -> None:
        cores = set(cores)
        if not cores:
            return

        def change(data: dict) -> None:
            folder = self._folder(data, pin)
            stamp = now()
            if not folder.get("resource"):
                folder["resource"] = {"version": previous["rv"], "seen": stamp}
            for core in cores & folder["configured"].keys():
                folder["updated"][core] = stamp
                if isinstance(drift := folder["dismissed"].get("drift"), dict):
                    drift.pop(core, None)

        self._update_profiles(change)

    def _carry_catalog_update(self, pin: Pin, previous: Document, record: Document, catalog: ccdb.Catalog) -> None:
        """Record drift before publishing the new version, so an interruption can repeat the same transition."""
        names = ccdb.bundle_ids(catalog)
        with self._lock:
            groups = self._catalog_cores(pin, previous)
            pending = dict(previous.get("pending", {}))
            removed = dict(previous.get("removed", {}))
            for core, group in groups.items():
                source = next(iter(group.values()))["source"]
                valid = {name: item for name, item in group.items() if name in names and names[name][1] == _LOAD_PREFIX}
                record["redirects"].update(valid)
                if len(valid) == len(group) and core not in removed:
                    pending[core] = source
                else:
                    mod = self._records.get(source[4:]) if source.startswith("mod:") else None
                    removed[core] = {"source": source, "mark": self._impact_mark(mod) if mod else None}
                    pending.pop(core, None)
            self._mark_catalog_update(pin, previous, groups.keys() | pending.keys())
            record.update(pending=pending, reapply=bool(pending), removed=removed)

    def _catalog_needs_mod_update(self, record: Document, core: str, source: str | None) -> bool:
        removed = record.get("removed", {}).get(core)
        if not source or not removed or source != removed["source"]:
            return False
        mod = self._records.get(source[4:]) if source.startswith("mod:") else None
        return mod is None or self._impact_mark(mod) == removed["mark"]

    def _generate_catalog(self, pin: Pin, redirects: Mapping[str, Document]) -> Document:
        """Generate from recorded official bytes in a class 2 or 3 step under the transaction lock.

        Each redirect supplies its placed filename and store metadata. Generation leaves the library lock free
        for foreground reads and takes it for one store save.
        """
        with _errors("generate catalog"), self._transaction_lock():
            live = self._catalog_paths(pin).live
            with self._writable([live], pin, resource_dirs=[pin.root], catalog=(None, None)):
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
                    raise ServiceError("official_invalid", file=self._catalog_library_copy(pin, record["rv"]))
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

    def _swap_catalog(
        self,
        pin: Pin,
        want: CatalogKind,
        *,
        repair: bool = False,
        revision: tuple[int, ...] | None = None,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        """Swap catalogs in one non-yielding class 2 or 3 transaction step.

        Checking, cloning and replacing leave the library lock free for foreground reads. One store save holds it.
        """
        if want not in ("official", "mods"):
            raise ValueError("Unknown catalog swap destination")
        with _errors("swap catalog"), self._transaction_lock():
            if pin.key in self._catalog_recovery_blocked:
                raise ServiceError("catalog_changed")
            live = self._catalog_paths(pin, recovering=repair).live
            record = self._catalog_record(pin)
            rv = self.resource_version(pin)
            if not record.get("official"):
                raise ServiceError("catalog_changed")
            if record["rv"] != rv:
                if not repair and want == "official" and self._other_official_catalog(live):
                    if record["live"] != "official":
                        self._save_catalog_record(pin, {**record, "live": "official"})
                    return
                raise ServiceError("catalog_changed")
            library_copy = self._catalog_library_copy(pin, rv)
            official_copy = _side_copy(live, rv, "official")
            backup = library_copy if repair or library_copy.is_file() else official_copy
            with self._writable(
                [live], pin, recovering=repair, resource_dirs=[pin.root], catalog=(backup, record["official"]["sha1"])
            ):
                if not repair:
                    revision = _file_revision(live)
                self._check_catalog_revision(pin, live, rv, revision)
                current = ("mods" if record["mods"] else "official") if repair else self._live_kind(live, record)
                if want == "mods" and self._catalog_check_pending(pin, rv):
                    raise ServiceError("catalog_changed")
                if current is None:
                    if want == "official" and self._other_official_catalog(live):
                        if record["live"] != "official":
                            self._save_catalog_record(pin, {**record, "live": "official"})
                        return
                    raise ServiceError("catalog_changed")
                self._clean_catalog_temps(live)
                if current == want and not repair:
                    if before_replace:
                        before_replace()
                    self._check_catalog_revision(pin, live, rv, revision)
                    if record["live"] != want:
                        self._save_catalog_live(pin, live, record, want)
                    return
                if record[want] is None:
                    raise ServiceError("catalog_invalid")
                source = _side_copy(live, record["rv"], want)
                sha1 = record[want]["sha1"]
                if repair and revision is None:
                    source = library_copy
                elif want == "official":
                    try:
                        verify_file(source, sha1)
                    except OSError:
                        source = library_copy
                swap_id = uuid.uuid4().hex
                journal = self.root / CATALOG_JOURNALS / swap_id / "journal.json"
                transaction = {
                    "version": 1,
                    "rv": rv,
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
                if before_replace:
                    try:
                        before_replace()
                    except ServiceError:
                        self._close_catalog_journal(journal, live)
                        raise
                self._check_stopping(pin)
                self._check_catalog_revision(pin, live, rv, revision)
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
        self._catalog_recovery_blocked.clear()
        for journal in sorted(journals.glob("*/journal.json")):
            try:
                with self._lock.unlocked():
                    self._recover_catalog_journal(journal)
            except (OSError, ValueError, KeyError, TypeError, ServiceError) as error:
                logger.warning("Catalog swap recovery deferred for %s: %s", journal.parent.name, error)
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    pin = parse_json(journal.read_bytes())["pin"]["key"]
                    self._catalog_recovery_blocked.add(pin)

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
        relative_copy = Path(data["official"]["library_copy"])
        rv = data.get("rv", relative_copy.stem)
        library_copy = self._catalog_library_copy(pin, rv)
        if relative_copy != library_copy.relative_to(self.root):
            raise InvalidStore("Invalid catalog journal backup")
        live_rv = self.resource_version(pin)
        if live_rv is None:
            raise ServiceError("catalog_changed")
        if rv != live_rv or rv != record.get("rv"):
            self._check_stopping(pin)
            self._close_catalog_journal(journal, live)
            return
        self._check_stopping(pin)
        revision = _file_revision(live)
        sha1 = digest(live.resolve(), "sha1") if revision is not None else None
        self._check_catalog_revision(pin, live, rv, revision)
        want = data["to"]["kind"]
        if want not in ("official", "mods") or not record.get(want):
            raise InvalidStore("Invalid catalog journal destination")
        official_sha1 = record["official"]["sha1"]
        to_sha1, from_sha1 = data["to"]["sha1"], data["from"]["sha1"]
        if (
            data["official"]["sha1"] != official_sha1
            or to_sha1 != record[want]["sha1"]
            or from_sha1 not in {stamp["sha1"] for stamp in (record["official"], record["mods"]) if stamp}
        ):
            raise InvalidStore("Catalog journal fingerprints disagree with the store")
        self._clean_catalog_temps(live)
        if sha1 == to_sha1:
            self._save_catalog_live(pin, live, record, want)
        elif sha1 != from_sha1 and not self._other_official_catalog(live):
            with self._writable(
                [live], pin, recovering=True, resource_dirs=[pin.root], catalog=(library_copy, official_sha1)
            ):
                temporary = _temp_copy(live, journal.parent.name)
                temporary.unlink(missing_ok=True)
                self._prepare_catalog_swap(library_copy, temporary, live, official_sha1)
                self._check_stopping(pin)
                self._check_catalog_revision(pin, live, rv, revision)
                replace_file(temporary, live)
                sync_directory(live.parent)
                self._save_catalog_live(pin, live, record, "official")
            self._catalog_restored(pin)
        self._close_catalog_journal(journal, live)
        self._waiting.discard(journal.parent.name)

    def _other_official_catalog(self, live: Path) -> bool:
        # A game update replaces the catalog with a newer official one, which must stay in place.
        try:
            return not _has_mod_ids(_validate_official(live.read_bytes()))
        except (FileNotFoundError, ccdb.CatalogFormatError):
            return False
