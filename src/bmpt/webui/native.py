"""pywebview-only features: the window, file dialogs and dropped file paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def pick_files(
    window: Any, file_types: Iterable[str] = (), multiple: bool = True, *, directory: str | None = None
) -> list[Path]:
    """Show the native open-file dialog.

    Args:
        window: The pywebview window the dialog belongs to.
        file_types: Filters in pywebview's form, such as ``"Bundle (*.bundle;*.backup)"``; none shows all files.
        multiple: Whether several files may be chosen.
        directory: The folder the dialog opens in; None leaves it to the system.

    Returns:
        The chosen files; empty when the dialog was cancelled.
    """
    import webview

    result = window.create_file_dialog(
        webview.FileDialog.OPEN,
        allow_multiple=multiple,
        file_types=tuple(file_types),
        **({"directory": directory} if directory else {}),
    )
    return [Path(p) for p in result or ()]


def pick_folder(window: Any, multiple: bool = True) -> list[Path]:
    """Show the native folder dialog.

    Args:
        window: The pywebview window the dialog belongs to.
        multiple: Whether several folders may be chosen.

    Returns:
        The chosen folders; empty when the dialog was cancelled.
    """
    import webview

    result = window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=multiple)
    return [Path(p) for p in result or ()]


def watch_drops(
    window: Any, on_paths: Callable[[list[str]], None], *, stopped: Callable[[], bool] = lambda: False
) -> bool:
    """Report the full paths of files dropped on the page.

    pywebview adds the paths to a drop event only when its handler is registered from Python.

    Args:
        window: The pywebview window whose page is watched.
        on_paths: Called with the dropped files' full paths.
        stopped: Whether the window is shutting down; registration stops at the next event name and drops are
            ignored from then on.

    Returns:
        Whether every handler was registered; False when shutdown abandoned the registration.
    """
    from webview.dom import DOMEventHandler

    def on_drop(event: dict) -> None:
        if stopped():
            return
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            on_paths(paths)

    def noop(_event: dict) -> None:
        pass

    for name in ("dragenter", "dragstart", "dragover", "drop"):
        if stopped():
            logger.info("Native drop registration: abandoned event=%s", name)
            return False
        logger.info("Native drop registration: begin event=%s", name)
        event = getattr(window.dom.document.events, name)
        event += DOMEventHandler(
            on_drop if name == "drop" else noop, True, True, debounce=500 if name == "dragover" else 0
        )
    if stopped():
        logger.info("Native drop registration: abandoned after=drop")
        return False
    logger.info("Native drop registration: complete")
    return True


def renderer_supported(renderer: str) -> bool:
    """Return whether the front end can run in a renderer; MSHTML (Internet Explorer) cannot.

    pywebview falls back to MSHTML where WebView2 is missing.

    Args:
        renderer: The renderer name pywebview reports at initialization.
    """
    return renderer not in ("mshtml",)


# Supported runtime floor; includes structuredClone and inert for safe UI transitions.
MIN_WEBVIEW2_MAJOR = 105
WEBVIEW2_CLIENT = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_version(registry=None) -> str | None:
    """Find the newest installed WebView2 Evergreen runtime on Windows.

    Per-user and per-machine runtimes are read in both registry views.

    Args:
        registry: The ``winreg`` module; None imports it. A stand-in lets tests run it on other systems.

    Returns:
        The version string, such as ``"120.0.2210.91"``; None when no runtime is installed.
    """
    if registry is None:
        import winreg as registry
    versions = []
    for hive in (registry.HKEY_CURRENT_USER, registry.HKEY_LOCAL_MACHINE):
        for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
            try:
                with registry.OpenKey(hive, WEBVIEW2_CLIENT, 0, registry.KEY_READ | view) as key:
                    value = registry.QueryValueEx(key, "pv")[0]
                parts = tuple(int(p) for p in value.split("."))
                if len(parts) == 4 and any(parts):
                    versions.append((parts, value))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return max(versions)[1] if versions else None


def webview2_supported(version: str | None) -> bool:
    """Return whether a WebView2 version meets ``MIN_WEBVIEW2_MAJOR``; False for None or an unreadable version."""
    try:
        return int(version.split(".")[0]) >= MIN_WEBVIEW2_MAJOR
    except (AttributeError, ValueError):
        return False


def engine_info() -> dict:
    """Describe the web engine the window uses.

    Returns:
        ``engine`` (WebView2, WebKit or unknown) and ``version``, which is None when it cannot be read.
    """
    import sys

    if sys.platform == "win32":
        return {"engine": "WebView2", "version": webview2_version()}
    if sys.platform == "darwin":
        import plistlib

        try:
            with Path("/System/Library/Frameworks/WebKit.framework/Resources/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            version = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
        except (OSError, ValueError):
            version = None
        return {"engine": "WebKit", "version": version}
    return {"engine": "unknown", "version": None}


def report_folders() -> list[Path]:
    """Return the Desktop and Downloads folders, where a report is saved.

    On Windows they come from the registry, which follows a redirected Desktop or Downloads.
    """
    import sys

    if sys.platform == "win32":
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        result = []
        for key, fallback in (("Desktop", "Desktop"), ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads")):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as handle:
                    value = winreg.QueryValueEx(handle, key)[0]
                result.append(Path(winreg.ExpandEnvironmentStrings(value)))
            except OSError:
                result.append(Path.home() / fallback)
        return result
    return [Path.home() / "Desktop", Path.home() / "Downloads"]


def copy_text(text: str) -> None:
    """Put text on the system clipboard, without pywebview.

    Args:
        text: The text to copy.

    Raises:
        OSError: The Windows clipboard could not be allocated, locked, opened or written.
        subprocess.CalledProcessError: The clipboard tool of macOS or Linux (``pbcopy``, ``xclip``) failed.
    """
    import subprocess
    import sys

    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        # Private libraries: prototypes set on ctypes.windll would change pywebview's calls as well.
        user, kernel = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
        user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user.SetClipboardData.restype = wintypes.HANDLE
        data = (text + "\0").encode("utf-16-le")
        handle = kernel.GlobalAlloc(0x0002, len(data))
        if not handle:
            raise OSError("Clipboard allocation failed")
        try:
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                raise OSError("Clipboard lock failed")
            ctypes.memmove(pointer, data, len(data))
            kernel.GlobalUnlock(handle)
            if not user.OpenClipboard(None):
                raise OSError("Clipboard unavailable")
            try:
                user.EmptyClipboard()
                if not user.SetClipboardData(13, handle):
                    raise OSError("Clipboard write failed")
                handle = None  # Ownership passes to the OS.
            finally:
                user.CloseClipboard()
        finally:
            if handle:
                kernel.GlobalFree(handle)
    else:
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True, timeout=5)


def message_box(title: str, message: str) -> None:
    """Show a blocking error dialog that depends on neither pywebview, its renderer nor the HTTP server.

    Args:
        title: The dialog's title.
        message: The dialog's text.
    """
    import sys

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    elif sys.platform == "darwin":
        import subprocess

        script = "on run argv\ndisplay alert (item 1 of argv) message (item 2 of argv) as critical\nend run"
        subprocess.run(["osascript", "-e", script, title, message], check=True)
    else:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        try:
            messagebox.showerror(title, message, parent=root)
        finally:
            root.destroy()


def startup_failure(reports, error: BaseException, *, runtime: bool = False) -> dict | None:
    """Report a failure to start the window: write a report and tell the user in an error dialog.

    Args:
        reports: The run's ``Reports``, which write the report file.
        error: What went wrong; its text goes into the report.
        runtime: The failure is the missing or unsupported WebView2 runtime; the dialog names it and its download
            address.

    Returns:
        The report as ``Reports.generate`` returned it; None when no report could be written.
    """
    from bmpt.engine import t

    result, report_path = None, None
    try:
        reports.frontend_event({"kind": "native", "message": str(error), "phase": "native.initialize"})
        result = reports.generate()
        if result and Path(result["path"]).is_file():
            report_path = result["path"]
    except Exception:
        logger.warning("Startup report unavailable", exc_info=True)
    text = t("webui.recovery.runtime" if runtime else "webui.recovery.native")
    if report_path:
        text += "\n\n" + report_path
    if runtime:
        text += "\n\n" + WEBVIEW2_URL
    message_box(t("webui.recovery.failed"), text)
    return result


def _install_edit_menu(api, main):
    from bmpt.engine import t

    for existing in main.itemArray():
        submenu = existing.submenu()
        if submenu and any(str(item.action()) == "copy:" for item in submenu.itemArray()):
            return
    title = t("webui.window.edit")
    menu = api.NSMenu.alloc().initWithTitle_(title)
    item = api.NSMenuItem.alloc().init()
    item.setTitle_(title)
    item.setSubmenu_(menu)
    for key, selector, shortcut in [
        ("cut", "cut:", "x"),
        ("copy", "copy:", "c"),
        ("paste", "paste:", "v"),
        ("selectAll", "selectAll:", "a"),
    ]:
        menu.addItemWithTitle_action_keyEquivalent_(t("webui.window." + key), selector, shortcut)
    main.addItem_(item)


def install_edit_menu() -> None:
    """Add an Edit menu on macOS, which restores the editing shortcuts without pywebview's default menus.

    Does nothing on other systems.
    """
    import sys

    if sys.platform != "darwin":
        return
    import AppKit
    from PyObjCTools import AppHelper

    def install():
        main = AppKit.NSApplication.sharedApplication().mainMenu()
        if main is not None:
            _install_edit_menu(AppKit, main)

    AppHelper.callAfter(install)


def window_title() -> str:
    """Return the native window's title: the App's name, with ``[DEV]`` before it in dev mode (``devmode.title``)."""
    from bmpt.engine import t

    from ..core import devmode

    return devmode.title(t("ui.app_title"))


def _set_launch_services_name(name: str) -> bool:
    """Set the display name LaunchServices keeps for this process, which the Dock and the app switcher show.

    Uses LaunchServices' private ``_LSSetApplicationInformationItem`` with ``_kLSDisplayNameKey``; there is no
    public call for it.

    Args:
        name: The new display name.

    Returns:
        True when LaunchServices accepted it.
    """
    import ctypes

    services = ctypes.CDLL(
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/LaunchServices"
    )
    foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    current_asn = services["_LSGetCurrentApplicationASN"]
    current_asn.restype = ctypes.c_void_p
    current_asn.argtypes = []
    set_item = services["_LSSetApplicationInformationItem"]
    set_item.restype = ctypes.c_int32
    set_item.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    create_string = foundation.CFStringCreateWithCString
    create_string.restype = ctypes.c_void_p
    create_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    release = foundation.CFRelease
    release.argtypes = [ctypes.c_void_p]

    asn = current_asn()
    key = ctypes.c_void_p.in_dll(services, "_kLSDisplayNameKey").value
    if not asn or not key:
        return False
    value = create_string(None, name.encode("utf-8"), 0x08000100)  # kCFStringEncodingUTF8
    if not value:
        return False
    try:
        # -2 is kLSDefaultSessionID, the session of the current user
        return set_item(-2, asn, key, value, None) == 0
    finally:
        release(value)


def mark_dev_dock() -> None:
    """Mark the App's Dock icon in dev mode: a ``DEV`` badge, and ``[DEV]`` before the name the Dock shows.

    Does nothing outside dev mode and on other systems than macOS. A failure is logged and never stops the App.
    """
    import sys

    from ..core import devmode

    if sys.platform != "darwin" or not devmode.active():
        return
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except Exception:
        logger.debug("dev mode Dock mark unavailable", exc_info=True)
        return

    def mark():
        try:
            AppKit.NSApplication.sharedApplication().dockTile().setBadgeLabel_("DEV")
        except Exception:
            logger.debug("dev mode Dock badge not set", exc_info=True)
        try:
            name = str(AppKit.NSRunningApplication.currentApplication().localizedName() or "")
            marked = devmode.title(name)
            if name and marked != name and not _set_launch_services_name(marked):
                logger.debug("LaunchServices refused the dev mode display name")
        except Exception:
            logger.debug("dev mode display name not set", exc_info=True)

    AppHelper.callAfter(mark)


def keep_web_view_black(window) -> None:
    """Stop the macOS web view from painting a background of its own.

    WKWebView fills itself white until the page has drawn its first frame, which shows as a white flash after the
    window opens. Without that fill the window's black (``background_color``) stays visible until the page paints,
    and the page's own backdrop is black too.

    Args:
        window: The pywebview window, once it exists.
    """
    import AppKit
    from webview.platforms.cocoa import BrowserView

    try:
        view = BrowserView.instances[window.uid].webview
        view.setValue_forKey_(False, "drawsBackground")
        if view.respondsToSelector_("setUnderPageBackgroundColor:"):
            view.setUnderPageBackgroundColor_(AppKit.NSColor.blackColor())
    except Exception:  # a flash is better than no window
        logger.warning("could not make the web view background transparent", exc_info=True)


def install_window_commands(services, chrome):
    """Route Command-Q and Command-W through the App's own quit, and add Command-M and Control-Command-F.

    Command-Q must be caught before WKWebView's direct ``app.stop_`` path, also while an input has the focus. The
    menu's Quit item is retargeted the same way, and the Edit menu is rebuilt after pywebview rebuilds the menus.

    Args:
        services: The Services; ``quit`` handles the close request.
        chrome: The window chrome; not used by the commands.

    Returns:
        A function that removes the key monitor and the observer; its ``refresh`` attribute reapplies the menu
        changes.
    """
    import AppKit
    from PyObjCTools import AppHelper
    from ..core.errors import ServiceError

    window = services.window.native

    def request_close():
        try:
            services.quit()
        except ServiceError as error:
            if error.code == "busy":
                services.events.publish("app.busy")
            else:
                logger.warning("Native close refused: %s", error.code)

    class WindowCommands(AppKit.NSObject):
        def closeWindow_(self, sender):
            request_close()

    target = WindowCommands.alloc().init()

    def key(event):
        flags = event.modifierFlags()
        if event.window() != window or not flags & AppKit.NSEventModifierFlagCommand:
            return event
        if flags & (AppKit.NSEventModifierFlagOption | AppKit.NSEventModifierFlagShift):
            return event
        char = str(event.charactersIgnoringModifiers()).lower()
        if char in ("q", "w") and not flags & AppKit.NSEventModifierFlagControl:
            request_close()
            return None
        if char == "m" and not flags & AppKit.NSEventModifierFlagControl:
            window.performMiniaturize_(None)
            return None
        if char == "f" and flags & AppKit.NSEventModifierFlagControl:
            window.toggleFullScreen_(None)
            return None
        return event

    monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(AppKit.NSEventMaskKeyDown, key)
    disposed = False

    def refresh():
        if disposed:
            return
        main = AppKit.NSApplication.sharedApplication().mainMenu()
        if main is None:
            return
        _install_edit_menu(AppKit, main)
        for item in main.itemArray():
            submenu = item.submenu()
            for command in submenu.itemArray() if submenu else []:
                if str(command.action()) == "terminate:":
                    command.setTarget_(target)
                    command.setAction_("closeWindow:")

    # pywebview 6.2.1 rebuilds menus in first_show and windowDidBecomeKey_.
    # Defer past its delegate and first_show so the rebuilt menu receives Edit
    # and guarded Quit, regardless of notification observer ordering.
    centre = AppKit.NSNotificationCenter.defaultCenter()
    observer = centre.addObserverForName_object_queue_usingBlock_(
        AppKit.NSWindowDidBecomeKeyNotification, window, None, lambda notification: AppHelper.callAfter(refresh)
    )
    refresh()
    AppHelper.callAfter(refresh)

    def dispose():
        nonlocal disposed
        disposed = True
        centre.removeObserver_(observer)
        AppKit.NSEvent.removeMonitor_(monitor)

    dispose.refresh = refresh
    return dispose
