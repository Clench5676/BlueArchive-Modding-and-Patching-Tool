"""``ModLibrary``: the mod library, composed of its sections, with its state and ``state.json``."""

from __future__ import annotations

from ..records import stored_target

import copy
import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from bmpt.host import steam_records
from ...catalog.records import CoreMap
from ...host.store_formats import FutureFormat, StateEncoder, epoch_root, read_runtime
from ...core.errors import ServiceError
from ...core.filelock import file_lock
from ...gameassets.cabs import CabIndex
from ...core.logging_utils import log_failure
from .. import pointers, rig_archive, staging, toggle_timing
from ..original_checks import OriginalChecks
from ..judge import supports_resource_type
from ..migrations import LEGACY_REMAP, legacy_remapped
from ..records import Pin
from ..profiles import Profiles
from ..storage import (
    Fingerprints,
    atomic_bytes,
    atomic_copy,
    atomic_json,
)
from .base import _UNREADABLE, _LibraryLock, _NewerState, _Reading, _combine, _held_after, _held_id, _valid_id
from .catalog import CatalogKind, CatalogMixin
from .copies import CopiesMixin
from .costumes import CostumesMixin
from .editing import EditingMixin
from .enable import EnableMixin
from .folders import FoldersMixin
from .add_animations import AddAnimationsMixin
from .impact import ImpactMixin
from .imports import ImportsMixin
from .mods import ModsMixin
from .mods_add import AddMixin
from .originals import OriginalsMixin
from .switch_plan import SwitchPlanMixin
from .switching import SwitchingMixin
from .updating import UpdatingMixin
from .views import ViewsMixin

if TYPE_CHECKING:
    from ...catalog import Catalog
    from ...host.config import Settings
    from ...gameassets import GameAssets


logger = logging.getLogger(__package__)


class ModLibrary(
    ModsMixin,
    AddMixin,
    ImportsMixin,
    FoldersMixin,
    OriginalsMixin,
    ImpactMixin,
    ViewsMixin,
    SwitchPlanMixin,
    CatalogMixin,
    CostumesMixin,
    SwitchingMixin,
    CopiesMixin,
    EnableMixin,
    EditingMixin,
    AddAnimationsMixin,
    UpdatingMixin,
):
    """Store mods, verified originals, installed-source records, and the Enable Mods configuration.

    One reentrant lock serializes state changes and UnityPy work in this service; the jobs over every game folder
    (toggle, adopt, Apply Mod Configuration) take it for one entry at a time. Call long operations on the caller's
    worker thread. Every method that reads or writes game files takes an optional pin (a game folder fixed for the
    call); without one it takes the current game folder, or every known one, from the settings at the call.
    """

    def __init__(
        self,
        root: Path,
        catalog: Catalog,
        game: GameAssets | None = None,
        settings: Settings | None = None,
        *,
        scan_allowed: Callable[[], bool] = lambda: False,
        data_id: str = "local",
        write_check: Callable[[Pin], None] | None = None,
        steam_files: steam_records.OfficialFiles | None = None,
        game_write_gate: Callable | None = None,
        catalog_write_gate: Callable | None = None,
    ) -> None:
        """Load a library and recover pending managed writes before accepting new changes.

        Args:
            root: Library storage folder, resolved before state and asset paths are created.
            catalog: Character catalog resolving bundle cores, alts, and explicit assignments.
            game: Game asset service used for Collection target resolution; None omits that service.
            settings: Game-folder, server, and save settings; None permits storage without a selected install.
            scan_allowed: Reports consent to inspect game files the library did not write.
            data_id: Identifier of this settings directory for shared-library install visibility.
            write_check: Runs immediately before an install's game writes and may raise ServiceError;
                None adds no gate. The web UI uses it to refuse writes while a classic window is open.
            steam_files: Shared official-file cache, also usable by error reports; None creates one.
            game_write_gate: Context holding verified legacy restore paths through a managed game write.
            catalog_write_gate: Context excluding older writers during catalog writes.
        """
        self.root = Path(root).resolve()
        self._managed = epoch_root(self.root, "library") is not None
        self.cache_root = self.root.parent.parent.parent / "cache" / "g2" if self._managed else self.root
        self._state_extra: dict = {}
        self._state_encoder = StateEncoder() if self._managed else None
        self._state_path = self.root / "state.json"
        self._prev_path = self.root / "state.json.prev"  # the last good state.json, kept before every write
        self._bad_path = self.root / "state.json.bad"  # an unreadable state.json, kept at the next write
        self.catalog = catalog
        self.game = game
        self.settings = settings
        self.data_id = data_id
        self._scan_allowed_input = scan_allowed
        self._write_check = write_check
        self._game_write_gate = game_write_gate
        self._catalog_write_gate = catalog_write_gate
        self._lock = _LibraryLock()
        self._hashes = Fingerprints()
        # cross-bundle pointers of a file by its SHA-1, and the objects of a game file's CABs by its revision
        self._pointer_cache: OrderedDict[str, frozenset[pointers.Pointer]] = OrderedDict()
        self._layout_cache: OrderedDict[tuple, dict[str, dict[int, int]]] = OrderedDict()
        self._steam_files = steam_files if steam_files is not None else steam_records.OfficialFiles()
        self._steam_roots_cache: tuple[float, list[Path]] | None = None
        self._legacy_import_matches: dict[str, str | None] = {}
        self._writing_mods = False
        self._transaction_owner: int | None = None
        self._deferred_prunes = 0
        self._prune_pending = False
        self._recovery_error: ServiceError | None = None  # why the last recovery attempt failed: writes refuse with it
        self._recovery_seen: tuple[tuple[str, ...], float] | None = None  # journals at the last attempt, and when
        self._recovery_logged: tuple | None = None  # (code, journals) of the failure already logged
        self._waiting: set[str] = set()  # write journals waiting for their install, already logged
        self._records: dict[str, dict[str, Any]] = {}
        self._remap_previews: dict[str, dict] = {}
        self._added_animations: dict[str, dict] = {}  # Add Animations jobs between their steps
        self._favorites: set[str] = set()
        self._applied: dict[str, dict[str, Any]] = {}
        self._migrations: set[str] = set()
        self._migration_pending = False
        self._base: tuple[dict, set, dict, set] = (
            {},
            set(),
            {},
            set(),
        )  # the state as last read or written, for merges
        self._held: list[Any] = []  # state.json records this library cannot use now, written back unchanged
        self._warned: set[tuple[str, str]] = set()  # held records already logged
        self._state_good = False  # whether this instance holds state from a good read of state.json
        self._state_error: ServiceError | None = None  # why state.json cannot be written now (library_state_unreadable)
        self._reread = False  # the last read could not open state.json: read it again at the next call
        self._version = 0  # bumped by every change of _applied, so a scan can tell it is stale
        self._pruned_cores: set[str] = set()
        # Per install: directory revisions, core index, regular bundle names by directory, generation.
        self._indexes: dict[str, tuple[tuple, dict[str, list[Path]], dict[Path, dict[str, None]], int]] = {}
        self._cab_indexes: dict[tuple[str, tuple[Path, ...]], CabIndex] = {}
        self._costume_builds: dict[str, threading.Event] = {}  # costume preview signatures being built
        self._costume_builds_lock = threading.Lock()
        self._cores: dict[str, str] = {}
        self._packed_wanted: set[str] = set()  # packed groups of files being imported
        self._generation = 0
        self._maps: dict[str, tuple[int, CoreMap]] = {}
        self._names_cache: dict[str, tuple[int, dict[str, Path]]] = {}
        self._originals_cache: tuple[Any, dict[str, int]] | None = None
        # how each kept original was verified: Steam's list or the CRC rule alone
        self._original_checks = OriginalChecks(self.root, self._hashes)
        self._backfill_misses: set[tuple] = set()
        # an install's checked catalog paths, reused for a moment (_catalog_paths)
        self._catalog_paths_kept: dict[str, tuple] = {}
        # entries a running move to the catalog finished and has not saved yet (_migration_outcome)
        self._migration_outcomes: dict[tuple[str, str], str | None] | None = None
        # a start job left kept originals to release: the maintenance prune takes them (take_prune_due)
        self._prune_due = False
        # the start's refresh of built outputs runs: only an output of the current builder is reused (_build_fits)
        self._fresh_builds_only = False
        # the official rigs of portraits kept per game release, and what was matched against them (_archived_rigs)
        self.rigs = rig_archive.RigArchive(self.root / "rigs")
        self._rig_matches: dict[tuple, dict] = {}
        self._mod_masks: dict[tuple, dict] = {}
        self._impacts: dict[tuple, tuple[str, dict]] = {}  # judged impacts of game updates (_impact_of)
        self._impact_cache: dict[str, tuple[tuple, dict]] = {}  # the last summary per install (update_impact)
        self._untested_ok: dict[
            tuple[str, str], set[str]
        ] = {}  # confirmed cores of an untested build (confirm_untested)
        self._maps_cache: tuple[tuple, Any] | None = None
        self.profiles = Profiles(self.root)
        self._catalog_restores: set[str] = set()
        self._catalog_recovery_blocked: set[str] = set()
        self._catalog_idle = threading.Event()
        self._catalog_idle.set()
        self._catalog_identifications: dict[Path, tuple[tuple[int, int, str, str | None], CatalogKind | None]] = {}
        self.profiles.gate = self._check_writes  # no change of the configuration while writes are refused
        staging.cleanup(self.root)
        self._load()
        try:
            self._recover_pending()
            with self._lock:
                self._lookup_records()
                self._migrate_collection_slots()
                self._migrate_packed_slots()
                self._known()
        except ServiceError as error:
            if self._managed and self._recovery_error is not None:
                raise
            log_failure(logger, logging.WARNING, "Mod configuration not looked up", error)
        except Exception as error:  # the library still works without it; the next build tries again
            log_failure(logger, logging.ERROR, "Mod configuration lookup failed", error)

    def _parse_state(self, data: Any) -> tuple[dict, set, dict, set, list]:
        """Parse the JSON of a state.json.

        A record that cannot be used (not an object, a key missing or of another type, a bad id) is skipped with a
        warning and its raw JSON is held, to be written back unchanged: one bad record costs that record at most, never
        the file. An invalid ``applied`` entry is dropped.

        Returns:
            The tuple (records, favourites, applied, migrations, held).

        Raises:
            ValueError: The file as a whole is not a state of this version (_NewerState for a version above 2).
        """
        if not isinstance(data, dict):
            raise ValueError("not a library state")
        version = data.get("version")
        if isinstance(version, (int, float)) and not isinstance(version, bool) and version > 2:
            raise _NewerState(version)
        if version != 2:
            raise ValueError("not a version 2 library state")
        mods, listed, written, migrated = (
            data.get(key, empty)
            for key, empty in (("mods", []), ("favorites", []), ("applied", {}), ("migrations", []))
        )
        if not (
            isinstance(mods, list)
            and isinstance(listed, list)
            and isinstance(written, dict)
            and isinstance(migrated, list)
        ):
            raise ValueError("invalid library state")
        records: dict[str, dict] = {}
        held: list[Any] = []
        for raw in mods:
            reason = self._unusable(raw)
            if reason is None and not all((self.root / "mods" / raw["id"] / f).is_file() for f in raw["files"]):
                reason = "files missing"  # a record stays until the user or a removal deletes it
            if reason is not None:
                key = (reason, _held_id(raw) or json.dumps(raw, sort_keys=True, default=str))
                if key not in self._warned:  # once per record, not at every read of the file
                    self._warned.add(key)
                    logger.warning(
                        "Holding a library record that cannot be used (%s): %s", reason, _held_id(raw) or "no usable id"
                    )
                held.append(raw)
                continue
            records[raw["id"]] = raw
        favorites = {key for key in listed if isinstance(key, str)}
        applied = {
            path: record
            for path, record in written.items()
            if isinstance(path, str)
            and isinstance(record, dict)
            and isinstance(record.get("source"), str)
            and isinstance(record.get("sha1"), str)
        }
        migrations = {key for key in migrated if isinstance(key, str)}
        return records, favorites, applied, migrations, held

    @staticmethod
    def _unusable(record: Any) -> str | None:
        """Why a state.json record cannot be used (a word, never a path), None when it can."""
        if not isinstance(record, dict):
            return "not an object"
        try:
            if not _valid_id(record["id"]):
                return "bad id"
            files = record["files"]
            if (
                not isinstance(files, list)
                or not files
                or not all(isinstance(f, str) and Path(f).name == f and f.endswith(".bundle") for f in files)
            ):
                return "bad files"
            if not all(isinstance(record[key], str) for key in ("name", "added")):
                return "bad text"
            if not isinstance(stored_target(record), str):
                return "bad target"
            if not supports_resource_type(record["resource_type"]) or not isinstance(record["seen"], bool):
                return "bad kind or flag"
            if "assigned_alt" in record and (not isinstance(record["assigned_alt"], str) or not record["assigned_alt"]):
                return "bad assignment"
        except (KeyError, TypeError):
            return "key missing or of another type"
        return None

    def _parse_file(self, path: Path) -> tuple[dict, set, dict, set, list]:
        try:
            document = read_runtime(path)
        except FutureFormat as error:
            raise _NewerState(str(error)) from error
        parsed = self._parse_state(document)
        self._state_extra = {
            key: value
            for key, value in document.items()
            if key not in ("version", "mods", "favorites", "applied", "migrations")
        }
        return parsed

    def _read_state(self) -> _Reading:
        """Read state.json, else (when it is unreadable or gone) its previous copy; never raises.

        With neither file the library is empty. A file of a newer build (version above 2) is never read as corrupt: the
        previous copy is not looked at. A file that cannot be opened (``OSError``: locked, scanned) is not corruption
        either. A missing file with a previous copy (a partial restore) reads the copy, as profiles.json does: nothing
        here deletes state.json, and its writes are atomic.
        """
        path = self._state_path
        exists = True
        try:
            return _Reading("ok", self._parse_file(path))
        except FileNotFoundError:
            exists = False
        except _NewerState:
            logger.warning("The library state was written by a newer version, leaving it as it is")
            return _Reading("newer", error=ServiceError("library_state_unreadable", file=str(path)))
        except OSError as error:
            log_failure(logger, logging.WARNING, "Cannot read the library state", error)
            return _Reading("io", error=ServiceError("library_io_error", file=str(path)))
        except _UNREADABLE as error:
            log_failure(
                logger, logging.WARNING, "Cannot read the library state, reading the previous copy instead", error
            )
        if not exists:
            if not self._prev_path.exists():
                return _Reading("missing")
            logger.warning("The library state is missing, reading the previous copy instead")
        unreadable = ServiceError("library_state_unreadable", file=str(path if exists else self._prev_path))
        try:
            state = self._parse_file(self._prev_path)
        except FileNotFoundError:
            if not exists:
                return _Reading("missing")
            logger.error("The library state is unreadable and has no previous copy")
            return _Reading("corrupt", error=unreadable)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Cannot read the previous library state", error)
            return _Reading("io", error=ServiceError("library_io_error", file=str(self._prev_path)))
        except (_NewerState, *_UNREADABLE) as error:
            log_failure(logger, logging.ERROR, "The previous library state is unreadable too", error)
            return _Reading("corrupt", error=unreadable)
        logger.warning("Reading the previous library state")
        return _Reading("prev", state)

    def _load(self) -> None:
        signature_ = self._state_signature()
        reading = self._read_state()
        self._seen = signature_
        if reading.error is not None:
            self._state_unreadable(reading)
            return
        self._state_good, self._state_error, self._reread = True, None, False
        if reading.state is None:
            return
        self._records, self._favorites, self._applied, self._migrations, self._held = reading.state
        self._base = copy.deepcopy(reading.state[:4])
        self._migration_pending = LEGACY_REMAP not in self._migrations
        if self._migration_pending:
            try:
                self._save()
            except (OSError, ServiceError) as error:
                log_failure(logger, logging.WARNING, "Could not migrate legacy remapped mods", error)

    def _state_unreadable(self, reading: _Reading) -> None:
        """Note a reading that found no state.

        A library that never read a good state stays empty and cannot be saved (``_state_error``); one that holds good
        state keeps it, unless the file is a newer build's. An ``io`` failure is read again at the next call, any other
        verdict stands until the file changes.
        """
        self._reread = reading.resource_type == "io"
        if reading.resource_type == "newer" or not self._state_good:
            self._state_error = reading.error

    def _check_writable(self) -> None:
        """Refuse a write before it starts changing files that state.json could not record.

        Raises:
            ServiceError: ``library_state_unreadable`` (a state that cannot be read, or a newer build's) or
            ``library_io_error`` (a state that cannot be opened), as read at the last refresh.
        """
        if self._state_error is not None:
            raise ServiceError(self._state_error.code, **self._state_error.params)

    def _disk_held(self, reading: _Reading) -> list:
        """Give the held records of what was read.

        A previous copy is older than this instance's state: the records it held before are kept too.
        """
        held = reading.state[4] if reading.state else []
        if reading.resource_type == "prev":
            held = [*held, *(raw for raw in self._held if raw not in held)]
        return held

    def _held_ids(self) -> frozenset[str]:
        """The ids of the records state.json holds but this library cannot use now."""
        return frozenset(mod_id for mod_id in map(_held_id, self._held) if mod_id)

    def _state_signature(self) -> tuple[int, int] | None:
        try:
            stat = self._state_path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _refresh_state(self, migrate: bool = True) -> None:
        """Take in what another instance sharing the library wrote to state.json since this one last read or wrote it.

        The file counts as changed when its size or date changed; this instance's own changes not saved yet are kept. A
        library that could not read its state reads it again at every call that finds the file changed (and after a
        failed open). The legacy migration is saved when ``migrate``, and skipped (not raised) while writes are refused.
        """
        self._recover_for_read()
        if not self._reread and self._state_signature() == self._seen:
            if self._migration_pending and migrate:
                self._migrate_state()
            return
        with file_lock(self.root / "state.json.lock"):
            signature_ = self._state_signature()
            reading = self._read_state()
        if reading.error is not None:
            self._seen = signature_
            self._state_unreadable(reading)
            return
        disk = reading.state or ({}, set(), {}, set(), [])
        base, mine = self._base, (self._records, self._favorites, self._applied, self._migrations)
        self._records, self._favorites, self._applied, self._migrations = _combine(
            reading.resource_type, base, mine, disk
        )
        self._held = _held_after(self._disk_held(reading), self._records, base[0], mine[0])
        self._base, self._seen = copy.deepcopy(disk[:4]), signature_
        self._state_good, self._state_error, self._reread = True, None, False
        self._version += 1
        self._migration_pending = LEGACY_REMAP not in self._migrations
        if self._migration_pending and migrate:
            self._migrate_state()

    def _migrate_state(self) -> None:
        """Save the state with the legacy remap migration, once writes are allowed.

        A pending recovery, a state that cannot be written (ServiceError) or a file that cannot be written (OSError)
        skips it for now, and it stays pending. A read never fails on it.
        """
        try:
            self._save()
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.DEBUG, "Library state migration skipped", error)

    @toggle_timing.stage("state_save")
    def _save(self) -> None:
        """Write state.json, merged with what another instance sharing the library wrote since this one last read it.

        This instance's own changes since then (records added, changed or removed by id, game file records written or
        dropped, favourites toggled) are applied to the file as it is now, under a lock both share. The merged state
        becomes this instance's only once it is written. Held records of the file are written back unchanged, less those
        this instance replaced or removed.

        The last good file is kept as state.json.prev before the write, and an unreadable one as state.json.bad. A
        library that never read a good state (``_state_good``), a file of a newer build and a file that cannot be opened
        are not written: ServiceError ``library_state_unreadable`` or ``library_io_error``, nothing changed.
        """
        self._check_recovery()
        path = self._state_path
        mine = (self._records, self._favorites, self._applied, self._migrations)
        with toggle_timing.enter("state_file_wait", file_lock(self.root / "state.json.lock")):
            reading = self._read_state()
            if reading.error is not None and (reading.resource_type != "corrupt" or not self._state_good):
                raise reading.error
            base = self._base
            if reading.error is not None:
                # the file and its previous copy are unreadable, but this instance holds the state it read before:
                # it replaces the file, which is kept as .bad
                merged, held = mine, _held_after(self._held, mine[0], base[0], mine[0])
            else:
                disk = reading.state or ({}, set(), {}, set(), [])
                merged = _combine(reading.resource_type, base, mine, disk)
                held = _held_after(self._disk_held(reading), merged[0], base[0], mine[0])
            if LEGACY_REMAP not in merged[3]:
                merged = (legacy_remapped(merged[0]), merged[1], merged[2], merged[3] | {LEGACY_REMAP})
            payload = {
                **self._state_extra,
                "version": 2,
                "mods": [*merged[0].values(), *held],
                "favorites": sorted(merged[1]),
                "applied": merged[2],
                "migrations": sorted(merged[3]),
            }
            self._keep_state_file(reading.resource_type)
            if self._state_encoder is None:
                atomic_json(path, payload)
            else:
                atomic_bytes(path, self._state_encoder.encode(payload))
            self._seen = self._state_signature()
        self._records, self._favorites, self._applied, self._migrations = merged
        self._held = held
        self._state_good, self._state_error, self._reread = True, None, False
        self._migration_pending = False
        self._base = (
            _snapshot_records(merged[0], base[0]),
            set(merged[1]),
            _snapshot_records(merged[2], base[2]),
            set(merged[3]),
        )
        self._version += 1

    def _keep_state_file(self, resource_type: str) -> None:
        """Keep the old state.json before it is replaced.

        A file that was read becomes state.json.prev (the last good file); one that could not be read is kept as
        state.json.bad, and the previous copy stays what it is.
        """
        if resource_type == "ok":
            atomic_copy(self._state_path, self._prev_path)
        elif (
            resource_type in ("prev", "corrupt") and self._state_path.exists()
        ):  # a missing file leaves nothing to keep
            try:
                atomic_copy(self._state_path, self._bad_path)
            except OSError as error:
                log_failure(logger, logging.WARNING, "Could not keep the unreadable library state", error)

    supports_resource_type = staticmethod(supports_resource_type)


def _snapshot_records(records: dict, previous: dict) -> dict:
    return {
        key: previous[key] if key in previous and previous[key] == record else copy.deepcopy(record)
        for key, record in records.items()
    }
