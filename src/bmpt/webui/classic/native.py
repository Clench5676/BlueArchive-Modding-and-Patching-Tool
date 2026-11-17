"""Native window operations on the classic window: Cocoa on macOS, Win32 on Windows."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any, Callable

from .placement import Rect, mac_placement, win_placement
from .processes import (
    ASFW_ANY,
    MDT_EFFECTIVE_DPI,
    MONITOR_DEFAULTTONEAREST,
    SW_RESTORE,
    SWP_NOACTIVATE,
    SWP_NOZORDER,
    ClassicProcess,
)
from .protocol import place_line


logger = logging.getLogger(__package__)


class NativeOps:
    """What the web UI's window does for the hand-off.

    This base does nothing, as when there is no window (``--browser``, ``--serve``).
    """

    def place(self, pid: int | None, hwnd: int, size: tuple[int, int]) -> str | None:
        """Place the child's window over the BAMT window.

        Args:
            pid: The child's process id, if reported.
            hwnd: The child's window handle (Windows), if reported; 0 when none.
            size: The classic window's logical size.

        Returns:
            A ``place`` line for the child to apply; None when the window was placed natively or cannot be.
        """
        return None

    def before_raise(self, pid: int | None) -> None:
        """Hand the foreground over to the child, just before ``raise`` is sent.

        Args:
            pid: The child's process id, if reported.
        """

    def raise_process(self, proc: ClassicProcess) -> None:
        """Bring a classic window found through ``classic.json`` (no control channel) to the front.

        Args:
            proc: The window ``classic.json`` names.
        """


class CocoaOps(NativeOps):
    """The window operations on macOS, where pywebview's ``window.native`` is the ``NSWindow``.

    AppKit is called on the main thread only, and the answer is waited for only from another thread (the RPC's
    request thread), never on the main thread itself.
    """

    WAIT = 2.0

    def __init__(self, window: Any):
        """Wrap the window.

        Args:
            window: The pywebview window.
        """
        self.window = window

    def _on_main(self, fn: Callable[[], Any], wait: float = WAIT) -> Any:
        if threading.current_thread() is threading.main_thread():
            return fn()
        from PyObjCTools import AppHelper

        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["value"] = fn()
            except Exception:
                logger.exception("main-thread call failed")
            finally:
                done.set()

        AppHelper.callAfter(run)
        if not done.wait(wait):
            logger.warning("the main thread did not answer in %.0f s", wait)
        return box.get("value")

    def place(self, pid: int | None, hwnd: int, size: tuple[int, int]) -> str | None:
        """Compute the ``place`` line from the BAMT window's frame and the screens; None without a window."""

        def read() -> tuple[Rect, list[tuple[Rect, Rect]]] | None:
            import AppKit

            native = getattr(self.window, "native", None)
            screens = list(AppKit.NSScreen.screens() or ())
            if native is None or not screens:
                return None

            def rect(r: Any) -> Rect:
                return Rect(r.origin.x, r.origin.y, r.size.width, r.size.height)

            return rect(native.frame()), [(rect(s.frame()), rect(s.visibleFrame())) for s in screens]

        got = self._on_main(read)
        return place_line(mac_placement(got[0], got[1], size)) if got else None

    @staticmethod
    def _hand_over(pid: int) -> None:
        """Make the process ``pid`` the active app (main thread).

        From macOS 14 an app activates another only by yielding its own activation to it first; earlier, by
        ignoring the other apps.
        """
        import AppKit

        target = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if target is None:
            return
        me = AppKit.NSApplication.sharedApplication()
        if me.respondsToSelector_("yieldActivationToApplication:"):
            me.yieldActivationToApplication_(target)  # the classic child also activates itself on raise
            target.activateWithOptions_(AppKit.NSApplicationActivateAllWindows)
        else:
            target.activateWithOptions_(
                AppKit.NSApplicationActivateAllWindows | AppKit.NSApplicationActivateIgnoringOtherApps
            )

    def before_raise(self, pid: int | None) -> None:
        """Make the child the active app, before the raise line is sent."""
        if pid:
            self._on_main(lambda: self._hand_over(pid), wait=1.0)  # done before the child reads its raise

    def raise_process(self, proc: ClassicProcess) -> None:
        """Make the window's process the active app."""
        self._on_main(lambda: self._hand_over(proc.pid), wait=1.0)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


class WinOps(NativeOps):
    """The window operations on Windows, where pywebview's ``window.native`` is the WinForms form.

    The web UI is DPI aware (pywebview calls ``SetProcessDPIAware``), so its bounds and the monitors' work areas
    are physical pixels. The parent places the child's window itself with ``SetWindowPos`` on the ``hwnd`` the
    child reported.
    """

    def __init__(self, window: Any):
        """Wrap the window.

        Args:
            window: The pywebview window.
        """
        self.window = window

    @staticmethod
    def _user32() -> Any:
        from ctypes import wintypes

        u32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        u32.MonitorFromRect.restype = wintypes.HANDLE
        u32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), wintypes.DWORD]
        u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        u32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.IsIconic.argtypes = [wintypes.HWND]
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        return u32

    def _bounds(self, u32: Any) -> Rect | None:
        native = getattr(self.window, "native", None)
        if native is None:
            return None
        try:
            b = native.Bounds
            return Rect(b.X, b.Y, b.Width, b.Height)
        except Exception:
            logger.debug("no form bounds", exc_info=True)
        try:
            r = _RECT()
            if u32.GetWindowRect(int(native.Handle.ToInt64()), ctypes.byref(r)):
                return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            logger.debug("no window rect", exc_info=True)
        return None

    @staticmethod
    def _dpi(monitor: Any) -> int:
        try:
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            shcore = ctypes.WinDLL("shcore")  # type: ignore[attr-defined]
            if shcore.GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                return int(dpi_x.value) or 96
        except (OSError, AttributeError):
            logger.debug("GetDpiForMonitor is unavailable", exc_info=True)
        return 96

    def place(self, pid: int | None, hwnd: int, size: tuple[int, int]) -> str | None:
        """Move the child's window over the BAMT window with ``SetWindowPos``.

        Returns:
            Always None: nothing is left for the child to apply.
        """
        if not hwnd:
            return None
        u32 = self._user32()
        bounds = self._bounds(u32)
        if bounds is None:
            return None
        cx, cy = (int(c) for c in bounds.centre)
        probe = _RECT(cx, cy, cx + 1, cy + 1)  # the monitor that holds the BAMT window's centre
        monitor = u32.MonitorFromRect(ctypes.byref(probe), MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not monitor or not u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        w = info.rcWork
        rect = win_placement(bounds, Rect(w.left, w.top, w.right - w.left, w.bottom - w.top), self._dpi(monitor), size)
        u32.SetWindowPos(hwnd, None, int(rect.x), int(rect.y), int(rect.w), int(rect.h), SWP_NOZORDER | SWP_NOACTIVATE)
        return None

    def before_raise(self, pid: int | None) -> None:
        """Allow any process to take the foreground, before the raise line is sent."""
        # ASFW_ANY: the child's own pid would be the onefile launcher's, not the process that owns the Tk window
        self._user32().AllowSetForegroundWindow(ASFW_ANY & 0xFFFFFFFF)

    def raise_process(self, proc: ClassicProcess) -> None:
        """Restore the window if it is minimized and bring it to the foreground."""
        u32 = self._user32()
        u32.AllowSetForegroundWindow(ASFW_ANY & 0xFFFFFFFF)
        if proc.hwnd:
            if u32.IsIconic(proc.hwnd):
                u32.ShowWindow(proc.hwnd, SW_RESTORE)
            u32.SetForegroundWindow(proc.hwnd)


def native_ops(window: Any) -> NativeOps:
    """Return the native window operations of this system.

    Args:
        window: The pywebview window; None gives operations that do nothing.
    """
    if window is None:
        return NativeOps()
    if sys.platform == "darwin":
        return CocoaOps(window)
    if sys.platform == "win32":
        return WinOps(window)
    return NativeOps()
