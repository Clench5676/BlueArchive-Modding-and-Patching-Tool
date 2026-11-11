"""The RPC methods the front end calls (see the RPC table in docs/web-ui.md)."""

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
import threading
import time
import weakref
import webbrowser
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .. import devmode, globallines, updater
from ..catalog.special import SPECIAL_PREFIX
from ..errors import ServiceError
from bmpt.engine import get_version_info, i18n_manager, offered_languages, parse_filename, t
from .strings import get_locale_dir
from ..kivo import clip_id
from ..library.storage import trailing_bytes
from . import strings
from .rpc import RpcRegistry, to_json
from .services import Services
from .session_log import tail
from .state import ALIAS_LENGTH, ALIAS_MAX, LOBBY_ZOOM_MODES, alias_key, alias_text, is_number, lobby_zoom, roster_view
from .system import can_restart_safe, reveal

# a build's own release notes, one Markdown file per version (shipped with the package's assets)
RELEASE_NOTES_DIR = Path(__file__).resolve().parent.parent / "assets" / "release-notes"


def release_notes(version: str, folder: Path = RELEASE_NOTES_DIR) -> dict[str, str]:
    """A build's own release notes (<X.Y.Z>.md, the text its GitHub release carries; a pre-release reads its version's),
    for Settings' update preview. Empty notes when the build ships none."""
    found = re.match(r"\d+\.\d+\.\d+", version)
    path = folder / f"{found.group(0)}.md" if found else None
    notes = path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""
    return {"version": found.group(0) if found else version, "notes": notes}

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("bmpt.webui.trace")
TRACE_BATCH, TRACE_LINE = 100, 600  # events per debug.trace call, characters per logged event
# UI language -> lobby lines key (Global en, zh-TW, ko, th; ja from Kivo, else Global)
LINE_LANGS = {"zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th"}
# seconds lobby.lines waits for the Global table (a download the first time); it answers with Kivo's lines after that,
# so a slow download never holds the page's connections (the table keeps coming in for the next lobby)
LINES_WAIT = 3.0


def ui_strings(lang: str) -> dict[str, str]:
    """The ``webui.*`` strings for a language, falling back key by key like the Python side does."""
    return strings.ui_strings(lang, get_locale_dir())


def clip_talks(talks: Any) -> dict[str, list[str]]:
    """The page's {talk animation: [clip ids]}, checked and normalised; {} when absent or malformed."""
    if not isinstance(talks, dict):
        return {}
    return {str(anim): [clip_id(str(c)) for c in ids[:32]] for anim, ids in list(talks.items())[:64]
            if isinstance(ids, list)}


def _background(fn: Any, *args: Any) -> Future:
    """Run fn on a daemon thread (a slow download never holds up quitting); its result as a future."""
    future: Future = Future()

    def run() -> None:
        try:
            future.set_result(fn(*args))
        except BaseException as e:  # handed to the caller
            future.set_exception(e)

    threading.Thread(target=run, name="lobby-lines", daemon=True).start()
    return future


def _url_bg(name: str | None) -> str | None:
    return f"/asset/art/bg/{name}" if name else None


def _url_portrait(name: str | None) -> str | None:
    return f"/asset/art/portrait/{name}.png" if name else None


# settings.set keys of the boolean preferences (webui/state.py FLAGS)
FLAG_KEYS = {"tray": "tray", "classicPrompt": "classic_prompt",
             "startupUpdate": "startup_update"}


def build_api(s: Services) -> RpcRegistry:
    rpc = RpcRegistry(on_exception=lambda *args: s.reports.backend_exception(*args) if hasattr(s, "reports") else None,
                      translate=getattr(s, "translate_error", None))
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
            s.reports.game = {"found": found, "resourceVersion": version, "server": s.settings.server_region(),
                              "platform": "Android" if s.settings.is_adb_mode() else "Windows"}
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
        phases = {"boot", "info", "settings", "strings", "fonts", "ui-indexes", "mod-state", "first-screen",
                  "fallback-loading", "sprite-loading"}
        if timings is not None:
            if not isinstance(timings, dict):
                raise ServiceError("bad_params", key="timings")
            for phase in phases:
                value = timings.get(phase)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 86400000:
                    s.timings.record("page." + phase, value / 1000)
        s.ready()
        s.tours_ready = not getattr(s, "safe_mode", False)

    @rpc.method("app.timings")
    def app_timings() -> dict[str, Any]:
        return s.timings.snapshot()

    @rpc.method("app.log")
    def app_log() -> dict[str, str]:
        return {"path": str(s.log_path) if s.log_path else "", "text": tail(s.log_path, s.log_secrets) if s.log_path else ""}

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
        return downloaded if (info is not None and downloaded is not None
                              and downloaded[2] == info.version and downloaded[0].is_file()) else None

    @rpc.method("app.checkUpdate")
    def app_check_update() -> Any:
        info = updater.check()
        s.update_info = info
        if info is None:
            return None
        downloaded = downloaded_update(info)
        return {**info.to_json(), "downloaded": downloaded is not None,
                "file": str(downloaded[0]) if downloaded else None}

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
            path, md5 = updater.download(info, dest, lambda current, total, name: progress(
                current, total, t("webui.progress.download", name=name)))
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
        if not isinstance(target, str) or not target.startswith(prefix) or updater.parse_version(target[len(prefix):]) is None:
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
            "gameFound": s.game_found(), "libraryDir": str(s.library_dir()), "outputDir": st.get("output_dir"),
            "voiceVolume": int(s.state.get("voice_volume", 80)), "voiceMuted": bool(s.state.get("voice_muted", False)),
            # {mode, custom}; before the modes it was a boolean, lobby_zoom_out
            "lobbyZoom": lobby_zoom(s.state.get("lobby_zoom"), s.state.get("lobby_zoom_custom"),
                                    s.state.get("lobby_zoom_out")),
            "lobbyPost": bool(s.state.get("lobby_post", True)),
            "lobbyIntro": bool(s.state.get("lobby_intro", True)),
            "roster": roster_view(s.state.get("roster")),
            "consent": s.state.consent, "cacheDir": str(s.cache_dir()),
            "spineConsent": s.state.spine_consent,
            "previewCache": s.state.preview_cache,
            "previewCacheGeneration": s.preview_cache.generation,
            "version": version, "native": s.window is not None,
            "prereleasePreviews": updater.prerelease_previews(version),
            "logPath": str(s.log_path),
            # the tray or menu bar icon, the hand-off prompt
            "tray": s.state.flag("tray"),
            "classicPrompt": s.state.flag("classic_prompt"), "traySupported": s.tray_supported,
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
                    raise ServiceError('busy')
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
        from ..game_paths import find_game_dirs

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
        return {"allowed": allowed and cache.allowed, "generation": cache.generation,
                "data": base64.b64encode(data).decode("ascii") if data is not None else None}

    @rpc.method("preview.modelRevision")
    def preview_model_revision(variant: str, source: str = "live") -> dict[str, str]:
        if (not isinstance(variant, str) or not variant or len(variant) > 256 or "/" in variant
                or not isinstance(source, str) or len(source) > 512):
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
            framing = re.fullmatch(r"/asset/spine/(?:spr|lobby|official-spr|official-lobby|mod|import)/"
                                   r"[a-zA-Z0-9_][a-zA-Z0-9_@.:-]{0,511}/[a-f0-9]{64}/framing\.json", path)
            if not (background or framing):
                raise ServiceError("bad_params", key="paths")
            checked.append(path)
        generation = s.preview_cache.generation
        identities = []
        try:
            for path in sorted(set(checked)):
                exported = s.asset(path[len("/asset/"):], {})
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
        """Input events of the fullscreen lobby, logged when the app was started with --debug (a user's trackpad
        reproduces once and the log tells what the page received)."""
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

    # --- students ---

    from .assigned_catalog import families_with_assignments, family_with_assignments, stored_asset_fields

    def visible_families() -> list[Any]:
        return families_with_assignments(s.catalog, s.library.assigned_targets())

    def visible_variant(key: str) -> Any:
        try:
            return s.catalog.variant(key)
        except (ServiceError, KeyError):
            if key in s.library.assigned_targets():
                return s.catalog.assignment_variant(key)
            raise

    def alias_target(family: Any) -> str:
        # A missing normal base never gives its nickname to a skin. A special-only family has no such base:
        # attach its old name to one stable, catalog-ordered scene and persist that choice.
        if family.variants and all(v.key.startswith(SPECIAL_PREFIX) for v in family.variants):
            return min(family.variants, key=lambda v: (v.order, v.key)).key
        return family.key

    def variant_aliases(families: list[Any] | None = None) -> dict[str, list[str]]:
        targets = {f.key: target for f in (visible_families() if families is None else families)
                   if (target := alias_target(f)) != f.key}
        return s.state.aliases(targets)

    photo_failure: dict[str, str] = {}  # the error code of the last failed photograph lookup, logged once

    def variant_json(v: Any, mods: dict[str, list], favorites: set[str], recent: list[str], seen: set[str] | None,
                     modified: dict[str, str] | None = None, custom: dict[str, list[str]] | None = None,
                     roster_state: dict[str, Any] | None = None) -> dict[str, Any]:
        own = mods.get(v.key, [])
        photograph = None
        try:
            photographs = getattr(s, "photographs", None)
            if photographs is not None:
                photograph = photographs.app_url(v.key)
                if photograph is None and photographs.selection(v.key) == "official" and v.portrait:
                    pin = s.library.current_pin()
                    if pin is not None:
                        photograph = s.photography.default_url(v.key, pin)
            photo_failure.clear()
        except ServiceError as error:
            # the thumbnail choice is optional: the student keeps its default one, and the log says so once
            photograph = None
            if photo_failure.get("code") != error.code:
                photo_failure["code"] = error.code
                logger.warning("Photograph lookup failed (%s), students use their default thumbnails", error.code)
        return {
            **({"thumbnail": photograph} if photograph else {}),
            **stored_asset_fields(v, own),
            "custom": (custom or {}).get(v.key, []),
            "key": v.key, "fileId": v.file_id, "family": v.family, "names": v.names, "playable": v.playable,
            "aliases": v.aliases,  # the search matches them, the page never shows them (the CN server's names)
            "recruitable": v.recruitable,  # on the selected server; playable is on some server
            "order": v.order, "spr": v.spr, "lobby": v.lobby, "date": v.date, "model": v.model,
            # a model with no slot (an event or story variant with no battle model: a field player or NPC, or a story
            # cutscene's or a UI scene's model): previewed and offered as a costume
            "fieldModel": getattr(v, "field_model", None),
            "spineCategory": getattr(v, "spine_category", None),
            "bg": _url_bg(v.bg), "portrait": _url_portrait(v.portrait),
            "favorite": v.key in favorites, "mods": len({getattr(m, "repaired_from", "") or m.id for m in own}),
            "modNames": list(dict.fromkeys(name for m in own for name in (
                getattr(m, "alias", ""), getattr(m, "label", ""), getattr(m, "core", ""),
                Path(getattr(m, "file", "")).name) if name)),
            "fresh": seen is not None and any(m.user_added and m.id not in seen for m in own),
            "modCounts": {kind: len({getattr(m, "repaired_from", "") or m.id for m in own if m.kind == kind})
                          for kind in ("spr", "lobby")},
            **({"configured": roster_state["configured"][v.key],
                "previewSources": roster_state["previews"].get(v.key, {})} if roster_state is not None else {
                    "configured": configured_of(v.key), "previewSources": preview_sources_of(v.key),
                    "selection": selection_of(v.key)}),
            # when the newest of its own mods was added (ISO), for the roster's "recently added" sort
            "added": max((m.added for m in own if getattr(m, "added", None)), default=None),
            "recent": recent.index(v.key) if v.key in recent else None,
            # the portrait game file carries something other than the official portrait (a mod or another variant)
            "replaced": (modified or {}).get(v.key),
        }

    def mods_by_target(entries: list | None = None) -> dict[str, list]:
        out: dict[str, list] = {}
        for m in s.library.mods() if entries is None else entries:
            out.setdefault(m.target, []).append(m)
        return out

    @rpc.method("roster.list")
    @s.timings.measure("roster.data")
    def roster_list() -> dict[str, Any]:
        if not s.game_found():
            raise ServiceError("game_not_found")
        catalog_families = s.catalog.families()
        snapshot = s.library.roster_snapshot([v.key for f in catalog_families for v in f.variants])
        catalog_families = families_with_assignments(s.catalog, snapshot.get("assigned", []))
        mods, favorites, recent = mods_by_target(snapshot["mods"]), snapshot["favorites"], s.state.recent()
        modified, custom = snapshot["modified"], variant_aliases(catalog_families)
        previews = s.state.preview_snapshot(snapshot["install"]) if snapshot["install"] is not None else {}
        by_id = {mod.id: mod for mod in snapshot["mods"]}
        variants = {v.key: v for family in catalog_families for v in family.variants}
        snapshot["previews"] = {key: valid_preview_sources(key, sources, by_id, variants) for key, sources in previews.items()}
        seen = s.state.seen_mods()
        families = []
        for f in catalog_families:
            families.append({"key": f.key, "base": f.names, "aliases": f.aliases,
                             "school": f.school,
                             "variants": [variant_json(v, mods, favorites, recent, seen, modified, custom, snapshot)
                                          for v in f.variants]})
        return {"families": families}

    def model_files(core: str | None, state: dict) -> list[dict[str, Any]]:
        """All model slot parts, with the same Needs Fix and restore judgements as the file table."""
        parts = [part for part in state.get("files", []) if part["kind"] == "model"]
        if parts:
            return sorted(parts, key=lambda part: (part["date"] or "", part["file"]), reverse=True)
        try:
            index = getattr(s.game, "_model_index", None) if core else None
            names = index().get(core.lower(), []) if index else []
        except (ServiceError, OSError):
            return []
        out = []
        for name in names:
            path = Path(name)
            parsed = parse_filename(path.name)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            trailing = trailing_bytes(path)
            if trailing is None:
                continue
            out.append({"file": str(path), "kind": "model", "date": parsed.date, "size": size,
                        "crc": "fixed" if trailing else "ok"})
        return sorted(out, key=lambda part: (part["date"] or "", part["file"]), reverse=True)

    def configured_of(key: str) -> dict[str, str | None]:
        try:
            return s.library.configured(key)
        except ServiceError:
            return {"spr": None, "lobby": None}

    def selection_of(key: str) -> dict:
        try:
            return s.library.selection(key)
        except ServiceError:
            return {}

    def preview_source_valid(variant: str, kind: str, source: str, mods: dict | None = None,
                             variants: dict | None = None) -> bool:
        v = variants.get(variant) if variants is not None else visible_variant(variant)
        if v is None:
            return False
        core = getattr(v, kind, None)
        if source == "official":
            return bool(core)
        if source.startswith("mod:"):
            mod = mods.get(source[4:]) if mods is not None else next((m for m in s.library.mods() if m.id == source[4:]), None)
            if mod is None or mod.kind != kind:
                return False
            if mod.target == variant:
                return True
            if not core:
                return False
            other = variants.get(mod.target) if variants is not None else visible_variant(mod.target)
        elif source.startswith("variant:") and kind == "spr":
            if not core:
                return False
            other = variants.get(source[8:]) if variants is not None else s.catalog.variant(source[8:])
        elif source.startswith("variant:") and kind == "model":
            # another variant's costume on this variant's model slot: any other model, the student's own or, chosen
            # through the switch's search, another student's (BL-131), whose check did not refuse it (costume_check)
            other = variants.get(source[8:]) if variants is not None else s.catalog.variant(source[8:])
            if not (bool(core) and other is not None and other.key != v.key and bool(
                    getattr(other, "model", None) or getattr(other, "field_model", None))):
                return False
            verdict = getattr(s.library, "costume_verdict", lambda *args: None)(v.key, other.key)
            return verdict is None or bool(verdict["ok"]) or bool(verdict.get("preview"))
        else:
            return False
        return other is not None and other.family == v.family and bool(getattr(other, kind, None)) and (
            kind == "spr" or getattr(other, kind, None) == core)

    def preview_sources_of(key: str) -> dict:
        pin = getattr(s.library, "current_pin", lambda: None)()
        if pin is None:
            return {}
        sources = s.state.preview_sources(pin.key, key)
        return valid_preview_sources(key, sources)

    def valid_preview_sources(key: str, sources: dict, mods: dict | None = None, variants: dict | None = None) -> dict:
        valid = {}
        for kind, source in sources.items():
            try:
                if preview_source_valid(key, kind, source, mods, variants):
                    valid[kind] = source
            except (ServiceError, KeyError):
                pass
        return valid

    def costume_keys(variant: str, others: Any) -> list[str]:
        if (not isinstance(variant, str) or not variant or not isinstance(others, list) or len(others) > 64
                or not all(isinstance(o, str) and o and len(o) <= 256 for o in others)):
            raise ServiceError("bad_params", key="others")
        return list(dict.fromkeys(others))

    @rpc.method("model.costumes")
    def model_costumes(variant: str, others: list[str]) -> dict[str, str | None]:
        """The saved verdicts of other variants' costumes on a variant's model (Switch 3D Model, BL-122): "ok",
        "preview" (past the stretch limit: shown, and applied only for another student's costume, BL-131),
        "refused", or None when not checked yet (``model.checkCostumes``). "ok" and "preview" pairs are offered;
        ``others`` may name any student's variants (the switch's search hits)."""
        out: dict[str, str | None] = {}
        for other in costume_keys(variant, others):
            try:
                if not preview_source_valid(variant, "model", f"variant:{other}"):
                    out[other] = "refused"
                    continue
                verdict = s.library.costume_verdict(variant, other)
            except (ServiceError, KeyError):
                out[other] = "refused"
                continue
            out[other] = verdict_kind(verdict)
        return out

    def verdict_kind(verdict: dict | None) -> str | None:
        return None if verdict is None else "ok" if verdict["ok"] else "preview" if verdict.get("preview") else "refused"

    @rpc.method("model.checkCostumes")
    def model_check_costumes(variant: str, others: list[str]) -> str:
        """A job checking other variants' costumes on a variant's model to their end (``Services.costume_check``);
        its result is {other: "ok" | "preview" | "refused"}. A refusal of the moment is "refused" and not saved."""
        keys = costume_keys(variant, others)

        def run(log: Any, progress: Any) -> dict[str, str]:
            out: dict[str, str] = {}
            for i, other in enumerate(keys):
                if progress:
                    progress(i, len(keys), "")
                try:
                    verdict = s.costume_check(variant, other) if preview_source_valid(
                        variant, "model", f"variant:{other}") else None
                except (ServiceError, KeyError):
                    verdict = None
                out[other] = verdict_kind(verdict) or "refused"
            return out

        return s.submit("costume-check", run)

    @rpc.method("model.optimize")
    def model_optimize(variant: str, source: str, name: str = "") -> str:
        """A job running Optimize Physics on a variant's 3D model from one source (BL-132): "official" or
        "variant:<key>", another variant's costume. Its result is {"mod": id}, the copy kept in the library, or
        {"unneeded": true, "reason": "official" | "no_gain"}. ``name`` is the source as the screen shows it, the copy's
        name."""
        if not isinstance(variant, str) or not variant or not isinstance(source, str) or not source or len(source) > 264:
            raise ServiceError("bad_params", key="source")
        if not isinstance(name, str) or len(name) > 200:
            raise ServiceError("bad_params", key="name")
        if source != "official" and not preview_source_valid(variant, "model", source):
            raise ServiceError("source_unsupported", source=source)

        library = s.library
        pin = s._current_pin(library)

        def run(log: Any, progress: Any) -> dict[str, Any]:
            progress({"current": 1, "total": 1, "step": ""})
            return library.optimize_costume(variant, source, name, pin=pin)

        return s.submit("model.optimize", run, writes="library", library=library)

    @rpc.method("family.get")
    @s.timings.measure("detail.data")
    def family_get(key: str) -> dict[str, Any]:
        f = family_with_assignments(s.catalog, s.library.assigned_targets(), key)
        mods, favorites, recent = mods_by_target(), s.library.favorites(), s.state.recent()
        seen = s.state.seen_mods()
        variants = []
        custom = variant_aliases()
        for v in f.variants:
            item = variant_json(v, mods, favorites, recent, seen, custom=custom)
            item["state"] = to_json(s.library.state(v.key))
            item["modList"] = [{**to_json(m), "fresh": seen is not None and m.user_added and m.id not in seen}
                               for m in mods.get(v.key, [])]
            for mod in item["modList"]:
                try:
                    if getattr(s.library, "preview_incomplete", lambda _: False)(mod["id"]):
                        mod["incomplete"] = True
                except ServiceError:
                    pass
            item["modelCores"] = getattr(s.library, "model_cores", lambda key: [v.model] if v.model else [])(v.key)
            item["modelFiles"] = model_files(v.model, item["state"])
            variants.append(item)
        # Include the installation: two servers can have the same resource hash.
        return {"key": f.key, "base": f.names, "aliases": f.aliases,
                "school": f.school, "variants": variants,
                "version": getattr(s.game, "cache_version", s.game.resource_version) if s.game_found() else None}

    @rpc.method("aliases.set")
    def aliases_set(aliases: list[str], variant: str | None = None, family: str | None = None) -> list[str]:
        """Keep the user's own aliases of a variant (the roster search matches them) and return them as kept: each
        cleaned (state.alias_text), empty and repeated ones dropped. ``alias_too_long`` or ``alias_too_many`` past the
        limits; nothing is kept then."""
        if not isinstance(variant if variant is not None else family, str) or not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise ServiceError("bad_params", key="aliases")
        # The legacy family argument edits the original variant only.
        key = visible_variant(variant).key if variant is not None else alias_target(
            family_with_assignments(s.catalog, s.library.assigned_targets(), family))
        names = [alias_text(a) for a in aliases]
        if any(len(name) > ALIAS_LENGTH for name in names):
            raise ServiceError("alias_too_long", max=ALIAS_LENGTH)
        if len({alias_key(name) for name in names if name}) > ALIAS_MAX:
            raise ServiceError("alias_too_many", max=ALIAS_MAX)
        variant_aliases()
        return s.state.set_aliases(key, names)

    warmed: dict[str, Any] = {"game": None}
    warm_lock = threading.Lock()

    @rpc.method("lobby.warm")
    def lobby_warm() -> None:
        """Warm the memorial prefab index once per game. CAB dependencies resolve on demand."""
        if not s.game_found():
            return
        game = s.game
        with warm_lock:
            if warmed["game"] is not None and warmed["game"]() is game:
                return
            warmed["game"] = weakref.ref(game)

        def build() -> None:
            try:
                game._lobby_prefabs()
            except Exception:  # the lobby builds them again when it needs them
                logger.debug("Cannot build the memorial lobby indexes", exc_info=True)

        threading.Thread(target=build, name="lobby-warm", daemon=True).start()

    @rpc.method("lobby.camera.save")
    def lobby_camera_save(variant: str, lobby: str, view: dict, scale: float, offset: list,
                          source: str = "official") -> str:
        """A job keeping the lobby's framing (``lobby.js cameraParams``) as a new Memorial Lobby choice of the variant
        (``library/lobby_camera.py``): its art the source the lobby showed ("official", ``mod:<id>`` of a lobby mod of
        the variant, or one of its cameras), its camera the prefab scaled by ``scale`` and moved by ``offset``. Its
        result: {variant, lobby, number, scale, offset, mod}. ``view`` is the framed view, for the log only."""
        from ..gameassets import AssetNotFound
        from ..library import lobby_camera

        v = visible_variant(variant) if isinstance(variant, str) else None
        if v is None or not v.lobby or lobby != v.lobby:
            raise ServiceError("bad_params", key="lobby")
        if not isinstance(source, str) or len(source) > 264 or not (
                source == "official" or preview_source_valid(variant, "lobby", source)):
            raise ServiceError("source_unsupported", source=str(source)[:264])
        if not isinstance(view, dict) or not all(lobby_camera._number(view.get(key)) for key in ("x", "y", "w", "h")):
            raise ServiceError("bad_params", key="view")
        k, (ox, oy) = lobby_camera.check_params(scale, offset)
        finder = getattr(s.game, "lobby_prefab", None) if s.game_found() else None
        try:
            found = finder(v.lobby) if finder is not None else None
        except (AssetNotFound, OSError, ValueError) as error:
            raise ServiceError("lobby_camera_unsupported", name=v.lobby) from error
        if found is None:
            raise ServiceError("lobby_camera_unsupported", name=v.lobby)
        prefab, root = found
        library = s.library
        pin = s._current_pin(library)

        def run(log: Any, progress: Any) -> dict[str, Any]:
            progress({"current": 1, "total": 1, "step": ""})
            log(f"Lobby camera of {variant}: view {view}, scale {k:.4f}, offset {ox:.1f}, {oy:.1f}")
            record = library.save_camera(variant, source, k, [ox, oy], prefab, root, pin=pin, log=log)
            meta = record[lobby_camera.TAG]
            return {"variant": variant, "lobby": v.lobby, "number": meta["number"], "scale": meta["scale"],
                    "offset": meta["offset"], "mod": record["id"]}

        return s.submit("lobby.camera.save", run, writes="library", library=library)

    @rpc.method("lobby.lines")
    def lobby_lines(variant: str, talks: dict[str, list[str]] | None = None, lang: str | None = None) -> dict[str, Any]:
        """The memorial lobby's lines by voice clip ({clip: {lang: text, "voice": bool}}): Kivo's (zh-CN, ja, the
        voices) and, for a ``lang`` other than zh-CN, the Global server's official text on the clips of each talk
        animation (``talks``: {animation: [clip ids in order]} from the skeleton, else Kivo's clip names).
        ``lines_unavailable`` when neither can be read; the Global text is left out when its table takes longer
        than LINES_WAIT."""
        v = visible_variant(variant)
        lang = lang or LINE_LANGS.get(i18n_manager.lang, "en")
        character = getattr(v, "schale_id", None)
        # the Global table (a download the first time) is read while Kivo's page is
        until = time.monotonic() + LINES_WAIT
        pending = _background(s.global_lines.dialogues, character) if lang != "zh-CN" and character else None
        try:
            kivo = s.kivo.lines(v.page_id or "")
        except ServiceError:
            kivo = None
        official: dict[str, dict[str, str]] = {}
        if pending is not None:
            try:
                table = pending.result(timeout=max(0.0, until - time.monotonic()))
                official = globallines.by_clip(table, clip_talks(talks) or globallines.talks_of(kivo or {}))
            except FutureTimeout:
                logger.info("Global lobby lines not ready after %.0f s: only Kivo's for %s", LINES_WAIT, variant)
            except ServiceError:
                pass
        if kivo is None and not official:
            raise ServiceError("lines_unavailable")
        return globallines.merge(kivo or {}, official)

    @rpc.method("favorites.set")
    def favorites_set(variant: str, on: bool) -> None:
        s.library.set_favorite(variant, bool(on))
        s.events.publish("library.changed")

    @rpc.method("ui.visit")
    def ui_visit(variant: str) -> None:
        s.state.visit(variant)

    @rpc.method("ui.preview")
    def ui_preview(variant: str, kind: str, source: str) -> dict:
        if not all(isinstance(value, str) for value in (variant, kind, source)) or kind not in ("spr", "lobby", "model"):
            raise ServiceError("bad_params", key="source")
        if not preview_source_valid(variant, kind, source):
            raise ServiceError("bad_params", key="source")
        pin = s.library.current_pin()
        if pin is None:
            raise ServiceError("game_not_found")
        s.state.set_preview_source(pin.key, variant, kind, source)
        return s.state.preview_sources(pin.key, variant)

    # --- batch import ---

    @rpc.method("import.pick")
    def import_pick(folder: bool = False, recursive: bool = False, directory: str | None = None) -> dict[str, Any]:
        return s.batch_import.pick(folder, recursive, directory)

    @rpc.method("import.analyze")
    def import_analyze(paths: list[str], recursive: bool = False) -> str:
        return s.batch_import.analyze(paths, recursive)

    @rpc.method("import.setSource")
    def import_set_source(id: str, server: str, platform: str) -> str:
        return s.batch_import.set_source(id, server, platform)

    @rpc.method("import.catalog")
    def import_catalog() -> dict[str, Any]:
        return s.batch_import.assignment_catalog()

    @rpc.method("import.assignCharacter")
    def import_assign_character(id: str, variant: str) -> str:
        return s.batch_import.assign_character(id, variant)

    @rpc.method("import.run")
    def import_run(ids: list[str], options: dict[str, Any], revision: int) -> str:
        return s.batch_import.run(ids, options, revision)

    @rpc.method("import.options")
    def import_options() -> dict[str, Any]:
        return s.batch_import.options()

    @rpc.method("import.setOptions")
    def import_set_options(strategy: str, types: list[str], skipUnchanged: bool, crc: str,
                           compression: str, extraBytes: str, backup: bool, workers: int,
                           output: str | None, enableAfterImport: bool, previewAfterImport: bool = False) -> dict[str, Any]:
        return s.batch_import.set_options({"strategy": strategy, "types": types, "skipUnchanged": skipUnchanged,
            "crc": crc, "compression": compression, "extraBytes": extraBytes, "backup": backup,
            "workers": workers, "output": output, "enableAfterImport": enableAfterImport,
            "previewAfterImport": previewAfterImport})

    # --- mods ---

    @rpc.method("mods.add")
    def mods_add(paths: list[str], background: bool = False) -> Any:
        # dev mode: refused before the library is built (ModLibrary.add checks again)
        for p in paths:
            devmode.check_read(Path(p))
        if background:
            library = s.library
            pin = library.current_pin()

            def add(log: Any, progress: Any) -> Any:
                if pin:
                    s._check_pin(library, pin)
                return library.add_and_apply([Path(p) for p in paths], progress=progress, pin=pin, log=log)

            return s.submit("mods.add", add, writes="game", library=library)
        library = s.library
        added = library.add_and_apply([Path(p) for p in paths])
        changed = [*added.applied, *added.configured, *added.installed]
        s.events.publish("library.changed", {"cores": sorted({m.core for m in changed}),
                                              "kinds": sorted({m.kind for m in changed}), "mods": True})
        if added or added.installed:
            s.refresh_status(library)
            s.publish_state()
            s.request_scan()
        return added

    @rpc.method("mods.pick")
    def mods_pick(background: bool = False) -> Any:
        if s.window is None:
            raise ServiceError("native_unavailable")
        from .native import pick_files

        paths = pick_files(s.window, ("Bundle (*.bundle;*.backup)",))
        return mods_add([str(p) for p in paths], background) if paths else []

    @rpc.method("mods.removalInfo")
    def mods_removal_info(id: str) -> dict[str, Any]:
        return s.library.removal_info(id)

    @rpc.method("mods.other")
    def mods_other() -> list[dict[str, Any]]:
        return s.library.other_mods()

    @rpc.method("mods.remove")
    def mods_remove(id: str, confirm: bool = False) -> str:
        """Delete a library source as one writer job; game write refusals remain in its result and drift."""
        library = s.library
        if id == 'official':
            raise ServiceError('asset_official')
        def fallback(variant, kind):
            try:
                value = variants[variant]
                if getattr(value, kind, None):
                    return 'official'
            except (KeyError, ServiceError):
                pass
            return next(('mod:' + mod.id for mod in remaining
                         if mod.id != id and mod.target == variant and mod.kind == kind), None)
        remaining = []
        variants = {}
        def remove(log, progress):
            remaining[:] = library.mods()
            variants.update((v.key, v) for family in s.catalog.families() for v in family.variants)
            result = library.remove(id, confirm=bool(confirm), log=log,
                reconcile=lambda: s.state.reconcile_deleted_source('mod:' + id, fallback))
            if result.failed:
                error = result.failed[0]
                raise ServiceError(error['code'], **error.get('params', {}))
            return result
        return s.submit("mods.remove", remove, writes="library", library=library)

    @rpc.method("mods.rename")
    def mods_rename(id: str, label: str) -> dict[str, Any]:
        entry = s.library.rename(id, label)
        s.events.publish("library.changed", {"cores": [], "kinds": [], "mods": True})
        return entry.to_json()

    @rpc.method("mods.seen")
    def mods_seen(id: str | None = None, ids: list[str] | None = None) -> None:
        if (id is None) == (ids is None):
            raise ServiceError("bad_params", key="ids")
        chosen = [id] if id is not None else ids
        if not isinstance(chosen, list) or not all(isinstance(v, str) and v for v in chosen):
            raise ServiceError("bad_params", key="ids")
        if chosen:
            s.state.mark_mods_seen(chosen)
            s.events.publish("library.changed", {"cores": [], "kinds": [], "mods": True})

    # --- the Enable Mods switch, mods already in use, drift ---

    @rpc.method("mods.state")
    def mods_state() -> dict[str, Any]:
        """The switch's state for every place that shows it, from the snapshot (never through the library lock)."""
        return s.mods_state()

    @rpc.method("mods.toggle")
    def mods_toggle(enabled: bool, expect: bool) -> str:
        """Turn mods off or on in every known install (also Retry: ``expect`` equal to the current intent)."""
        return s.submit_toggle(enabled, expect)

    @rpc.method("mods.consent")
    def mods_consent(granted: bool) -> dict[str, Any]:
        if not isinstance(granted, bool):
            raise ServiceError("bad_params", key="granted")
        s.set_scan_consent(granted)
        return {}

    @rpc.method("mods.explained")
    def mods_explained() -> dict[str, Any]:
        s.state.set("mods_explained", True)
        s.publish_state()
        return {}

    @rpc.method("mods.scan")
    def mods_scan(dismissed: bool = False) -> dict[str, bool]:
        """Request a scan (``dismissed``: Settings' Check, which lists dismissed entries again); the result comes as
        ``library.scanned``. ``scan_off`` without the scan consent."""
        if not s.scan_allowed():
            raise ServiceError("scan_off")
        return {"requested": s.request_scan(bool(dismissed)) is not None}

    @rpc.method("mods.entries")
    def mods_entries() -> list[Any]:
        """The last scan's entries, as every writer job's result updated them; none without the scan consent."""
        return s.snapshot.entries() if s.scan_allowed() else []

    def entry_list(entries: Any) -> list[dict[str, Any]]:
        if not isinstance(entries, list) or not all(isinstance(item, dict) and isinstance(item.get("install"), str)
                                                    and isinstance(item.get("core"), str) for item in entries):
            raise ServiceError("bad_params", key="entries")
        return [{"install": item["install"], "core": item["core"], **({"state": item["state"]}
                 if isinstance(item.get("state"), str) else {})} for item in entries]

    @rpc.method("mods.adopt")
    def mods_adopt(entries: list[dict[str, Any]]) -> str:
        """Add to Library: a library writer job, allowed while the game runs and while a classic window is open."""
        listed = entry_list(entries)
        library = s.library
        pins = library.installs()
        return s.submit("library.adopt", lambda log, progress: library.adopt(listed, pins, log=log, progress=progress),
                        writes="library", library=library)

    @rpc.method("mods.restoreDrift")
    def mods_restore_drift(entries: list[dict[str, Any]]) -> str:
        """The drift prompt's Apply Mod Configuration."""
        return s.submit_restore_drift(entry_list(entries))

    @rpc.method("mods.dismiss")
    def mods_dismiss(kind: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """The drift prompt's Keep as Is, the untracked prompt's Don't Ask Again."""
        listed = entry_list(entries)
        s.library.dismiss(kind, listed)
        s.snapshot.mark_dismissed({(item["install"], item["core"].lower()) for item in listed})
        s.publish_state()
        return {}

    @rpc.method("mods.impact")
    def mods_impact(ids: list[str] | None = None, force: bool = False) -> str:
        """What a game update did under the library's mods (a reader job). Without ``ids``: the impact summary of the
        current install, classified only when its resource version differs from the last one seen, or always with
        ``force`` (Settings' View) (``update_impact``). With ``ids``: those mods' impacts now, {mods: [{id,
        impact}]}."""
        if ids is not None and (not isinstance(ids, list) or len(ids) > 4096
                                or not all(isinstance(v, str) and v for v in ids)):
            raise ServiceError("bad_params", key="ids")
        if not isinstance(force, bool):
            raise ServiceError("bad_params", key="force")
        library = s.library
        pin = s._current_pin(library)
        if ids is None:
            return s.submit("mods.impact", lambda log, progress: library.update_impact(pin, force=force,
                                                                                      progress=progress))
        return s.submit("mods.impact", lambda log, progress: {"mods": library.impacts(ids, pin)})

    @rpc.method("mods.updateDue")
    def mods_update_due() -> dict[str, Any]:
        """Whether the start asks to check the mods against a game update (``update_due``): {due, install, version,
        previous}. Reads the version file and the configuration only."""
        library = s.library
        return library.update_due(library.current_pin())  # no game folder: nothing is asked

    @rpc.method("mods.impactSeen")
    def mods_impact_seen(install: str, version: str) -> dict[str, Any]:
        """The impact summary of a game update was shown: its resource version is not told again."""
        if not isinstance(install, str) or not isinstance(version, str) or not version:
            raise ServiceError("bad_params", key="version")
        s.library.impact_seen(install, version)
        return {}

    @rpc.method("mods.confirmUntested")
    def mods_confirm_untested(confirm: dict[str, Any]) -> dict[str, Any]:
        """The user confirmed the writes an ``untested_build`` refusal named: its ``confirm`` given back
        ({install: {version, cores}}); the refused call is then made again."""
        s.library.confirm_untested(confirm)
        return {}

    @rpc.method("mods.sizes")
    def mods_sizes(kind: str) -> dict[str, int]:
        return s.library.sizes(kind)

    @rpc.method("mods.clearBuilt")
    def mods_clear_built() -> str:
        library = s.library
        return s.submit("library.clearBuilt", lambda log, progress: {"freed": library.clear_built()},
                        writes="library", library=library)

    @rpc.method("mods.forget")
    def mods_forget(install: str) -> str:
        """Remove from List: a recorded install this data directory's settings no longer name; ``bad_params`` for a
        named one."""
        library = s.library
        named = {item["key"] for item in library.status().get("installs", []) if item.get("named")}
        if not isinstance(install, str) or install in named:
            raise ServiceError("bad_params", install=install)
        return s.submit("library.forget", lambda log, progress: library.forget(install), writes="library",
                        library=library)

    # --- actions (jobs) ---

    @rpc.method("actions.apply")
    def actions_apply(variant: str, source: str, kind: str = "spr") -> str:
        """Switch what the game shows: write a source into the variant's portrait ("spr") or memorial lobby ("lobby")
        game files of the current install, pinned at submit, and configure it. The library keeps what it replaces
        (ModLibrary._commit). While mods are off it only configures the source (``deferred``)."""
        return s.submit_apply(variant, source, kind)

    @rpc.method("actions.restore")
    def actions_restore(variant: str, kind: str = "spr") -> str:
        return s.submit_restore(variant, kind)

    @rpc.method("actions.update")
    def actions_update(mod: str) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit("update", lambda log, progress: library.update(mod, log=log, progress=progress, pin=pin),
                        writes="library", library=library)

    @rpc.method("actions.extract")
    def actions_extract(target: str) -> str:
        out_dir = s.settings.output_subdir("extract")
        library = s.library
        pin = library.current_pin()
        return s.submit("extract", lambda log, progress: {
            "folder": str(library.extract(target, out_dir, log=log, pin=pin, progress=progress))})

    @rpc.method("actions.compare")
    def actions_compare(mod: str) -> Any:
        return s.library.compare(mod)

    @rpc.method("actions.remap")
    def actions_remap(mod: str, geometry: Any = None) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.remap(mod, log=log, progress=progress, pin=pin,
                                                                     geometry=geometry))

    @rpc.method("actions.oldRig")
    def actions_old_rig(mod: str) -> str:
        """The repair review of a texture-only portrait with the rig it was drawn for (ModLibrary.old_rig); accepted
        or discarded as a remap's."""
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.old_rig(mod, log=log, progress=progress, pin=pin))

    def spine_licensed() -> None:
        # the graft writes skeletons with code derived from the Spine runtime: only after the user's licence answer
        if s.state.spine_consent is not True:
            raise ServiceError("graft_spine_licence")

    @rpc.method("actions.graft")
    def actions_graft(mod: str) -> str:
        """The first step of a graft of the official rig's new animations into a mod's own rig (ModLibrary.graft):
        the rigs for the page to pose."""
        spine_licensed()
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.graft(mod, log=log, progress=progress, pin=pin))

    @rpc.method("actions.graftBuild")
    def actions_graft_build(token: str, geometry: Any = None) -> str:
        spine_licensed()
        library = s.library

        def build(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.extract"})
            return library.graft_build(token, geometry)

        return s.submit("remap", build)

    @rpc.method("actions.graftCheck")
    def actions_graft_check(token: str, geometry: Any = None) -> str:
        """The last step: the review of the grafted copy, accepted or discarded as a remap's."""
        spine_licensed()
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.graft_check(token, geometry, log=log,
                                                                           progress=progress, pin=pin))

    @rpc.method("actions.discardGraft")
    def actions_discard_graft(token: str) -> None:
        s.library.discard_graft(token)

    @rpc.method("actions.redrawn")
    def actions_redrawn(mod: str, geometry: Any) -> str:
        library = s.library
        pin = s._current_pin(library)

        def check(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
            return library.redrawn(mod, geometry, pin=pin)

        return s.submit("compatibility", check)

    @rpc.method("actions.acceptRemap")
    def actions_accept_remap(preview: str) -> str:
        library = s.library
        def accept(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.add"})
            return library.accept_remap(preview)

        return s.submit("remap.accept", accept,
                        writes="library", library=library)

    @rpc.method("actions.discardRemap")
    def actions_discard_remap(preview: str) -> None:
        s.library.discard_remap(preview)

    @rpc.method("actions.compatibilityStamp")
    def actions_compatibility_stamp(mod: str) -> str:
        library = s.library
        return library.compatibility_stamp(mod, pin=s._current_pin(library))

    @rpc.method("actions.compatibility")
    def actions_compatibility(mod: str) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit("compatibility", lambda log, progress: library.compatibility(mod, pin=pin, progress=progress))

    @rpc.method("jobs.get")
    def jobs_get(id: str) -> dict[str, Any]:
        job = s.jobs.get(id)
        return {**job.to_json(), "log": job.log[-500:]}

    # --- system ---

    @rpc.method("os.reveal")
    def os_reveal(path: str) -> None:
        reveal(path)

    from .export_service import register_api as register_export_api
    from .student_hall import register_api as register_hall_api

    register_export_api(rpc, s)
    register_hall_api(rpc, s)
    return rpc
