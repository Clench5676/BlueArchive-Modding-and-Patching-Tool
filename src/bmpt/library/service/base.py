"""What the library's modules share: the lock and its decorators, the view of the game folders, the helpers."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Mapping

from bmpt.core import devmode, preview_gate, spans
from bmpt.engine import (
    SEARCH_DIR_SUFFIXES,
    parse_filename,
)
from ...core.errors import ServiceError
from ...core.filelock import file_lock
from ...gameassets.cabs import CabIndex
from ...core.logging_utils import log_failure
from .. import profiles as profiles_mod
from .. import integrity, mod_progress
from ..judge import (
    CRC_STATES,
    LIBRARY_SOURCES,
    carried_of,
    classify,
    core_of,
    date_of,
    kind_of_file,
    now,
    part_of,
    says_official,
    seconds,
)
from ..models import Pin
from ..storage import (
    atomic_json,
    read_tail,
    trailing_bytes,
)

if TYPE_CHECKING:
    from .library import ModLibrary


logger = logging.getLogger(__package__)


SPACE_MARGIN = 512 * 1024 * 1024  # left free on the library's volume by an Off and an adoption
SAVE_EVERY = (10, 2.0)  # a toggle saves its unfinished list every 10 entries or 2 s
BUILT_TEMP_MAX_AGE = 60 * 60  # seconds before an abandoned built copy can be cleared
COSTUME_PREVIEWS = 4  # costume previews kept in built/previews (one folder per build signature)
# times a costume preview is built again when its build signature changed while it was built (costume_preview)
COSTUME_BUILD_TRIES = 3
POINTER_CACHE = 256  # files whose cross-bundle pointers, and game files whose objects, are kept in memory
COSTUME_VERDICTS = 4000  # costume verdicts kept in costume-verdicts.json, newest first
WORN_VERDICTS = 1000  # models' worn-item verdicts kept in worn-items.json, newest first
WORN_LOCK_WAIT = 2.0  # seconds a build waits for worn-items.json's lock before it leaves the verdict unsaved
# the startup rig archive (archive_rigs) works only once no other caller used the library for this long
RIG_ARCHIVE_QUIET = 1.0
# between two entries a toggle lets other callers of the library go first: it waits until none of them used the lock
# for TOGGLE_YIELD[0] seconds, at most TOGGLE_YIELD[1] seconds per entry (a preview's file requests would otherwise
# get one library call in per entry)
TOGGLE_YIELD = (0.1, 0.5)
# between two steps of a job that holds the library for its whole run (CRC repair, prune), a caller waiting for the lock
# (a preview, a page read) gets it first: the job gives the lock up until that caller has it, at most this long
STEP_ASIDE = 0.5
RECOVERY_RETRY = 60.0  # seconds before a read tries a recovery that failed (or waits) again, if the journals stay
# a toggle's refusals that stop it: every later write would fail the same way
STOPPING = ("game_running", "game_status_unavailable", "steam_busy", "folder_changed", "classic_open", "untested_build")
# refusals of the moment: a costume build of an older rule the start could not build again is tried at the next one
LATER = (*STOPPING, "busy", "entry_changed", "library_state_unreadable", "library_io_error")


def _app_version() -> str:
    """This build's version (``updater.current_version``)."""
    from ...host.updater import current_version

    return current_version()


def _game_folder(game_file: Path) -> Path:
    """Name the game folder a game file is in, as the settings name it.

    That is its resource folder less the subfolder the search looks in (searching.SEARCH_DIR_SUFFIXES).
    """
    folder = game_file.parent
    for suffix in sorted(filter(None, SEARCH_DIR_SUFFIXES), key=len, reverse=True):
        parts = tuple(part.lower() for part in Path(suffix).parts)
        if len(folder.parts) > len(parts) and tuple(part.lower() for part in folder.parts[-len(parts) :]) == parts:
            return Path(*folder.parts[: -len(parts)])
    return folder


# set on a thread while it does background work that steps aside for everyone else (archive_rigs): ``on``; while it
# runs a long job that lets other callers in between its steps (a toggle): ``paced``
_background = threading.local()


class _LibraryLock:
    """The library's re-entrant lock, which notes when callers last took or gave it back.

    It notes when any caller but background work (``_background``) last took or gave it back: what background work steps
    aside for (``ModLibrary._yield_to_foreground``); and when any caller but background work and a paced job did: what a
    paced job lets in between its steps (``ModLibrary._let_others_in``).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.used = 0.0  # time.monotonic() of the last foreground acquire or release
        self.asked = 0.0  # the same, less the paced job's own
        self.waiting = 0  # callers but background work and a paced job blocked on the lock right now
        self._count = threading.Lock()
        # told True when such a caller starts waiting with nobody else waiting, False when the last stops: the
        # page's preview deadlines do not count that time (``library.waiting``)
        self.on_wait: Callable[[bool], None] | None = None
        self._wait_turn = 0  # the transitions of ``waiting`` in order; a late one is not told
        self._told = 0
        self._telling = threading.Lock()

    def _waited(self, turn: int, busy: bool) -> None:
        hook = self.on_wait
        if hook is None:
            return
        with self._telling:
            if turn <= self._told:
                return  # a later transition was told already
            self._told = turn
            try:
                hook(busy)
            except Exception:  # a listener never fails a library call
                logger.debug("library wait listener failed", exc_info=True)

    def _note(self) -> None:
        if not getattr(_background, "on", False):
            self.used = time.monotonic()
            if not getattr(_background, "paced", False):
                self.asked = self.used

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        self._note()
        if self._lock.acquire(False):
            return True
        if not blocking:
            return False
        counted = not (getattr(_background, "on", False) or getattr(_background, "paced", False))
        if counted:
            with self._count:
                self.waiting += 1
                first = self.waiting == 1
                if first:
                    self._wait_turn += 1
                    turn = self._wait_turn
            if first:
                self._waited(turn, True)
        try:
            return self._lock.acquire(True, timeout)
        finally:
            if counted:
                with self._count:
                    self.waiting -= 1
                    last = self.waiting == 0
                    if last:
                        self._wait_turn += 1
                        turn = self._wait_turn
                if last:
                    self._waited(turn, False)
            self._note()

    def release(self) -> None:
        self._lock.release()
        self._note()

    def step_aside(self, longest: float = STEP_ASIDE) -> float:
        """Give up the lock between two steps of a job while another caller waits for it.

        Between two steps of a job that holds the lock (however often it took it): when a caller waits for it, give the
        lock up whole until that caller has it, at most ``longest`` seconds, then take it back, blocking while that
        caller works.

        Returns:
            How long it waited; nothing when nobody waits or this thread does not hold the lock.
        """
        release = getattr(self._lock, "_release_save", None)
        restore = getattr(self._lock, "_acquire_restore", None)
        owned = getattr(self._lock, "_is_owned", None)
        if not self.waiting or release is None or restore is None or owned is None or not owned():
            return 0.0
        started = time.monotonic()
        state = release()
        try:
            while self.waiting and time.monotonic() - started < longest:
                time.sleep(0.005)
        finally:
            restore(state)
        return time.monotonic() - started

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _serialized(method: Callable) -> Callable:
    """Run a public method under the library lock, after bringing the state up to date.

    A pending journal is recovered if it can be, and never fails the call: reads work, and a write is refused by its own
    gates (``ModLibrary._check_recovery``) with the real failure.
    """

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
        log_failure(
            logger, logging.ERROR, f"Library operation failed ({operation})", error, code="library_operation_failed"
        )
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
    held records); ``error`` is set for ``corrupt``, ``newer`` and ``io``.
    """

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
    """Pick the held records to write after a merge.

    They are the disk copy's, less those whose id is among the merged ``records`` and those this instance removed since
    ``base`` (in ``base``, not in ``mine``). A record without a usable id is always kept: nothing can say it was
    removed.
    """
    removed = base.keys() - mine.keys()
    kept = []
    for raw in disk_held:
        mod_id = _held_id(raw)
        if mod_id is None or (mod_id not in records and mod_id not in removed):
            kept.append(raw)
    return kept


@dataclass
class _CostumeInputs:
    """What a costume build reads of the library, gathered under the library lock.

    ``ModLibrary._costume_inputs`` gathers it so the build itself can run without the lock (``costume_preview``). It
    holds the bytes to stage for each part of the model slot, the source model's files, the CAB index, the slot's dev
    name, and the rule that gives the official copy of a dependency the build finds (``_official_or_current``: not
    serialized, as for the preview reads).
    """

    originals: dict[Path, Path]  # the slot's game files -> the official (else current) copy staged for it
    dev: str  # the source model's dev name
    chosen: set[str]  # the names of the source's files that hold its prefab
    sources: list[Path]  # the official (else current, for a preview) copies of the source model's files
    cabs: CabIndex
    target_dev: str
    official: Callable[[Path], Path]
    revisions: dict[str, str] = field(default_factory=dict)  # dev name -> its model's game revision (_revision)


def _revision(files: Iterable[tuple[Path, Path]]) -> str:
    """Return a model's game revision.

    It is the names of its game files (an official name holds its CRC) and the sizes of the copies read for them.
    """
    return hashlib.sha1(repr(sorted((game.name, read.stat().st_size) for game, read in files)).encode()).hexdigest()


def _item_fraction(
    progress: Callable[..., None] | None, current: int = 1, total: int = 1, build: float = 0.9
) -> Callable[[float], None] | None:
    """Report how far a costume build is as the job's progress of its item ``current`` of ``total``.

    The build's own ``fraction`` comes from ``_stage_costume``. The progress is {"current", "total", "step",
    "fraction"}, with the build's phases as steps (``mod_progress.single``). ``build`` is the item's share the build
    fills, the rest being for a write into the game files after it (1 for a build the library keeps). None without a
    job's progress.
    """
    return mod_progress.single(progress, current, total, build)


class _WornVerdicts:
    """The worn-item verdict of each model a costume build measures (``model_props.WornStore``).

    Verdicts are kept in worn-items.json beside the costume verdicts, keyed by the costume rule's version, the model and
    its game revision. Builds run outside the library and model locks, two at once at times: a write reads, changes and
    writes the file under a lock of its own (this process's and the lock file's, for other processes), and the atomic
    write keeps every read whole. A file that cannot be read or written, or a lock not had within ``WORN_LOCK_WAIT``,
    only means the model is measured again.
    """

    _lock = threading.Lock()

    def __init__(self, path: Path, revisions: Mapping[str, str]):
        self.path, self.revisions = path, dict(revisions)

    def _key(self, dev: str) -> str | None:
        from ...gameassets.costume_rule import COSTUME_RULE

        revision = self.revisions.get(dev)
        return f"{COSTUME_RULE}|{dev}|{revision}" if revision else None

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        models = data.get("models") if isinstance(data, dict) and data.get("version") == 1 else None
        return {k: v for k, v in (models or {}).items() if isinstance(k, str) and isinstance(v, dict)}

    def get(self, dev: str) -> Mapping | None:
        key = self._key(dev)
        entry = self._read().get(key) if key else None
        worn = entry.get("worn") if entry else None
        return worn if isinstance(worn, dict) else None

    def put(self, dev: str, verdict: Mapping) -> None:
        key = self._key(dev)
        if key is None:
            return
        try:
            with self._lock, file_lock(self.path.with_name(self.path.name + ".lock"), timeout=WORN_LOCK_WAIT):
                models = self._read()
                models[key] = {"worn": verdict, "at": now()}
                kept = dict(
                    sorted(models.items(), key=lambda item: str(item[1].get("at", "")), reverse=True)[:WORN_VERDICTS]
                )
                atomic_json(self.path, {"version": 1, "models": kept})
        except (OSError, TimeoutError) as error:
            log_failure(logger, logging.WARNING, "Could not save a worn-item verdict", error)


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
    """Find the file that a game update rebuilt ``path`` as.

    It is the one other file of its group (its name less the CRC) in its folder. An update rebuilds a changed group in
    place, keeping its name and date and changing only the CRC. None when no other file or several have that name
    (groups packed one asset per bundle share it).
    """
    if not parse_filename(path.name).crc:
        return None
    key = integrity.replacement_key(path.name)
    rebuilt = [
        other
        for other in index.get(core_of(path.name), [])
        if other.parent == path.parent and other.name != path.name and integrity.replacement_key(other.name) == key
    ]
    return rebuilt[0] if len(rebuilt) == 1 else None


def _has_successor(index: dict[str, list[Path]], path: Path) -> bool:
    """Tell whether the file that replaced ``path`` in a game update is in its folder in an install's index.

    That file is the group rebuilt under a new CRC (``_rebuilt``), or a file of the same part dated after it (an update
    that renamed it).
    """
    if _rebuilt(index, path) is not None:
        return True
    part, date = part_of(path.name), date_of(path.name)
    return bool(date) and any(
        other.parent == path.parent and part_of(other.name) == part and date_of(other.name) > date
        for other in index.get(core_of(path.name), [])
    )


class _View:
    """A consistent copy of what the judgement reads besides the game files.

    That is the library's records, its mods, the configuration, the indexes and the kept originals. ``allow`` says which
    game files it may read:

    - ``scan``: headers and tails of any file (the scan consent is given);
    - ``family``: the same, for the files the detail screen shows (no consent needed);
    - ``recorded``: only the files the library wrote to (no consent); another file raises _Unread;
    - ``full``: write strength, every file the library cannot show unchanged is hashed in full (switching).
    """

    def __init__(
        self, library: ModLibrary, pins: list[Pin], config: dict | None, allow: str, snapshot: bool = False
    ) -> None:
        """Collect what the judgement reads besides the game files.

        ``snapshot``: copy the records, for a view read outside the library lock (the scan). A view used under the lock
        shares the library's records and the maps built from them.
        """
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

    def folder(self, key: str) -> dict:
        return ((self.config or {}).get("folders") or {}).get(key) or {}

    def configured(self, key: str, core: str) -> dict | None:
        return (self.folder(key).get("configured") or {}).get(core)

    def official_size(self, name: str) -> int | None:
        """Give the size of the official file of a name, where the library knows it by stat alone.

        That is a kept original of that name, else a record of that name, in any folder, that says official bytes. The
        CRC is in the name, so every official file of that name has those bytes.
        """
        if name not in self._sizes:
            steam = self.library._steam_official(name, roots=[pin.root for pin in self.pins.values()])
            size = steam.size if steam is not None else self.originals.get(name)
            if size is None:
                size = next(
                    (
                        record.get("size")
                        for _path, record in self.by_name.get(name, [])
                        if says_official(record) and isinstance(record.get("size"), int)
                    ),
                    None,
                )
            self._sizes[name] = size
        return self._sizes[name]

    def source_exists(self, source: str, key: str, kind: str, *, held: bool = True) -> bool:
        """Tell whether the library has a source.

        A held record (kept in state.json but not usable now) counts: its configuration and the game files carrying it
        are not those of a source the library lost. ``held=False`` asks only for a usable record (whether the library
        has the source's bytes).
        """
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

    def _header(self, path: Path, recorded: bool) -> int | None:
        if self.allow == "recorded" and not recorded:
            raise _Unread(path)
        return trailing_bytes(path)

    def official_now(self, path: Path, size: int, trailing: int | None, name: str | None = None) -> bool:
        """Tell whether a file holds official bytes.

        At write strength that is decided by the CRC32 of its whole content; else by the cheap rule, no trailing bytes
        and the official size of its name where the library knows it.
        """
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
                return _Part(path, size, mtime, "damaged", False, False, record is not None, None, "needs_fix", record)
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
                # Preserve bytes of a missing source by offering them for adoption.
                carried = "official" if flag else "unknown"
            else:
                carried = source
            crc = record.get("crc")
            if crc not in CRC_STATES:
                if trailing is ...:
                    trailing = self._header(path, True)
                crc = "fixed" if trailing else "ok"
            return _Part(
                path,
                size,
                mtime,
                carried,
                flag,
                True,
                False,
                None if trailing is ... else trailing,
                crc,
                record,
                restamp,
            )
        official = self.official_now(path, size, trailing)
        if official and self.allow != "full" and _outside(record, size, mtime):
            # a full judgement found these very bytes changed outside the library: the cheap rule cannot tell
            official = False
        return _Part(
            path,
            size,
            mtime,
            "official" if official else "unknown",
            official,
            False,
            record is not None,
            trailing,
            "fixed" if trailing else "ok",
            record,
        )

    def says_nonofficial(self, path: Path, record: dict, present: bool) -> bool:
        """Tell whether a record says the library wrote non-official bytes there.

        A record of an older build without the flag says so when its size is not the official size of its name, or (its
        file gone) an original of that name is kept.
        """
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
        """Tell whether the library recorded the file that replaced ``path`` in its folder (it wrote the successor).

        The successor is the group rebuilt under a new CRC (``_rebuilt``; only with ``pin``) or a later file of the same
        part.
        """
        records = self.by_folder.get(path.parent, [])
        rebuilt = _rebuilt(self.indexes[pin.key], path) if pin is not None else None
        if rebuilt is not None and any(other == rebuilt for other, _record in records):
            return True
        part, date = part_of(path.name), date_of(path.name)
        return any(part_of(other.name) == part and date_of(other.name) > date for other, _record in records)

    def copy_of(self, part: _Part, pin: Pin) -> str | None:
        """Find where an official copy of a game file is, judged cheaply.

        The candidates are the library's kept original, the file itself, its .backup, a file of its name in another game
        folder; None when there is none (or it may not be looked for). Candidates beyond the file are looked at only for
        a file that is not official and has no original.
        """
        name = part.path.name
        # a kept original a Steam list rejected is no official copy: the other candidates are looked at
        if (
            name in self.originals
            and self.library._original_checks.of_file(self.library.root / "originals" / name) != "failed"
        ):
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

    def entry(self, pin: Pin, core: str) -> _Entry:
        core = self.maps[pin.key].slot(core)
        kind = self.library._kind(core, pin) or "spr"
        files = [path for value in self.maps[pin.key].slot_cores(core) for path in self.indexes[pin.key].get(value, [])]
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
        written = max(
            (
                seconds(part.record.get("at")) or seconds(part.record.get("mtime")) or 0.0
                for part in parts
                if part.carried == carried and part.record
            ),
            default=None,
        )
        toggled = seconds((self.config or {}).get("toggled"))
        marker = core in (folder.get("updated") or {})

        def judged(source: str | None) -> tuple[str, str | None]:
            return classify(
                self.enabled,
                source,
                carried,
                files=bool(files),
                damaged=damaged,
                configured_exists=self.source_exists(source, pin.key, kind) if source else True,
                carried_exists=self.source_exists(carried, pin.key, kind)
                if carried.startswith(LIBRARY_SOURCES)
                else True,
                drifted_present=drifted_present,
                drifted_gone=drifted_gone,
                marker=marker,
                written=written,
                since=seconds((config or {}).get("since")),
                toggled=toggled,
            )

        state, takeover = judged(configured)
        if state == "takeover":
            configured = takeover
            state, _ = classify(
                self.enabled,
                takeover,
                carried,
                files=bool(files),
                drifted_present=drifted_present,
                drifted_gone=drifted_gone,
                marker=marker,
            )
        copies = {str(part.path): self.copy_of(part, pin) for part in parts}
        overwritten = (
            any(not part.official and part.carried != "damaged" and copies[str(part.path)] is None for part in parts)
            and self.allow != "recorded"
        )
        variant = self.maps[pin.key].key(core) or (config or {}).get("variant") or core
        if kind == "other" and files:
            variant = parse_filename(files[0].name).core
        entry = _Entry(
            pin,
            core,
            kind,
            variant,
            parts,
            carried,
            configured,
            state,
            takeover,
            drifted_present or drifted_gone or marker,
            overwritten,
            copies,
        )
        entry.dismissed = self.dismissed(entry)
        return entry

    def dismissed(self, entry: _Entry) -> bool:
        kind = "drift" if entry.state in ("reset", "updated") else "untracked"
        stored = ((self.folder(entry.pin.key).get("dismissed") or {}).get(kind) or {}).get(entry.core)
        return bool(stored) and sorted(map(tuple, stored)) == signature(entry.parts)

    def cores(self, pin: Pin) -> set[str]:
        """List every core of an install the judgement covers.

        These are the portrait and lobby cores of its index, the configured ones and the ones the library recorded files
        of.
        """
        cores = {core for core in self.indexes[pin.key] if self.library._kind(core, pin)}
        cores.update(self.folder(pin.key).get("configured") or {})
        for folder in pin.dirs:
            cores.update(
                core_of(path.name) for path, _record in self.by_folder.get(folder, []) if kind_of_file(path.name)
            )
        return {self.maps[pin.key].slot(core) for core in cores}

    def entries(
        self,
        pins: list[Pin],
        cancel: Callable[[], bool] | None = None,
        cores: set[str] | None = None,
        *,
        background: bool = False,
    ) -> list[_Entry]:
        """List every entry that is configured, not in its intended state, or overwritten.

        A ``background`` pass (the scan, outside the library lock) pauses while a model preview is exported; callers
        holding the library lock never pause, since a preview may need that lock.
        """
        out = []
        for pin in pins:
            wanted = self.cores(pin)
            if cores is not None:
                wanted &= {self.maps[pin.key].slot(core_name) for core_name in cores}
            for core_name in sorted(wanted):
                if background:
                    preview_gate.yield_to_previews(cancel)
                if cancel and cancel():
                    return out
                try:
                    entry = self.entry(pin, core_name)
                except (OSError, _Unread):
                    continue
                if entry.configured or entry.state != "ok" or entry.overwritten:
                    out.append(entry)
        return out


def _reasons(failures: list[tuple[str, str]]) -> str:
    """The build failures of ``install_staged`` as one line, for the error and the log."""
    return "; ".join(f"{core}: {reason}" for core, reason in failures)


def _outside(record: dict | None, size: int, mtime: int) -> bool:
    """Tell whether a record remembers the full judgement's verdict on the file as it is now.

    ``outside`` is its size and modification time when a switch found it changed outside the library, though the cheap
    rule calls it official.
    """
    mark = (record or {}).get("outside")
    return isinstance(mark, list) and len(mark) == 2 and mark[0] == size and mark[1] == mtime


def signature(parts: list[_Part]) -> list[tuple[str, int, int]]:
    """Sort an entry's parts by filename, byte size, and nanosecond mtime for dismissal matching."""
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


def _merge(base: dict, mine: dict, disk: dict) -> dict:
    """Apply this instance's changes since ``base`` to ``disk``.

    Keys it added or changed take its value, keys it removed go; the rest is as the file holds it (this instance's own
    objects where they are equal).
    """
    out = {key: mine[key] if key in mine and mine[key] == value else value for key, value in disk.items()}
    for key in base.keys() | mine.keys():
        if key in mine:
            if key not in base or mine[key] != base[key]:
                out[key] = mine[key]
        else:
            out.pop(key, None)
    return out


def _combine(kind: str, base: tuple, mine: tuple, disk: tuple) -> tuple[dict, set, dict, set]:
    """Apply this instance's changes since ``base`` (``mine``) to the state read from disk.

    Returns (records, favourites, applied, migrations). The previous copy of an unreadable state.json (``kind``
    ``prev``) is older than what this instance holds, so it adds to ``mine`` and drops nothing but what this instance
    removed since ``base``.
    """
    if kind == "prev":
        records, applied = {**disk[0], **mine[0]}, {**disk[2], **mine[2]}
        for key in base[0].keys() - mine[0].keys():
            records.pop(key, None)
        for key in base[2].keys() - mine[2].keys():
            applied.pop(key, None)
        return records, (set(disk[1]) | mine[1]) - (base[1] - mine[1]), applied, mine[3] | disk[3]
    return (
        _merge(base[0], mine[0], disk[0]),
        (set(disk[1]) | (mine[1] - base[1])) - (base[1] - mine[1]),
        _merge(base[2], mine[2], disk[2]),
        mine[3] | disk[3],
    )


def _drop_source(data: dict, source: str, keys: list[dict]) -> list[tuple[str, str]]:
    """Drop every configuration naming a source, with its markers and built copies.

    The built copies are collected in ``keys``. Returns the entries.
    """
    dropped = []
    for key, folder in data["folders"].items():
        for core_name in [
            core_name for core_name, entry in folder["configured"].items() if entry.get("source") == source
        ]:
            del folder["configured"][core_name]
            folder["updated"].pop(core_name, None)
            dropped.append((key, core_name))
        for core_name in [core_name for core_name, entry in folder["built"].items() if entry.get("source") == source]:
            keys.append(folder["built"].pop(core_name))
            if (key, core_name) not in dropped:
                dropped.append((key, core_name))
    return dropped


def _pins_signature(pins: list[Pin]) -> list[tuple[str, tuple[str, ...]]]:
    return sorted((pin.key, tuple(str(folder) for folder in pin.dirs)) for pin in pins)


@functools.cache
class _ChoiceChange:
    """One apply's change of saved choices.

    The choice is saved before the build (a failed build shows its error on it); a write then refused for a reason of
    the moment (STOPPING: the game running, Steam busy, the classic UI open, the installs changed) puts the cores'
    entries back as they were, and the built copies the new choice replaced are deleted only once it stands (``close``).
    """

    def __init__(self, library: "ModLibrary", pin: Pin, variant_key: str, source: str):
        self.library, self.pin, self.variant_key, self.source = library, pin, variant_key, source
        self.saved: dict[str, dict[str, Any]] = {}
        self.dropped: list[dict] = []

    def configure(self, kinds: dict[str, str]) -> None:
        for core_name, parts in self.library._saved_choices(self.pin, kinds).items():
            self.saved.setdefault(core_name, parts)
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
            self.library._update_profiles(
                lambda data: False, after=lambda data: self.library._delete_built_files(dropped, data)
            )
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
