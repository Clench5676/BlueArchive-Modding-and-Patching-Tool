"""The RPC methods the front end calls (see the RPC table in docs/webui/api.md)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import sys
import tempfile
import webbrowser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..host import updater
from ..core import devmode
from ..core.errors import ServiceError
from bmpt.engine import get_version_info, i18n_manager, offered_languages, t
from .strings import get_locale_dir
from . import strings
from .api_mods import register as register_mods
from .api_students import register as register_students
from .rpc import RpcRegistry
from .services import Services
from .session_log import tail
from .state import LOBBY_ZOOM_MODES, is_number, lobby_zoom, roster_view
from .system import can_restart_safe, reveal

# a build's own release notes, one Markdown file per version (shipped with the package's assets)
RELEASE_NOTES_DIR = Path(__file__).resolve().parent.parent / "assets" / "release-notes"


def release_notes(version: str, folder: Path = RELEASE_NOTES_DIR) -> dict[str, str]:
    """Read a build's own release notes, for Settings' update preview.

    The notes are ``<X.Y.Z>.md``, the text its GitHub release carries; a pre-release reads the file of its own
    ``X.Y.Z``.

    Args:
        version: The build's version; its ``X.Y.Z`` part names the file.
        folder: The folder of the notes files.

    Returns:
        ``version`` (the ``X.Y.Z`` part, or ``version`` itself when it has none) and ``notes`` (Markdown; empty when
        the build ships none).
    """
    found = re.match(r"\d+\.\d+\.\d+", version)
    path = folder / f"{found.group(0)}.md" if found else None
    notes = path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""
    return {"version": found.group(0) if found else version, "notes": notes}


logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("bmpt.webui.trace")
TRACE_BATCH, TRACE_LINE = 100, 600  # events per debug.trace call, characters per logged event


def ui_strings(lang: str) -> dict[str, str]:
    """The ``webui.*`` strings for a language, falling back key by key like the Python side does."""
    return strings.ui_strings(lang, get_locale_dir())


# settings.set keys of the boolean preferences (webui/state.py FLAGS)
FLAG_KEYS = {"tray": "tray", "classicPrompt": "classic_prompt", "startupUpdate": "startup_update"}


def build_api(s: Services) -> RpcRegistry:
    """Build the registry of the RPC methods the front end calls through ``/api/rpc``.

    The methods are grouped by namespace (``app``, ``settings``, ``mods``, ``actions``, ``import``, ...) and each
    is a thin call into the services; the photography, tours, export and Student Hall methods come from their own
    modules. The RPC table in docs/webui/api.md lists them.

    Args:
        s: The Services every method works on.

    Returns:
        The registry.
    """
    rpc = RpcRegistry(
        on_exception=lambda *args: s.reports.backend_exception(*args) if hasattr(s, "reports") else None,
        translate=getattr(s, "translate_error", None),
    )
    from .photography import register as register_photography

    register_photography(rpc, s)
    from .tours import register as register_tours

    register_tours(rpc, s)

    # --- app ---

    @rpc.method("app.info")
    def app_info() -> dict[str, Any]:
        found = s.game_found()
        if hasattr(s, "reports") and not s.safe_mode:
            s.reports.game["found"] = found
        version = resource_version() if found else None
        if hasattr(s, "reports") and not s.safe_mode:
            s.reports.game = {
                "found": found,
                "resourceVersion": version,
                "server": s.settings.server_region(),
                "platform": "Android" if s.settings.is_adb_mode() else "Windows",
            }
        return {
            "safeMode": getattr(s, "safe_mode", False),
            "version": get_version_info().get("version"),
            "language": i18n_manager.lang,
            "platform": sys.platform,
            "gameFound": found,
            "resourceVersion": version,
            "resourceContext": getattr(s.game, "cache_version", version) if found else None,
            "consent": s.state.consent,
            "spineConsent": s.state.spine_consent,
            "previewCache": s.state.preview_cache,
            "previewCacheGeneration": s.preview_cache.generation,
            "uiCached": found and s.ui_cached(version),
            "cacheDir": str(s.cache_dir()),
            "dataDir": str(s.data_dir),
            "dataDirWritable": getattr(s, "data_dir_writable", True),
            "native": s.window is not None,
            "compiled": updater.is_compiled(),
            "mute": s.mute,
            "debug": s.debug,
            "startupUpdate": s.state.flag("startup_update"),
        }

    def resource_version() -> str:
        with s.timings.measure("game.resource-version"):
            return s.game.resource_version

    @rpc.method("app.ready")
    def app_ready(timings: dict[str, Any] | None = None) -> None:
        phases = {
            "boot",
            "info",
            "settings",
            "strings",
            "fonts",
            "ui-indexes",
            "mod-state",
            "first-screen",
            "fallback-loading",
            "sprite-loading",
        }
        if timings is not None:
            if not isinstance(timings, dict):
                raise ServiceError("bad_params", key="timings")
            for phase in phases:
                value = timings.get(phase)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and 0 <= value <= 86400000
                ):
                    s.timings.record("page." + phase, value / 1000)
        s.ready()
        s.tours_ready = not getattr(s, "safe_mode", False)

    @rpc.method("app.timings")
    def app_timings() -> dict[str, Any]:
        return s.timings.snapshot()

    @rpc.method("app.log")
    def app_log() -> dict[str, str]:
        return {
            "path": str(s.log_path) if s.log_path else "",
            "text": tail(s.log_path, s.log_secrets) if s.log_path else "",
        }

    @rpc.method("app.report")
    def app_report(context: dict | None = None, event: dict | None = None) -> dict:
        s.reports.frontend_event(event=event, context=context)
        return s.reports.generate(trigger=event)

    @rpc.method("app.frontendError")
    def app_frontend_error(event: dict | None = None, context: dict | None = None) -> bool:
        return s.reports.frontend_event(event, context)

    @rpc.method("app.logFolder")
    def app_log_folder() -> None:
        reveal(s.log_path.parent if s.log_path else s.data_dir)

    @rpc.method("app.restartSafe")
    def app_restart_safe() -> None:
        if s.jobs.busy():
            raise ServiceError("busy")
        if not can_restart_safe() or getattr(s, "serve_only", False) or (s.window is None and s.quit_handle is None):
            raise ServiceError("safe_restart_unavailable")
        if s.restart_requested:
            return
        s.quit(restart=True)

    @rpc.method("window.chrome")
    def window_chrome() -> dict:
        return s.chrome.snapshot() if s.chrome is not None else {"active": False, "platform": "browser", "sequence": 0}

    @rpc.method("window.layout")
    def window_layout(centre: float, height: float, drag: list, state: str = "") -> None:
        if s.chrome is not None:
            try:
                s.chrome.update(centre, height, drag, state)
            except (ValueError, TypeError, KeyError):
                raise ServiceError("bad_params", key="geometry") from None

    @rpc.method("window.motion")
    def window_motion(frames: list, period: float = 1 / 60) -> None:
        if s.chrome is not None:
            try:
                s.chrome.motion(frames, period)
            except (ValueError, TypeError):
                raise ServiceError("bad_params", key="motion") from None

    @rpc.method("window.control")
    def window_control(action: str) -> None:
        if action not in ("minimize", "maximize", "close", "fullscreen"):
            raise ServiceError("bad_params", key="action")
        if s.window is None:
            raise ServiceError("native_only")
        if action == "close":
            s.quit()
        elif s.chrome is not None:
            s.chrome.command(action)
        elif action == "minimize":
            s.window.minimize()
        elif action == "fullscreen":
            s.window.toggle_fullscreen()
        else:
            s.window.maximize()

    @rpc.method("app.consent")
    def app_consent(granted: bool) -> None:
        s.set_consent(bool(granted))
        if not granted and s.window is not None and not s.jobs.busy():
            s.quit()

    @rpc.method("app.spineConsent")
    def app_spine_consent(granted: Any = ...) -> bool | None:
        if granted is not ...:
            if not isinstance(granted, bool):
                raise ServiceError("bad_params", key="granted")
            s.set_spine_consent(granted)
        return s.state.spine_consent

    @rpc.method("app.quit")
    def app_quit() -> None:
        s.quit()

    # --- the classic window (the bottom bar's entries without a web screen, Settings' More Settings) ---

    @rpc.method("app.openClassic")
    def app_open_classic(tab: str | None = None) -> dict[str, bool]:
        # a game writer job stops at its next write once a classic window is open (a toggle half done): no new window
        # until it ended; an open one is only raised
        def may_start() -> None:
            if s.update_installing or s.jobs.busy("game"):
                raise ServiceError("busy")

        return s.classic.open(tab, may_start=may_start)

    @rpc.method("app.classic")
    def app_classic() -> dict[str, Any]:
        return {**s.classic.status(), "queued": len(s.queue)}

    @rpc.method("classic.front")
    def classic_front() -> dict[str, bool]:
        """The classic modal's Bring to Front: raise the open classic window, never start one."""
        return {"raised": s.classic.bring_to_front()}

    @rpc.method("classic.queue")
    def classic_queue(method: str, params: dict[str, Any] | None = None) -> dict[str, int]:
        return {"queued": s.queue_call(method, params or {})}

    @rpc.method("classic.clearQueue")
    def classic_clear_queue() -> dict[str, Any]:
        s.clear_queue()
        return {}

    # --- updates ---

    def downloaded_update(info: Any) -> Any:
        downloaded = getattr(s, "update_file", None)
        return (
            downloaded
            if (
                info is not None
                and downloaded is not None
                and downloaded[2] == info.version
                and downloaded[0].is_file()
            )
            else None
        )

    @rpc.method("app.checkUpdate")
    def app_check_update() -> Any:
        info = updater.check()
        s.update_info = info
        if info is None:
            return None
        downloaded = downloaded_update(info)
        return {
            **info.to_json(),
            "downloaded": downloaded is not None,
            "file": str(downloaded[0]) if downloaded else None,
        }

    @rpc.method("app.update")
    def app_update() -> str:
        info = getattr(s, "update_info", None)
        if info is None or not info.installable:
            raise ServiceError("update_unsupported")

        def download(log: Any, progress: Any) -> dict[str, str]:
            downloaded = downloaded_update(info)
            if downloaded is not None:
                return {"file": str(downloaded[0])}
            dest = Path(tempfile.mkdtemp(prefix="bamt-update-"))
            path, md5 = updater.download(
                info,
                dest,
                lambda current, total, name: progress(current, total, t("webui.progress.download", name=name)),
            )
            s.update_file = (path, md5, info.version)  # what installUpdate may swap in, for this release only
            return {"file": str(path)}

        return s.jobs.submit("update.download", download)

    @rpc.method("app.releaseNotes")
    def app_release_notes() -> dict[str, str]:
        return release_notes(str(get_version_info().get("version") or ""))

    @rpc.method("app.openReleasePage")
    def app_open_release_page(page: str | None = None) -> None:
        info = getattr(s, "update_info", None)
        target = page if page is not None else info.page if info else ""
        prefix = f"https://github.com/{updater.REPO}/releases/tag/"
        if (
            not isinstance(target, str)
            or not target.startswith(prefix)
            or updater.parse_version(target[len(prefix) :]) is None
        ):
            raise ServiceError("update_unavailable")
        webbrowser.open(target)

    @rpc.method("app.installUpdate")
    def app_install_update(file: str) -> None:
        # only the build this session downloaded and verified, only in the app window (it has to quit to be
        # replaced), and never while a job writes into the game folder
        downloaded = downloaded_update(s.update_info)
        if s.window is None:
            raise ServiceError("update_unsupported")
        if downloaded is None or Path(file) != downloaded[0] or not downloaded[0].is_file():
            raise ServiceError("update_unavailable")
        return s.install_update(downloaded[0], downloaded[1])

    # --- settings (the popup behind the top bar's settings button) ---

    def language_name(code: str) -> str:
        # each language is named in itself (webui.languageName of its own locale file)
        return ui_strings(code).get("webui.languageName", code)

    @rpc.method("settings.get")
    def settings_get() -> dict[str, Any]:
        st = s.settings
        version = get_version_info().get("version") or ""
        return {
            "language": i18n_manager.lang,
            "languages": [{"code": c, "name": language_name(c)} for c in offered_languages()],
            "fileSource": st.get("file_source"),
            "gameDirs": {"global": st.get("game_resource_dir"), "japan": st.get("game_resource_dir_japan")},
            "gameFound": s.game_found(),
            "libraryDir": str(s.library_dir()),
            "outputDir": st.get("output_dir"),
            "voiceVolume": int(s.state.get("voice_volume", 80)),
            "voiceMuted": bool(s.state.get("voice_muted", False)),
            # {mode, custom}; before the modes it was a boolean, lobby_zoom_out
            "lobbyZoom": lobby_zoom(
                s.state.get("lobby_zoom"), s.state.get("lobby_zoom_custom"), s.state.get("lobby_zoom_out")
            ),
            "lobbyPost": bool(s.state.get("lobby_post", True)),
            "lobbyIntro": bool(s.state.get("lobby_intro", True)),
            "roster": roster_view(s.state.get("roster")),
            "consent": s.state.consent,
            "cacheDir": str(s.cache_dir()),
            "spineConsent": s.state.spine_consent,
            "previewCache": s.state.preview_cache,
            "previewCacheGeneration": s.preview_cache.generation,
            "version": version,
            "native": s.window is not None,
            "prereleasePreviews": updater.prerelease_previews(version),
            "buttonSizeTags": updater.button_size_tags(version),
            "logPath": str(s.log_path),
            # the tray or menu bar icon, the hand-off prompt
            "tray": s.state.flag("tray"),
            "classicPrompt": s.state.flag("classic_prompt"),
            "traySupported": s.tray_supported,
            "startupUpdate": s.state.flag("startup_update"),
        }

    @rpc.method("settings.set")
    def settings_set(key: str, value: Any) -> dict[str, Any]:
        st = s.settings
        if key == "language":
            if value not in offered_languages():
                raise ServiceError("bad_params", key=key)
            st.set("language", value)
            st.save()
            i18n_manager.set_language(value)
        elif key == "fileSource":
            if value not in ("windows_global", "windows_japan"):
                raise ServiceError("bad_params", key=key)
            from bmpt.engine import is_game_install

            # Validate before publishing the preference or invalidating a usable Hall.
            with s._lock:
                if s.jobs.busy():
                    raise ServiceError("busy")
                region = "japan" if value == "windows_japan" else "global"
                path = st.game_dir(region)
                if path is None or not devmode.allows_read(path) or not path.is_dir() or not is_game_install(path):
                    raise ServiceError("game_directory_missing", region=region)
                st.set("file_source", value)
                st.save()
                s.reload_game()
        elif key in ("gameDir.global", "gameDir.japan"):
            path = Path(str(value)).expanduser()
            devmode.check_read(path)
            from bmpt.engine import is_game_install

            if not path.is_dir() or not is_game_install(path):
                raise ServiceError("game_not_found")
            with s._lock:
                if s.jobs.busy():
                    raise ServiceError("busy")
                st.set("game_resource_dir" if key.endswith("global") else "game_resource_dir_japan", str(path))
                st.save()
                s.reload_game()
        elif key == "outputDir":
            path = Path(str(value)).expanduser()
            devmode.check_read(path)
            path.mkdir(parents=True, exist_ok=True)
            st.set("output_dir", str(path))
            st.save()
        elif key == "libraryDir":
            s.move_library(Path(str(value)))
        elif key == "cacheDir":
            s.set_cache_parent(Path(str(value)))
        elif key == "previewCache":
            if not isinstance(value, bool):
                raise ServiceError("bad_params", key=key)
            s.set_preview_cache(value)
        elif key == "voiceVolume":
            s.state.set("voice_volume", max(0, min(100, int(value))))
        elif key == "voiceMuted":
            s.state.set("voice_muted", bool(value))
        elif key == "lobbyZoom":
            if not isinstance(value, str) or value not in LOBBY_ZOOM_MODES:
                raise ServiceError("bad_params", key=key)
            s.state.set("lobby_zoom", value)
        elif key == "lobbyZoomCustom":
            if not is_number(value):
                raise ServiceError("bad_params", key=key)
            s.state.set("lobby_zoom_custom", lobby_zoom(None, value)["custom"])
        elif key == "lobbyPost":
            s.state.set("lobby_post", bool(value))
        elif key == "lobbyIntro":
            s.state.set("lobby_intro", bool(value))
        elif key == "roster":
            s.state.set("roster", roster_view(value))
        elif key in FLAG_KEYS:
            if not isinstance(value, bool):
                raise ServiceError("bad_params", key=key)
            s.state.set(FLAG_KEYS[key], value)
            s.publish_state()
        else:
            raise ServiceError("bad_params", key=key)
        return settings_get()

    @rpc.method("settings.detectGame")
    def settings_detect_game(region: str) -> list[str]:
        if region not in ("global", "japan"):
            raise ServiceError("bad_params", region=region)
        if not s.scan_allowed():
            raise ServiceError("scan_off")  # finding the game reads Steam's records: the Check Game Files consent
        from ..host.game_paths import find_game_dirs

        return [str(p) for p in find_game_dirs(region)]

    @rpc.method("settings.pickFolder")
    def settings_pick_folder(start: str | None = None) -> str | None:
        if s.window is None:
            raise ServiceError("native_unavailable")
        import webview

        result = s.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
        return str(result[0]) if result else None

    @rpc.method("settings.clearCache")
    def settings_clear_cache() -> dict[str, Any]:
        s.clear_cache()
        return settings_get()

    @rpc.method("preview.cacheGet")
    def preview_cache_get(key: str, renderer: str) -> dict[str, Any]:
        cache = s.preview_cache
        generation = cache.generation
        allowed = cache.allowed and not s.safe_mode
        data = s.preview_thumbnail(key, renderer) if allowed else None
        # A concurrent clear/toggle must not return a stale generation as usable data.
        if cache.generation != generation:
            data = None
        return {
            "allowed": allowed and cache.allowed,
            "generation": cache.generation,
            "data": base64.b64encode(data).decode("ascii") if data is not None else None,
        }

    @rpc.method("preview.modelRevision")
    def preview_model_revision(variant: str, source: str = "live") -> dict[str, str]:
        if (
            not isinstance(variant, str)
            or not variant
            or len(variant) > 256
            or "/" in variant
            or not isinstance(source, str)
            or len(source) > 512
        ):
            raise ServiceError("bad_params", key="variant")
        generation = s.preview_cache.generation
        try:
            exported = s.asset("model/" + variant + ".glb", {"source": source})
        except KeyError as error:
            raise ServiceError("bad_params", key="variant") from error
        return {"revision": hashlib.sha256(exported.body).hexdigest(), "generation": generation}

    @rpc.method("preview.assetRevision")
    def preview_asset_revision(paths: list[str]) -> dict[str, str]:
        """Identify the exact background and framing bytes used by a persisted still."""
        if not s.preview_cache.allowed or s.safe_mode:
            raise ServiceError("bad_params", key="previewCache")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 2:
            raise ServiceError("bad_params", key="paths")
        checked = []
        for path in paths:
            if not isinstance(path, str) or len(path) > 2048:
                raise ServiceError("bad_params", key="paths")
            try:
                path = unquote(path, errors="strict")
            except UnicodeError as error:
                raise ServiceError("bad_params", key="paths") from error
            background = re.fullmatch(r"/asset/art/bg/BG_[a-zA-Z0-9_-]+\.png", path)
            framing = re.fullmatch(
                r"/asset/spine/(?:spr|lobby|official-spr|official-lobby|mod|import)/"
                r"[a-zA-Z0-9_][a-zA-Z0-9_@.:-]{0,511}/[a-f0-9]{64}/framing\.json",
                path,
            )
            if not (background or framing):
                raise ServiceError("bad_params", key="paths")
            checked.append(path)
        generation = s.preview_cache.generation
        identities = []
        try:
            for path in sorted(set(checked)):
                exported = s.asset(path[len("/asset/") :], {})
                identities.append([path, hashlib.sha256(exported.body).hexdigest()])
        except KeyError as error:
            raise ServiceError("bad_params", key="paths") from error
        digest = hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()
        return {"revision": digest, "generation": generation}

    @rpc.method("preview.cachePut")
    def preview_cache_put(key: str, data: str, generation: str, renderer: str) -> bool:
        cache = s.preview_cache
        if s.safe_mode or not cache.allowed or generation != cache.generation:
            return False
        # Base64 leaves sufficient room under the authenticated RPC's 4 MiB body cap.
        limit = 2 * 1024 * 1024
        if not isinstance(data, str) or len(data) > 4 * ((limit + 2) // 3):
            raise ServiceError("bad_params", key="data")
        from PIL import Image

        try:
            raw = base64.b64decode(data, validate=True)
            if len(raw) > limit or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid thumbnail PNG")
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.width > 4096 or image.height > 4096:
                    raise ValueError("Invalid thumbnail dimensions")
                image.verify()
        except (ValueError, binascii.Error, OSError, SyntaxError, Image.DecompressionBombError) as error:
            raise ServiceError("bad_params", key="data") from error
        return cache.put(s.preview_thumbnail_key(key, renderer), raw, generation=generation)

    @rpc.method("debug.trace")
    def debug_trace(events: list[Any]) -> None:
        """Log the fullscreen lobby's input events, when the App was started with ``--debug``.

        A user's trackpad behaviour reproduces once, and the log tells what the page received.
        """
        if not s.debug or not isinstance(events, list):
            return
        for event in events[:TRACE_BATCH]:
            line = json.dumps(event, ensure_ascii=True, separators=(",", ":"), default=str)
            trace_logger.info("%s", line[:TRACE_LINE])

    @rpc.method("preview.failed")
    def preview_failed(url: str = "", step: str = "", error: str = "") -> None:
        """Record bounded browser preview diagnostics in release sessions too."""
        detail = {key: str(value)[:4000] for key, value in {"url": url, "step": step, "error": error}.items()}
        logger.warning("Preview failed: %s", json.dumps(detail, ensure_ascii=True))

    @rpc.method("i18n.strings")
    def i18n_strings(lang: str | None = None) -> dict[str, str]:
        return ui_strings(lang or i18n_manager.lang)

    register_students(rpc, s)
    register_mods(rpc, s)

    # --- system ---

    @rpc.method("os.reveal")
    def os_reveal(path: str) -> None:
        reveal(path)

    from .export_service import register_api as register_export_api
    from .student_hall import register_api as register_hall_api

    register_export_api(rpc, s)
    register_hall_api(rpc, s)
    return rpc
