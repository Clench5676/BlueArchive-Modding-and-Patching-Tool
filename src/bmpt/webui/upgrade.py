"""The authenticated web bootstrap while data services are unavailable."""

from __future__ import annotations

import logging
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from ..core.errors import ServiceError
from ..host.data_upgrade import DataUpgrade
from .strings import ui_strings
from .system import reveal

logger = logging.getLogger(__name__)


class UpgradeBootstrap:
    """Expose upgrade decisions over the local session without granting service access."""

    def __init__(
        self,
        coordinator: DataUpgrade,
        *,
        language: Callable[[], str],
        ready: Callable[[], None],
        quit_app: Callable[[], None],
        pick: Callable[[], Path | None] = lambda: None,
        opened: bool = False,
        update: Callable[[], None] | None = None,
        on_opened: Callable[[], None] | None = None,
    ) -> None:
        """Bind the coordinator to the lifetime and native actions of its bootstrap window."""
        self.coordinator = coordinator
        self.language = language
        self.ready = ready
        self.quit_app = quit_app
        self.pick = pick
        self._action = threading.Lock()
        self._opened = opened
        self._update = update or _open_update
        self._on_opened = on_opened

    def state(self) -> dict:
        """Return the localized notice state."""
        state = self.coordinator.state()
        if state["status"] == "ready" and not self._opened:
            state["status"] = "running"
            state["notice"] = None
            if state.get("steps"):
                state["steps"][-1]["status"] = "running"
            state["percent"] = min(99, state["percent"])
        return {
            **state,
            "strings": {
                key.removeprefix("webui."): value
                for key, value in ui_strings(self.language()).items()
                if key.startswith("webui.upgrade.")
            },
        }

    def dispatch(self, action: str) -> dict:
        """Apply an authenticated bootstrap action and report the resulting state."""
        if action == "state":
            return self.state()
        if action == "quit":
            self.quit_app()
            return self.state()
        if action == "openLog":
            try:
                reveal(self.coordinator.log_folder)
            except ServiceError as error:
                logger.warning("Could not open the upgrade log folder: %s", error)
            return self.state()
        if not self._action.acquire(blocking=False):
            return self.state()
        try:
            if action == "acknowledge":
                if self._opened:
                    self.coordinator.acknowledge()
                return self.state()
            if self._opened:
                return self.state()
            if action == "update":
                if self.coordinator.state()["reason"] == "newerFormat":
                    self._update()
                return self.state()
            target = self.pick() if action == "relocate" else None
            state = (
                self.coordinator.state()
                if action == "relocate" and target is None
                else self.coordinator.run(action, target=target)
            )
            if state["status"] == "ready" and self.coordinator.paths is not None:
                try:
                    self.ready()
                except Exception:
                    logger.exception("Opening committed data services failed")
                    self.coordinator.open_failed()
                else:
                    self._opened = True
                    if self._on_opened is not None:
                        self._on_opened()
            return self.state()
        finally:
            self._action.release()


def _open_update() -> None:
    from ..host import updater

    try:
        update = updater.check()
    except ServiceError as error:
        logger.warning("The update check is unavailable: %s", error)
        update = None
    webbrowser.open(update.page if update else f"https://github.com/{updater.REPO}/releases")
