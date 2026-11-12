"""Thread-safe library operations built on the classic mod installer."""

from __future__ import annotations

import base64
import contextlib
import copy
import functools
import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Hashable, Iterable, Iterator, Mapping, Sequence

from UnityPy.helpers.ResourceReader import get_resource_data

from bmpt import atlas_remap, devmode, game_paths, graft as grafting, mod_origin, preview_gate, rebake, spans, \
    steam_records
from bmpt.engine import (
    SEARCH_DIR_SUFFIXES, AssetType, Bundle, LogFunc, ProgressCallback, core, get_search_dirs, get_skel_version,
    is_safe_file_name, no_log, parse_filename, t,
)
from ..catalog.models import CoreMap
from ..errors import ServiceError
from ..filelock import file_lock
from ..gameassets.cabs import CabIndex
from ..gameassets.discovery import cab_names, may_hold_model_root, own_bundle
from ..gameassets.preview_reads import PreviewReads
from ..logging_utils import log_failure
from ..openfiles import remove_tree, temporary_directory
from . import profiles as profiles_mod
from . import fingerprint, integrity, rig_archive, safety, staging, tested_build
from .original_checks import OriginalChecks, Verdict
from .install import Action, PlannedFile, PlannedGroup, backup_file, install_staged
from .rebuild import rebuild
from . import targets as resolver
from .identity import identify
from .judge import (
    CRC_STATES, KINDS, LIBRARY_SOURCES, carried_of, classify, core_of, crc_state, date_of, distinguishing_folders,
    install_key, install_root, kind_of, kind_of_file, learn_packed, leaves, mod_versions, now, other_target, packed_cab,
    part_of, says_official, seconds, supports_kind,
)
from .migrations import LEGACY_REMAP, legacy_remapped
from .models import (
    AddResult, AdoptResult, ApplyResult, AssetDifference, CompareResult, DeleteResult, EntryState, GameFileState, Kind,
    ModEntry, Pin, RemapResult, ScanResult, ToggleResult, UpdateResult, VariantState,
)
from .profiles import Profiles
from .safety import check_resource_path
from .storage import (
    Fingerprint, Fingerprints, atomic_clone, atomic_copy, atomic_json, read_tail, sync_directory, trailing_bytes,
    verified_move, verify_file,
)
from .variants import adapt_variant_sources

if TYPE_CHECKING:
    from ..catalog import Catalog
    from ..config import Settings
    from ..gameassets import GameAssets

logger = logging.getLogger(__name__)

SPACE_MARGIN = 512 * 1024 * 1024  # left free on the library's volume by an Off and an adoption
SAVE_EVERY = (10, 2.0)  # a toggle saves its unfinished list every 10 entries or 2 s
BUILT_TEMP_MAX_AGE = 60 * 60  # seconds before an abandoned built copy can be cleared
COSTUME_PREVIEWS = 4  # costume previews kept in built/previews (one folder per build signature)
COSTUME_VERDICTS = 4000  # costume verdicts kept in costume-verdicts.json, newest first
# the startup rig archive (archive_rigs) works only once no other caller used the library for this long (BL-183)
RIG_ARCHIVE_QUIET = 1.0
RECOVERY_RETRY = 60.0  # seconds before a read tries a recovery that failed (or waits) again, if the journals stay
# a toggle's refusals that stop it: every later write would fail the same way
STOPPING = ("game_running", "game_status_unavailable", "steam_busy", "folder_changed", "classic_open", "untested_build")


def _game_folder(game_file: Path) -> Path:
    """The game folder a game file is in, as the settings name it: its resource folder less the subfolder the search
    looks in (searching.SEARCH_DIR_SUFFIXES)."""
    folder = game_file.parent
    for suffix in sorted(filter(None, SEARCH_DIR_SUFFIXES), key=len, reverse=True):
        parts = tuple(part.lower() for part in Path(suffix).parts)
        if len(folder.parts) > len(parts) and tuple(part.lower() for part in folder.parts[-len(parts):]) == parts:
            return Path(*folder.parts[:-len(parts)])
    return folder


# set on a thread while it does background work that steps aside for everyone else (archive_rigs)
_background = threading.local()


class _LibraryLock:
    """The library's lock (re-entrant) that notes when any caller but background work (``_background``) last took or
    gave it back: what background work steps aside for (``ModLibrary._yield_to_foreground``, BL-183)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.used = 0.0  # time.monotonic() of the last foreground acquire or release

    def _note(self) -> None:
        if not getattr(_background, "on", False):
            self.used = time.monotonic()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        self._note()
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()
        self._note()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _serialized(method: Callable) -> Callable:
    """Run a public method under the library lock, after bringing the state up to date. A pending journal is
    recovered if it can be, and never fails the call: reads work, and a write is refused by its own gates
    (``ModLibrary._check_recovery``) with the real failure."""
    @wraps(method)
    def call(self: ModLibrary, *args: Any, **kwargs: Any) -> Any:
        with spans.held("library-lock", self._lock, floor=0.05), _errors(method.__name__):
            self._refresh_state()
            return method(self, *args, **kwargs)
    return call


def _mod_writer(method: Callable) -> Callable:
    """Serialize source additions and deletions across instances, before state and profiles locks."""
    @wraps(method)
    def call(self: ModLibrary, *args: Any, **kwargs: Any) -> Any:
        if self._writing_mods:
            return method(self, *args, **kwargs)
        with self._transaction_lock(), file_lock(self.root / "mods.lock"):
            self._writing_mods = True
            try:
                self._refresh_state()
                self._check_writable()
                return method(self, *args, **kwargs)
            finally:
                self._writing_mods = False
    return call


@contextlib.contextmanager
def _errors(operation: str) -> Iterator[None]:
    """Library failures as the user sees them: a file the library could not read or write, else the operation."""
    try:
        yield
    except OSError as error:
        log_failure(logger, logging.ERROR, "Library file operation failed", error, code="library_io_error")
        raise ServiceError("library_io_error", file=error.filename) from error
    except ServiceError:
        raise
    except Exception as error:
        log_failure(logger, logging.ERROR, f"Library operation failed ({operation})", error,
                    code="library_operation_failed")
        raise ServiceError("library_operation_failed", operation=operation) from error


class _Unread(Exception):
    """A file the judgement may not read: one the library did not write, without scan consent."""


class _NewerState(Exception):
    """A state.json of a newer build (a version above 1): never read as corrupt, never replaced."""


# what a state.json that is not a state of this version raises while it is read
_UNREADABLE = (ValueError, TypeError, KeyError, AttributeError, RecursionError)


@dataclass
class _Reading:
    """One reading of state.json (``ModLibrary._read_state``).

    ``kind``: ``ok`` (the file), ``missing`` (no file: an empty library), ``prev`` (the file is unreadable, its
    previous copy was read instead), ``corrupt`` (neither is readable), ``newer`` (a version above 1) or ``io`` (the
    file could not be opened: not a verdict on its contents). ``state`` is (records, favourites, applied, migrations,
    held records); ``error`` is set for ``corrupt``, ``newer`` and ``io``."""

    kind: str
    state: tuple | None = None
    error: ServiceError | None = None


def _valid_id(mod_id: Any) -> bool:
    return isinstance(mod_id, str) and len(mod_id) == 32 and all(c in "0123456789abcdef" for c in mod_id)


def _held_id(raw: Any) -> str | None:
    """The id of a held record, None when it has no usable one."""
    mod_id = raw.get("id") if isinstance(raw, dict) else None
    return mod_id if _valid_id(mod_id) else None


def _held_after(disk_held: list, records: dict, base: dict, mine: dict) -> list:
    """The held records to write after a merge: the disk copy's, less those whose id is among the merged ``records``
    and those this instance removed since ``base`` (in ``base``, not in ``mine``). A record without a usable id is
    always kept: nothing can say it was removed."""
    removed = base.keys() - mine.keys()
    kept = []
    for raw in disk_held:
        mod_id = _held_id(raw)
        if mod_id is None or (mod_id not in records and mod_id not in removed):
            kept.append(raw)
    return kept


@dataclass
class _Part:
    """One game file as the judgement found it."""

    path: Path
    size: int
    mtime: int
    carried: str  # "official", "unknown", "damaged", "removed", "mod:<id>", "variant:<key>"
    official: bool  # the file holds official bytes
    matched: bool  # unchanged since the library's record of it
    changed: bool  # a record exists and the file changed since
    trailing: int | None  # bytes after the bundle data, when the header was read
    crc: str
    record: dict | None
    restamp: int | None = None  # the record's new modification time: same bytes, new date


@dataclass
class _Entry:
    """One entry as the judgement found it (EntryState before it is reported)."""

    pin: Pin
    core: str
    kind: str
    variant: str
    parts: list[_Part]
    carried: str
    configured: str | None
    state: str
    takeover: str | None = None
    drifted: bool = False
    overwritten: bool = False
    copies: dict[str, str | None] = field(default_factory=dict)
    dismissed: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.pin.key, self.core

    def item(self) -> dict[str, str]:
        return {"install": self.pin.key, "core": self.core, "variant": self.variant, "kind": self.kind}


def _rebuilt(index: dict[str, list[Path]], path: Path) -> Path | None:
    """The file that a game update rebuilt ``path`` as: the one other file of its group (its name less the CRC) in its
    folder. An update rebuilds a changed group in place, keeping its name and date and changing only the CRC. None
    when no other file or several have that name (groups packed one asset per bundle share it)."""
    if not parse_filename(path.name).crc:
        return None
    key = integrity.replacement_key(path.name)
    rebuilt = [other for other in index.get(core_of(path.name), [])
               if other.parent == path.parent and other.name != path.name
               and integrity.replacement_key(other.name) == key]
    return rebuilt[0] if len(rebuilt) == 1 else None


def _has_successor(index: dict[str, list[Path]], path: Path) -> bool:
    """Whether the file that replaced ``path`` in a game update is in its folder in an install's index: the group
    rebuilt under a new CRC (``_rebuilt``), or a file of the same part dated after it (an update that renamed it)."""
    if _rebuilt(index, path) is not None:
        return True
    part, date = part_of(path.name), date_of(path.name)
    return bool(date) and any(
        other.parent == path.parent and part_of(other.name) == part and date_of(other.name) > date
        for other in index.get(core_of(path.name), []))


class _View:
    """A consistent copy of what the judgement reads besides the game files: the library's records, its mods, the
    configuration, the indexes and the kept originals. ``allow`` says which game files it may read:

    - ``scan``: headers and tails of any file (the scan consent is given);
    - ``family``: the same, for the files the detail screen shows (decision 27, no consent needed);
    - ``recorded``: only the files the library wrote to (no consent); another file raises _Unread;
    - ``full``: write strength, every file the library cannot show unchanged is hashed in full (switching).
    """

    def __init__(self, library: ModLibrary, pins: list[Pin], config: dict | None, allow: str,
                 snapshot: bool = False) -> None:
        """``snapshot``: copy the records, for a view read outside the library lock (the scan). A view used under
        the lock shares the library's records and the maps built from them."""
        self.library = library
        self.pins = {pin.key: pin for pin in pins}
        self.mods = set(library._records)
        self.held = library._held_ids()  # records the library keeps but cannot use now: their sources still exist
        self.config = config
        self.enabled = profiles_mod.enabled(config)
        self.allow = allow
        self.indexes = {pin.key: library._index(pin) for pin in pins}
        self.maps = {pin.key: library._core_map(pin) for pin in pins}
        self.names = {pin.key: library._names(pin) for pin in pins}
        self.originals: dict[str, int] = dict(library._originals())
        if snapshot:
            self.applied = {path: dict(record) for path, record in library._applied.items()}
            self.by_name, self.by_folder = _record_maps(self.applied)
        else:
            self.applied = library._applied
            self.by_name, self.by_folder = library._record_maps()
        self._sizes: dict[str, int | None] = {}

    # --- what the library knows ---

    def folder(self, key: str) -> dict:
        return ((self.config or {}).get("folders") or {}).get(key) or {}

    def configured(self, key: str, core: str) -> dict | None:
        return (self.folder(key).get("configured") or {}).get(core)

    def official_size(self, name: str) -> int | None:
        """The size of the official file of a name, where the library knows it by stat alone: a kept original of
        that name, else a record of that name, in any folder, that says official bytes. The CRC is in the name, so
        every official file of that name has those bytes."""
        if name not in self._sizes:
            steam = self.library._steam_official(name, roots=[pin.root for pin in self.pins.values()])
            size = steam.size if steam is not None else self.originals.get(name)
            if size is None:
                size = next((record.get("size") for _path, record in self.by_name.get(name, [])
                             if says_official(record) and isinstance(record.get("size"), int)), None)
            self._sizes[name] = size
        return self._sizes[name]

    def source_exists(self, source: str, key: str, kind: str, *, held: bool = True) -> bool:
        """Whether the library has a source. A held record (kept in state.json but not usable now) counts: its
        configuration and the game files carrying it are not those of a source the library lost. ``held=False``
        asks only for a usable record (whether the library has the source's bytes)."""
        if source == "official":
            return True
        if source.startswith("mod:"):
            return source[4:] in self.mods or (held and source[4:] in self.held)
        if source.startswith("variant:"):
            if kind == "model":  # a costume: any other model of the student, field-mode ones (no slot) included
                try:
                    other = self.library._variant(source[8:])
                except ServiceError:
                    return False
                return bool(getattr(other, "model", None) or getattr(other, "field_model", None))
            cores = self.maps.get(key)
            return bool(cores and cores.core(source[8:], kind))
        return False

    def records_of(self, pin: Pin, core: str) -> list[tuple[Path, dict]]:
        """The library's records of a core's files in an install, gone files included."""
        out = []
        for folder in pin.dirs:
            for path, record in self.by_folder.get(folder, []):
                if self.maps[pin.key].slot(core_of(path.name)) == core:
                    out.append((path, record))
        return out

    # --- one file ---

    def _header(self, path: Path, recorded: bool) -> int | None:
        if self.allow == "recorded" and not recorded:
            raise _Unread(path)
        return trailing_bytes(path)

    def official_now(self, path: Path, size: int, trailing: int | None, name: str | None = None) -> bool:
        """Whether a file holds official bytes: at write strength by the CRC32 of its whole content; else by the
        cheap rule, no trailing bytes and the official size of its name where the library knows it."""
        name = name or path.name
        if self.allow == "full":
            return self.library._is_official_as(path, name)
        expected = self.official_size(name)
        return trailing == 0 and (expected is None or size == expected)

    def part(self, path: Path, pin: Pin, kind: str) -> _Part:
        """One file judged as ``allow`` permits. A file whose size or date changed while it was read is read again."""
        part = self._part(path, pin, kind)
        if self.allow != "full" and not part.matched:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (part.size, part.mtime):
                part = self._part(path, pin, kind)
        return part

    def _part(self, path: Path, pin: Pin, kind: str) -> _Part:
        record = self.applied.get(str(path.resolve()))
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime_ns
        matched = bool(record) and record.get("size") == size and record.get("mtime") == mtime
        trailing: Any = ...
        restamp = None
        if not matched or self.allow == "full":
            if self.allow == "full":
                matched = False
                fingerprint = self.library._hashes.get(path)
                trailing = fingerprint.trailing
                if trailing is not None and record and fingerprint.sha1 == record.get("sha1"):
                    matched, restamp = True, mtime
            else:
                trailing = self._header(path, record is not None)
                if trailing is not None and record and record.get("tail") and record.get("size") == size:
                    if read_tail(path) == record["tail"]:
                        matched, restamp = True, mtime
            if trailing is None:
                return _Part(path, size, mtime, "damaged", False, False, record is not None, None, "needs_fix",
                             record)
        if matched:
            flag = record.get("official")
            if not isinstance(flag, bool):
                if trailing is ...:
                    trailing = self._header(path, True)
                flag = self.official_now(path, size, trailing if trailing is not None else -1)
            source = record.get("source", "")
            if record.get("removed"):
                carried = "removed"
            elif not self.source_exists(source, pin.key, kind):
                # the bytes of a source the library no longer has: offered for adoption, never lost
                carried = "official" if flag else "unknown"
            else:
                carried = source
            crc = record.get("crc")
            if crc not in CRC_STATES:
                if trailing is ...:
                    trailing = self._header(path, True)
                crc = "fixed" if trailing else "ok"
            return _Part(path, size, mtime, carried, flag, True, False, None if trailing is ... else trailing,
                         crc, record, restamp)
        official = self.official_now(path, size, trailing)
        if official and self.allow != "full" and _outside(record, size, mtime):
            # a full judgement found these very bytes changed outside the library: the cheap rule cannot tell
            official = False
        return _Part(path, size, mtime, "official" if official else "unknown", official, False, record is not None,
                     trailing, "fixed" if trailing else "ok", record)

    def says_nonofficial(self, path: Path, record: dict, present: bool) -> bool:
        """Whether a record says the library wrote non-official bytes there. A record of an older build without the
        flag: its size is not the official size of its name, or (its file gone) an original of that name is kept."""
        flag = record.get("official")
        if isinstance(flag, bool):
            return not flag
        if record.get("source") == "official":
            return False
        expected = self.official_size(path.name)
        if expected is not None and record.get("size") != expected:
            return True
        return not present and path.name in self.originals

    def successor(self, pin: Pin, path: Path) -> bool:
        """Whether a present file of the same part, dated after it, is in the same folder (a game update)."""
        return _has_successor(self.indexes[pin.key], path)

    def later_record(self, path: Path, pin: Pin | None = None) -> bool:
        """Whether the library recorded the file that replaced ``path`` in its folder (it wrote the successor): with
        ``pin``, the group rebuilt under a new CRC (``_rebuilt``); a later file of the same part."""
        records = self.by_folder.get(path.parent, [])
        rebuilt = _rebuilt(self.indexes[pin.key], path) if pin is not None else None
        if rebuilt is not None and any(other == rebuilt for other, _record in records):
            return True
        part, date = part_of(path.name), date_of(path.name)
        return any(part_of(other.name) == part and date_of(other.name) > date for other, _record in records)

    def copy_of(self, part: _Part, pin: Pin) -> str | None:
        """Where an official copy of a game file is, judged cheaply: the library's kept original, the file itself,
        its .backup, a file of its name in another game folder; None when there is none (or it may not be looked
        for). Candidates beyond the file are looked at only for a file that is not official and has no original."""
        name = part.path.name
        # a kept original a Steam list rejected is no official copy (BL-194): the other candidates are looked at
        if name in self.originals and not self.library._original_failed(self.library.root / "originals" / name, False):
            return "kept"
        if part.official:
            return "game"
        if self.allow == "recorded":
            return None
        backup = part.path.with_name(name + ".backup")
        if self._candidate(backup, name):
            return "backup"
        if any(self._candidate(other, name) for other in self.elsewhere(name, part.path)):
            return "elsewhere"
        return None

    def _candidate(self, path: Path, name: str) -> bool:
        try:
            if not path.is_file():
                return False
            size = path.stat().st_size
            if self.allow == "full":
                return self.library._is_official_as(path, name)
            return self.official_now(path, size, trailing_bytes(path), name)
        except OSError:
            return False

    def elsewhere(self, name: str, exclude: Path) -> list[Path]:
        """Files of a name in the other known installs and in the folders of the library's records."""
        exclude = exclude.resolve()
        out: list[Path] = []
        for key, names in self.names.items():
            other = names.get(name)
            if other is not None and other.resolve() != exclude:
                out.append(other)
        for path, _record in self.by_name.get(name, []):
            if path != exclude and path not in out and devmode.allows_read(path) and path.exists():
                out.append(path)
        return out

    # --- one entry ---

    def entry(self, pin: Pin, core: str) -> _Entry:
        core = self.maps[pin.key].slot(core)
        kind = self.library._kind(core, pin) or "spr"
        files = [path for value in self.maps[pin.key].slot_cores(core)
                 for path in self.indexes[pin.key].get(value, [])]
        parts = [self.part(path, pin, kind) for path in files]
        folder = self.folder(pin.key)
        config = self.configured(pin.key, core)
        configured = config["source"] if config else None
        damaged = any(part.carried == "damaged" for part in parts)
        carried = "damaged" if damaged else carried_of((part.carried, part.official) for part in parts)
        drifted_present = drifted_gone = False
        if configured:
            present = {str(part.path.resolve()): part for part in parts}
            for path, record in self.records_of(pin, core):
                is_present = str(path) in present
                if not self.says_nonofficial(path, record, is_present):
                    continue
                if is_present:
                    part = present[str(path)]
                    if not part.matched and part.official:
                        drifted_present = True
                elif path.parent.is_dir() and self.successor(pin, path) and not self.later_record(path, pin):
                    drifted_gone = True
        written = max((seconds(part.record.get("at")) or seconds(part.record.get("mtime")) or 0.0
                       for part in parts if part.carried == carried and part.record), default=None)
        toggled = seconds((self.config or {}).get("toggled"))
        marker = core in (folder.get("updated") or {})

        def judged(source: str | None) -> tuple[str, str | None]:
            return classify(
                self.enabled, source, carried, files=bool(files), damaged=damaged,
                configured_exists=self.source_exists(source, pin.key, kind) if source else True,
                carried_exists=self.source_exists(carried, pin.key, kind) if carried.startswith(LIBRARY_SOURCES)
                else True, drifted_present=drifted_present, drifted_gone=drifted_gone, marker=marker,
                written=written, since=seconds((config or {}).get("since")), toggled=toggled)

        state, takeover = judged(configured)
        if state == "takeover":
            configured = takeover
            state, _ = classify(self.enabled, takeover, carried, files=bool(files),
                                drifted_present=drifted_present, drifted_gone=drifted_gone, marker=marker)
        copies = {str(part.path): self.copy_of(part, pin) for part in parts}
        overwritten = any(not part.official and part.carried != "damaged" and copies[str(part.path)] is None
                          for part in parts) and self.allow != "recorded"
        variant = self.maps[pin.key].key(core) or (config or {}).get("variant") or core
        if kind == "other" and files:
            variant = parse_filename(files[0].name).core
        entry = _Entry(pin, core, kind, variant, parts, carried, configured, state, takeover,
                       drifted_present or drifted_gone or marker, overwritten, copies)
        entry.dismissed = self.dismissed(entry)
        return entry

    def dismissed(self, entry: _Entry) -> bool:
        kind = "drift" if entry.state in ("reset", "updated") else "untracked"
        stored = ((self.folder(entry.pin.key).get("dismissed") or {}).get(kind) or {}).get(entry.core)
        return bool(stored) and sorted(map(tuple, stored)) == signature(entry.parts)

    def cores(self, pin: Pin) -> set[str]:
        """Every core of an install the judgement covers: the portrait and lobby cores of its index, the configured
        ones and the ones the library recorded files of."""
        cores = {core for core in self.indexes[pin.key] if self.library._kind(core, pin)}
        cores.update(self.folder(pin.key).get("configured") or {})
        for folder in pin.dirs:
            cores.update(core_of(path.name) for path, _record in self.by_folder.get(folder, [])
                         if kind_of_file(path.name))
        return {self.maps[pin.key].slot(core) for core in cores}

    def entries(self, pins: list[Pin], cancel: Callable[[], bool] | None = None,
                cores: set[str] | None = None, *, background: bool = False) -> list[_Entry]:
        """Every entry that is configured, not in its intended state, or overwritten. A ``background`` pass (the
        scan, outside the library lock) pauses while a model preview is exported; callers holding the library lock
        never pause, since a preview may need that lock."""
        out = []
        for pin in pins:
            wanted = self.cores(pin)
            if cores is not None:
                wanted &= {self.maps[pin.key].slot(core) for core in cores}
            for core in sorted(wanted):
                if background:
                    preview_gate.yield_to_previews(cancel)
                if cancel and cancel():
                    return out
                try:
                    entry = self.entry(pin, core)
                except (OSError, _Unread):
                    continue
                if entry.configured or entry.state != "ok" or entry.overwritten:
                    out.append(entry)
        return out


def _outside(record: dict | None, size: int, mtime: int) -> bool:
    """Whether a record remembers the full judgement's verdict on the file as it is now (``outside``: its size and
    modification time when a switch found it changed outside the library, though the cheap rule calls it official)."""
    mark = (record or {}).get("outside")
    return isinstance(mark, list) and len(mark) == 2 and mark[0] == size and mark[1] == mtime


def signature(parts: list[_Part]) -> list[tuple[str, int, int]]:
    """An entry's files by name, size and modification time (ns): what a dismissal remembers."""
    return sorted((part.path.name, part.size, part.mtime) for part in parts)


def _record_maps(applied: dict[str, dict]) -> tuple[dict[str, list[tuple[Path, dict]]], dict[Path, list]]:
    """The library's game file records by file name and by folder."""
    by_name: dict[str, list[tuple[Path, dict]]] = {}
    by_folder: dict[Path, list[tuple[Path, dict]]] = {}
    for name, record in applied.items():
        path = Path(name)
        by_name.setdefault(path.name, []).append((path, record))
        by_folder.setdefault(path.parent, []).append((path, record))
    return by_name, by_folder


class ModLibrary:
    """Store mods, retain verified official originals, track installed sources, and keep the mod configuration and
    the Enable Mods switch (profiles.json).

    One reentrant lock serializes state changes and UnityPy work in this service; the jobs over every game folder
    (toggle, adopt, Apply Mod Configuration) take it for one entry at a time. Call long operations on the caller's
    worker thread. Every method that reads or writes game files takes an optional pin (a game folder fixed for the
    call); without one it takes the current game folder, or every known one, from the settings at the call.
    """

    def __init__(self, root: Path, catalog: Catalog, game: GameAssets | None = None, settings: Settings | None = None,
                 *, scan_allowed: Callable[[], bool] = lambda: False, data_id: str = "local",
                 write_check: Callable[[Pin], None] | None = None,
                 steam_files: steam_records.OfficialFiles | None = None) -> None:
        """``write_check(pin)`` runs right before game files of an install are written, with the other checks of a
        write, and refuses by raising ServiceError (the web UI: ``classic_open`` while a classic window is open).
        ``steam_files`` is Steam's file list cache to share (the web UI's error report reads the same one)."""
        self.root = Path(root).resolve()
        self._state_path = self.root / "state.json"
        self._prev_path = self.root / "state.json.prev"  # the last good state.json, kept before every write
        self._bad_path = self.root / "state.json.bad"  # an unreadable state.json, kept at the next write
        self.catalog = catalog
        self.game = game  # unused: the library reads game files through its pins
        self.settings = settings
        self.data_id = data_id
        self._scan_allowed_input = scan_allowed
        self._write_check = write_check
        self._lock = _LibraryLock()
        self._hashes = Fingerprints()
        self._steam_files = steam_files if steam_files is not None else steam_records.OfficialFiles()
        self._steam_roots_cache: tuple[float, list[Path]] | None = None
        self._legacy_import_matches: dict[str, str | None] = {}
        self._writing_mods = False
        self._transacting = False
        self._deferred_prunes = 0
        self._prune_pending = False
        self._recovery_error: ServiceError | None = None  # why the last recovery attempt failed: writes refuse with it
        self._recovery_seen: tuple[tuple[str, ...], float] | None = None  # journals at the last attempt, and when
        self._recovery_logged: tuple | None = None  # (code, journals) of the failure already logged
        self._waiting: set[str] = set()  # write journals waiting for their install, already logged
        self._records: dict[str, dict[str, Any]] = {}
        self._remap_previews: dict[str, dict] = {}
        self._grafts: dict[str, dict] = {}  # graft jobs between their steps (graft, graft_build, graft_check)
        self._favorites: set[str] = set()
        self._applied: dict[str, dict[str, Any]] = {}
        self._migrations: set[str] = set()
        self._migration_pending = False
        self._base: tuple[dict, set, dict, set] = ({}, set(), {}, set())  # the state as last read or written, for merges
        self._held: list[Any] = []  # state.json records this library cannot use now, written back unchanged
        self._warned: set[tuple[str, str]] = set()  # held records already logged
        self._state_good = False  # whether this instance holds state from a good read of state.json
        self._state_error: ServiceError | None = None  # why state.json cannot be written now (library_state_unreadable)
        self._reread = False  # the last read could not open state.json: read it again at the next call
        self._version = 0  # bumped by every change of _applied, so a scan can tell it is stale
        self._pruned_cores: set[str] = set()
        # per install key: (folders' signature, index, its files, generation); file name -> lower-case core
        self._indexes: dict[str, tuple[tuple, dict[str, list[Path]], frozenset, int]] = {}
        self._cab_indexes: dict[tuple[str, tuple[Path, ...]], CabIndex] = {}
        self._cores: dict[str, str] = {}
        self._packed_wanted: set[str] = set()  # packed groups of files being imported (BL-101)
        self._generation = 0
        self._maps: dict[str, tuple[int, CoreMap]] = {}
        self._names_cache: dict[str, tuple[int, dict[str, Path]]] = {}
        self._originals_cache: tuple[Any, dict[str, int]] | None = None
        # how each kept original was verified: Steam's list or the CRC rule alone (BL-194)
        self._original_checks = OriginalChecks(self.root)
        self._backfill_misses: set[tuple] = set()
        # the official rigs of portraits kept per game release, and what was matched against them (_archived_rigs)
        self.rigs = rig_archive.RigArchive(self.root / "rigs")
        self._rig_matches: dict[tuple, dict] = {}
        self._mod_masks: dict[tuple, dict] = {}
        self._impacts: dict[tuple, tuple[str, dict]] = {}  # judged impacts of game updates (_impact_of)
        self._impact_cache: dict[str, tuple[str, dict]] = {}  # the last summary per install (update_impact)
        self._untested_ok: dict[tuple[str, str], set[str]] = {}  # confirmed cores of an untested build (confirm_untested)
        self._maps_cache: tuple[tuple, Any] | None = None
        self.profiles = Profiles(self.root)
        self.profiles.gate = self._check_writes  # no change of the configuration while writes are refused
        staging.cleanup(self.root)
        self._load()
        with self._lock:
            try:
                self._recover_pending()
                self._lookup_records()
                self._migrate_collection_slots()
                self._migrate_packed_slots()
                self._known()
            except ServiceError as error:
                log_failure(logger, logging.WARNING, "Mod configuration not looked up", error)
            except Exception as error:  # the library still works without it; the next build tries again
                log_failure(logger, logging.ERROR, "Mod configuration lookup failed", error)

    # --- state.json ---

    def _parse_state(self, data: Any) -> tuple[dict, set, dict, set, list]:
        """(records, favourites, applied, migrations, held) of a state.json's JSON. A record that cannot be used (not
        an object, a key missing or of another type, a bad id) is skipped with a warning and its raw JSON is held, to
        be written back unchanged: one bad record costs that record at most, never the file. An invalid ``applied``
        entry is dropped. ValueError when the file as a whole is not a state of this version (_NewerState for a version
        above 1)."""
        if not isinstance(data, dict):
            raise ValueError("not a library state")
        version = data.get("version")
        if isinstance(version, (int, float)) and not isinstance(version, bool) and version > 1:
            raise _NewerState(version)
        if version != 1:
            raise ValueError("not a version 1 library state")
        mods, listed, written, migrated = (data.get(key, empty) for key, empty in
                                           (("mods", []), ("favorites", []), ("applied", {}), ("migrations", [])))
        if not (isinstance(mods, list) and isinstance(listed, list) and isinstance(written, dict)
                and isinstance(migrated, list)):
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
                    logger.warning("Holding a library record that cannot be used (%s): %s", reason,
                                   _held_id(raw) or "no usable id")
                held.append(raw)
                continue
            records[raw["id"]] = raw
        favorites = {key for key in listed if isinstance(key, str)}
        applied = {
            path: record for path, record in written.items()
            if isinstance(path, str) and isinstance(record, dict)
            and isinstance(record.get("source"), str) and isinstance(record.get("sha1"), str)
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
            if not isinstance(files, list) or not files or not all(
                    isinstance(f, str) and Path(f).name == f and f.endswith(".bundle") for f in files):
                return "bad files"
            if not all(isinstance(record[key], str) for key in ("label", "target", "added")):
                return "bad text"
            if not supports_kind(record["kind"]) or not isinstance(record["seen"], bool):
                return "bad kind or flag"
            if "assignment" in record and (not isinstance(record["assignment"], str) or not record["assignment"]):
                return "bad assignment"
        except (KeyError, TypeError):
            return "key missing or of another type"
        return None

    def _parse_file(self, path: Path) -> tuple[dict, set, dict, set, list]:
        return self._parse_state(json.loads(path.read_text(encoding="utf-8")))

    def _read_state(self) -> _Reading:
        """Read state.json, else (when it is unreadable or gone) its previous copy; never raises. With neither file the
        library is empty. A file of a newer build (version above 1) is never read as corrupt: the previous copy is not
        looked at. A file that cannot be opened (``OSError``: locked, scanned) is not corruption either. A missing
        file with a previous copy (a partial restore) reads the copy, as profiles.json does: nothing here deletes
        state.json, and its writes are atomic."""
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
            log_failure(logger, logging.WARNING, "Cannot read the library state, reading the previous copy instead",
                        error)
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
        """Note a reading that found no state. A library that never read a good state stays empty and cannot be
        saved (``_state_error``); one that holds good state keeps it, unless the file is a newer build's. An ``io``
        failure is read again at the next call, any other verdict stands until the file changes."""
        self._reread = reading.kind == "io"
        if reading.kind == "newer" or not self._state_good:
            self._state_error = reading.error

    def _check_writable(self) -> None:
        """Refuse a write before it starts changing files that state.json could not record: ServiceError
        ``library_state_unreadable`` (a state that cannot be read, or a newer build's) or ``library_io_error`` (a state
        that cannot be opened), as read at the last refresh."""
        if self._state_error is not None:
            raise ServiceError(self._state_error.code, **self._state_error.params)

    def _disk_held(self, reading: _Reading) -> list:
        """The held records of what was read. A previous copy is older than this instance's state: the records it held
        before are kept too."""
        held = reading.state[4] if reading.state else []
        if reading.kind == "prev":
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
        """Take in what another instance sharing the library wrote to state.json since this one last read or wrote
        it (its size or date changed), keeping this instance's own changes not saved yet. A library that could not
        read its state reads it again at every call that finds the file changed (and after a failed open). The legacy
        migration is saved when ``migrate``, and skipped (not raised) while writes are refused."""
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
        self._records, self._favorites, self._applied, self._migrations = _combine(reading.kind, base, mine, disk)
        self._held = _held_after(self._disk_held(reading), self._records, base[0], mine[0])
        self._base, self._seen = copy.deepcopy(disk[:4]), signature_
        self._state_good, self._state_error, self._reread = True, None, False
        self._version += 1
        self._migration_pending = LEGACY_REMAP not in self._migrations
        if self._migration_pending and migrate:
            self._migrate_state()

    def _migrate_state(self) -> None:
        """Save the state with the legacy remap migration, once writes are allowed: a pending recovery, a state that
        cannot be written (ServiceError) or a file that cannot be written (OSError) skips it for now, and it stays
        pending. A read never fails on it."""
        try:
            self._save()
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.DEBUG, "Library state migration skipped", error)

    def _save(self) -> None:
        """Write state.json, merged with what another instance sharing the library wrote since this one last read
        it: this instance's own changes since then (records added, changed or removed by id, game file records
        written or dropped, favourites toggled) applied to the file as it is now, under a lock both share. The
        merged state becomes this instance's only once it is written. Held records of the file are written back
        unchanged, less those this instance replaced or removed.

        The last good file is kept as state.json.prev before the write, and an unreadable one as state.json.bad. A
        library that never read a good state (``_state_good``), a file of a newer build and a file that cannot be
        opened are not written: ServiceError ``library_state_unreadable`` or ``library_io_error``, nothing changed."""
        self._check_recovery()
        path = self._state_path
        mine = (self._records, self._favorites, self._applied, self._migrations)
        with file_lock(self.root / "state.json.lock"):
            reading = self._read_state()
            if reading.error is not None and (reading.kind != "corrupt" or not self._state_good):
                raise reading.error
            base = self._base
            if reading.error is not None:
                # the file and its previous copy are unreadable, but this instance holds the state it read before:
                # it replaces the file, which is kept as .bad
                merged, held = mine, _held_after(self._held, mine[0], base[0], mine[0])
            else:
                disk = reading.state or ({}, set(), {}, set(), [])
                merged = _combine(reading.kind, base, mine, disk)
                held = _held_after(self._disk_held(reading), merged[0], base[0], mine[0])
            if LEGACY_REMAP not in merged[3]:
                merged = (legacy_remapped(merged[0]), merged[1], merged[2], merged[3] | {LEGACY_REMAP})
            payload = {"version": 1, "mods": [*merged[0].values(), *held], "favorites": sorted(merged[1]),
                       "applied": merged[2], "migrations": sorted(merged[3])}
            self._keep_state_file(reading.kind)
            atomic_json(path, payload)
            self._seen = self._state_signature()
        self._records, self._favorites, self._applied, self._migrations = merged
        self._held = held
        self._state_good, self._state_error, self._reread = True, None, False
        self._migration_pending = False
        self._base = copy.deepcopy(merged)
        self._version += 1

    def _keep_state_file(self, kind: str) -> None:
        """Before state.json is replaced: a file that was read becomes state.json.prev (the last good file); one that
        could not be read is kept as state.json.bad, and the previous copy stays what it is."""
        if kind == "ok":
            atomic_copy(self._state_path, self._prev_path)
        elif kind in ("prev", "corrupt") and self._state_path.exists():  # a missing file leaves nothing to keep
            try:
                atomic_copy(self._state_path, self._bad_path)
            except OSError as error:
                log_failure(logger, logging.WARNING, "Could not keep the unreadable library state", error)

    # --- mods ---

    @_serialized
    def assigned_targets(self) -> set[str]:
        """Explicit ownership targets represented by stored Mods, including absent local students."""
        return {record["assignment"] for record in self._records.values() if record.get("assignment")}

    @_serialized
    def variant_metadata(self, key: str) -> Any:
        """Resolve stored ownership under the library lock for read-only consumers."""
        return self._variant(key)

    def _variant(self, key: str) -> Any:
        try:
            return self.catalog.variant(key)
        except (KeyError, ServiceError) as error:
            if isinstance(error, ServiceError) and error.code != "catalog_variant_not_found":
                raise
            if callable(getattr(self.catalog, "assignment_variant", None)) and any(
                    (record.get("assignment") or record.get("target")) == key for record in self._records.values()):
                try:
                    return self.catalog.assignment_variant(key)
                except (KeyError, ServiceError) as lookup:
                    if isinstance(lookup, ServiceError) and lookup.code != "catalog_variant_not_found":
                        raise
            # Current NPC bundles may have no roster entry. Their stored filename owns the model slot.
            record = next((record for record in self._records.values() if record["kind"] == "model"
                           and record["target"] == key and core_of(record["files"][0]) == key), None)
            if record:
                from ..catalog.models import Variant
                from ..catalog._names import LANGUAGES, BASE_SHORT
                return Variant(key, key, key, {lang: {"full": key, "short": BASE_SHORT[lang]}
                                               for lang in LANGUAGES}, False, 0, None, None, None, None, None,
                               model=key)
            if isinstance(error, KeyError):
                raise ServiceError("variant_not_found", variant=key) from error
            raise

    def _record(self, mod_id: str) -> dict[str, Any]:
        try:
            return self._records[mod_id]
        except KeyError as error:
            raise ServiceError("mod_not_found", id=mod_id) from error

    def _paths(self, record: dict[str, Any]) -> list[Path]:
        directory = self.root / "mods" / record["id"]
        paths = [directory / name for name in record["files"]]
        if not directory.resolve().is_relative_to(self.root) or any(
                not path.resolve().is_relative_to(directory.resolve()) for path in paths):
            raise ServiceError("mod_unsupported", file=paths[0])
        return paths

    def _target_of(self, record: dict[str, Any]) -> str:
        """The variant a mod belongs to: the one the current catalog gives its core, else its stored target (fixed
        when it was added; a later catalog may name that core by an alias)."""
        if record.get("assignment"):
            return record["assignment"]
        if record["kind"] == "other":
            return record["target"]
        variant = self.catalog.variant_for_core(core_of(record["files"][0]))
        return variant.key if variant is not None else record["target"]

    def _repaired_id(self, record: dict[str, Any]) -> str:
        """The repaired copy a mod links to, "" when the link points at no record: an old library that deleted the copy
        keeps its id, which counts as none. A held record is there (its files are missing for now, not gone): its link
        stays, and the mod stays not repairable while it is held."""
        mod_id = record.get("repaired_id")
        if not isinstance(mod_id, str) or not mod_id:
            return ""
        return mod_id if mod_id in self._records or mod_id in self._held_ids() else ""

    @staticmethod
    def _optimized_from(record: dict[str, Any]) -> str:
        """The source an Optimize Physics copy was built from ("variant:<key>"), "" for any other mod (BL-132)."""
        source = record.get("optimized_from")
        return source if isinstance(source, str) and source.startswith("variant:") else ""

    def _entry(self, record: dict[str, Any]) -> ModEntry:
        from .lobby_camera import meta_of

        paths = self._paths(record)
        parsed = parse_filename(paths[0].name)
        # a memorial lobby camera is one of its variant's lobby choices (lobby_camera)
        camera = meta_of(record)
        # The parts of one mod can carry different dates: the mod is dated by its newest part, not by the first one.
        return ModEntry(record["id"], paths[0], self._label(record), camera["variant"] if camera else self._target_of(record),
                        "lobby" if camera else record["kind"],
                        record["added"], not record["seen"], sum(p.stat().st_size for p in paths),
                        max(date_of(path.name) for path in paths), parsed.crc,
                        camera["core"] if camera else core_of(paths[0].name),
                        repaired_from=record.get("repaired_from", ""), repaired_id=self._repaired_id(record),
                        user_added=record.get("user_added") is True, alias=record.get("alias", ""),
                        label_base=record["label"], optimized_from=self._optimized_from(record),
                        camera={key: camera[key] for key in ("source", "number", "scale", "offset")} if camera else None)

    def _label(self, record: dict[str, Any], seen: frozenset[str] = frozenset()) -> str:
        """Resolve internal aliases and repaired names without touching bundle names or bytes."""
        alias = record.get("alias")
        if isinstance(alias, str) and alias:
            return alias
        if record.get("roster_photography"):
            return t("webui.photo.modName")
        if isinstance(record.get("lobby_camera"), dict):
            return t("webui.lobby.cameraName", n=record["lobby_camera"].get("number", 1))
        if self._optimized_from(record):
            return t("webui.model.optimizedName", name=record["label"])
        source = record.get("repaired_from")
        if isinstance(source, str) and source and source not in seen:
            parent = self._records.get(source)
            name = self._label(parent, seen | {record["id"]}) if parent else record["label"]
            return t("webui.remap.name", name=name)
        return record["label"]

    @_serialized
    def mods(self) -> list[ModEntry]:
        """Return the mod entries in insertion order."""
        return [self._entry(record) for record in self._records.values()]

    @property
    def photographs(self):
        from .photographs import Photographs, student_family

        if not hasattr(self, "_photographs"):
            self._photographs = Photographs(self.root, lambda key, catalog=self.catalog: student_family(catalog, key))
        return self._photographs

    def _migrate_collection_slots(self) -> None:
        """Partition legacy shared Collection slots by batch without changing any game bytes."""
        if not any(name.lower().startswith("uis-01_common-14_charactercollect-")
                   for record in self._records.values() for name in record["files"]):
            return
        def update(data):
            changed = False
            for install, folder in data.get("folders", {}).items():
                configured = folder.get("configured", {})
                for old, entry in list(configured.items()):
                    source = entry.get("source", "")
                    record = self._records.get(source[4:], {}) if source.startswith("mod:") else {}
                    names = [name for name in record.get("files", [])
                             if name.lower().startswith("uis-01_common-14_charactercollect-")]
                    targets = {core_of(name) for name in names if "other:" + hashlib.sha256(
                        repr((parse_filename(name).prefix, parse_filename(name).res_type)).encode()).hexdigest() == old}
                    if len(targets) != 1:
                        continue
                    new = targets.pop()
                    if new == old or new in configured:
                        continue
                    configured[new] = configured.pop(old)
                    for field in ("built", "updated"):
                        if old in folder.get(field, {}):
                            folder[field][new] = folder[field].pop(old)
                    for marker in folder.get("dismissed", {}).values():
                        if isinstance(marker, dict) and old in marker:
                            marker[new] = marker.pop(old)
                    for item in data.get("unfinished", []):
                        if item.get("install") == install and item.get("core") == old:
                            item["core"] = new
                    changed = True
            return changed
        self.profiles.update(update)

    @_serialized
    def photograph_choice(self, descriptor: dict, pin: Pin | None = None) -> dict:
        from . import portrait_edit

        return portrait_edit.choice(self, descriptor, self._current(pin))

    @_serialized
    def photograph_removal_info(self, variant: str, key: str) -> dict:
        from .portrait_edit import usage_targets
        if key == 'official':
            return {'official': True, 'used': False, 'used_here': False, 'used_elsewhere': False, 'targets': []}
        self.photographs._entry(variant, key)
        uses = usage_targets(self)
        targets = sorted(uses.get(key, set()))
        return {'official': False, 'used': key in uses, 'used_here': variant in targets,
                'used_elsewhere': any(target != variant for target in targets), 'targets': targets}

    @_serialized
    @_mod_writer
    def delete_photograph(self, variant: str, key: str, confirm: bool = False, log: LogFunc = no_log) -> dict:
        from . import portrait_edit

        if key in ("official", None):
            raise ServiceError("asset_official")
        self.photographs._entry(variant, key)
        if key in portrait_edit.in_use(self) and not confirm:
            raise ServiceError("asset_delete_confirm")
        from .asset_delete import transaction
        with transaction(self) as deletion:
            if key in portrait_edit.in_use(self):
                portrait_edit.remove_photo(self, key, log)
            portrait_edit._prune_composites(self)
            deletion.remember_photo(variant, key)
            result = self.photographs.delete(variant, key, portrait_edit.in_use(self))
        return result

    @_serialized
    @_mod_writer
    def apply_photograph(self, variant: str, key: str | None, descriptor: dict, pin: Pin | None = None,
                         log: LogFunc = no_log, progress: ProgressCallback | None = None) -> ApplyResult:
        from . import portrait_edit

        target = self._variant(variant)
        if not getattr(target, "portrait", None) or target.portrait != descriptor.get("name"):
            raise ServiceError("photo_target_unavailable")
        png = self.photographs.image(variant, key) if key is not None else None
        return portrait_edit.apply(self, variant, key, png, descriptor, self._current(pin), log, progress)

    supports_kind = staticmethod(supports_kind)

    def add_files(self, kind: Kind, paths: list[Path], progress: ProgressCallback | None = None,
                  pin: Pin | None = None, *, apply: bool = False, log: LogFunc = no_log,
                  direct: bool = False) -> AddResult:
        """Import a batch of one supported kind, optionally applying through the master switch."""
        if not supports_kind(kind):
            if not paths:
                raise ServiceError("source_unsupported", kind=kind)  # no file to name
            raise ServiceError("mod_unsupported", file=paths[0], kind=kind)
        if apply:
            return self.add_and_apply(paths, progress, pin, log, kind=kind)
        return self.add(paths, progress, pin, kind=kind, direct=direct)

    @_serialized
    @_mod_writer
    def add(self, paths: list[Path], progress: ProgressCallback | None = None, pin: Pin | None = None,
            *, kind: Kind | None = None, direct: bool = False) -> AddResult:
        """Store one mod per slot and kind, whatever the dates of its parts (a part repeated under another date starts
        a version per date, judge.mod_versions), deduplicating complete bundle sets. In dev mode (devmode) only files
        inside the dev roots are taken."""
        for path in paths:
            devmode.check_read(path)
        self._note_packed(paths, pin)  # under the locks add holds: no library lock after its file locks
        return self._add(paths, progress, pin, kind, direct=direct)

    def _identity(self, files: dict[str, Path]) -> frozenset:
        return frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1) for name, path in files.items())

    @_serialized
    def import_source_identity(self, files: dict[str, Path], target: dict, originals: list[Path]) -> dict:
        """Content identities survive moves; official bytes scope conversions to their target release."""
        return {"files": sorted(([list(part_of(name)), self._hashes.get(path).sha1] for name, path in files.items()),
                                key=lambda item: json.dumps(item)),
                "target": dict(target),
                "originals": sorted([path.name, self._hashes.get(path).sha1] for path in originals)}

    def _output_identity(self, paths: list[Path]) -> list:
        """Bind every stored output hash to its complete part filename."""
        return sorted([path.name, self._hashes.get(path).sha1] for path in paths)

    @_serialized
    def find_import_source(self, source: dict) -> str | None:
        for record in self._records.values():
            matches = [item for item in record.get("import_sources", []) if item.get("source") == source]
            if matches:
                output = self._output_identity(self._paths(record))
                if any(item.get("output") == output for item in matches):
                    return record["id"]
        return None

    @_serialized
    def find_legacy_import_source(self, source: dict, kind: str) -> str | None:
        """Recognize only recorded source hashes, scoped to their target and official release.

        Entries without these records cannot be identified without a rebuild, so skip them.
        Cache both hits and misses until the source, records or stored file revisions change.
        """
        records, revisions = [], []
        for record in self._records.values():
            if record["kind"] != kind or record.get("import_sources") or not record.get("source_hashes"):
                continue
            records.append(record)
            stamps = []
            for path in self._paths(record):
                stat = path.stat()
                stamps.append((path.name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            revisions.append((record["id"], record["source_hashes"], stamps))
        key = json.dumps([source, kind, revisions], sort_keys=True)
        if key not in self._legacy_import_matches:
            match = next((record["id"] for record in records if record["source_hashes"] == source), None)
            if len(self._legacy_import_matches) >= 128:
                self._legacy_import_matches.clear()
            self._legacy_import_matches[key] = match
        return self._legacy_import_matches[key]

    @_serialized
    def legacy_import_snapshot(self, kind: str, target: str) -> list:
        """Limit legacy comparison to this asset's unlinked records and their current bytes."""
        return sorted([record["id"], self._output_identity(self._paths(record))]
                      for record in self._records.values()
                      if record["kind"] == kind and record["target"] == target
                      and not record.get("import_sources") and not record.get("source_hashes"))

    @_serialized
    def find_import_output(self, paths: list[Path], candidates: set[str] | None = None) -> str | None:
        output = self._output_identity(paths)
        records = [record for record in self._records.values()
                   if candidates is None or record["id"] in candidates]
        exact = next((record["id"] for record in records
                      if self._output_identity(self._paths(record)) == output), None)
        if exact or candidates is None:
            return exact
        # Normalize only the container packing and CRC tail. Serialized objects,
        # external references, streams and complete part filenames must still agree.
        def contents(files):
            result = []
            for path in files:
                bundle = Bundle.load(path)
                if bundle is None:
                    raise ServiceError("bundle_invalid", file=path)
                result.append([path.name, hashlib.sha256(bundle.compress("none")).hexdigest()])
            return sorted(result)
        wanted = contents(paths)
        names = {path.name for path in paths}
        for record in records:
            files = self._paths(record)
            if {path.name for path in files} == names and contents(files) == wanted:
                return record["id"]
        return None

    @_serialized
    @_mod_writer
    def remember_import_source(self, mod_id: str, source: dict) -> None:
        record = self._record(mod_id)
        item = {"source": copy.deepcopy(source),
                "output": self._output_identity(self._paths(record))}
        previous = copy.deepcopy(record)
        imports = record.setdefault("import_sources", [])
        if item in imports:
            return
        imports.append(item)
        try:
            self._save()
        except Exception:
            self._records[mod_id] = previous
            raise

    def _known(self) -> dict[frozenset, str]:
        known = {}
        for record in self._records.values():
            identity = self._identity({path.name: path for path in self._paths(record)})
            if identity in known:
                logger.warning("Duplicate library mods: %s (%s), %s (%s)", known[identity],
                               self._records[known[identity]]["label"], record["id"], record["label"])
            else:
                known[identity] = record["id"]
        return known

    def _add(self, paths: list[Path], progress: ProgressCallback | None = None, pin: Pin | None = None,
             expected_kind: Kind | None = None, *, direct: bool = False) -> AddResult:
        pin = pin or self.current_pin()
        known = self._known()
        groups: dict[tuple[str, ...], dict[str, Path]] = {}
        targets = {}
        taken: list[tuple[Path, str, str, str, str]] = []
        for i, source in enumerate(map(Path, paths)):
            if progress:
                progress(i, len(paths), t("webui.progress.add", name=source.name))
            name = source.name.removesuffix(".backup") if source.name.endswith(".bundle.backup") else source.name
            parsed = parse_filename(name)
            kind = kind_of_file(name)
            if not name.endswith(".bundle"):
                raise ServiceError("mod_unsupported", file=source)
            if not parsed.prefix or not parsed.crc or kind is None:
                fingerprint = self._hashes.get(source).sha1
                matches = [path.name for record in self._records.values() for path in self._paths(record)
                           if self._hashes.get(path).sha1 == fingerprint]
                if len({core_of(name) for name in matches}) != 1:
                    raise ServiceError("mod_unsupported", file=source)
                name = matches[0]
            name, target, kind = self._identify_import(source, name, pin)
            if expected_kind is not None and kind != expected_kind:
                raise ServiceError("mod_unsupported", file=source)
            core = self.import_core(parse_filename(name).core, kind, pin)
            taken.append((source, name, target, kind, core))
        # Model sets share their prefab slot key; other character kinds keep their physical core.
        versions = iter(self.import_versions([((core, kind), name)
                                              for _, name, _, kind, core in taken if kind != "other"], pin))
        for source, name, target, kind, core in taken:
            key = (str(source.parent), "other") if kind == "other" else (
                core, kind, next(versions))
            targets[key] = (target, kind)
            group = groups.setdefault(key, {})
            if kind == "other":
                previous = next((old for old in group if part_of(old) == part_of(name)), None)
                if previous:
                    if self._hashes.get(group[previous]).sha1 != self._hashes.get(source).sha1:
                        raise ServiceError("mod_target_ambiguous", file=source)
                    if date_of(previous) >= date_of(name):
                        continue
                    del group[previous]
            if name in group and self._hashes.get(group[name]).sha1 != self._hashes.get(source).sha1:
                raise ServiceError("mod_unsupported", file=source)
            group[name] = source

        # Validate every group before storing any part of this upload.
        whole = set()  # groups copied whole as files made for the current parts
        for key, group in groups.items():
            if direct and self.direct_import(group, pin):
                whole.add(key)
            else:
                self._check_group(group, pin, compatibility=False)
        added = AddResult()
        for key, group in groups.items():
            signature_ = self._identity(group)
            if signature_ in known:
                entry = self._entry(self._records[known[signature_]])
                if entry not in added and entry not in added.duplicates:
                    added.duplicates.append(entry)
                if self._recognize_installed(entry.id, signature_):
                    added.installed.append(entry)
                continue
            names = sorted(group)
            parsed = parse_filename(names[0])
            target, kind = targets[key]
            if kind == "other":
                target = other_target(group)
            record = self._store(group, parsed.core, target, kind, user_added=True,
                                 built_against=self._built_against(group, pin, "import", painted=key in whole))
            known[signature_] = record["id"]
            entry = self._entry(record)
            added.append(entry)
            if self._recognize_installed(entry.id, signature_):
                added.installed.append(entry)
        if progress:
            progress(len(paths), len(paths), t("webui.progress.add",
                                              name=paths[-1].name if paths else t("webui.progress.mods")))
        return added

    def _check_group(self, files: dict[str, Path], pin: Pin | None, *, compatibility: bool = True,
                     dependencies: bool = True, conversion: bool = False, variant_key: str | None = None,
                     inspection: integrity.Inspection | None = None, built: dict | None = None) -> dict:
        """``built`` is the stored fingerprint of the files when they are a library mod's (``_read_built_against``).
        The report's ``rig`` names what the official rig changed under the mod: atlases repacked since its textures
        were drawn (``layout``), animations its skeleton lacks and regions its atlas lacks (``atlas_remap.rig_gains``).
        ``remappable`` is any of them or a page size the texture does not fit (``misfit``)."""
        inspect = inspection.inspect if inspection else integrity.inspect
        cab_inspection = inspection or integrity.Inspection()
        selected, official, effective = integrity.Contents(), integrity.Contents(), integrity.Contents()
        baseline = integrity.Contents()
        sources = {name: inspect(path, allow_unknown=True) if conversion else inspect(path)
                   for name, path in files.items()}
        core = core_of(next(iter(files)))
        kind = kind_of_file(next(iter(files)))
        target_core = core
        targets = (self._other_targets(files, pin) if kind == "other" else self._spine_parts(core, pin)) if pin else []
        if pin and not targets and variant_key and kind != "other":
            target_core = self._core_map(pin).core(variant_key, kind) or core
            targets = self._spine_parts(target_core, pin)
        if targets and kind not in ("other", "model"):
            target_core = parse_filename(targets[0].name).core
        originals, original_paths, current_parts = {}, [], {}
        unavailable = not targets
        for target in targets:
            try:
                original = self._official_for(target, pin, game_first=True)
                originals[target] = inspect(original)
                original_paths.append(original)
            except ServiceError as error:
                if error.code not in ("original_unavailable", "original_invalid"):
                    raise
                unavailable = True
                current_parts[target] = inspect(target)
        for contents in originals.values():
            official.merge(contents)
        baseline_parts = {**current_parts, **originals}
        for contents in baseline_parts.values():
            baseline.merge(contents)
        expected = official.platforms or {"StandaloneWindows64"}
        for name, contents in sources.items():
            if not conversion:
                integrity.check_platform(contents, files[name], expected)
            selected.merge(contents)
        # Compatibility may address a variant whose installed core changed after this Mod was stored.
        target_names = {name.replace(f"-{parse_filename(name).core}-", f"-{target_core}-", 1): path
                        for name, path in files.items()} if target_core != core and kind != "other" else files
        resolution = self._resolve(target_names, targets, pin, cab_inspection)
        replacements = resolution.current
        replaced = set(replacements)
        older = resolution.older
        check_dependencies = dependencies and (not compatibility or kind in ("model", "other"))
        if older and kind == "other":
            replaced.update(self._other_targets(older, pin) if pin else [])
        elif older:
            # The dependency check leaves out every part the files write; the Spine comparison, as the preview, only
            # the parts they stand in for.
            replaced.update(resolution.updated if check_dependencies else resolution.replaced)
        for target, contents in baseline_parts.items():
            if target not in replaced:
                effective.merge(contents)
        if not compatibility:
            # Import validates dependencies only; absent text parts use the current official assets.
            paired = atlas_remap.pair_atlases(selected.atlases, baseline.atlases)
            selected.atlases = {**{name: data for name, data in baseline.atlases.items() if name not in paired},
                               **selected.atlases}
        effective.merge(selected)
        missing = (integrity.missing_parts(effective) - integrity.missing_parts(baseline)
                   if check_dependencies else set())
        if missing and pin:
            references = missing & effective.references
            if references:
                missing.difference_update(self._cab_index(pin).resolve(references, targets[0] if targets else None,
                                                                     exclude=replaced))
        if missing:
            if inspection is not None:
                inspection.missing_cabs.update(missing & effective.references)
            # Name omitted files from the selected folders, without importing them.
            names = dict(effective.reference_names)
            for target in replaced:
                # CABs removed by this upload identify the omitted replacement file, even without an original.
                with contextlib.suppress(ValueError):
                    names.update({name: target.name for name in cab_inspection.cabs(target)})
                if target in baseline_parts:
                    names.update({name: target.name for name in baseline_parts[target].files})
            for folder in {path.parent for path in files.values()}:
                if cab_inspection.header_only_missing:
                    continue
                for path in sorted([*folder.glob("*.bundle"), *folder.glob("*.bundle.backup")]):
                    if path not in files.values() and core_of(path.name) == core:
                        try:
                            contents = inspect(path)
                        except ServiceError:
                            continue
                        names.update({name: path.name for name in contents.files})
                        names.update({name + ".png": path.name for name in contents.textures})
            raise ServiceError("mod_parts_missing", files=", ".join(sorted({names.get(name, name) for name in missing})))
        if not compatibility:
            return {}
        current = (self._replacements(files, targets, pin, cab_inspection)
                   if target_names is not files else replacements)
        report = {"kind": kind, "official": official.spine(), "mod": effective.spine(), "unavailable": unavailable,
                "outdated": bool(targets) and len(current) != len(files)}
        if conversion:
            # Internal preflight metadata; ordinary compatibility RPCs remain JSON-only.
            report["sourceContents"], report["officialContents"] = selected, official
            report["originalPaths"] = original_paths
        try:
            mixed = {name: data for name, data in effective.atlases.items() if name not in selected.atlases
                     and any(integrity.texture_key(page) in selected.textures
                             for page in atlas_remap.parse_atlas(data, name).pages)}
            misfits = {name for name, data in mixed.items()
                       if atlas_remap.page_size_mismatch({name: data}, effective.textures)}
            misfit = bool(misfits)
            layout = fingerprint.changed_layouts(built, mixed)
            if kind == "spr" and not unavailable:
                # Where no record keeps the rig the mod's textures were drawn for, the earlier release the archive
                # keeps that they follow (rig_archive), against the installed pages of the verified official copies.
                unrecorded = {name: data for name, data in mixed.items()
                              if not isinstance((layout.get(name) or {}).get("skeleton"), str)}
                layout = {**layout, **self._archived_rigs(list(files.values()), unrecorded,
                                                          lambda: self._official_page_masks(original_paths))}
            gains = atlas_remap.rig_gains(selected.atlases, selected.skeletons, official.atlases, official.skeletons)
            rerig = atlas_remap.rerigged(gains, selected.atlases, selected.skeletons, official.atlases,
                                         official.skeletons, selected.textures)
            report["rig"] = {"layout": sorted(layout), "animations": gains["animations"],
                             "regions": {name: gains["regions"][name] for name in rerig if name in gains["regions"]}}
            # The rig a repacked atlas's textures were drawn for, where it was recorded: the source of the
            # world-space rebake (rig-geometry.js forRemap).
            recorded = {name: entry for name, entry in layout.items() if isinstance(entry.get("skeleton"), str)}
            dated = (self._dated_rig(list(files.values()), misfits - recorded.keys(), official.atlases, original_paths)
                     if kind == "spr" and not unavailable else {})
            # repacked since the textures were drawn, with no rig to repair them from (mod-compatibility.js tells it)
            report["rig"]["unknown"] = sorted(name for name, entry in layout.items()
                                              if not isinstance(entry.get("skeleton"), str)
                                              and not isinstance((dated.get(name) or {}).get("skeleton"), str))
            drawn_by = atlas_remap.pair_atlases(official.skeletons, dict.fromkeys({**dated, **recorded}))
            report["source"] = {"atlases": {name: entry["text"] for name, entry in {**dated, **recorded}.items()},
                                "skeletons": {}}
            for name, entry in {**dated, **recorded}.items():
                if not isinstance(entry.get("skeleton"), str):
                    continue
                data = base64.b64decode(entry["skeleton"])
                skeleton = drawn_by.get(name) or f"{Path(name).stem}.skel"
                report["source"]["skeletons"][skeleton] = {"version": get_skel_version(data), "data": entry["skeleton"]}
            # The whole rig a texture-only portrait's textures were drawn for (``old_rig``): what Update offers for
            # a portrait the game drew anew, which the rebake cannot move (mod-compatibility.js).
            old_rig = (atlas_remap.old_rig_plan({**layout, **dated}, official.atlases, official.skeletons,
                                                selected.skeletons, selected.textures, official.textures)
                       if kind == "spr" else atlas_remap.OldRig())
            report["oldRig"] = {"atlases": sorted(old_rig.atlases), "animations": old_rig.animations,
                                "available": not unavailable and old_rig.available}
            # The graft (bmpt.graft): the mod's own binary skeleton keeps its rig and takes the animations the
            # official one gained; or the old rig above takes them, where they keep it from being offered.
            own = {name: names for name, names in gains["animations"].items() if self._graftable(
                selected, official, name)} if kind == "spr" else {}
            gained = old_rig.animations if kind == "spr" and old_rig.atlases and old_rig.complete else {}
            report["graft"] = {"kind": "own" if own else "oldRig" if gained else None,
                               "animations": own or gained, "available": not unavailable and bool(own or gained)}
            report["misfit"] = not unavailable and misfit
            # Portraits are baked through world space; other kinds only move regions onto a page size that fits.
            wanted = misfit or bool(recorded) or bool(rerig) if kind == "spr" else misfit
            report["remappable"] = not unavailable and wanted
        except (ValueError, UnicodeError):
            report["rig"] = {"layout": [], "animations": {}, "regions": {}, "unknown": []}
            report["source"] = {"atlases": {}, "skeletons": {}}
            report["oldRig"] = {"atlases": [], "animations": {}, "available": False}
            report["graft"] = {"kind": None, "animations": {}, "available": False}
            report["misfit"] = report["remappable"] = False
        return report

    def _graftable(self, selected: integrity.Contents, official: integrity.Contents, skeleton: str) -> bool:
        """Whether the mod carries a binary 4.2 skeleton for the official ``skeleton`` and the atlas of its stem
        with the pages of that atlas."""
        own = atlas_remap.pair_atlases(selected.skeletons, official.skeletons).get(skeleton)
        atlas = next((name for name, other in atlas_remap.pair_atlases(official.skeletons, official.atlases).items()
                      if other == skeleton), None)
        source = atlas_remap.pair_atlases(selected.atlases, official.atlases).get(atlas) if atlas else None
        if own is None or source is None or not self._binary_42(selected.skeletons[own]):
            return False
        try:
            pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(selected.atlases[source]).pages}
        except (ValueError, UnicodeError):
            return False
        return bool(pages) and pages <= set(selected.textures)

    @_serialized
    def compatibility_stamp(self, mod_id: str, pin: Pin | None = None) -> str:
        """Read-only identity of the Mod and every candidate official source used by compatibility checks."""
        record = self._record(mod_id)
        pin = pin or self.current_pin()
        files = {path.name: path for path in self._paths(record)}
        core = core_of(next(iter(files)))
        kind = kind_of_file(next(iter(files)))
        targets = (self._other_targets(files, pin) if kind == "other" else self._spine_parts(core, pin)) if pin else []
        if pin and not targets and kind != "other":
            target_core = self._core_map(pin).core(self._target_of(record), kind) or core
            targets = self._spine_parts(target_core, pin)
        paths = set(files.values()) | set(targets)
        for target in targets:
            paths.update([self._original_path(target), target.with_name(target.name + ".backup"), *self._elsewhere(target)])
        stamps = []
        for path in sorted(paths):
            try:
                stat = path.stat()
                stamp = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            except FileNotFoundError:
                stamp = None
            stamps.append([str(path.resolve()), stamp])
        identity = [pin.key if pin else None, self._target_of(record), record.get("repaired_from"),
                    self._repaired_id(record) or None, stamps]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @_serialized
    def compatibility(self, mod_id: str, pin: Pin | None = None,
                      progress: Callable[[dict], None] | None = None) -> dict:
        """Fresh official and effective Spine data, without building or rewriting a mod."""
        record = self._record(mod_id)
        if progress:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
        report = self._check_group({path.name: path for path in self._paths(record)}, pin or self.current_pin(),
                                   variant_key=self._target_of(record), built=self._read_built_against(record))
        report["repaired_from"], report["repaired_id"] = record.get("repaired_from", ""), self._repaired_id(record)
        report["remappable"] = report["remappable"] and not (report["repaired_from"] or report["repaired_id"])
        old_rig = report.setdefault("oldRig", {"atlases": [], "animations": {}, "available": False})
        old_rig["available"] = old_rig["available"] and report["remappable"]
        graft = report.setdefault("graft", {"kind": None, "animations": {}, "available": False})
        graft["available"] = graft["available"] and not (report["repaired_from"] or report["repaired_id"])
        return report

    @_serialized
    def identify_import(self, path: Path, name: str, pin: Pin | None = None) -> tuple[str, str, str]:
        """Current official slots own their identity even when a Mod renames its assets."""
        return self._identify_import(path, name, pin)

    def _identify_import(self, path: Path, name: str, pin: Pin | None = None) -> tuple[str, str, str]:
        if kind_of_file(name) == "other":
            return identify(path, name, self.catalog, filename_only=True)
        pin = pin or self.current_pin()
        targets = self._spine_parts(core_of(name), pin) if pin else []
        named = any(integrity.replacement_key(target.name) == integrity.replacement_key(name) for target in targets)
        if not named:
            named = any(integrity.replacement_key(kept) == integrity.replacement_key(name)
                        and self._is_official_as(self.root / "originals" / kept, kept) for kept in self._originals())
        return identify(path, name, self.catalog, filename_only=named)

    def import_core(self, core: str, kind: str, pin: Pin | None = None) -> str:
        """The import set's core key: a model's prefab slot, otherwise its unchanged physical core."""
        core = core.lower()
        if kind != "model":
            return core
        pin = pin or self.current_pin()
        return self._core_map(pin).slot(core) if pin else core

    def import_versions(self, entries: Sequence[tuple[Hashable, str]], pin: Pin | None = None) -> list[str]:
        """The common Add/batch version rule. Callers key model sets by import_core, across all physical cores."""
        pin = pin or self.current_pin()
        installed = (path.name for paths in self._index(pin).values() for path in paths) if pin else ()
        return mod_versions(entries, installed)

    def _cab_index(self, pin: Pin) -> CabIndex:
        key = (pin.key, tuple(pin.dirs))
        if key not in self._cab_indexes:
            self._cab_indexes[key] = CabIndex(pin.dirs)
        return self._cab_indexes[key]

    def _readers(self, targets: Sequence[Path], pin: Pin | None, inspection: integrity.Inspection | None = None,
                 *, keys: bool = True, release: bool = True) -> resolver.Readers:
        """What the resolver reads of a file: its CABs, asset paths and (with ``keys``) asset names and types; a file
        of ``targets`` is read from its official copy where there is one, else as it is. Asset evidence is on top of
        the name rules: a file the readers cannot read gives none. With ``release``, a file naming a part without its
        CRC or by its CABs is that part's current release only with the part's structure (``Inspection.structure``)."""
        inspection = inspection or integrity.Inspection()
        members, baselines = set(targets), {}

        def baseline(path: Path) -> Path:
            if pin is None or path not in members:
                return path
            if path not in baselines:
                try:
                    baselines[path] = self._official_or_current(path, pin)
                except ServiceError:
                    baselines[path] = path
            return baselines[path]

        def cabs(path: Path):
            return inspection.cabs(self._official_or_current(path, pin) if pin and path in members else path)

        def paths(path: Path) -> set[str]:
            try:
                return set(inspection.inspect(baseline(path), allow_unknown=True).paths)
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No asset paths read from %s", path.name, exc_info=True)
                return set()

        def asset_keys(path: Path) -> set:
            if not keys:
                return set()
            try:
                return set(inspection.keys(baseline(path)))
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No asset keys read from %s", path.name, exc_info=True)
                return set()

        def same_release(source: Path, target: Path) -> bool:
            # A part rebuilt by a game update keeps its name (less the CRC) and CABs. Only a file of the part's
            # structure as it is now replaces it whole; unread files give no evidence and keep the name rule.
            try:
                return inspection.structure(source) == inspection.structure(baseline(target))
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No structure read for %s", source.name, exc_info=True)
                return True

        return resolver.Readers(cabs, paths, asset_keys, same_release if release else resolver.NAMES_ONLY.release)

    def _resolve(self, files: dict[str, Path], targets: Sequence[Path], pin: Pin | None,
                 inspection: integrity.Inspection | None = None, *, whole: bool = True,
                 keys: bool = True, release: bool = True) -> resolver.Resolution:
        """The one rule for the official parts these files replace or update (``targets.resolve``)."""
        return resolver.resolve(files, targets, self._readers(targets, pin, inspection, keys=keys, release=release),
                                whole=whole)

    def _replacements(self, files: dict[str, Path], targets: list[Path], pin: Pin | None,
                      inspection: integrity.Inspection | None = None, *, release: bool = True) -> dict[Path, Path]:
        """The files that are a part's current release: {target: source}. ``release=False`` takes the names and
        CABs alone, for a caller that judges the release itself."""
        return self._resolve(files, targets, pin, inspection, release=release).current

    def _update_groups(self, older: dict[str, Path], targets: Sequence[Path], pin: Pin,
                       inspection: integrity.Inspection | None = None) -> list[PlannedGroup]:
        """Install groups for files of older releases or other builds (every file treated as one)."""
        return self._groups_of(self._resolve(older, targets, pin, inspection, whole=False))

    @staticmethod
    def _groups_of(resolution: resolver.Resolution) -> list[PlannedGroup]:
        """Install groups for the files the resolution does not replace whole: per name prefix and core, the parts
        they update (groups reaching a common part update it together); files that found no part are NOT_FOUND."""
        planned: list[PlannedGroup] = []
        for group in resolution.groups:
            parsed = parse_filename(group.names[0])
            if not group.targets:
                planned.append(PlannedGroup(parsed.prefix, parsed.category, parsed.core, [
                    PlannedFile(path, parse_filename(path.name), Action.NOT_FOUND) for path in group.sources]))
                continue
            merged = PlannedGroup(parsed.prefix, parsed.category, parsed.core, [
                PlannedFile(path, parse_filename(path.name), Action.UPDATE)
                for path in group.sources], target_paths=sorted(group.targets))
            for other in [other for other in planned if set(other.target_paths) & set(merged.target_paths)]:
                planned.remove(other)
                merged.files[:0] = other.files
                merged.target_paths = sorted(set(other.target_paths) | set(merged.target_paths))
            planned.append(merged)
        return planned

    def _replaced_parts(self, files: dict[str, Path], targets: list[Path], pin: Pin | None) -> set[Path]:
        """The parts a preview drops for these files (``Resolution.replaced``)."""
        return self._resolve(files, targets, pin).replaced

    def direct_import(self, files: dict[str, Path], pin: Pin | None = None, *,
                      inspection: integrity.Inspection | None = None) -> dict | None:
        """Validate current parts and the resulting set before admitting a whole-file copy."""
        pin = pin or self.current_pin()
        if not pin:
            return None
        name = next(iter(files))
        targets = (self._other_targets(files, pin) if kind_of_file(name) == "other"
                   else self._spine_parts(core_of(name), pin))
        inspection = inspection or integrity.Inspection()
        # Named for current parts: this check judges their release itself and refuses another one by name.
        replacements = self._replacements(files, targets, pin, inspection, release=False)
        if len(replacements) != len(files):
            return None
        selected, official, originals = integrity.Contents(), integrity.Contents(), []
        identities = []
        for target, source in replacements.items():
            try:
                original = self._official_for(target, pin, game_first=True)
            except ServiceError as error:
                if error.code != "original_unavailable":
                    raise
                # Current slots can accept whole files without an official baseline. The live file is
                # format evidence only; it must never enter originals or a conversion's baselines.
                inspection.check(source)
                selected.merge(inspection.inspect(source))
                integrity.check_format(inspection.metadata(source), source, inspection.metadata(target))
                continue
            incoming, baseline = inspection.metadata(source), inspection.metadata(original)
            integrity.check_format(incoming, source, baseline)
            identities.append((source, incoming, baseline))
            selected.merge(inspection.inspect(source))
            official.merge(inspection.inspect(original))
            originals.append(original)
        target_origin = mod_origin.target_of(self.settings, pin, official)
        evidence, error = mod_origin.constraints(set(files), selected, official, target_origin)
        if error or (evidence.get("server") and evidence["server"] != target_origin["server"]):
            raise ServiceError(error or "source_conflict")
        # Retained official prefabs must still resolve into the replacement CABs.
        compatibility = self._check_group(files, pin, conversion=True, inspection=inspection)
        self._check_group(files, pin, compatibility=False, inspection=inspection)
        for source, incoming, baseline in identities:
            if not incoming.files:
                raise ServiceError("mod_release_mismatch", file=source.name)
            if kind_of_file(name) == "model":
                self._check_model_references(source, incoming, baseline, replacements, targets, pin, inspection)
            elif incoming.files != baseline.files or incoming.layout != baseline.layout:
                raise ServiceError("mod_release_mismatch", file=source.name)
        inspection.validate()
        source_origin, error = mod_origin.source_of(set(files), selected, official, target_origin)
        if error == "source_unknown" and compatibility["unavailable"]:
            # The current slot already identifies a direct replacement; absent official CAB evidence
            # must not turn the missing baseline into an import refusal.
            evidence, error = mod_origin.constraints(set(files), selected, official, target_origin)
            source_origin = {**target_origin, **{key: value for key, value in evidence.items() if value},
                             "detected": False}
        if error:
            raise ServiceError(error)
        return {"direct": replacements, "targets": list(replacements), "originals": originals,
                "sourceContents": selected, "officialContents": official, "source": source_origin,
                "compatibility": compatibility}

    def _check_model_references(self, source: Path, incoming: integrity.Contents, baseline: integrity.Contents,
                                replacements: dict[Path, Path], targets: list[Path], pin: Pin,
                                inspection: integrity.Inspection) -> None:
        """Only changes to objects consumed outside a replaced part can break its public interface."""
        changed = {}
        for cab, layout in baseline.layout.items():
            replacement = dict(incoming.layout.get(cab, ()))
            ids = {path_id for path_id, kind in layout if replacement.get(path_id) != kind}
            if ids:
                changed[cab] = ids
        removed_cabs = baseline.files - incoming.files
        if not changed and not removed_cabs:
            return  # Preserving every original object is safe without scanning other bundles.
        consumers = {path: path for paths in self._index(pin).values() for path in paths}
        for target in targets:
            consumers[target] = (replacements[target] if target in replacements else
                                 self._official_or_current(target, pin))
        for path in consumers.values():
            if path != source and integrity.references_changed_objects(path, changed, removed_cabs, inspection):
                raise ServiceError("mod_release_mismatch", file=source.name)

    @_serialized
    def inspect_import(self, files: dict[str, Path], pin: Pin | None = None, *, conversion: bool = False,
                       inspection: integrity.Inspection | None = None) -> dict:
        """Preflight canonical upload names without storing or adopting anything.

        Use the same identities, dependency checks and installed-content judgement as add().
        Original recovery is deliberately separate: an already installed upload can be recognized
        even when an outside installer left no official copy behind.
        """
        for path in files.values():
            devmode.check_read(path)
            if inspection:
                inspection.check(path)
        identity = self._identity(files)
        if inspection:
            inspection.validate()
        duplicate = self._known().get(identity)
        core_name = core_of(next(iter(files)))
        pin = pin or self.current_pin()
        generic = kind_of_file(next(iter(files))) == "other"
        targets = []
        if pin:
            try:
                targets = self._other_targets(files, pin) if generic else self._spine_parts(core_name, pin)
            except ServiceError:
                if not duplicate:
                    raise
        installed = False
        if pin:
            view = self._view([pin], "full")
            entry = view.entry(pin, core_name)
            if generic:
                entry.parts = [view.part(path, pin, "other") for path in targets]
            installed = self._matches_installed(identity, entry)
        if (duplicate or installed) and not conversion:
            return {"identity": identity, "libraryId": duplicate, "installed": installed, "targets": targets}
        rebuild = False
        try:
            direct = self.direct_import(files, pin, inspection=inspection) if conversion else None
        except ServiceError as error:
            if (duplicate or installed) and error.code in ("original_unavailable", "original_invalid"):
                return {"identity": identity, "libraryId": duplicate, "installed": installed,
                        "targets": targets, "recognized": True}
            if kind_of_file(next(iter(files))) not in ("spr", "lobby") or error.code not in (
                    "mod_platform_mismatch", "mod_platform_unknown", "mod_unity_version_mismatch",
                    "mod_release_mismatch", "source_conflict"):
                raise
            # Current Spine filenames identify slots, not a compatible whole-file format.
            # Preserve this verdict even on the same platform: extraction and target
            # rebuilding must succeed before add() validates and stores the output.
            direct, rebuild = None, True
        if direct:
            return {"identity": identity, "libraryId": duplicate, "installed": installed, **direct}
        report = self._check_group(files, pin, dependencies=False, conversion=conversion, inspection=inspection)
        # Old CAB references are replaced by the rebuild. Its output goes through add()'s full preflight.
        if not report["outdated"] and not conversion:
            self._check_group(files, pin, compatibility=False, inspection=inspection)
        originals = report.pop("originalPaths", []) if conversion else (
            [self._official_for(path, pin) for path in targets] if generic else [])
        return {"identity": identity, "libraryId": duplicate, "installed": installed, "compatibility": report,
                "targets": targets, "originals": originals if generic else [], "rebuild": rebuild}

    @_serialized
    def apply_import(self, variant_key: str, source: str, *, kind: Kind, pin: Pin,
                     settings: Settings, backup: bool, log: LogFunc = no_log,
                     progress: ProgressCallback | None = None) -> ApplyResult:
        """Apply with one import's save settings, retaining the normal switch and transaction checks.

        The settings adapter is local to this serialized operation; neither config.toml nor
        the shared Settings object changes. Deferred configurations use the usual switch flow.
        """
        previous = self.settings
        self.settings = settings
        try:
            return self.apply(variant_key, source, kind=kind, pin=pin, log=log,
                              progress=progress, create_backup=backup)
        finally:
            self.settings = previous

    def add_and_apply(self, paths: list[Path], progress: ProgressCallback | None = None,
                      pin: Pin | None = None, log: LogFunc = no_log, *, kind: Kind | None = None) -> AddResult:
        """The web add action: store, check, then use the same switch as a source selection."""
        pin = pin or self.current_pin()
        added = self.add(paths, progress, pin=pin, kind=kind)
        for entry in added:
            try:
                result = self.apply(entry.target, f"mod:{entry.id}", kind=entry.kind, pin=pin, log=log,
                                    progress=progress)
            except ServiceError as error:
                added.failed.append({"id": entry.id, **error.to_json()})
                if (pin is not None and entry.kind != "other"
                        and self.configured(entry.target, pin).get(entry.kind) == f"mod:{entry.id}"):
                    added.configured.append(entry)
            else:
                (added.configured if result.deferred else added.applied).append(entry)
                added.kept_current |= result.kept_current
                if result.incomplete:
                    added.incomplete.append(entry.id)
        return added

    @_serialized
    @_mod_writer
    def add_assigned_files(self, files: dict[str, Path], variant: str, kind: str,
                           pin: Pin | None = None) -> AddResult:
        """Store an explicitly attributed, self-contained Mod without claiming an install target."""
        from .manual_identity import validate

        self.catalog.assignment_variant(variant)
        validate(files, kind)
        signature = self._identity(files)
        known = self._known().get(signature)
        result = AddResult()
        if known:
            record = self._record(known)
            if self._target_of(record) != variant or record["kind"] != kind:
                raise ServiceError("mod_target_mismatch", id=known, variant=variant)
            result.duplicates.append(self._entry(record))
            return result
        record = self._store(files, variant, variant, kind, user_added=True, assignment=variant)
        result.append(self._entry(record))
        return result

    def _store(self, files: dict[str, Path], label: str, target: str, kind: str,
               *, repaired_from: str = "", user_added: bool = False, assignment: str = "",
               metadata: dict[str, Any] | None = None, built_against: dict | None = None) -> dict[str, Any]:
        """Publish a complete directory before its metadata, with durable recovery of either outcome.
        ``built_against`` (``_built_against``) is stored with the files, as part of the same directory."""
        names = sorted(files)
        if not names:
            raise ServiceError("source_unsupported")  # no file to name
        refused = next((name for name in names if Path(name).name != name or not name.endswith(".bundle")), None)
        if refused is not None:
            raise ServiceError("mod_unsupported", file=refused)
        mod_id = uuid.uuid4().hex
        directory = self.root / "mods" / mod_id
        transaction = self.root / "import-transactions" / mod_id
        journal = transaction / "journal.json"
        staged = transaction / "files"
        if directory.parent.is_symlink() or transaction.parent.is_symlink() or directory.exists() or transaction.exists():
            raise ServiceError("library_io_error", file=transaction)
        record = {"id": mod_id, "files": names, "label": label, "target": target,
                  "kind": kind, "added": datetime.now(timezone.utc).isoformat(), "seen": False,
                  "user_added": user_added}
        if metadata:
            if set(metadata) & set(record):
                raise ValueError("Generated Mod metadata overrides a reserved field")
            record.update(copy.deepcopy(metadata))
        if repaired_from:
            record["repaired_from"] = repaired_from
        if assignment:
            record["assignment"] = assignment
        hashes = {name: self._hashes.get(files[name]).sha1 for name in names}
        data = {"version": 1, "id": mod_id, "phase": "preparing", "record": record, "hashes": hashes,
                "repaired_from": repaired_from,
                "old_repaired_id": self._record(repaired_from).get("repaired_id") if repaired_from else None}
        atomic_json(journal, data)
        try:
            for name in names:
                atomic_copy(files[name], staged / name)
                verify_file(staged / name, hashes[name])
                if self._hashes.get(files[name]).sha1 != hashes[name]:
                    raise ServiceError("entry_changed", core=core_of(name))
            if built_against is not None:
                try:
                    fingerprint.write(staged, built_against, hashes)
                except OSError as error:  # the mod is whole without it
                    log_failure(logger, logging.WARNING, "Could not store the official parts' fingerprint", error)
            data["phase"] = "publishing"
            atomic_json(journal, data)
            directory.parent.mkdir(parents=True, exist_ok=True)
            sync_directory(self.root)
            os.replace(staged, directory)
            sync_directory(directory.parent)
            sync_directory(transaction)
            self._records[mod_id] = record
            if repaired_from:
                self._record(repaired_from)["repaired_id"] = mod_id
            self._save()
        except Exception as error:
            try:
                data["phase"] = "rollback"
                atomic_json(journal, data)
                self._rollback_import(journal, data)
            except Exception as recovery_error:
                log_failure(logger, logging.ERROR, "Mod import rollback pending", recovery_error)
                raise ServiceError("library_recovery_pending", file=journal) from error
            raise
        # Metadata publication is the commit point. A leftover journal is safe:
        # recovery verifies the complete committed group before removing it.
        try:
            shutil.rmtree(transaction)
            sync_directory(transaction.parent)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Committed import journal retained", error)
        return record

    def _rollback_import(self, journal: Path, data: dict) -> None:
        mod_id = data["id"]
        directory = self.root / "mods" / mod_id
        if (re.fullmatch(r"[0-9a-f]{32}", mod_id) is None or directory.parent.is_symlink()
                or directory.is_symlink() or not directory.resolve().is_relative_to(self.root)
                or journal.is_symlink() or journal.parent.is_symlink()
                or not journal.resolve().is_relative_to(self.root / "import-transactions")):
            raise ServiceError("library_io_error", file=journal)
        self._records.pop(mod_id, None)
        repaired = self._records.get(data["repaired_from"])
        if repaired and repaired.get("repaired_id") == mod_id:
            if data["old_repaired_id"] is None:
                repaired.pop("repaired_id", None)
            else:
                repaired["repaired_id"] = data["old_repaired_id"]
        path = self._state_path
        with file_lock(self.root / "state.json.lock"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = None
            if state is not None:
                if isinstance(state, dict) and isinstance(state.get("version"), (int, float)) \
                        and not isinstance(state["version"], bool) and state["version"] > 1:
                    raise ServiceError("library_state_unreadable", file=path)
                if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("mods"), list):
                    raise ServiceError("library_io_error", file=path)
                before = copy.deepcopy(state)
                # held records are raw JSON, whatever their shape: only an object with this id goes
                state["mods"] = [record for record in state["mods"]
                                 if not (isinstance(record, dict) and record.get("id") == mod_id)]
                for record in state["mods"]:
                    if (isinstance(record, dict) and record.get("id") == data["repaired_from"]
                            and record.get("repaired_id") == mod_id):
                        if data["old_repaired_id"] is None:
                            record.pop("repaired_id", None)
                        else:
                            record["repaired_id"] = data["old_repaired_id"]
                if state != before:
                    # the file just read is the last good one; a rollback does not wait for its copy
                    try:
                        atomic_copy(path, self._prev_path)
                    except OSError as error:
                        log_failure(logger, logging.WARNING, "Could not keep the previous library state", error)
                    atomic_json(path, state)
                    self._held = [raw for raw in self._held if _held_id(raw) != mod_id]
            # A prior replace may have succeeded before its directory flush failed.
            # Prove the metadata removal durable even when this retry changes no JSON.
            sync_directory(self.root)
        self._seen = None
        self._refresh_state()
        if directory.exists():
            shutil.rmtree(directory)
            sync_directory(directory.parent)
        shutil.rmtree(journal.parent)
        sync_directory(journal.parent.parent)

    def _recover_imports(self) -> None:
        root = self.root / "import-transactions"
        if root.is_symlink():
            raise ServiceError("library_io_error", file=root)
        journals = sorted(root.glob("*/journal.json"))
        if not journals:
            return
        with file_lock(self.root / "mods.lock"):
            for journal in journals:
                try:
                    if (journal.is_symlink() or journal.parent.is_symlink()
                            or not journal.resolve().is_relative_to(root.resolve())):
                        raise ValueError("Invalid import journal path")
                    data = json.loads(journal.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("operation") == "update":
                        self._recover_update(journal, data)
                        continue
                    mod_id, record, hashes = data["id"], data["record"], data["hashes"]
                    if (not isinstance(mod_id, str) or re.fullmatch(r"[0-9a-f]{32}", mod_id) is None
                            or journal != root / mod_id / "journal.json" or journal.is_symlink()
                            or journal.parent.is_symlink() or not isinstance(record, dict)
                            or record.get("id") != mod_id or not isinstance(hashes, dict)
                            or data["version"] != 1 or data["phase"] not in ("preparing", "publishing", "rollback")
                            or not record.get("files") or set(record["files"]) != set(hashes)
                            or any(not isinstance(name, str) or Path(name).name != name or not name.endswith(".bundle")
                                   or not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None
                                   for name, value in hashes.items())):
                        raise ValueError("Invalid import journal")
                    directory = self.root / "mods" / mod_id
                    if (directory.parent.is_symlink() or directory.is_symlink()
                            or not directory.resolve().is_relative_to(self.root)):
                        raise ValueError("Invalid import directory")
                    committed = self._records.get(mod_id)
                    if data["phase"] == "publishing" and committed and committed.get("files") == record["files"]:
                        for name, digest in hashes.items():
                            if (directory / name).is_symlink():
                                raise ValueError("Invalid imported file")
                            verify_file(directory / name, digest)
                        sync_directory(directory)
                        sync_directory(directory.parent)
                        sync_directory(self.root)
                        shutil.rmtree(journal.parent)
                        sync_directory(root)
                    else:
                        self._rollback_import(journal, data)
                except Exception as error:
                    raise ServiceError("library_recovery_pending", file=journal) from error

    @_mod_writer
    def _add_group(self, paths: list[Path], label: str, pin: Pin) -> tuple[str, bool]:
        """Store the parts of one core as one mod, whatever their dates, keyed by the variant key the install's own
        core map gives the core (a core the current catalog does not map is stored too). Returns the id of the mod
        and whether it was created: a part set already in the library is not stored twice."""
        files = {path.name: path for path in paths}
        signature_ = self._identity(files)
        existing = self._known().get(signature_)
        if existing is not None:
            return existing, False
        core_name = core_of(paths[0].name)
        kind = kind_of_file(paths[0].name)
        if kind is None:
            raise ServiceError("mod_unsupported", file=paths[0])
        key = other_target(files) if kind == "other" else self._core_map(pin).key(core_name) or core_name.lower()
        return self._store(files, label if kind == "other" else core_name, key, kind,
                           built_against=self._built_against(files, pin, "import", painted=True))["id"], True

    def _recognize_installed(self, mod_id: str, identity: frozenset) -> bool:
        """Adopt exact outside content without writing any game files."""
        record = self._records[mod_id]
        matched = False
        for pin in self.installs():
            view = self._view(self._view_pins(pin), "full")
            cores = {view.maps[pin.key].slot(core_of(name)) for name in record["files"]}
            entries = [view.entry(pin, core) for core in cores]
            parts = [part for entry in entries for part in entry.parts]
            if not parts or not self._identity({p.path.name: p.path for p in parts if not p.official}) <= identity:
                continue
            for entry in entries:
                local = frozenset(item for item in identity
                                  if item[0] in view.maps[pin.key].slot_cores(entry.core))
                if entry.state not in ("untracked", "changed") or not self._matches_installed(local, entry):
                    continue
                try:
                    self._adopt_one(pin, entry.core, mod_id=mod_id)
                except ServiceError as error:
                    if error.code != "entry_changed":
                        raise
                else:
                    matched = True
        return matched

    def _matches_installed(self, identity: frozenset, entry: _Entry) -> bool:
        files = {part.path.name: part.path for part in entry.parts}
        modified = {part.path.name: part.path for part in entry.parts if not part.official}
        if entry.kind == "other":
            slots = {core_of(name) for name in files}
            identity = frozenset(item for item in identity if item[0] in slots)
        return bool(modified) and self._identity(modified) <= identity <= self._identity(files)

    @_serialized
    def other_mods(self, pin: Pin | None = None) -> list[dict]:
        """Settings' generic sources, with their target files and current install's configuration."""
        pin = pin or self.current_pin()
        view = self._view([pin], "full") if pin else None
        rows = []
        for record in self._records.values():
            if record["kind"] != "other" or record.get("roster_photography") or record.get("lobby_camera"):
                continue
            source = f"mod:{record['id']}"
            try:
                targets = self._game_files(record["target"], "other", pin) if pin else []
            except ServiceError:
                targets = []
            entries = [view.entry(pin, core_of(path.name)) for path in targets]
            selected = sum(entry.configured == source for entry in entries)
            active = sum(entry.carried == source for entry in entries)
            state = "official" if all(not entry.configured and entry.carried == "official" for entry in entries) else "inactive"
            if not entries:
                state = "missing"
            elif selected == len(entries):
                state = "active" if active == len(entries) and profiles_mod.enabled(self.profiles.read()) else "set"
            elif any(entry.state not in ("ok", "pending") for entry in entries):
                state = "changed"
            elif selected or active:
                state = "mixed"
            rows.append({"id": record["id"], "label": self._label(record), "target": record["target"],
                         "files": [path.name for path in targets] or list(record["files"]),
                         "selected": bool(entries) and selected == len(entries), "state": state,
                         **({"overwritten": True} if any(entry.overwritten for entry in entries) else {})})
        return rows

    @_serialized
    def removal_info(self, mod_id: str) -> dict:
        """The confirmation's name and whether any configuration or applied record uses this source."""
        return self._removal_info(mod_id)

    def _removal_info(self, mod_id: str) -> dict:
        source = f"mod:{mod_id}"
        used = any(entry.get("source") == source
                   for folder in ((self.profiles.read() or {}).get("folders") or {}).values()
                   for entry in folder["configured"].values()) or bool(self._in_use(mod_id))
        return {"name": self._label(self._record(mod_id)), "used": used}

    @_serialized
    @_mod_writer
    def remove(self, mod_id: str, confirm: bool = False, log: LogFunc = no_log,
               reconcile: Callable[[], None] | None = None) -> DeleteResult:
        """Reconcile every use through the protected writer before deleting its managed source."""
        if mod_id == 'official':
            raise ServiceError('asset_official')
        record = self._record(mod_id)
        directory = self.root / "mods" / mod_id
        self._paths(record)  # refuse a stored directory or file that resolves outside the library
        source = f"mod:{mod_id}"
        pins = self._all_pins()
        for name, applied in self._applied.items():
            if applied.get("source") == source and not any(Path(name).parent in pin.dirs for pin in pins):
                folder = _game_folder(Path(name))
                if devmode.allows_read(folder):
                    pin = self._pin(folder, self.settings.server_region() if self.settings else "global")
                    if pin is not None:
                        pins.append(pin)
        by_key = {pin.key: pin for pin in pins}
        affected = set()
        for name, applied in self._applied.items():
            if applied.get("source") == source:
                path = Path(name)
                affected.update((pin.key, self._core_map(pin).slot(core_of(path.name)))
                                for pin in pins if path.parent in pin.dirs)
        keys: list[dict] = []

        def change(data: dict) -> None:
            affected.update(_drop_source(data, source, keys))
            for folder in data["folders"].values():
                for entries in folder["dismissed"].values():
                    if isinstance(entries, dict):
                        entries.pop(core_of(record["files"][0]), None)
            for key, core in affected:
                folder = data["folders"].get(key)
                if folder:
                    folder["updated"].pop(core, None)
                    for entries in folder["dismissed"].values():
                        if isinstance(entries, dict):
                            entries.pop(core, None)
            data["unfinished"] = [item for item in data["unfinished"]
                                  if (item["install"], item["core"]) not in affected]

        if self._removal_info(mod_id)["used"] and not confirm:
            raise ServiceError("asset_delete_confirm")
        # the lobby cameras framed on this mod's art go with it (BL-137): each puts its prefab back first
        from .lobby_camera import cameras, meta_of
        for camera in [r["id"] for r in cameras(self) if meta_of(r).get("source") == source]:
            gone = self.remove(camera, confirm=True, log=log)
            if gone.failed:
                return gone
        from .asset_delete import transaction
        done = []
        try:
            with transaction(self) as deletion:
                deletion.remember_mods([mod_id])
                self._update_profiles(change)
                if profiles_mod.enabled(self.profiles.read()):
                    for key, core in sorted(affected):
                        pin = by_key.get(key)
                        if pin is None:
                            raise ServiceError('game_not_found')
                        config = (self.profiles.read() or {}).get('folders', {}).get(key, {})
                        target = config.get('configured', {}).get(core, {}).get('source', 'official')
                        with _errors('restore'):
                            self._switch(pin, core, target, log)
                        done.append({'install': key, 'core': core})
                for applied in self._applied.values():
                    if applied.get('source') == source:
                        applied.update(source='removed', removed=True)
                        applied.pop('build', None)
                del self._records[mod_id]
                for other in self._records.values():
                    if other.get("repaired_id") == mod_id:
                        del other["repaired_id"]  # the original is repairable again: one write with the copy's removal
                self._save()
                remove_tree(directory)
                self._delete_built_files(keys, self.profiles.read())
                if reconcile:
                    reconcile()
        except ServiceError as error:
            return DeleteResult([], [{'entry': {'id': mod_id}, **error.to_json()}], [], [], [])
        self._prune_quietly()
        return DeleteResult(done, [], [{'install': key, 'core': core} for key, core in sorted(affected)],
                            sorted({core for _, core in affected}), [record['kind']] if affected else [])

    def _in_use(self, mod_id: str) -> list[Path]:
        """The game files still carrying the mod: stats and tails first, then SHA-1 when their stamp changed.
        A part the mod left official carries nothing of it."""
        paths = []
        for name, record in self._applied.items():
            if record.get("source") != f"mod:{mod_id}" or record.get("removed"):
                continue
            path = Path(name)
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                matched = record.get("size") == stat.st_size and (
                    record.get("mtime") == stat.st_mtime_ns
                    or (bool(record.get("tail")) and read_tail(path) == record["tail"]))
                if not matched and self._hashes.get(path).sha1 != record.get("sha1"):
                    continue
                flag = record.get("official")
                if not isinstance(flag, bool):
                    expected = self._official_size(path.name)
                    flag = trailing_bytes(path) == 0 and (expected is None or expected == stat.st_size)
                if not flag:
                    paths.append(path)
            except OSError:
                continue
        return paths

    def _official_size(self, name: str) -> int | None:
        steam = self._steam_official(name)
        if steam is not None:
            return steam.size
        original = self._original_path(Path(name))
        with contextlib.suppress(OSError):
            return original.stat().st_size
        return next((record.get("size") for path, record in self._applied.items()
                     if Path(path).name == name and says_official(record) and isinstance(record.get("size"), int)),
                    None)

    @_serialized
    def rename(self, mod_id: str, label: str) -> ModEntry:
        """Set or clear a metadata-only alias, retaining the default name."""
        if not isinstance(label, str):
            raise ServiceError("bad_params", key="label")
        alias = " ".join("".join(ch for ch in unicodedata.normalize("NFC", label)
                                 if ch.isspace() or not unicodedata.category(ch).startswith("C")).split())
        if len(alias) > 120:
            raise ServiceError("bad_params", key="label")
        record = self._record(mod_id)
        old = record.get("alias")
        if alias:
            record["alias"] = alias
        else:
            record.pop("alias", None)
        try:
            self._save()
        except (OSError, ServiceError):
            if old is None:
                record.pop("alias", None)
            else:
                record["alias"] = old
            raise
        return self._entry(self._record(mod_id))

    @_serialized
    def mark_seen(self, mod_id: str) -> None:
        """Clear a mod's fresh flag."""
        record = self._record(mod_id)
        old = record["seen"]
        record["seen"] = True
        try:
            self._save()
        except (OSError, ServiceError):
            record["seen"] = old
            raise

    @_serialized
    def favorites(self) -> set[str]:
        """Return a snapshot of the favorite variant keys."""
        return set(self._favorites)

    @_serialized
    def set_favorite(self, variant_key: str, on: bool) -> None:
        """Set a variant's favorite flag."""
        self._variant(variant_key)
        old = set(self._favorites)
        if on:
            self._favorites.add(variant_key)
        else:
            self._favorites.discard(variant_key)
        try:
            self._save()
        except (OSError, ServiceError):
            self._favorites = old
            raise

    # --- game folders ---

    def _scan_allowed(self) -> bool:
        """Whether the scan consent is given, asked at each use (it can change while the library lives)."""
        try:
            return bool(self._scan_allowed_input())
        except Exception as error:
            log_failure(logger, logging.WARNING, "Cannot read the scan consent", error)
            return False

    def _pin(self, folder: Path, server: str) -> Pin | None:
        if not folder.is_dir():
            return None
        root = install_root(folder)
        dirs = tuple(directory.resolve() for directory in get_search_dirs(folder))
        if not dirs:
            return None
        return Pin(install_key(root), root, dirs, server)

    def _named_pins(self) -> list[Pin]:
        """The Global and Japan folders the settings name, each when it is set and exists, whatever the file source.
        In dev mode (devmode) a folder outside the dev roots is not used, as the settings' own game folder."""
        out: list[Pin] = []
        if self.settings is None:
            return out
        for setting, server in (("game_resource_dir", "global"), ("game_resource_dir_japan", "japan")):
            try:
                value = self.settings.get(setting)
            except KeyError:
                continue
            if not value or not devmode.allows_read(value):
                continue
            pin = self._pin(Path(value), server)
            if pin is not None and all(other.key != pin.key for other in out):
                out.append(pin)
        return out

    def _recorded_pin(self, key: str, folder: dict) -> Pin | None:
        root = Path(key)
        if not devmode.allows_read(root) or not root.is_dir():
            return None
        dirs = tuple(directory.resolve() for directory in get_search_dirs(root))
        return Pin(key, root, dirs, folder.get("server") or "global") if dirs else None

    @_serialized
    def variant_cores(self, variant_key: str) -> set[str]:
        """The asset cores of a variant in every known install (lower case), for a scan of them."""
        if variant_key.startswith(("other:", "other-set:")):
            return {core_of(name) for record in self._records.values()
                    if record["kind"] == "other" and record["target"] == variant_key for name in record["files"]}
        cores = set()
        for pin in self.installs():
            for kind in KINDS:
                core = self._core_map(pin).core(variant_key, kind)
                if core:
                    cores.add(core.lower())
        return cores

    def installs(self) -> list[Pin]:
        """The known installs, the ones the switch covers: the folders the settings name, then every install with a
        configuration record whose folder exists, unless this data directory removed it from its list."""
        pins = self._named_pins()
        try:
            config = self.profiles.read()
        except ServiceError:
            return pins
        for key, folder in ((config or {}).get("folders") or {}).items():
            if any(pin.key == key for pin in pins) or self.data_id in (folder.get("hidden") or []):
                continue
            pin = self._recorded_pin(key, folder)
            if pin is not None:
                pins.append(pin)
        return pins

    def _all_pins(self) -> list[Pin]:
        """The known installs and the recorded ones this data directory removed from its list: whose kept originals
        must stay just the same."""
        pins = self.installs()
        with contextlib.suppress(ServiceError):
            for key, folder in ((self.profiles.read() or {}).get("folders") or {}).items():
                if all(pin.key != key for pin in pins):
                    pin = self._recorded_pin(key, folder)
                    if pin is not None:
                        pins.append(pin)
        return pins

    def current_pin(self) -> Pin | None:
        """The known install of the selected file source, None in ADB mode or when its folder is missing."""
        if self.settings is None or self.settings.is_adb_mode():
            return None
        folder = self.settings.game_dir()
        if folder is None:
            return None
        return self._pin(folder, self.settings.server_region())

    def _current(self, pin: Pin | None = None) -> Pin:
        pin = pin or self.current_pin()
        if pin is None:
            raise ServiceError("game_not_found")
        return pin

    def _check_pins(self, pins: list[Pin]) -> None:
        """folder_changed when the known installs now differ from the ones a job was submitted with."""
        if _pins_signature(pins) != _pins_signature(self.installs()):
            raise ServiceError("folder_changed")

    def _check_idle(self, pins: list[Pin]) -> None:
        idle = getattr(safety, "check_steam_idle", None)
        if idle is not None:
            for pin in pins:
                idle(pin.root)

    # --- groups packed one asset per bundle (BL-101) ---

    @staticmethod
    def _packed_groups(listing: Iterable[tuple[str, str]]) -> dict[tuple, list[Path]]:
        """The generic bundles whose part (``part_of``: prefix and part number, no date or CRC) several files of one
        folder share, by part: groups packed one asset per bundle, and a part kept under several dates. Their names
        cannot tell them apart over game updates, so each gets a slot keyed by its CAB."""
        groups: dict[tuple, list[Path]] = {}
        for name, path in listing:
            if kind_of_file(name) == "other":
                groups.setdefault((str(Path(path).parent), part_of(name)), []).append(Path(path))
        packed: dict[tuple, list[Path]] = {}
        for (_folder, part), paths in groups.items():
            if len(paths) > 1:
                packed.setdefault(part, []).extend(paths)
        return packed

    def _wanted_packed(self) -> set[tuple]:
        """The parts of the generic mods' files and of files being imported: only bundles of those parts need their CAB
        learned."""
        return {part_of(name) for record in self._records.values() if record.get("kind") == "other"
                for name in record["files"]} | self._packed_wanted

    def _learn_packed(self, paths: Iterable[Path]) -> bool:
        """Learn each bundle's CAB from its header for ``judge.core_of``; True when a slot key changed."""
        changed = False
        for path in paths:
            if packed_cab(path.name) is not None:
                continue
            cab = self._first_cab(path)
            if cab and learn_packed(path.name, cab):
                self._cores.pop(path.name, None)
                changed = True
        return changed

    @staticmethod
    def _first_cab(path: Path) -> str | None:
        try:
            cabs = [name for name in cab_names(path) if not name.endswith((".ress", ".resource"))]
        except (OSError, ValueError):
            return None
        return min(cabs) if cabs else None

    def _learn_packed_sources(self, files: dict[str, Path], packed: dict[tuple, list[Path]], pin: Pin) -> bool:
        """Learn the slot of each mod file of a packed part: the CAB it shares with a bundle of that part in the
        install, else (an older release) the CAB of the one install file it updates, found by its assets."""
        changed = False
        for name, path in files.items():
            name = name.removesuffix(".backup")
            members = packed.get(part_of(name))
            if not members or packed_cab(name) is not None:
                continue
            cab = self._first_cab(path)
            held = {packed_cab(member.name) for member in members}
            if cab is None or cab not in held:
                try:
                    targets = self._other_targets({name: path}, pin)
                except ServiceError:
                    targets = []
                cab = packed_cab(targets[0].name) or self._first_cab(targets[0]) if len(targets) == 1 else None
            if cab and learn_packed(name, cab):
                self._cores.pop(name, None)
                changed = True
        return changed

    def _listing(self, pin: Pin) -> list[tuple[str, str]]:
        listing: list[tuple[str, str]] = []
        for directory in pin.dirs:
            try:
                with os.scandir(directory) as entries:
                    listing.extend((entry.name, entry.path) for entry in entries
                                   if entry.name.endswith(".bundle") and entry.is_file(follow_symlinks=False))
            except OSError:
                continue
        return listing

    @_serialized
    def note_packed(self, paths: Iterable[Path], pin: Pin | None = None) -> None:
        """Before files are identified: a generic file of a packed part (``_packed_groups``) gets the slot of the install
        file it replaces, and every install bundle of that part a slot of its own, each keyed by its CAB."""
        self._note_packed(paths, pin)

    def _note_packed(self, paths: Iterable[Path], pin: Pin | None = None) -> None:
        pin = pin or self.current_pin()
        files = {Path(path).name.removesuffix(".backup"): Path(path) for path in paths}
        parts = {part_of(name) for name in files if kind_of_file(name) == "other"}
        if pin is None or not parts:
            return
        packed = self._packed_groups(self._listing(pin))
        hit = parts & packed.keys()
        if not hit:
            return
        self._packed_wanted |= hit
        changed = self._learn_packed([path for part in hit for path in packed[part]])
        changed |= self._learn_packed_sources(files, packed, pin)
        if changed:
            self._indexes.pop(pin.key, None)

    def _migrate_packed_slots(self) -> None:
        """Give each bundle of a group packed one asset per bundle its own slot, in the generic mods' records and the
        configuration: before BL-101 a whole group was one slot. No game bytes change."""
        records = [record for record in self._records.values() if record.get("kind") == "other"]
        if not records:
            return
        pins = self.installs()
        groups = {pin.key: self._packed_groups(self._listing(pin)) for pin in pins}
        for pin in pins:
            self._indexes.pop(pin.key, None)
            self._index(pin)  # learns the install's bundles of the parts the records use
        moved: dict[str, dict[str, set[str]]] = {}  # mod id -> old shared slot -> its files' own slots
        changed = False
        for record in records:
            names = [name for name in record["files"] if any(part_of(name) in packed for packed in groups.values())]
            if not names:
                continue
            try:
                paths = {path.name: path for path in self._paths(record)}
            except ServiceError:
                continue
            for pin in pins:
                self._learn_packed_sources({name: paths[name] for name in names if name in paths}, groups[pin.key], pin)
            for name in names:
                if packed_cab(name) is None:
                    continue
                shared = "other:" + hashlib.sha256(repr(part_of(name)).encode()).hexdigest()
                moved.setdefault(record["id"], {}).setdefault(shared, set()).add(core_of(name))
            target = other_target(record["files"])
            if target != record["target"]:
                record["target"] = target
                changed = True
        if changed:
            self._save()
        if not moved:
            return
        self._indexes.clear()

        def update(data: dict) -> bool:
            done = False
            for install, folder in data.get("folders", {}).items():
                configured = folder.get("configured", {})
                for old, entry in list(configured.items()):
                    source = entry.get("source", "")
                    new = moved.get(source[4:], {}).get(old) if source.startswith("mod:") else None
                    if not new:
                        continue
                    entry = configured.pop(old)
                    for key in sorted(new):
                        configured.setdefault(key, copy.deepcopy(entry))
                    for field in ("built", "updated"):
                        if old in folder.get(field, {}):
                            value = folder[field].pop(old)
                            for key in sorted(new):
                                folder[field].setdefault(key, copy.deepcopy(value))
                    for marker in folder.get("dismissed", {}).values():
                        if isinstance(marker, dict) and old in marker:
                            value = marker.pop(old)
                            for key in sorted(new):
                                marker.setdefault(key, copy.deepcopy(value))
                    items = [item for item in data.get("unfinished", [])
                             if item.get("install") == install and item.get("core") == old]
                    for item in items:
                        data["unfinished"].remove(item)
                        data["unfinished"].extend({**item, "core": key} for key in sorted(new))
                    done = True
            return done
        self.profiles.update(update)

    def _index(self, pin: Pin) -> dict[str, list[Path]]:
        """The install's bundle files by lower-case core (names only). Listed again when a folder changes, and
        rebuilt only when the files there are not the same ones (a write replacing a file keeps the index)."""
        try:
            signature_ = tuple((str(d), d.stat().st_mtime_ns) for d in pin.dirs)
        except OSError:
            signature_ = ()
        cached = self._indexes.get(pin.key)
        if cached and cached[0] == signature_ and signature_:
            return cached[1]
        listing = self._listing(pin)
        files = frozenset(path for _name, path in listing)
        if cached and cached[2] == files:
            self._indexes[pin.key] = (signature_, cached[1], files, cached[3])
            return cached[1]
        packed = self._packed_groups(listing)
        wanted = self._wanted_packed()
        self._learn_packed([path for key, paths in packed.items() if key in wanted for path in paths])
        index: dict[str, list[Path]] = {}
        for name, path in listing:
            core = self._cores.get(name)
            if core is None:
                core = self._cores[name] = core_of(name)
            index.setdefault(core, []).append(Path(path))
        index = {key: sorted(paths) for key, paths in index.items()}
        self._generation += 1
        self._indexes[pin.key] = (signature_, index, files, self._generation)
        return index

    def _kind(self, core: str, pin: Pin) -> str | None:
        """Use the bundle category for special slots, including files removed by an update."""
        if core.startswith("other:"):
            return "other"
        paths = self._index(pin).get(core.lower(), [])
        if paths:
            return next((kind for path in paths if (kind := kind_of_file(path.name))), None)
        for name in self._applied:
            path = Path(name)
            if path.parent in pin.dirs and core_of(path.name) == core.lower():
                return kind_of_file(path.name)
        for record in self._records.values():
            if core_of(record["files"][0]) == core.lower():
                return record["kind"]
        mapped = self._maps.get(pin.key)
        if mapped and core.lower() in mapped[1].model.values():
            return "model"
        return kind_of(core)

    def _built_for(self, pin: Pin) -> int:
        """Which build of the install's index the caches built on it belong to."""
        self._index(pin)
        return self._indexes[pin.key][3]

    def _core_map(self, pin: Pin) -> CoreMap:
        """How the install's cores map to variant keys (Catalog.core_map on its file names)."""
        index = self._index(pin)
        built_for = self._built_for(pin)
        cached = self._maps.get(pin.key)
        if cached and cached[0] == built_for:
            return cached[1]
        slots = [parse_filename(path.name) for paths in index.values()
                 if (path := next((part for part in paths if kind_of_file(part.name)), None)) is not None]
        spr = [slot.core for slot in slots if kind_of(slot.core, slot.category) == "spr"]
        lobby = [slot.core for slot in slots if kind_of(slot.core, slot.category) == "lobby"]
        special = [slot.core for slot in slots if kind_of(slot.core, slot.category) == "lobby" and
                   slot.category in ("spinebackground", "spinecharacters")]
        build = getattr(self.catalog, "core_map", None)
        if build is not None:
            mapped = build(spr, lobby, special_cores=special) if special else build(spr, lobby)
        else:  # a catalog without core maps: the current catalog's own answers
            keys, spr_of, lobby_of = {}, {}, {}
            for core in [*spr, *lobby]:
                variant = self.catalog.variant_for_core(core.lower())
                if variant is not None:
                    keys[core.lower()] = variant.key
                    target = spr_of if core.lower().endswith("_spr") else lobby_of
                    target.setdefault(variant.key, core)
            mapped = CoreMap(keys, spr_of, lobby_of, {})
        models = [slot.core.lower() for slot in slots if kind_of(slot.core, slot.category) == "model"]
        model_map = getattr(self.catalog, "model_map", None)
        model_variant = getattr(self.catalog, "variant_for_model_core", self.catalog.variant_for_core)
        keys = model_map(models) if model_map else {
            core.lower(): variant.key for core in models
            if (variant := model_variant(core.lower())) is not None}
        model_of = {}
        preferred = {v.key: v.model.lower() for family in getattr(self.catalog, "families", lambda: [])()
                     for v in family.variants
                     if getattr(v, "model", None)}
        # Prefer this install's prefab over its dependency cores, even when a dependency sorts first.
        prefabs = {core for core in models if any(parse_filename(path.name).category == "character"
                                                  for path in index[core.lower()])}
        for core in sorted(models):
            key = keys.get(core.lower(), core.lower())
            mapped.keys.setdefault(core.lower(), key)
            model_of.setdefault(key, core)
            if core in prefabs and model_of[key] not in prefabs:
                model_of[key] = core
        for key, core in preferred.items():
            if key in model_of:
                model_of[key] = core
                mapped.keys.setdefault(core, key)
        model_cores = {key: (core, *sorted(value for value in models
                                          if value != core and keys.get(value.lower()) == key))
                       for key, core in model_of.items()}
        mapped = replace(mapped, model=model_of, model_cores=model_cores)
        self._maps[pin.key] = (built_for, mapped)
        return mapped

    def _names(self, pin: Pin) -> dict[str, Path]:
        """The install's bundle files by name, rebuilt with its index."""
        index = self._index(pin)
        built_for = self._built_for(pin)
        cached = self._names_cache.get(pin.key)
        if cached and cached[0] == built_for:
            return cached[1]
        names = {path.name: path for paths in index.values() for path in paths}
        self._names_cache[pin.key] = (built_for, names)
        return names

    def _originals(self) -> dict[str, int]:
        """The kept originals by name with their sizes, listed again when the folder changed."""
        directory = self.root / "originals"
        try:
            signature_: Any = directory.stat().st_mtime_ns
        except OSError:
            signature_ = None
        if self._originals_cache and self._originals_cache[0] == signature_ and signature_ is not None:
            return self._originals_cache[1]
        sizes: dict[str, int] = {}
        if signature_ is not None:
            for path in directory.glob("*.bundle"):
                with contextlib.suppress(OSError):
                    sizes[path.name] = path.stat().st_size
        self._originals_cache = (signature_, sizes)
        return sizes

    def _record_maps(self) -> tuple[dict, dict]:
        """The records by name and by folder, rebuilt when the records changed."""
        key = (self._version, id(self._applied), len(self._applied))
        if self._maps_cache and self._maps_cache[0] == key:
            return self._maps_cache[1]
        maps = _record_maps(self._applied)
        self._maps_cache = (key, maps)
        return maps

    def _game_files(self, key: str, kind: Kind | None = None, pin: Pin | None = None) -> list[Path]:
        if kind == "other":
            pin = self._current(pin)
            record = next((record for record in self._records.values()
                           if record["kind"] == "other" and record["target"] == key), None)
            if record is None:
                return self._index(pin).get(key, []) if key.startswith("other:") else []
            index = self._index(pin)
            slots = {core_of(name) for name in record["files"]}
            if all(slot in index for slot in slots):
                return resolver.slot_files(index, slots)
            return self._other_targets({path.name: path for path in self._paths(record)}, pin)
        variant = self._variant(key)
        pin = self._current(pin)
        index = self._index(pin)
        return [path for current, value in (("spr", variant.spr), ("lobby", variant.lobby), ("model", getattr(variant, "model", None)))
                if value and (kind is None or current == kind)
                for core in (self._core_map(pin).slot_cores(value) if current == "model" else (value.lower(),))
                for path in index.get(core, [])]

    @_serialized
    def model_cores(self, variant_key: str, pin: Pin | None = None) -> list[str]:
        """A variant's model slot: its prefab core, followed by its own installed dependency cores."""
        variant = self._variant(variant_key)
        core = getattr(variant, "model", None)
        if not core:
            return []
        pin = pin or self.current_pin()
        return list(self._core_map(pin).slot_cores(core)) if pin else [core.lower()]

    def _other_targets(self, files: dict[str, Path], pin: Pin) -> list[Path]:
        """The official files generic mod files replace or update (``targets.generic``)."""
        return sorted({target for match in self._other_matches(files, pin).values() for target in match.targets})

    def _other_matches(self, files: dict[str, Path], pin: Pin) -> dict[str, resolver.Match]:
        available = [path for slot, paths in self._index(pin).items() if slot.startswith("other:") for path in paths]

        def container_keys(path: Path, source: bool) -> set:
            if source:
                bundle = Bundle.load(path)
                if bundle is None:
                    raise ServiceError("bundle_invalid", file=path)
            else:
                bundle = Bundle.load(self._official_for(path, pin))
            return bundle.get_asset_keys("cont_name_type") if bundle else set()

        return resolver.generic(files, available, self._readers(available, pin, keys=False),
                                container_keys=container_keys)

    def _view(self, pins: list[Pin], allow: str, config: dict | None | type(...) = ...,
              snapshot: bool = False) -> _View:
        self._refresh_state()
        if config is ...:
            config = self.profiles.read()
        return _View(self, pins, config, allow, snapshot)

    # --- official copies ---

    def _is_official(self, path: Path) -> bool:
        return self._is_official_as(path, path.name)

    def _steam_official(self, name: str, path: Path | None = None,
                        roots: Sequence[Path] | None = None) -> steam_records.OfficialFile | None:
        """What Steam's file list says the official file of a bundle name is (its size and SHA-1), only with the
        consent to read Steam's records: the list of the Steam install holding ``path``, else of ``roots``, else of
        every known install. None when no list names it, or two lists disagree."""
        if not self._scan_allowed():
            return None
        name = name.removesuffix(".backup")
        folders: list[Path] = []
        if path is not None and game_paths.steam_app_manifest(path.parent) is not None:
            folders = [path.parent]
        elif roots is not None:
            folders = list(roots)
        else:
            folders = self._steam_roots()
        found = set()
        for folder in folders:
            try:
                entry = self._steam_files.bundles(folder).get(name)
            except OSError:
                entry = None
            if entry is not None:
                found.add(entry)
        return next(iter(found)) if len(found) == 1 else None

    def _steam_roots(self) -> list[Path]:
        """The known installs' roots for Steam's file lists, listed again after a few seconds (a judgement asks once
        per file)."""
        now = time.monotonic()
        cached = self._steam_roots_cache
        if cached is None or now - cached[0] > 5:
            cached = self._steam_roots_cache = (now, [pin.root for pin in self.installs()])
        return cached[1]

    def _is_official_as(self, path: Path, name: str) -> bool:
        """Official: the size and SHA-1 that Steam's file list gives ``name`` (``_steam_official``), else the CRC32 of
        the whole content against the CRC in ``name``, with no trailing bytes. A mod can match a CRC, not a SHA-1.
        A kept original also records which rule it passed, and one a Steam list rejected stays rejected without the
        list (``_judge_original``)."""
        fingerprint = self._hashes.get(path)
        steam = self._steam_official(name, path)
        if self._is_kept(path) and path.name == name:
            return self._judge_original(path, fingerprint, steam) in ("steam", "crc")
        if steam is not None:
            return fingerprint.size == steam.size and fingerprint.sha1 == steam.sha1
        # the very bytes a Steam list rejected as the kept original of this name stay a mod's without the list
        return self._original_checks.of_bytes(name, fingerprint) != "failed" and self._crc_official(fingerprint, name)

    @staticmethod
    def _crc_official(fingerprint: Fingerprint, name: str) -> bool:
        parsed = parse_filename(name)
        return fingerprint.trailing == 0 and bool(parsed.crc) and fingerprint.crc == int(parsed.crc)

    def _is_kept(self, path: Path) -> bool:
        """Whether a path is a kept original (a file of originals/)."""
        try:
            return path.parent.resolve() == (self.root / "originals").resolve()
        except (OSError, RuntimeError):
            return False

    def _judge_original(self, original: Path, fingerprint: Fingerprint,
                        steam: steam_records.OfficialFile | None) -> Verdict | None:
        """How a kept original's bytes pass, recorded (BL-194): ``steam`` (Steam's list gives its size and SHA-1, now
        or when it was recorded), ``crc`` (no list names it, the CRC rule alone), ``failed`` (a Steam list gave other
        bytes: never official again, list or not); None when it fails the CRC rule (damaged, ``original_invalid``)."""
        if steam is not None:
            verdict: Verdict = "steam" if (fingerprint.size, fingerprint.sha1) == (steam.size, steam.sha1) else "failed"
            self._original_checks.save(original, verdict, fingerprint, set(self._originals()))
            return verdict
        recorded = self._original_checks.of_bytes(original.name, fingerprint)
        if recorded in ("failed", "steam"):
            return recorded
        if not self._crc_official(fingerprint, original.name):
            return None
        if recorded is None:
            self._original_checks.save(original, "crc", fingerprint, set(self._originals()))
        return "crc"

    def _original_failed(self, original: Path, full: bool) -> bool:
        """Whether a kept original is one a Steam list rejected: at write strength by its bytes (hashed), else by
        its record while the file is unchanged since."""
        try:
            if full:
                return self._judge_original(original, self._hashes.get(original),
                                            self._steam_official(original.name, original)) == "failed"
            return self._original_checks.of_file(original) == "failed"
        except OSError:
            return False

    def original_check(self, name: str, copy: str | None, roots: Sequence[Path] | None = None) -> str | None:
        """What verifies the official copy a screen names (``copy``, as ``_View.copy_of`` gives it, which never names a
        kept original a Steam list rejected), cheaply (no file read): ``steam`` (Steam's list names the file, or the
        kept original passed it), ``crc`` (the CRC rule alone); None without a copy or for the game file itself."""
        if copy not in ("kept", "backup", "elsewhere"):
            return None
        if copy == "kept" and self._original_checks.of_file(self.root / "originals" / name) == "steam":
            return "steam"
        return "steam" if self._steam_official(name, roots=roots) is not None else "crc"

    def check_originals(self, cancel: Callable[[], bool] | None = None) -> dict[str, int]:
        """With Steam's list (the scan consent), verify the kept originals it names that carry no verdict of their
        bytes as they are now (kept by the CRC rule alone, kept by an older build, changed since): each is hashed once
        and recorded ``steam`` or ``failed``. Not serialized: the scan calls it outside the library lock. Returns the
        counts by verdict of the originals it judged."""
        counts: dict[str, int] = {}
        if not self._scan_allowed():
            return counts
        roots = self._steam_roots()
        for name in sorted(self._originals()):
            if cancel and cancel():
                break
            original = self.root / "originals" / name
            if self._original_checks.of_file(original) in ("steam", "failed"):
                continue
            steam = self._steam_official(name, roots=roots)
            if steam is None:
                continue
            preview_gate.yield_to_previews(cancel)
            try:
                verdict = self._judge_original(original, self._hashes.get(original), steam)
            except OSError:
                continue  # released meanwhile
            if verdict == "failed":
                logger.warning("The kept original %s is not the file Steam's list names", name)
            counts[verdict or "invalid"] = counts.get(verdict or "invalid", 0) + 1
        return counts

    def _original_path(self, game_file: Path) -> Path:
        return self.root / "originals" / game_file.name

    def _in_library(self, path: str | os.PathLike[str] | None) -> bool:
        """Whether a path an OS error names lies in the library's own folder rather than in the game."""
        if path is None:
            return False
        try:
            return Path(os.fsdecode(path)).resolve().is_relative_to(self.root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def _elsewhere(self, game_file: Path) -> list[Path]:
        """Files of a game file's name in the other known installs and the folders of the library's records."""
        name, exclude = game_file.name, game_file.resolve()
        out: list[Path] = []
        for pin in self.installs():
            for path in self._index(pin).get(core_of(name), []):
                if path.name == name and path.resolve() != exclude:
                    out.append(path)
        for recorded in self._applied:
            path = Path(recorded)
            if path.name == name and path != exclude and path not in out and devmode.allows_read(path) \
                    and path.is_file():
                out.append(path)
        return out

    def _official_for(self, game_file: Path, pin: Pin, full: bool = True, *, game_first: bool = False) -> Path:
        """An official copy of a game file, in this order: the library's kept original (it was verified when it was
        kept; ``original_invalid`` when it fails, never overwritten), the game file itself, its .backup beside it (the
        classic GUI's; judged by the name without .backup), a file of its name in another game folder. At write
        strength each candidate is verified by the CRC32 of its whole content; else (previews) by the cheap rule.
        ``original_unavailable`` when there is none. Read-only checks may prefer the current official game file
        with ``game_first``; an invalid kept original must still block writes."""
        check_resource_path(game_file, list(pin.dirs))

        def official(path: Path) -> bool:
            try:
                if full:
                    return self._is_official_as(path, game_file.name)
                stat_size = path.stat().st_size
                expected = self._official_size(game_file.name)
                return trailing_bytes(path) == 0 and (expected is None or expected == stat_size)
            except OSError:
                return False

        if game_first and official(game_file):
            return game_file
        original = self._original_path(game_file)
        # a kept original a Steam list rejected is a mod's bytes: never an official copy, skipped (BL-194)
        if original.exists() and not self._original_failed(original, full):
            if full and not self._is_official(original):
                raise ServiceError("original_invalid", file=original)
            return original
        if official(game_file):
            return game_file
        backup = game_file.with_name(game_file.name + ".backup")
        if backup.is_file() and official(backup):
            return backup
        for other in self._elsewhere(game_file):
            if official(other):
                return other
        raise ServiceError("original_unavailable", file=game_file)

    def _keep_original(self, game_file: Path, pin: Pin, *, moving: bool = False) -> Path:
        """Keep verified official bytes, cloning where possible. A journaled write may move its own target;
        backups and files of another install must remain available in their current locations."""
        official = self._official_for(game_file, pin)
        original = self._original_path(game_file)
        if official != original:
            sha1 = self._hashes.get(official).sha1
            if not atomic_clone(official, original):
                if moving and official == game_file and not official.is_symlink():
                    verified_move(official, original, sha1)
                else:
                    atomic_copy(official, original)
            verify_file(original, sha1)
            if not self._is_official(original):
                if official.exists():
                    original.unlink()
                raise ServiceError("original_invalid", file=game_file)
        return official

    def _official_or_current(self, game_file: Path, pin: Pin) -> Path:
        """An omitted mod part uses official bytes when available, otherwise the current game bytes.

        This fallback is never an official baseline for rebuilding, keeping originals or restoring.
        """
        try:
            return self._official_for(game_file, pin)
        except ServiceError as error:
            if error.code != "original_unavailable":
                raise
            return game_file

    def _official_sha1(self, target: Path, pin: Pin) -> str | None:
        try:
            return self._hashes.get(self._official_for(target, pin)).sha1
        except (ServiceError, OSError):
            return None

    def _keep_replaced(self, targets: list[Path], pin: Pin) -> list[str]:
        """Store the game files about to be replaced that are kept nowhere else as library mods, one per core, and
        return the ids of the mods it created. Each target is judged in full: official files go to originals/
        (_keep_original), files carrying a library source stay where they are, and a file changed outside the
        library (another tool, a manual copy), or the bytes of a source the library no longer has, is kept. A part
        of a mod the user removed is never kept. At write strength a target's sha1 is compared with its record, never
        its size and date: _write has hashed every target already, so this reads nothing more."""
        view = self._view([pin], "full")
        keep: dict[str, list[Path]] = {}
        for target in targets:
            if self._replaced_unkept(target, pin, view):
                keep.setdefault(core_of(target.name), []).append(target)
        created = []
        for paths in keep.values():
            mod_id, new = self._add_group(paths, parse_filename(paths[0].name).core, pin)
            logger.info("Kept the replaced game file %s as mod %s", paths[0].name, mod_id)
            if new:
                created.append(mod_id)
        return created

    def _replaced_unkept(self, target: Path, pin: Pin, view: _View) -> bool:
        """Whether a game file about to be replaced holds bytes kept nowhere else, by the full judgement: a target
        whose sha1 equals its record carries the record's source, and is kept only when that source is gone from the
        library (never a removed mod's part) and the target is not official; any other target is kept when it is not
        official. A file whose header cannot be read is never kept."""
        fingerprint = self._hashes.get(target)
        record = self._applied.get(str(target.resolve()))
        if record and fingerprint.sha1 == record.get("sha1"):
            if record.get("removed"):
                return False
            # a held record has no bytes to show: the target's bytes are kept as a mod (held=False)
            if view.source_exists(record.get("source", ""), pin.key, kind_of_file(target.name) or "spr", held=False):
                return False
        elif fingerprint.trailing is None:
            return False
        return not self._is_official(target)

    def _drop_mods(self, mod_ids: list[str]) -> None:
        """Forget mods _keep_replaced added for a replacement that was rolled back (their files are in the game
        again). The state is saved before a folder is deleted. A state that cannot be saved still lists them, so
        their folders stay: a record whose files are gone is held, never dropped."""
        if not mod_ids:
            return
        deletion = getattr(self, '_asset_deletion', None)
        if deletion:
            deletion.remember_mods(mod_ids)
            for mod_id in mod_ids:
                self._records.pop(mod_id, None)
            self._save()
            for mod_id in mod_ids:
                remove_tree(self.root / 'mods' / mod_id)
            return
        removed = {mod_id: self._records.pop(mod_id) for mod_id in mod_ids if mod_id in self._records}
        try:
            self._save()
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not save the library state after a rollback", error)
            self._records.update(removed)  # still listed in state.json: they keep their folders
            return
        for mod_id in mod_ids:
            shutil.rmtree(self.root / "mods" / mod_id, ignore_errors=True)

    # --- what a mod was built against ---

    def _core_targets(self, files: Mapping[str, Path], pin: Pin) -> list[Path]:
        """The official parts of the core a mod's files belong to."""
        name = next(iter(files))
        if kind_of_file(name) == "other":
            return self._other_targets(dict(files), pin)
        return self._spine_parts(core_of(name), pin)

    def _built_against(self, files: Mapping[str, Path], pin: Pin | None, event: str, *,
                       painted: bool | Mapping[str, dict] | None = None) -> dict | None:
        """The fingerprint (``fingerprint.document``) of the official parts a mod's files are built against: every
        part its core has now, from a verified official copy, each with how the mod's own files stand to it
        (``fingerprint.standing``). A part with no official copy is left out (``complete`` False). Headers and text
        assets only. None when nothing can be told (no install, no part, unreadable files); it never fails the
        import, update or remap that asks.

        ``painted`` tells which atlas layout the mod's textures follow where the mod carries no atlas
        (``fingerprint.painted``): True when the files are known to be made for these parts (a whole-file copy of
        files named for them), the record of the files before when a rebuild only moved their textures over, and
        nothing when no one knows."""
        if pin is None or not files:
            return None
        try:
            inspection = integrity.Inspection()
            mod = fingerprint.carried(inspection, list(files.values()))
            parts, complete = [], True
            skeletons: dict[str, bytes] = {}
            for target in self._core_targets(files, pin):
                try:
                    official = self._official_for(target, pin, game_first=True)
                except ServiceError as error:
                    if error.code not in ("original_unavailable", "original_invalid"):
                        raise
                    complete = False
                    continue
                item = fingerprint.part(inspection, official, name=target.name, sha1=self._hashes.get(official).sha1)
                item["mod"] = fingerprint.standing(item, mod)
                parts.append(item)
                if painted is True:
                    skeletons.update(inspection.inspect(official, allow_unknown=True).skeletons)
            if not parts:
                return None
            if painted is True:
                known = fingerprint.painted(parts, mod, skeletons)
            elif painted:
                known = {name: entry for name, entry in painted.items() if name not in mod["atlases"]}
            else:
                known = None
            return fingerprint.document(event, parts, complete=complete, painted=known)
        except Exception as error:  # noqa: BLE001 - a fingerprint is never worth the operation
            log_failure(logger, logging.DEBUG, "No fingerprint of the official parts", error)
            return None

    def _rig_gains(self, files: Mapping[str, Path], pin: Pin | None) -> dict:
        """What the official rig of a mod's core has that the files lack (``atlas_remap.rig_gains``), with only its
        non-empty kinds; empty when nothing, or nothing can be told. Text assets only; never raises."""
        if pin is None or not files:
            return {}
        try:
            inspection = integrity.Inspection()
            mod, official = integrity.Contents(), integrity.Contents()
            for path in files.values():
                mod.merge(inspection.inspect(path, allow_unknown=True))
            for target in self._core_targets(files, pin):
                try:
                    official.merge(inspection.inspect(self._official_for(target, pin, game_first=True)))
                except ServiceError as error:
                    if error.code not in ("original_unavailable", "original_invalid"):
                        raise
            gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
            return {kind: names for kind, names in gains.items() if names}
        except Exception as error:  # noqa: BLE001 - a notice is never worth the operation
            log_failure(logger, logging.DEBUG, "No comparison with the official rig", error)
            return {}

    def _mod_hashes(self, record: dict[str, Any]) -> dict[str, str]:
        return {path.name: self._hashes.get(path).sha1 for path in self._paths(record)}

    def _read_built_against(self, record: dict[str, Any]) -> dict | None:
        """The stored fingerprint of a mod's files as they are now, or None."""
        try:
            return fingerprint.read(self.root / "mods" / record["id"], self._mod_hashes(record))
        except (OSError, ServiceError):
            return None

    def _write_built_against(self, record: dict[str, Any], data: dict | None) -> bool:
        if data is None or self._state_error is not None:  # a library that refuses writes gets none
            return False
        try:
            fingerprint.write(self.root / "mods" / record["id"], data, self._mod_hashes(record))
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not store the official parts' fingerprint", error)
            return False
        # a new baseline: the impacts judged from the one before go (the same files can now tell otherwise)
        self._impacts = {key: found for key, found in self._impacts.items() if key[0] != record["id"]}
        return True

    @_serialized
    def built_against(self, mod_id: str, pin: Pin | None = None) -> dict | None:
        """The fingerprint of the official parts a mod was built against (``fingerprint``): the one recorded when
        the mod was imported, updated or remapped, else one made now from the official files that still show it
        (``_backfill_built_against``). None when nothing tells."""
        record = self._record(mod_id)
        return self._read_built_against(record) or self._backfill_built_against(record, pin or self.current_pin())

    @_serialized
    def official_parts_now(self, mod_id: str, pin: Pin | None = None) -> dict | None:
        """The fingerprint the official parts of a mod's core give now, to hold against the one the mod was built
        against (``built_against``, ``fingerprint.diff`` per part). Nothing is stored. None when nothing tells."""
        record = self._record(mod_id)
        return self._built_against({path.name: path for path in self._paths(record)}, pin or self.current_pin(),
                                   "now")

    # --- what a game update did under each mod ---

    def _impact(self, record: dict[str, Any], pin: Pin | None) -> str:
        """What a game update did to the official parts a mod was built against (``fingerprint.impact``), held
        against its core's parts in ``pin``. Never raises."""
        return self._impact_of(record, pin)[0]

    def _impact_of(self, record: dict[str, Any], pin: Pin | None) -> tuple[str, dict[str, Any]]:
        """``_impact`` with what changed in those parts (``fingerprint.changes``). Kept per mod files and official
        part names (a rebuilt part has another CRC in its name), so a toggle reaching many entries reads each core
        once. Never raises."""
        if pin is None:
            return fingerprint.UNKNOWN, {}
        try:
            files = {path.name: path for path in self._paths(record)}
            if not files:
                return fingerprint.UNKNOWN, {}
            targets = self._core_targets(files, pin)
            key = (record["id"], tuple(sorted(self._mod_hashes(record).items())), pin.key,
                   tuple(sorted(str(path) for path in targets)))
            known = self._impacts.get(key)
            if known is not None:
                return known
            if not targets:
                # a variant whose core moved since (the catalog maps it elsewhere) is no target gone
                mapped = (self._core_map(pin).core(self._target_of(record), record["kind"])
                          if record["kind"] != "other" else None)
                moved = bool(mapped) and mapped.lower() != core_of(next(iter(files)))
                gone = bool(self._read_built_against(record)) and not moved
                found = (fingerprint.GONE, {"gone": True}) if gone else (fingerprint.UNKNOWN, {})
            else:
                built = self._read_built_against(record) or self._backfill_built_against(record, pin)
                now = self._built_against(files, pin, "now") if built else None
                found = ((fingerprint.impact(built, now), fingerprint.changes(built, now)) if built
                         else (fingerprint.UNKNOWN, {}))
            if len(self._impacts) >= 4096:
                self._impacts.clear()
            self._impacts[key] = found
            return found
        except Exception as error:  # noqa: BLE001 - a judgement that cannot be made tells nothing
            log_failure(logger, logging.DEBUG, "No impact judged for the mod", error)
            return fingerprint.UNKNOWN, {}

    def _hold(self, pin: Pin, target: str) -> bool:
        """Whether an On or its Retry holds an entry configured with ``target`` instead of writing it: a mod whose
        official rig a game update changed (``fingerprint.RIG_CHANGED``), so the write would carry its old rig or
        layout onto the new parts. Remapping or updating the mod records its new baseline and ends the hold; Apply
        Mod Configuration and a switch on the detail screen write it as asked."""
        if not target.startswith("mod:") or target[4:] not in self._records:
            return False
        return self._impact(self._records[target[4:]], pin) == fingerprint.RIG_CHANGED

    def resource_version(self, pin: Pin) -> str | None:
        """The resource version of an install (``discovery.resource_version``), None when it cannot be read."""
        from ..gameassets.discovery import resource_version

        try:
            return resource_version(list(pin.dirs))
        except OSError:
            return None

    def update_impact(self, pin: Pin | None = None, *, force: bool = False,
                      progress: Callable[[dict], None] | None = None) -> dict[str, Any]:
        """The impact summary of a game update on an install (the current one by default): when its resource version
        differs from the last one seen there (or none was recorded yet), every library mod classified from the
        fingerprint it was built against (``fingerprint.impact``), each mod told with what changed in its official
        parts (``changes``: ``fingerprint.changes``). ``due`` is True when a mod needs telling (rebuilt,
        new parts, changed rig or gone); a check with nothing to tell records the version as seen at once, one with
        something waits for ``impact_seen``. ``force`` classifies whatever was seen. Read-only otherwise; never
        fails on one mod."""
        pin = pin or self.current_pin()
        if pin is None:
            return {"due": False, "install": None, "version": None, "previous": None, "mods": [], "counts": {}}
        version = self.resource_version(pin)
        config = self.profiles.read() or {}
        seen = ((config.get("folders") or {}).get(pin.key) or {}).get("resource") or {}
        previous = seen.get("version") if isinstance(seen, dict) else None
        out: dict[str, Any] = {"due": False, "install": pin.key, "version": version, "previous": previous,
                               "mods": [], "counts": {name: 0 for name in fingerprint.IMPACTS}}
        if version is None or (version == previous and not force):
            return out
        configured = {entry.get("source") for entry in
                      (((config.get("folders") or {}).get(pin.key) or {}).get("configured") or {}).values()}
        with self._lock:
            entries = {entry.id: entry for entry in self.mods()}
            records = [self._records[mod_id] for mod_id in entries if mod_id in self._records]
            # the same resource version, the same mods and baselines and configuration as the last check: its answer
            # (owner, 2026-11-11: Settings' View shows the summary at once when nothing changed)
            stamp = self._impact_stamp(pin, version, records, configured)
        cached = self._impact_cache.get(pin.key)
        if cached and cached[0] == stamp:
            out.update(mods=[dict(mod) for mod in cached[1]["mods"]], counts=dict(cached[1]["counts"]))
            out["due"] = bool(out["mods"])
            return out
        for i, record in enumerate(records, 1):
            if progress:
                entry = entries[record["id"]]
                progress({"current": i, "total": len(records), "step": "import.step.check", "variant": entry.target,
                          "kind": entry.kind, "label": entry.label})
            with self._lock:
                if record["id"] not in self._records:
                    continue
                found, changes = self._impact_of(record, pin)
            out["counts"][found] += 1
            if found in (fingerprint.UNCHANGED, fingerprint.UNKNOWN):
                continue
            entry = entries[record["id"]]
            out["mods"].append({"id": entry.id, "label": entry.label, "target": entry.target, "kind": entry.kind,
                                "core": entry.core, "impact": found, "changes": changes,
                                "configured": f"mod:{entry.id}" in configured, "repaired_id": entry.repaired_id})
        out["due"] = bool(out["mods"])
        self._impact_cache[pin.key] = (stamp, {"mods": [dict(mod) for mod in out["mods"]], "counts": dict(out["counts"])})
        if not out["due"] and not force:
            self.impact_seen(pin.key, version)
        return out

    def _impact_stamp(self, pin: Pin, version: str, records: list[dict[str, Any]], configured: set) -> str:
        """What an impact summary depends on: the resource version, each mod's files and stored baseline (by its
        modification time) and the configured sources. Names, sizes and times only."""
        parts = [pin.key, version, sorted(str(source) for source in configured if source)]
        for record in records:
            baseline = self.root / "mods" / record["id"] / fingerprint.FILE_NAME
            try:
                mark = baseline.stat().st_mtime_ns
            except OSError:
                mark = None
            parts.append([record["id"], sorted(record.get("files") or []), record.get("label"), mark])
        return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def update_due(self, pin: Pin | None = None) -> dict[str, Any]:
        """Whether the start asks to check the library's mods against a game update: the install's resource version
        differs from the last one seen there and the library has mods. Reads only the version file and the
        configuration. With no version seen yet (a first start, a newly chosen folder) or no mod, the version is
        recorded as seen at once and nothing is asked. Never fails: a version that cannot be read asks nothing."""
        pin = pin or self.current_pin()
        out: dict[str, Any] = {"due": False, "install": None, "version": None, "previous": None}
        if pin is None:
            return out
        version = self.resource_version(pin)
        config = self.profiles.read() or {}
        seen = ((config.get("folders") or {}).get(pin.key) or {}).get("resource") or {}
        previous = seen.get("version") if isinstance(seen, dict) else None
        out.update(install=pin.key, version=version, previous=previous)
        if version is None or version == previous:
            return out
        with self._lock:
            has_mods = bool(self._records)
        if previous is None or not has_mods:
            try:
                self.impact_seen(pin.key, version)
            except ServiceError as error:
                log_failure(logger, logging.DEBUG, "Could not record the resource version as seen", error)
            return out
        out["due"] = True
        return out

    def impacts(self, ids: Sequence[str], pin: Pin | None = None) -> list[dict[str, str]]:
        """The impact of a game update under each of the mods ``ids`` (``fingerprint.impact``), in the install
        ``pin`` (the current one by default); ids of no mod are left out."""
        pin = pin or self.current_pin()
        out = []
        for mod_id in ids:
            with self._lock:
                record = self._records.get(mod_id)
                if record is not None:
                    out.append({"id": mod_id, "impact": self._impact(record, pin)})
        return out

    def impact_seen(self, install: str, version: str) -> None:
        """Record a resource version of an install as seen (its impact summary shown or not needed)."""
        if not isinstance(install, str) or not isinstance(version, str) or not version:
            raise ServiceError("bad_params", key="version")
        if self._writes_refused():
            return

        def change(data: dict) -> bool:
            folder = data["folders"].setdefault(install, {})
            if (folder.get("resource") or {}).get("version") == version:
                return False
            folder["resource"] = {"version": version, "seen": now()}
            return True

        with _errors("impact_seen"):
            self.profiles.update(change)

    # --- an untested game build (tested_build) ---

    def untested_version(self, pin: Pin) -> str | None:
        """An install's resource version when the App was not tested on it: its server has tested data and the version
        is not among the tested ones. None otherwise (tested, no data for the server, or the version unreadable)."""
        tested = tested_build.for_server(pin.server)
        if tested is None:
            return None
        version = self.resource_version(pin)
        return None if version is None or version in tested.versions else version

    def _untested_cores(self, pin: Pin, targets: Iterable[Path], version: str | None) -> list[str]:
        """The cores of ``targets`` the untested build of resource version ``version`` (``untested_version``) changed
        and the user has not confirmed writing: a core with a bundle whose name the tested build does not have (a part
        rebuilt under a new CRC, or a new part). Names only."""
        targets = list(targets)
        if version is None or not targets:
            return []
        tested = tested_build.for_server(pin.server)
        confirmed = self._untested_ok.get((pin.key, version), set())
        index = self._index(pin)
        changed = []
        for core in sorted({core_of(path.name) for path in targets}):
            names = [path.name for path in index.get(core) or [path for path in targets if core_of(path.name) == core]]
            if core not in confirmed and any(not tested.has(name) for name in names):
                changed.append(core)
        return changed

    def _untested_entry(self, pin: Pin, core: str, targets: Sequence[Path], variant: str | None = None,
                        kind: str | None = None) -> dict[str, Any]:
        """An entry an untested build's confirmation names: {install, core, variant, kind}, with its file names for a
        generic part."""
        kind = kind or self._kind(core, pin) or "spr"
        entry = {"install": pin.key, "core": core, "variant": variant or self._core_map(pin).key(core) or core,
                 "kind": kind}
        if kind == "other":
            entry["files"] = [{"file": path.name} for path in targets]
        return entry

    def _check_untested(self, writes: Sequence[tuple[Pin, dict[str, Any], Sequence[Path]]]) -> None:
        """Refuse writes onto cores an untested build changed until the user confirms them (``untested_build``, a
        refusal of the moment: nothing is written or configured). ``writes`` holds, per entry to write, its install,
        the entry the refusal names (``_untested_entry``) and its target files. The refusal's ``confirm``
        ({install: {version, cores}}) is what ``confirm_untested`` takes back."""
        entries: list[dict[str, Any]] = []
        confirm: dict[str, dict[str, Any]] = {}
        versions: dict[str, str | None] = {}  # read once per install
        for pin, entry, targets in writes:
            if pin.key not in versions:
                versions[pin.key] = self.untested_version(pin)
            version = versions[pin.key]
            cores = self._untested_cores(pin, targets, version)
            if not cores:
                continue
            entries.append(entry)
            item = confirm.setdefault(pin.key, {"version": version, "cores": []})
            item["cores"] = sorted({*item["cores"], *cores})
        if entries:
            logger.info("Untested game build: %d entries need a confirmation", len(entries))
            raise ServiceError("untested_build", entries=entries, confirm=confirm)

    def confirm_untested(self, confirm: Mapping[str, Mapping[str, Any]]) -> None:
        """The user confirmed writing onto the cores an ``untested_build`` refusal named ({install: {version, cores}}):
        writes onto them pass while the program runs and the install keeps that resource version."""
        if not isinstance(confirm, Mapping) or len(confirm) > 64:
            raise ServiceError("bad_params", key="confirm")
        for install, item in confirm.items():
            cores = item.get("cores") if isinstance(item, Mapping) else None
            version = item.get("version") if isinstance(item, Mapping) else None
            if (not isinstance(install, str) or not isinstance(version, str) or not version
                    or not isinstance(cores, list) or len(cores) > 65536
                    or not all(isinstance(core, str) and core for core in cores)):
                raise ServiceError("bad_params", key="confirm")
            self._untested_ok.setdefault((install, version), set()).update(cores)
            logger.info("Untested game build: %d cores confirmed in %s", len(cores), install)

    def _backfill_built_against(self, record: dict[str, Any], pin: Pin | None) -> dict | None:
        """A fingerprint for a mod stored without one, from evidence alone: the official files of its core (the
        install's current parts and the kept originals) whose atlases, skeletons and textures the mod's own files
        carry unchanged. One file per part: the install's part before a kept original. It is stored, as
        incomplete (other parts of the core are not known). None, and nothing stored, when no file shows it."""
        try:
            paths = self._paths(record)
            files = {path.name: path for path in paths}
            miss = (record["id"], tuple(sorted(self._mod_hashes(record).items())), pin.key if pin else None,
                    tuple(sorted(self._originals())))
            if miss in self._backfill_misses:
                return None
            candidates: list[tuple[str, Path]] = []
            if pin is not None:
                for target in self._core_targets(files, pin):
                    try:
                        candidates.append((target.name, self._official_for(target, pin, game_first=True)))
                    except ServiceError as error:
                        if error.code not in ("original_unavailable", "original_invalid"):
                            raise
            generic = record["kind"] == "other"
            cores = {core_of(name) for name in files}
            keys = {integrity.replacement_key(name) for name in files}
            for name in sorted(self._originals()):
                if (integrity.replacement_key(name) in keys) if generic else (core_of(name) in cores):
                    kept = self.root / "originals" / name
                    if self._is_official(kept):
                        candidates.append((name, kept))
            inspection = integrity.Inspection()
            mod = fingerprint.carried(inspection, paths)
            parts, taken = [], set()
            for name, official in candidates:
                key = integrity.replacement_key(name)
                if key in taken:
                    continue
                item = fingerprint.part(inspection, official, name=name, sha1=self._hashes.get(official).sha1)
                if fingerprint.standing(item, mod) == fingerprint.SAME:
                    item["mod"] = fingerprint.SAME
                    parts.append(item)
                    taken.add(key)
            if not parts:
                if len(self._backfill_misses) >= 4096:
                    self._backfill_misses.clear()
                self._backfill_misses.add(miss)
                return None
            data = fingerprint.document("backfill", parts, complete=False)
            self._write_built_against(record, data)
            return self._read_built_against(record) or data
        except Exception as error:  # noqa: BLE001 - evidence that cannot be read is no evidence
            log_failure(logger, logging.DEBUG, "No fingerprint made for the mod", error)
            return None

    def _backfill_before_release(self, name: str) -> None:
        """Before a kept original goes: the mods of its core stored without a fingerprint get theirs while the
        file that shows it is still here (``_backfill_built_against``, kept originals only). Never raises."""
        try:
            key, core = integrity.replacement_key(name), core_of(name)
            for record in list(self._records.values()):
                names = record.get("files") or []
                if record["kind"] == "other":
                    if key not in {integrity.replacement_key(file) for file in names}:
                        continue
                elif core not in {core_of(file) for file in names}:
                    continue
                if self._read_built_against(record) is None:
                    self._backfill_built_against(record, None)
        except Exception as error:  # noqa: BLE001 - releasing the original does not wait for it
            log_failure(logger, logging.DEBUG, "No fingerprint made before the release", error)

    # --- kept originals ---

    def _needs_original(self, path: Path, record: dict[str, Any]) -> bool:
        """Whether a game file the library wrote to may still need the kept original of its name, by stat alone: not
        once its folder exists and the file is gone, nor while it is unchanged since the library wrote official
        bytes there. A folder that cannot be seen (an unmounted drive) is judged by its record. When unsure, the
        original stays: the cost is disk space, never a lost official copy."""
        try:
            if not path.parent.is_dir():
                return not says_official(record)
            if not path.is_file():
                return False
            stat = path.stat()
            return not (says_official(record) and record.get("size") == stat.st_size
                        and record.get("mtime") == stat.st_mtime_ns)
        except OSError:
            return True

    def _needed_elsewhere(self, name: str, mine: set[str], pins: list[Pin]) -> bool:
        """Whether another file needs the kept original of a name: a file of that name the library wrote to outside
        ``mine`` (resolved paths), judged by _needs_original, or a file of that name in one of ``pins`` outside
        ``mine`` the library never wrote (it may be another tool's mod, with this original its only official copy).
        ``pins`` holds the installs this data directory removed from its list too (_all_pins)."""
        for other, record in self._applied.items():
            if other not in mine and Path(other).name == name and self._needs_original(Path(other), record):
                return True
        for pin in pins:
            for path in self._index(pin).get(core_of(name), []):
                resolved = str(path.resolve())
                if path.name == name and resolved not in mine and resolved not in self._applied:
                    return True
        return False

    def _kept_for_configuration(self, name: str, config: dict | None, pins: list[Pin]) -> bool:
        """Whether a configured entry keeps the original of a name: its game file of that name exists in that
        install, and the configured source may write non-official bytes there (no record there shows the configured
        source leaving that part official). When unsure, the original stays. While troubleshooting (BL-185), the
        configuration saved to restore keeps its originals too: releasing them all would be a long pass for nothing."""
        core = core_of(name)
        saved = (config or {}).get("troubleshoot")
        saved = saved.get("folders") if isinstance(saved, dict) else None
        saved = saved if isinstance(saved, dict) else {}
        for pin in pins:
            slot = self._core_map(pin).slot(core)
            entry = ((((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}).get(slot)
            if not entry:
                kept = saved.get(pin.key)
                entry = kept.get(slot) if isinstance(kept, dict) else None
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                continue
            path = next((path for path in self._index(pin).get(core, []) if path.name == name), None)
            if path is None:
                continue
            record = self._applied.get(str(path.resolve()))
            if not (record and record.get("source") == entry["source"] and record.get("official") is True):
                return True
        return False

    def _release_failed(self, targets: list[Path], pin: Pin) -> None:
        """After a write that failed with its game files back as they were, release the originals that same call kept
        from the game files themselves: those files hold the bytes just verified."""
        if not targets:
            return
        try:
            config = self.profiles.read()
        except ServiceError:
            return
        pins = self._all_pins()
        for target in targets:
            original = self._original_path(target)
            if self._kept_for_configuration(target.name, config, pins) or self._needed_elsewhere(
                    target.name, {str(target.resolve())}, pins):
                continue
            with contextlib.suppress(OSError):
                if not original.resolve().is_relative_to(self.root):
                    continue
                original.unlink()
                logger.info("Released the original of %s kept by a failed write", target.name)

    def _prune(self) -> list[Path]:
        """Serialize deletion with writes that may still need an original for rollback. Nothing is deleted while a
        journal is pending or the state cannot be written (the start job calls this)."""
        if self._state_error is not None or any((self.root / "transactions").glob("*/journal.json")):
            return []
        with self._transaction_lock():
            return self._prune_originals()

    def _prune_originals(self) -> list[Path]:
        """Delete the kept originals no longer needed, over every known install, from names, stats and records only
        (no game file's contents are read; an original about to go is read once for the mods of its core that have
        no fingerprint of their official parts yet, ``_backfill_before_release``): an original whose name is present
        is released once every file of that name is unchanged since the library wrote official bytes there; an
        original whose name is gone from every known install is deleted when a game update superseded it (a
        later-dated file of the same part in the folder of each of its gone records). Then the records of superseded
        gone files are dropped (with the entry's ``updated`` marker when the library wrote non-official bytes there),
        and the built copies whose names are gone.

        What it keeps is decided over the installs this data directory removed from its list too (``guarded``): a
        library shared with a data directory that names such an install keeps the same originals here as there, so
        removing an install from the list never changes what the prune keeps."""
        directory = self.root / "originals"
        if not directory.resolve().is_relative_to(self.root):
            return []
        try:
            config = self.profiles.read()
        except ServiceError:
            return []
        pins = self.installs()
        guarded = self._all_pins()
        listed = {path.name for path in directory.glob("*.bundle")} if directory.is_dir() else set()
        view = _View(self, guarded, config, "recorded")
        view.originals = {name: size for name, size in view.originals.items()}  # as listed at the start
        present: dict[str, list[Path]] = {}
        for names in view.names.values():
            for name, path in names.items():
                present.setdefault(name, []).append(path)
        known_dirs = {folder: pin for pin in guarded for folder in pin.dirs}
        released: list[Path] = []
        for name in sorted(listed):
            original = directory / name
            files = present.get(name, [])
            if files:
                if self._kept_for_configuration(name, config, guarded):
                    continue
                if not all(self._unchanged_official(path) for path in files):
                    continue
                if self._needed_elsewhere(name, {str(path.resolve()) for path in files}, guarded):
                    continue
            else:
                gone = [Path(path) for path in self._applied if Path(path).name == name
                        and Path(path).parent in known_dirs and not Path(path).exists()]
                if not gone or not all(view.successor(known_dirs[path.parent], path) for path in gone):
                    continue
                if self._needed_elsewhere(name, {str(path) for path in gone}, guarded):
                    continue
            self._backfill_before_release(name)
            try:
                original.unlink()
            except OSError as error:
                log_failure(logger, logging.WARNING, "Could not delete the kept original", error)
                continue
            logger.info("Released the kept original %s", name)
            released.append(original)
        # superseded gone records: kept while an original of their name is, else dropped (with the marker)
        kept = {name for name in listed if (directory / name).exists()}
        stale: list[str] = []
        markers: dict[str, set[str]] = {}
        for pin in pins:
            for folder in pin.dirs:
                for path, record in view.by_folder.get(folder, []):
                    if path.exists() or not view.successor(pin, path) or path.name in kept:
                        continue
                    stale.append(str(path))
                    core = view.maps[pin.key].slot(core_of(path.name))
                    if (view.says_nonofficial(path, record, present=False) and view.configured(pin.key, core)
                            and not view.later_record(path, pin)):
                        markers.setdefault(pin.key, set()).add(core)
        built_drops: dict[str, set[str]] = {}
        for pin in pins:
            names = view.names[pin.key]
            for core, key in (view.folder(pin.key).get("built") or {}).items():
                wanted = [*(key.get("parts") or {}), *(key.get("inputs") or [])]
                if any(name not in names for name in wanted):
                    built_drops.setdefault(pin.key, set()).add(core)
        if stale:
            old_applied = dict(self._applied)
            for name in stale:
                self._applied.pop(name, None)
            try:
                self._save()
            except (OSError, ServiceError) as error:
                self._applied = old_applied
                log_failure(logger, logging.WARNING, "Could not save the library state after pruning", error)
                markers = {}
        if markers or built_drops:
            stamp = now()
            dropped: list[dict] = []

            def change(data: dict) -> bool:
                changed = False
                for key, cores in markers.items():
                    folder = data["folders"].get(key)
                    for core in cores:
                        if folder and core in folder["configured"]:
                            folder["updated"][core] = stamp
                            changed = True
                for key, cores in built_drops.items():
                    folder = data["folders"].get(key)
                    for core in cores:
                        if folder and core in folder["built"]:
                            dropped.append(folder["built"].pop(core))
                            changed = True
                return changed

            self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))
        self._pruned_cores.update(core_of(Path(name).name) for name in stale)
        self._pruned_cores.update(core for cores in built_drops.values() for core in cores)
        return released

    @_serialized
    def take_pruned_cores(self) -> set[str]:
        """Cores changed by global pruning since the last writer's replacement scan was queued."""
        cores, self._pruned_cores = self._pruned_cores, set()
        return cores

    def _unchanged_official(self, path: Path) -> bool:
        record = self._applied.get(str(path.resolve()))
        if not says_official(record):
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        return record.get("size") == stat.st_size and record.get("mtime") == stat.st_mtime_ns

    def _prune_quietly(self) -> None:
        # Writers already hold the library lock, possibly followed by transaction
        # and Mod locks. Do not reacquire it after those higher-order locks.
        if getattr(self, '_asset_deletion', None):
            return
        if self._deferred_prunes:
            self._prune_pending = True
            return
        try:
            self._prune()
        except Exception as error:  # never in the way of the switch itself; the next prune tries again
            log_failure(logger, logging.WARNING, "Could not prune the kept originals", error)

    @contextlib.contextmanager
    def import_batch(self) -> Iterator[None]:
        """Keep per-Mod transactions independent, but run global housekeeping once."""
        with self._lock:
            self._deferred_prunes += 1
        try:
            yield
        finally:
            with self._lock:
                self._deferred_prunes -= 1
                if not self._deferred_prunes and self._prune_pending:
                    self._prune_pending = False
                    self._prune_quietly()

    @_serialized
    def prune(self) -> list[Path]:
        """Delete the kept originals no longer needed and return them: those whose game file the library wrote
        official bytes to again, and those of files a game update replaced. Originals another install may need are
        kept, and so are those of configured entries the next Off copies back."""
        return self._prune()

    # --- what the game shows ---

    def _file_state(self, view: _View, entry: _Entry, part: _Part) -> GameFileState:
        parsed = parse_filename(part.path.name)
        copy_ = entry.copies.get(str(part.path))
        applied = "unknown" if part.carried == "damaged" else part.carried
        checked = None if part.official else self.original_check(
            part.path.name, copy_, roots=[pin.root for pin in view.pins.values()])
        return GameFileState(part.path, entry.core if entry.kind == "other" else parsed.core, entry.kind,
                             applied, part.crc, part.path.name in view.originals,
                             parsed.date, part.size, copy_, copy_ is not None, part.official, checked)

    @_serialized
    def state(self, variant_key: str, pin: Pin | None = None) -> VariantState:
        """Inspect all game parts of a variant cheaply: size, date and header, never the whole file (decision 27).
        Needs Fix comes from the record of the switch that wrote the file, while it is unchanged since."""
        pin = self._current(pin)
        view = self._view(self._view_pins(pin), "family")
        files = []
        if variant_key.startswith(("other:", "other-set:")):
            values = {core_of(path.name) for path in self._game_files(variant_key, "other", pin)}
        else:
            variant = self._variant(variant_key)
            values = (variant.spr, variant.lobby, getattr(variant, "model", None))
        for value in values:
            if not value:
                continue
            core = view.maps[pin.key].slot(value)
            entry = view.entry(pin, core)
            files.extend(self._file_state(view, entry, part) for part in entry.parts)
        sources = {file.applied for file in files}
        crc = "needs_fix" if any(f.crc == "needs_fix" for f in files) else (
            "fixed" if any(f.crc == "fixed" for f in files) else "ok")
        return VariantState(variant_key, files, sources.pop() if len(sources) == 1 else "unknown",
                            crc, any(f.original for f in files))

    def _view_pins(self, pin: Pin) -> list[Pin]:
        pins = [pin]
        pins.extend(other for other in self.installs() if other.key != pin.key)
        return pins

    @_serialized
    def configured(self, variant_key: str, pin: Pin | None = None) -> dict[str, str | None]:
        """The sources configured for a variant's asset kinds in the current install."""
        variant = self._variant(variant_key)
        pin = self._current(pin)
        folder = (((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        from . import lobby_camera

        return lobby_camera.overlay(lobby_camera.cameras(self, variant_key), folder,
                                    self._configured_sources(variant, folder))

    @staticmethod
    def _configured_sources(variant: Any, folder: dict) -> dict[str, str | None]:
        return {kind: (folder.get(value.lower()) or {}).get("source") if value else None
                for kind, value in (("spr", variant.spr), ("lobby", variant.lobby), ("model", getattr(variant, "model", None)))
                if kind != "model" or hasattr(variant, "model")}

    @_serialized
    def roster_snapshot(self, variant_keys: list[str]) -> dict[str, Any]:
        """Read one install and configuration for the whole roster under the library lock.

        The roster needs manifest intent and cheap replacement checks, not physical
        installation verification. Detailed selection errors and byte verification
        remain in ``selection`` for the detail screen and write operations.
        """
        pin = self.current_pin()
        config = self.profiles.read() if pin is not None else None
        folder = ((config or {}).get("folders") or {}).get(pin.key) if pin else None
        choices = (folder or {}).get("configured") or {}
        assigned = {record["assignment"] for record in self._records.values() if record.get("assignment")}
        configured = {}
        from . import lobby_camera

        made = {}
        for record in lobby_camera.cameras(self):
            made.setdefault(lobby_camera.meta_of(record)["variant"], []).append(record)
        for key in dict.fromkeys([*variant_keys, *sorted(assigned)]):
            try:
                configured[key] = lobby_camera.overlay(made.get(key, []), choices, self._configured_sources(
                    self._variant(key), choices)) if pin else {"spr": None, "lobby": None}
            except ServiceError:
                configured[key] = {"spr": None, "lobby": None}
        modified = {}
        if pin is not None:
            allow = "scan" if self._scan_allowed() else "recorded"
            modified = self._modified(pin, _View(self, [pin], config, allow))
        return {"install": pin.key if pin else None,
                "mods": [self._entry(record) for record in self._records.values()],
                "favorites": set(self._favorites), "configured": configured, "modified": modified,
                "assigned": sorted(assigned)}

    @_serialized
    def selection(self, variant_key: str, pin: Pin | None = None) -> dict[str, dict]:
        """Saved choices and verified installation status, using the master switch as intent.

        Every current part must match the recorded write of this source. Fingerprints are
        cached by size, mtime and ctime; an unchanged tail is not proof of installation.
        """
        pin = self._current(pin)
        variant = self._variant(variant_key)
        config = self.profiles.read()
        folder = (((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        result = {}
        for kind, core in (("spr", variant.spr), ("lobby", variant.lobby), ("model", getattr(variant, "model", None))):
            choice = folder.get(core.lower()) if core else None
            if not choice:
                continue
            result[kind] = self._selection_of(pin, config, core, choice)
        from . import lobby_camera

        return lobby_camera.overlay_selection(self, variant_key, pin, config, folder, result)

    def _selection_of(self, pin: Pin, config: dict | None, core: str, choice: dict) -> dict:
        """One configured entry's saved choice and whether it is verified installed (``selection``)."""
        paths = self._spine_parts(core, pin)
        installed = bool(paths) and profiles_mod.enabled(config) and not choice.get("error")
        try:
            for path in paths:
                record = self._applied.get(str(path.resolve())) or {}
                if (record.get("source") != choice["source"] or record.get("removed")
                        or self._hashes.get(path).sha1 != record.get("sha1")):
                    installed = False
                    break
            recorded = {Path(name) for name, record in self._applied.items()
                        if Path(name).parent in pin.dirs
                        and self._core_map(pin).slot(core_of(Path(name).name)) == core.lower()
                        and record.get("source") == choice["source"] and not record.get("removed")}
            installed = installed and recorded == set(paths)
        except (OSError, ServiceError):
            installed = False
        entry = {"source": choice["source"], "installed": installed,
                 "error": None if installed else choice.get("error")}
        source = choice["source"]
        if source.startswith("mod:") and source[4:] in self._records and self._incomplete(self._records[source[4:]], pin):
            entry["incomplete"] = True
        return entry

    def _selection_error(self, pin: Pin, cores, error: dict | None) -> None:
        def change(data):
            choices = (data["folders"].get(pin.key) or {}).get("configured", {})
            changed = False
            for core in cores:
                choice = choices.get(core)
                if choice is not None and choice.get("error") != error:
                    if error is None:
                        choice.pop("error", None)
                    else:
                        choice["error"] = error
                    changed = True
            return changed
        self._update_profiles(change)

    @_serialized
    def modified(self, pin: Pin | None = None) -> dict[str, str]:
        """Variants whose portrait or special slot files are not official, from cheap checks only (no hashing): the
        library's own records while the file is unchanged since it wrote it (a part a record says official adds nothing),
        and, with scan consent, the header of every other slot file (trailing bytes, or not the official size of its
        name). Returns {variant key: source}, the source being "mod:<id>", "variant:<key>" or "unknown"."""
        pin = self.current_pin() if pin is None else pin
        if pin is None:
            return {}
        allowed = self._scan_allowed()
        view = self._view([pin], "scan" if allowed else "recorded")
        return self._modified(pin, view)

    def _modified(self, pin: Pin, view: _View) -> dict[str, str]:
        out: dict[str, str] = {}
        for core, paths in view.indexes[pin.key].items():
            variant = self.catalog.variant_for_core(core)
            if variant is None or (not core.endswith("_spr") and not getattr(variant, "spine_category", None)):
                continue
            kind = next((kind for path in paths if (kind := kind_of_file(path.name))), None) or "spr"
            sources = set()
            for path in paths:
                try:
                    part = view.part(path, pin, kind)
                except (_Unread, OSError):
                    continue
                if not part.official and part.carried != "damaged":
                    sources.add(part.carried)
            if sources:
                out[variant.key] = sources.pop() if len(sources) == 1 else "unknown"
        return out

    @_serialized
    def official_bundles(self, core: str, pin: Pin | None = None, *, current_fallback: bool = False) -> list[Path]:
        """Verified official parts of a core, including kept originals of overwritten game files.

        Resolve the whole set or fail. Model previews can explicitly request current files where
        no official copy remains; callers rebuilding mods always require verified official bytes.
        """
        pin = self._current(pin)
        paths = self._spine_parts(core, pin)
        if not paths:
            raise ServiceError("game_file_not_found", variant=core)
        resolve = self._official_or_current if current_fallback else self._official_for
        return [resolve(path, pin) for path in paths]

    def _spine_parts(self, core: str, pin: Pin) -> list[Path]:
        """The supported character bundle parts of a core, in game folder order.

        Model parts include the prefab and its own meshes, materials, textures and clips; shared dependencies
        remain supplied by the install's CAB graph.
        """
        core = core.lower()
        index = self._index(pin)
        paths = [path for value in self._core_map(pin).slot_cores(core) for path in index.get(value, [])]
        return [path for path in paths if kind_of_file(path.name)]

    @_serialized
    def live_bundles(self, core: str, pin: Pin | None = None) -> list[Path]:
        """The game files of a core with the parts that carry a mod last.

        Files merge by name, the last one winning (``GameAssets.spine_files_from``). A mod preview merges the mod's
        parts after the official ones; the game folder lists parts by file name, so a part left official could come
        after an applied mod part and win a texture it shares by name: the mod's atlas drawn over the official
        texture. Putting every part that is not official last gives the game files the preview's order. Judged by
        the cheap rule (headers, never whole files).
        """
        pin = self._current(pin)
        paths = self._spine_parts(core, pin)
        if not paths:
            raise ServiceError("game_file_not_found", variant=core)
        view = self._view([pin], "family")
        kind = self._kind(core, pin) or "spr"
        parts = [view.part(path, pin, kind) for path in paths]
        return [part.path for part in sorted(parts, key=lambda part: not part.official)]

    @_serialized
    def preview_bundles(self, mod_id: str, pin: Pin | None = None) -> list[Path]:
        """Return omitted parts followed by the mod's overriding parts.

        Parts use the import/apply replacement mapping. Omitted parts prefer verified official
        copies of the mod's own core in this install, falling back to its current files. Pass this order to
        ``GameAssets.spine_files_from`` so mod assets win.
        """
        from .lobby_camera import meta_of

        pin = self._current(pin)
        record = self._record(mod_id)
        camera = meta_of(record)
        if camera is not None:  # a lobby camera shows its source's art
            source = camera["source"]
            return (self._preview(self._record(source[4:]), pin) if source.startswith("mod:")
                    else self.official_bundles(camera["core"], pin=pin))
        return self._preview(record, pin)

    @_serialized
    def save_camera(self, variant_key: str, source: str, scale: float, offset: list[float], prefab: str, root: str,
                    pin: Pin | None = None, log: LogFunc = no_log) -> dict[str, Any]:
        """Keep a memorial lobby camera as a new lobby choice of a variant (``lobby_camera.save``)."""
        from . import lobby_camera

        return lobby_camera.save(self, variant_key, source, scale, offset, prefab, root, self._current(pin), log)

    @_serialized
    def camera_prefab(self, mod_id: str, pin: Pin | None = None) -> tuple[dict, Path, Path | None, Path | None] | None:
        """A lobby camera's parameters and preview prefab (``lobby_camera.preview_files``), None for another mod."""
        from . import lobby_camera

        record = self._record(mod_id)
        meta = lobby_camera.meta_of(record)
        return None if meta is None else (copy.deepcopy(meta), *lobby_camera.preview_files(self, record, self._current(pin)))

    @_serialized
    def lobby_prefab_base(self, core: str, pin: Pin | None = None) -> Path | None:
        """The prefab the previews of a lobby's choices read when a camera holds its prefab (``lobby_camera.live_base``)."""
        from . import lobby_camera

        pin = pin or self.current_pin()
        return lobby_camera.live_base(self, core, pin) if pin is not None and lobby_camera.cameras(self) else None

    # --- the official rigs of portraits, kept per release (rig_archive) ---

    def archive_rigs(self, pin: Pin | None = None, cancel: Callable[[], bool] | None = None) -> int:
        """Keep the official rig of every portrait of an install (default the current one) in the archive
        (``rig_archive``): its atlas and skeleton with its pages' alpha masks, from verified official copies only, each
        distinct rig once. Done again only when the install's portrait files changed (their names carry their CRC).
        With the scan consent only, a core at a time under the library lock, each once nothing else used the library
        for ``RIG_ARCHIVE_QUIET`` (BL-183: page changes, previews and mod switches never queue behind it). Returns how
        many rigs it kept; a core that cannot be read is skipped. Never raises."""
        _background.on = True
        try:
            pin = pin or self.current_pin()
            if pin is None or not self._scan_allowed():
                return 0
            with self._lock:
                index = self._index(pin)
                cores = sorted(core for core, paths in index.items()
                               if paths and all(kind_of_file(path.name) == "spr" for path in paths))
                stamp = hashlib.sha1(json.dumps([[core, sorted(p.name for p in index[core])] for core in cores])
                                     .encode()).hexdigest()
            if self.rigs.stamp(pin.key) == stamp:
                return 0
            kept, waited = 0, 0.0
            with self.rigs.deferred():
                try:
                    for core in cores:
                        preview_gate.yield_to_previews(cancel)
                        wait = self._yield_to_foreground(cancel)
                        if wait is None:
                            return kept
                        waited += wait
                        kept += self._archive_core(core, pin)
                finally:
                    spans.record("library.rigs.yield", waited)
                self.rigs.set_stamp(pin.key, stamp)
            logger.info("Rig archive: %d portrait rigs kept of %d cores", kept, len(cores))
            return kept
        except Exception as error:  # never in the way of the program; the next start tries again
            log_failure(logger, logging.WARNING, "Could not keep the official portrait rigs", error)
            return 0
        finally:
            _background.on = False

    def _yield_to_foreground(self, cancel: Callable[[], bool] | None) -> float | None:
        """Wait until no other caller used the library's lock for ``RIG_ARCHIVE_QUIET`` seconds: how long it waited, None
        once cancelled."""
        started = time.monotonic()
        while True:
            if cancel is not None and cancel():
                return None
            quiet = time.monotonic() - self._lock.used
            if quiet >= RIG_ARCHIVE_QUIET:
                return time.monotonic() - started
            time.sleep(min(0.1, RIG_ARCHIVE_QUIET - quiet))

    @_serialized
    def _archive_core(self, core: str, pin: Pin) -> int:
        """Keep the official rigs of one portrait core (``archive_rigs``)."""
        try:
            parts = sorted(self._spine_parts(core, pin), key=lambda path: path.stat().st_size)
        except (OSError, ServiceError):
            return 0
        atlases: dict[str, bytes] = {}
        skeletons: dict[str, bytes] = {}
        holders: dict[str, Path] = {}  # texture key -> the verified official part that holds it
        for part in parts:
            try:
                official = self._official_for(part, pin, game_first=True)
                contents = integrity.inspect(official, allow_unknown=True)
            except (ServiceError, OSError, ValueError):
                continue
            atlases.update(contents.atlases)
            skeletons.update(contents.skeletons)
            holders.update({key: official for key in contents.textures})
        drawn_by = atlas_remap.pair_atlases(skeletons, atlases)
        kept = 0
        for name, text in atlases.items():
            skeleton = drawn_by.get(name)
            if skeleton is None or self.rigs.has(core, hashlib.sha1(text).hexdigest(),
                                                 hashlib.sha1(skeletons[skeleton]).hexdigest()):
                continue
            try:
                pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(text, name).pages}
                images = atlas_remap.read_inputs(sorted({holders[key] for key in pages if key in holders})).textures
            except (atlas_remap.RemapError, ValueError, UnicodeError, OSError, EOFError):
                continue
            masks = {key: rig_archive.alpha_mask(images[key]) for key in pages if key in images}
            if masks and self.rigs.add(core, name, text, skeleton, skeletons[skeleton],
                                       fingerprint.atlas(text)["layout"], masks):
                kept += 1
        return kept

    def _mod_page_masks(self, paths: list[Path]) -> dict:
        """The alpha masks of a mod's own pages (texture key to mask), kept per file content."""
        key = tuple(sorted((path.name, self._hashes.get(path).sha1) for path in paths))
        masks = self._mod_masks.get(key)
        if masks is None:
            textures = atlas_remap.read_inputs(paths).textures
            masks = {name: rig_archive.alpha_mask(image) for name, image in textures.items()}
            if len(self._mod_masks) >= 256:
                self._mod_masks.clear()
            self._mod_masks[key] = masks
        return masks

    def _official_page_masks(self, paths: list[Path]) -> dict:
        """The alpha masks of the pages of verified official copies (texture key to mask), kept per file content."""
        return self._mod_page_masks(paths)

    def _archived_rigs(self, paths: list[Path], atlases: Mapping[str, bytes],
                       current: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, dict]:
        """Of the official ``atlases`` as they are now that a mod's textures paint without the mod carrying them, the
        ones its pages were drawn for in an earlier release the archive keeps (``rig_archive.best``): per atlas name a
        ``fingerprint.painted``-style entry (``text``, base64 ``skeleton``, ``layout``, ``archived``). ``current`` gives
        the installed release's page masks; without it, the archive's rig of the installed layout stands for them, and
        an atlas whose installed rig the archive does not keep is left out. Nothing is decoded unless the archive keeps
        a rig of another layout for the core. Never raises."""
        if not paths or not atlases:
            return {}
        core = core_of(paths[0].name)
        try:
            kept = self.rigs.rigs(core)
            now = {name: fingerprint.atlas(text)["layout"] for name, text in atlases.items()}
            stem = lambda name: Path(atlas_remap.texture_key(name)).stem
            candidates = {name: [rig for rig in kept if stem(rig.atlas) == stem(name) and rig.layout != layout]
                          for name, layout in now.items()}
            if not any(candidates.values()):
                return {}
            key = (tuple(sorted((path.name, self._hashes.get(path).sha1) for path in paths)),
                   tuple(sorted(now.items())), self.rigs.generation(), current is not None)
            if key in self._rig_matches:
                return self._rig_matches[key]
            mod = self._mod_page_masks(paths)
            masks_now = current() if current is not None else None
            result = {}
            for name, rigs in candidates.items():
                installed = masks_now if masks_now is not None else next(
                    (rig.unpacked() for rig in kept if stem(rig.atlas) == stem(name) and rig.layout == now[name]), None)
                if not rigs or installed is None:
                    continue
                found = rig_archive.best(rigs, mod, installed)
                if found is None:
                    continue
                rig = found[0]
                text, skeleton = self.rigs.read(rig.atlas_sha1), self.rigs.read(rig.skeleton_sha1)
                if text is None or skeleton is None:
                    continue
                result[name] = {"text": text.decode("utf-8-sig"), "layout": rig.layout, "archived": True,
                                "skeleton": base64.b64encode(skeleton).decode("ascii")}
            if len(self._rig_matches) >= 256:
                self._rig_matches.clear()
            self._rig_matches[key] = result
            return result
        except (atlas_remap.RemapError, ValueError, UnicodeError, OSError, EOFError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not match a mod against the kept rigs", error)
            return {}

    @_serialized
    def recorded_rig(self, mod_id: str, atlases: Mapping[str, bytes] | None = None) -> dict[str, dict]:
        """The rigs a portrait mod's textures were drawn for, per atlas basename: what its fingerprint recorded
        (``fingerprint.painted``, the entries that keep their skeleton) and, for the ``atlases`` its preview draws with
        (name to bytes) that no record covers, the earlier release the archive keeps that its pages follow
        (``_archived_rigs``). Empty for another mod. Its previews draw it on them where the atlas was repacked since
        (``fingerprint.drawn_on``)."""
        record = self._record(mod_id)
        if record.get("kind") != "spr":
            return {}
        painted = (self._read_built_against(record) or {}).get("painted") or {}
        known = {name: entry for name, entry in painted.items()
                 if isinstance(entry.get("text"), str) and isinstance(entry.get("skeleton"), str)}
        unrecorded = {name: data for name, data in (atlases or {}).items() if integrity.basename(name) not in known}
        if unrecorded:
            archived = self._archived_rigs(self._paths(record), unrecorded)
            known.update({integrity.basename(name): entry for name, entry in archived.items()})
        return known

    @_serialized
    def rig_stamp(self, mod_id: str) -> str:
        """What a portrait mod's preview drawn on a kept rig depends on beyond its files: the archive's state for its
        core (``recorded_rig``), as a short stamp; empty for another mod and for a core the archive keeps one layout of
        at most (nothing to draw on but the installed rig), so a first archive redraws no preview."""
        record = self._record(mod_id)
        if record.get("kind") != "spr":
            return ""
        paths = self._paths(record)
        rigs = self.rigs.rigs(core_of(paths[0].name)) if paths else []
        if len({rig.layout for rig in rigs}) < 2:
            return ""
        return hashlib.sha1(json.dumps([[rig.atlas_sha1, rig.skeleton_sha1] for rig in rigs]).encode()).hexdigest()[:16]

    def _preview(self, record: dict[str, Any], pin: Pin) -> list[Path]:
        paths = self._paths(record)
        targets = self._spine_parts(core_of(paths[0].name), pin)
        replaced = self._replaced_parts({path.name: path for path in paths}, targets, pin)
        others = [self._official_or_current(path, pin) for path in targets if path not in replaced]
        return others + paths

    @_serialized
    def preview_complements(self, core: str, names: list[str], pin: Pin | None = None, *,
                            files: dict[str, Path] | None = None) -> list[Path]:
        """Parts omitted by an incoming preview, resolved with the same rule as a library mod."""
        pin = self._current(pin)
        targets = self._spine_parts(core, pin)
        if files is not None:
            replaced = self._replaced_parts(files, targets, pin)
        else:
            replaced = resolver.resolve({name: Path(name) for name in names}, targets).replaced
        return [self._official_or_current(path, pin) for path in targets if path not in replaced]

    def preview_reads(self, policy: str, pin: Pin | None = None) -> PreviewReads:
        """The bytes a 3D preview of a chosen source reads for the game files its source does not supply. A model
        export also loads shared bundles and textures stored under other cores (ch0263's materials take theirs from
        the NPC en0002), which other mods replace; a preview never reads them as the game folder holds them now, so it
        follows neither the mods switch nor the game's state:

        - ``official`` (the Original card): verified official bytes (``_official_or_current``); the current bytes only
          where no official copy is left at all, as for the slot's own parts.
        - ``configured`` (a mod or an import): the bytes the configured source of each file's slot writes (its kept
          built copy, else the mod's own file of that name, else the game file while it carries that source), else
          as ``official``.

        Not serialized, like ``official_bundles``: the export calls ``resolve`` under the model lock."""
        if policy not in ("official", "configured"):
            raise ValueError(f"Unknown preview policy {policy!r}")
        pin = self._current(pin)
        folder: dict[str, Any] = {}
        if policy == "configured":
            with contextlib.suppress(ServiceError):
                folder = (((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {})
        configured, built = folder.get("configured") or {}, folder.get("built") or {}
        key = hashlib.sha256(json.dumps([policy, configured, built], sort_keys=True, default=str).encode()).hexdigest()
        core_map = self._core_map(pin)
        dirs = list(pin.dirs)
        chosen: dict[Path, Path] = {}
        lock = threading.Lock()

        def official(path: Path) -> Path:
            try:
                check_resource_path(path, dirs)
            except ServiceError:
                return path  # outside this install's resource folders: the library holds nothing for it
            return self._official_or_current(path, pin)

        def configured_file(path: Path) -> Path | None:
            kind = kind_of_file(path.name)
            if not kind:
                return None
            core = core_of(path.name)
            slot = core_map.slot(core) if kind == "model" else core  # the key _configure and the built copies use
            source = (configured.get(slot) or {}).get("source") or ""
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            if record is None:
                return None
            own = {file.name: file for file in self._paths(record)}
            kept = built.get(slot) or {}
            part = (kept.get("parts") or {}).get(path.name) if kept.get("source") == source else None
            if isinstance(part, dict):
                sha1 = part.get("sha1")
                candidate = own.get(part.get("mod_file")) if part.get("mod_file") else (
                    self.root / "built" / f"{sha1}.bundle"
                    if isinstance(sha1, str) and re.fullmatch(r"[0-9a-f]{40}", sha1) else None)
                if candidate is not None and candidate.is_file() and self._hashes.get(candidate).sha1 == sha1:
                    return candidate
            if path.name in own and own[path.name].is_file():
                return own[path.name]
            applied = self._applied.get(str(path.resolve()))
            if applied and applied.get("source") == source and self._hashes.get(path).sha1 == applied.get("sha1"):
                return path  # the game file is the configured write (an adapted mod without a kept copy)
            return None

        def resolve(path: Path) -> Path:
            path = Path(path)
            with lock:
                if path in chosen:
                    return chosen[path]
            result = (configured_file(path) if policy == "configured" else None) or official(path)
            with lock:
                chosen[path] = result
            return result

        def trusted(path: Path) -> bool:
            try:
                return Path(path).resolve().is_relative_to(self.root)
            except (OSError, RuntimeError):
                return False

        return PreviewReads(resolve, key, trusted)

    def _incomplete(self, record: dict[str, Any], pin: Pin) -> bool:
        targets = self._spine_parts(core_of(record["files"][0]), pin)
        replaced = self._replaced_parts({path.name: path for path in self._paths(record)}, targets, pin)
        return any(self._official_or_current(path, pin) == path and not self._is_official(path)
                   for path in targets if path not in replaced)

    @_serialized
    def preview_incomplete(self, mod_id: str, pin: Pin | None = None) -> bool:
        """Whether previewing this partial mod needs non-official live parts in this install."""
        from .lobby_camera import meta_of

        pin = pin or self.current_pin()
        record = self._record(mod_id)
        camera = meta_of(record)
        if camera is not None:  # a lobby camera is as complete as its source's art
            source = camera["source"]
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            if record is None:
                return False
        return bool(pin and self._incomplete(record, pin))

    # --- switching ---

    def _plan(self, record: dict[str, Any], log: LogFunc, pin: Pin) -> list[PlannedGroup]:
        paths = self._paths(record)
        files = {path.name: path for path in paths}
        targets = (self._other_targets(files, pin) if record["kind"] == "other" else
                   self._spine_parts(core_of(paths[0].name), pin))
        resolution = self._resolve(files, targets, pin)
        current = resolution.current
        groups = {}
        inspection = integrity.Inspection()
        for target, path in current.items():
            if path.name != target.name:
                # A whole file under another name is written as it is: it must use the part's serialized format.
                integrity.check_format(inspection.metadata(path), path,
                                       inspection.metadata(self._official_or_current(target, pin)))
            parsed = parse_filename(path.name)
            group = groups.setdefault(parsed.prefix, PlannedGroup(parsed.prefix, parsed.category, parsed.core, []))
            group.files.append(PlannedFile(path, parsed, Action.DIRECT_REPLACE, existing_path=target))
        planned = list(groups.values())
        older = {name: path for name, path in files.items() if path not in current.values()}
        if older:
            if record["kind"] == "other":
                planned.append(PlannedGroup(prefix="", category=None, core=record["target"], files=[
                    PlannedFile(path, parse_filename(name), Action.UPDATE)
                    for name, path in older.items()], target_paths=self._other_targets(older, pin)))
            else:
                planned.extend(self._groups_of(resolution))
        if not planned or any(file.action not in (Action.DIRECT_REPLACE, Action.UPDATE)
                              for group in planned for file in group.files):
            raise ServiceError("game_file_not_found", variant=record["target"])
        return planned

    @staticmethod
    def _plan_targets(groups: list[PlannedGroup]) -> list[Path]:
        return sorted({path for group in groups for path in (
            group.target_paths + [file.existing_path for file in group.files if file.existing_path is not None])})

    def _stage_official(self, paths: list[Path], directory: Path, pin: Pin,
                        *, current_fallback: bool = False) -> dict[Path, Path]:
        staged = {}
        directory.mkdir(parents=True, exist_ok=True)
        for path in paths:
            target = directory / path.name
            if target in staged.values():
                raise ServiceError("game_file_conflict", file=path)
            source = self._official_or_current(path, pin) if current_fallback else self._official_for(path, pin)
            atomic_copy(source, target)
            staged[path] = target
        return staged

    def _stage_direct(self, targets: list[Path], sources: dict[str, Path], directory: Path, pin: Pin,
                      updating: set[Path]) -> dict[Path, Path]:
        """Stage whole-file replacements and omitted parts without requiring an official base.

        Asset transfers still require verified official targets, including generic updates.
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
                if error.code != "original_unavailable" or path in updating:
                    raise
                source = sources.get(path.name) or path
            atomic_copy(source, prepared)
            staged[path] = prepared
        return staged

    def _commit(self, replacements: dict[Path, Path], source: str, log: LogFunc,
                progress: ProgressCallback | None = None, pin: Pin | None = None, prune: bool = True,
                build_keys: dict[Path, str] | None = None, create_backup: bool = False,
                expected: dict[Path, str] | None = None, kept_current: list[str] | None = None,
                untested: bool = True) -> list[Path]:
        """Write prepared files over game files as one unit, keeping what they replace: the official file in
        originals/ (once), a file kept nowhere else as a library mod (_keep_replaced). Every written part is rolled
        back on failure. With ``prune``, kept originals no longer needed are deleted afterwards, whatever the
        outcome; bulk operations prune once at their end. ``untested``: a write of another source than official onto
        a core an untested build changed needs the user's confirmation (``_check_untested``); False for a rollback
        that puts back what was there."""
        pin = self._current(pin)
        deletion = getattr(self, '_asset_deletion', None)
        if deletion:
            deletion.remember_game(replacements, pin)
        try:
            with self._transaction_lock():
                return self._write(replacements, source, log, progress, pin, build_keys,
                                   create_backup=create_backup, untested=untested,
                                   **({"expected": expected} if expected else {}),
                                   **({"kept_current": kept_current} if kept_current is not None else {}))
        finally:
            if prune:
                self._prune_quietly()

    @contextlib.contextmanager
    def _writable(self, paths: list[Path], pin: Pin, *, recovering: bool = False) -> Iterator[None]:
        """The checks right before game files are written: the game not running, Steam not updating or verifying the
        install, the files inside its resource folders and lockable, and the caller's own check (write_check)."""
        if self._write_check is not None:
            self._write_check(pin)
        self._check_idle([pin])
        with safety.writable_game_files(paths, list(pin.dirs), allow_missing=recovering):
            yield

    @contextlib.contextmanager
    def _transaction_lock(self) -> Iterator[None]:
        """Lock before mods, state and profiles; nested writes already hold this process lock. Taking the lock
        recovers every pending journal first, every time, and raises the real failure when one cannot be rolled back
        (a write never goes on over a journal it could not settle); the failure is remembered for the reads and for
        the writes that do not take this lock (``_check_recovery``)."""
        if self._transacting:
            yield
            return
        with file_lock(self.root / "transactions.lock", timeout=10):
            self._transacting = True
            try:
                self._refresh_state(migrate=False)  # nothing is saved before the journals are settled
                try:
                    self._recover_transactions()
                except Exception as error:
                    self._remember_recovery(error)
                    raise
                self._remember_recovery(None)
                self._check_writable()
                yield
            finally:
                self._transacting = False

    def _journals(self) -> tuple[str, ...]:
        """The pending journals: write, import and deletion journals under the library, as sorted paths."""
        return tuple(sorted(str(path) for folder in ("transactions", "import-transactions", "asset-deletions")
                            for path in (self.root / folder).glob("*/journal.json")))

    def _recover_pending(self) -> None:
        """Recover before indexing game files, including one moved away just before a crash. Raises the failure of a
        journal that cannot be settled (it stays pending); nothing happens without a journal."""
        if self._transacting:
            return
        if self._journals():
            with self._transaction_lock():
                pass
        else:
            self._recovery_error = self._recovery_seen = self._recovery_logged = None

    def _recover_for_read(self) -> None:
        """Attempt recovery before a read, never failing it: a failure is logged once, remembered (with the pending
        journals and the time) and the read goes on. A read tries again only when the set of journals changed or
        RECOVERY_RETRY seconds have passed; a retry that finds other journals still pending but raises nothing
        follows the same limit. Writes recover on their own (``_transaction_lock``)."""
        if self._transacting:
            return
        journals = self._journals()
        if not journals:
            self._recovery_error = self._recovery_seen = self._recovery_logged = None
            return
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
        """Note the outcome of a recovery attempt: the failure writes refuse with (None: it succeeded), and the
        journals still pending with the time, which limit how often a read tries again."""
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
        """Refuse a change of state or configuration while a recovery has failed, with that failure. Recovery is
        attempted again first (a closed game, a repaired file), so the refusal is the real, current one. Not during a
        recovery itself or the writes under the transaction lock, which recovered before they started."""
        if self._recovery_error is not None and not self._transacting:
            self._recover_pending()

    def _check_writes(self) -> None:
        """The gate of every change of the configuration (``Profiles.gate``): refused with the real error while a
        recovery has failed (``_check_recovery``) or the state cannot be written (``_check_writable``), because a
        library that reads an empty state would judge every source gone and settle that into profiles.json."""
        self._check_recovery()
        self._check_writable()

    def _writes_refused(self) -> bool:
        """Whether changes of state and configuration are refused now, without trying again: a recovery has failed or
        the state cannot be written. For the reads that settle something on the side and skip it instead."""
        return self._state_error is not None or (self._recovery_error is not None and not self._transacting)

    def state_unreadable(self) -> bool:
        """Whether this library holds no state from a good read of state.json: reads answer an empty library, which
        is not the user's library (callers must not take a baseline from it). The file is read again first if it was
        unreadable, as it may have been fixed since."""
        with self._lock:
            if not self._state_good:
                self._refresh_state()
            return not self._state_good

    def _recover_transactions(self) -> None:
        """Settle every pending journal under the transaction lock: imports, then write journals, then asset deletions.
        A failure (ServiceError) leaves the journal and stops here; a write journal whose install is not known now waits
        (skipped, logged once). A game file changed outside the library is left as it is (``_rollback_write``)."""
        self._recover_imports()
        for path in sorted((self.root / "transactions").glob("*/journal.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data["version"] != 1 or data["phase"] not in ("preparing", "writing", "committed") \
                        or not path.resolve().is_relative_to(self.root / "transactions"):
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
                        logger.warning("Write journal %s waits for its install, which is not known now",
                                       path.parent.name)
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
                with self._writable(targets, pin, recovering=True):
                    left = self._rollback_write(data, pin)
                self._release_failed([Path(item["target"]) for item in data["files"]
                                      if item["kept_now"] and item["target"] not in left], pin)
                self._drop_mods(self._droppable(data["kept"], left))
                shutil.rmtree(path.parent)
                self._waiting.discard(path.parent.name)
                logger.info("Recovered interrupted library write %s", path.parent.name)
            except (ValueError, TypeError, KeyError) as error:
                raise ServiceError("library_io_error", file=path) from error
        from .asset_delete import recover
        recover(self)

    def _droppable(self, kept: list[str], left: set[str]) -> list[str]:
        """Of the mods a rolled back write kept from game files, those to forget: not the ones kept from a file left
        as it is (changed outside the library), whose bytes are in no game file again."""
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
        is dropped and its original stays. It is returned with the targets left, so that nothing it kept is released."""
        left: set[str] = set()
        superseded: set[str] = set()
        for item in reversed(data["files"]):
            target = Path(item["target"])
            if not target.exists() and _has_successor(self._index(pin), target):
                logger.warning("Game file %s was superseded by a game update, not restoring it",
                               core_of(target.name))
                left.add(item["target"])
                superseded.add(item["target"])
                continue
            current = self._hashes.get(target).sha1 if target.exists() else None
            original = self.root / item["restore"] if item["restore"] else None
            if original is not None and not original.exists():
                if current != item["after"]:
                    raise ServiceError("original_unavailable", file=target)
                verified_move(target, original, item["after"])
                current = None
            if current == item["before"]:
                continue
            if current not in (None, item["after"]):
                logger.warning("Game file %s was changed outside the library, leaving it as it is",
                               core_of(target.name))
                left.add(item["target"])
                continue
            backup = self.root / item["rollback"]
            verify_file(backup, item["before"])
            if item["official"] and item["kept_now"] and not self._needed_elsewhere(
                    target.name, {str(target.resolve())}, self._all_pins()):
                verified_move(backup, target, item["before"])
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

    def _write(self, replacements: dict[Path, Path], source: str, log: LogFunc,
               progress: ProgressCallback | None, pin: Pin, build_keys: dict[Path, str] | None = None,
               *, create_backup: bool = False, expected: dict[Path, str] | None = None,
               kept_current: list[str] | None = None, untested: bool = True) -> list[Path]:
        dirs = list(pin.dirs)
        for target, prepared in replacements.items():
            check_resource_path(target, dirs)
            if self._hashes.get(prepared).trailing is None:
                raise ServiceError("bundle_invalid", file=prepared)
        changed = {target: prepared for target, prepared in replacements.items()
                   if self._hashes.get(target).sha1 != self._hashes.get(prepared).sha1}
        if changed and untested and source != "official":
            # the last check: every write path asked before it built (apply, a toggle's plan); one that did not, asks
            written = sorted(changed)
            self._check_untested([(pin, self._untested_entry(pin, core_of(written[0].name), written), written)])
        fingerprints = {target: self._hashes.get(prepared) for target, prepared in replacements.items()}
        tails = {target: read_tail(prepared) if fingerprints[target].trailing else None
                 for target, prepared in replacements.items()}
        old_applied = dict(self._applied)
        stamp = now()

        def records() -> dict[str, dict[str, Any]]:
            out = {}
            for target in replacements:
                fingerprint = fingerprints[target]
                stat = target.stat()
                record = {"source": source, "sha1": fingerprint.sha1, "size": stat.st_size,
                          "mtime": stat.st_mtime_ns,
                          "official": self._official_sha1(target, pin) == fingerprint.sha1,
                          "at": stamp, "tail": tails[target],
                          "crc": crc_state(fingerprint.crc, fingerprint.trailing, target.name)}
                if build_keys and target in build_keys:
                    record["build"] = build_keys[target]
                previous = self._applied.get(str(target.resolve()))
                if previous and {k: v for k, v in previous.items() if k != "at"} == {
                        k: v for k, v in record.items() if k != "at"}:
                    record = previous  # nothing new: the write time stays the one of the bytes there
                out[str(target.resolve())] = record
            return out

        if not changed:
            new = records()
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
                        if error.code != "original_unavailable":
                            raise
                # Include omitted parts of the core: the outside mod must survive as a whole.
                kept = self._keep_replaced(list(replacements), pin)
                directory = self.root / "transactions" / uuid.uuid4().hex
                directory.mkdir(parents=True)
                sync_directory(self.root)
                sync_directory(directory.parent)
                journal = directory / "journal.json"
                items = []
                pins = self._all_pins()
                for i, target in enumerate(changed):
                    original = self._original_path(target)
                    official = target in officials and self._hashes.get(officials[target]).sha1 == self._hashes.get(target).sha1
                    new_original = officials.get(target) == target and not original.exists()
                    if new_original:
                        kept_now.append(target)
                    restore = source == "official" and changed[target] == original and not self._needed_elsewhere(
                        target.name, {str(target.resolve())}, pins)
                    backup = original if official else directory / f"{i}.bundle"
                    items.append({"target": str(target.resolve()), "before": self._hashes.get(target).sha1,
                                  "after": fingerprints[target].sha1, "official": official,
                                  "rollback": str(backup.relative_to(self.root)),
                                  "kept_now": new_original,
                                  "restore": str(original.relative_to(self.root)) if restore else None})
                transaction = {"version": 1, "phase": "preparing",
                               "pin": {"key": pin.key, "root": str(pin.root), "dirs": list(map(str, pin.dirs)),
                                       "server": pin.server}, "files": items, "kept": kept,
                               "old_applied": {item["target"]: old_applied.get(item["target"]) for item in items}}
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
                            progress(i, len(changed), t("webui.progress.restore" if source == "official"
                                                        else "webui.progress.apply", name=target.name))
                    saving_state = True
                    stage = "state"
                    self._applied.update(records())
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

    def _stage_adapted(self, sources: list[Path], targets: list[Path], directory: Path,
                       log: LogFunc, pin: Pin) -> dict[Path, Path]:
        staged = self._stage_official(targets, directory / "targets", pin, current_fallback=True)
        output = directory / "output"
        output.mkdir(parents=True)
        adapted = adapt_variant_sources(
            sources, list(staged.values()), directory / "sources", self.settings.asset_types(), log)
        result = rebuild(
            adapted, list(staged.values()), output,
            self.settings.asset_types(), self.settings.save_options(self.settings.crc_needed(targets[0])),
            self.settings.spine_options(), skip_unchanged=True, match_strategy="name_type",
            anim_check=self.settings.anim_check_options(), log=log)
        if not result.success:
            raise ServiceError("mod_apply_failed", file=targets[0])
        reverse = {value: key for key, value in staged.items()}
        replacements = dict(staged)
        for pair in result.file_pairs:
            replacements[reverse[pair.source]] = pair.output
        return replacements

    def _costume_files(self, other: str, pin: Pin) -> tuple[str, list[Path], list[Path]]:
        """Another variant's model as a costume source: its dev name, the game files that hold its prefab and every
        game file of its own (the model slot; for a variant with a model but no slot, the field character bundles,
        which hold other students' field models as well, and the parts of its own dependency bundles that can hold a
        story-only or UI-only model)."""
        variant = self._variant(other)
        core = getattr(variant, "model", None)
        if core:
            files = self._game_files(other, "model", pin)
            prefabs = [path for path in files if path.name.startswith("character-")]
            if prefabs:
                return core.lower(), prefabs, files
        dev = getattr(variant, "field_model", None)
        if dev:
            names = self._names(pin)
            files = sorted((path for name, path in names.items() if name.startswith("field-character-_mxload")
                            or (own_bundle(name, dev) and may_hold_model_root(name))), key=lambda path: path.name)
            if files:
                return dev.lower(), files, files
        raise ServiceError("game_file_not_found", variant=other)

    def _costume_source(self, other: str, pin: Pin, *, preview: bool = False) -> tuple[str, list[Path]]:
        """The dev name and the official copies of another variant's model files (the build's inputs)."""
        dev, _, files = self._costume_files(other, pin)
        resolve = self._official_or_current if preview else self._official_for
        return dev, [resolve(path, pin) for path in files]

    def _stage_costume(self, other: str, targets: list[Path], directory: Path, log: LogFunc, pin: Pin, *,
                       preview: bool = False, report: dict[str, Any] | None = None,
                       physics: bool = False, cross: bool = False) -> dict[Path, Path]:
        """The model slot ``targets`` (official copies) carrying the costume of ``other``'s model: changed parts built
        into ``directory``, the others as staged official copies. Reads official bytes only: the slot's own parts, the
        source model's files, and any other bundle the CAB index finds. ``physics``: Optimize Physics (BL-132), the
        build may also choose Rule B, or a rule built with the followed leg groups made rigid; ``report["physics"]`` says
        whether it chose one of them. ``cross``: another student's costume
        (``_costume_cross``, BL-131), built past the stretch limit as well and carrying its halo."""
        from ..gameassets.model_adapt import adapt

        staged = self._stage_official(targets, directory / "targets", pin, current_fallback=True)
        dev, prefab_files, files = self._costume_files(other, pin)
        resolve = self._official_or_current if preview else self._official_for
        index: dict[str, Path] = {}
        sources = [resolve(path, pin) for path in files]
        chosen = {path.name for path in prefab_files}
        for path in sources:
            for cab in cab_names(path):
                index.setdefault(cab, path)
        for path in staged.values():
            for cab in cab_names(path):
                index[cab] = path
        cabs = self._cab_index(pin)

        def resolver(wanted: set[str], hint: Path | None) -> dict[str, str]:
            return {cab: str(self._official_or_current(Path(path), pin)) for cab, path in cabs.resolve(wanted, hint).items()}

        target_dev = self._core_map(pin).slot(core_of(targets[0].name))
        from ..gameassets.service import model_lock

        with model_lock("costume"):
            result = adapt(index=index, resolver=resolver, target_files=list(staged.values()),
                           target_dev=target_dev, source_files=[p for p in sources if p.name in chosen],
                           source_dev=dev, open_writable=Bundle.load, physics=physics,
                           halo=cross)
        if report is not None and result.plan.unfit:
            report["unfit"] = {"source": "stretch", "adapted": f"{result.plan.stretch:.2f}", "own": f"{result.plan.own:.2f}"}
        if report is not None:
            report["physics"] = result.hems is not None and (result.hems.skirt is not None or result.hems.regrouped)
        output = directory / "output"
        output.mkdir(parents=True, exist_ok=True)
        reverse = {value: key for key, value in staged.items()}
        replacements = dict(staged)
        for path, bundle in result.changed.items():
            target = reverse[path]
            ok, message = bundle.save(output / path.name, self.settings.save_options(self.settings.crc_needed(target)))
            if not ok:
                raise ServiceError("mod_apply_failed", file=target)
            replacements[target] = output / path.name
        return replacements

    def _costume_signature(self, variant_key: str, other: str, pin: Pin) -> tuple[str, list[Path]]:
        """The build signature of another variant's costume on a variant's model slot (the preview's, from official
        bytes where there are any, else the current ones), and the slot's files. Keyed by the costume rule's version
        rather than the program's (BL-134): a new program build keeps the verdicts and previews of the same rule."""
        from ..gameassets.costume_rule import COSTUME_RULE

        rule = f"costume-rule:{COSTUME_RULE}"
        self._variant(variant_key)
        if other == variant_key:
            raise ServiceError("source_unsupported", source=f"variant:{other}")
        self._variant(other)
        targets = self._game_files(variant_key, "model", pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=variant_key)
        _, inputs = self._costume_source(other, pin, preview=True)
        try:
            return self._build_signature(inputs, targets, pin, rule), targets
        except ServiceError as error:
            if error.code != "original_unavailable":
                raise
            return hashlib.sha1(repr(("current", rule, [(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                                                    for p in [*inputs, *targets]])).encode()).hexdigest(), targets

    def _costume_cross(self, slot: Any, other: str) -> bool:
        """Whether another variant's costume on the model of ``slot`` (its variant, None when not known) is another
        student's (BL-131) and carries its own halo. Every pair may be applied past the stretch limit (BL-138)."""
        return slot is not None and self._variant(other).family != slot.family

    def _costume_allowed(self, verdict: dict[str, Any] | None) -> bool:
        """Whether a pair's saved verdict lets it be built for the game: unchecked, "ok" or "preview" (past the
        stretch limit); never a refusal."""
        return verdict is None or verdict["ok"] or verdict.get("preview")

    def _verdicts(self) -> dict[str, dict[str, Any]]:
        path = self.root / "costume-verdicts.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        verdicts = data.get("verdicts") if isinstance(data, dict) and data.get("version") == 1 else None
        return {key: value for key, value in (verdicts or {}).items()
                if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("ok"), bool)}

    def _save_verdict(self, signature: str, ok: bool, reason: str | None = None,
                      detail: Mapping[str, Any] | None = None, *, preview: bool = False) -> dict[str, Any]:
        verdict = {"ok": ok, "reason": reason, "detail": {str(k): str(v) for k, v in (detail or {}).items()},
                   "at": now(), **({"preview": True} if preview and not ok else {})}
        verdicts = self._verdicts()
        verdicts[signature] = verdict
        kept = dict(sorted(verdicts.items(), key=lambda item: str(item[1].get("at", "")), reverse=True)[:COSTUME_VERDICTS])
        try:
            atomic_json(self.root / "costume-verdicts.json", {"version": 1, "verdicts": kept})
        except OSError as error:  # the verdict is still returned; the pair is checked again next time
            log_failure(logger, logging.WARNING, "Could not save a costume verdict", error)
        return verdict

    @_serialized
    def costume_verdict(self, variant_key: str, other: str, pin: Pin | None = None) -> dict[str, Any] | None:
        """Whether another variant's costume on a variant's model was built and exported for the current build
        signature (the program, the official bytes of both models, the save options): {"ok", "reason", "detail",
        "at"}, plus "preview" for a pair past the stretch limit (still offered), or None when not judged
        yet. A pair the Switch 3D Model card offers has one with ok or preview (BL-122)."""
        signature, _ = self._costume_signature(variant_key, other, self._current(pin))
        return self._verdicts().get(signature)

    @_serialized
    def record_costume_verdict(self, variant_key: str, other: str, ok: bool, reason: str | None = None,
                               detail: Mapping[str, Any] | None = None, pin: Pin | None = None) -> dict[str, Any]:
        """Save the verdict of a pair checked to its end (its preview built and exported, or refused)."""
        signature, _ = self._costume_signature(variant_key, other, self._current(pin))
        return self._save_verdict(signature, ok, reason, detail)

    @_serialized
    def optimize_costume(self, variant_key: str, source: str, name: str, pin: Pin | None = None) -> dict[str, Any]:
        """Optimize Physics (BL-132): a variant's model slot carrying ``source``'s costume ("variant:<key>") built with
        the physics rules (Rule B, the skirt chains as new bones), kept as a model mod of the variant named after
        ``name`` (the costume as the screen shows it): {"mod": id}, the copy made before when there is one. {"unneeded":
        True, "reason": ...} when there is nothing to build: "official" for the official model, whose own clips move its
        skirt, and "no_gain" for a costume that no physics rule improves within the stretch limit."""
        pin = self._current(pin)
        self._variant(variant_key)
        if source == "official":
            return {"unneeded": True, "reason": "official"}
        if not isinstance(source, str) or not source.startswith("variant:"):
            raise ServiceError("source_unsupported", source=str(source))
        other = source[8:]
        signature, targets = self._costume_signature(variant_key, other, pin)
        verdict = self._verdicts().get(signature)
        cross = self._costume_cross(self._variant(variant_key), other)
        if not self._costume_allowed(verdict):  # refused: never built for the game
            raise ServiceError("source_unsupported", source=verdict.get("reason") or source)
        made = next((record for record in self._records.values() if self._optimized_from(record) == source
                     and record["kind"] == "model" and self._target_of(record) == variant_key), None)
        if made is not None:
            return {"mod": made["id"]}
        label = name.strip()[:200] if isinstance(name, str) and name.strip() else other
        report: dict[str, Any] = {}
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            replacements = self._stage_costume(other, targets, Path(temporary) / "model", no_log, pin, report=report,
                                               physics=True, cross=cross)
            if not report.get("physics"):
                return {"unneeded": True, "reason": "no_gain"}
            built = {path.name: path for path in replacements.values() if path.parent.name == "output"}
            record = self._store(built, label, variant_key, "model", metadata={"optimized_from": source},
                                 built_against=self._built_against(built, pin, "import"))
        return {"mod": record["id"]}

    @_serialized
    def costume_preview(self, variant_key: str, other: str, pin: Pin | None = None) -> list[Path]:
        """The parts of a variant's model slot carrying another variant's costume, for a 3D preview: built once per
        build signature into built/previews/<signature> and kept for the newest few (the export cache keys on their
        paths). Reads official bytes where there are any, else the current ones, like the other previews."""
        pin = self._current(pin)
        signature, targets = self._costume_signature(variant_key, other, pin)
        cross = self._costume_cross(self._variant(variant_key), other)
        folder = self.root / "built" / "previews" / signature
        done = folder / "parts.json"
        if done.is_file():
            try:
                kept = json.loads(done.read_text())
                kept = kept if isinstance(kept, dict) else {"parts": kept, "unfit": None}
                parts = [folder / name for name in kept["parts"] if is_safe_file_name(name)]
                if parts and all(path.is_file() for path in parts):
                    os.utime(folder)
                    if kept.get("unfit") and signature not in self._verdicts():
                        self._save_verdict(signature, False, "stretch", kept["unfit"], preview=True)
                    return parts
            except (OSError, ValueError, KeyError, TypeError):
                pass
        report: dict[str, Any] = {}
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            try:
                replacements = self._stage_costume(other, targets, Path(temporary) / "model", no_log, pin, preview=True,
                                                   report=report, cross=cross)
            except ServiceError as error:
                if error.code == "source_unsupported":  # the rule cannot carry this pair: never offered again
                    self._save_verdict(signature, False, error.params.get("source"), error.params)
                raise
            built = [path for target, path in replacements.items() if path.parent.name == "output"]
            folder.mkdir(parents=True, exist_ok=True)
            for path in built:
                atomic_copy(path, folder / path.name)
            done.write_text(json.dumps({"parts": [path.name for path in built], "unfit": report.get("unfit")}))
        if report.get("unfit"):  # past the stretch limit: still offered, with a caution
            self._save_verdict(signature, False, "stretch", report["unfit"], preview=True)
        previews = sorted((p for p in folder.parent.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in previews[COSTUME_PREVIEWS:]:
            remove_tree(stale)
        return [folder / path.name for path in built]

    def _install_direct(self, record: dict[str, Any], targets: list[Path], work: Path, log: LogFunc,
                        progress: ProgressCallback | None, pin: Pin) -> dict[Path, Path]:
        sources = {path.name: path for path in self._paths(record)}
        by_name = {path.name: path for path in targets}
        if (len(sources) == len(record["files"]) and len(by_name) == len(targets)
                and sources.keys() <= by_name.keys()
                and all(not self._needs_crc_fix(path, by_name[name]) for name, path in sources.items())):
            # These parts already have the current names and required CRC. The
            # transaction checks and copies their exact bytes once; staging an
            # official copy and replacing it with the same Mod adds no protection.
            if progress:
                progress(0, 1, t("webui.progress.process", name=core_of(record["files"][0])))
            return {target: sources[target.name] if target.name in sources else self._official_or_current(target, pin)
                    for target in targets}
        if record["kind"] == "other":
            current = self._replacements(sources, targets, pin)
            staged = self._stage_direct(targets, sources, work / "targets", pin,
                                        set(targets) - current.keys())
            output = work / "output"
            output.mkdir(parents=True, exist_ok=True)
            sources = self._paths(record)
            for target, original in staged.items():
                exact = current.get(target)
                if exact:
                    if not self._needs_crc_fix(exact, target):
                        atomic_copy(exact, original)
                        continue
                    # Same-name mods retain the whole uploaded bundle, including types outside the update filter.
                    parsed = parse_filename(exact.name)
                    group = PlannedGroup(parsed.prefix, parsed.category, parsed.core, [
                        PlannedFile(exact, parsed, Action.DIRECT_REPLACE, existing_path=original, need_crc_fix=True)])
                    if install_staged([group], output, self.settings.asset_types(),
                                      self.settings.save_options(self.settings.crc_needed(target)),
                                      self.settings.spine_options(), log=log):
                        raise ServiceError("mod_apply_failed", id=record["id"])
                    continue
                matching = resolver.same_prefix(sources, target)
                if not matching:
                    raise ServiceError("mod_target_mismatch", id=record["id"], variant=core_of(target.name))
                result = rebuild(matching, [original], output, self.settings.asset_types(),
                    self.settings.save_options(self.settings.crc_needed(target)), self.settings.spine_options(),
                    match_strategy="cont_name_type", skip_unchanged=True,
                    anim_check=self.settings.anim_check_options(), log=log)
                if not result.success:
                    raise ServiceError("mod_apply_failed", id=record["id"])
                for pair in result.file_pairs:
                    atomic_copy(pair.output, original)
            return staged
        started = time.perf_counter()
        groups = self._plan(record, log, pin)
        allowed = set(targets)
        planned = self._plan_targets(groups)
        if any(path not in allowed for path in planned):
            raise ServiceError("game_file_outside_resources", file=next(p for p in planned if p not in allowed))
        updating = {path for group in groups for path in group.target_paths
                    if any(file.action == Action.UPDATE for file in group.files)}
        staged = self._stage_direct(targets, sources, work / "targets", pin, updating)
        logger.info("Mod apply plan and stage: %.3f s", time.perf_counter() - started)
        started = time.perf_counter()
        # Build only into temporary copies: a part is copied whole or rebuilt; all real game writes go through
        # _commit.
        staged_groups = [replace(group, files=[replace(file,
            existing_path=staged.get(file.existing_path),
            need_crc_fix=self._needs_crc_fix(file.mod_path, file.existing_path)
            if file.existing_path else False) for file in group.files],
            target_paths=[staged[path] for path in group.target_paths]) for group in groups]
        failures = install_staged(
            staged_groups, work / "output", self.settings.asset_types(),
            self.settings.save_options(self.settings.crc_needed(targets[0])),
            self.settings.spine_options(), anim_check=self.settings.anim_check_options(), log=log, progress=(
                lambda current, total, name: progress(current, total, t("webui.progress.process", name=name))
            ) if progress else None)
        logger.info("Mod apply build and CRC: %.3f s", time.perf_counter() - started)
        if failures:
            raise ServiceError("mod_apply_failed", id=record["id"])
        return staged

    def _needs_crc_fix(self, source: Path, target: Path) -> bool:
        if not self.settings.crc_needed(target):
            return False
        expected = parse_filename(target.name).crc
        fingerprint = self._hashes.get(source)
        # Already fixed mods can be copied without loading and recompressing their textures with UnityPy.
        return fingerprint.trailing is None or not expected or fingerprint.crc != int(expected)

    @_serialized
    def apply(self, variant_key: str, source: str, log: LogFunc = no_log, progress: ProgressCallback | None = None,
              kind: Kind | None = "spr", pin: Pin | None = None, *, create_backup: bool = False) -> ApplyResult:
        """Install and configure one kind from a mod, variant, or originals (``_apply_choice``). A memorial lobby camera
        (``lobby_camera``) is a lobby choice: its source's art and its own prefab; any other lobby choice puts the
        variant's prefab back to its base."""
        from . import lobby_camera

        camera = lobby_camera.camera_of(self, source) if kind == "lobby" else None
        if source != "official":
            self._untested_apply(variant_key, source, kind, self._current(pin), camera)
        if camera is not None:
            return lobby_camera.apply(self, variant_key, camera, log, progress, self._current(pin), create_backup)
        result = self._apply_choice(variant_key, source, log, progress, kind, pin, create_backup=create_backup)
        if kind in ("lobby", None) and lobby_camera.cameras(self, variant_key):
            lobby_camera.release(self, variant_key, self._current(pin), log, progress)
        return result

    def _apply_choice(self, variant_key: str, source: str, log: LogFunc = no_log,
                      progress: ProgressCallback | None = None, kind: Kind | None = "spr", pin: Pin | None = None, *,
                      create_backup: bool = False) -> ApplyResult:
        """Install and configure one kind from a mod, variant, or originals.

        None selects all kinds for originals, and both Spine kinds for variant adaptation.

        A mod replaces all parts of its own kind as a unit. Another variant's mod must belong to the same family and
        is adapted to official targets. While mods are off nothing is written: the source is configured and applied
        when mods are on (``deferred``). Save the validated choice before attempting installation.
        """
        if source == "official":
            pin = self._current(pin)
            if not profiles_mod.enabled(self.profiles.read()):
                targets = self._game_files(variant_key, kind, pin)
                kinds = {(core_of(path.name) if kind == "other" else kind_of_file(path.name)): core_of(path.name)
                         for path in targets}
                return self._defer(pin, variant_key, kinds, None)
            return self._restore(variant_key, log, kind, pin=pin)
        if kind == "other":
            return self._apply_other(variant_key, source, log, progress, self._current(pin), create_backup)
        variant = self._variant(variant_key)
        pin = self._current(pin)
        on = profiles_mod.enabled(self.profiles.read())
        started = time.perf_counter()
        kinds = {}
        kept_current: list[str] = []
        incomplete = False
        change = _ChoiceChange(self, pin, variant_key, source)
        try:
            if on:
                self._check_stopping(pin)
            with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                work = Path(temporary)
                build_keys: dict[Path, str] = {}
                if source.startswith("mod:"):
                    record = self._record(source[4:])
                    target_variant = self._target_of(record)
                    if (kind is not None and record["kind"] != kind) or (
                            self._variant(target_variant).family != variant.family):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant_key)
                    targets = self._game_files(variant_key, record["kind"], pin)
                    if not targets:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    cores = [getattr(variant, "model").lower() if record["kind"] == "model"
                             else core_of(targets[0].name)]
                    own_model = record["kind"] == "model" and all(
                        core_of(name) in self._core_map(pin).slot_cores(cores[0]) for name in record["files"])
                    if (record["kind"] == "model" and not own_model) or (
                            record["kind"] == "lobby" and core_of(record["files"][0]) != cores[0]):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant_key)
                    if not on:
                        return self._defer(pin, variant_key, {record["kind"]: cores[0]}, source)
                    kinds = {record["kind"]: cores[0]}
                    incomplete = self._incomplete(record, pin)
                    change.configure(kinds)
                    if own_model or core_of(record["files"][0]) == cores[0]:
                        view = self._view([pin], "full")
                        entry = view.entry(pin, cores[0])
                        identity = self._identity({path.name: path for path in self._paths(record)})
                        if entry.carried == source and self._matches_installed(identity, entry):
                            self._note_judged(view, entry)
                            change.configure({record["kind"]: cores[0]})
                            self._selection_error(pin, kinds.values(), None)
                            return ApplyResult(variant_key, source, [], unchanged=True, incomplete=incomplete)
                        replacements = self._cached_build(self._paths(record), targets, pin,
                            lambda: self._install_direct(record, targets, work, log, progress, pin), build_keys)
                    elif target_variant != variant_key:
                        sources = self._preview(record, pin)
                        replacements = self._cached_build(sources, targets, pin,
                            lambda: self._stage_adapted(sources, targets, work / record["kind"], log, pin), build_keys)
                    else:  # a lobby mod of another core of this variant: not offered, never written there
                        raise ServiceError("game_file_outside_resources", file=targets[0])
                    kinds = {record["kind"]: cores[0]}
                elif source.startswith("variant:") and kind == "model":
                    # another variant's costume put on this variant's model slot (gameassets.model_adapt)
                    other = source[8:]
                    if other == variant_key:
                        raise ServiceError("source_unsupported", source=source)
                    cross = self._costume_cross(variant, other)
                    own = getattr(variant, "model", None)
                    targets = self._game_files(variant_key, "model", pin)
                    if not own or not targets:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    verdict = self._verdicts().get(self._costume_signature(variant_key, other, pin)[0])
                    if not self._costume_allowed(verdict):  # refused: never saved or written
                        raise ServiceError("source_unsupported", source=verdict.get("reason") or source)
                    kinds = {"model": own.lower()}
                    change.configure(kinds)
                    if not on:
                        return self._defer(pin, variant_key, kinds, source)
                    _, inputs = self._costume_source(other, pin)
                    replacements = self._cached_build(inputs, targets, pin,
                        lambda: self._stage_costume(other, targets, work / "model", log, pin, cross=cross), build_keys)
                elif source.startswith("variant:"):
                    other = source[8:]
                    self._variant(other)
                    if kind == "model":
                        raise ServiceError("source_unsupported", source=source)
                    replacements: dict[Path, Path] = {}
                    kinds = {}
                    for current in (("spr", "lobby") if kind is None else (kind,)):
                        targets = self._game_files(variant_key, current, pin)
                        sources = self._game_files(other, current, pin)
                        if not targets or not sources:
                            continue
                        kinds[current] = core_of(targets[0].name)
                    if not kinds:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    change.configure(kinds)
                    if on:
                        for current in kinds:
                            targets = self._game_files(variant_key, current, pin)
                            sources = self._game_files(other, current, pin)
                            official = [self._official_for(path, pin) for path in sources]
                            replacements.update(self._cached_build(official, targets, pin,
                                lambda: self._stage_adapted(official, targets, work / current, log, pin), build_keys))
                    if not kinds:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    if not on:
                        return self._defer(pin, variant_key, kinds, source)
                else:
                    raise ServiceError("source_unsupported", source=source)
                prepared = time.perf_counter()
                files = self._commit(replacements, source, log, progress, pin, prune=False, build_keys=build_keys,
                                     create_backup=create_backup, kept_current=kept_current)
                change.configure(kinds)
                self._selection_error(pin, kinds.values(), None)
                for core in kinds.values():
                    self._clear_marker(pin, core)
                logger.info("Mod apply copy and record: %.3f s", time.perf_counter() - prepared)
                return ApplyResult(variant_key, source, files, unchanged=not files,
                                   kept_current=bool(kept_current), incomplete=incomplete)
        except Exception as error:
            if not change.refused(error) and kinds:
                self._selection_error(pin, kinds.values(), error.to_json() if isinstance(error, ServiceError)
                                      else {"code": "library_io_error" if isinstance(error, OSError) else "library_operation_failed",
                                            "params": {"operation": "apply"}})
            raise
        finally:
            change.close()
            pruning = time.perf_counter()
            self._prune_quietly()
            logger.info("Mod apply prune: %.3f s; total: %.3f s", time.perf_counter() - pruning,
                        time.perf_counter() - started)

    def _untested_apply(self, variant_key: str, source: str, kind: Kind | None, pin: Pin,
                        camera: dict[str, Any] | None) -> None:
        """An apply's confirmation on an untested build (``_check_untested``), before anything is built or configured:
        the targets of each kind it writes, a camera's art and prefab together. Nothing to confirm while mods are off,
        when an apply only configures."""
        if self.untested_version(pin) is None or not profiles_mod.enabled(self.profiles.read()):
            return
        if camera is not None:  # one entry, the lobby, for the art and the prefab it holds
            groups = [(variant_key, "lobby", [*self._game_files(variant_key, "lobby", pin),
                                              *self._game_files(camera["target"], "other", pin)])]
        elif kind == "other":
            groups = [(variant_key, "other", self._game_files(variant_key, "other", pin))]
        else:
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            kinds = (kind,) if kind else ((record["kind"],) if record else ("spr", "lobby"))
            groups = [(variant_key, current, self._game_files(variant_key, current, pin)) for current in kinds]
        self._check_untested([(pin, self._untested_entry(pin, core_of(targets[0].name), targets, variant, current),
                               targets) for variant, current, targets in groups if targets])

    def _defer(self, pin: Pin, variant_key: str, kinds: dict[str, str], source: str | None) -> ApplyResult:
        """While mods are off: configure the source without writing, applied when mods are on."""
        self._configure(pin, variant_key, kinds, source)
        if source is None:
            self._prune_quietly()
        return ApplyResult(variant_key, source or "official", [], unchanged=True, deferred=True)

    def _apply_other(self, target: str, source: str, log: LogFunc, progress: ProgressCallback | None,
                     pin: Pin, create_backup: bool, *, untested: bool = True) -> ApplyResult:
        if not source.startswith("mod:"):
            raise ServiceError("source_unsupported", source=source)
        record = self._record(source[4:])
        if record["kind"] != "other" or record["target"] != target:
            raise ServiceError("mod_target_mismatch", id=record["id"], variant=target)
        targets = self._game_files(target, "other", pin)
        kinds = {core_of(path.name): core_of(path.name) for path in targets}
        if not profiles_mod.enabled(self.profiles.read()):
            return self._defer(pin, target, kinds, source)
        kept_current: list[str] = []
        change = _ChoiceChange(self, pin, target, source)
        try:
            self._check_stopping(pin)
            change.configure(kinds)
            with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                build_keys = {}
                replacements = self._cached_build(self._paths(record), targets, pin,
                    lambda: self._install_direct(record, targets, Path(temporary), log, progress, pin), build_keys)
                files = self._commit(replacements, source, log, progress, pin, prune=False,
                                     build_keys=build_keys, create_backup=create_backup, kept_current=kept_current,
                                     untested=untested)
            self._configure(pin, target, kinds, source)
            self._selection_error(pin, kinds, None)
            for slot in kinds:
                self._clear_marker(pin, slot)
            return ApplyResult(target, source, files, unchanged=not files, kept_current=bool(kept_current))
        except Exception as error:
            if not change.refused(error):
                self._selection_error(pin, kinds, error.to_json() if isinstance(error, ServiceError)
                                      else {"code": "library_io_error" if isinstance(error, OSError) else "library_operation_failed",
                                            "params": {"operation": "apply"}})
            raise
        finally:
            change.close()
            self._prune_quietly()

    @_serialized
    def restore(self, variant_key: str, log: LogFunc = no_log, kind: Kind | None = "spr",
                pin: Pin | None = None, progress: ProgressCallback | None = None) -> ApplyResult:
        """Restore one kind to official bytes and drop its configuration; None restores all kinds. Files that
        are official already are not written (only recorded). A lobby's restore puts its prefab back too, when one of
        its cameras holds it (``lobby_camera.release``)."""
        from . import lobby_camera

        result = self._restore(variant_key, log, kind, pin, progress)
        if kind in ("lobby", None) and not variant_key.startswith(("other:", "other-set:")) and (
                lobby_camera.cameras(self, variant_key)):
            lobby_camera.release(self, variant_key, self._current(pin), log, progress)
        return result

    def _restore(self, variant_key: str, log: LogFunc = no_log, kind: Kind | None = "spr",
                 pin: Pin | None = None, progress: ProgressCallback | None = None) -> ApplyResult:
        pin = self._current(pin)
        self.profiles.read()
        targets = self._game_files(variant_key, kind, pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=variant_key)
        try:
            replacements = {path: self._official_for(path, pin) for path in targets}
            files = self._commit(replacements, "official", log, progress=progress, pin=pin, prune=False)
            kinds = {(core_of(path.name) if kind == "other" else kind_of_file(path.name)): core_of(path.name)
                     for path in targets}
            self._configure(pin, variant_key, kinds, None)
        finally:
            self._prune_quietly()
        return ApplyResult(variant_key, "official", files, unchanged=not files)

    @_serialized
    def would_write(self, variant_key: str, source: str, kind: Kind | None = "spr", pin: Pin | None = None,
                    *, setting: bool = False) -> bool:
        """Whether a setting (setting=True) or explicit restore would write a game file, judged cheaply: not a deferred
        apply while mods are off, nor a restore of files that are official already. Such calls only change the
        configuration (allowed while the classic UI is open)."""
        pin = self._current(pin)
        on = profiles_mod.enabled(self.profiles.read())
        if setting and not on:
            return False
        if source != "official":
            return on
        view = self._view([pin], "family")
        for path in self._game_files(variant_key, kind, pin):
            part = view.part(path, pin, kind_of_file(path.name) or "spr")
            if not part.official:
                return True
        return False

    def _switch(self, pin: Pin, core: str, source: str, log: LogFunc = no_log,
                progress: ProgressCallback | None = None, use_built: bool = True) -> list[Path]:
        """The internal write of an entry: its core's parts in its own install switched to a source, and nothing
        else (the configuration stays). From a built copy when one still fits; a mod made for the core is installed
        directly, another variant's mod adapted, another variant's files (in that same install) adapted."""
        core = self._core_map(pin).slot(core)
        targets = self._spine_parts(core, pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=core)
        kind = self._kind(core, pin) or "spr"
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            work = Path(temporary)
            build_keys: dict[Path, str] = {}
            replacements = None
            if source == "official":
                replacements = {path: self._official_for(path, pin) for path in targets}
            elif use_built:
                replacements = self._built_replacements(pin, core, source, targets, build_keys)
            if replacements is None:
                if source.startswith("mod:"):
                    record = self._record(source[4:])
                    if record["kind"] == "model" and any(
                            core_of(name) not in self._core_map(pin).slot_cores(core) for name in record["files"]):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=core)
                    if record["kind"] in ("other", "model") or core_of(record["files"][0]) == core:
                        replacements = self._cached_build(self._paths(record), targets, pin,
                            lambda: self._install_direct(record, targets, work, log, progress, pin), build_keys)
                    else:
                        sources = self._preview(record, pin)
                        replacements = self._cached_build(sources, targets, pin,
                            lambda: self._stage_adapted(sources, targets, work / kind, log, pin), build_keys)
                elif source.startswith("variant:") and kind == "model":
                    _, inputs = self._costume_source(source[8:], pin)
                    try:
                        owner = self._core_map(pin).key(core)
                        slot = self._variant(owner) if owner else None
                    except ServiceError:
                        slot = None  # a slot of no known variant: built as before, the same student's rule
                    cross = self._costume_cross(slot, source[8:])
                    replacements = self._cached_build(inputs, targets, pin,
                        lambda: self._stage_costume(source[8:], targets, work / kind, log, pin, cross=cross),
                        build_keys)
                elif source.startswith("variant:"):
                    other = self._core_map(pin).core(source[8:], kind)
                    sources = self._index(pin).get(other.lower(), []) if other else []
                    if not sources:
                        raise ServiceError("game_file_not_found", variant=source[8:])
                    official = [self._official_for(path, pin) for path in sources]
                    replacements = self._cached_build(official, targets, pin,
                        lambda: self._stage_adapted(official, targets, work / kind, log, pin), build_keys)
                else:
                    raise ServiceError("source_unsupported", source=source)
            files = self._commit(replacements, source, log, progress, pin, prune=False, build_keys=build_keys)
        self._clear_marker(pin, core)
        return files

    # --- built copies ---

    def _build_signature(self, sources: list[Path], targets: list[Path], pin: Pin, version: str | None = None) -> str:
        """What a build is made from: the program (or ``version``, a rule's own), the bytes of its inputs, the official
        bytes of its targets and the options. A game file written from it records this as its ``build``."""
        inputs = [(path.name, self._hashes.get(path).sha1) for path in sources]
        originals = [(path.name, self._hashes.get(self._official_for(path, pin)).sha1) for path in targets]
        options = (sorted(self.settings.asset_types()), self.settings.save_options(self.settings.crc_needed(targets[0])),
                   self.settings.spine_options(), self.settings.anim_check_key())
        return hashlib.sha1(repr((1, version or _build_version(), inputs, originals, options)).encode()).hexdigest()

    def _cached_build(self, sources: list[Path], targets: list[Path], pin: Pin,
                      build: Callable[[], dict[Path, Path]], build_keys: dict[Path, str]) -> dict[Path, Path]:
        """Reuse only a referenced instant-On copy with the same program, inputs, official targets and options.
        Prepared files otherwise live solely in the caller's temporary staging directory."""
        if len({path.name for path in targets}) != len(targets):
            return build()  # built keys identify parts by file name
        try:
            signature = self._build_signature(sources, targets, pin)
        except ServiceError as error:
            if error.code != "original_unavailable":
                raise
            return build()  # No official baseline to sign; never cache a live fallback as official.
        build_keys.update((target, signature) for target in targets)
        for folder in ((self.profiles.read() or {}).get("folders") or {}).values():
            for core, key in (folder.get("built") or {}).items():
                configured = (folder.get("configured") or {}).get(core) or {}
                if key.get("build") != signature or key.get("source") != configured.get("source"):
                    continue
                parts = key.get("parts") or {}
                if not parts or not set(parts) <= {target.name for target in targets}:
                    continue
                try:
                    replacements = {}
                    for target in targets:
                        part = parts.get(target.name)
                        if part is None:
                            replacements[target] = self._official_or_current(target, pin)
                            continue
                        kept = self.root / "built" / f"{part['sha1']}.bundle"
                        if part.get("mod_file") and key["source"].startswith("mod:"):
                            kept = self.root / "mods" / key["source"][4:] / part["mod_file"]
                        if self._hashes.get(kept).sha1 != part["sha1"]:
                            raise ValueError("Changed built copy")
                        replacements[target] = kept
                    logger.debug("Reused configured build %s", signature)
                    return replacements
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        start = time.perf_counter()
        replacements = build()
        logger.debug("Built %s in %.3f s", signature, time.perf_counter() - start)
        return replacements

    def _built_replacements(self, pin: Pin, core: str, source: str, targets: list[Path],
                            build_keys: dict[Path, str] | None = None) -> dict[Path, Path] | None:
        """The entry's built copy written back, when it still fits: built for this very source, every part's name and
        every input's name still in the install. The other parts take their official copies. A kept file whose bytes
        are not the recorded ones drops the key (the entry is rebuilt)."""
        folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
        key = (folder.get("built") or {}).get(core)
        if not key:
            return None
        names = {path.name for paths in self._index(pin).values() for path in paths}
        parts = key.get("parts") or {}
        fits = key.get("source") == source and parts and all(name in names for name in parts) and all(
            name in names for name in key.get("inputs") or [])
        replacements: dict[Path, Path] = {}
        if fits:
            for target in targets:
                part = parts.get(target.name)
                if part is None:
                    replacements[target] = self._official_or_current(target, pin)
                    continue
                kept = (self.root / "mods" / source[4:] / part["mod_file"]) if part.get("mod_file") and \
                    source.startswith("mod:") else self.root / "built" / f"{part.get('sha1')}.bundle"
                try:
                    if self._hashes.get(kept).sha1 != part.get("sha1"):
                        raise ValueError(kept)
                except (OSError, ValueError):
                    logger.info("The built copy of %s no longer fits, rebuilding", target.name)
                    fits = False
                    break
                replacements[target] = kept
        if fits:
            if build_keys is not None and key.get("build"):
                build_keys.update((target, key["build"]) for target in targets)
            return replacements
        self._drop_built(pin, [core])
        return None

    def _current_builds(self, pin: Pin, core: str, record: dict[str, Any], targets: list[Path]) -> set[str]:
        """The builds a write of a mod over an entry's game files signs now, as apply and switch sign them
        (``_cached_build``); those that cannot be told are left out."""
        def sign(sources: Callable[[], list[Path]], group: Callable[[], list[Path]]) -> str | None:
            try:
                files = group()
                if not files or len({path.name for path in files}) != len(files):
                    return None  # built keys identify parts by file name: such a write signs nothing
                return self._build_signature(sources(), files, pin)
            except (OSError, ServiceError):
                return None

        if record["kind"] == "other":
            # a switch signs one slot's files, an apply the files of the whole mod: either may have written a part
            signatures = {sign(lambda: self._paths(record), lambda: targets),
                          sign(lambda: self._paths(record), lambda: self._game_files(record["target"], "other", pin))}
        elif record["kind"] == "model" or core_of(record["files"][0]) == core:
            signatures = {sign(lambda: self._paths(record), lambda: targets)}
        else:
            signatures = {sign(lambda: self._preview(record, pin), lambda: targets)}
        signatures.discard(None)
        return signatures

    def _make_built(self, pin: Pin, entry: _Entry) -> None:
        """Keep the exact bytes of an entry's modded parts before an Off replaces them, whole: every non-official part
        of one write of its configured source, as a file of the mod in mods/<id>/ when one holds those bytes, else a
        copy in built/<sha1>.bundle. The key replaces any earlier one. Nothing is kept when a part of a mod was not
        written from the mod's current files (the mod was updated since): a key naming only some parts would put
        official bytes over the others, and the old build must not come back. The entry is built again by the On."""
        source = entry.configured
        parts: dict[str, dict[str, Any]] = {}
        mod_files: dict[str, str] = {}
        record = self._records.get(source[4:]) if source.startswith("mod:") else None
        if record is not None:
            for path in self._paths(record):
                with contextlib.suppress(OSError):
                    mod_files[self._hashes.get(path).sha1] = path.name
        modded = [part for part in entry.parts if not part.official and part.record]
        current: set[str] | None = None
        for part in modded:
            if record is None or part.record["sha1"] in mod_files:
                continue
            if current is None:
                current = self._current_builds(pin, entry.core, record, [part.path for part in entry.parts])
            if part.record.get("build") not in current:
                logger.info("Not keeping the built copy of %s: %s was not written from the current %s", entry.core,
                            part.path.name, source)
                return
        for part in modded:
            sha1 = part.record["sha1"]
            if sha1 in mod_files:
                parts[part.path.name] = {"sha1": sha1, "mod_file": mod_files[sha1]}
                continue
            built = self.root / "built" / f"{sha1}.bundle"
            if not built.exists():
                atomic_copy(part.path, built)
            parts[part.path.name] = {"sha1": sha1, "mod_file": None}
        if not parts:
            return
        inputs = self._inputs(pin, entry.core, source)
        key = {"source": source, "inputs": inputs, "parts": parts}
        signatures = {(part.record or {}).get("build") for part in entry.parts}
        if len(signatures) == 1 and None not in signatures:
            key["build"] = signatures.pop()
        replaced: list[dict] = []

        def change(data: dict) -> None:
            built = self._folder(data, pin)["built"]
            if entry.core in built:
                replaced.append(built[entry.core])
            built[entry.core] = key

        self._update_profiles(change, after=lambda data: self._delete_built_files(replaced, data))
        # another instance may have dropped the last reference before the new key was saved
        recovered = False
        for part in entry.parts:
            kept = parts.get(part.path.name)
            if kept is not None and kept["mod_file"] is None:
                built = self.root / "built" / f"{kept['sha1']}.bundle"
                if not built.exists():
                    atomic_copy(part.path, built)
                    recovered = True
        if recovered:
            self._update_profiles(lambda data: False, after=lambda data: self._delete_built_files([key], data))

    def _inputs(self, pin: Pin, core: str, source: str) -> list[str]:
        """The other game files a build of a source reads: an adapted mod's official parts of its own core, another
        variant's files of the kind; none for a mod made for the core."""
        index = self._index(pin)
        if source.startswith("mod:") and source[4:] in self._records:
            record = self._records[source[4:]]
            own = core_of(record["files"][0])
            if record["kind"] == "model" or own == core:
                return []
            targets = index.get(own, [])
            replaced = self._replaced_parts({path.name: path for path in self._paths(record)}, targets, pin)
            return sorted(path.name for path in targets if path not in replaced)
        if source.startswith("variant:") and self._kind(core, pin) == "model":
            try:
                return sorted(path.name for path in self._costume_files(source[8:], pin)[2])
            except ServiceError:
                return []
        if source.startswith("variant:"):
            other = self._core_map(pin).core(source[8:], self._kind(core, pin) or "spr")
            return sorted(path.name for path in index.get(other.lower(), [])) if other else []
        return []

    def _drop_built(self, pin_or_key: Pin | str, cores: list[str] | None = None,
                    when: Callable[[dict], bool] | None = None) -> None:
        """Drop built keys of an install (the given cores, or those ``when`` picks; with neither, all), deleting the
        built/ files no other key references."""
        target = pin_or_key.key if isinstance(pin_or_key, Pin) else pin_or_key
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            folder = data["folders"].get(target)
            if not folder:
                return False
            for core in list(folder["built"]):
                if (cores is not None and core not in cores) or (when is not None and not when(folder["built"][core])):
                    continue
                dropped.append(folder["built"].pop(core))
            return bool(dropped)

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    def _delete_built_files(self, keys: list[dict], data: dict | None) -> None:
        """Delete dropped keys' files no key references, under the profiles lock, against the saved configuration."""
        if not keys:
            return
        # a part kept as a file of its mod reads mods/<id>/, so only the others hold their built/ file
        referenced = {part.get("sha1") for folder in ((data or {}).get("folders") or {}).values()
                      for key in (folder.get("built") or {}).values()
                      for part in (key.get("parts") or {}).values() if not part.get("mod_file")}
        for key in keys:
            for part in (key.get("parts") or {}).values():
                sha1 = part.get("sha1")
                if part.get("mod_file") or not sha1 or sha1 in referenced:
                    continue
                path = self.root / "built" / f"{sha1}.bundle"
                if path.resolve().is_relative_to(self.root) and path.parent == self.root / "built":
                    deletion = getattr(self, '_asset_deletion', None)
                    if deletion:
                        deletion.remember_built(path)
                        path.unlink(missing_ok=True)
                    else:
                        with contextlib.suppress(OSError):
                            path.unlink()

    @_serialized
    def clear_built(self) -> int:
        """Clear Built Mods: delete built/*.bundle, stale temporaries and every built key with a part kept there.
        Keys whose parts are all files of their mod stay: they use no disk. The kept originals stay (Off needs
        them). Returns the bytes freed."""
        directory = self.root / "built"
        freed = _folder_size(directory)

        def change(data: dict) -> bool:
            changed = False
            for folder in data["folders"].values():
                for core in list(folder["built"]):
                    if any(not part.get("mod_file") for part in (folder["built"][core].get("parts") or {}).values()):
                        del folder["built"][core]
                        changed = True
            return changed

        def after(data: dict) -> None:
            nonlocal freed
            for path in directory.glob("*.bundle"):
                with contextlib.suppress(OSError):
                    path.unlink()
            previews = directory / "previews"
            if previews.is_dir():
                for part in previews.rglob("*.bundle"):
                    with contextlib.suppress(OSError):
                        freed += part.stat().st_size
                with contextlib.suppress(OSError):
                    remove_tree(previews)
            cutoff = time.time() - BUILT_TEMP_MAX_AGE
            for path in directory.glob(".library-*"):
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    if stat.st_mtime <= cutoff:
                        path.unlink()
                        freed += stat.st_size

        self._update_profiles(change, after=after)
        return freed

    # --- the configuration ---

    def _update_profiles(self, change: Callable[[dict], Any], after: Callable[[dict], Any] | None = None) -> Any:
        """One change of profiles.json, then ``after`` under its lock. Neither callback calls into Profiles or the
        library's public methods. Returns what the change returned when it is not a bool, else the configuration
        as written."""
        result: list[Any] = []

        def wrapped(data: dict) -> Any:
            value = change(data)
            result.append(value)
            return value

        data = self.profiles.update(wrapped, after=after)
        value = result[0] if result else None
        return data if value is None or isinstance(value, bool) else value

    def _folder(self, data: dict, pin: Pin) -> dict:
        """The install's record in a configuration, created when missing (with this data directory's id in
        named_by when its settings name the install)."""
        folders = data["folders"]
        folder = folders.get(pin.key)
        if folder is None:
            folder = folders[pin.key] = {"server": pin.server, "named_by": {}, "hidden": [], "configured": {},
                                          "updated": {}, "built": {}, "dismissed": {}}
            if any(named.key == pin.key for named in self._named_pins()):
                folder["named_by"][self.data_id] = now()
        return folder

    def _configure(self, pin: Pin, variant_key: str, kinds: dict[str, str], source: str | None, *,
                   dropped: list[dict] | None = None) -> None:
        """Set (or, with None, drop) the configured source of an entry's cores. A changed or dropped configuration
        drops the entry's built copy and its updated marker. With ``dropped``, the dropped built keys go there and
        their files stay until the caller deletes them (``_ChoiceChange.close``), else they are deleted at once."""
        kinds = {kind: self._core_map(pin).slot(core) if kind == "model" else core for kind, core in kinds.items()}
        stamp = now()
        dropped_keys: list[dict] = []
        named = any(other.key == pin.key for other in self._named_pins())

        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            if folder is None:
                if source is None:
                    return False
                folder = self._new_folder(data, pin, named)
            changed = False
            for kind, core in kinds.items():
                if core.startswith("other:"):
                    kind = "other"
                current = folder["configured"].get(core)
                if source is None:
                    if current is None:
                        continue
                    del folder["configured"][core]
                elif current and current.get("source") == source:
                    continue
                else:
                    folder["configured"][core] = {"variant": variant_key, "kind": kind, "source": source,
                                                  "since": stamp}
                changed = True
                folder["updated"].pop(core, None)
                if core in folder["built"]:
                    dropped_keys.append(folder["built"].pop(core))
            return changed

        if dropped is not None:
            self._update_profiles(change)
            dropped.extend(dropped_keys)
            return
        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped_keys, data))

    def _check_stopping(self, pin: Pin) -> None:
        """The refusals of the moment that the write-time checks (``_writable``) would give whatever the files: the
        classic UI open, Steam changing the install, the game running. Checked before a choice is saved, so a write
        refused for one of them (STOPPING) leaves the choice as it was and costs no build."""
        if self._write_check is not None:
            self._write_check(pin)
        self._check_idle([pin])
        safety.check_game_for(list(pin.dirs))

    def _saved_choices(self, pin: Pin, kinds: dict[str, str]) -> dict[str, dict[str, Any]]:
        """The saved entries of an entry's cores (configured, updated marker, built key), as ``_put_back_choices``
        restores them."""
        folder = (((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {})
        cores = [self._core_map(pin).slot(core) if kind == "model" else core for kind, core in kinds.items()]
        return {core: {part: copy.deepcopy((folder.get(part) or {}).get(core)) for part in ("configured", "updated", "built")}
                for core in cores}

    def _put_back_choices(self, pin: Pin, saved: dict[str, dict[str, Any]]) -> None:
        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            if folder is None:
                return False
            for core, parts in saved.items():
                for part, value in parts.items():
                    if value is None:
                        folder.setdefault(part, {}).pop(core, None)
                    else:
                        folder.setdefault(part, {})[core] = copy.deepcopy(value)
            return True
        self._update_profiles(change)

    def _new_folder(self, data: dict, pin: Pin, named: bool) -> dict:
        folder = data["folders"][pin.key] = {"server": pin.server, "named_by": {}, "hidden": [], "configured": {},
                                             "updated": {}, "built": {}, "dismissed": {}}
        if named:
            folder["named_by"][self.data_id] = now()
        return folder

    def _clear_marker(self, pin: Pin, core: str) -> None:
        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            return bool(folder) and folder["updated"].pop(core, None) is not None

        with contextlib.suppress(ServiceError):
            if core in ((((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}).get("updated") or {}):
                self._update_profiles(change)

    def _lookup_records(self) -> None:
        """At each build: find each named install's record (its own; a moved install's, re-keyed; else one migrated
        from what the library already wrote there), then refresh named_by and hidden of the records that exist. The
        scan consent is asked before profiles.json is locked."""
        config = self.profiles.read()
        allowed = self._scan_allowed()
        named = self._named_pins()
        folders = (config or {}).get("folders") or {}
        moves: dict[str, str] = {}
        migrations: dict[str, dict[str, dict]] = {}
        for pin in named:
            if pin.key in folders:
                continue
            gone = [key for key, folder in folders.items() if key not in moves.values()
                    and (folder.get("server") or "global") == pin.server and not Path(key).exists()
                    and Path(key).parent.is_dir()]
            if len(gone) == 1:
                moves[pin.key] = gone[0]
                continue
            migration = self._migration(pin, config)
            if migration:
                migrations[pin.key] = migration
        for new, old in moves.items():
            self._rekey(Path(old), next(pin for pin in named if pin.key == new), allowed)
        if not folders and not migrations:
            return
        named_keys = {pin.key for pin in named}
        pins = {pin.key: pin for pin in named}
        stamp = now()

        def change(data: dict) -> None:
            for new, old in moves.items():
                if new in data["folders"] or old not in data["folders"]:
                    continue
                data["folders"][new] = data["folders"].pop(old)
                for item in data["unfinished"]:
                    if item["install"] == old:
                        item["install"] = new
                logger.info("Moved the mod configuration of %s to %s", old, new)
            for key, configured in migrations.items():
                if key in data["folders"]:
                    continue
                folder = self._new_folder(data, pins[key], True)
                folder["configured"] = configured
                logger.info("Configured %d entries of %s from what the library wrote there", len(configured), key)
            for key, folder in data["folders"].items():
                if key in named_keys:
                    folder["named_by"][self.data_id] = stamp
                    if self.data_id in folder["hidden"]:
                        folder["hidden"].remove(self.data_id)
                else:
                    folder["named_by"].pop(self.data_id, None)

        self.profiles.update(change)

    def _rekey(self, old_root: Path, pin: Pin, allowed: bool) -> None:
        """A moved install: its records follow to the same relative paths where the file matches, by size and date,
        or (with scan consent: the new paths are not the library's yet) by size and last bytes. Nothing is hashed."""
        moved = 0
        for name, record in list(self._applied.items()):
            path = Path(name)
            if not path.is_relative_to(old_root):
                continue
            new = pin.root / path.relative_to(old_root)
            try:
                stat = new.stat()
            except OSError:
                continue
            match = stat.st_size == record.get("size") and stat.st_mtime_ns == record.get("mtime")
            if not match and allowed and record.get("tail") and stat.st_size == record.get("size"):
                match = read_tail(new) == record["tail"]
            if match:
                del self._applied[name]
                self._applied[str(new.resolve())] = {**record, "mtime": stat.st_mtime_ns}
                moved += 1
        if moved:
            self._save()
            logger.info("Moved %d records of %s to %s", moved, old_root, pin.root)

    def _migration(self, pin: Pin, config: dict | None) -> dict[str, dict]:
        """The configuration of an install without a record, from what the library already wrote there (recorded
        paths and names only): every entry whose non-official parts all carry one library source, and every entry
        whose present parts carry none while its gone records (renamed by a game update) all say non-official bytes
        of one source."""
        view = _View(self, [pin], config, "recorded")
        stamp = now()
        configured: dict[str, dict] = {}
        by_core: dict[str, list[tuple[Path, dict]]] = {}
        for folder in pin.dirs:
            for path, record in view.by_folder.get(folder, []):
                core = view.maps[pin.key].slot(core_of(path.name))
                if kind_of_file(path.name):
                    by_core.setdefault(core, []).append((path, record))
        for core, records in sorted(by_core.items()):
            kind = self._kind(core, pin)
            parts = []
            for path in self._spine_parts(core, pin):
                if str(path.resolve()) in view.applied:
                    with contextlib.suppress(OSError, _Unread):
                        parts.append(view.part(path, pin, kind))
            nonofficial = [part for part in parts if not part.official]
            sources = {part.carried for part in nonofficial}
            source = None
            if nonofficial:
                if len(sources) == 1 and next(iter(sources)).startswith(LIBRARY_SOURCES):
                    source = next(iter(sources))
            else:
                gone = [(path, record) for path, record in records
                        if not path.exists() and path.parent.is_dir() and view.successor(pin, path)]
                gone_sources = {record.get("source") for path, record in gone
                                if view.says_nonofficial(path, record, present=False)}
                if gone and len(gone_sources) == 1 and all(
                        view.says_nonofficial(path, record, present=False) for path, record in gone):
                    candidate = next(iter(gone_sources))
                    if candidate and candidate.startswith(LIBRARY_SOURCES) and view.source_exists(
                            candidate, pin.key, kind):
                        source = candidate
            if source is None:
                # Older state kept only applied records. Preserve that choice after an
                # external reset too; selection() separately verifies installation.
                remembered = {record.get("source") for path, record in records
                              if view.says_nonofficial(path, record, present=path.exists())}
                if len(remembered) == 1:
                    candidate = next(iter(remembered))
                    if candidate and candidate.startswith(LIBRARY_SOURCES) and view.source_exists(candidate, pin.key, kind):
                        source = candidate
            if source:
                configured[core] = {"variant": view.maps[pin.key].key(core) or core, "kind": kind,
                                    "source": source, "since": stamp}
        return configured

    # --- entries and the scan ---

    def _entry_state(self, view: _View, entry: _Entry) -> EntryState:
        names = None
        if self.catalog.variant_for_core(entry.core) is None:
            names = view.maps[entry.pin.key].names.get(entry.variant)
        unknown = [part for part in entry.parts if part.carried == "unknown"]
        return EntryState(entry.pin.key, entry.core, entry.variant, names, entry.kind, entry.carried,
                          entry.configured, entry.state, entry.overwritten,
                          any(part.trailing for part in unknown), sum(part.size for part in unknown),
                          [self._file_state(view, entry, part) for part in entry.parts], entry.dismissed)

    def entries(self, pins: list[Pin] | None = None, include_dismissed: bool = False) -> list[EntryState]:
        """The entries of the pinned installs (default: every known one) that are configured, untracked, damaged,
        overwritten or not in their intended state. It reads headers of files the library did not write: it needs
        the scan consent (``scan_off``)."""
        return self.scan(pins, include_dismissed=include_dismissed).entries

    def scan(self, pins: list[Pin] | None = None, cancel: Callable[[], bool] | None = None,
             include_dismissed: bool = False, cores: set[str] | None = None) -> ScanResult:
        """The scan: the entries of every known install, judged cheaply (headers and the last bytes of recorded
        files; no file is read in full), with counts per install. Only with scan consent (``scan_off``). The records
        are copied under the library lock and the files read outside it; when the library wrote meanwhile, the
        result is ``stale`` and carries nothing. Take-overs and dropped configurations of sources gone from the
        library are saved."""
        if not self._scan_allowed():
            raise ServiceError("scan_off")
        # the kept originals Steam's list names and no verdict covers yet, hashed once each (BL-194)
        with contextlib.suppress(OSError, ServiceError):
            self.check_originals(cancel)
        with self._lock, _errors("scan"):
            pins = list(pins) if pins is not None else self.installs()
            view = self._view(pins, "scan", snapshot=True)
            version = self._version
        found = view.entries(pins, cancel, cores, background=True)
        with self._lock, _errors("scan"):
            if self._version != version:
                return ScanResult([], {}, stale=True)
            self._settle(view, found)
            result = [self._entry_state(view, entry) for entry in found]
        if include_dismissed:
            result = [replace(entry, dismissed=False) for entry in result]
        counts: dict[str, dict[str, int]] = {}
        for entry in result:
            count = counts.setdefault(entry.install, {})
            name = "stuck" if entry.state == "pending" and entry.overwritten else entry.state
            count[name] = count.get(name, 0) + 1
            if entry.configured:
                count["configured"] = count.get("configured", 0) + 1
        return ScanResult(result, counts)

    def _settle(self, view: _View, found: list[_Entry]) -> None:
        """What the judgement settles in the configuration: take-overs (a newer library write than the configured
        source, or a library source with none) and sources gone from the library (dropped once listed); and the new
        dates of files the library knows by their last bytes."""
        if self._writes_refused():
            return  # a recovery that has failed refuses changes: the next scan settles
        restamps = {str(part.path.resolve()): part.restamp for entry in found for part in entry.parts
                    if part.restamp is not None and part.record is not None}
        if restamps:
            for path, mtime in restamps.items():
                if path in self._applied:
                    logger.info("Same bytes, new date: %s", Path(path).name)
                    self._applied[path]["mtime"] = mtime
            with contextlib.suppress(OSError, ServiceError):
                self._save()
        takeovers = [entry for entry in found if entry.takeover]
        missing = [entry for entry in found if entry.state == "source_missing"]
        if not takeovers and not missing:
            return
        stamp = now()
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            changed = False
            for entry in takeovers:
                folder = self._folder(data, entry.pin)
                current = (folder["configured"].get(entry.core) or {}).get("source")
                expected = (view.configured(entry.pin.key, entry.core) or {}).get("source")
                if current != expected:
                    continue
                logger.info("Configured %s in %s with %s (taken over)", entry.core, entry.pin.key, entry.takeover)
                folder["configured"][entry.core] = {"variant": entry.variant, "kind": entry.kind,
                                                    "source": entry.takeover, "since": stamp}
                if entry.core in folder["built"]:
                    dropped.append(folder["built"].pop(entry.core))
                changed = True
            for entry in missing:
                folder = data["folders"].get(entry.pin.key)
                if folder and entry.core in folder["configured"]:
                    logger.info("Dropped the configuration of %s in %s: %s is gone from the library", entry.core,
                                entry.pin.key, entry.configured)
                    del folder["configured"][entry.core]
                    folder["updated"].pop(entry.core, None)
                    if entry.core in folder["built"]:
                        dropped.append(folder["built"].pop(entry.core))
                    changed = True
            return changed

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    # --- adopting mods already in use ---

    def adopt(self, entries: list[dict], pins: list[Pin] | None = None, log: LogFunc = no_log,
              progress: ProgressCallback | None = None, cancel: Callable[[], bool] | None = None) -> AdoptResult:
        """Add mods already in use to the library (a writer job of the library only): each given entry ({install,
        core}) that is still untracked or changed is stored as one mod of its non-official parts, every part is
        recorded, the entry configured with it and its built copy kept as the mod's own files. Official copies are
        kept in originals/ now, while they exist. ``not_enough_space`` when it does not fit with 512 MiB to spare."""
        with self._lock, _errors("adopt"):
            pins = list(pins) if pins is not None else self.installs()
            self._check_pins(pins)
            by_key = {pin.key: pin for pin in pins}
            wanted = [(by_key[item["install"]], item["core"].lower()) for item in entries
                      if isinstance(item, dict) and item.get("install") in by_key and isinstance(item.get("core"), str)]
            view = self._view(pins, "family")
            need = 0
            for pin, core in wanted:
                with contextlib.suppress(OSError, _Unread):
                    entry = view.entry(pin, core)
                    need += sum(part.size for part in entry.parts if part.carried == "unknown")
                    need += sum(part.size for part in entry.parts if not part.official
                                and entry.copies.get(str(part.path)) in ("backup", "elsewhere"))
            self._check_space(need)
        added, failed, skipped, cores, kinds = [], [], [], set(), set()
        for i, (pin, core) in enumerate(wanted, 1):
            if cancel and cancel():
                break
            with self._lock:
                try:
                    with _errors("adopt"):
                        mod_id = self._adopt_one(pin, core)
                except ServiceError as error:
                    if error.code == "entry_changed":
                        skipped.append({"install": pin.key, "core": core, "why": "entry_changed"})
                    else:
                        failed.append({"entry": {"install": pin.key, "core": core}, "code": error.code,
                                       "params": error.to_json()["params"]})
                    continue
                added.append(self._entry(self._records[mod_id]))
                cores.add(core)
                kinds.add(self._kind(core, pin) or "spr")
            if progress:
                progress(i, len(wanted), t("webui.progress.add", name=core))
        return AdoptResult(added, failed, skipped, sorted(cores), sorted(kinds))

    def _check_space(self, need: int) -> None:
        if need <= 0:
            return
        folder = self.root
        while not folder.exists() and folder.parent != folder:
            folder = folder.parent
        free = shutil.disk_usage(folder).free
        if need + SPACE_MARGIN > free:
            raise ServiceError("not_enough_space", need=need, free=free)

    @_mod_writer
    def _adopt_one(self, pin: Pin, core: str, mod_id: str | None = None) -> str:
        """Adopt one entry, under the library lock: its files read in full (they are copied anyway). Rolled back as a
        whole on failure; a mod already in the library stays as it was."""
        view = self._view([pin, *[p for p in self.installs() if p.key != pin.key]], "full")
        entry = view.entry(pin, core)
        core = entry.core
        if entry.state not in ("untracked", "changed"):
            raise ServiceError("entry_changed", core=core)
        nonofficial = [part for part in entry.parts if not part.official]
        if not nonofficial:
            raise ServiceError("entry_changed", core=core)
        old_applied = copy.deepcopy(self._applied)
        if mod_id is None:
            mod_id = next((record["id"] for record in self._records.values()
                           if self._matches_installed(self._identity({path.name: path for path in self._paths(record)}),
                                                      entry)), None)
        if mod_id is None:
            mod_id, created = self._add_group([part.path for part in nonofficial],
                                              parse_filename(nonofficial[0].path.name).core, pin)
        else:
            record = self._record(mod_id)
            if not self._matches_installed(self._identity({path.name: path for path in self._paths(record)}), entry):
                raise ServiceError("entry_changed", core=core)
            created = False
        source = f"mod:{mod_id}"
        try:
            for part in nonofficial:
                with contextlib.suppress(ServiceError):  # overwritten: no official copy to keep
                    self._keep_original(part.path, pin)
            stamp = now()
            for part in entry.parts:
                fingerprint = self._hashes.get(part.path)
                stat = part.path.stat()
                self._applied[str(part.path.resolve())] = {
                    "source": source, "sha1": fingerprint.sha1, "size": stat.st_size, "mtime": stat.st_mtime_ns,
                    "official": part.official, "at": stamp,
                    "tail": read_tail(part.path) if fingerprint.trailing else None}
            self._save()
            files = {self._hashes.get(path).sha1: path.name for path in self._paths(self._records[mod_id])}
            built = {"source": source, "inputs": [],
                     "parts": {part.path.name: {"sha1": self._hashes.get(part.path).sha1,
                                                "mod_file": files.get(self._hashes.get(part.path).sha1)}
                               for part in nonofficial}}
            variant = entry.variant
            named = any(other.key == pin.key for other in self._named_pins())
            replaced: list[dict] = []

            def change(data: dict) -> None:
                folder = data["folders"].get(pin.key) or self._new_folder(data, pin, named)
                folder["configured"][core] = {"variant": variant, "kind": entry.kind, "source": source,
                                              "since": stamp}
                folder["updated"].pop(core, None)
                if core in folder["built"]:
                    replaced.append(folder["built"][core])
                folder["built"][core] = built

            self._update_profiles(change, after=lambda data: self._delete_built_files(replaced, data))
        except Exception:
            self._applied = old_applied
            with contextlib.suppress(OSError, ServiceError):
                self._save()
            if created:
                self._drop_mods([mod_id])
            raise
        logger.info("Added %s of %s to the library as mod %s", core, pin.key, mod_id)
        return mod_id

    # --- the Enable Mods switch ---

    def intent(self) -> dict[str, Any]:
        """The switch's shared intent: {enabled, toggled, unfinished: {pending, left, stuck, held}, held: [{install,
        core, variant, kind, source, label}]} over this data directory's known installs (``label``: the configured
        mod's name, while it is in the library); ``profiles_unreadable`` raised as is."""
        config = self.profiles.read()
        keys = {pin.key for pin in self.installs()}
        counts = {why: 0 for why in profiles_mod.WHY}
        held = []
        for item in (config or {}).get("unfinished", []):
            if item["install"] in keys:
                counts[item["why"]] += 1
                if item["why"] == "held":
                    entry = ((((config or {}).get("folders") or {}).get(item["install"]) or {}).get("configured")
                             or {}).get(item["core"]) or {}
                    source = entry.get("source")
                    record = (self._records.get(source[4:])
                              if isinstance(source, str) and source.startswith("mod:") else None)
                    held.append({"install": item["install"], "core": item["core"], "variant": entry.get("variant"),
                                 "kind": entry.get("kind"), "source": source,
                                 **({"label": self._label(record)} if record else {})})
        return {"enabled": profiles_mod.enabled(config), "toggled": (config or {}).get("toggled"),
                "unfinished": counts, "held": held}

    def status(self) -> dict[str, Any]:
        """What the switch's snapshot needs from the library, from names and profiles.json only: the known
        installs, the current one, the intent, the configured count, and ``reason`` (profiles_unreadable or None).
        Never takes the library lock or walks the storage folders. Each install's
        ``distinct`` is the shortest trailing part of its path that tells it from the other installs of its server
        (for the install label, "Global (BlueArchive)"), None when it is the only one of its server."""
        pins = self.installs()
        named = {pin.key for pin in self._named_pins()}
        current = self.current_pin()
        servers: dict[str, dict[str, Path]] = {}
        for pin in pins:
            servers.setdefault(pin.server, {})[pin.key] = pin.root
        distinct: dict[str, str] = {}
        for roots in servers.values():
            if len(roots) > 1:
                distinct.update(distinguishing_folders(roots))
        out: dict[str, Any] = {
            "installs": [{"id": pin.id, "key": pin.key, "server": pin.server, "folder": str(pin.root),
                          "named": pin.key in named, "distinct": distinct.get(pin.key)} for pin in pins],
            "current": current.key if current else None,
            "reason": None,
        }
        try:
            config = self.profiles.read()
            out.update(self.intent())
        except ServiceError as error:
            out.update(enabled=True, toggled=None, unfinished={why: 0 for why in profiles_mod.WHY}, held=[],
                       reason=error.code, file=error.params.get("file"))
            config = None
        keys = {pin.key for pin in pins}
        out["configured"] = sum(len(folder.get("configured") or {}) for key, folder in
                                ((config or {}).get("folders") or {}).items() if key in keys)
        return out

    def sizes(self, kind: str) -> dict[str, int]:
        """Count stored bundles only for the size a help popup or Settings row is showing."""
        if kind not in ("built", "originals"):
            raise ServiceError("bad_params", key="kind")
        return {f"{kind}Size": _folder_size(self.root / kind)}

    def _plan_toggle(self, enabled: bool, pins: list[Pin], config: dict | None,
                     found: list[_Entry]) -> list[tuple[Pin, str]]:
        """The entries a toggle reaches, in every known install: for an Off with scan consent, every entry its own
        pass over the files found (``found``), and for any Off the configured entries and those with records. For an
        On, the configured entries, those with a record of a removed mod, and those the unfinished list holds."""
        plan: list[tuple[Pin, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(pin: Pin, core: str) -> None:
            core = self._core_map(pin).slot(core)
            if (pin.key, core) not in seen and self._kind(core, pin):
                seen.add((pin.key, core))
                plan.append((pin, core))

        folders = (config or {}).get("folders") or {}
        for entry in found:
            add(entry.pin, entry.core)
        for pin in pins:
            folder = folders.get(pin.key) or {}
            for core in sorted(folder.get("configured") or {}):
                add(pin, core)
            for name, record in sorted(self._applied.items()):
                path = Path(name)
                if path.parent in pin.dirs and (not enabled or record.get("removed")):
                    add(pin, core_of(path.name))
        if enabled:
            keys = {pin.key: pin for pin in pins}
            for item in (config or {}).get("unfinished", []):
                if item["install"] in keys:
                    add(keys[item["install"]], item["core"])
        return plan

    def _hidden_items(self, config: dict | None) -> list[dict[str, str]]:
        """The entries of the installs this data directory removed from its list: listed as not switched for the data
        directory that names them (a toggle here leaves their files as they are)."""
        items = []
        for key, folder in ((config or {}).get("folders") or {}).items():
            if self.data_id not in (folder.get("hidden") or []):
                continue
            cores = set(folder.get("configured") or {})
            root = Path(key)
            cores.update(core_of(Path(path).name) for path in self._applied
                         if Path(path).is_relative_to(root) and kind_of_file(Path(path).name))
            pin = self._recorded_pin(key, folder)
            if pin is not None:
                mapped = self._core_map(pin)
                cores = {mapped.slot(core) for core in cores}
            items.extend({"install": key, "core": core, "why": "pending"} for core in sorted(cores))
        return items

    def toggle(self, enabled: bool, expect: bool, pins: list[Pin] | None = None, log: LogFunc = no_log,
               progress: ProgressCallback | None = None, cancel: Callable[[], bool] | None = None) -> ToggleResult:
        """The Enable Mods switch over every known install (a writer job of the game folders). An Off adopts the mods
        it finds first (with scan consent its own pass finds them; without, the changed files it meets), keeps the
        built copies, then switches every entry carrying a library source to official. An On switches every
        configured entry that does not carry its source to it, from the built copy when it still fits; a file
        changed outside the library is skipped (``why`` ``changed``) and left as it is. The
        configuration never changes (decision 2); the intent and the entries not switched yet are saved first.
        ``expect`` is the intent the caller saw (``state_changed`` otherwise); Retry is the same call with ``expect``
        equal to the current intent."""
        return self._run(enabled, expect, pins, log, progress, cancel, drift=None)

    def restore_drift(self, entries: list[dict], pins: list[Pin] | None = None, log: LogFunc = no_log,
                      progress: ProgressCallback | None = None,
                      cancel: Callable[[], bool] | None = None) -> ToggleResult:
        """Apply Mod Configuration: switch each given reset or updated entry ({install, core, state}) to its
        configured source again, the whole core, from its built copy when it still fits; its configuration stays.
        A changed entry is switched only when listed as changed (its outside change is kept as a mod first); one
        that turned changed since it was listed is skipped."""
        return self._run(None, None, pins, log, progress, cancel, drift=entries)

    def _run(self, enabled: bool | None, expect: bool | None, pins: list[Pin] | None, log: LogFunc,
             progress: ProgressCallback | None, cancel: Callable[[], bool] | None,
             drift: list[dict] | None) -> ToggleResult:
        with self._lock, _errors("toggle"):
            pins = list(pins) if pins is not None else self.installs()
            self._check_pins(pins)
            config = self.profiles.read()
            current = profiles_mod.enabled(config)
            if drift is None and current != expect:
                raise ServiceError("state_changed")
            safety.check_game_not_running()
            self._check_idle(pins)
            if drift is None:
                from .portrait_edit import refresh_missing
                from .lobby_camera import refresh
                refresh_missing(self, pins, log)
                refresh(self, pins, log)
                config = self.profiles.read()
            if drift is not None:
                if not current:  # mods off: official files are the intent, there is no drift to apply
                    raise ServiceError("state_changed")
                enabled = current
                by_key = {pin.key: pin for pin in pins}
                plan = [(by_key[item["install"]], item["core"].lower()) for item in drift
                        if isinstance(item, dict) and item.get("install") in by_key
                        and isinstance(item.get("core"), str)]
                listed = {(item["install"], item["core"].lower()): item.get("state") for item in drift
                          if isinstance(item, dict) and isinstance(item.get("core"), str)}
                allowed = False
            else:
                allowed = self._scan_allowed()  # asked once: the job keeps the answer it planned with
                # an Off with scan consent plans from the files as they are now: its own pass
                found = self._view(pins, "scan", config).entries(pins) if (not enabled and allowed) else []
                plan = self._plan_toggle(enabled, pins, config, found)
                listed = {}
                if not enabled:
                    self._check_space(self._off_space(plan, found, config))
            hidden = self._hidden_items(config) if drift is None else []
            if enabled:  # an On, its Retry or Apply Mod Configuration: confirmed before the intent changes
                self._untested_plan(plan, config, drift is not None)
        stamp = now()
        if drift is None:
            def intent(data: dict) -> None:
                data["enabled"], data["toggled"] = enabled, stamp
                listed_keys = {(item["install"], item["core"]) for item in data["unfinished"]}
                for pin, core in plan:
                    if (pin.key, core) not in listed_keys:
                        data["unfinished"].append({"install": pin.key, "core": core, "why": "pending"})
                        listed_keys.add((pin.key, core))
                for item in hidden:
                    if (item["install"], item["core"]) not in listed_keys:
                        data["unfinished"].append(item)
                        listed_keys.add((item["install"], item["core"]))

            with _errors("toggle"):
                self.profiles.update(intent)
            logger.info("Mods %s: %d entries to reach", "on" if enabled else "off", len(plan))
        result = {"done": [], "adopted": [], "failed": [], "stuck": [], "left": [], "skipped": [], "dropped": [],
                  "held": []}
        outcomes: dict[tuple[str, str], str | None] = {}
        cores: set[str] = set()
        kinds: set[str] = set()
        saved_at, unsaved = time.monotonic(), 0
        stop: ServiceError | None = None
        for i, (pin, core) in enumerate(plan, 1):
            if cancel and cancel():
                break
            with self._lock:
                try:
                    with _errors("toggle"):
                        self._check_pins(pins)
                        outcome, why, entry = self._reach(pin, core, enabled, allowed, listed.get((pin.key, core)),
                                                          drift is not None, log)
                except ServiceError as error:
                    item = {"install": pin.key, "core": core, "kind": self._kind(core, pin) or "spr",
                            "variant": self._core_map(pin).key(core) or core}
                    if error.code not in STOPPING:  # a refusal of the moment belongs to the switch, not the choice
                        self._selection_error(pin, [core], error.to_json())
                    result["failed"].append({"entry": item, "code": error.code, "params": error.to_json()["params"]})
                    outcomes[(pin.key, core)] = "pending"
                    if error.code in STOPPING:
                        stop = error
                        break
                    continue
            outcomes[(pin.key, core)] = why
            if "done" in outcome:
                self._selection_error(pin, [core], None)
            for name, value in outcome.items():
                result[name].append(value)
            if entry is not None and ("done" in outcome or "adopted" in outcome):
                cores.add(core)
                kinds.add(entry["kind"])
            unsaved += 1
            if unsaved >= SAVE_EVERY[0] or time.monotonic() - saved_at >= SAVE_EVERY[1]:
                with _errors("toggle"):
                    self._save_outcomes(outcomes)
                outcomes, unsaved, saved_at = {}, 0, time.monotonic()
            if progress:
                progress(i, len(plan), t("webui.progress.apply" if enabled else "webui.progress.restore", name=core))
        with self._lock:
            self._prune_quietly()
        with _errors("toggle"):
            self._save_outcomes(outcomes)
        if stop is not None:
            raise stop
        return ToggleResult(bool(enabled), result["done"], result["adopted"], result["failed"], result["stuck"],
                            result["left"], result["skipped"], result["dropped"], sorted(cores), sorted(kinds),
                            result["held"])

    def _untested_plan(self, plan: Sequence[tuple[Pin, str]], config: dict | None, drift: bool) -> None:
        """The confirmation of a toggle's writes on an untested build (``_check_untested``): every planned entry
        configured with a source other than official, but those an On or its Retry holds for a changed rig
        (``_hold``)."""
        writes = []
        untested = {key: self.untested_version(pin) for key, pin in {pin.key: pin for pin, _core in plan}.items()}
        for pin, core in plan:
            if untested[pin.key] is None:
                continue
            entry = ((((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}).get(core) or {}
            source = entry.get("source")
            if not isinstance(source, str) or not source or source == "official" or (
                    not drift and self._hold(pin, source)):
                continue
            targets = self._spine_parts(self._core_map(pin).slot(core), pin)
            if targets:
                writes.append((pin, self._untested_entry(pin, core, targets, entry.get("variant"), entry.get("kind")),
                               targets))
        self._check_untested(writes)

    def _save_outcomes(self, outcomes: dict[tuple[str, str], str | None]) -> None:
        """The unfinished list after reached entries: an entry that holds its intent leaves, another keeps (or gets)
        the why of what happened. Items of entries the job did not reach stay as they are."""
        if not outcomes:
            return

        def change(data: dict) -> bool:
            items = {(item["install"], item["core"]): item for item in data["unfinished"]}
            for key, why in outcomes.items():
                if why is None:
                    items.pop(key, None)
                else:
                    items[key] = {"install": key[0], "core": key[1], "why": why}
            data["unfinished"] = list(items.values())
            return True

        self.profiles.update(change)

    def _off_space(self, plan: list[tuple[Pin, str]], found: list[_Entry], config: dict | None) -> int:
        """What an Off copies: the mods it adopts and their official copies (from its pass), and the built copies it
        keeps (parts not in built/ yet and not files of their mod)."""
        need = 0
        for entry in found:
            if entry.state in ("untracked", "changed"):
                need += sum(part.size for part in entry.parts if part.carried == "unknown")
                need += sum(part.size for part in entry.parts if not part.official
                            and entry.copies.get(str(part.path)) in ("backup", "elsewhere"))
        folders = (config or {}).get("folders") or {}
        for pin, core in plan:
            configured = ((folders.get(pin.key) or {}).get("configured") or {}).get(core)
            source = (configured or {}).get("source")
            if not source:
                continue
            mod_sha1s: set[str] = set()
            if source.startswith("mod:") and source[4:] in self._records:
                with contextlib.suppress(OSError):
                    mod_sha1s = {self._hashes.get(path).sha1 for path in self._paths(self._records[source[4:]])}
            for path in self._spine_parts(core, pin):
                record = self._applied.get(str(path.resolve()))
                if not record or record.get("source") != source or record.get("official") is True:
                    continue
                if record.get("sha1") in mod_sha1s or (self.root / "built" / f"{record.get('sha1')}.bundle").exists():
                    continue
                need += record.get("size") or 0
        return need

    def _reach(self, pin: Pin, core: str, enabled: bool, allowed: bool, listed_state: str | None, drift: bool,
               log: LogFunc) -> tuple[dict[str, Any], str | None, dict | None]:
        """One entry of a toggle, Retry or Apply Mod Configuration, under the library lock: its configuration read
        again and its files judged at write strength (a part the library can show unchanged by stat carries its
        record's source; every other part is read in full). Returns (what happened, by result list; the entry's
        why in the unfinished list, None when it leaves; the entry)."""
        pins = self._view_pins(pin)
        view = self._view(pins, "full")
        entry = view.entry(pin, core)
        self._note_judged(view, entry)
        item = entry.item()
        outcome: dict[str, Any] = {}
        if entry.state == "source_missing":
            self._configure(pin, entry.variant, {entry.kind: core}, None)
            logger.info("Dropped the configuration of %s: %s is gone from the library", core, entry.configured)
            outcome["dropped"] = item
            view = self._view(pins, "full")
            entry = view.entry(pin, core)
        if entry.takeover:
            self._configure(pin, entry.variant, {entry.kind: core}, entry.takeover)
            logger.info("Configured %s in %s with %s (taken over)", core, pin.key, entry.takeover)
            view = self._view(pins, "full")
            entry = view.entry(pin, core)
        state = entry.state
        if state in ("damaged", "missing"):
            outcome["skipped"] = {**item, "why": state}
            return outcome, None, item
        if enabled is False:
            if state in ("untracked", "changed"):
                try:
                    if allowed is False:
                        self._check_space(sum(part.size for part in entry.parts if part.carried == "unknown"))
                    saved = entry.configured
                    mod_id = self._adopt_one(pin, core)
                    if saved and view.source_exists(saved, pin.key, entry.kind):
                        self._configure(pin, entry.variant, {entry.kind: core}, saved)
                except ServiceError as error:
                    outcome["left"] = {**item, "code": error.code, "params": error.to_json()["params"]}
                    return outcome, "left", item
                outcome["adopted"] = {**item, "mod": mod_id}
                view = self._view(pins, "full")
                entry = view.entry(pin, core)
            if entry.carried == "official":
                return outcome, None, item
            if entry.overwritten:
                outcome["stuck"] = item
                return outcome, "stuck", item
            if entry.configured and entry.carried == entry.configured and not entry.drifted:
                self._make_built(pin, entry)
            try:
                self._switch(pin, core, "official", log)
            except ServiceError as error:
                if error.code == "original_unavailable":
                    outcome["stuck"] = item
                    return outcome, "stuck", item
                raise
            outcome["done"] = item
            return outcome, None, item
        # mods on: an On, its Retry, or Apply Mod Configuration. An outside change is never written over by an On or its
        # Retry (it leaves the unfinished list: the "Mods Not in the Library" prompt offers it); only Apply Mod
        # Configuration may, for an entry it was given as changed
        if state in ("untracked", "changed") and not (drift and state == "changed" and listed_state == "changed"):
            outcome["skipped"] = {**item, "why": state}
            return outcome, (None if leaves(state, True) else "pending"), item
        if drift and state not in ("reset", "updated", "changed"):
            if state != "ok":
                outcome["skipped"] = {**item, "why": "entry_changed"}
            return outcome, (None if leaves(state, True) else "pending"), item
        target = entry.configured or "official"
        if state == "ok":
            self._selection_error(pin, [core], None)
            return outcome, None, item
        if entry.overwritten and not entry.configured:
            outcome["stuck"] = item
            return outcome, "stuck", item
        if not drift and self._hold(pin, target):
            # A game update changed the official rig under the mod: no On or Retry writes it until it is remapped or
            # updated, or the user applies it as it is.
            outcome["held"] = {**item, "mod": target[4:]}
            return outcome, "held", item
        mod_count = len(self._records)
        try:
            self._switch(pin, core, target, log)
        except ServiceError as error:
            if error.code == "original_unavailable":
                outcome["stuck"] = item
                return outcome, "stuck", item
            raise
        outcome["done"] = item
        if len(self._records) > mod_count:
            outcome["done"] = {**outcome["done"], "kept_current": True}
        if target.startswith("mod:") and target[4:] in self._records and self._incomplete(self._records[target[4:]], pin):
            outcome["done"] = {**outcome["done"], "incomplete": True}
        return outcome, None, item

    def _note_judged(self, view: _View, entry: _Entry) -> None:
        """What a judgement found that the records keep, so the cheap judgement agrees with it from then on: the new
        date of a file whose bytes are the record's (its sha1 at write strength, its last bytes cheaply), and, at
        write strength, a file the cheap rule calls official whose bytes were changed outside the library (``outside``,
        its size and date; dropped by the next write there)."""
        if self._writes_refused():
            return  # a recovery that has failed refuses changes: a later judgement notes it
        changed = False
        for part in entry.parts:
            record = self._applied.get(str(part.path.resolve())) if part.record is not None else None
            if record is None:
                continue
            if part.restamp is not None:
                if record.get("mtime") != part.restamp or "outside" in record:
                    logger.info("Same bytes, new date: %s", part.path.name)
                    record["mtime"] = part.restamp
                    record.pop("outside", None)
                    changed = True
                continue
            if view.allow != "full" or part.matched or part.carried != "unknown" or part.trailing != 0:
                continue
            expected = view.official_size(part.path.name)
            if expected is not None and expected != part.size:
                continue  # the cheap rule finds it already: another size than the official file
            mark = [part.size, part.mtime]
            if record.get("outside") != mark:
                logger.info("Changed outside the library, though it looks official: %s", part.path.name)
                record["outside"] = mark
                changed = True
        if changed:
            try:
                self._save()
            except (OSError, ServiceError) as error:
                log_failure(logger, logging.WARNING, "Could not save what a judgement found", error)

    def judge_unfinished(self) -> int:
        """Judge the unfinished list's entries again from what the library recorded (stat, header and last bytes of
        its own writes; no scan consent needed), and drop those that need nothing more (the leaving rule). Only the
        items of this data directory's known installs; nothing is dropped when the intent changed meanwhile.
        Returns the number of items dropped. Runs on the job worker."""
        with self._lock, _errors("judge_unfinished"):
            if self._writes_refused():
                return 0  # a recovery that has failed refuses changes of the list
            config = self.profiles.read()
            if not config or not config.get("unfinished"):
                return 0
            enabled, toggled = profiles_mod.enabled(config), config.get("toggled")
            pins = self.installs()
            by_key = {pin.key: pin for pin in pins}
            allowed = self._scan_allowed()
            view = self._view(pins, "scan" if allowed else "recorded", config)
            leaving: set[tuple[str, str]] = set()
            for item in config["unfinished"]:
                pin = by_key.get(item["install"])
                if pin is None:
                    continue
                files = view.indexes[pin.key].get(item["core"], [])
                if not allowed and files and not any(str(path.resolve()) in view.applied for path in files):
                    continue  # judged only by the scan, or when a toggle reaches it
                try:
                    entry = view.entry(pin, item["core"])
                except (OSError, _Unread):
                    continue
                self._note_judged(view, entry)
                if leaves(entry.state, enabled):
                    leaving.add((item["install"], item["core"]))
            if not leaving:
                return 0
            dropped = []

            def change(data: dict) -> bool:
                if profiles_mod.enabled(data) != enabled or data.get("toggled") != toggled:
                    return False
                kept = [item for item in data["unfinished"] if (item["install"], item["core"]) not in leaving]
                dropped.extend(range(len(data["unfinished"]) - len(kept)))
                data["unfinished"] = kept
                return bool(dropped)

            self.profiles.update(change)
            return len(dropped)

    # --- prompts and settings ---

    @_serialized
    def dismiss(self, kind: str, entries: list[dict]) -> None:
        """Keep as Is / Don't Ask Again: remember each entry's files by name, size and modification time, so its
        prompt does not come back until one of them changes. Nothing is read beyond stats."""
        if kind not in ("drift", "untracked"):
            raise ServiceError("bad_params", kind=kind)
        pins = {pin.key: pin for pin in self.installs()}
        found = {}
        for item in entries:
            pin = pins.get(item.get("install")) if isinstance(item, dict) else None
            if pin is None or not isinstance(item.get("core"), str):
                continue
            core = self._core_map(pin).slot(item["core"])
            files = []
            for path in self._spine_parts(core, pin):
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    files.append([path.name, stat.st_size, stat.st_mtime_ns])
            found[(pin.key, core)] = (pin, sorted(files))
        if not found:
            return

        def change(data: dict) -> None:
            for (key, core), (pin, files) in found.items():
                self._folder(data, pin)["dismissed"].setdefault(kind, {})[core] = files

        self._update_profiles(change)

    @_serialized
    def forget(self, install: str) -> None:
        """Remove from List: take a recorded install this data directory's settings no longer name off its list. With
        no other data directory naming it, its record goes (configuration, markers, dismissals, built copies and
        unfinished items); otherwise it is only hidden here. state.json's records and the kept originals stay, and
        no game file is touched. ``bad_params`` for a named install."""
        if any(pin.key == install for pin in self._named_pins()):
            raise ServiceError("bad_params", install=install)
        dropped: list[dict] = []
        data_id = self.data_id

        def change(data: dict) -> bool:
            folder = data["folders"].get(install)
            if folder is None:
                return False
            if any(other != data_id for other in folder["named_by"]):
                if data_id not in folder["hidden"]:
                    folder["hidden"].append(data_id)
                return True
            dropped.extend(folder["built"].values())
            del data["folders"][install]
            data["unfinished"] = [item for item in data["unfinished"] if item["install"] != install]
            return True

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    # --- working on mods ---

    def _remap_source_atlases(self, paths: list[Path], selected: dict[str, bytes],
                             official: dict[str, bytes], available: list[Path] | None = None) -> dict[str, bytes]:
        """Fill absent source atlases only from verified official copies of the mod's date."""
        missing = official.keys() - atlas_remap.pair_atlases(selected, official).keys()
        if not missing:
            return selected
        core = core_of(paths[0].name)
        dates = {date_of(path.name) for path in paths} - {""}
        fallback = {}
        candidates = {name: self.root / "originals" / name for name in self._originals()}
        candidates.update({path.name: path for path in available or ()})
        for name, path in sorted(candidates.items()):
            if core_of(name) != core or date_of(name) not in dates:
                continue
            if not self._is_official(path):
                raise ServiceError("original_invalid", file=name)
            for asset, text in integrity.inspect(path).atlases.items():
                if asset in fallback and fallback[asset] != text:
                    raise atlas_remap.RemapError("atlas_remap_asset_ambiguous", file=name, name=asset)
                fallback[asset] = text
        pairs = atlas_remap.pair_atlases(fallback, {name: official[name] for name in missing})
        result = dict(selected)
        result.update({name: fallback[source] for name, source in pairs.items()})
        if not result:
            raise atlas_remap.RemapError("atlas_remap_source_missing", file=", ".join(sorted(missing)),
                                         date=", ".join(sorted(dates)) or "—")
        return result

    def _remap_view(self, record: dict, paths: list[Path], targets: list[Path], originals: dict[Path, Path],
                    pin: Pin) -> dict:
        """What a remap starts from: the mod's own inputs (``mod``), the official ones (``official``, from
        ``originals``: per target its verified official copy), the official files with the mod's over them (``view``),
        the official atlases the mod paints without carrying them (``mixed``), and why a remap is wanted: those whose
        page the texture does not fit (``misfit``), those repacked since its textures were drawn on them (``layout``,
        ``fingerprint.changed_layouts``) and the mod's own atlases that give way to the official rig, with its
        skeleton (``rerig``, ``atlas_remap.rerigged``). ``dated``: the rig of the mod's date of the misfit atlases
        the fingerprint does not record (``_dated_rig``)."""
        mod = atlas_remap.read_inputs(paths)
        official = atlas_remap.read_inputs(list(originals.values()))
        replaced = self._replaced_parts({path.name: path for path in paths}, targets, pin)
        others = [original for target, original in originals.items() if target not in replaced]
        view = atlas_remap.read_inputs(others + paths)
        mixed = {name: data for name, data in view.atlases.items() if name not in mod.atlases
                 and any(atlas_remap.texture_key(page) in mod.textures
                         for page in atlas_remap.parse_atlas(data, name).pages)}
        sizes = {name: image.size for name, image in view.textures.items()}
        misfit = {name for name, data in mixed.items() if atlas_remap.page_size_mismatch({name: data}, sizes)}
        layout = fingerprint.changed_layouts(self._read_built_against(record), mixed)
        if kind_of_file(paths[0].name) == "spr":
            # the rig the archive keeps for textures no record covers, as compatibility finds it
            unrecorded = {name: data for name, data in mixed.items()
                          if not isinstance((layout.get(name) or {}).get("skeleton"), str)}
            layout = {**layout, **self._archived_rigs(paths, unrecorded, lambda: {
                key: rig_archive.alpha_mask(image) for key, image in official.textures.items()})}
        gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
        rerig = atlas_remap.rerigged(gains, mod.atlases, mod.skeletons, official.atlases, official.skeletons,
                                     mod.textures)
        unrecorded = {name for name in misfit if not isinstance((layout.get(name) or {}).get("skeleton"), str)}
        dated = self._dated_rig(paths, unrecorded, official.atlases, originals.values())
        return {"mod": mod, "official": official, "view": view, "mixed": mixed, "misfit": misfit, "layout": layout,
                "rerig": rerig, "dated": dated}

    @staticmethod
    def _rebake_plan(inputs: dict) -> dict[str, bytes]:
        """The portrait atlases the world-space rebake bakes (``bmpt.rebake``), each with the atlas the mod's textures
        were drawn on, chosen as ``rig-geometry.js`` ``forRemap`` chooses it (the mod's own, else the recorded one:
        the fingerprint's, or the official copy of the mod's date, else the official one): the mod's own atlases that give way to the official rig, and the official ones it
        paints without carrying them where they were repacked under a recorded rig or the texture does not fit. An
        atlas the mod carries otherwise keeps its own rig and textures."""
        mod, official = inputs["mod"], inputs["official"]
        carried = atlas_remap.pair_atlases(mod.atlases, official.atlases)
        plan = {}
        for name, text in official.atlases.items():
            recorded = inputs["layout"].get(name) or {}
            if name in inputs["rerig"]:
                plan[name] = mod.atlases[carried[name]]
            elif name in carried:
                continue
            elif isinstance(recorded.get("skeleton"), str):
                plan[name] = recorded["text"].encode("utf-8")
            elif name in inputs["misfit"]:
                dated = inputs["dated"].get(name)
                plan[name] = dated["text"].encode("utf-8") if dated else text
        return plan

    @staticmethod
    def _posted_rigs(geometry: Any, name: str, source: bytes, target: bytes) -> tuple:
        """The posted geometry of an atlas's two rigs (``rig-geometry.js`` ``forRemap``), checked against the atlases
        read here (``rebake.rig``), with the two atlases."""
        posted = geometry.get(name) if isinstance(geometry, dict) else None
        if not isinstance(posted, dict):
            raise atlas_remap.RemapError("bad_params", key="geometry")
        source_atlas, target_atlas = atlas_remap.parse_atlas(source, name), atlas_remap.parse_atlas(target, name)
        return (rebake.rig(posted.get("source"), source_atlas), rebake.rig(posted.get("target"), target_atlas),
                source_atlas, target_atlas)

    @staticmethod
    def _rebake_pages(inputs: dict, source: atlas_remap.Atlas, target: atlas_remap.Atlas,
                      done: dict | None = None) -> tuple[dict, dict]:
        """The mod's pages of the source atlas and the official pages of the target one, keyed by texture key; a page
        an earlier atlas baked (``done``) is taken as it came out."""
        mod, official = inputs["mod"], inputs["official"]
        source_pages = {key: mod.textures[key] for key in map(atlas_remap.texture_key, source.pages)
                        if key in mod.textures}
        official_pages = {}
        for page in target.pages:
            key = atlas_remap.texture_key(page)
            if key not in official.textures:
                raise atlas_remap.RemapError("atlas_remap_page_missing", file=target.file, page=page)
            official_pages[key] = (done or {}).get(key, official.textures[key])
        return source_pages, official_pages

    def _rebake(self, inputs: dict, plan: dict[str, bytes], geometry: Any) -> tuple[dict, dict]:
        """The texture patch of a portrait remap: every planned atlas baked (``rebake.bake``), every other page as the
        official files with the mod's over them have it."""
        official, view = inputs["official"], inputs["view"]
        report = {"moved": [], "added": [], "official": [], "dropped": [], "upscaled": [], "warnings": []}
        baked: dict[str, Any] = {}
        for name, source_text in plan.items():
            source, target, source_atlas, target_atlas = self._posted_rigs(
                geometry, name, source_text, official.atlases[name])
            source_pages, official_pages = self._rebake_pages(inputs, source_atlas, target_atlas, baked)
            pages, summary = rebake.bake(source, target, source_pages, official_pages)
            baked.update(pages)
            for key in ("moved", "official"):
                report[key].extend(f"{name} - {region}" for region in summary[key])
        patch = {official.keys[key]: baked.get(key, view.textures.get(key, image))
                 for key, image in official.textures.items() if key in official.keys}
        report["partial"], report["unchanged"] = bool(report["official"]), False
        return patch, report

    @_serialized
    def redrawn(self, mod_id: str, geometry: Any, pin: Pin | None = None) -> dict:
        """Whether the game drew a portrait mod's picture anew since its textures were drawn: the smallest overlap of
        the two rest pictures (``rebake.overlap``) over the atlases a remap would bake. Read-only."""
        pin = self._current(pin)
        record = self._record(mod_id)
        paths = self._paths(record)
        if kind_of_file(paths[0].name) != "spr":
            return {"redrawn": False, "overlap": 1.0}
        if not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])
        originals = {target: self._official_for(target, pin) for target in targets}
        lowest = 1.0
        try:
            inputs = self._remap_view(record, paths, targets, originals, pin)
            for name, source_text in self._rebake_plan(inputs).items():
                source, target, source_atlas, target_atlas = self._posted_rigs(
                    geometry, name, source_text, inputs["official"].atlases[name])
                lowest = min(lowest, rebake.overlap(source, target, *self._rebake_pages(inputs, source_atlas,
                                                                                         target_atlas)))
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        except (ValueError, KeyError, EOFError, OSError) as error:
            raise ServiceError("atlas_remap_invalid", file=", ".join(path.name for path in paths),
                               reason=str(error)) from error
        return {"redrawn": lowest < rebake.REDRAWN_BELOW, "overlap": round(lowest, 4)}

    def _dated_rig(self, paths: list[Path], names: Iterable[str], official: dict[str, bytes],
                   available: Iterable[Path] = ()) -> dict[str, dict]:
        """Of the official atlases ``names``, those a verified official copy of the mod's date holds (one the
        library kept, or one of ``available`` of that date), each as a ``fingerprint.painted`` entry: that copy's
        atlas ``text`` and, where it has one, the base64 ``skeleton`` of the same stem. The rig a texture-only mod's
        textures were drawn for when its fingerprint does not record it."""
        names = set(names)
        if not names or not paths:
            return {}
        core = core_of(paths[0].name)
        dates = {date_of(path.name) for path in paths} - {""}
        candidates = {name: self.root / "originals" / name for name in self._originals()}
        candidates.update({path.name: path for path in available})
        atlases: dict[str, bytes] = {}
        skeletons: dict[str, bytes] = {}
        for name, path in sorted(candidates.items()):
            if core_of(name) != core or date_of(name) not in dates or not self._is_official(path):
                continue
            contents = integrity.inspect(path)
            atlases.update(contents.atlases)
            skeletons.update(contents.skeletons)
        pairs = atlas_remap.pair_atlases(atlases, {name: official[name] for name in names if name in official})
        drawn_by = atlas_remap.pair_atlases(skeletons, dict.fromkeys(pairs))
        return {name: {"text": atlases[source].decode("utf-8-sig"),
                       **({"skeleton": base64.b64encode(skeletons[drawn_by[name]]).decode("ascii")}
                          if name in drawn_by else {})}
                for name, source in pairs.items()}

    @_serialized
    def remap(self, mod_id: str, log: LogFunc = no_log,
              progress: Callable[[dict], None] | None = None, pin: Pin | None = None, geometry: Any = None) -> dict:
        """Prepare a review in temporary storage; only accept_remap can add it to the library. A portrait's pages are
        baked through world space from ``geometry`` (``rig-geometry.js`` ``forRemap``, required); other kinds move
        regions by name onto a page size the texture does not fit."""
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repaired_from") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        self._remap_previews = {token: preview for token, preview in self._remap_previews.items()
                                if preview["expires"] > time.monotonic() and preview["source"] != mod_id}
        paths = self._paths(record)
        portrait = kind_of_file(paths[0].name) == "spr"
        if portrait and not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])
        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                step("extract")
                inputs = self._remap_view(record, paths, targets, originals, pin)
                mod, official, original_view = inputs["mod"], inputs["official"], inputs["view"]
                rerig = inputs["rerig"] if portrait else {}
                given = {source: name for name, source in
                         atlas_remap.pair_atlases(mod.atlases, official.atlases).items() if name in rerig}
                given_skeletons = {source: name for name, source in
                                   atlas_remap.pair_atlases(mod.skeletons, official.skeletons).items()
                                   if name in rerig.values()}
                if portrait:
                    plan = self._rebake_plan(inputs)
                    if not plan:
                        return {"unchanged": True}
                    patch, report = self._rebake(inputs, plan, geometry)
                    for source, name in sorted(given_skeletons.items()):
                        if not atlas_remap.same_rig(mod.skeletons[source], official.skeletons[name]):
                            report["warnings"].append({"key": "rig", "name": name})
                    report["partial"] = bool(report["warnings"] or report["official"])
                else:
                    mixed = inputs["mixed"]
                    if not inputs["misfit"]:
                        return {"unchanged": True}
                    mod.atlases = {**mod.atlases,
                                   **self._remap_source_atlases(paths, {}, mixed, list(originals.values()))}
                    target_inputs = replace(official, atlases=original_view.atlases,
                                            skeletons=original_view.skeletons)
                    patch, report = atlas_remap.prepare_patch(mod, target_inputs)
                # Preserve the winning mod skeleton and atlas when porting the texture patch, but for the ones
                # that give way to the official rig.
                text_patch = {}
                for path in paths:
                    bundle = Bundle.load(path)
                    extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
                    text_patch.update({key: value for key, value in extracted.items()
                                       if key.name not in given and key.name not in given_skeletons})
                target_keys = set().union(*(Bundle.load(path).get_asset_keys("cont_name_type") for path in originals.values()))
                if text_patch.keys() - target_keys:
                    raise atlas_remap.RemapError("atlas_remap_target_mismatch", file=paths[0].name,
                                                 matched=len(text_patch.keys() & target_keys), expected=len(text_patch))
                patch.update(text_patch)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError("atlas_remap_invalid", file=", ".join(path.name for path in paths),
                                   reason=str(error)) from error
            if report["unchanged"]:
                return {"unchanged": True, "report": report}
            return self._remap_review(mod_id, identity, paths, originals, official, original_view, output, patch,
                                      report, pin, log, step)

    @_serialized
    def old_rig(self, mod_id: str, log: LogFunc = no_log, progress: Callable[[dict], None] | None = None,
                pin: Pin | None = None) -> dict:
        """Prepare the review of a texture-only portrait mod applied with the rig its textures were drawn for, as a
        whole (``atlas_remap.old_rig_plan`` over the recorded rig, ``_remap_view`` ``layout``, or the official copy
        of the mod's date, ``dated``): that atlas and skeleton with the mod's textures, ported into the current
        official parts. Only accept_remap can add it to the library. For a portrait the game drew anew, whose pixels
        the rebake cannot move (``rebake.REDRAWN_BELOW``). Refused with ``old_rig_animations`` when an official
        skeleton has animations the old one lacks (the game calls them by name), and with ``old_rig_unavailable``
        when a recorded rig cannot be set up."""
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repaired_from") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        self._remap_previews = {token: preview for token, preview in self._remap_previews.items()
                                if preview["expires"] > time.monotonic() and preview["source"] != mod_id}
        paths = self._paths(record)
        if kind_of_file(paths[0].name) != "spr":
            raise ServiceError("old_rig_unavailable")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                step("extract")
                inputs = self._remap_view(record, paths, targets, originals, pin)
                mod, official = inputs["mod"], inputs["official"]
                plan = atlas_remap.old_rig_plan({**inputs["layout"], **inputs["dated"]}, official.atlases,
                                                official.skeletons, mod.skeletons, mod.textures, official.keys)
                patch = self._old_rig_patch(paths, originals, official, mod, plan) if plan.available else {}
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError("atlas_remap_invalid", file=", ".join(path.name for path in paths),
                                   reason=str(error)) from error
            if plan.animations:
                names = dict.fromkeys(name for missing in plan.animations.values() for name in missing)
                raise ServiceError("old_rig_animations", names=", ".join(names))
            if not plan.available:
                raise ServiceError("old_rig_unavailable")
            report = {"moved": [], "added": [], "official": [], "dropped": [], "upscaled": [], "warnings": [],
                      "rigs": sorted(Path(name).stem for name in plan.atlases), "partial": False, "unchanged": False}
            return self._remap_review(mod_id, identity, paths, originals, official, inputs["view"], output, patch,
                                      report, pin, log, step)

    @staticmethod
    def _old_rig_patch(paths: list[Path], originals: dict[Path, Path], official: atlas_remap.Inputs,
                       mod: atlas_remap.Inputs, plan: atlas_remap.OldRig) -> dict:
        """The patch of an old-rig set (``old_rig``): the mod's own text assets, the plan's atlases and skeletons
        over the official ones of their names, and the mod's own textures, all by ``cont_name_type``."""
        patch: dict = {}
        texts: dict[str, Any] = {}
        for original in originals.values():
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            for key in bundle.get_asset_keys("cont_name_type"):
                if key.type == "TextAsset":
                    texts.setdefault(key.name, key)
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path.name)
            extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
            patch.update(extracted)
        if patch.keys() - set(texts.values()):
            raise atlas_remap.RemapError("atlas_remap_target_mismatch", file=paths[0].name,
                                         matched=len(patch.keys() & set(texts.values())), expected=len(patch))
        for name, (atlas, skeleton, data) in plan.atlases.items():
            patch[texts[name]] = atlas
            patch[texts[skeleton]] = data
        patch.update({official.keys[key]: image for key, image in mod.textures.items() if key in official.keys})
        return patch

    # --- the graft of new official animations into a mod's own rig (bmpt.graft) ---

    def _graft_rigs(self, record: dict, paths: list[Path], inputs: dict,
                    available: Iterable[Path]) -> dict[str, dict]:
        """The rigs a graft adds the official rig's new animations to, per official atlas name: the mod's own binary
        skeleton and atlas where it carries them and the official skeleton has animations it lacks (``kind`` "own";
        ``base``: the official rig the mod was made from, as the fingerprint recorded it or the official copy of the
        mod's date, where known), else the rig a texture-only portrait's textures were drawn for
        (``atlas_remap.old_rig_plan``; ``kind`` "oldRig", its own base). Each with the official ``skeleton`` name,
        the ``animations`` it gains and the mod's ``pages`` (texture keys). ``available``: verified official files
        that may be of the mod's date (``_dated_rig``)."""
        mod, official = inputs["mod"], inputs["official"]
        gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
        skeleton_of = atlas_remap.pair_atlases(official.skeletons, official.atlases)
        own_skeletons = atlas_remap.pair_atlases(mod.skeletons, official.skeletons)
        own_atlases = atlas_remap.pair_atlases(mod.atlases, official.atlases)
        painted = (self._read_built_against(record) or {}).get("painted") or {}
        rigs: dict[str, dict] = {}

        def rig(kind: str, name: str, skeleton: str, text: str, data: bytes, base: tuple | None) -> None:
            try:
                pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(text, name).pages}
            except (ValueError, UnicodeError):
                return
            if not pages or not pages <= set(mod.textures) or not self._binary_42(data):
                return
            if base is not None and not self._binary_42(base[1]):
                base = None
            rigs[name] = {"kind": kind, "skeleton": skeleton, "atlas": text, "data": data, "base": base,
                          "animations": gains["animations"].get(skeleton, []), "pages": sorted(pages)}

        for name, skeleton in skeleton_of.items():
            if skeleton not in gains["animations"] or skeleton not in own_skeletons or name not in own_atlases:
                continue
            entry = painted.get(integrity.basename(name)) or {}
            if not isinstance(entry.get("skeleton"), str) or not isinstance(entry.get("text"), str):
                entry = self._dated_rig(paths, [name], official.atlases, available).get(name) or {}
            base = None
            if isinstance(entry.get("skeleton"), str) and isinstance(entry.get("text"), str):
                with contextlib.suppress(ValueError):
                    base = (entry["text"], base64.b64decode(entry["skeleton"], validate=True))
            if base is not None and base[1] == official.skeletons[skeleton]:
                base = None  # an official copy that kept the mod's date is the current rig: it tells no origin
            try:
                text = mod.atlases[own_atlases[name]].decode("utf-8-sig")
            except UnicodeError:
                continue
            rig("own", name, skeleton, text, mod.skeletons[own_skeletons[skeleton]], base)
        if rigs:
            return rigs
        plan = atlas_remap.old_rig_plan({**inputs["layout"], **inputs["dated"]}, official.atlases,
                                        official.skeletons, mod.skeletons, mod.textures, official.keys)
        if plan.complete:
            for name, (atlas, skeleton, data) in plan.atlases.items():
                if skeleton in plan.animations:
                    gains["animations"][skeleton] = plan.animations[skeleton]
                    text = atlas.decode("utf-8")
                    rig("oldRig", name, skeleton, text, data, (text, data))
        return rigs

    @staticmethod
    def _binary_42(data: bytes) -> bool:
        try:
            return (get_skel_version(data) or "").startswith("4.2.") and not data.lstrip()[:1] == b"{"
        except (ValueError, UnicodeError):
            return False

    def _graft_state(self, token: str, mod_id: str | None = None) -> dict:
        state = self._grafts.get(token) if isinstance(token, str) else None
        if state is None or state["expires"] <= time.monotonic() or (mod_id and state["source"] != mod_id):
            self._grafts.pop(token, None) if isinstance(token, str) else None
            raise ServiceError("entry_changed")
        return state

    @_serialized
    def graft(self, mod_id: str, log: LogFunc = no_log, progress: Callable[[dict], None] | None = None,
              pin: Pin | None = None) -> dict:
        """The first step of a graft (``bmpt.graft``): the rigs to pose, per official atlas name the mod's rig, the
        official rig it was made from (``base``, or None) and the official rig, each as ``atlas`` text and base64
        ``skeleton``; kept under a ``token`` for ``graft_build`` and ``graft_check``. Refused with
        ``graft_unavailable`` when no rig gains animations a graft could carry (``_graft_rigs``)."""
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repaired_from") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        now = time.monotonic()
        self._grafts = {token: state for token, state in self._grafts.items()
                        if state["expires"] > now and state["source"] != mod_id}
        paths = self._paths(record)
        if kind_of_file(paths[0].name) != "spr":
            raise ServiceError("graft_unavailable")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])
        if progress:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            originals = self._stage_official(targets, Path(temporary) / "official", pin)
            try:
                inputs = self._remap_view(record, paths, targets, originals, pin)
                sources = [self._official_for(target, pin) for target in targets]
                rigs = self._graft_rigs(record, paths, inputs, sources)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError("atlas_remap_invalid", file=", ".join(path.name for path in paths),
                                   reason=str(error)) from error
        if not rigs:
            raise ServiceError("graft_unavailable")
        official = inputs["official"]
        token = uuid.uuid4().hex
        self._grafts[token] = {"source": mod_id, "identity": identity, "pin": pin.key, "expires": now + 3600,
                               "rigs": rigs, "inputs": inputs, "prepared": None}

        def b64(data: bytes) -> str:
            return base64.b64encode(data).decode("ascii")

        posed = {}
        for name, rig in rigs.items():
            base = rig["base"]
            posed[name] = {"kind": rig["kind"], "animations": rig["animations"],
                           "mod": {"atlas": rig["atlas"], "skeleton": b64(rig["data"])},
                           "base": {"atlas": base[0], "skeleton": b64(base[1])} if base else None,
                           "official": {"atlas": official.atlases[name].decode("utf-8-sig"),
                                        "skeleton": b64(official.skeletons[rig["skeleton"]])}}
        return {"token": token, "rigs": posed}

    @_serialized
    def graft_build(self, token: str, geometry: Any) -> dict:
        """The second step: splice and pack every rig (``graft.prepare``) from the posted setup poses
        (``rig-geometry.js`` ``setupOf``: per atlas name ``mod``, ``official`` and ``base``), and hand back the
        test rigs to pose: per atlas the ``skeleton`` (base64) and ``atlas``, and ``check``: per animation to pose
        the slots whose part is compared; ``official``: the same for the official rig."""
        state = self._graft_state(token)
        if not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        official = state["inputs"]["official"]
        prepared, out, refused = {}, {}, {}
        try:
            for name, rig in state["rigs"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                mod_atlas = atlas_remap.parse_atlas(rig["atlas"], name)
                official_text = official.atlases[name].decode("utf-8-sig")
                mod_setup = grafting.setup(posted.get("mod"), mod_atlas)
                official_setup = grafting.setup(posted.get("official"))
                base_setup = grafting.setup(posted["base"]) if rig["base"] and posted.get("base") else None
                mod_pages = {key: state["inputs"]["mod"].textures[key] for key in rig["pages"]}
                one = grafting.prepare(rig["data"], rig["atlas"], mod_pages, official.skeletons[rig["skeleton"]],
                                       official_text, official.textures, mod_setup, official_setup, base_setup)
                prepared[name] = one
                refused.update(one.graft.refused)
                check = {animation: slots for animation, slots in one.slots.items()}
                check.update({test: one.slots[animation] for test, (animation, _c) in one.candidates.items()})
                out[name] = {"skeleton": base64.b64encode(one.test_skeleton).decode("ascii"), "atlas": one.test_atlas,
                             "check": check, "official": dict(one.slots)}
        except grafting.GraftError as error:
            raise ServiceError(error.code, **error.params) from error
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        state["prepared"] = prepared
        return {"rigs": out, "refused": refused}

    @_serialized
    def graft_check(self, token: str, geometry: Any, log: LogFunc = no_log,
                    progress: Callable[[dict], None] | None = None, pin: Pin | None = None) -> dict:
        """The last step: check every grafted expression against the official one from the posted poses of the test
        and the official rigs (``graft.choose``), and prepare the review of the repaired copy as a remap's
        (``_remap_review``; accepted with ``accept_remap``)."""
        state = self._graft_state(token)
        pin = self._current(pin)
        if state["prepared"] is None or not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        if pin.key != state["pin"]:
            raise ServiceError("entry_changed")
        mod_id = state["source"]
        record = self._record(mod_id)
        paths = self._paths(record)
        if state["identity"] != self._identity({path.name: path for path in paths}):
            raise ServiceError("entry_changed")
        inputs = state["inputs"]
        mod, official = inputs["mod"], inputs["official"]
        checked: dict[str, grafting.Checked] = {}
        try:
            for name, one in state["prepared"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                test = rebake.rig(posted.get("test"), atlas_remap.parse_atlas(one.test_atlas, name),
                                  max_poses=len(one.candidates) + len(one.slots) + 1)
                reference = rebake.rig(posted.get("official"), atlas_remap.parse_atlas(one.official_text, name))
                pages = {**{key: mod.textures[key] for key in state["rigs"][name]["pages"]}, **one.pages.images}
                checked[name] = grafting.choose(one, reference, test, official.textures, pages)
        except grafting.GraftError as error:
            raise ServiceError(error.code, **error.params) from error
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        self._grafts.pop(token, None)
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        report = {"moved": [], "added": [], "official": [], "dropped": [], "upscaled": [], "warnings": [],
                  "grafted": [], "refused": {}, "corrected": [], "unfixed": [], "grown": 0, "unanchored": False,
                  "unchanged": False}
        marks: list[str] = []
        for name, one in state["prepared"].items():
            result = checked[name]
            report["refused"].update(one.graft.refused)
            report["unanchored"] = report["unanchored"] or bool(one.unanchored)
            report["grown"] = max([report["grown"], *one.pages.grown.values()])
            for animation, outcome in result.results.items():
                report["grafted"].append(animation)
                shown = [c for key in one.graft.shown.get(animation, []) if (c := one.graft.carried.get(key))]
                if any(c.slot in one.unanchored for c in shown):  # placed without an anchor: rung for a look
                    marks += [c.region for c in shown if c.region]
                if outcome["candidate"]:
                    report["corrected"].append(animation)
                if not outcome["fixed"]:
                    report["unfixed"].append(animation)
                    marks += [c.region for key in one.graft.shown.get(animation, [])
                              if (c := one.graft.carried.get(key)) and c.region]
        report["marks"] = sorted(set(marks))
        report["partial"] = bool(report["unfixed"] or report["refused"] or report["unanchored"])
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                patch = self._graft_patch(paths, originals, official, mod, state["rigs"], checked)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            return self._remap_review(mod_id, state["identity"], paths, originals, official, inputs["view"], output,
                                      patch, report, pin, log, step)

    @_serialized
    def discard_graft(self, token: str) -> None:
        """Drop a graft between its steps without changing any mod or game file."""
        if isinstance(token, str):
            self._grafts.pop(token, None)

    @staticmethod
    def _graft_patch(paths: list[Path], originals: dict[Path, Path], official: atlas_remap.Inputs,
                     mod: atlas_remap.Inputs, rigs: dict[str, dict], checked: dict) -> dict:
        """The patch of a grafted copy: the mod's own text assets, every grafted skeleton and atlas over the official
        ones of their names, the mod's own textures and the grafted pages, all by ``cont_name_type``."""
        patch: dict = {}
        texts: dict[str, Any] = {}
        for original in originals.values():
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            for key in bundle.get_asset_keys("cont_name_type"):
                if key.type == "TextAsset":
                    texts.setdefault(key.name, key)
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path.name)
            extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
            patch.update(extracted)
        if patch.keys() - set(texts.values()):
            raise atlas_remap.RemapError("atlas_remap_target_mismatch", file=paths[0].name,
                                         matched=len(patch.keys() & set(texts.values())), expected=len(patch))
        patch.update({official.keys[key]: image for key, image in mod.textures.items() if key in official.keys})
        for name, rig in rigs.items():
            result = checked[name]
            if name not in texts or rig["skeleton"] not in texts:
                raise atlas_remap.RemapError("atlas_remap_target_mismatch", file=paths[0].name,
                                             matched=0, expected=2)
            patch[texts[name]] = result.atlas.encode("utf-8")
            patch[texts[rig["skeleton"]]] = result.skeleton
            for key, image in result.pages.items():
                if key not in official.keys:
                    raise ServiceError("graft_page_missing", page=key)
                patch[official.keys[key]] = image
        return patch

    def _remap_review(self, mod_id: str, identity: Any, paths: list[Path], originals: dict[Path, Path],
                      official: atlas_remap.Inputs, original_view: atlas_remap.Inputs, output: Path, patch: dict,
                      report: dict, pin: Pin, log: LogFunc, step: Callable[..., None]) -> dict:
        """The review of a prepared repair: the official parts (``originals``) with ``patch`` ported in by
        ``cont_name_type`` and CRC fixed as the settings ask, in ``output``; kept in temporary storage under a token
        that only accept_remap can add to the library. Every Texture2D of a part takes the patch's image of its
        texture key where the patch has one. ``original_view``: the official files with the mod's over them, the
        review's "before"."""
        generated = {}
        for i, (target, original) in enumerate(originals.items(), 1):
            step("replace", i)
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            keys = bundle.get_asset_keys("cont_name_type")
            local_patch = {key: value for key, value in patch.items() if key in keys}
            local_patch.update({key: patch[official.keys[atlas_remap.texture_key(key.name)]]
                                for key in keys if key.type == "Texture2D"
                                and official.keys.get(atlas_remap.texture_key(key.name)) in patch})
            if local_patch:
                result = rebuild([], [original], output, {"Texture2D"},
                    self.settings.save_options(self.settings.crc_needed(target)),
                    match_strategy="cont_name_type", log=log, prepared_patch=local_patch,
                    on_step=lambda key: step(key, i))
                if not result.success or len(result.file_pairs) != 1:
                    raise ServiceError("mod_update_failed", id=mod_id)
            else:
                atomic_copy(original, output / target.name)
            generated[target.name] = output / target.name
        if identity != self._identity({path.name: path for path in paths}):
            raise ServiceError("entry_changed")
        comparison = self._compare_paths(mod_id, list(generated.values()), paths)
        built_against = self._built_against(generated, pin, "remap")
        repaired = atlas_remap.read_inputs(list(generated.values()))

        def png(image) -> str | None:
            if image is None:
                return None
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")

        pages = [{"name": name, "original": png(original_view.textures.get(name)),
                  "repaired": png(repaired.textures.get(name))}
                 for name in sorted(original_view.textures.keys() | repaired.textures.keys())]

        def scenes(inputs) -> list[dict]:
            atlas_by_stem = {Path(name).stem.lower(): data for name, data in inputs.atlases.items()}
            result = []
            for name, data in inputs.skeletons.items():
                atlas = atlas_by_stem.get(Path(name).stem.lower())
                if atlas is None and len(inputs.atlases) == 1:
                    atlas = next(iter(inputs.atlases.values()))
                if atlas is not None:
                    result.append({"name": name, "json": data.decode("utf-8-sig", errors="replace").lstrip().startswith("{"),
                                   "skeleton": base64.b64encode(data).decode("ascii"),
                                   "atlas": atlas.decode("utf-8-sig")})
            return result

        drawings = {"original": scenes(original_view), "repaired": scenes(repaired)}
        token = uuid.uuid4().hex
        self._remap_previews[token] = {
            "source": mod_id, "identity": identity,
            "files": {name: path.read_bytes() for name, path in generated.items()},
            "report": report, "compare": comparison, "expires": time.monotonic() + 3600,
            "built_against": built_against,
        }
        return {"preview": token, "report": report, "compare": comparison.to_json(),
                "pages": pages, "drawings": drawings}

    @_serialized
    @_mod_writer
    def accept_remap(self, preview: str) -> RemapResult:
        """Store the exact reviewed bytes once, recording both ends in the same state write."""
        prepared = self._remap_previews.get(preview)
        if prepared is None or prepared["expires"] <= time.monotonic():
            self._remap_previews.pop(preview, None)
            raise ServiceError("entry_changed")
        record = self._record(prepared["source"])
        if record.get("repaired_from") or self._repaired_id(record) or prepared["identity"] != self._identity(
                {path.name: path for path in self._paths(record)}):
            raise ServiceError("entry_changed")
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "accept-") as temporary:
            files = {}
            for name, data in prepared["files"].items():
                path = Path(temporary) / name
                path.write_bytes(data)
                files[name] = path
            stored = self._store(files, self._label(record), record["target"], record["kind"],
                                 repaired_from=record["id"], built_against=prepared.get("built_against"))
        self._remap_previews.pop(preview, None)
        return RemapResult(self._entry(stored), prepared["report"],
                           replace(prepared["compare"], mod_id=stored["id"]))

    @_serialized
    def discard_remap(self, preview: str) -> None:
        """Drop the temporary review without changing any mod or game file."""
        self._remap_previews.pop(preview, None)

    @_serialized
    def update(self, mod_id: str, log: LogFunc = no_log,
               progress: Callable[[dict], None] | None = None, pin: Pin | None = None) -> UpdateResult:
        """Rebuild a mod against current official bundles and retain its old files (``_publish_update``, atomic per
        mod). Its built copies go: they hold the old build. The entries configured with it are marked updated (a game
        file still holds the old build); an update that changed nothing marks none."""
        self._check_recovery()  # refused before the rebuild when a recovery has failed or the state cannot be saved
        self._check_writable()
        pin = self._current(pin)
        record = self._record(mod_id)
        paths = self._paths(record)
        built_from = {path.name: self._hashes.get(path).sha1 for path in paths}
        painted = (self._read_built_against(record) or {}).get("painted")  # the textures move over as they are
        targets = self._plan_targets(self._plan(record, log, pin))
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "update-") as temporary:
            output = Path(temporary)
            generated = []
            all_unchanged = True
            for i, target in enumerate(targets, 1):
                def step(key: str) -> None:
                    if progress:
                        progress({"current": i, "total": len(targets), "step": f"import.step.{key}"})

                step("check")
                result = rebuild(
                    paths, [self._official_for(target, pin)], output, self.settings.asset_types(),
                    self.settings.save_options(self.settings.crc_needed(target)), self.settings.spine_options(),
                    skip_unchanged=True, match_strategy="cont_name_type",
                    anim_check=self.settings.anim_check_options(), log=log, on_step=step)
                if not result.success:
                    raise ServiceError("mod_update_failed", id=mod_id)
                if not result.all_unchanged:
                    all_unchanged = False
                generated.extend(pair.output for pair in result.file_pairs)
            if all_unchanged or (len(generated) == len(paths) and all(
                    new.name == old.name and self._hashes.get(new).sha1 == self._hashes.get(old).sha1
                    for new, old in zip(sorted(generated), sorted(paths)))):
                # the mod is what a rebuild on these official parts gives: they are its baseline, also in place of
                # one of earlier parts whose change held it (an update ends the hold, BL-159)
                if self._read_built_against(record) is None or self._impact(record, pin) != fingerprint.UNCHANGED:
                    self._write_built_against(record, self._built_against(
                        {path.name: path for path in paths}, pin, "update", painted=painted))
                return UpdateResult(self._entry(record), paths, [], unchanged=True,
                                    rig=self._rig_gains({path.name: path for path in paths}, pin))
            # Include unchanged parts when a rebuild splits a mod across bundles.
            names = {path.name for path in generated}
            for target in targets:
                if target.name not in names:
                    atomic_copy(self._official_for(target, pin), output / target.name)
                    generated.append(output / target.name)
            built_against = self._built_against({path.name: path for path in generated}, pin, "update",
                                                painted=painted)
            rig = self._rig_gains({path.name: path for path in generated}, pin)
            record, previous = self._publish_update(mod_id, built_from, generated)
            # after the commit: a fingerprint of the files before stays theirs if the update is rolled back
            self._write_built_against(record, built_against)
        return UpdateResult(self._entry(record), self._paths(record), previous, rig=rig)

    @_mod_writer
    def _publish_update(self, mod_id: str, built_from: dict[str, str],
                        generated: list[Path]) -> tuple[dict[str, Any], list[Path]]:
        """Replace a mod's files with its rebuild, atomically per mod. A journal (``import-transactions/<id>``,
        ``operation`` ``update``) names the old and the new files with their hashes and the ``previous/<uuid>`` copy of
        the old ones before any file changes; the record's new file list is the commit point. A failure or an
        interruption before it leaves the old mod whole (``_rollback_update``, also at recovery): its files and file
        list come back, and its id, alias, assignment and repair links never change. Only the newest previous copy is
        kept. Refuses with ``entry_changed`` when the mod's files changed since the rebuild read them."""
        record = self._record(mod_id)
        paths = self._paths(record)
        if {path.name: self._hashes.get(path).sha1 for path in paths} != built_from:
            raise ServiceError("entry_changed", core=core_of(paths[0].name))
        directory = self.root / "mods" / mod_id
        transaction = self.root / "import-transactions" / mod_id
        journal = transaction / "journal.json"
        if transaction.parent.is_symlink() or transaction.exists() or (directory / "previous").is_symlink():
            raise ServiceError("library_io_error", file=transaction)
        new = {path.name: self._hashes.get(path).sha1 for path in generated}
        data = {"version": 1, "operation": "update", "id": mod_id, "phase": "copying", "old": built_from,
                "new": new, "previous": uuid.uuid4().hex}
        previous_dir = directory / "previous" / data["previous"]
        atomic_json(journal, data)
        try:
            previous = []
            for path in paths:
                backup = previous_dir / path.name
                atomic_copy(path, backup)
                verify_file(backup, built_from[path.name])
                previous.append(backup)
            data["phase"] = "replacing"
            atomic_json(journal, data)
            for path in generated:
                atomic_copy(path, directory / path.name)
                verify_file(directory / path.name, new[path.name])
            record["files"] = sorted(new)
            self._save()
        except Exception as error:
            try:
                self._rollback_update(journal, data)
            except Exception as recovery_error:
                log_failure(logger, logging.ERROR, "Mod update rollback pending", recovery_error)
                raise ServiceError("library_recovery_pending", file=journal) from error
            raise
        self._finish_update(journal, data)
        return record, previous

    def _finish_update(self, journal: Path, data: dict) -> None:
        """After the commit: the old files the rebuild does not name go, older previous copies go, the built copies
        and markers follow (``_after_update``), then the journal. Repeating it is safe."""
        directory = self.root / "mods" / data["id"]
        for name in data["old"]:
            if name not in data["new"]:
                (directory / name).unlink(missing_ok=True)
        kept = directory / "previous"
        if kept.is_dir() and not kept.is_symlink():
            for folder in kept.iterdir():
                if folder.name != data["previous"]:
                    if folder.is_dir() and not folder.is_symlink():
                        shutil.rmtree(folder)
                    else:
                        folder.unlink()
        self._after_update(data["id"])
        try:
            shutil.rmtree(journal.parent)
            sync_directory(journal.parent.parent)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Committed update journal retained", error)

    def _rollback_update(self, journal: Path, data: dict) -> None:
        """Put the old mod back: each old file whose bytes are not the journal's comes from the previous copy (verified),
        the new files the old mod does not name go, and the record's file list in state.json is the old one again.
        This attempt's previous copy and the journal go last. Repeating it is safe."""
        mod_id, old = data["id"], data["old"]
        directory = self.root / "mods" / mod_id
        previous_dir = directory / "previous" / data["previous"]
        for name, digest in old.items():
            path = directory / name
            if path.is_file() and not path.is_symlink() and self._hashes.get(path).sha1 == digest:
                continue
            verify_file(previous_dir / name, digest)
            atomic_copy(previous_dir / name, path)
            verify_file(path, digest)
        for name in data["new"]:
            if name not in old:
                (directory / name).unlink(missing_ok=True)
        names = sorted(old)
        record = self._records.get(mod_id)
        if record is not None:
            record["files"] = names
        path = self._state_path
        with file_lock(self.root / "state.json.lock"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = None
            if state is not None:
                if isinstance(state, dict) and isinstance(state.get("version"), (int, float)) \
                        and not isinstance(state["version"], bool) and state["version"] > 1:
                    raise ServiceError("library_state_unreadable", file=path)
                if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("mods"), list):
                    raise ServiceError("library_io_error", file=path)
                stored = [item for item in state["mods"] if isinstance(item, dict) and item.get("id") == mod_id
                          and item.get("files") != names]
                if stored:
                    for item in stored:
                        item["files"] = names
                    try:
                        atomic_copy(path, self._prev_path)
                    except OSError as error:
                        log_failure(logger, logging.WARNING, "Could not keep the previous library state", error)
                    atomic_json(path, state)
            sync_directory(self.root)
        self._seen = None
        self._refresh_state()
        if previous_dir.exists():
            shutil.rmtree(previous_dir)
        shutil.rmtree(journal.parent)
        sync_directory(journal.parent.parent)

    def _recover_update(self, journal: Path, data: dict) -> None:
        """Settle an interrupted update (``_publish_update``): finish it when the record names the new files and each
        holds its new bytes, else roll it back."""
        root = self.root / "import-transactions"
        mod_id, old, new = data["id"], data["old"], data["new"]

        def names_valid(files: Any) -> bool:
            return isinstance(files, dict) and bool(files) and all(
                isinstance(name, str) and Path(name).name == name and name.endswith(".bundle")
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None
                for name, value in files.items())

        if (not isinstance(mod_id, str) or re.fullmatch(r"[0-9a-f]{32}", mod_id) is None
                or journal != root / mod_id / "journal.json" or data["version"] != 1
                or data["phase"] not in ("copying", "replacing") or not names_valid(old) or not names_valid(new)
                or not isinstance(data["previous"], str) or re.fullmatch(r"[0-9a-f]{32}", data["previous"]) is None):
            raise ValueError("Invalid update journal")
        directory = self.root / "mods" / mod_id
        if (directory.parent.is_symlink() or directory.is_symlink() or (directory / "previous").is_symlink()
                or not directory.resolve().is_relative_to(self.root)):
            raise ValueError("Invalid update directory")
        record = self._records.get(mod_id)

        def holds(name: str, digest: str) -> bool:
            path = directory / name
            return path.is_file() and not path.is_symlink() and self._hashes.get(path).sha1 == digest

        if (data["phase"] == "replacing" and record is not None and record.get("files") == sorted(new)
                and all(holds(name, digest) for name, digest in new.items())):
            self._finish_update(journal, data)
        else:
            self._rollback_update(journal, data)

    def _after_update(self, mod_id: str) -> None:
        """A mod's build changed: the built copies of the old build go, and every entry configured with the mod gets the
        updated marker, so the game files still holding the old build are offered Apply Mod Configuration. An earlier
        Keep as Is of that prompt is forgotten: it answered the old build."""
        source, stamp = f"mod:{mod_id}", now()
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            changed = False
            for folder in data["folders"].values():
                for core in [core for core, key in folder["built"].items() if key.get("source") == source]:
                    dropped.append(folder["built"].pop(core))
                    changed = True
                for core, entry in folder["configured"].items():
                    if entry.get("source") == source:
                        folder["updated"][core] = stamp
                        if isinstance(folder["dismissed"].get("drift"), dict):
                            folder["dismissed"]["drift"].pop(core, None)
                        changed = True
            return changed

        with contextlib.suppress(ServiceError):
            self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    @_serialized
    def extract(self, target: str, out_dir: Path, log: LogFunc = no_log, pin: Pin | None = None,
                progress: Callable[[dict], None] | None = None) -> Path:
        """Extract game or mod bundles into a folder named after their core."""
        if target.startswith("mod:"):
            paths = self._paths(self._record(target[4:]))
        elif target.startswith("variant:"):
            paths = self._game_files(target[8:], None, pin)
        else:
            raise ServiceError("source_unsupported", source=target)
        if not paths:
            raise ServiceError("game_file_not_found", target=target)
        name = parse_filename(paths[0].name).core
        # The engine clears the output folder before extracting, so the folder must be a child of out_dir.
        if not is_safe_file_name(name):
            raise ServiceError("mod_unsupported", file=paths[0])
        folder = Path(out_dir).resolve() / name
        folder.mkdir(parents=True, exist_ok=True)
        report = (lambda current, total, _name: progress({"current": current, "total": total,
                                                        "step": "import.step.extract"})) if progress else None
        success, _ = core.process_asset_extraction(paths, folder, spine_options=self.settings.spine_options(False),
                                                  log=log, progress_callback=report)
        if not success:
            raise ServiceError("asset_extraction_failed", target=target)
        return folder

    def _asset_hashes(self, paths: list[Path]) -> dict[tuple[str, str, int], tuple[str, int]]:
        assets = {}
        counts: dict[tuple[str, str], int] = {}
        types = {AssetType.Texture2D, AssetType.TextAsset, AssetType.Mesh, AssetType.AnimationClip}
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path)
            try:
                for obj in bundle.env.objects:
                    if obj.type not in types:
                        continue
                    name = obj.peek_name() or ""
                    key = (name, obj.type.name)
                    occurrence = counts.get(key, 0)
                    counts[key] = occurrence + 1
                    raw = obj.get_raw_data()
                    digest = hashlib.sha1(raw)
                    size = len(raw)
                    # Streamed textures and meshes may change without any change to
                    # their serialized object; include the referenced payload too.
                    data = obj.read()
                    stream = getattr(data, "m_StreamData", None)
                    if stream and stream.path and stream.size:
                        payload = get_resource_data(stream.path, obj.assets_file, stream.offset, stream.size)
                        digest.update(payload)
                        size += len(payload)
                    assets[(*key, occurrence)] = (digest.hexdigest(), size)
            except (ValueError, EOFError) as error:
                raise ServiceError("bundle_invalid", file=path) from error
        return assets

    @_serialized
    def compare(self, mod_id: str, pin: Pin | None = None) -> CompareResult:
        """Hash raw assets against the current official files, even when a mod is installed."""
        pin = self._current(pin)
        record = self._record(mod_id)
        targets = self._plan_targets(self._plan(record, no_log, pin))
        baseline = [self._official_for(path, pin) for path in targets]
        return self._compare_paths(mod_id, self._paths(record), baseline)

    def _compare_paths(self, mod_id: str, paths: list[Path], baseline: list[Path]) -> CompareResult:
        mod = self._asset_hashes(paths)
        official = self._asset_hashes(baseline)
        differences = []
        for key in sorted(mod.keys() | official.keys()):
            left, right = mod.get(key), official.get(key)
            status = "removed" if left is None else "added" if right is None else (
                "same" if left[0] == right[0] else "changed")
            differences.append(AssetDifference(key[0], key[1], status, left[1] if left else None,
                                               right[1] if right else None))
        return CompareResult(mod_id, differences)


def _merge(base: dict, mine: dict, disk: dict) -> dict:
    """This instance's changes since ``base`` applied to ``disk``: keys it added or changed take its value, keys it
    removed go; the rest is as the file holds it (this instance's own objects where they are equal)."""
    out = {key: mine[key] if key in mine and mine[key] == value else value for key, value in disk.items()}
    for key in base.keys() | mine.keys():
        if key in mine:
            if key not in base or mine[key] != base[key]:
                out[key] = mine[key]
        else:
            out.pop(key, None)
    return out


def _combine(kind: str, base: tuple, mine: tuple, disk: tuple) -> tuple[dict, set, dict, set]:
    """This instance's changes since ``base`` (``mine``) on the state read from disk: (records, favourites, applied,
    migrations). The previous copy of an unreadable state.json (``kind`` ``prev``) is older than what this instance
    holds, so it adds to ``mine`` and drops nothing but what this instance removed since ``base``."""
    if kind == "prev":
        records, applied = {**disk[0], **mine[0]}, {**disk[2], **mine[2]}
        for key in base[0].keys() - mine[0].keys():
            records.pop(key, None)
        for key in base[2].keys() - mine[2].keys():
            applied.pop(key, None)
        return records, (set(disk[1]) | mine[1]) - (base[1] - mine[1]), applied, mine[3] | disk[3]
    return (_merge(base[0], mine[0], disk[0]), (set(disk[1]) | (mine[1] - base[1])) - (base[1] - mine[1]),
            _merge(base[2], mine[2], disk[2]), mine[3] | disk[3])


def _drop_source(data: dict, source: str, keys: list[dict]) -> list[tuple[str, str]]:
    """Drop every configuration naming a source, with its markers and built copies (collected in ``keys``);
    returns the entries."""
    dropped = []
    for key, folder in data["folders"].items():
        for core in [core for core, entry in folder["configured"].items() if entry.get("source") == source]:
            del folder["configured"][core]
            folder["updated"].pop(core, None)
            dropped.append((key, core))
        for core in [core for core, entry in folder["built"].items() if entry.get("source") == source]:
            keys.append(folder["built"].pop(core))
            if (key, core) not in dropped:
                dropped.append((key, core))
    return dropped


def _pins_signature(pins: list[Pin]) -> list[tuple[str, tuple[str, ...]]]:
    return sorted((pin.key, tuple(str(folder) for folder in pin.dirs)) for pin in pins)


@functools.cache
class _ChoiceChange:
    """One apply's change of saved choices. The choice is saved before the build (a failed build shows its error on
    it); a write then refused for a reason of the moment (STOPPING: the game running, Steam busy, the classic UI open,
    the installs changed) puts the cores' entries back as they were, and the built copies the new choice replaced are
    deleted only once it stands (``close``)."""

    def __init__(self, library: "ModLibrary", pin: Pin, variant_key: str, source: str):
        self.library, self.pin, self.variant_key, self.source = library, pin, variant_key, source
        self.saved: dict[str, dict[str, Any]] = {}
        self.dropped: list[dict] = []

    def configure(self, kinds: dict[str, str]) -> None:
        for core, parts in self.library._saved_choices(self.pin, kinds).items():
            self.saved.setdefault(core, parts)
        self.library._configure(self.pin, self.variant_key, kinds, self.source, dropped=self.dropped)

    def refused(self, error: BaseException) -> bool:
        """Whether ``error`` is a refusal of the moment; the saved entries are put back then."""
        if not (isinstance(error, ServiceError) and error.code in STOPPING):
            return False
        if self.saved:
            self.library._put_back_choices(self.pin, self.saved)
        return True

    def close(self) -> None:
        """Delete the replaced built copies no saved key references (none, after a put back)."""
        if not self.dropped:
            return
        dropped, self.dropped = self.dropped, []
        try:
            self.library._update_profiles(lambda data: False,
                                          after=lambda data: self.library._delete_built_files(dropped, data))
        except Exception as error:  # the next prune deletes what is left
            log_failure(logger, logging.WARNING, "Could not delete replaced built copies", error)


def _build_version() -> str:
    """The program build: a cached build is never reused across versions."""
    from bmpt.engine import get_version_info

    info = get_version_info()
    return f"{info.get('version')}:{info.get('commit_hash')}"


def _folder_size(directory: Path) -> int:
    total = 0
    if directory.is_dir():
        for path in directory.glob("*.bundle"):
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total
