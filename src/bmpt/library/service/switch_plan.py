"""Planning and writing a switch: staging, the transaction journals, recovery and the checks of a write."""

from __future__ import annotations

from ..records import stored_target

import contextlib
import hashlib
import errno
import tempfile
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Collection, Iterator, Mapping

from bmpt.core import devmode
from bmpt.engine import (
    Bundle,
    LogFunc,
    ProgressCallback,
    parse_filename,
    t,
)
from ...host.store_formats import CATALOG_JOURNALS, read_runtime
from ...core.errors import ServiceError
from ...core.filelock import file_lock
from ...core.logging_utils import log_failure
from .. import integrity, pointers, safety, toggle_timing
from ..bundle_crc import name_hash
from ..install import Action, PlannedFile, PlannedGroup, backup_file
from ..judge import (
    core_of,
    crc_state,
    now,
)
from ..records import Pin
from ..safety import check_resource_path
from ..storage import (
    atomic_clone,
    atomic_copy,
    atomic_json,
    read_tail,
    sync_directory,
    verified_move,
    verify_file,
)
from .base import POINTER_CACHE, RECOVERY_RETRY, _has_successor


logger = logging.getLogger(__package__)

# A layout lookup per referencing file: {CAB: {path id: class id}}, None for a CAB found nowhere.
_World = Callable[[Path], Callable[[str], dict[int, int] | None]]


class SwitchPlanMixin:
    """Planning and writing a switch: staging, the transaction journals, recovery and the checks of a write."""

    @toggle_timing.stage("entry_plan")
    def _plan(
        self,
        record: dict[str, Any],
        log: LogFunc,
        pin: Pin,
        *,
        stale: Mapping[Path, set[pointers.Pointer]] | None = None,
        check_references: bool = True,
    ) -> list[PlannedGroup]:
        """Plan install groups, demoting stale whole files before a build.

        Files updated into their parts carry their dangling references too (``_stale_updates``), so their clips keep
        the official ones. Comparisons only ask for targets, without reference reads; ``stale`` carries the
        references the whole-file shortcut already found dangling.
        """
        paths = self._paths(record)
        files = {path.name: path for path in paths}
        targets = (
            self._other_targets(files, pin)
            if record["resource_type"] == "other"
            else self._spine_parts(core_of(paths[0].name), pin)
        )
        stale = dict(stale or {})
        resolution = self._resolve(files, targets, pin, older=stale)
        more_stale = self._stale_sources(resolution.current, pin, targets=targets) if check_references else {}
        if more_stale:
            stale.update(more_stale)
            resolution = self._resolve(files, targets, pin, older=stale)
        current = resolution.current
        groups = {}
        inspection = integrity.Inspection()
        for target, path in current.items():
            if path.name != target.name:
                # A whole file under another name is written as it is: it must use the part's serialized format.
                integrity.check_format(
                    inspection.metadata(path), path, inspection.metadata(self._official_or_current(target, pin))
                )
            parsed = parse_filename(path.name)
            group = groups.setdefault(parsed.prefix, PlannedGroup(parsed.prefix, parsed.category, parsed.core, []))
            group.files.append(PlannedFile(path, parsed, Action.DIRECT_REPLACE, existing_path=target))
        planned = list(groups.values())
        older = {name: path for name, path in files.items() if path not in current.values()}
        if older:
            if record["resource_type"] == "other":
                planned.append(
                    PlannedGroup(
                        prefix="",
                        category=None,
                        core=stored_target(record),
                        files=[PlannedFile(path, parse_filename(name), Action.UPDATE) for name, path in older.items()],
                        target_paths=self._other_targets(older, pin),
                    )
                )
            else:
                planned.extend(self._groups_of(resolution))
        if check_references:
            updates = {
                file.mod_path: group.target_paths
                for group in planned
                for file in group.files
                if file.action == Action.UPDATE and file.mod_path not in stale
            }
            stale.update(self._stale_updates(updates, current, pin, targets=targets))
        if stale:
            for group in planned:
                for file in group.files:
                    if file.mod_path in stale:
                        file.stale_pointers = stale[file.mod_path]
        if not planned or any(
            file.action not in (Action.DIRECT_REPLACE, Action.UPDATE) for group in planned for file in group.files
        ):
            raise ServiceError("game_file_not_found", alt=stored_target(record))
        return planned

    @staticmethod
    def _plan_targets(groups: list[PlannedGroup]) -> list[Path]:
        return sorted(
            {
                path
                for group in groups
                for path in (
                    group.target_paths + [file.existing_path for file in group.files if file.existing_path is not None]
                )
            }
        )

    @toggle_timing.stage("stage")
    def _stage_official(
        self, paths: list[Path], directory: Path, pin: Pin, *, current_fallback: bool = False
    ) -> dict[Path, Path]:
        staged = {}
        directory.mkdir(parents=True, exist_ok=True)
        for path in paths:
            target = directory / path.name
            if target in staged.values():
                raise ServiceError("game_file_conflict", file=path)
            source = self._official_or_current(path, pin) if current_fallback else self._official_for(path, pin)
            if self._managed and self._is_kept(source):
                self._copy_verified_official(source, target)
            else:
                atomic_copy(source, target)
            staged[path] = target
        return staged

    @toggle_timing.stage("stage")
    def _stage_direct(
        self,
        targets: list[Path],
        sources: dict[str, Path],
        directory: Path,
        pin: Pin,
        updating: set[Path],
        *,
        placed: bool,
    ) -> dict[Path, Path]:
        """Stage whole-file replacements and omitted parts without requiring an official base.

        Asset transfers and CRC correction for hash-named targets require verified official targets.
        """
        directory.mkdir(parents=True, exist_ok=True)
        staged = {}
        for path in targets:
            prepared = directory / path.name
            if prepared in staged.values():
                raise ServiceError("game_file_conflict", file=path)
            try:
                source = self._official_for(path, pin)
            except ServiceError as error:
                needs_official_crc = not placed and name_hash(path.name) and self.settings.crc_needed(path)
                if error.code != "official_unavailable" or path in updating or needs_official_crc:
                    raise
                source = sources.get(path.name) or path
            if self._managed and self._is_kept(source):
                self._copy_verified_official(source, prepared)
            else:
                atomic_copy(source, prepared)
            staged[path] = prepared
        return staged

    def _commit(
        self,
        replacements: dict[Path, Path],
        source: str,
        log: LogFunc,
        progress: ProgressCallback | None = None,
        pin: Pin | None = None,
        prune: bool = True,
        build_keys: dict[Path, str] | None = None,
        create_backup: bool = False,
        expected: dict[Path, str] | None = None,
        kept_current: list[str] | None = None,
        untested: bool = True,
        before_write: Callable[[], None] | None = None,
        replaced_kept: bool = False,
    ) -> list[Path]:
        """Write prepared files over game files as one unit, keeping what they replace.

        ``replaced_kept``: the caller verified that every replaced file's bytes are kept elsewhere (a placed copy),
        so none is looked for again.

        That is the official file in originals/ (once) and a file kept nowhere else as a library mod (_keep_replaced).
        Every written part is rolled back on failure. With ``prune``, unneeded kept originals are deleted afterwards,
        whatever the outcome; bulk operations prune once at their end. ``untested``: a write of another source than
        official onto a core an untested build changed needs the user's confirmation (``_check_untested``); False for a
        rollback that puts back what was there.
        """
        pin = self._current(pin)
        deletion = getattr(self, "_asset_deletion", None)
        if deletion:
            deletion.remember_game(replacements, pin)
        try:
            with toggle_timing.stage("restore" if source == "official" else "write"), self._transaction_lock():
                return self._write(
                    replacements,
                    source,
                    log,
                    progress,
                    pin,
                    build_keys,
                    create_backup=create_backup,
                    untested=untested,
                    **({"before_write": before_write} if before_write else {}),
                    **({"expected": expected} if expected else {}),
                    **({"kept_current": kept_current} if kept_current is not None else {}),
                    **({"replaced_kept": True} if replaced_kept else {}),
                )
        finally:
            if prune:
                self._prune_quietly()

    @contextlib.contextmanager
    def _writable(
        self,
        paths: list[Path],
        pin: Pin,
        *,
        recovering: bool = False,
        resource_dirs: list[Path] | None = None,
        catalog: tuple[Path | None, str | None] | None = None,
    ) -> Iterator[None]:
        """Run the checks right before game files are written.

        They are: the game not running, Steam not updating or verifying the install, the files inside its resource
        folders and lockable, and the caller's own check (write_check).
        """
        if self._write_check is not None:
            self._write_check(pin)
        self._check_idle([pin])
        legacy_gate = contextlib.nullcontext()
        if catalog is not None:
            if self._catalog_write_gate is not None:
                legacy_gate = self._catalog_write_gate(*catalog)
        elif self._game_write_gate is not None:
            legacy_gate = self._game_write_gate(paths, pin.root, self._is_official_as)
        with (
            toggle_timing.enter(
                "game_file_preflight",
                safety.writable_game_files(
                    paths, list(pin.dirs) if resource_dirs is None else resource_dirs, allow_missing=recovering
                ),
            ),
            toggle_timing.enter("legacy_write_gate", legacy_gate),
        ):
            yield

    @contextlib.contextmanager
    def _transaction_lock(self) -> Iterator[None]:
        """Take the transaction lock, before the mods, state and profiles locks.

        Nested writes already hold this process lock. Taking the lock recovers every pending journal first, every time,
        and raises the real failure when one cannot be rolled back (a write never goes on over a journal it could not
        settle); the failure is remembered for the reads and for the writes that do not take this lock
        (``_check_recovery``).

        A writer waiting for a catalog transaction in this process gives up the library lock for foreground
        reads and for the catalog's store save. That wait does not count against the interprocess lock timeout.
        A writer whose locked preparation may have changed must start over.
        """
        owner = threading.get_ident()
        if self._transaction_owner == owner:
            yield
            return
        prepared_version = None
        while not self._catalog_idle.is_set():
            version = self._version
            with self._lock.unlocked() as released:
                self._catalog_idle.wait()
            if released:
                if self._version != version:
                    raise ServiceError("state_changed")
                prepared_version = version
        with toggle_timing.enter("transaction_wait", file_lock(self.root / "transactions.lock", timeout=10)):
            self._transaction_owner = owner
            try:
                self._refresh_state(migrate=False)  # nothing is saved before the journals are settled
                try:
                    self._recover_transactions()
                except Exception as error:
                    self._remember_recovery(error)
                    raise
                self._remember_recovery(None)
                self._check_writable()
                if prepared_version is not None and self._version != prepared_version:
                    raise ServiceError("state_changed")
                yield
            finally:
                self._transaction_owner = None

    def _journals(self) -> tuple[str, ...]:
        """The pending game, import, catalog and deletion journals under the library, as sorted paths."""
        return tuple(
            sorted(
                str(path)
                for folder in ("transactions", "import-transactions", CATALOG_JOURNALS, "asset-deletions")
                for path in (self.root / folder).glob("*/journal.json")
            )
        )

    def _recover_pending(self) -> None:
        """Recover before indexing game files, including one moved away just before a crash.

        Raises the failure of a journal that cannot be settled (it stays pending); nothing happens without a journal.
        """
        if self._transaction_owner:
            return
        if self._journals():
            with self._transaction_lock():
                pass
        else:
            self._recovery_error = self._recovery_seen = self._recovery_logged = None

    def _recover_for_read(self) -> None:
        """Attempt recovery before a read, never failing it.

        A failure is logged once, remembered (with the pending journals and the time) and the read goes on. A read tries
        again only when the set of journals changed or RECOVERY_RETRY seconds have passed; a retry that finds other
        journals still pending but raises nothing follows the same limit. Writes recover on their own
        (``_transaction_lock``).
        """
        if self._transaction_owner:
            return
        journals = self._journals()
        if not journals:
            self._recovery_error = self._recovery_seen = self._recovery_logged = None
            return
        if any(Path(path).parent.parent.name == CATALOG_JOURNALS for path in journals):
            return  # Catalog recovery belongs to startup and writers, where it can run outside the library lock.
        seen = self._recovery_seen
        if seen is not None and seen[0] == journals and time.monotonic() - seen[1] < RECOVERY_RETRY:
            return
        # the attempt is noted first: a lock another process holds (its own write) must not hold up every read
        self._recovery_seen = (journals, time.monotonic())
        try:
            with self._transaction_lock():
                pass
        except Exception:
            pass  # _remember_recovery logs it and makes it a ServiceError; the read goes on

    def _remember_recovery(self, error: Exception | None) -> None:
        """Note the outcome of a recovery attempt.

        That is the failure writes refuse with (None: it succeeded), and the journals still pending with the time, which
        limit how often a read tries again.
        """
        journals = self._journals()
        self._recovery_seen = (journals, time.monotonic()) if journals else None
        if error is None:
            self._recovery_error = self._recovery_logged = None
            return
        if isinstance(error, ServiceError):
            failure = error
        elif isinstance(error, OSError):
            failure = ServiceError("library_io_error", file=error.filename)
        else:
            failure = ServiceError("library_operation_failed", operation="recover")
        self._recovery_error = failure
        if self._recovery_logged != (failure.code, journals):
            self._recovery_logged = (failure.code, journals)
            log_failure(logger, logging.WARNING, "Mod recovery pending, reads continue", error)

    def _check_recovery(self) -> None:
        """Refuse a change of state or configuration while a recovery has failed, with that failure.

        Recovery is attempted again first (a closed game, a repaired file), so the refusal is the real, current one. Not
        during a recovery itself or the writes under the transaction lock, which recovered before they started.
        """
        if self._recovery_error is not None and not self._transaction_owner:
            self._recover_pending()

    def _check_writes(self) -> None:
        """Gate every change of the configuration (``Profiles.gate``).

        It is refused with the real error while a recovery has failed (``_check_recovery``) or the state cannot be
        written (``_check_writable``), because a library that reads an empty state would judge every source gone and
        settle that into profiles.json.
        """
        self._check_recovery()
        self._check_writable()

    def _writes_refused(self) -> bool:
        """Tell whether changes of state and configuration are refused now, without trying again.

        That is when a recovery has failed or the state cannot be written. It is for the reads that settle something on
        the side and skip it instead.
        """
        return self._state_error is not None or (self._recovery_error is not None and not self._transaction_owner)

    def state_unreadable(self) -> bool:
        """Check whether the library lacks state from a good read, retrying an unreadable state first.

        Without good state, reads describe an empty library. Callers must not use
        that empty result as a baseline for the user's stored library.
        """
        with self._lock:
            if not self._state_good:
                self._refresh_state()
            return not self._state_good

    @toggle_timing.stage("journal_recovery")
    def _recover_transactions(self) -> None:
        """Settle imports, write journals, catalog swaps, then asset deletions under the transaction lock.

        A failure (ServiceError) leaves the journal and stops here; a write journal whose install is not known now waits
        (skipped, logged once). A game file changed outside the library is left as it is (``_rollback_write``).
        """
        if self._managed:
            from ...host.store_formats import InvalidStore
            from ...host.upgrade_journals import verify_journals

            try:
                verify_journals(self.root, self._records, check_game=True)
            except (OSError, InvalidStore) as error:
                raise ServiceError("library_recovery_pending", file=self.root) from error
        self._recover_imports()
        for path in sorted((self.root / "transactions").glob("*/journal.json")):
            try:
                data = read_runtime(path)
                if (
                    data["version"] != 2
                    or data["phase"] not in ("preparing", "writing", "committed")
                    or not path.resolve().is_relative_to(self.root / "transactions")
                ):
                    raise ValueError("Invalid write journal")
                if data["phase"] == "committed":
                    shutil.rmtree(path.parent)
                    continue
                info = data["pin"]
                pin = self._pin(Path(info["root"]), info["server"])
                if pin is None or pin.key != info["key"]:
                    # The install is not known now (its folder moved, the settings name another). The journal waits
                    # with its files; writes to other installs go on (a rollback restores only its own keys, and
                    # _prune refuses while a journal exists).
                    if path.parent.name not in self._waiting:
                        self._waiting.add(path.parent.name)
                        logger.warning(
                            "Write journal %s waits for its install, which is not known now", path.parent.name
                        )
                    continue
                # Another resource subfolder or a moved game folder is no reason to stop, if every target still lies in
                # a resource folder of the install; otherwise the folders changed.
                dirs_changed = list(map(str, pin.dirs)) != info["dirs"]
                targets = []
                for i, item in enumerate(data["files"]):
                    target, backup = Path(item["target"]), self.root / item["rollback"]
                    try:
                        check_resource_path(target, list(pin.dirs))
                    except ServiceError as error:
                        if dirs_changed:
                            raise ServiceError("folder_changed") from error
                        raise
                    expected = self._original_path(target) if item["official"] else path.parent / f"{i}.bundle"
                    if backup != expected or not backup.resolve().is_relative_to(self.root):
                        raise ValueError("Invalid rollback path")
                    if item["restore"] and self.root / item["restore"] != self._original_path(target):
                        raise ValueError("Invalid original path")
                    targets.append(target)
                if self._managed:
                    targets = [
                        target
                        for target, item in zip(targets, data["files"])
                        if not target.exists() or self._hashes.get(target).sha1 != item["before"]
                    ]
                with self._writable(targets, pin, recovering=True):
                    left = self._rollback_write(data, pin)
                self._release_failed(
                    [Path(item["target"]) for item in data["files"] if item["kept_now"] and item["target"] not in left],
                    pin,
                )
                self._drop_mods(self._droppable(data["kept"], left))
                shutil.rmtree(path.parent)
                self._waiting.discard(path.parent.name)
                logger.info("Recovered interrupted library write %s", path.parent.name)
            except (ValueError, TypeError, KeyError) as error:
                raise ServiceError("library_io_error", file=path) from error
        self._recover_catalog()
        from ..asset_delete import recover

        recover(self)

    def _droppable(self, kept: list[str], left: set[str]) -> list[str]:
        """Pick the mods a rolled back write kept from game files that are to be forgotten.

        Not the ones kept from a file left as it is (changed outside the library), whose bytes are in no game file
        again.
        """
        if not left:
            return list(kept)
        names = {Path(key).name for key in left}
        return [mod_id for mod_id in kept if not names & set((self._records.get(mod_id) or {}).get("files", ()))]

    def _rollback_write(self, data: dict, pin: Pin) -> set[str]:
        """Return consumed originals before restoring mods. Repeating any completed step is safe.

        A game file whose bytes are neither the journal's before nor its after (a Steam repair, another tool) was
        changed outside the library: its bytes are left as they are, a warning is logged, the other files are
        restored and the rollback finishes. Returns the targets left that way. Its record is put back as it was,
        without a new modification time, so that the file is judged as the outside change it is.

        A target that is missing while a later-dated file of its part is in its folder was superseded by a game
        update: it is not restored (that would write the old name beside the new one), the journal's own record of it
        is dropped and its original stays. It is returned with the targets left, so that nothing it kept is released.
        """
        left: set[str] = set()
        superseded: set[str] = set()
        for item in reversed(data["files"]):
            target = Path(item["target"])
            if not target.exists() and _has_successor(self._index(pin), target):
                logger.warning("Game file %s was superseded by a game update, not restoring it", core_of(target.name))
                left.add(item["target"])
                superseded.add(item["target"])
                continue
            current = self._hashes.get(target).sha1 if target.exists() else None
            original = self.root / item["restore"] if item["restore"] else None
            if original is not None and not original.exists():
                if current != item["after"]:
                    raise ServiceError("official_unavailable", file=target)
                verified_move(target, original, item["after"])
                self._original_checks.remember_digest(original, item["after"])
                current = None
            if current == item["before"]:
                continue
            if current not in (None, item["after"]):
                logger.warning(
                    "Game file %s was changed outside the library, leaving it as it is", core_of(target.name)
                )
                left.add(item["target"])
                continue
            backup = self.root / item["rollback"]
            kept_backup = self._managed and self._is_kept(backup)
            if not kept_backup:
                verify_file(backup, item["before"])
            if (
                not self._managed
                and item["official"]
                and item["kept_now"]
                and not self._needed_elsewhere(target.name, {str(target.resolve())}, self._all_pins())
            ):
                verified_move(backup, target, item["before"])
            elif kept_backup:
                with tempfile.TemporaryDirectory(prefix="verified-official-", dir=self.root) as folder:
                    staged = Path(folder) / backup.name
                    self._copy_verified_official(backup, staged)
                    if self._hashes.get(staged).sha1 != item["before"]:
                        raise OSError(errno.EIO, "File verification failed", str(staged))
                    atomic_copy(staged, target)
            elif not atomic_clone(backup, target):
                atomic_copy(backup, target)
            verify_file(target, item["before"])
        # A failed state flush may already have published the new file. Merge it before undoing its records.
        self._refresh_state()
        for item in data["files"]:
            key = item["target"]
            old = data["old_applied"].get(key)
            if old is None or key in superseded:
                self._applied.pop(key, None)
                continue
            try:
                # A rollback copy has a new date, even though its bytes are unchanged. A file left as it is keeps the
                # record's own date: the stat check must not read the outside bytes as the recorded ones.
                mtime = None if key in left else Path(key).stat().st_mtime_ns
            except OSError:
                mtime = None
            self._applied[key] = {**old, "mtime": mtime} if mtime is not None else dict(old)
        if self._applied != self._base[2]:
            self._save()
        return left

    def _pointers_of(self, path: Path, *, log_level: int = logging.DEBUG) -> frozenset[pointers.Pointer]:
        """Give the cross-bundle pointers of a file (``pointers.external_pointers``), kept by its SHA-1.

        A file that is no readable bundle has none: it gives no evidence here, and the bundle checks judge it.
        """
        sha1 = self._hashes.get(path).sha1
        found = self._pointer_cache.get(sha1)
        if found is None:
            kept = self.checks.get("pointers", sha1)
            if kept is not None:
                found = frozenset(pointers.Pointer(cab, path_id, declared) for cab, path_id, declared in kept)
        if found is None:
            devmode.check_read(path)
            try:
                bundle = Bundle.load(path)
                if bundle is None:
                    raise ServiceError("bundle_invalid", file=path.name)
                found = frozenset(pointers.external_pointers(bundle.env))
            except Exception as error:  # noqa: BLE001 - an unreadable file gives no evidence
                log_failure(logger, log_level, f"Could not read the pointers of {path.name}", error)
                return frozenset()  # not kept: the read may succeed later
            self.checks.put("pointers", sha1, sorted([item.cab, item.path_id, item.declared] for item in found))
        if sha1 not in self._pointer_cache:
            self._pointer_cache[sha1] = found
            while len(self._pointer_cache) > POINTER_CACHE:
                self._pointer_cache.popitem(last=False)
        self._pointer_cache.move_to_end(sha1)
        return found

    def _layouts_of(self, path: Path) -> dict[str, dict[int, int]]:
        """Give the objects of each CAB of a file, {CAB: {path id: class id}}, from its headers.

        They are kept by the file's revision.
        """
        devmode.check_read(path)
        stat = path.stat()
        key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        layouts = self._layout_cache.get(key)
        if layouts is None:
            sha1 = self._hashes.get(path).sha1
            kept = self.checks.get("layouts", sha1)
            if kept is not None:
                layouts = {
                    cab: {int(path_id): kind for path_id, kind in objects.items()} for cab, objects in kept.items()
                }
            else:
                contents = integrity.metadata(path, inspection=integrity.Inspection())
                layouts = {cab: dict(objects) for cab, objects in contents.layout.items()}
                self.checks.put(
                    "layouts", sha1, {cab: {str(k): v for k, v in objects.items()} for cab, objects in layouts.items()}
                )
            self._layout_cache[key] = layouts
            while len(self._layout_cache) > POINTER_CACHE:
                self._layout_cache.popitem(last=False)
        self._layout_cache.move_to_end(key)
        return layouts

    def _officials(self, targets: Collection[Path], pin: Pin) -> dict[Path, Path | None]:
        """Give each target's official copy, None where none is available."""
        official: dict[Path, Path | None] = {}
        for target in dict.fromkeys(targets):
            try:
                official[target] = self._official_for(target, pin)
            except ServiceError as error:
                if error.code not in ("official_unavailable", "official_invalid"):
                    raise
                official[target] = None
        return official

    def _reference_worlds(
        self, official: Mapping[Path, Path | None], replacements: Mapping[Path, Path], pin: Pin, *, log_level: int
    ) -> tuple[_World, _World]:
        """Give the layouts a write's pointers are judged against, as (resulting, official) worlds.

        Each world maps a referencing file (a hint for the CAB index) to {CAB: {path id: class id}} lookups. The
        resulting world is the entry as the write leaves it: ``replacements`` ({target: prepared}) over the official
        copies of ``official``'s targets, the current file where a target has none. The official world holds the
        official copies alone. A CAB resolves within the entry first, and only outside it to the install
        (``CabIndex``); one found nowhere gives None, left to the missing-part checks.
        """
        index = self._cab_index(pin)

        def layouts(path: Path) -> dict[str, dict[int, int]]:
            try:
                return self._layouts_of(path)
            except (ServiceError, OSError, ValueError) as error:
                log_failure(logger, log_level, f"Could not read the object layout of {path.name}", error)
                return {}

        def world(files: Mapping[Path, Path | None]) -> _World:
            own: dict[str, Path] = {}
            for path in files.values():
                if path is not None:
                    own.update(dict.fromkeys(layouts(path), path))

            def layout_for(hint: Path) -> Callable[[str], dict[int, int] | None]:
                def layout(cab: str) -> dict[int, int] | None:
                    path = own.get(cab)
                    if path is None:
                        found = index.resolve({cab}, hint, exclude=official.keys())
                        path = Path(found[cab]) if cab in found else None
                    return layouts(path).get(cab) if path is not None else None

                return layout

            return layout_for

        resulting = {
            target: replacements.get(target, original if original is not None else target)
            for target, original in official.items()
        }
        return world(resulting), world(official)

    @toggle_timing.stage("pointer_check")
    def _broken_references(
        self, replacements: Mapping[Path, Path], pin: Pin, *, targets: Collection[Path] = ()
    ) -> dict[Path, set[pointers.Pointer]]:
        """Find the written files of ``replacements`` ({target: prepared}) that point at objects the game does not hold.

        That is a pointer into another bundle's CAB whose object is gone there or of a type the field cannot hold
        (``pointers.dangling``); the result is {target: those pointers}. ``targets`` completes a planned entry: its
        omitted parts use official copies where available, else the current files, as the write will
        (``_reference_worlds``). A file that is the target's official copy is not judged, and what the official copy
        itself points at in vain, against the official files, is not held against a file (the game's own data then has
        it too).
        """
        official = self._officials((*targets, *replacements), pin)
        written = [
            target
            for target, prepared in replacements.items()
            if official[target] is None or self._hashes.get(official[target]).sha1 != self._hashes.get(prepared).sha1
        ]
        if not written:
            return {}
        # The answer is a matter of the bytes involved and of the game files of one resource version: kept by them.
        version = self.resource_version(pin)
        key = None
        if version is not None:
            involved = sorted(
                (
                    target.name,
                    self._hashes.get(replacements[target]).sha1 if target in replacements else None,
                    self._hashes.get(official[target]).sha1 if official[target] is not None else None,
                    None if target in replacements or official[target] is not None else self._hashes.get(target).sha1,
                )
                for target in official
            )
            key = hashlib.sha1(repr((pin.key, version, involved)).encode()).hexdigest()
            kept = self.checks.get("references", key)
            if kept is not None:
                by_name = {target.name: target for target in written}
                return {
                    by_name[name]: {pointers.Pointer(cab, path_id, declared) for cab, path_id, declared in found}
                    for name, found in kept.items()
                    if name in by_name
                }
        log_level = logging.INFO if targets else logging.DEBUG
        current, baseline = self._reference_worlds(official, replacements, pin, log_level=log_level)
        broken: dict[Path, set[pointers.Pointer]] = {}
        complete = True
        for target in written:
            mine = self._pointers_of(replacements[target], log_level=log_level)
            # an unreadable file gives no pointers and is not kept: its entry's answer is not kept either
            complete = complete and self._hashes.get(replacements[target]).sha1 in self._pointer_cache
            found = pointers.dangling(mine, current(target))
            if found and official[target] is not None:
                found -= pointers.dangling(self._pointers_of(official[target], log_level=log_level), baseline(target))
            if found:
                broken[target] = found
        if key is not None and complete:  # a file that could not be read gives no answer to keep
            self.checks.put(
                "references",
                key,
                {
                    target.name: sorted([item.cab, item.path_id, item.declared] for item in found)
                    for target, found in broken.items()
                },
            )
        return broken

    def _stale_build(self, replacements: Mapping[Path, Path], pin: Pin) -> bool:
        """Tell whether a kept build points at objects absent from the game (``_broken_references``).

        A game update that rebuilt a bundle it points into, under the same CAB, leaves its inputs' and targets' names as
        they were. Such a copy is built again from the current files; a file that cannot be read keeps the copy (the
        write's own check reads it again).
        """
        try:
            broken = self._broken_references(replacements, pin)
        except (ServiceError, OSError, ValueError) as error:
            log_failure(logger, logging.DEBUG, "Could not read the references of a built copy", error)
            return False
        if broken:
            logger.info(
                "The built copy of %s points at objects the game no longer holds, rebuilding",
                ", ".join(sorted(target.name for target in broken)),
            )
        return bool(broken)

    def _stale_sources(
        self, current: Mapping[Path, Path], pin: Pin, *, targets: Collection[Path]
    ) -> dict[Path, set[pointers.Pointer]]:
        """Find dangling references by source file among whole replacements (``_broken_references``).

        ``current`` is {target: source}, the files a resolution writes whole. A game update rebuilt a bundle they point
        into, under the same CAB, and left the part's name, or its structure, as it was. The entry's full ``targets``
        supplies the official copies its write restores alongside them. They are files of an older release, updated into
        the part as it is now like any other; written whole, the write's own check would refuse them. A file that cannot
        be read stays as it is (that check reads it again).
        """
        if not current:
            return {}
        try:
            broken = self._broken_references(current, pin, targets=targets)
        except (ServiceError, OSError, ValueError) as error:
            names = ", ".join(sorted(path.name for path in current.values()))
            log_failure(logger, logging.WARNING, f"Could not check mod references for {names}", error)
            return {}
        if broken:
            logger.info(
                "%s points at objects the game no longer holds, updating it as a file of an older release",
                ", ".join(sorted(current[target].name for target in broken)),
            )
        return {current[target]: found for target, found in broken.items()}

    @toggle_timing.stage("pointer_check")
    def _stale_updates(
        self,
        updates: Mapping[Path, Collection[Path]],
        current: Mapping[Path, Path],
        pin: Pin,
        *,
        targets: Collection[Path],
    ) -> dict[Path, set[pointers.Pointer]]:
        """Find dangling references by source file among the files an entry's write updates rather than writes whole.

        ``updates`` is {source: the targets it updates}, ``current`` the same write's whole replacements ({target:
        source}) and ``targets`` the entry's full targets. A source of an older release may point into a sibling part's
        CAB at an object the current game no longer holds; its clips would carry that pointer into the part as raw
        bytes. Pointers are judged against the entry as the write leaves it (``_reference_worlds``), less what the
        updated parts' official copies themselves point at in vain. The result goes to ``rebuild(stale_pointers=)``,
        which keeps the official clips for the clips carrying them. A source that cannot be read gives no evidence (the
        write's own check judges the result).

        Runs inside the class 2 entry step under the library lock; the pointer reads are kept by SHA-1
        (``_pointers_of``).
        """
        updates = {source: list(updated) for source, updated in updates.items() if updated}
        if not updates:
            return {}
        stale: dict[Path, set[pointers.Pointer]] = {}
        try:
            entry = (*targets, *current, *(target for updated in updates.values() for target in updated))
            official = self._officials(entry, pin)
            resulting, baseline = self._reference_worlds(official, current, pin, log_level=logging.INFO)
            for source, updated in updates.items():
                found = pointers.dangling(self._pointers_of(source, log_level=logging.INFO), resulting(updated[0]))
                for target in updated:
                    if found and official[target] is not None:
                        found -= pointers.dangling(
                            self._pointers_of(official[target], log_level=logging.INFO), baseline(target)
                        )
                if found:
                    stale[source] = found
        except (ServiceError, OSError, ValueError) as error:
            names = ", ".join(sorted(source.name for source in updates))
            log_failure(logger, logging.WARNING, f"Could not check mod references for {names}", error)
            return {}
        for source in sorted(stale):
            logger.info(
                "%s carries references to objects the game no longer holds; "
                "their animation clips keep the official ones",
                source.name,
            )
        return stale

    def _check_references(self, changed: Mapping[Path, Path], pin: Pin) -> None:
        """Make the last reference check of a write.

        A file that points at objects absent from its destination CAB (``_broken_references``) never reaches the
        install; the game would draw nothing there and report nothing (mod_release_mismatch, naming the file).
        """
        broken = self._broken_references(changed, pin)
        for target, found in sorted(broken.items()):
            logger.warning(
                "Refused a write pointing at %d objects the game no longer holds: %s (%s)",
                len(found),
                target.name,
                ", ".join(f"{item.cab}:{item.path_id}" for item in sorted(found)[:3]),
            )
            raise ServiceError("mod_release_mismatch", file=changed[target].name)

    @toggle_timing.stage("crc_check")
    def _check_target_crc(self, changed: dict[Path, Path], pin: Pin) -> None:
        """Check official-named replacements in the writer's class, under the library lock for one entry.

        Bytes whose CRC differs from the official CRC never reach an install that needs the CRC fix, whatever step
        before missed it (crc_fix_failed, naming the file). Catalog placements bypass this check. The caller yields
        at the next entry boundary.
        """
        for target, prepared in changed.items():
            if not self.settings.crc_needed(target):
                continue
            fingerprint = self._hashes.get(prepared)
            expected = self._target_crc(target, pin)
            if crc_state(fingerprint.crc, fingerprint.trailing, target.name, expected_crc=expected) == "needs_fix":
                logger.warning("Refused a write whose CRC does not match the target: %s", target.name)
                raise ServiceError("crc_fix_failed", file=target)

    def _write(
        self,
        replacements: dict[Path, Path],
        source: str,
        log: LogFunc,
        progress: ProgressCallback | None,
        pin: Pin,
        build_keys: dict[Path, str] | None = None,
        *,
        create_backup: bool = False,
        expected: dict[Path, str] | None = None,
        kept_current: list[str] | None = None,
        untested: bool = True,
        before_write: Callable[[], None] | None = None,
        replaced_kept: bool = False,
    ) -> list[Path]:
        kept = {target: path for target, path in replacements.items() if self._managed and self._is_kept(path)}
        options = dict(create_backup=create_backup, expected=expected, kept_current=kept_current, untested=untested)
        if replaced_kept:
            options["replaced_kept"] = True
        if before_write is not None:
            options["before_write"] = before_write
        with contextlib.ExitStack() as resources:
            if kept:
                work = Path(
                    resources.enter_context(tempfile.TemporaryDirectory(prefix="verified-official-", dir=self.root))
                )
                replacements = dict(replacements)
                for index, (target, path) in enumerate(kept.items()):
                    staged = work / str(index) / path.name
                    self._copy_verified_official(path, staged)
                    replacements[target] = staged
            return self._write_prepared(replacements, source, log, progress, pin, build_keys, **options)

    def _write_prepared(
        self,
        replacements: dict[Path, Path],
        source: str,
        log: LogFunc,
        progress: ProgressCallback | None,
        pin: Pin,
        build_keys: dict[Path, str] | None = None,
        *,
        create_backup: bool = False,
        expected: dict[Path, str] | None = None,
        kept_current: list[str] | None = None,
        untested: bool = True,
        before_write: Callable[[], None] | None = None,
        replaced_kept: bool = False,
    ) -> list[Path]:
        from ...gameassets.costume_rule import COSTUME_RULE

        dirs = list(pin.dirs)
        for target, prepared in replacements.items():
            check_resource_path(target, dirs)
            if self._hashes.get(prepared).trailing is None:
                raise ServiceError("bundle_invalid", file=prepared)
        changed = {
            target: prepared
            for target, prepared in replacements.items()
            if self._hashes.get(target).sha1 != self._hashes.get(prepared).sha1
        }
        if untested and source != "official":  # restores and rollbacks put back what was there
            self._check_target_crc(changed, pin)
            self._check_references(changed, pin)
        if changed and untested and source != "official":
            # the last check: every write path asked before it built (apply, a toggle's plan); one that did not, asks
            written = sorted(changed)
            self._check_untested([(pin, self._untested_entry(pin, core_of(written[0].name), written), written)])
        fingerprints = {target: self._hashes.get(prepared) for target, prepared in replacements.items()}
        tails = {
            target: read_tail(prepared) if fingerprints[target].trailing else None
            for target, prepared in replacements.items()
        }
        old_applied = dict(self._applied)
        stamp = now()
        # Migration stages and verifies bundles outside the library lock. Ordinary writes already hold it.
        state_lock = self._lock if before_write is not None else contextlib.nullcontext()

        def records() -> dict[str, dict[str, Any]]:
            out = {}
            for target in replacements:
                fingerprint = fingerprints[target]
                stat = target.stat()
                expected_crc = self._target_crc(target, pin) if name_hash(target.name) else None
                record = {
                    "source": source,
                    "sha1": fingerprint.sha1,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime_ns,
                    "official": self._official_sha1(target, pin) == fingerprint.sha1,
                    "at": stamp,
                    "tail": tails[target],
                    "crc": crc_state(fingerprint.crc, fingerprint.trailing, target.name, expected_crc=expected_crc),
                }
                if build_keys and target in build_keys:
                    record["build"] = build_keys[target]
                if source.startswith("alt:"):
                    # built by this program, under its costume rule: an older rule asks for an update
                    record["costume_rule"] = COSTUME_RULE
                previous = self._applied.get(str(target.resolve()))
                if previous and {k: v for k, v in previous.items() if k != "at"} == {
                    k: v for k, v in record.items() if k != "at"
                }:
                    record = previous  # nothing new: the write time stays the one of the bytes there
                out[str(target.resolve())] = record
            return out

        if not changed:
            new = records()
            with state_lock:
                if any(self._applied.get(path) != record for path, record in new.items()):
                    self._applied.update(new)
                    try:
                        self._save()
                    except (OSError, ServiceError):
                        self._applied = old_applied
                        raise
            return []
        written: list[Path] = []
        kept_now: list[Path] = []  # originals this call kept from the game file itself
        kept: list[str] = []
        left: set[str] = set()  # files a rollback found changed outside the library and left as they are
        restored = True
        journal: Path | None = None
        transaction: dict | None = None
        saving_state = False
        try:
            with self._writable(list(changed), pin):
                if expected and any(self._hashes.get(path).sha1 != digest for path, digest in expected.items()):
                    raise ServiceError("entry_changed", core=core_of(next(iter(changed)).name))
                officials = {}
                for target in changed:
                    try:
                        officials[target] = self._official_for(target, pin)
                    except ServiceError as error:
                        if error.code != "official_unavailable":
                            raise
                # Include omitted parts of the core: the outside mod must survive as a whole.
                kept = [] if replaced_kept else self._keep_replaced(list(replacements), pin)
                directory = self.root / "transactions" / uuid.uuid4().hex
                directory.mkdir(parents=True)
                sync_directory(self.root)
                sync_directory(directory.parent)
                journal = directory / "journal.json"
                items = []
                pins = self._all_pins()
                for i, target in enumerate(changed):
                    original = self._original_path(target)
                    official = (
                        target in officials
                        and self._hashes.get(officials[target]).sha1 == self._hashes.get(target).sha1
                    )
                    new_original = officials.get(target) == target and not original.exists()
                    if new_original:
                        kept_now.append(target)
                    restore = (
                        not self._managed
                        and source == "official"
                        and changed[target] == original
                        and not self._needed_elsewhere(target.name, {str(target.resolve())}, pins)
                    )
                    backup = original if official else directory / f"{i}.bundle"
                    items.append(
                        {
                            "target": str(target.resolve()),
                            "before": self._hashes.get(target).sha1,
                            "after": fingerprints[target].sha1,
                            "official": official,
                            "rollback": str(backup.relative_to(self.root)),
                            "kept_now": new_original,
                            "restore": str(original.relative_to(self.root)) if restore else None,
                        }
                    )
                transaction = {
                    "version": 2,
                    "phase": "preparing",
                    "pin": {
                        "key": pin.key,
                        "root": str(pin.root),
                        "dirs": list(map(str, pin.dirs)),
                        "server": pin.server,
                    },
                    "files": items,
                    "kept": kept,
                    "old_applied": {item["target"]: old_applied.get(item["target"]) for item in items},
                }
                atomic_json(journal, transaction)
                stage = "backup"  # what failed, for the log: the user only sees the error code
                try:
                    for item, target in zip(items, changed):
                        # The verified original kept first is the baseline, so no adjacent .backup is written then
                        # (one already beside the game file is left alone); the classic install keeps its own.
                        if target in officials:
                            self._keep_original(target, pin, moving=True)
                        kept_original = self._original_path(target).is_file()
                        backup_file(target, create_backup and not kept_original, log)
                        if not item["official"]:
                            backup = self.root / item["rollback"]
                            if not atomic_clone(target, backup):
                                atomic_copy(target, backup)
                            verify_file(backup, item["before"])
                    transaction["phase"] = "writing"
                    atomic_json(journal, transaction)
                    stage = "write"
                    if before_write is not None:
                        before_write()
                    for i, (target, prepared) in enumerate(changed.items(), 1):
                        check_resource_path(target, dirs)
                        if source == "official" and prepared == self._original_path(target):
                            if not atomic_clone(prepared, target):
                                if items[i - 1]["restore"]:
                                    verified_move(prepared, target, fingerprints[target].sha1)
                                else:
                                    atomic_copy(prepared, target)  # another install still needs this original
                        else:
                            atomic_copy(prepared, target)
                        verify_file(target, fingerprints[target].sha1)
                        written.append(target)
                        log(f"Installed {target.name}")
                        if progress:
                            progress(
                                i,
                                len(changed),
                                t(
                                    "webui.progress.restore" if source == "official" else "webui.progress.apply",
                                    name=target.name,
                                ),
                            )
                    saving_state = True
                    stage = "state"
                    new = records()
                    with state_lock:
                        self._applied.update(new)
                        self._save()
                except Exception as error:
                    self._applied = old_applied
                    try:
                        left = self._rollback_write(transaction, pin)
                    except Exception as rollback_error:
                        restored = False
                        log_failure(logger, logging.ERROR, "Could not roll back library write", rollback_error)
                    if isinstance(error, OSError):
                        code = "library_io_error" if saving_state else safety.write_refusal(error)
                        if code in ("game_file_denied", "game_file_write_failed") and self._in_library(error.filename):
                            code = "library_io_error"  # the backup copy into the library failed, not the game file
                        log_failure(logger, logging.WARNING, f"Library write failed ({stage}, {code})", error)
                        raise ServiceError(code, file=error.filename) from error
                    raise
                # An uncertain commit marker stays for recovery to decide, before any originals are released.
                restored = False
                transaction["phase"] = "committed"
                atomic_json(journal, transaction)
        except Exception:
            if restored:
                # what they kept is in the game again (not for a file left as it is); a part left half written keeps
                # them
                self._drop_mods(self._droppable(kept, left))
                self._release_failed([target for target in kept_now if str(target.resolve()) not in left], pin)
                if journal is not None:
                    shutil.rmtree(journal.parent)
            raise
        if journal is not None:
            shutil.rmtree(journal.parent)
        if kept_current is not None:
            kept_current.extend(kept)
        return written
