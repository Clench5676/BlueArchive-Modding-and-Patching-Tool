"""Standard Windows frame and native window state, without client-area extensions."""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes

from .window_chrome import Rect

WM_SIZING, WM_DPICHANGED = 0x214, 0x2E0


def aspect_rect(rect, edge, frame, minimum=(960, 540), dpi=96):
    """Constrain client pixels to 16:9, anchoring the opposite dragged edges.

    Corners project onto the nearest point on the aspect line. Integer RECTs permit at most half a physical pixel
    of rounding error in the dependent dimension.

    Args:
        rect: The window rectangle being dragged, in physical pixels.
        edge: The ``WM_SIZING`` edge code, 1 to 8: left, right, top, top left, top right, bottom, bottom left,
            bottom right.
        frame: The width and height of the window frame around the client area, in physical pixels.
        minimum: The smallest client size, in CSS pixels.
        dpi: The window's DPI; 96 is a scale of 100%.

    Returns:
        The corrected window rectangle, in physical pixels.

    Raises:
        ValueError: ``edge`` is not a known sizing edge.
    """
    if edge not in range(1, 9):
        raise ValueError("Unknown sizing edge")
    fw, fh = frame
    ratio = 16 / 9
    min_w, min_h = (math.ceil(value * dpi / 96) for value in minimum)
    width, height = rect.right - rect.left - fw, rect.bottom - rect.top - fh
    if edge in (1, 2):
        width = max(width, min_w, math.ceil(min_h * ratio))
        height = round(width / ratio)
    else:
        if edge not in (3, 6):
            height = (width * ratio + height) / (ratio * ratio + 1)
        height = max(round(height), min_h, math.ceil(min_w / ratio))
        width = round(height * ratio)
    width, height = width + fw, height + fh
    left = rect.right - width if edge in (1, 4, 7) else rect.left
    top = rect.bottom - height if edge in (3, 4, 5) else rect.top
    return Rect(left, top, left + width, top + height)


def private_user32():
    """Return a private copy of user32, so that its prototypes are ours alone.

    ``ctypes.windll.user32`` is shared with pywebview, whose ``move`` passes ``None`` sizes to an unprototyped
    SetWindowPos; prototypes set there turn every one of those calls into ``ctypes.ArgumentError``.
    """
    return ctypes.WinDLL("user32", use_last_error=True)


class WindowsHost:
    """The Windows chrome host: the standard frame stays, and the client area keeps its 16:9 shape.

    It reports the window state and runs the window commands; it has no native buttons to measure.
    """

    def __init__(self, window, owner, *, api=None, subclass_factory=None):
        """Create the host, not yet installed.

        Args:
            window: The pywebview window, whose WinForms form is used.
            owner: The ``WindowChrome`` that publishes what the host reports.
            api: The user32 module; None uses ``private_user32()``. For tests.
            subclass_factory: Creates the window's message hook; None uses ``WindowSubclass``. For tests.
        """
        self.window, self.owner, self.form = window, owner, window.native
        self.user, self.subclass_factory = api, subclass_factory or WindowSubclass
        self.hook = None
        self.old_minimum = None

    def dispatch(self, fn):
        """Run a function on the form's GUI thread, at once when already there."""
        from System import Action

        if self.form.InvokeRequired:
            self.form.BeginInvoke(Action(fn))
        else:
            fn()

    def install(self):
        """Set the user32 prototypes, hook the window's messages and fit the client area to 16:9."""
        # pywebview creates the normal resizable frame. Its caption, hit testing,
        # resize borders and WebView client bounds remain owned by WinForms/Windows.
        self.user = self.user or private_user32()
        for name, arguments, result in (
            (
                "GetWindowLongPtrW" if hasattr(self.user, "GetWindowLongPtrW") else "GetWindowLongW",
                [wintypes.HWND, ctypes.c_int],
                ctypes.c_ssize_t,
            ),
            ("GetMenu", [wintypes.HWND], wintypes.HMENU),
            ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (
                "AdjustWindowRectExForDpi",
                [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD, wintypes.UINT],
                wintypes.BOOL,
            ),
            (
                "SetWindowPos",
                [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT],
                wintypes.BOOL,
            ),
            ("GetDpiForWindow", [wintypes.HWND], wintypes.UINT),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result
        self.old_minimum = self.form.MinimumSize
        self._attach_handle()
        self._fit_client(initial=True)
        self.on_resize = lambda *_: self.owner.refresh()
        self.form.Resize += self.on_resize
        if hasattr(self.form, "DpiChanged"):
            self.form.DpiChanged += self.on_resize
        self.form.HandleCreated += self.on_handle

    def _attach_handle(self):
        if self.hook is not None:
            self.hook.dispose()
        self.handle = int(self.form.Handle.ToInt64())
        self.hook = self.subclass_factory(self.handle, self.message, self._message_failed)
        self._set_minimum()

    def on_handle(self, *_):
        """Hook the new window handle after WinForms recreated it."""
        # WinForms may recreate its handle while entering/leaving fullscreen.
        try:
            self._attach_handle()
        except Exception as error:
            self.owner.fail(error)

    def _message_failed(self, error):
        from System import Action

        self.form.BeginInvoke(Action(lambda: self.owner.fail(error)))

    def dpi(self):
        """Return the window's DPI; 96 is a scale of 100%."""
        return self.user.GetDpiForWindow(self.handle) or self.form.DeviceDpi

    def frame_size(self):
        """Return the width and height of the standard window frame around the client area, in physical pixels.

        Raises:
            OSError: Windows cannot measure the frame.
        """
        get_style = getattr(self.user, "GetWindowLongPtrW", None) or self.user.GetWindowLongW
        rect = wintypes.RECT(0, 0, 0, 0)
        if not self.user.AdjustWindowRectExForDpi(
            ctypes.byref(rect),
            get_style(self.handle, -16),
            bool(self.user.GetMenu(self.handle)),
            get_style(self.handle, -20),
            self.dpi(),
        ):
            raise OSError("Cannot measure the standard Windows frame")
        return rect.right - rect.left, rect.bottom - rect.top

    def _set_minimum(self):
        from System.Drawing import Size

        fw, fh = self.frame_size()
        minimum = self.window.min_size
        self.form.MinimumSize = Size(
            math.ceil(minimum[0] * self.dpi() / 96) + fw, math.ceil(minimum[1] * self.dpi() / 96) + fh
        )

    def _fit_client(self, *, initial=False):
        if any(self.state().values()):
            return
        frame = self.frame_size()
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(self.handle, ctypes.byref(rect)):
            raise OSError("Cannot measure the Windows window")
        if initial:
            rect.right = rect.left + round(self.window.initial_width * self.dpi() / 96) + frame[0]
            rect.bottom = rect.top + round(self.window.initial_height * self.dpi() / 96) + frame[1]
        corrected = aspect_rect(
            Rect(rect.left, rect.top, rect.right, rect.bottom), 2, frame, self.window.min_size, self.dpi()
        )
        if not self.user.SetWindowPos(
            self.handle,
            None,
            corrected.left,
            corrected.top,
            corrected.right - corrected.left,
            corrected.bottom - corrected.top,
            0x14,
        ):  # SWP_NOZORDER | SWP_NOACTIVATE
            raise OSError("Cannot set the Windows client aspect ratio")

    def message(self, code, wparam, lparam, default):
        """Handle a window message: keep the aspect ratio while sizing, refit after a DPI change.

        Args:
            code: The window message.
            wparam: The message's WPARAM; for ``WM_SIZING`` the dragged edge.
            lparam: The message's LPARAM; for ``WM_SIZING`` the address of the window RECT to correct.
            default: Forwards the message to the window's previous procedure and returns its result.

        Returns:
            The message's result; 1 when ``WM_SIZING`` was handled here.
        """
        if code == WM_SIZING and not any(self.state().values()):
            rect = wintypes.RECT.from_address(lparam)
            corrected = aspect_rect(
                Rect(rect.left, rect.top, rect.right, rect.bottom),
                wparam,
                self.frame_size(),
                self.window.min_size,
                self.dpi(),
            )
            rect.left, rect.top, rect.right, rect.bottom = (
                corrected.left,
                corrected.top,
                corrected.right,
                corrected.bottom,
            )
            return 1
        result = default()
        if code == WM_DPICHANGED:
            # WinForms applies the new DPI/suggested frame first. Maximize/fullscreen
            # keep their screen/work-area geometry, even on non-16:9 monitors.
            self._set_minimum()
            self._fit_client()
        return result

    def state(self):
        """Return the window state: ``maximized``, ``minimized`` and ``fullscreen`` as booleans."""
        name = str(self.form.WindowState)
        return {
            "maximized": name == "Maximized",
            "minimized": name == "Minimized",
            "fullscreen": bool(self.form.is_fullscreen),
        }

    def measure(self, layout):
        """Return None: the standard caption is outside the WebView's client area, so there is nothing to measure.

        Args:
            layout: The page's toolbar layout; unused here.
        """
        return None

    def command(self, action):
        """Do a window action.

        Args:
            action: ``minimize``, ``maximize`` (which restores a maximized window) or ``fullscreen``.
        """
        from System.Windows.Forms import FormWindowState

        if action == "fullscreen":
            self.window.toggle_fullscreen()
        else:
            self.form.WindowState = (
                FormWindowState.Minimized
                if action == "minimize"
                else FormWindowState.Normal
                if self.state()["maximized"]
                else FormWindowState.Maximized
            )

    def dispose(self):
        """Remove the form's event handlers and the message hook, and restore the form's minimum size."""
        if hasattr(self, "on_resize"):
            self.form.Resize -= self.on_resize
            if hasattr(self.form, "DpiChanged"):
                self.form.DpiChanged -= self.on_resize
            self.form.HandleCreated -= self.on_handle
            del self.on_resize
        if self.hook is not None:
            self.hook.dispose()
            self.hook = None
        if self.old_minimum is not None and not self.form.IsDisposed:
            self.form.MinimumSize = self.old_minimum
            self.old_minimum = None


class WindowSubclass:
    """Public comctl32 subclass, on the form's GUI thread, with a retained callback.

    This avoids managed virtual-method overrides and never subclasses WebView2's
    foreign-process child HWNDs.
    """

    def __init__(self, handle, message, failed, *, api=None, callback_type=None):
        """Subclass a window.

        Args:
            handle: The window's HWND.
            message: ``message(code, wparam, lparam, default)``, called for every window message; its result is
                the message's result.
            failed: Called with an exception that ``message`` raised; the message then takes its default.
            api: The comctl32 module; None uses ``ctypes.windll.comctl32``. For tests.
            callback_type: The ctypes function-type factory; None uses ``ctypes.WINFUNCTYPE``. For tests.

        Raises:
            OSError: Windows refuses the subclass.
        """
        self.api = api or ctypes.windll.comctl32
        callback_type = callback_type or ctypes.WINFUNCTYPE
        self.handle, self.identifier = handle, id(self)
        prototype = callback_type(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )

        def callback(hwnd, code, wparam, lparam, identifier, data):
            forwarded, result = False, 0

            def default():
                nonlocal forwarded, result
                if not forwarded:
                    result = self.api.DefSubclassProc(hwnd, code, wparam, lparam)
                    forwarded = True
                return result

            try:
                return message(code, wparam, lparam, default)
            except Exception as error:
                failed(error)
                return default()

        self.callback = prototype(callback)
        self.api.SetWindowSubclass.argtypes = [wintypes.HWND, prototype, ctypes.c_size_t, ctypes.c_size_t]
        self.api.RemoveWindowSubclass.argtypes = [wintypes.HWND, prototype, ctypes.c_size_t]
        self.api.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self.api.DefSubclassProc.restype = ctypes.c_ssize_t
        if not self.api.SetWindowSubclass(handle, self.callback, self.identifier, 0):
            raise OSError("Native message subclass installation failed")

    def dispose(self):
        """Remove the subclass."""
        self.api.RemoveWindowSubclass(self.handle, self.callback, self.identifier)
