"""Entry point of the web UI: ``bamt-web`` (or ``python -m bmpt.webui``)."""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Literal

from ..core.localization import register_locales

register_locales()

from ..core import devmode
from ..host.data_upgrade import DataUpgrade
from ..host.format_manifest import DATA_MANIFEST
from ..host.store_formats import NAMESPACE
from bmpt.engine import EXE_DIR, i18n_manager, t
from .api import build_api
from .instance import InstanceLock, raise_running, read_info
from .server import Server, WebApp
from .services import Services
from .events import EventBus
from .rpc import RpcRegistry
from .upgrade import UpgradeBootstrap
from .session_log import RedactingFormatter, open_run_log
from .diagnostics import Reports, install_exception_hooks

logger = logging.getLogger(__name__)

CONFIG_POLL_SECONDS = 2.0


def build(
    port: int = 0,
    services: Services | None = None,
    token: str | None = None,
    *,
    safe_mode: bool = False,
    reports: Reports | None = None,
    upgrade: DataUpgrade | None = None,
) -> tuple[Services, Server]:
    """Wire the services, the RPC methods and the HTTP server; the server is not started.

    Args:
        port: The port to listen on at 127.0.0.1; 0 takes a free one.
        services: Services to serve, for tests and tools; None creates them.
        token: The session token the page must send; None picks a random one (a fixed one is for development).
        safe_mode: Start without game assets, Spine or restored pages. Applies only to the services created here.
        upgrade: Coordinator supplying the committed paths.
        reports: The run's crash reports, which take over the services' report state and secrets so that the log
            hides them; None keeps the services' own.

    Returns:
        The services and the server.
    """
    s = services or Services(safe_mode=safe_mode, upgrade=upgrade)
    if reports is not None:
        _bind_reports(s, reports)
    with s.timings.measure("server.build"):
        lang = s.settings.get("language")
        if lang and lang != i18n_manager.lang:
            i18n_manager.set_language(lang)  # the language chosen in either GUI
        if upgrade is not None:
            try:
                s.library
            except BaseException:
                s.close()
                raise
        app = WebApp(
            build_api(s),
            s.events,
            assets=s.asset,
            consent=lambda: s.state.consent,
            token=token,
            ui_cached=lambda: False,
            recovery=lambda: recovery_config(s),
            classic=s.classic.wsgi,
            raise_window=s.raise_window,
            on_exception=s.reports.backend_exception,
            upgrade=UpgradeBootstrap(
                upgrade, language=lambda: i18n_manager.lang, ready=lambda: None, quit_app=s.quit, opened=True
            ).dispatch
            if upgrade
            else None,
        )
        s.log_secrets.update((app.token, app.raise_token))
        server = Server(app, port)
        s.origin = f"http://127.0.0.1:{server.port}"  # the classic window's channel
    return s, server


def _bind_reports(s: Services, reports: Reports) -> None:
    reports.services = s
    reports.game = s.reports.game
    reports.secrets.update(s.log_secrets)
    s.log_secrets = reports.secrets
    s.log_path = reports.log_path
    s.reports = reports


def _watch_config(s: Services, stop: threading.Event) -> None:
    # the classic GUI may save config.toml while the web UI runs
    while not stop.wait(CONFIG_POLL_SECONDS):
        try:
            s.settings.reload_if_changed()
        except Exception:
            logger.exception("config reload failed")


def _trace_to_file(path: Path) -> None:
    """Write the front end's input trace (debug.trace) to a file next to the settings too.

    A compiled app can send the file along without a terminal; it starts empty on every --debug start.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError:
        logger.warning("cannot write the input trace to %s", path, exc_info=True)
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    trace = logging.getLogger("bmpt.webui.trace")
    trace.setLevel(logging.INFO)
    trace.addHandler(handler)
    logger.info("input trace in %s", path)


def main(argv: list[str] | None = None) -> None:
    """Run the web UI until its window or browser session ends.

    A second start on the same data directory raises the running instance's window and exits. After a request
    to restart in safe mode or a native upgrade handoff, the next process starts after the leases close.

    Args:
        argv: The command line without the program name; None reads ``sys.argv``.

    Raises:
        SystemExit: Another instance runs on the data directory.
    """
    parser = argparse.ArgumentParser(prog="bamt-web")
    parser.add_argument("--browser", action="store_true", help="open the UI in the system browser instead of a window")
    parser.add_argument("--serve", action="store_true", help="only run the server (for development tools)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", help="a fixed session token instead of a random one (development only)")
    parser.add_argument("--mute", action="store_true", help="never play voices (development and tests)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--safe-mode", action="store_true", help="start without game assets, Spine or restored pages")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    data = Path(EXE_DIR)
    devmode.check_read(data)
    runtime = data / NAMESPACE / "runtime"
    instance = InstanceLock(data, runtime=runtime)
    try:
        acquired = instance.acquire(strict=True)
    except OSError as error:
        logger.error("Cannot lock the data directory: %s", error)
        raise SystemExit(1) from error
    if not acquired:
        raise SystemExit(second_start(data, serve=args.serve))

    upgrade = None
    restart = False
    try:
        upgrade = DataUpgrade(data)
        upgrade.take_instance_lock(instance)
        if not args.serve and not (data / DATA_MANIFEST).exists():
            restart = _serve_bootstrap(args, instance, upgrade, start_upgrade=True)
        else:
            state = upgrade.run()
            if state["status"] == "ready":
                restart = _run(args, instance, upgrade)
            elif args.serve:
                logger.error("Data upgrade %s. Log folder %s", state["status"], state["logFolder"])
                raise SystemExit(1)
            else:
                restart = _serve_bootstrap(args, instance, upgrade)
    finally:
        if upgrade is not None:
            upgrade.close()
        if instance.held:
            instance.release()
    if restart:
        import subprocess
        from .system import restart_command

        command = restart_command(
            safe_mode=restart != "relaunch", browser=args.browser, debug=args.debug, mute=args.mute
        )
        subprocess.Popen(command)


def second_start(data_dir: Path, *, serve: bool) -> int:
    """Handle a start while another instance runs on the data directory.

    Args:
        data_dir: The data directory the running instance holds.
        serve: Started with ``--serve``, which never raises the other window.

    Returns:
        The exit code: 0 when the running instance's window was brought to the front, 1 when this start must
        refuse (with ``--serve``, or the instance gave no answer).
    """
    if serve:
        logger.error("another instance of the web UI runs on the data directory %s", data_dir)
        return 1
    runtime = data_dir / NAMESPACE / "runtime"
    if (read_info(runtime) is not None and raise_running(runtime)) or raise_running(data_dir):
        logger.info("brought the running instance on %s to the front", data_dir)
        return 0
    logger.error("another instance holds %s but did not answer", data_dir)
    return 1


def recovery_config(s: Services) -> dict:
    """Build what the recovery overlay of the served page needs, whether or not the page loads its own script.

    Args:
        s: The services; their safe-mode flag and window decide whether a restart in safe mode is offered.

    Returns:
        ``safeMode``, ``canRestartSafe``, ``language`` and the recovery strings in the UI language.
    """
    from .strings import ui_strings
    from .system import can_restart_safe

    strings = ui_strings(i18n_manager.lang)
    return {
        "safeMode": s.safe_mode,
        "canRestartSafe": can_restart_safe()
        and not getattr(s, "serve_only", False)
        and (getattr(s, "window", None) is not None or getattr(s, "quit_handle", None) is not None),
        "language": i18n_manager.lang,
        "strings": {
            k.removeprefix("webui."): v
            for k, v in strings.items()
            if k.startswith(("webui.recovery.", "webui.upgrade."))
            or k in ("webui.settings.button", "webui.settings.log.open", "webui.picker.ok")
        },
    }


def _run(args: argparse.Namespace, instance: InstanceLock, upgrade: DataUpgrade) -> bool | Literal["relaunch"]:
    safe_mode = getattr(args, "safe_mode", False)
    runtime = upgrade.paths.runtime
    reports = Reports(runtime, safe_mode=safe_mode)
    if args.token:
        reports.secrets.add(args.token)
    handler = None
    restore = install_exception_hooks(reports)
    try:
        handler = open_run_log(runtime / "webui.log", reports.secrets, safe_mode=safe_mode)
        reports.log_path = Path(handler.baseFilename) if hasattr(handler, "baseFilename") else None
        logging.getLogger().addHandler(handler)
        return _serve(args, instance, reports, handler, upgrade)
    except Exception as error:
        if getattr(error, "code", None) in (
            "library_recovery_pending",
            "official_unavailable",
            "folder_changed",
            "library_io_error",
        ):
            upgrade.open_failed()
            if args.serve:
                raise SystemExit(1) from error
            return _serve_bootstrap(args, instance, upgrade, reports=reports if handler is not None else None)
        logger.exception("Startup failed")
        reports.backend_exception(type(error), error, error.__traceback__)
        if not args.serve:
            from .native import startup_failure

            startup_failure(reports, error)
        return False
    finally:
        logger.info("Application lifecycle: run finished")
        restore()
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()


def _serve(args: argparse.Namespace, instance: InstanceLock, reports: Reports, handler, upgrade: DataUpgrade) -> bool:
    s, server = build(args.port, token=args.token, safe_mode=reports.safe_mode, reports=reports, upgrade=upgrade)
    s.serve_only = args.serve
    handler.setFormatter(RedactingFormatter(s.log_secrets))
    for phase, value in s.timings.snapshot().items():
        logger.info("Timing %s: %.3f s (%s calls before log opened)", phase, value["seconds"], value["count"])
    s.mute = args.mute
    s.debug = args.debug
    stop = threading.Event()
    try:
        if args.debug:
            _trace_to_file(s.runtime / "input-trace.log")
        with s.timings.measure("server.start"):
            server.start()
        instance.publish(server.port, server.app.raise_token)
        if args.browser:
            s.quit_handle = stop.set
            s.browser_url = server.url
        threading.Thread(target=_watch_config, args=(s, stop), name="bamt-config", daemon=True).start()
        s.start()
        logger.info("web UI at %s", server.url)
        if args.browser or args.serve:
            if args.browser:
                webbrowser.open(server.url)
            print(server.url, flush=True)
            stop.wait()
        else:
            _run_window(s, server, args.debug)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Application lifecycle: services cleanup begin")
        stop.set()
        server.stop()
        s.close()
        logger.info("Application lifecycle: services cleanup complete")
    return s.restart_requested


def _run_window(s: Services, server: Server, debug: bool) -> None:
    from . import native

    if sys.platform == "win32":
        version = native.webview2_version()
        if not native.webview2_supported(version):
            native.startup_failure(s.reports, RuntimeError(f"WebView2 {version or 'missing'}"), runtime=True)
            return
    with s.timings.measure("native.import"):
        import webview

    from .native import WEBVIEW2_URL, renderer_supported, watch_drops
    from .tray import Tray

    # text_select stays True: with False (pywebview's default) pywebview appends "body { user-select: none; cursor:
    # default }" to the page, which replaces the game's pointer with the system arrow on everything but the buttons.
    # The page turns off text selection itself (app.css).
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    webview.settings["DRAG_REGION_SELECTOR"] = ".topbar:not(.native-toolbar)"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    with s.timings.measure("native.create-window"):
        window = webview.create_window(
            t("ui.app_title"),
            server.url,
            width=1600,
            height=900,
            min_size=(960, 540),
            background_color="#000000",
            text_select=True,
            frameless=sys.platform != "win32",
            easy_drag=False,
        )
    s.window = window
    from .window_chrome import WindowChrome
    from .window_close import CloseCoordinator

    s.chrome = WindowChrome(window, s.events, platform=sys.platform)
    s.close_coordinator = CloseCoordinator(s, s.chrome.close, page_loaded=False)
    commands = None
    lifecycle = {"shown": False, "loaded": False, "closing": False, "closed": False, "drops": "not-started"}
    window_stopped = threading.Event()

    def before_show():
        nonlocal commands
        logger.info("Native lifecycle: before-show begin")
        if sys.platform == "win32":
            s.chrome.install()
        if sys.platform == "darwin":
            native.keep_web_view_black(window)
            commands = native.install_window_commands(s, s.chrome)
        logger.info("Native lifecycle: before-show complete")

    window.events.before_show += before_show

    def on_shown():
        lifecycle["shown"] = True
        logger.info("Native lifecycle: shown")
        if s.chrome.active:
            s.chrome.host.dispatch(s.chrome.refresh)

    window.events.shown += on_shown
    for name, key, value in (
        ("maximized", "maximized", True),
        ("restored", "maximized", False),
        ("minimized", "minimized", True),
    ):
        # Event handlers with default parameters are called with event arguments by pywebview.
        def bind_state(key, value):
            return lambda: s.chrome.fallback_state(key, value)

        event = getattr(window.events, name)
        event += bind_state(key, value)
    # the tray or menu bar icon, shown while the preference "tray" is on; it goes with the window
    tray = Tray(s) if Tray.supported() else None
    s.tray_supported = tray is not None
    if tray is not None:
        tray.attach()

    def on_initialized(renderer: str) -> bool:
        logger.info("Native lifecycle: initialized renderer=%s", renderer)
        s.timings.record("native.initialize", time.perf_counter() - native_started)
        if renderer_supported(renderer):
            return True
        logger.error("unsupported web renderer %s; install WebView2 from %s", renderer, WEBVIEW2_URL)
        native.startup_failure(s.reports, RuntimeError(f"Unsupported renderer: {renderer}"), runtime=True)
        return False

    def install_chrome() -> None:
        if not window_stopped.is_set():
            s.chrome.install()

    def load_document() -> None:
        if window_stopped.is_set():
            logger.info("Native lifecycle: loaded skipped stopped=True")
            return
        if sys.platform == "darwin" and getattr(window, "native", None) is not None:
            # Cocoa replaces its placeholder contentView when navigation finishes.
            from PyObjCTools import AppHelper

            if s.chrome.host is None:
                AppHelper.callAfter(install_chrome)
            if commands is not None:
                AppHelper.callAfter(commands.refresh)
        try:
            lifecycle["drops"] = "running"
            complete = watch_drops(
                window, lambda paths: s.events.publish("drop", {"paths": paths}), stopped=window_stopped.is_set
            )
            lifecycle["drops"] = "complete" if complete else "abandoned"
        except Exception:
            lifecycle["drops"] = "failed"
            logger.exception("drag and drop is unavailable")
        logger.info("Native lifecycle: loaded complete drops=%s", lifecycle["drops"])

    def on_loaded() -> None:
        lifecycle["loaded"] = True
        logger.info("Native lifecycle: loaded begin")
        s.close_coordinator.page_loaded = True
        s.timings.record("native.document", time.perf_counter() - native_started)
        # pywebview's event thread is non-daemon. Cocoa and WebView2 can wait forever
        # for a GUI result after the loop ends; only this UI work may be abandoned.
        threading.Thread(target=load_document, name="bamt-window-loaded", daemon=True).start()

    def on_closing() -> bool:
        lifecycle["closing"] = True
        logger.info("Native lifecycle: closing begin")
        allowed = s.on_closing()
        if allowed:
            window_stopped.set()
        logger.info("Native lifecycle: closing complete allowed=%s", allowed)
        return allowed

    def on_closed() -> None:
        lifecycle["closed"] = True
        window_stopped.set()
        logger.info("Native lifecycle: closed begin")
        s.on_closed()
        logger.info("Native lifecycle: closed complete")

    window.events.initialized += on_initialized
    window.events.loaded += on_loaded
    window.events.closing += on_closing
    window.events.closed += on_closed
    storage = (
        s.committed.runtime if s.committed else s.data_dir
    ) / "webview"  # never next to the program in a compiled build: data_dir follows EXE_DIR rules
    try:
        # the tray starts on a thread of its own once the GUI loop runs
        native_started = time.perf_counter()
        logger.info("Native lifecycle: loop starting")
        webview.start(
            func=tray.start if tray is not None else None,
            args=(window,) if tray is not None else None,
            debug=debug,
            private_mode=True,
            storage_path=str(storage),
            gui="edgechromium" if sys.platform == "win32" else None,
        )
    except BaseException as error:
        logger.info("Native lifecycle: loop raised type=%s", type(error).__name__)
        raise
    else:
        logger.info("Native lifecycle: loop returned")
    finally:
        window_stopped.set()
        logger.info(
            "Native lifecycle: cleanup begin shown=%s loaded=%s closing=%s closed=%s drops=%s",
            lifecycle["shown"],
            lifecycle["loaded"],
            lifecycle["closing"],
            lifecycle["closed"],
            lifecycle["drops"],
        )
        if commands is not None:
            commands()
        s.chrome.dispose()
        if tray is not None:
            tray.close()  # on the main thread once the loop ended: the icon goes with the window
        logger.info("Native lifecycle: cleanup complete")


def _serve_bootstrap(
    args: argparse.Namespace,
    instance: InstanceLock,
    upgrade: DataUpgrade,
    *,
    classic: bool = False,
    start_upgrade: bool = False,
    reports: Reports | None = None,
) -> bool | Literal["relaunch"]:
    from .system import can_restart_safe

    stop = threading.Event()
    window = None
    services = None
    safe_mode = getattr(args, "safe_mode", False)
    relaunch = not (classic or args.browser or safe_mode) and can_restart_safe()
    relaunch_requested = False
    own_log = reports is None
    if reports is None:
        reports = Reports(upgrade.data_dir / NAMESPACE / "runtime", safe_mode=safe_mode)

    def quit_app() -> None:
        stop.set()
        if window is not None:
            window.destroy()

    def pick() -> Path | None:
        if window is None:
            return None
        import webview

        selected = window.create_file_dialog(webview.FileDialog.FOLDER)
        return Path(selected[0]) if selected else None

    def ready() -> None:
        nonlocal relaunch_requested, services
        if stop.is_set():
            return
        if relaunch:
            relaunch_requested = True
            return
        if classic:
            _recover_for_classic(upgrade)
            threading.Timer(0.1, quit_app).start()
            return
        candidate = Services(safe_mode=safe_mode, upgrade=upgrade)
        try:
            _bind_reports(candidate, reports)
            candidate.library
            candidate.mute, candidate.debug = args.mute, args.debug
            candidate.origin = f"http://127.0.0.1:{server.port}"
            candidate.window = window
            candidate.quit_handle = quit_app
            if args.browser:
                candidate.browser_url = server.url
            registry = build_api(candidate)
            candidate.start()
        except BaseException:
            candidate.close()
            raise
        app.rpc = registry
        app.events = candidate.events
        app.assets = candidate.asset
        app.consent = lambda: candidate.state.consent
        app.recovery = lambda: recovery_config(candidate)
        app.classic = candidate.classic.wsgi
        app.raise_window = candidate.raise_window
        app.on_exception = candidate.reports.backend_exception
        services = candidate
        threading.Thread(target=_watch_config, args=(candidate, stop), name="bamt-config", daemon=True).start()
        app.services_ready = True
        instance.publish(server.port, app.raise_token)

    def schedule_quit() -> None:
        threading.Timer(0.1, quit_app).start()

    bootstrap = UpgradeBootstrap(
        upgrade,
        language=lambda: i18n_manager.lang,
        ready=ready,
        quit_app=schedule_quit,
        pick=pick,
        on_opened=schedule_quit if relaunch else None,
    )

    def bootstrap_config() -> dict:
        return {
            "language": i18n_manager.lang,
            "strings": bootstrap.state()["strings"],
            "canRelocateUpgrade": not args.browser,
            "upgradeHandoff": "classic" if classic else "safeMode" if safe_mode else "relaunch" if relaunch else None,
        }

    app = WebApp(
        RpcRegistry(),
        EventBus(),
        token=args.token,
        upgrade=bootstrap.dispatch,
        recovery=bootstrap_config,
        on_exception=reports.backend_exception,
        services_ready=False,
    )
    reports.secrets.update((app.token, app.raise_token))
    handler = None
    server = None
    try:
        if own_log:
            handler = open_run_log(reports.data_dir / "webui.log", reports.secrets, safe_mode=safe_mode)
            reports.log_path = Path(handler.baseFilename) if hasattr(handler, "baseFilename") else None
            logging.getLogger().addHandler(handler)
        server = Server(app, args.port)
        server.start()
        logger.info("Data upgrade bootstrap listening on port %s", server.port)
        if args.browser:
            app.raise_window = lambda: webbrowser.open(server.url)
            instance.publish(server.port, app.raise_token)
            if start_upgrade:
                threading.Thread(target=bootstrap.dispatch, args=("retry",), daemon=True).start()
            webbrowser.open(server.url)
            print(server.url, flush=True)
            stop.wait()
        else:
            import webview

            window = webview.create_window(
                t("ui.app_title"), server.url, width=1200, height=760, background_color="#000000"
            )
            app.raise_window = lambda: (window.show(), window.restore())
            instance.publish(server.port, app.raise_token)
            if start_upgrade:
                window.events.shown += lambda: threading.Thread(
                    target=bootstrap.dispatch, args=("retry",), daemon=True
                ).start()
            with tempfile.TemporaryDirectory(prefix="bmpt-upgrade-webview-") as storage:
                webview.start(
                    debug=args.debug,
                    private_mode=True,
                    storage_path=storage,
                    gui="edgechromium" if sys.platform == "win32" else None,
                )
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            if server is not None:
                server.stop()
            if services is not None:
                services.close()
        finally:
            if handler is not None:
                logging.getLogger().removeHandler(handler)
                handler.close()
    if relaunch_requested:
        return "relaunch"
    return bool(services and services.restart_requested)


def _recover_for_classic(upgrade: DataUpgrade) -> None:
    paths = upgrade.paths
    if paths is None:
        return
    if not any(paths.library_epoch.glob("*/**/journal.json")):
        return
    service = Services(upgrade=upgrade, safe_mode=True)
    try:
        service.library
    finally:
        service.close()


def open_classic_upgrade(upgrade: DataUpgrade) -> bool:
    """Show the same gated notice for standalone Classic until the generation opens."""
    runtime = upgrade.data_dir / NAMESPACE / "runtime"
    instance = InstanceLock(upgrade.data_dir, runtime=runtime)
    try:
        if not instance.acquire(strict=True):
            second_start(upgrade.data_dir, serve=False)
            return False
    except OSError as error:
        logger.error("Cannot lock the data directory: %s", error)
        return False
    upgrade.take_instance_lock(instance)
    if upgrade.run()["status"] == "ready":
        try:
            _recover_for_classic(upgrade)
        except Exception:
            upgrade.open_failed()
        else:
            return True
    options = argparse.Namespace(port=0, token=None, browser=False, debug=False)
    _serve_bootstrap(options, instance, upgrade, classic=True)
    return upgrade.paths is not None


if __name__ == "__main__":
    main()
