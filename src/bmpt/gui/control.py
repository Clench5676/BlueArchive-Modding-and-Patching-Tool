"""Connect the classic GUI to its launching web UI (see docs/webui/desktop.md, "Classic window").

Started with ``--control`` and the variables ``BAMT_CLASSIC_URL`` and ``BAMT_CLASSIC_TOKEN``, the classic window starts
transparent, reports ``ready`` once mapped, and follows the web UI's control lines: ``place``, then ``raise``, and
``tab <name>`` or ``settings`` when the user clicks another greyed nav entry. It reports the number of running tasks
(``busy``), so the web UI never switches the tab under a running task.

A daemon thread long-polls the web UI; the Tk thread drains the lines with ``root.after``, since Tk is only called from
its own thread. When the web UI quits, the polls fail and the window stays open on its own.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, MutableMapping

from ..webui.classic import (
    CONTROL_PATH,
    CONTROL_POLL,
    ENV_TOKEN,
    ENV_URL,
    REPORT_PATH,
    SHOW_FALLBACK,
    TOKEN_HEADER,
    iter_lines,
)

logger = logging.getLogger(__name__)

PUMP_MS = 100
POLL_FAILURES = 3  # consecutive failed polls before the channel counts as gone


def take_channel(environ: MutableMapping[str, str] | None = None) -> tuple[str, str] | None:
    """Consume the web UI's origin and window token so subprocesses cannot inherit them.

    Args:
        environ: Environment mapping to remove channel variables from; None uses os.environ.

    Returns:
        Origin without a trailing slash and token, or None when either variable is absent.
    """
    env = os.environ if environ is None else environ
    url, token = env.pop(ENV_URL, None), env.pop(ENV_TOKEN, None)
    return (url.rstrip("/"), token) if url and token else None


class BusyCounter:
    """Count running classic tasks and notify listeners on the thread changing the count.

    Tasks include threads started by TabFrame.run_in_thread and dialogs between
    set_task_running(True) and set_task_running(False).
    """

    def __init__(self) -> None:
        """Start with no running tasks or listeners."""
        self._lock = threading.Lock()
        self._n = 0
        self._listeners: list[Callable[[int], None]] = []

    @property
    def value(self) -> int:
        """Return the running task count under the counter lock."""
        with self._lock:
            return self._n

    def listen(self, fn: Callable[[int], None]) -> None:
        """Subscribe to future count changes on whichever thread changes the count.

        Args:
            fn: Callback receiving each new nonnegative task count; exceptions are logged and suppressed.
        """
        with self._lock:
            self._listeners.append(fn)

    def add(self, delta: int) -> int:
        """Adjust the count, clamp it at zero, and notify listeners outside the lock.

        Args:
            delta: Number of tasks started, or a negative number for tasks that ended.

        Returns:
            The count immediately after this adjustment.
        """
        with self._lock:
            self._n = max(self._n + delta, 0)
            n, listeners = self._n, list(self._listeners)
        for fn in listeners:
            try:
                fn(n)
            except Exception:
                logger.exception("busy listener failed")
        return n

    def start_thread(self, target: Callable[..., Any], *args: Any) -> threading.Thread:
        """Start a daemon task counted from before thread startup until it ends.

        Args:
            target: Work to run with the supplied positional arguments.
            *args: Arguments passed unchanged to target on the new thread.

        Returns:
            The started thread; the count is restored even if startup or the task fails.
        """

        def run() -> None:
            try:
                target(*args)
            finally:
                self.add(-1)

        thread = threading.Thread(target=run, daemon=True)
        self.add(1)
        try:
            thread.start()
        except BaseException:
            self.add(-1)
            raise
        return thread


busy = BusyCounter()


class ControlClient:
    """Exchange control lines and reports through separate HTTP worker threads.

    The default opener bypasses proxies because the web UI listens on 127.0.0.1.
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        opener: Any = None,
        poll_timeout: float = CONTROL_POLL + 10,
        retry_wait: float = 1.0,
    ):
        """Prepare queues and workers for an authenticated local control channel.

        Args:
            url: Web UI origin; trailing slashes are removed before endpoint paths are appended.
            token: Window token sent in the control authentication header.
            opener: HTTP opener with an open method; None builds one with proxies disabled.
            poll_timeout: HTTP timeout in seconds for polls and reports.
            retry_wait: Seconds between failed polls before the channel is declared closed.
        """
        self.url = url.rstrip("/")
        self.token = token
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._poll_timeout = poll_timeout
        self._retry_wait = retry_wait
        self._lines: queue.Queue[str] = queue.Queue()
        self._reports: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stop = threading.Event()
        self.closed = threading.Event()  # set once the poll thread gave up: the web UI is gone

    def start(self) -> "ControlClient":
        """Start polling and report-sending daemon threads and return this client."""
        threading.Thread(target=self._poll_loop, name="bamt-control", daemon=True).start()
        threading.Thread(target=self._send_loop, name="bamt-report", daemon=True).start()
        return self

    def stop(self) -> None:
        """Signal polling to stop and queue the report sender's shutdown marker."""
        self._stop.set()
        self._reports.put(None)

    def lines(self) -> list[str]:
        """Drain and return control lines received since the last call."""
        out: list[str] = []
        while True:
            try:
                out.append(self._lines.get_nowait())
            except queue.Empty:
                return out

    def report(self, kind: str, **data: Any) -> None:
        """Queue a report from any thread without waiting for HTTP delivery.

        Args:
            kind: Report type, such as ready, busy, or shown.
            **data: JSON fields merged into the report; a type field overrides kind.
        """
        if not self._stop.is_set():
            self._reports.put({"type": kind, **data})

    def _request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {TOKEN_HEADER: self.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.url + path, data=data, headers=headers, method="POST" if body is not None else "GET"
        )
        with self._opener.open(req, timeout=self._poll_timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def _poll_loop(self) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    data = self._request(CONTROL_PATH)
                except urllib.error.HTTPError as e:
                    logger.info("control channel refused (%s)", e.code)  # the web UI forgot this window
                    return
                except (urllib.error.URLError, OSError, ValueError) as e:
                    if isinstance(getattr(e, "reason", e), TimeoutError):
                        continue
                    failures += 1
                    if failures >= POLL_FAILURES:
                        logger.info("control channel gone: %s", e)
                        return
                    self._stop.wait(self._retry_wait)
                    continue
                failures = 0
                for line in data.get("lines", []) if isinstance(data, dict) else []:
                    if isinstance(line, str):
                        self._lines.put(line)
        finally:
            self.closed.set()

    def _send_loop(self) -> None:
        while True:
            item = self._reports.get()
            if item is None:
                return
            try:
                self._request(REPORT_PATH, item)
            except (urllib.error.URLError, OSError, ValueError):
                logger.debug("report %s not sent", item.get("type"), exc_info=True)


class ControlBridge:
    """Apply control lines on the Tk thread and report window readiness and task counts."""

    def __init__(
        self,
        root: Any,
        app: Any,
        client: ControlClient,
        *,
        counter: BusyCounter = busy,
        pump_ms: int = PUMP_MS,
        show_after_ms: int = int(SHOW_FALLBACK * 1000),
    ):
        """Bind a classic window and its task counter to the HTTP channel.

        Args:
            root: Tk root supplying event binding, timers, mapping state, and window attributes.
            app: Classic App with tabs, show_tab, open_settings_dialog, place_window, and raise_window.
            client: Channel receiving commands and queuing readiness, task, and shown reports.
            counter: Task counter preventing navigation while work is running.
            pump_ms: Milliseconds between command-queue drains on the Tk thread.
            show_after_ms: Milliseconds after readiness before showing the window if no raise arrives.
        """
        self.root = root
        self.app = app
        self.client = client
        self.counter = counter
        self.pump_ms = pump_ms
        self.show_after_ms = show_after_ms
        self.ready = False
        self.raised = False

    def start(self) -> "ControlBridge":
        """Start the channel, readiness reporting, and Tk command pump; return this bridge."""
        self.counter.listen(lambda n: self.client.report("busy", n=n))
        self.client.start()
        self.root.bind("<Map>", self._on_map, add="+")
        if self.root.winfo_ismapped():
            self.root.after(0, self._report_ready)
        self.root.after(self.pump_ms, self.pump)
        return self

    def _on_map(self, event: Any) -> None:
        if getattr(event, "widget", None) is self.root:
            self._report_ready()

    def _report_ready(self) -> None:
        if self.ready:
            return
        self.ready = True
        hwnd = 0
        if sys.platform == "win32":
            try:
                hwnd = int(self.root.wm_frame(), 16)
            except (ValueError, TypeError):
                hwnd = 0
        self.client.report("ready", pid=os.getpid(), hwnd=hwnd, exe=sys.executable)
        if self.counter.value:
            self.client.report("busy", n=self.counter.value)
        self.root.after(self.show_after_ms, self._show_anyway)

    def _show_anyway(self) -> None:
        # never left invisible: no raise came (the web UI is gone or gave up waiting)
        if not self.raised:
            self.root.attributes("-alpha", 1.0)

    def pump(self) -> None:
        """Reschedule the Tk pump and apply queued commands, logging individual failures."""
        self.root.after(self.pump_ms, self.pump)  # first, so a nested event loop (a dialog) keeps it running
        for cmd in iter_lines(self.client.lines()):
            try:
                self.apply(cmd)
            except Exception:
                logger.exception("control line %s failed", cmd)

    def apply(self, cmd: tuple) -> None:
        """Apply a parsed control command, allowing navigation only while idle.

        Args:
            cmd: iter_lines tuple: tab and its name, settings, place and its geometry, or raise.
        """
        kind = cmd[0]
        if kind == "tab":
            tab = getattr(self.app.tabs, cmd[1], None)
            if tab is not None and self.counter.value == 0:
                self.app.show_tab(tab)
                self.client.report("shown", tab=cmd[1])
        elif kind == "settings":
            if self.counter.value == 0:
                self.root.after(0, self.app.open_settings_dialog)
                self.client.report("shown", tab="settings")
        elif kind == "place":
            self.app.place_window(*cmd[1:])
        elif kind == "raise":
            self.raised = True
            self.app.raise_window()
