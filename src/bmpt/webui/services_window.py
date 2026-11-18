"""The classic window, the game watch, the window and quitting, a mixin of the web UI's ``Services``."""

from __future__ import annotations

import logging
import threading
import webbrowser
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..host import updater
from ..core.errors import ServiceError
from .classic.queued import QUEUE_METHODS, QueuedCall, stat_files
from .jobs import MAINTENANCE
from .services_jobs import PRUNE_WAIT

if TYPE_CHECKING:
    from .services import Services


logger = logging.getLogger("bmpt.webui.services")  # the services log under one name

UPDATE_QUIT_WAIT = 3.0  # seconds for the native window to close for an update


class _QueueHost:
    """What the classic queue asks of the web UI when the classic window closed."""

    def __init__(self, services: Services):
        self._s = services

    def intent(self) -> bool | None:
        return self._s.library.intent()["enabled"]

    def pins(self) -> dict[str, Any]:
        return {pin.key: pin for pin in self._s.library.installs()}

    def current(self) -> str | None:
        pin = self._s.library.current_pin()
        return pin.key if pin else None

    def submit(self, method: str, params: dict[str, Any]) -> Any:
        return self._s.submit_call(method, params)


class WindowMixin:
    """The classic window, the game watch, the window and quitting of ``Services``."""

    # --- the classic window ---

    def classic_open(self) -> bool:
        """Whether a classic window is open: this instance's, or one found through classic.json."""
        return self.classic.is_open()

    def check_classic(self) -> None:
        """Refuse while a classic window is open.

        Raises:
            ServiceError: ``classic_open``.
        """
        if self.classic_open():
            raise ServiceError("classic_open")

    def _write_check(self, pin: Any) -> None:
        # the library's own check right before game files are written: never while the classic UI is open
        self.check_classic()

    def _classic_changed(self) -> None:
        self.publish_state()

    def _classic_closed(self, code: int | None = None) -> None:
        """This instance's classic window closed (its watcher thread)."""
        self.after_classic()

    def _orphan_closed(self) -> None:
        """A classic window an earlier instance left closed (the game watch)."""
        self.after_classic()

    def after_classic(self) -> None:
        """Follow up a closed classic window: the page reads the files again, a scan runs with consent, then the queue.

        The classic tabs may have changed game files.
        """
        self.events.publish("library.changed")
        ticket = self.request_scan()
        if ticket is None:
            self.run_queue()
        else:
            with self._lock:
                self._queue_ticket = ticket
            if self.scanner.served() >= ticket:  # served (or dropped) before the ticket was kept
                self._release_queue(self.scanner.served())
        self.publish_state()

    def run_queue(self) -> Any:
        """Submit the writes queued while the classic window was open, those that still hold.

        The dropped ones are published (``classic.queueDone``) for one result popup.

        Returns:
            The queue's run (``QueueRun``); None when nothing was queued.
        """
        if not len(self.queue):
            return None
        run = self.queue.run(_QueueHost(self))
        self.events.publish("classic.queueDone", run.to_json())
        self.publish_state()
        return run

    def queue_call(self, method: str, params: dict[str, Any]) -> int:
        """Keep a write that was refused for when the classic window closes (``classic.queue``).

        The call is kept with its pins and the sizes and dates of its entries' files.

        Args:
            method: The refused RPC method, one of ``classic.QUEUE_METHODS``.
            params: The refused call's parameters.

        Returns:
            How many calls are queued.

        Raises:
            ServiceError: ``bad_params`` for another method or invalid parameters; ``game_file_not_found`` for a
                variant with no files.
        """
        if method not in QUEUE_METHODS or not isinstance(params, dict):
            raise ServiceError("bad_params", method=method)
        library = self.library
        if method == "mods.toggle":
            enabled, expect = params.get("enabled"), params.get("expect")
            if not isinstance(enabled, bool) or not isinstance(expect, bool):
                raise ServiceError("bad_params", key="enabled")
            call = QueuedCall(
                method, {"enabled": enabled, "expect": expect}, pins={pin.key: pin for pin in library.installs()}
            )
            count = self.queue.add(call, intent=library.intent()["enabled"])
        elif method in ("actions.apply", "actions.restore"):
            variant, kind = params.get("variant"), params.get("kind", "spr")
            if not isinstance(variant, str) or kind not in ("spr", "lobby", "model", "other"):
                raise ServiceError("bad_params", key="variant")
            clean: dict[str, Any] = {"variant": variant, "kind": kind}
            if method == "actions.apply":
                if not isinstance(params.get("source"), str):
                    raise ServiceError("bad_params", key="source")
                clean["source"] = params["source"]
            pin = self._current_pin(library)
            if kind == "other":
                from ..library.judge import core_of

                states = library.state(variant, pin=pin).files
                entries = tuple(sorted({(pin.key, core_of(f.file.name)) for f in states}))
                if not entries:
                    raise ServiceError("game_file_not_found", variant=variant)
                files = {
                    entry: stat_files([str(f.file) for f in states if core_of(f.file.name) == entry[1]])
                    for entry in entries
                }
                call = QueuedCall(method, clean, entries=entries, pins={pin.key: pin}, files=files, current=True)
                count = self.queue.add(call)
                self.publish_state()
                return count
            try:
                found = self.catalog.variant(variant)
            except KeyError as error:
                raise ServiceError("alt_not_found", variant=variant) from error
            core = getattr(found, kind, None) or ""
            files = [str(f.file) for f in library.state(variant, pin=pin).files if f.kind == kind]
            entry = (pin.key, core.lower())
            call = QueuedCall(
                method, clean, entries=(entry,), pins={pin.key: pin}, files={entry: stat_files(files)}, current=True
            )
            count = self.queue.add(call)
        else:  # mods.restoreDrift
            listed = params.get("entries")
            if not isinstance(listed, list):
                raise ServiceError("bad_params", key="entries")
            pins = {pin.key: pin for pin in library.installs()}
            known = {(e.install, e.core): e for e in self.snapshot.entries()}
            items, keys, files = [], [], {}
            for item in listed:
                if not (isinstance(item, dict) and item.get("install") in pins and isinstance(item.get("core"), str)):
                    continue
                key = (item["install"], item["core"].lower())
                if key in keys:
                    continue
                entry = known.get(key)
                keys.append(key)
                items.append(item)
                files[key] = stat_files([str(f.file) for f in entry.files] if entry is not None else [])
            if not keys:
                raise ServiceError("bad_params", key="entries")
            call = QueuedCall(
                method,
                {"entries": items},
                entries=tuple(keys),
                pins={key[0]: pins[key[0]] for key in keys},
                files=files,
            )
            count = self.queue.add(call)
        self.publish_state()
        return count

    def clear_queue(self) -> int:
        """Empty the classic queue and publish the state.

        Returns:
            How many calls were dropped.
        """
        dropped = self.queue.clear()
        self.publish_state()
        return dropped

    def _own_classic(self) -> int | None:
        status = self.classic.status()
        return status["pid"] if status["open"] and not status["orphan"] else None

    # --- the game watch, the window and quitting ---

    def start(self) -> None:
        """Start the watch; library maintenance waits for the first screen to be revealed."""
        self._startup_ready = False
        if not self.safe_mode:
            self.start_watch()

    def ready(self) -> None:
        """The page revealed its first screen. Start optional checks once, away from the UI request."""
        with self._lock:
            if self._startup_ready or self.safe_mode:
                return
            self._startup_ready = True
        from ..host.updater import finalize_windows_update

        finalize_windows_update()
        threading.Thread(target=self._warm, name="bamt-library", daemon=True).start()

    def _warm(self) -> None:
        try:
            library = self.library
            self._start_crc_repair(library)
            requested = False
            with self._lock:
                if self._prune_job is None and self.scan_allowed():
                    self._start_prune(library)
                    requested = True
            if requested:
                self.request_scan()
            if self.refresh_status() is not None:
                self.publish_state()
            # the official rigs of the portraits as this release has them, kept before an update replaces them
            #: after the prune, away from the job queue, a core at a time
            if self._prune_job is not None:
                self.jobs.wait(self._prune_job, PRUNE_WAIT)
            archive = getattr(library, "archive_rigs", None)
            if archive is not None and self.scan_allowed():
                with self.timings.measure("library.rigs"):
                    archive(cancel=lambda: self.jobs.closing)
        except Exception:
            logger.exception("startup maintenance failed")

    def _start_crc_repair(self, library: Any) -> str | None:
        """Repair, without asking, the files whose bytes lack their target filename's CRC.

        This is a game writer job, started once such files are found (``crc_repair_due``, read once per library);
        it returns the job's id, or None when nothing is due. Nothing is shown: an entry it cannot write now (the
        game running, a classic window open) is logged and tried again at the next start.
        """
        due = getattr(library, "crc_repair_due", None)
        try:
            if due is None or not due().get("due"):
                return None
        except ServiceError as error:
            logger.info("CRC repair not checked: %s", error.code)
            return None

        def run(log: Any, progress: Any) -> Any:
            if self.classic_open():
                logger.info("CRC repair left for the next start: a classic window is open")
                return None
            return library.repair_crc(log=log)

        return self.submit("mods.crcRepair", run, writes="game", library=library, rank=MAINTENANCE)

    def start_watch(self, run: bool = True) -> None:
        """Make the game watch, once.

        Args:
            run: Start the watch's thread; without it, ``watch.poll()`` checks once.
        """
        from .gamewatch import GameWatch

        if self.watch is not None:
            return
        self.watch = GameWatch(
            installs=lambda: [item["folder"] for item in (self.snapshot.status() or {}).get("installs") or []],
            find_classic=self.classic.orphan,
            profiles_seen=self.profiles_seen,
            on_change=lambda state: self.publish_state(),
            publish=self.events.publish,
            request_scan=self.request_scan,
            active=lambda: self.events.listeners() > 0 or self.tray_shown,
            own_classic=self._own_classic,
            on_classic_closed=self._orphan_closed,
            on_steam_idle=self._steam_idle,
            steam_allowed=self.scan_allowed,
        )
        if run:
            self.watch.start()

    def _steam_idle(self) -> None:
        """Rebuild the game-backed services after Steam went idle on an install, if a game update changed them.

        The services are rebuilt as a settings change does it (once no job runs) when a built game's resource version
        changed. An unchanged version does nothing.
        """
        with self._lock:
            games = [game for game in (self._game, *self._other_games.values()) if game is not None]
        changed = False
        for game in games:
            check = getattr(game, "resource_changed", None)
            try:
                changed = bool(check and check())
            except OSError:
                logger.debug("cannot read the resource version after Steam went idle", exc_info=True)
            if changed:
                break
        if changed:
            logger.info("the game's resource version changed: rebuilding the game services")
            self._on_settings_changed()

    def raise_window(self) -> None:
        """Show and raise the window for a second start of the program on this data directory.

        In browser mode the page is opened again.
        """
        window = self.window
        if window is not None:
            for name in ("show", "restore"):
                try:
                    getattr(window, name)()
                except Exception:
                    logger.debug("window.%s failed", name, exc_info=True)
            return
        if self.browser_url:
            webbrowser.open(self.browser_url)

    def quit(self, *, restart: bool = False) -> None:
        """Quit the App (``app.quit``): close the window, or in browser mode stop the server once the answer went out.

        Args:
            restart: Start the App again in safe mode after it quit.

        Raises:
            ServiceError: ``busy`` while a writer job is queued or running; ``native_unavailable`` with ``--serve``.
        """
        if self.jobs.busy():
            raise ServiceError("busy")
        if self.close_coordinator is not None:

            def close():
                self.restart_requested = restart
                self.chrome.close()

            self.close_coordinator.request(close, cancelled=lambda: setattr(self, "restart_requested", False))
            return
        if self.window is not None:
            # Non-pywebview service hosts still pass the writer check above.
            self.window.destroy()
            return
        if self.quit_handle is not None:
            self.restart_requested = restart
            threading.Timer(0.3, self.quit_handle).start()
            return
        raise ServiceError("native_unavailable")

    def install_update(self, path: Path, md5: str) -> None:
        """Close the window and run the prepared installer, reserving writer and classic admission until then.

        The reservation holds until the native window closes or refuses the update.

        Args:
            path: The downloaded installer.
            md5: Its MD5, verified before it runs.

        Raises:
            ServiceError: ``busy`` while an update is installing; ``classic_open`` while a classic window is open;
                ``update_restart_failed`` when the window does not close in time.
        """
        if self.close_coordinator is not None:
            return self.close_coordinator.request(lambda: self._install_update(path, md5), reserve=False).result()
        return self._install_update(path, md5)

    def _install_update(self, path: Path, md5: str) -> None:
        done: Future = Future()
        with self.classic.starting():
            if self.update_installing:
                raise ServiceError("busy")
            if self.classic_open():
                raise ServiceError("classic_open")
            self.jobs.reserve_quit()
            try:
                launch = updater.prepare_install(path, md5)
            except Exception:
                self.jobs.cancel_quit()
                raise
            self.update_installing = True
            self._pending_update = (launch, done)
        try:
            # destroy() may dispatch closing on another thread, or return without closing the window.
            if self.chrome is not None:
                self.chrome.close()
            else:
                self.window.destroy()
            try:
                done.result(timeout=UPDATE_QUIT_WAIT)
            except FutureTimeout:
                with self.classic.starting():
                    if not done.done():
                        self._cancel_update(ServiceError("update_restart_failed"))
                done.result()
        except Exception:
            with self.classic.starting():
                if self._pending_update is not None and self._pending_update[1] is done:
                    self._cancel_update(ServiceError("update_restart_failed"))
            raise

    def _cancel_update(self, error: Exception) -> None:
        """Called under classic.starting(); a refused quit retains the verified download for retry."""
        pending, self._pending_update = self._pending_update, None
        self.update_installing = False
        self.jobs.cancel_quit()
        if pending is not None:
            pending[1].set_exception(error)

    def on_closing(self) -> bool:
        """Decide whether the window may close: refuse while a writer job or a classic window holds the update.

        Returns:
            Whether the close may go on.
        """
        with self.classic.starting():
            if self.jobs.busy():
                if self._pending_update is not None:
                    self._cancel_update(ServiceError("busy"))
                self.events.publish("app.busy")
                return False
            if self._pending_update is not None and self.classic_open():
                self._cancel_update(ServiceError("classic_open"))
                return False
            if self.close_coordinator is not None:
                return self.close_coordinator.native_closing()
            return True

    def on_closed(self) -> None:
        """Launch a prepared helper once the window is gone, including backends that skip closing on destroy()."""
        if self.close_coordinator is not None:
            self.close_coordinator.closed()
        with self.classic.starting():
            pending = self._pending_update
            if pending is None:
                return
            try:
                pending[0]()
            except Exception as error:
                self._cancel_update(error)
                logger.exception("update helper failed to start")
                return
            self._pending_update = None
            # Keep admission closed and the install latch set until exit: repeated requests cannot spawn helpers.
            pending[1].set_result(None)
