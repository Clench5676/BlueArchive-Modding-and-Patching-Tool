"""Tk-free application settings.

Reads and writes the same ``config.toml`` as the classic GUI (``gui/configs.py``): the same groups, keys and defaults,
so both GUIs share one file. Unknown groups and keys found in the file are kept when saving.

A file that cannot be read starts the app on defaults instead of stopping it: a group that is not a table or a value of
another type is read as missing (the rest of the file is still read), and a file that cannot be parsed as a whole
(non-UTF-8 bytes, a syntax error) reads as all defaults, is copied to ``config.toml.bad`` before any save replaces it,
and is never applied by ``reload_if_changed`` (a save in progress leaves a half-written file for a moment).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import toml

from bmpt.core import devmode
from bmpt.engine import (
    EXE_DIR,
    AnimCheckOptions,
    CharacterInternalIDMap,
    SaveOptions,
    SkelConvertOptions,
    get_BA_path,
    get_spine_backend,
    parse_hex_bytes,
    utils,
)
from .data_paths import APP_DIR_NAME, OLD_APP_DIR_NAME, default_cache_dir, migrate_cache_dir
from .core.logging_utils import log_failure

CONFIG_FILE = EXE_DIR / "config.toml"

logger = logging.getLogger(__name__)
_ignored_game_dirs: set[str] = set()  # dev mode: game folders outside the dev roots, logged once each


@dataclass(frozen=True)
class Field:
    """One setting of ``config.toml``: its group, its default and the type its values are coerced to."""

    group: str
    default: Any  # a value, or a callable returning one
    # the value's type (bool, int or str): values read from the file are coerced to it, so a callable default (game
    # detection) runs only when the key is missing; inferred from a plain default, required for a callable one
    kind: type | None = None

    def __post_init__(self) -> None:
        """Infer the type from a plain default; a callable default must name it."""
        if self.kind is None:
            if callable(self.default):
                raise TypeError("a field with a callable default needs its kind")
            object.__setattr__(self, "kind", type(self.default))


def install_needs_crc(file_source: object) -> bool:
    """Whether an install of this file source needs the CRC fix.

    The Windows clients check every bundle's CRC against its file name, Android ones do not. Fails closed: any source
    not known to be Android (``adb_*``) needs it.
    """
    return not str(file_source).startswith("adb_")


def _default_file_source() -> str:
    if get_BA_path("global"):
        return "windows_global"
    if get_BA_path("japan"):
        return "windows_japan"
    return "windows_global"


# key -> field, in the order of gui/configs.py
FIELDS: dict[str, Field] = {
    # Directories
    "file_source": Field("Directories", _default_file_source, str),
    "game_resource_dir": Field("Directories", lambda: get_BA_path("global") or "", str),
    "game_resource_dir_japan": Field("Directories", lambda: get_BA_path("japan") or "", str),
    "game_dir_android_global": Field(
        "Directories", "/storage/emulated/0/Android/data/com.nexon.bluearchive/files/PUB/Resource/"
    ),
    "game_dir_android_japan": Field("Directories", "/storage/emulated/0/Android/data/com.Yostar JP.BlueArchive/files/"),
    "mod_backup_path": Field("Directories", lambda: str(EXE_DIR / "output" / "backup"), str),
    # AppSettings
    "language": Field("AppSettings", ""),
    "output_dir": Field("AppSettings", lambda: str(EXE_DIR / "output"), str),
    "max_workers": Field("AppSettings", 4),
    # SaveOptions
    "extra_bytes": Field("SaveOptions", "0x08080808"),
    "enable_crc_correction": Field("SaveOptions", "auto"),
    "create_backup": Field("SaveOptions", True),
    "compression_method": Field("SaveOptions", "lzma"),
    "skip_unchanged": Field("SaveOptions", True),
    # ResourceTypes
    "replace_texture2d": Field("ResourceTypes", True),
    "replace_textasset": Field("ResourceTypes", True),
    "replace_mesh": Field("ResourceTypes", True),
    "replace_animationclip": Field("ResourceTypes", True),
    "replace_all": Field("ResourceTypes", False),
    # SpineConverter
    "spine_converter_path": Field("SpineConverter", ""),
    "enable_spine_conversion": Field("SpineConverter", False),
    "target_spine_version": Field("SpineConverter", "4.2.33"),
    # SpineDowngrade
    "enable_spine_downgrade": Field("SpineDowngrade", False),
    "spine_downgrade_version": Field("SpineDowngrade", "3.8.75"),
    "scale_atlas": Field("SpineDowngrade", False),
    # SpineViewer
    "spine_viewer_path": Field("SpineViewer", ""),
    "check_animations": Field("SpineViewer", False),
    "preview_thumbnail_size": Field("SpineViewer", 768),
    "mod_install_render_preview": Field("SpineViewer", True),
    # BACIIMap
    "bacii_map_path": Field("BACIIMap", lambda: str(EXE_DIR / "Addons" / "BA-Characters-Internal-ID.csv"), str),
    "character_index_column": Field("BACIIMap", "file_id"),
    "character_name_field": Field("BACIIMap", CharacterInternalIDMap.DEFAULT_NAME_FIELD),
    # Tabs
    "enable_spine38_namefix": Field("Tabs", False),
    "enable_bleed": Field("Tabs", False),
    "unpack_atlas": Field("Tabs", False),
    "enable_spine_preview": Field("Tabs", False),
    "render_preview": Field("Tabs", False),
    # Tools
    "report_format": Field("Tools", "list"),
    "report_render_preset": Field("Tools", "low"),
    # ADB
    "adb_path": Field("ADB", "adb"),
    "adb_device": Field("ADB", ""),
    "adb_cache_dir": Field("ADB", lambda: str(EXE_DIR / "Addons" / "adb_cache"), str),
}


def _default(key: str) -> Any:
    d = FIELDS[key].default
    return d() if callable(d) else d


def _coerce(key: str, value: Any) -> Any:
    """Bring a value read from the file to the field's type, without running a callable default."""
    kind = FIELDS[key].kind
    try:
        if kind is bool:
            return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
        if kind is int:
            return int(value)
        if kind is str:
            return str(value)
    except (TypeError, ValueError):
        return _default(key)
    return value


def migrate_game_directories(directories: dict, detect: Callable[[str], str | None]) -> dict:
    """Move a single legacy game folder to the Japan key when install detection identifies it as Japan only.

    The Global and Japan keys stay as they are, so older versions can still read both folders. Explicit pairs and
    unrecognised folders keep their assignments.

    Args:
        directories: The ``Directories`` table read from the file; returned as is when it is not a table.
        detect: Returns the game folder detected for a region (``"global"``, ``"japan"``), or None or empty.

    Returns:
        The table, with ``game_resource_dir`` moved to ``game_resource_dir_japan`` when only the Japan install
        detected lies in the same install as the legacy folder.
    """
    if not isinstance(directories, dict):
        return directories
    legacy = directories.get("game_resource_dir")
    if (
        not isinstance(legacy, str)
        or not legacy
        or "\0" in legacy
        or directories.get("game_resource_dir_japan")
        or not devmode.allows_read(legacy)
    ):
        return directories
    from .library.judge import install_root

    try:
        root = install_root(Path(legacy).expanduser())
        matches = {
            region
            for region in ("global", "japan")
            if (folder := detect(region))
            and devmode.allows_read(folder)
            and install_root(Path(folder).expanduser()) == root
        }
    except (OSError, RuntimeError, ValueError):
        return directories
    if matches != {"japan"}:
        return directories
    return {**directories, "game_resource_dir": "", "game_resource_dir_japan": str(legacy)}


class Settings:
    """The settings in ``config.toml``. Thread-safe; ``reload_if_changed`` picks up saves made by the classic GUI.

    In dev mode (devmode) the file must lie inside the dev roots, and a game folder outside them is not used.
    """

    def __init__(self, path: Path = CONFIG_FILE):
        """Read the settings file.

        Args:
            path: The settings file; ``config.toml`` in the data folder by default.

        Raises:
            ServiceError: ``dev_path_outside`` in dev mode, for a file outside the dev roots.
        """
        devmode.check_read(path)
        self.path = Path(path)
        self.bad = self.path.with_name(f"{self.path.name}.bad")
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._extra: dict[str, dict] = {}  # groups and keys this schema does not know, kept on save
        self._mtime: tuple[int, int] | None = None  # the file's (mtime, size) when it was last applied or saved
        self._failed: tuple[int, int] | None = None  # the (mtime, size) of the file the last read of failed on
        self._applied = False  # whether a read of the file has been applied yet (a failed one is not applied after)
        self._listeners: list[Callable[[], None]] = []
        self.load()

    # --- file ---

    def _read_file(self, *, reload: bool) -> tuple[dict | None, Exception | None]:
        """Read and parse the file.

        (the file's tables, None); ({}, None) while it is missing; (None, why) when it exists but cannot be read
        or parsed as a whole. On a reload a file without content counts as unreadable as well: it is what a save in
        progress leaves for a moment, and an empty file is never the app's own.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
            if reload and not text.strip():
                raise ValueError("no content")
            return toml.loads(text), None
        except FileNotFoundError:
            return {}, None
        except Exception as error:  # OSError, bad bytes, a syntax error, and whatever the parser raises on garbage
            return None, error

    def load(self) -> bool:
        """Read the file into the settings.

        Missing keys take their defaults; a missing game folder is detected.

        Returns:
            False when the file exists but cannot be read as a whole: at the first read everything is then default,
            afterwards nothing changes (a failed read is never applied).
        """
        with self._lock:
            signature = self._stat()
            data, error = self._read_file(reload=self._applied)
            if data is None:
                if signature != self._failed:  # once per state of the file, not at every poll
                    log_failure(
                        logger,
                        logging.WARNING,
                        "Cannot read config.toml, keeping the settings in use"
                        if self._applied
                        else "Cannot read config.toml, using defaults",
                        error,
                    )
                self._failed = signature
                if self._applied:
                    return False
                data = {}
            else:
                self._failed = None
            for group, entries in list(data.items()):
                if not isinstance(entries, dict):  # nothing to read in it: its keys read as missing
                    logger.warning("config.toml: %s is not a table, using defaults for it", group)
                    del data[group]
            values, known = {}, set()
            detected: dict[str, str] = {}

            def detect(region: str) -> str:
                if region not in detected:
                    detected[region] = get_BA_path(region) or ""
                return detected[region]

            data["Directories"] = migrate_game_directories(data.get("Directories", {}), detect)
            for key, f in FIELDS.items():
                group = data.get(f.group, {})
                if key in group and isinstance(
                    group[key], (str, int, float, bool)
                ):  # else (a date, a list) it reads as missing
                    values[key] = _coerce(key, group[key])
                elif key == "file_source":
                    values[key] = (
                        "windows_global"
                        if detect("global")
                        else "windows_japan"
                        if detect("japan")
                        else "windows_global"
                    )
                elif key in ("game_resource_dir", "game_resource_dir_japan"):
                    values[key] = detect("japan" if key.endswith("japan") else "global")
                else:
                    values[key] = _default(key)
                known.add((f.group, key))
            extra: dict[str, dict] = {}
            for group, entries in data.items():
                for k, v in entries.items():
                    if (group, k) not in known:
                        extra.setdefault(group, {})[k] = v
            self._values, self._extra = values, extra
            self._applied = True
            self._mtime = signature if error is None else None
            return error is None

    def save(self) -> None:
        """Write the settings, with the groups and keys this schema does not know, in one replace.

        A file on disk that cannot be parsed is first copied to ``config.toml.bad``.
        """
        with self._lock:
            data: dict[str, dict] = {g: dict(v) for g, v in self._extra.items()}
            for key, f in FIELDS.items():
                data.setdefault(f.group, {})[key] = self._values[key]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._keep_unreadable()
            tmp = self.path.with_suffix(".toml.tmp")
            tmp.write_text(toml.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime, self._failed = self._stat(), None

    def _keep_unreadable(self) -> None:
        """Copy a config.toml that cannot be parsed as a whole to config.toml.bad before a save replaces it.

        The user may want it back. A file without content, or one that cannot be copied, has nothing to keep.
        """
        data, _ = self._read_file(reload=False)
        if data is not None:
            return
        name = None
        try:
            fd, name = tempfile.mkstemp(prefix=f".{self.bad.name}.", suffix=".tmp", dir=self.bad.parent)
            with os.fdopen(fd, "wb") as out, self.path.open("rb") as stream:
                shutil.copyfileobj(stream, out)
            os.replace(name, self.bad)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Cannot keep the unreadable config.toml", error)
            if name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(name)

    def reload_if_changed(self) -> bool:
        """Reload when the file changed on disk since the last load or save, then call the ``on_change`` listeners.

        A file that cannot be read as a whole (a save in progress) changes nothing, and is read again once it changes.

        Returns:
            Whether it reloaded.
        """
        with self._lock:
            current = self._stat()
            if current == self._mtime or (current is not None and current == self._failed):
                return False
            if not self.load():
                return False
        for listener in list(self._listeners):
            listener()
        return True

    def on_change(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after each reload that picked up a change on disk."""
        self._listeners.append(listener)

    def _stat(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    # --- values ---

    def get(self, key: str) -> Any:
        """Return a setting's value; raises KeyError for a key ``FIELDS`` does not have."""
        with self._lock:
            return self._values[key]

    def set(self, key: str, value: Any) -> None:
        """Set a setting in memory (``save`` writes it), coerced to the field's type.

        Args:
            key: A key of ``FIELDS``.
            value: The new value; one that cannot be coerced becomes the default.

        Raises:
            KeyError: The key is not a setting.
        """
        if key not in FIELDS:
            raise KeyError(key)
        with self._lock:
            self._values[key] = _coerce(key, value)

    def as_dict(self) -> dict[str, Any]:
        """Return a copy of all settings by key."""
        with self._lock:
            return dict(self._values)

    # --- derived values (mirrors the builders in gui/app.py) ---

    def is_adb_mode(self) -> bool:
        """Whether the file source is a phone over ADB rather than a local install."""
        return str(self.get("file_source")).startswith("adb_")

    def server_region(self) -> str:
        """Return the file source's server: ``"japan"`` or ``"global"``."""
        return "japan" if self.get("file_source") in ("windows_japan", "adb_japan") else "global"

    def game_dir(self, region: str | None = None) -> Path | None:
        """Return the local game folder set for a server.

        In dev mode a folder outside the dev roots is logged once and not used.

        Args:
            region: ``"global"`` or ``"japan"``; None uses the current file source.

        Returns:
            The folder, or None in ADB mode, when it is not set, or when dev mode refuses it.
        """
        source = self.get("file_source") if region is None else f"windows_{region}"
        key = {"windows_global": "game_resource_dir", "windows_japan": "game_resource_dir_japan"}.get(source)
        value = self.get(key) if key else ""
        if not value:
            return None
        if not devmode.allows_read(value):
            if value not in _ignored_game_dirs:
                _ignored_game_dirs.add(value)
                logger.warning("Game folder outside the dev roots, not used: %s", value)
            return None
        return Path(value).expanduser()

    def resource_dirs(self) -> list[Path]:
        """Return the existing resource folders of the current local game (empty when there is none)."""
        from bmpt.engine import get_search_dirs

        base = self.game_dir()
        return get_search_dirs(base) if base and base.exists() else []

    def asset_types(self) -> set[str]:
        """Return the asset types a mod install replaces: ``{"ALL"}``, or the checked ones by Unity type name."""
        if self.get("replace_all"):
            return {"ALL"}
        names = {
            "replace_texture2d": "Texture2D",
            "replace_textasset": "TextAsset",
            "replace_mesh": "Mesh",
            "replace_animationclip": "AnimationClip",
        }
        return {v for k, v in names.items() if self.get(k)}

    def save_options(self, perform_crc: bool) -> SaveOptions:
        """Return the engine's bundle save options from the settings.

        Args:
            perform_crc: Whether the saved bundle gets the CRC fix, as ``crc_needed`` decides for the install.
        """
        return SaveOptions(
            perform_crc=perform_crc,
            extra_bytes=parse_hex_bytes(self.get("extra_bytes")),
            compression=self.get("compression_method"),
        )

    def crc_needed(self, target: Path | None = None) -> bool:
        """Whether files written for the selected install get the CRC fix.

        ``"true"`` and ``"false"`` force it; ``"auto"`` follows the install (``install_needs_crc``).

        Args:
            target: Accepted for the callers; it does not change the answer. ``auto`` never reads the live file:
                it may be another tool's mod or unreadable, and a wrong "no" would write a whole group unfixed.
        """
        setting = self.get("enable_crc_correction")
        if setting == "true":
            return True
        if setting == "false":
            return False
        return install_needs_crc(self.get("file_source"))

    def spine_options(self, upgrade: bool = True) -> SkelConvertOptions:
        """Return the options of the external Spine converter.

        Args:
            upgrade: True for the conversion to the game's Spine version, False for the downgrade to an older one.
        """
        if upgrade:
            return SkelConvertOptions(
                enabled=self.get("enable_spine_conversion"),
                converter_path=Path(self.get("spine_converter_path")),
                target_version=self.get("target_spine_version"),
            )
        return SkelConvertOptions(
            enabled=self.get("enable_spine_downgrade"),
            converter_path=Path(self.get("spine_converter_path")),
            target_version=str(self.get("spine_downgrade_version")).strip(),
        )

    def anim_check_options(self) -> AnimCheckOptions:
        """Return the animation check options, with the Spine viewer backend when its program exists."""
        path = self._viewer_path()
        backend = get_spine_backend("spine_viewer", path) if path is not None and path.is_file() else None
        return AnimCheckOptions(enabled=self.get("check_animations"), backend=backend)

    def anim_check_key(self) -> tuple[bool, str]:
        """Return the animation check settings as plain values, for keys that must stay equal between calls."""
        path = self._viewer_path()
        return bool(self.get("check_animations")), str(path) if path is not None else ""

    def _viewer_path(self) -> Path | None:
        path = str(self.get("spine_viewer_path")).strip()
        return Path(path) if path else None

    def output_subdir(self, name: str) -> Path:
        """Return a folder of the output folder, created when missing.

        Args:
            name: The subfolder's name, a fixed name chosen by the caller.

        Raises:
            ServiceError: ``dev_path_outside`` in dev mode, for a folder outside the dev roots.
        """
        path = Path(self.get("output_dir")) / name
        devmode.check_read(path)
        path.mkdir(parents=True, exist_ok=True)
        return path


def user_cache_dir() -> Path:
    """Return where light game UI assets may be cached after the user agreed (never next to the program).

    With ``BAMT_DATA_DIR`` (devmode) the cache is ``<BAMT_DATA_DIR>/cache``, never the released app's.
    """
    return default_cache_dir(utils.DATA_DIR_OVERRIDE, utils.EXE_DIR)


def managed_cache_dir(parent: str | None = None) -> Path:
    """Return the cache folder under the parent chosen in Settings, shared by the App and the CLI.

    The cache under the old folder name there is removed.

    Args:
        parent: The chosen parent folder; empty or None uses ``user_cache_dir``.

    Raises:
        ServiceError: ``dev_path_outside`` in dev mode, for a parent outside the dev roots.
    """
    if not parent:
        return user_cache_dir()
    chosen = Path(parent)
    devmode.check_read(chosen)
    return migrate_cache_dir(chosen / OLD_APP_DIR_NAME / "cache", chosen / APP_DIR_NAME / "cache")
