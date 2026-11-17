"""``ClassicWindow``: starts the classic GUI in its own process, places and raises it, and talks to it."""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ...core.errors import ServiceError
from ..system import CREATE_NO_WINDOW, check_classic_tab, classic_command
from .native import NativeOps
from .processes import (
    SYSTEM_PROCESSES,
    ClassicProcess,
    Processes,
    find_classic,
    remove_classic_json,
    write_classic_json,
)
from .protocol import (
    _TAB_NAME,
    CLASSIC_LOG,
    CLASSIC_SIZE,
    CONTROL_PATH,
    CONTROL_POLL,
    ENV_TOKEN,
    ENV_URL,
    MAX_REPORT,
    READY_TIMEOUT,
    REPORT_PATH,
    SILENT_EXIT,
    tab_line,
)


logger = logging.getLogger(__package__)


@dataclass(eq=False)
class _Child:
    popen: Any  # None until Popen returned
    token: str
    tab: str | None  # the tab it shows, or should show once placed
    started_tab: str | None
    log: Any = None
    lines: list[str] = field(default_factory=list)
    pid: int | None = None  # reported at ready; on Windows the Popen pid is the onefile launcher's
    hwnd: int = 0
    exe: str = ""
    created: int | None = None
    busy: int = 0
    ready: bool = False
    placed: bool = False  # lines go out only once the window is placed: ready, place, yield, raise
    abandoned: bool = False  # the opener gave up waiting for ready
    exited: bool = False
    code: int | None = None
    polls: int = 0
    last_poll: float = field(default_factory=time.monotonic)


class ClassicWindow:
    """The one classic window of this data directory.

    It was started by this instance, or left by an earlier one and found through ``classic.json`` (an *orphan*,
    raised by pid but without a control channel).
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        origin: str | Callable[[], str],
        publish: Callable[[str, Any], None] | None = None,
        native: NativeOps | None = None,
        command: Callable[[str | None], list[str]] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        processes: Processes | None = None,
        on_change: Callable[[], None] | None = None,
        on_closed: Callable[[int | None], None] | None = None,
        ready_timeout: float = READY_TIMEOUT,
        poll_timeout: float = CONTROL_POLL,
        silent_exit: float = SILENT_EXIT,
        watch_interval: float = 1.0,
    ):
        """Create the manager; no window is started.

        Args:
            data_dir: The data directory, which holds ``classic.json`` and ``classic.log``.
            origin: The web UI server's origin, or a function that returns it once the server listens.
            publish: The event bus's ``publish``; None publishes nothing.
            native: The window operations (``native_ops(window)``); None does nothing natively.
            command: Builds the child's command line for a tab; None starts the classic GUI with its control
                channel.
            popen: Starts the child; tests replace ``subprocess.Popen``.
            processes: The process table; None reads the system's.
            on_change: Called after every change of ``status()``, outside the state lock.
            on_closed: Called with the child's exit code after ``classic.closed``, outside the state lock.
            ready_timeout: Seconds ``open`` waits for the new window to be ready.
            poll_timeout: Seconds a control long-poll waits for a line.
            silent_exit: Seconds without a poll after which a child whose launcher returned early counts as ended.
            watch_interval: Seconds between two checks of the child's process.
        """
        self.data_dir = Path(data_dir)
        self._origin = origin if callable(origin) else (lambda: origin)
        self._publish = publish or (lambda event, data=None: None)
        self.native = native or NativeOps()
        self._command = command or (lambda tab: classic_command(tab, control=True))
        self._popen = popen
        self._procs = processes or SYSTEM_PROCESSES
        self._on_change = on_change
        self._on_closed = on_closed
        self._ready_timeout = ready_timeout
        self._poll_timeout = poll_timeout
        self._silent_exit = silent_exit
        self._watch_interval = watch_interval
        self._cond = threading.Condition()
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._child: _Child | None = None

    # --- state ---

    def _live(self) -> _Child | None:
        with self._cond:
            child = self._child
            return child if child is not None and not child.exited else None

    def orphan(self) -> ClassicProcess | None:
        """Find a classic window this instance did not start, through ``classic.json`` (GameWatch's reader)."""
        if self._live() is not None:
            return None
        return find_classic(self.data_dir, self._procs)

    def is_open(self) -> bool:
        """Whether a classic window is open: game-folder writes are refused with ``classic_open`` meanwhile."""
        return self._live() is not None or self.orphan() is not None

    def status(self) -> dict[str, Any]:
        """Describe the classic window as ``{open, ready, busy, pid, orphan}``.

        ``app.classic`` adds the queue's count.
        """
        child = self._live()
        if child is not None:
            with self._cond:
                return {
                    "open": True,
                    "ready": child.ready,
                    "busy": child.busy > 0,
                    "pid": child.pid or _popen_pid(child),
                    "orphan": False,
                }
        orphan = self.orphan()
        if orphan is not None:
            return {"open": True, "ready": True, "busy": False, "pid": orphan.pid, "orphan": True}
        return {"open": False, "ready": False, "busy": False, "pid": None, "orphan": False}

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                logger.exception("classic change hook failed")

    # --- open ---

    @contextmanager
    def starting(self) -> Iterator[None]:
        """Serialize game-writer checks and submission with a new classic child's registration."""
        with self._start_lock:
            yield

    def open(self, tab: str | None = None, may_start: Callable[[], None] | None = None) -> dict[str, bool]:
        """Start the classic window, or bring the open one to the front (``app.openClassic``).

        An open window is raised on the tab asked for unless a classic task runs. A new window is waited for until
        its ``ready``, at most ``ready_timeout``.

        Args:
            tab: The tab to show, ``"settings"`` for the classic settings, or None for the default tab.
            may_start: Called under ``starting()`` before a new child is registered; it can refuse by raising. An
                open window is still raised.

        Returns:
            ``{"started": bool}``, whether this call started a new window.

        Raises:
            ServiceError: ``bad_params`` for an unknown tab; ``classic_failed`` (with ``exit_code``) when the child
                cannot be started, exits or stays silent before it is ready.
        """
        check_classic_tab(tab)
        with self._start_lock:
            child = self._live()
            if child is None:
                orphan = find_classic(self.data_dir, self._procs)
                if orphan is not None:
                    self._native_call(self.native.raise_process, orphan)
                    return {"started": False}
                if may_start is not None:
                    may_start()
                child = self._start(tab)
                started = True
            else:
                started = False
        if not started:
            self._show(child, tab)
            return {"started": False}
        self._await_ready(child)
        self._place_and_raise(child)
        return {"started": True}

    def bring_to_front(self) -> bool:
        """Raise the open classic window, for the classic modal's Bring to Front.

        Returns:
            False when no classic window is open.
        """
        child = self._live()
        if child is not None:
            self._show(child, None)
            return True
        orphan = self.orphan()
        if orphan is not None:
            self._native_call(self.native.raise_process, orphan)
            return True
        return False

    def _start(self, tab: str | None) -> _Child:
        token = secrets.token_urlsafe(24)
        env = os.environ.copy()
        env["BAMT_DATA_DIR"] = str(self.data_dir.resolve())
        env[ENV_URL] = self._origin().rstrip("/")
        env[ENV_TOKEN] = token
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"  # classic.log keeps what it printed before a crash
        cmd = self._command(tab)
        log = None
        try:
            # never an unread pipe, which fills and blocks the child
            log = open(self.data_dir / CLASSIC_LOG, "wb")
        except OSError:
            logger.warning("cannot write %s", self.data_dir / CLASSIC_LOG, exc_info=True)
        # known before it starts, so that its first poll finds it
        child = _Child(popen=None, token=token, tab=tab, started_tab=tab, log=log)
        with self._cond:
            self._child = child
        try:
            popen = self._popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as e:
            with self._cond:
                child.exited = True
                if self._child is child:
                    self._child = None
            if log is not None:
                log.close()
            raise ServiceError("classic_failed", error=str(e)) from e
        with self._cond:
            child.popen = popen
        logger.info("classic GUI started (pid %s): %s", popen.pid, cmd)
        threading.Thread(target=self._watch, args=(child,), name="bamt-classic", daemon=True).start()
        self._publish("classic.opened", {"pid": popen.pid})
        self._changed()
        return child

    def _await_ready(self, child: _Child) -> None:
        deadline = time.monotonic() + self._ready_timeout
        with self._cond:
            while not child.ready and not child.exited:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            if child.ready:
                return
            child.abandoned = True
            code = child.code if child.exited else None
        logger.warning("the classic GUI %s before it was ready", "exited" if child.exited else "stayed silent")
        raise ServiceError("classic_failed", exit_code=code)

    def _place_and_raise(self, child: _Child) -> None:
        line = self._native_call(self.native.place, child.pid, child.hwnd, CLASSIC_SIZE)
        with self._cond:
            if line:
                child.lines.append(line)
            if child.tab and child.tab != child.started_tab and child.busy == 0:
                child.lines.append(tab_line(child.tab))  # asked for by a second click while it started
            child.placed = True
        self._raise(child)

    def _show(self, child: _Child, tab: str | None) -> None:
        with self._cond:
            if tab and child.busy == 0:  # a running classic task's tab is never switched underneath it
                child.tab = tab
                if child.placed:
                    child.lines = [x for x in child.lines if not (x == "settings" or x.startswith("tab "))]
                    child.lines.append(tab_line(tab))
            if not child.placed:
                return  # the opener places and raises it once it is ready
        self._raise(child)

    def _raise(self, child: _Child) -> None:
        self._native_call(self.native.before_raise, child.pid)
        with self._cond:
            if "raise" in child.lines:
                child.lines.remove("raise")
            child.lines.append("raise")
            self._cond.notify_all()

    def _native_call(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception:
            logger.exception("native window call %s failed", getattr(fn, "__name__", fn))
            return None

    # --- the child's channel ---

    def _match(self, token: str) -> _Child | None:
        child = self._child
        if child is None or child.exited or not token:
            return None
        if not hmac.compare_digest(token.encode("utf-8", "surrogateescape"), child.token.encode("utf-8")):
            return None
        return child

    def control(self, token: str, timeout: float | None = None) -> list[str] | None:
        """Serve ``GET /api/classic/control``: wait for lines for the child and return them.

        Args:
            token: The child's one-time token.
            timeout: Seconds to wait for a line; None uses ``poll_timeout``.

        Returns:
            The lines, once there are some or when the time is up; None for an unknown token or while the manager
            is closed, which makes the child stop polling.
        """
        wait = self._poll_timeout if timeout is None else timeout
        with self._cond:
            child = self._match(token)
            if child is None or self._stop.is_set():
                return None  # refused: the child stops polling
            child.polls += 1
            try:
                deadline = time.monotonic() + wait
                while not (child.placed and child.lines) and not child.exited and not self._stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)
                if self._stop.is_set():
                    return None
                if not child.placed:
                    return []
                lines, child.lines = child.lines, []
                return lines
            finally:
                child.polls -= 1
                child.last_poll = time.monotonic()

    def report(self, token: str, data: Any) -> bool | None:
        """Take a report of the child, ``POST /api/classic/report``.

        Args:
            token: The child's one-time token.
            data: The report, ``{"type": ..., ...}``: ``ready {pid, hwnd, exe}``, ``shown {tab}`` or ``busy {n}``.

        Returns:
            True when taken; False for a malformed report; None for an unknown token.
        """
        child = self._match(token)
        if child is None:
            return None
        if not isinstance(data, dict):
            return False
        kind = data.get("type")
        if kind == "ready":
            self._ready(child, data)
        elif kind == "shown":
            tab = data.get("tab")
            if not (tab is None or (isinstance(tab, str) and (tab == "settings" or _TAB_NAME.fullmatch(tab)))):
                return False
            with self._cond:
                child.tab = tab
            self._publish("classic.ready", {"pid": child.pid, "tab": tab})
        elif kind == "busy":
            n = _int(data.get("n"))
            if n is None:
                return False
            with self._cond:
                changed, child.busy = child.busy != max(n, 0), max(n, 0)
            if changed:
                self._changed()
        else:
            return False
        return True

    def _ready(self, child: _Child, data: dict[str, Any]) -> None:
        pid = _int(data.get("pid")) or _popen_pid(child)
        hwnd = _int(data.get("hwnd")) or 0
        exe = data.get("exe") if isinstance(data.get("exe"), str) else ""
        with self._cond:
            if child.ready:
                return
        created = self._procs.created(pid) if pid and pid > 0 else None
        # written before ready counts, so the file names the window once app.openClassic answers
        if created is None:
            logger.warning("no creation time for the classic GUI (pid %s); classic.json not written", pid)
        else:
            write_classic_json(self.data_dir, ClassicProcess(pid=pid, exe=exe, hwnd=max(hwnd, 0), created=created))
        with self._cond:
            child.pid, child.hwnd, child.exe, child.created = pid, max(hwnd, 0), exe, created
            child.ready = True
            if child.abandoned:
                child.placed = True  # nobody places it: it shows itself, and later clicks raise it
            self._cond.notify_all()
            exited = child.exited
        if exited:  # it ended meanwhile, and _closed may have looked for the file before it was written
            remove_classic_json(self.data_dir, pid)
        logger.info("classic GUI ready (pid %s)", pid)
        self._publish("classic.ready", {"pid": pid, "tab": child.tab})
        self._changed()

    def wsgi(self, environ: dict, start_response: Callable) -> list[bytes]:
        """Answer the ``/api/classic/*`` endpoints, which the web UI's WSGI app routes to after its Host check.

        They take the child's token (``X-BAMT-Classic``), never the session token.

        Args:
            environ: The WSGI environment of the request.
            start_response: The WSGI callback that takes the status and the headers.

        Returns:
            The response body.
        """
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        token = environ.get("HTTP_X_BAMT_CLASSIC", "")
        if path == CONTROL_PATH:
            if method != "GET":
                return _reply(start_response, "405 Method Not Allowed", {})
            lines = self.control(token)
            if lines is None:
                return _reply(start_response, "403 Forbidden", {})
            return _reply(start_response, "200 OK", {"lines": lines})
        if path == REPORT_PATH:
            if method != "POST":
                return _reply(start_response, "405 Method Not Allowed", {})
            try:
                length = int(environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            if self._match(token) is None:
                return _reply(start_response, "403 Forbidden", {})
            if length <= 0 or length > MAX_REPORT:
                return _reply(start_response, "400 Bad Request", {})
            try:
                data = json.loads(environ["wsgi.input"].read(length))
            except (ValueError, KeyError):
                return _reply(start_response, "400 Bad Request", {})
            ok = self.report(token, data)
            if ok is None:
                return _reply(start_response, "403 Forbidden", {})
            return _reply(start_response, "200 OK" if ok else "400 Bad Request", {})
        return _reply(start_response, "404 Not Found", {})

    # --- exit ---

    def _silent(self, child: _Child) -> bool:
        with self._cond:
            return child.polls == 0 and time.monotonic() - child.last_poll > self._silent_exit

    def _watch(self, child: _Child) -> None:
        code = child.popen.wait()
        # the Windows onefile launcher may return before the window it unpacked: follow the pid the child
        # reported, until it ends or stops polling
        while not self._stop.is_set():
            with self._cond:
                pid, created = child.pid, child.created
            if not pid or pid == child.popen.pid or not self._procs.alive(pid):
                break
            if created is not None and self._procs.created(pid) != created:
                break
            if self._silent(child):
                break
            self._stop.wait(self._watch_interval)
        self._closed(child, code)

    def _closed(self, child: _Child, code: int | None) -> None:
        with self._cond:
            if child.exited:
                return
            child.exited, child.code = True, code
            if self._child is child:
                self._child = None
            self._cond.notify_all()
        if child.pid:
            remove_classic_json(self.data_dir, child.pid)
        if child.log is not None:
            try:
                child.log.close()
            except OSError:
                pass
        logger.info("classic GUI closed (code %s)", code)
        self._publish("classic.closed", {"code": code})
        self._changed()
        if self._on_closed is not None:
            try:
                self._on_closed(code)
            except Exception:
                logger.exception("classic close hook failed")

    def close(self) -> None:
        """End the long-polls because the web UI quits.

        The classic window stays open; its polls fail and it stops polling.
        """
        self._stop.set()
        with self._cond:
            self._cond.notify_all()


def _popen_pid(child: _Child) -> int | None:
    return child.popen.pid if child.popen is not None else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reply(start_response: Callable, status: str, data: Any) -> list[bytes]:
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ],
    )
    return [body]
