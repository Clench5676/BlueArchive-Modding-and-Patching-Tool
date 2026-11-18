"""Builds and holds the services behind the web UI, and answers /asset/... requests."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from ..host import steam_records
from ..core import devmode, preview_gate, spans
from ..host.data_upgrade import DataUpgrade, UpgradeBlocked
from ..host.store_formats import InvalidStore
from ..host.config import APP_DIR_NAME, Settings, managed_cache_dir
from ..host.data_paths import OLD_APP_DIR_NAME, legacy_cache_path
from ..host.addons import character_map_path
from ..lines.global_lines import GlobalLines
from ..lines.kivo import Kivo
from ..core.errors import ServiceError
from bmpt.engine import EXE_DIR, i18n_manager
from ..core.logging_utils import log_failure
from .classic.native import NativeOps, native_ops
from .classic.queued import ClassicQueue
from .classic.window import ClassicWindow
from ..library.relocate import relocate
from .events import EventBus
from .jobs import MAINTENANCE, JobRunner
from .scanner import Scanner
from .timing import Timings
from .state import UiState
from .strings import install_label, ui_strings
from .services_assets import AssetsMixin
from .services_jobs import JobsMixin
from .services_window import WindowMixin


logger = logging.getLogger(__name__)
# the library's files and folders a library move takes along; the lock files are made again where needed
LIBRARY_ITEMS = (
    "mods",
    "originals",
    "built",
    "transactions",
    "import-transactions",
    "asset-deletions",
    "photographs",
    "state.json",
    "state.json.prev",
    "state.json.bad",
    "profiles.json",
    "profiles.json.prev",
    "profiles.json.bad",
)


# errno values of a write refused by permissions or a read-only volume
WRITE_DENIED = (errno.EACCES, errno.EPERM, errno.EROFS)


def folder_writable(folder: Path) -> bool:
    """Whether files can be created in a folder: a real temporary file is written and removed again."""
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".write-test-", suffix=".tmp", dir=folder)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except OSError:
        return False
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


class ModsSnapshot:
    """What ``mods.state`` answers from, never through the library lock.

    It holds the library's status (the known installs, the shared intent, the disk use; ``ModLibrary.status``), the
    last scan's entries and a running toggle's progress. GameWatch, the scanner and the jobs update it. Its lock is
    a leaf lock.
    """

    def __init__(self) -> None:
        """Start with nothing known."""
        self._lock = threading.Lock()
        self._status: dict[str, Any] | None = None
        self._entries: list[Any] = []
        self._scanned: str | None = None
        self._progress: dict[str, int] | None = None

    def status(self) -> dict[str, Any] | None:
        """Return the library's status as last read; None before the first read."""
        with self._lock:
            return self._status

    def set_status(self, status: dict[str, Any] | None) -> None:
        """Keep the library's status as the last one read."""
        with self._lock:
            self._status = status

    def entries(self) -> list[Any]:
        """Return a copy of the entries of the last scan."""
        with self._lock:
            return list(self._entries)

    def scanned(self) -> str | None:
        """Return when the entries were scanned, as a UTC ISO time; None while there are none."""
        with self._lock:
            return self._scanned

    def set_entries(self, entries: list[Any], scanned: str | None) -> None:
        """Keep the entries of a scan and the time it ended."""
        with self._lock:
            self._entries, self._scanned = list(entries), scanned

    def clear_entries(self) -> None:
        """Forget the entries and the scan time."""
        with self._lock:
            self._entries, self._scanned = [], None

    def drop_entries(self, keys: set[tuple[str, str]], cores: set[str] = frozenset()) -> None:
        """Leave out the entries a job switched or adopted, until the scan after it.

        Args:
            keys: The entries as ``(install key, core)``.
            cores: Cores whose entries go in any install.
        """
        if not keys and not cores:
            return
        with self._lock:
            self._entries = [e for e in self._entries if (e.install, e.core) not in keys and e.core not in cores]

    def mark_dismissed(self, keys: set[tuple[str, str]]) -> None:
        """Mark entries as dismissed.

        Args:
            keys: The entries as ``(install key, core)``.
        """
        from dataclasses import replace

        with self._lock:
            self._entries = [replace(e, dismissed=True) if (e.install, e.core) in keys else e for e in self._entries]

    def progress(self) -> dict[str, int] | None:
        """Return a running toggle's progress, ``{done, total}``; None when none runs."""
        with self._lock:
            return self._progress

    def set_progress(self, progress: dict[str, int] | None) -> None:
        """Keep a toggle's progress; None when it ended."""
        with self._lock:
            self._progress = progress


class _WindowOps(NativeOps):
    """The classic window's native operations on the window the services hold at the call.

    pywebview makes the window after the services are built; there is none in browser and serve mode.
    """

    def __init__(self, services: Services):
        self._services = services

    def _ops(self) -> NativeOps:
        return native_ops(self._services.window)

    def place(self, pid: int | None, hwnd: int, size: tuple[int, int]) -> str | None:
        return self._ops().place(pid, hwnd, size)

    def before_raise(self, pid: int | None) -> None:
        self._ops().before_raise(pid)

    def raise_process(self, proc: Any) -> None:
        self._ops().raise_process(proc)


def _intent_key(status: dict[str, Any] | None) -> tuple:
    if not status:
        return ()
    unfinished = status.get("unfinished") or {}
    return (status.get("enabled"), status.get("toggled"), tuple(sorted(unfinished.items())), status.get("reason"))


class Services(JobsMixin, WindowMixin, AssetsMixin):
    """The web UI's service container.

    Game-backed services are built on first use and rebuilt after a game folder change; tests pass fakes for any
    of them.

    In dev mode (devmode) the data directory, the library and the cache must lie inside the dev roots, and moving
    the library or the cache out of them is refused.

    Besides the services, it holds what the Enable Mods switch and the classic hand-off need: the ``mods.state``
    snapshot, the scanner (only with the scan consent), the game watch (started by ``start()``), the classic window
    and the queue of the writes waiting for it to close.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        data_dir: Path = EXE_DIR,
        *,
        events: EventBus | None = None,
        jobs: JobRunner | None = None,
        state: UiState | None = None,
        cache: Any = None,
        game: Any = None,
        catalog: Any = None,
        library: Any = None,
        classic_options: dict[str, Any] | None = None,
        safe_mode: bool = False,
        upgrade: DataUpgrade | None = None,
    ):
        """Create the container; game-backed services are built when first used.

        Args:
            settings: The classic settings (config.toml); None loads them.
            data_dir: The data directory: webui.json, the logs and, by default, the mod library are in it.
            events: The event bus; None creates one.
            jobs: The job runner; None creates one on ``events``.
            state: The UI state; None reads webui.json in ``data_dir``.
            cache: An asset cache to use instead of the consented one in the cache folder; for tests.
            game: Game assets to use instead of the ones built from the settings; for tests.
            catalog: A catalog to use instead of the built one; for tests.
            library: A mod library to use instead of the built one; for tests.
            classic_options: Options of the ``ClassicWindow``, such as a fake ``popen``; for tests.
            safe_mode: Start without game assets, Spine or restored pages.
            upgrade: Coordinator holding the live data and library paths.
        """
        self.upgrade = upgrade
        self.committed = upgrade.paths if upgrade else None
        if upgrade and self.committed is None:
            raise ValueError("Services need a committed data generation")
        if self.committed:
            data_dir = self.committed.data_root
        devmode.check_read(data_dir)
        self.timings = Timings()
        spans.install(self.timings)  # game assets and the library record their waits here
        with self.timings.measure("settings.load-detect"):
            self.settings = settings or (
                Settings(self.committed.config, artifacts=self.committed.artifacts) if self.committed else Settings()
            )
        self.data_dir = Path(data_dir)
        # tested at start (a real temporary file); app.info reports it and the page shows one notice when it is False
        self.data_dir_writable = folder_writable(self.data_dir)
        if not self.data_dir_writable:
            logger.error("The data folder cannot be written, state and settings cannot be saved")
        self.runtime = self.committed.runtime if self.committed else self.data_dir
        self.log_path = self.runtime / "webui.log"
        self.log_secrets: set[str] = set()
        self.safe_mode = safe_mode
        self.restart_requested = False
        # Steam's file list of each install, parsed once for the library's judgements and the error report
        self.steam_files = steam_records.OfficialFiles()
        from .diagnostics import Reports

        self.reports = Reports(self.runtime, services=self, safe_mode=safe_mode)
        self.reports.secrets = self.log_secrets
        game_dir = self.settings.game_dir()
        self.reports.game = {
            "found": bool(game_dir and game_dir.is_dir()),
            "server": self.settings.server_region(),
            "platform": "Android" if self.settings.is_adb_mode() else "Windows",
            "resourceVersion": None,
        }
        self.events = events or EventBus()
        # the page pauses its preview deadlines while a preview waits for a job (the model lock, a costume build)
        events_bus = self.events
        preview_gate.on_wait = lambda busy: events_bus.publish("preview.waiting", {"busy": busy})
        self.jobs = jobs or JobRunner(self.events)
        with self.timings.measure("settings.ui-state"):
            self.state = state or UiState(self.committed.ui_state if self.committed else self.data_dir / "webui.json")
        self.chrome = None
        self.close_coordinator = None
        self.window: Any = None  # the pywebview window, when there is one (see native.py)
        self.update_info: Any = None  # the last update check's result
        self.update_file: tuple[Path, str, str] | None = (
            None  # verified path, MD5 and release version, for this session
        )
        self.update_installing = False
        self._pending_update: tuple[Callable[[], None], Future] | None = None
        self.kivo = Kivo()
        self.global_lines = GlobalLines()
        self.mute = False  # development: never play voices
        self.debug = False  # started with --debug: the front end may send its input trace (debug.trace)
        self.origin = "http://127.0.0.1:0"  # the server's origin, for the classic window's channel (main.build)
        self.quit_handle: Callable[[], None] | None = None  # browser mode: stops the server (main)
        self.browser_url: str | None = None  # browser mode: the page's address, opened again by a second start
        self.tray_supported = False  # a tray or menu bar icon can be shown (the tray sets it)
        self.tray_shown = False  # the tray or menu bar icon is shown (the game watch then polls fast)
        self._lock = threading.RLock()
        # Held by photograph saves and selections and by a library move, so a save never lands in the folder a move is
        # leaving. Taken before ``_lock`` and before the photograph store's own lock, never while holding either.
        self.thumbnail_writes = threading.RLock()
        self._thumbnail_catalog: Any = None
        self._cache, self._game, self._catalog, self._library = cache, game, catalog, library
        self._preview_cache = None
        self._legacy_cache_enabled = self.committed is not None
        self._injected = {k for k, v in (("game", game), ("catalog", catalog), ("library", library)) if v is not None}
        self._other_games: dict[str, Any] = {}  # other installs' GameAssets by install key, for <core>@<id> URLs
        self._install_stamps: tuple | None = (
            None  # game_found: the listing stamps of the folders last found to hold a game
        )
        self._prune_job: str | None = None
        self._startup_ready = True  # start() defers maintenance until the page reports its first reveal
        self._profiles_signature: Any = None
        self._intent_seen: tuple = ()
        self._queue_ticket: int | None = None  # the scan after a classic window closed: the queue runs after it
        self._progress_at = 0.0
        self._strings_cache: tuple[str, dict[str, str]] | None = None
        self.snapshot = ModsSnapshot()
        self.queue = ClassicQueue()
        self.classic = ClassicWindow(
            self.committed.runtime if self.committed else self.data_dir,
            origin=lambda: self.origin,
            publish=self.events.publish,
            **{
                "native": _WindowOps(self),
                "committed": lambda: self.committed,
                "read_only": lambda: bool(self.upgrade and self.upgrade.classic_read_only()),
                "protected": lambda: self.upgrade.classic_protected_destinations() if self.upgrade else [],
                **(classic_options or {}),
                "on_change": self._classic_changed,
                "on_closed": self._classic_closed,
            },
        )
        self.scanner = Scanner(self._scan, allowed=self.scan_allowed, writer_busy=self.jobs.busy, on_done=self._scanned)
        self.jobs.translate = self.translate_error
        self.jobs.listen(self._job_finished)
        self.jobs.listen(lambda job: self.reports.job_finished(job))
        self.jobs.on_exception = lambda *args: self.reports.backend_exception(*args)
        self.watch: Any = None  # the GameWatch, once start() runs
        self.settings.on_change(self._on_settings_changed)
        from .batch_import import BatchImport

        self.batch_import = BatchImport(self)

    def translate_error(self, error: BaseException) -> ServiceError | None:
        """Say which ``ServiceError`` an unexpected failure stands for.

        A write the permissions refuse, to a file in the data folder while that folder cannot be written, is
        ``data_dir_readonly`` rather than ``internal``. A single file that is busy while the folder takes new files
        is not this.

        Args:
            error: The exception.

        Returns:
            The error; None when the failure stands for none.
        """
        if not isinstance(error, OSError) or error.errno not in WRITE_DENIED:
            return None
        root = os.path.normcase(os.path.abspath(self.data_dir))
        for name in (error.filename, error.filename2):
            if isinstance(name, (str, bytes, os.PathLike)):
                path = os.path.normcase(os.path.abspath(os.fsdecode(name)))
                if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
                    break
        else:
            return None
        if folder_writable(self.data_dir):
            return None
        log_failure(logger, logging.WARNING, "The data folder cannot be written", error)
        return ServiceError("data_dir_readonly", path=str(self.data_dir))

    # --- lazily built services ---

    @property
    def cache(self) -> Any:
        """The asset cache in the cache folder, made on first use; it stores only with the asset consent."""
        with self._lock:
            if self._cache is None:
                from ..gameassets import AssetCache

                self._cache = AssetCache(
                    allowed=self.state.consent is True, root=self.cache_dir(), fallback=self._cache_fallback()
                )
            return self._cache

    @property
    def preview_cache(self) -> Any:
        """The store of persisted preview stills, made on first use; it stores unless the user opted out."""
        with self._lock:
            if self._preview_cache is None:
                from ..gameassets.cache import PreviewCache

                fallback = self._cache_fallback()
                self._preview_cache = PreviewCache(
                    self.cache_dir() / "previews",
                    allowed=self.state.preview_cache,
                    fallback=fallback / "previews" if fallback is not None else None,
                )
            return self._preview_cache

    def set_preview_cache(self, enabled: bool) -> None:
        """Save whether previews are stored on disk, and apply it at once.

        Args:
            enabled: Whether stills are persisted.
        """
        with self._lock:
            self.state.set("preview_cache", enabled)
            self.preview_cache.allowed = enabled

    def preview_thumbnail_key(self, key: str, renderer: str, *, by_version: bool = False) -> str:
        """Build the storage key of a persisted still.

        The client hashes all renderer inputs, the revision of every source a still was drawn from included (the
        stamps of its Spine bundles, the bytes of its background and framing, its model's export). The server
        isolates the installation and the renderer's own code (``stills.revision``): a release that draws a still
        differently never reuses the earlier still. The install's resource version is not part of the key, so a game
        update redraws only the stills whose sources it changed.

        Args:
            key: The client's hash of the still's inputs.
            renderer: The renderer that draws it, a key of ``stills.RENDERERS``.
            by_version: Build the key that includes the install's resource version, under which ``preview_thumbnail``
                looks for stills to move to the current key.

        Raises:
            ServiceError: ``bad_params`` for a key that is empty or longer than 4096 bytes, or an unknown renderer.
        """
        if not isinstance(key, str) or not key or len(key) > 4096:
            raise ServiceError("bad_params", key="key")
        try:
            encoded = key.encode()
        except UnicodeError as error:
            raise ServiceError("bad_params", key="key") from error
        if len(encoded) > 4096:
            raise ServiceError("bad_params", key="key")
        game = self.game
        dirs = getattr(game, "resource_dirs", None)
        if by_version:
            namespace = getattr(game, "preview_namespace", None)
            if namespace is None:
                namespace = json.dumps(
                    [self.settings.get("file_source"), str(self.settings.game_dir()), game.resource_version]
                )
        elif dirs is not None:
            namespace = json.dumps(["install", [str(path) for path in dirs]])
        else:
            namespace = json.dumps(["install", self.settings.get("file_source"), str(self.settings.game_dir())])
        from . import stills

        namespace += "\n" + stills.revision(renderer)
        return "thumbnail/" + hashlib.sha256((namespace + "\n" + key).encode()).hexdigest()

    def preview_thumbnail(self, key: str, renderer: str) -> bytes | None:
        """Read a persisted still.

        A still stored under the key that includes the install's resource version moves to the current key, so it is
        not drawn again.

        Args:
            key: The client's hash of the still's inputs.
            renderer: The renderer that draws it, a key of ``stills.RENDERERS``.

        Returns:
            The image bytes; None when none is stored.
        """
        cache = self.preview_cache
        found = cache.get(self.preview_thumbnail_key(key, renderer))
        if found is None:
            found = cache.get(self.preview_thumbnail_key(key, renderer, by_version=True))
            if found is not None:
                cache.put(self.preview_thumbnail_key(key, renderer), found, generation=cache.generation)
        return found

    def clear_cache(self) -> None:
        """Empty the asset cache and the preview store, and drop the game assets built from the game files."""
        with self._lock:
            self._drop_legacy_cache()
            self.preview_cache.clear_with(self.cache.clear)
            for game in [self._game, *self._other_games.values()]:
                if game is not None and hasattr(game, "clear_memory"):
                    game.clear_memory()
            if "game" not in self._injected:
                self._game = None
            self._other_games = {}

    @property
    def game(self) -> Any:
        """The game assets of the selected install, built on first use.

        Raises:
            ServiceError: ``game_not_found`` when the settings name no resource folder.
        """
        self._refresh_if_idle()
        with self._lock:
            if self._game is None:
                with self.timings.measure("game.detect"):
                    dirs = self.settings.resource_dirs()
                if not dirs:
                    raise ServiceError("game_not_found")
                self._game = self._make_game(dirs)
            return self._game

    def _make_game(self, dirs: list[Path], *, private: bool = False) -> Any:
        from ..gameassets import AssetCache, GameAssets

        if private:
            # Light indexes contain bundle paths, so give each other installation its own memory cache.
            # The separately consented preview store includes the install identity in every key.
            return GameAssets(
                dirs,
                AssetCache(
                    allowed=False, max_bytes=128 * 1024 * 1024, root=self.cache_dir(), fallback=self._cache_fallback()
                ),
                preview_cache=self.preview_cache,
            )
        return GameAssets(dirs, self.cache, preview_cache=self.preview_cache)

    def game_found(self) -> bool:
        """Return whether the selected folder holds a game; always False in safe mode."""
        if self.safe_mode:
            return False
        try:
            self.game
        except ServiceError:
            return False
        # a folder that holds no game is no game: the page then starts on the no-game screen
        return "game" in self._injected or self._holds_game()

    def _holds_game(self) -> bool:
        """Return whether the selected game folder is a game install (``searching.is_game_install``).

        A positive verdict is kept while the search directories keep their listing stamps; a folder without the
        game is checked again at every call, so a download that adds the UI bundles is seen.
        """
        from bmpt.engine import get_search_dirs, is_game_install

        base = self.settings.game_dir()
        if base is None:
            return False
        try:
            stamps = tuple(
                (str(path), stat.st_mtime_ns, stat.st_ino)
                for path, stat in ((path, path.stat()) for path in get_search_dirs(base))
            )
        except OSError:
            return False
        if self._install_stamps == stamps:
            return True
        found = is_game_install(base)
        self._install_stamps = stamps if found else None
        return found

    @property
    def catalog(self) -> Any:
        """The student catalog, built on first use from the game and the character map."""
        self._refresh_if_idle()
        with self._lock:
            if self._catalog is None:
                from ..catalog import Catalog

                csv = character_map_path(Path(self.settings.get("bacii_map_path")), self.data_dir)
                # recruitable follows the server of the selected game folder
                with self.timings.measure("catalog.open"):
                    self._catalog = Catalog(
                        self.game if self.game_found() else None,
                        csv if csv.exists() else None,
                        server=self.settings.server_region(),
                    )
            return self._catalog

    @property
    def library(self) -> Any:
        """The mod library, built on first use.

        It is built without a current install too (ADB mode, the selected folder missing): the switch then works
        over the other known installs. A new library gets its ``library.prune`` job and the start scan.
        """
        if self.upgrade and self.upgrade.paths is None:
            raise ServiceError("library_recovery_pending")
        self._refresh_if_idle()
        built = None
        with self._lock:
            if self._library is None:
                from ..library import ModLibrary

                devmode.check_read(self.library_dir())
                game = self.game if self.game_found() else None
                with self.timings.measure("library.open"):
                    built = self._library = ModLibrary(
                        self.library_dir(),
                        self.catalog,
                        game,
                        self.settings,
                        scan_allowed=self.scan_allowed,
                        data_id=self.state.data_id(self.data_dir),
                        write_check=self._write_check,
                        steam_files=self.steam_files,
                        game_write_gate=self.upgrade.game_write if self.upgrade else None,
                    )
                lock = getattr(built, "_lock", None)
                if lock is not None and hasattr(lock, "on_wait"):
                    # the page pauses its preview deadlines while a read waits for the library
                    lock.on_wait = lambda busy: self.events.publish("library.waiting", {"busy": busy})
            library = self._library
            # Establish the baseline before any caller (including an add or adoption job) gets the library. A library
            # that could not read its state answers empty: that is no baseline (every mod would count as new later).
            unreadable = getattr(library, "state_unreadable", None)
            self.state.seen_mods(
                None if unreadable is not None and unreadable() else lambda: [m.id for m in library.mods()]
            )
            if built is not None:
                # kept originals the game has no use for (verified in the launcher, replaced by a game update) are
                # deleted once per library, on the job worker so no apply runs meanwhile; the judging of the
                # unfinished list follows it
                if self._startup_ready and self.scan_allowed():
                    self._start_prune(built)
        if built is not None and self._startup_ready and self.scan_allowed():
            self.request_scan()  # the start scan (with scan consent), after the prune job
        return library

    @property
    def thumbnail_store(self):
        """The photograph store of the library folder in use.

        Its family lookup is bound to the catalog resolved here, never reached through the services lock: the
        store's own lock is held while it looks families up, and no code path may hold that lock while it waits for
        ``self._lock``. A new catalog (a new game folder) makes a new store. Call it with the photograph lock free.
        """
        from ..library.photographs import Photographs, student_family

        catalog = self.catalog
        root = self.library_dir()
        with self._lock:
            store = getattr(self, "_photographs", None)
            if store is None or store.root.parent != root or self._thumbnail_catalog is not catalog:
                self._thumbnail_catalog = catalog
                self._photographs = store = Photographs(root, lambda key: student_family(catalog, key))
            return store

    @property
    def thumbnails(self):
        """The student photography operations, made on first use."""
        from .thumbnails import Thumbnails

        if not hasattr(self, "_photography"):
            self._photography = Thumbnails(self)
        return self._photography

    def _start_prune(self, library: Any) -> None:
        def prune(log: Any, progress: Any) -> list[str]:
            with self.timings.measure("library.prune"):
                return [str(p) for p in library.prune()]

        self._prune_job = self.submit("library.prune", prune, writes="library", library=library, rank=MAINTENANCE)

    def cache_dir(self) -> Path:
        """Return where consented game assets are cached.

        That is a ``cache`` folder of the App's own inside the chosen folder, else the default user cache folder.
        It is always a folder of its own, so clearing it never touches anything else.
        """
        if self.committed is None:
            return managed_cache_dir(self.state.get("cache_parent"))
        return self.committed.cache(self._legacy_cache_dir())

    def _legacy_cache_dir(self) -> Path:
        parent = self.state.get("cache_parent")
        return Path(parent) / APP_DIR_NAME / "cache" if parent else legacy_cache_path(self.data_dir)

    def _cache_fallback(self) -> Path | None:
        if not self._legacy_cache_enabled:
            return None
        legacy = self._legacy_cache_dir()
        return legacy if devmode.allows_read(legacy) and legacy.is_dir() else None

    @staticmethod
    def _remove_managed_cache(path: Path) -> None:
        if (
            path.name == "cache"
            and path.parent.name in (APP_DIR_NAME, OLD_APP_DIR_NAME)
            and devmode.allows_read(path)
            and not path.parent.is_symlink()
            and not path.parent.is_junction()
            and path.is_dir()
        ):
            shutil.rmtree(path, ignore_errors=True)

    def _drop_legacy_cache(self, legacy: Path | None = None) -> None:
        if self.committed is None:
            return
        self._legacy_cache_enabled = False
        if self._cache is not None:
            self._cache.drop_fallback()
        if self._preview_cache is not None:
            self._preview_cache.drop_fallback()
        for game in self._other_games.values():
            game.cache.drop_fallback()
        self._remove_managed_cache(legacy if legacy is not None else self._legacy_cache_dir())

    def set_cache_parent(self, parent: Path) -> Path:
        """Move the cache to another folder; the cache left behind is removed and rebuilt from the game files on demand.

        Args:
            parent: The folder that holds the App's own cache folder.

        Returns:
            The new cache folder.

        Raises:
            ServiceError: ``path_not_found`` when ``parent`` is not a folder.
        """
        parent = Path(parent).expanduser().resolve()
        devmode.check_read(parent)
        if not parent.is_dir():
            raise ServiceError("path_not_found", path=str(parent))
        with self._lock:
            old = self.cache_dir()
            legacy = self._legacy_cache_dir() if self.committed is not None else None
            preview_cache = self.preview_cache
            self.state.set("cache_parent", str(parent))
            self._drop_legacy_cache(legacy)
            new = self.cache_dir()
            if new != old:
                # A cache relocation must invalidate pending writes to the previous generation too.
                def clear_old() -> None:
                    if self._cache is not None:
                        self._cache.clear()
                        self._cache.root = new
                    elif self.committed is None:
                        self._remove_managed_cache(old)
                    else:
                        shutil.rmtree(old, ignore_errors=True)

                preview_cache.relocate(new / "previews", clear_old)
        return new

    def check_output(self, destination: Path, *, writing: bool = True) -> None:
        """Verify that an export destination cannot modify a managed store.

        Args:
            destination: Folder chosen for output.
            writing: Whether the caller is about to create an output file.
        """
        if writing:
            devmode.check_write(destination)
        else:
            devmode.check_read(destination)
        if self.upgrade:
            self.upgrade.check_output(destination)

    def library_dir(self) -> Path:
        """Return where the mod library lives: the user's choice, else the data directory's ``library`` folder."""
        if self.committed:
            return self.committed.library_epoch
        chosen = self.state.get("library_dir")
        return Path(chosen) if chosen else self.data_dir / "library"

    def selected_library_dir(self) -> Path:
        """Return the library location shown in Settings."""
        return self.committed.library_root if self.committed else self.library_dir()

    def move_library(self, target: Path, *, adopt: bool = False) -> Path:
        """Use another folder for the mod library.

        A verified snapshot is published at the destination before the active route changes.

        Args:
            target: The desired library folder.
            adopt: The caller explicitly chose to open the existing destination library.

        Returns:
            The resolved target.

        Raises:
            ServiceError: ``busy`` while a job runs; ``path_not_found`` when ``target`` is a file;
                ``library_target_not_empty`` when it holds items that would clash; ``library_io_error`` when the
                copy fails.
        """
        target = Path(target).expanduser().resolve()
        devmode.check_read(target)
        current = self.selected_library_dir().resolve()
        if target == current:
            return target
        if target.exists() and not target.is_dir():
            raise ServiceError("path_not_found", path=str(target))
        if self.jobs.busy():
            raise ServiceError("busy")  # a running job still writes through the current library

        if self.upgrade:
            self.check_classic()
            self.scanner.cancel()
            self.scanner.wait_idle(5.0)
            with self.thumbnail_writes, self._lock:
                try:
                    self.committed = self.upgrade.relocate(target, adopt=adopt)
                except (UpgradeBlocked, InvalidStore, OSError) as error:
                    raise ServiceError("library_io_error", file=target) from error
                self._library = None
                self._profiles_signature = None
            self.preview_cache.clear()
            self.snapshot.clear_entries()
            self.events.publish("library.changed")
            self.refresh_status()
            return self.committed.library_root

        def has_library(folder: Path) -> bool:
            return (
                (folder / "state.json").exists()
                or (folder / "profiles.json").exists()
                or (folder / "photographs" / "index.json").exists()
            )

        self.scanner.cancel()  # a running scan reads the current library
        self.scanner.wait_idle(5.0)
        with self.thumbnail_writes, self._lock:  # a photograph save or selection waits, then uses the new folder
            if has_library(current) and not has_library(target):
                clashes = [item for item in LIBRARY_ITEMS if (target / item).exists()]
                if clashes:
                    raise ServiceError("library_target_not_empty", path=str(target / clashes[0]))
                try:
                    relocation = relocate(current, target, LIBRARY_ITEMS)
                except OSError as e:
                    log_failure(logger, logging.WARNING, "Cannot copy the mod library to the new folder", e)
                    raise ServiceError("library_io_error", file=str(target)) from e
            else:
                relocation = None
            try:
                self.state.set("library_dir", str(target))
            except BaseException:
                if relocation is not None:
                    relocation.undo()  # still in use where it was: nothing was lost, the copies go
                raise
            if "library" not in self._injected:
                self._library = None
                self._profiles_signature = None
            if relocation is not None:
                left = relocation.finish()
                if left:
                    logger.warning(
                        "the old library folder still holds %s; the library is complete in the new folder",
                        ", ".join(left),
                    )
        self.snapshot.clear_entries()
        self.events.publish("library.changed")
        self.refresh_status()
        self.publish_state()
        return target

    def reload_game(self) -> None:
        """Rebuild the game-backed services after the game folder or file source changed."""
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        # a new game folder means new game-backed services; while a writer job runs they are swapped once it is done,
        # so the job never shares the library with a second instance
        with self._lock:
            self._stale = True
            if self._preview_cache is not None:
                self._preview_cache.invalidate_pending()
        self._refresh_if_idle()
        self.events.publish("config.changed")
        # the known installs may be others now: a running scan covers the previous ones
        self.scanner.cancel()
        self.refresh_status()
        self.request_scan()
        self.publish_state()

    def _refresh_if_idle(self) -> None:
        with self._lock:
            if not getattr(self, "_stale", False) or self.jobs.busy():
                return
            self._stale = False
            for name in ("game", "catalog", "library"):
                if name not in self._injected:
                    setattr(self, f"_{name}", None)
            self._other_games = {}
            self._profiles_signature = None

    def set_consent(self, granted: bool) -> None:
        """Save the answer to the asset consent popup and apply it to the cache.

        Args:
            granted: Whether the web view and the cache may store light assets.
        """
        self.state.set_consent(granted)
        if self._cache is not None:
            self._cache.allowed = granted

    def set_spine_consent(self, granted: bool) -> None:
        """Save the answer to the Spine licence notice.

        Args:
            granted: True once the notice is acknowledged.
        """
        self.state.set("spine_consent", granted)

    def ui_cached(self, version: str | None = None) -> bool:
        """Return whether consented light UI assets already exist for the current install.

        Args:
            version: The install's resource version; None reads it from the game.
        """
        if self.safe_mode or self.state.consent is not True:
            return False
        if version is None:
            if not self.game_found():
                return False
            version = self.game.resource_version
        cache_version = getattr(self.game, "cache_version", version)
        return bool(getattr(self.cache, "has_disk_entries", lambda _: False)(cache_version))

    # --- the mods.state snapshot ---

    def refresh_status(self, library: Any = None) -> dict[str, Any] | None:
        """Read the library's status into the snapshot; it reads names and profiles.json only, never the library lock.

        Args:
            library: The library to read; None takes the current one.

        Returns:
            The status; the last known one when it cannot be read.
        """
        try:
            library = library if library is not None else self.library
            status = library.status()
        except ServiceError as error:
            logger.info("mods status unavailable: %s", error.code)
            return self.snapshot.status()
        except Exception:
            logger.exception("mods status unavailable")
            return self.snapshot.status()
        self.snapshot.set_status(status)
        self._intent_seen = _intent_key(status)
        return status

    def profiles_seen(self) -> bool:
        """Tell the game watch whether another data directory changed the library's shared intent.

        The watch asks at each poll. The intent is ``enabled``, ``toggled`` and ``unfinished`` of profiles.json,
        which is read again only when its size or date changed, under its file lock, never under the library lock.
        A change also requests a scan and the judging of the unfinished list, so the red dot follows with or
        without scan consent.

        Returns:
            Whether the shared intent changed since the last call.
        """
        library = self._library
        profiles = getattr(library, "profiles", None)
        if profiles is None:
            return False
        try:
            signature = profiles.signature()
        except Exception:
            return False
        if signature == self._profiles_signature:
            return False
        self._profiles_signature = signature
        try:
            intent = {**library.intent(), "reason": None}
        except ServiceError as error:
            intent = {"reason": error.code}
        status = dict(self.snapshot.status() or {})
        status.update(
            {key: value for key, value in intent.items() if key in ("enabled", "toggled", "unfinished", "reason")}
        )
        if _intent_key(status) == self._intent_seen:
            return False
        self.refresh_status(library)
        self.request_scan()
        self.submit_judge()
        return True

    def mods_state(self) -> dict[str, Any]:
        """Build the ``mods.state`` answer.

        It comes from the snapshot, the preferences, the game watch, the classic window and the jobs.

        Returns:
            The labelled ``installs`` and the ``current`` one, ``enabled``, ``available`` with the ``reason`` it is
            not, the ``scan`` consent, the counts of ``pending``, ``stuck``, ``left``, ``held``, ``drift``,
            ``untracked``, ``damaged`` and ``sourceMissing`` entries, the ``queued`` calls, when it was ``scanned``,
            the tray flags, ``busy`` and the toggle's ``progress``.
        """
        status = self.snapshot.status()
        if status is None:
            status = self.refresh_status() or {}
        consent = self.state.scan_consent
        entries = self.snapshot.entries() if consent is True else []
        unfinished = {"pending": 0, "left": 0, "stuck": 0, "held": 0, **(status.get("unfinished") or {})}
        held = [item for item in status.get("held") or [] if isinstance(item, dict)]
        holding = {(item.get("install"), item.get("core")) for item in held}
        count = lambda test: sum(1 for e in entries if test(e))  # noqa: E731
        if consent is True:
            # a held entry is told as held, not as one to retry
            pending = count(lambda e: e.state == "pending" and not e.overwritten and (e.install, e.core) not in holding)
            stuck = count(lambda e: e.state == "pending" and e.overwritten)
            left = unfinished["left"]  # an adopt that failed: only the toggle that made it knows
        else:
            pending, stuck, left = unfinished["pending"], unfinished["stuck"], unfinished["left"]
        reason, params = self._reason(status)
        strings = self._strings()
        installs = [
            {
                **{k: v for k, v in item.items() if k != "distinct"},
                "label": install_label(strings, item.get("server", "global"), item.get("distinct")),
                "distinct": item.get("distinct"),
            }
            for item in status.get("installs") or []
        ]
        return {
            "installs": installs,
            "current": status.get("current"),
            "enabled": status.get("enabled", True),
            "available": reason is None,
            "reason": reason,
            "reasonParams": params,
            "scan": consent,
            "explained": self.state.flag("mods_explained"),
            "configured": status.get("configured", 0),
            "pending": pending,
            "stuck": stuck,
            "left": left,
            "held": unfinished["held"],
            "heldEntries": held,
            "unfinished": {
                "pending": unfinished["pending"],
                "left": unfinished["left"],
                "stuck": unfinished["stuck"],
                "held": unfinished["held"],
            },
            "drift": count(lambda e: e.state in ("reset", "updated") and not e.dismissed),
            "untracked": count(lambda e: e.state in ("untracked", "changed") and not e.dismissed),
            "damaged": count(lambda e: e.state == "damaged"),
            "sourceMissing": count(lambda e: e.state == "source_missing"),
            "queued": len(self.queue),
            "scanned": self.snapshot.scanned() if consent is True else None,
            "tray": self.state.flag("tray"),
            "traySupported": self.tray_supported,
            "busy": self.jobs.active("mods.toggle") or len(self.queue) > 0,
            "progress": self.snapshot.progress(),
        }

    def _reason(self, status: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """Say why the switch is unavailable, with the reason's parameters.

        The reasons are: no known install at all, an unreadable configuration, the game watch's answer (the game
        running or its state unknown, Steam busy, a classic window left by an earlier instance), or this instance's
        classic window.
        """
        if not status.get("installs"):
            return "game_not_found", {}
        if status.get("reason"):
            return status["reason"], {"file": status.get("file")} if status.get("file") else {}
        watch = getattr(getattr(self.watch, "state", None), "reason", None)
        if watch:
            return watch, {}
        if self.classic_open():
            return "classic_open", {}
        return None, {}

    def _strings(self) -> dict[str, str]:
        """The UI language's strings, for the install labels (the locale files do not change while the app runs)."""
        lang = i18n_manager.lang
        cached = self._strings_cache
        if cached is None or cached[0] != lang:
            cached = self._strings_cache = (lang, ui_strings(lang))
        return cached[1]

    def publish_state(self) -> None:
        """Publish ``mods.state`` to the pages and the tray; a failure is logged."""
        try:
            self.events.publish("mods.state", self.mods_state())
        except Exception:
            logger.exception("mods.state not published")

    def close(self, wait: float | None = 600.0) -> None:
        """Shut the services down.

        The game watch and the scanner stop, the classic long-polls end, the jobs drain and the event bus closes.

        Args:
            wait: Seconds to wait for writer jobs to finish (``JobRunner.shutdown``); None does not wait.
        """
        if self.watch is not None:
            self.watch.stop()
        self.scanner.stop()
        self.classic.close()
        self.jobs.shutdown(wait=wait)
        self.events.close()
