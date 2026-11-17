"""Locate Blue Archive installations.

Windows: the uninstall entries in the registry, then the libraries of the Steam client.
Other platforms: the same lookups inside Wine prefixes (system.reg and the libraries of the
Windows Steam client), then the libraries of a native Steam client.
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

from . import devmode

logger = logging.getLogger(__name__)

GameRegion = Literal["global", "japan"]

STEAM_APP_ID = 3557620

# Uninstall entries under HKEY_LOCAL_MACHINE that hold "InstallLocation"; both registry views are tried
UNINSTALL_KEYS: dict[str, tuple[str, ...]] = {
    "global": (
        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {STEAM_APP_ID}",
        rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {STEAM_APP_ID}",
    ),
    "japan": (
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\e02a2fab-b426-5ce2-b9de-b9e7506c327e",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\e02a2fab-b426-5ce2-b9de-b9e7506c327e",
    ),
}

# Where the Windows Steam client installs itself inside a Wine prefix
WINE_STEAM_DIRS = (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam")

# Apple's data folders under ~/Library/Application Support (some are privacy-protected); never scanned
_SKIPPED_SUPPORT_DIRS = {
    "AddressBook",
    "CallHistoryDB",
    "CallHistoryTransactions",
    "CloudDocs",
    "FaceTime",
    "FileProvider",
    "Knowledge",
    "MobileSync",
}


def find_game_dirs(region: GameRegion) -> list[Path]:
    """Return the existing install directories found for a region, most likely first, without duplicates.

    When ``BAMT_GAME_DIRS_GLOBAL`` / ``BAMT_GAME_DIRS_JAPAN`` is set (devmode), the folders it names are returned and
    nothing is searched. Dev mode (``BAMT_DEV_ROOTS``) never searches and drops named folders outside the dev roots.
    Outside Windows the paths are returned with links resolved.

    Args:
        region: ``"global"`` (registry, Steam libraries) or ``"japan"`` (the publisher's launcher entry only).
    """
    named = devmode.game_dirs(region)
    if named is not None:
        allowed = []
        for path in named:
            if devmode.allows_read(path):
                allowed.append(path)
            else:
                logger.warning("Game folder outside the dev roots, ignored: %s", path)
        return _unique_dirs(allowed)
    if devmode.active():
        return []
    if sys.platform == "win32":
        return _unique_dirs(_windows_candidates(region))
    return _unique_dirs(_unix_candidates(region))


# --- Windows ---


def _windows_candidates(region: GameRegion) -> Iterator[Path]:
    import winreg

    def read(root, key_path: str, name: str) -> str | None:
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return value if isinstance(value, str) and value else None

    for key_path in UNINSTALL_KEYS[region]:
        if location := read(winreg.HKEY_LOCAL_MACHINE, key_path, "InstallLocation"):
            yield Path(location)
    if region == "global":
        for root, key_path, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            if steam_dir := read(root, key_path, name):
                yield from steam_app_dirs(Path(steam_dir), Path)


# --- macOS / Linux ---


def _unix_candidates(region: GameRegion) -> Iterator[Path]:
    for prefix in _wine_prefixes():
        if (prefix / "drive_c").is_dir():
            yield from wine_prefix_candidates(prefix, region)
    if region == "global":
        for steam_dir in _native_steam_dirs():
            yield from steam_app_dirs(steam_dir, Path)


def wine_prefix_candidates(prefix: Path, region: GameRegion) -> Iterator[Path]:
    """Yield the install directories recorded in one Wine prefix, registry first, then Steam libraries.

    The directories are host paths and may not exist.

    Args:
        prefix: The Wine prefix (the folder holding ``system.reg`` and ``drive_c``).
        region: ``"global"`` also searches the libraries of a Windows Steam client in the prefix.
    """

    def to_host(windows_path: str) -> Path | None:
        return wine_to_host(prefix, windows_path)

    try:
        registry = (prefix / "system.reg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        registry = ""
    for key_path in UNINSTALL_KEYS[region]:
        location = reg_value(registry, key_path, "InstallLocation")
        if location and (host_path := to_host(location)):
            yield host_path
    if region == "global":
        for steam_dir in WINE_STEAM_DIRS:
            if host_steam_dir := to_host(steam_dir):
                yield from steam_app_dirs(host_steam_dir, to_host)


def _wine_prefixes() -> Iterator[Path]:
    """Wine prefixes in the usual places."""
    home = Path.home()
    if env_prefix := os.environ.get("WINEPREFIX"):
        yield Path(env_prefix)
    yield home / ".wine"
    if sys.platform != "darwin":
        return
    yield from _subdirs(home / "Library/Application Support/CrossOver/Bottles")
    # Wrapper apps (Wineskin, Kegworks, Sikarugir...) keep their prefix inside the bundle
    for root, depth in (
        (Path("/Applications"), 2),
        (home / "Applications", 2),
        (home / "Library/Application Support", 3),
    ):
        for app in _find_app_bundles(root, depth):
            yield app / "Contents/SharedSupport/prefix"


def _native_steam_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    return [home / ".local/share/Steam", home / ".steam/steam"]


def wine_to_host(prefix: Path, windows_path: str) -> Path | None:
    """Map a Windows path inside a Wine prefix to the host path through the prefix's drive links.

    Args:
        prefix: The Wine prefix.
        windows_path: An absolute path with a drive letter, either separator.

    Returns:
        The host path (which may not exist), or None for a path without a drive letter.
    """
    match = re.match(r"([A-Za-z]):[\\/]*(.*)", windows_path.strip())
    if not match:
        return None
    drive_letter = match[1].lower()
    drive = prefix / "dosdevices" / f"{drive_letter}:"
    if drive_letter == "c" and not drive.exists():
        drive = prefix / "drive_c"
    return drive.joinpath(*[part for part in re.split(r"[\\/]+", match[2]) if part])


def reg_value(registry: str, key_path: str, name: str) -> str | None:
    r"""Read a string value from the text of a Wine registry file, unescaped.

    Args:
        registry: The file's text; ``system.reg`` holds HKEY_LOCAL_MACHINE.
        key_path: The key without its hive, as on Windows (``SOFTWARE\...``); matched ignoring case.
        name: The value's name, matched ignoring case.

    Returns:
        The value, or None when the key or a string value of that name is missing.
    """
    header = re.compile(r"^\[" + re.escape(key_path.replace("\\", "\\\\")) + r"\]", re.IGNORECASE | re.MULTILINE)
    key_match = header.search(registry)
    if not key_match:
        return None
    end = registry.find("\n[", key_match.end())
    section = registry[key_match.end() : end if end >= 0 else len(registry)]
    value_match = re.search(r'^"' + re.escape(name) + r'"="((?:[^"\\]|\\.)*)"', section, re.IGNORECASE | re.MULTILINE)
    return _unescape_reg(value_match[1]) if value_match else None


def _unescape_reg(value: str) -> str:
    r"""Undo the escaping of Wine registry strings (\\, \" and \x#### for non-ASCII characters)."""

    def replace(match: re.Match) -> str:
        escaped = match[1]
        if escaped[0] == "x" and len(escaped) > 1:
            return chr(int(escaped[1:], 16))
        return escaped

    return re.sub(r"\\(x[0-9a-fA-F]{1,4}|.)", replace, value)


# --- Steam ---


def steam_app_dirs(steam_dir: Path, to_host: Callable[[str], Path | None]) -> Iterator[Path]:
    """Yield the install directories of the game in every library of a Steam client.

    The directories are named by the app manifests and may not exist.

    Args:
        steam_dir: The Steam client's folder; it is a library itself.
        to_host: Maps a library path written in ``libraryfolders.vdf`` to a host path, or None to skip it.
    """
    libraries = [steam_dir]
    try:
        library_folders = (steam_dir / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        library_folders = ""
    libraries += [path for path in map(to_host, vdf_values(library_folders, "path")) if path]
    for library in libraries:
        try:
            manifest = (library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        for install_dir in vdf_values(manifest, "installdir")[:1]:
            yield library / "steamapps" / "common" / install_dir


def steam_app_manifest(game_folder: Path) -> Path | None:
    """Return the game's app manifest in the Steam library holding a game folder, or None outside a Steam library.

    The folder may be the install root (steamapps/common/<installdir>) or a resource folder below it: the walk goes
    up to an ancestor named "common" whose parent is "steamapps", first along the folder as given (a link to the
    install keeps its library), then along its resolved path.
    """
    walks = [game_folder]
    try:
        resolved = game_folder.resolve()
    except (OSError, RuntimeError):
        resolved = game_folder
    if resolved != game_folder:
        walks.append(resolved)
    for folder in walks:
        for ancestor in (folder, *folder.parents):
            if ancestor.name.casefold() == "common" and ancestor.parent.name.casefold() == "steamapps":
                manifest = ancestor.parent / f"appmanifest_{STEAM_APP_ID}.acf"
                if manifest.is_file():
                    return manifest
                break
    return None


def vdf_values(text: str, key: str) -> list[str]:
    """Return every value of a key in Valve KeyValues text (``libraryfolders.vdf``, ``appmanifest_*.acf``).

    The key matches at any depth, ignoring case; escaped backslashes are undone.
    """
    pattern = r'"' + re.escape(key) + r'"\s+"((?:[^"\\]|\\.)*)"'
    return [value.replace("\\\\", "\\") for value in re.findall(pattern, text, re.IGNORECASE)]


# --- helpers ---


def _unique_dirs(candidates: Iterable[Path]) -> list[Path]:
    """Existing directories in order, without duplicates (compared after resolving links).

    Outside Windows the resolved path is returned, so Wine drive links are not shown.
    """
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            if not path.is_dir():
                continue
            real = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(real))
        if key not in seen:
            seen.add(key)
            result.append(path if sys.platform == "win32" else real)
    return result


def _subdirs(root: Path) -> list[Path]:
    try:
        return sorted(entry for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return []


def _find_app_bundles(root: Path, depth: int) -> Iterator[Path]:
    """*.app bundles at most depth levels below root, without entering bundles, links or Apple's data folders."""
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith((".", "com.apple.")) or entry.name in _SKIPPED_SUPPORT_DIRS:
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if entry.name.endswith(".app"):
            yield Path(entry.path)
        elif depth > 1:
            yield from _find_app_bundles(Path(entry.path), depth - 1)
