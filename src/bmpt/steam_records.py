"""Steam's own records of an install, read-only and on this device: which build is installed and the size and SHA-1 of
every file of it, from the depot manifest Steam keeps in its ``depotcache`` folder.

The game's bundles name their CRC32, which a mod can match; Steam's file list says what the official build holds. The
callers ask only with the user's consent to read Steam's records (the Check Game Files consent). Nothing here writes,
opens the network or reads a game file.
"""

from __future__ import annotations

import logging
import re
import struct
import sys
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import devmode, game_paths

logger = logging.getLogger(__name__)

#: The Global client's Windows content depot (the macOS install runs the Windows client under Wine)
DEPOT_GLOBAL_WINDOWS = 3557621
#: The folders below the install whose bundles the library judges
BUNDLE_FOLDERS = ("BlueArchive_Data/StreamingAssets/PUB/Resource/GameData/Windows/",
                  "BlueArchive_Data/StreamingAssets/PUB/Resource/Preload/Windows/")
#: A depot manifest larger than this is not read (the Global build's is about 9 MB)
MAX_MANIFEST = 256 * 1024 * 1024

_MAGIC_PAYLOAD, _MAGIC_METADATA, _MAGIC_SIGNATURE, _MAGIC_END = 0x71F617D0, 0x1F4812BE, 0x1B81B817, 0x32C415AB
_FLAG_DIRECTORY, _FLAG_SYMLINK = 0x40, 0x200
_EMPTY_SHA1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"


class ManifestError(ValueError):
    """A depot manifest or app manifest that cannot be read."""


@dataclass(frozen=True)
class OfficialFile:
    size: int
    sha1: str


# --- Valve KeyValues text (appmanifest_*.acf) ---

_TOKEN = re.compile(r'\s*(?://[^\n]*\n?\s*)*(?:"((?:[^"\\]|\\.)*)"|([{}])|([^\s{}"]+))', re.S)


def parse_keyvalues(text: str) -> dict:
    """KeyValues text as nested dicts with lower-case keys (the last of a repeated key wins)."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            if not text[pos:].strip():
                break
            raise ManifestError("unreadable KeyValues text")
        pos = match.end()
        quoted, brace, bare = match.groups()
        if quoted is not None:
            tokens.append(("s", re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), quoted)))
        elif brace is not None:
            tokens.append((brace, brace))
        elif bare is not None:
            tokens.append(("s", bare))
    root: dict = {}
    stack = [root]
    index = 0
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "}":
            if len(stack) == 1:
                raise ManifestError("unbalanced KeyValues text")
            stack.pop()
            index += 1
            continue
        if kind != "s" or index + 1 >= len(tokens):
            raise ManifestError("malformed KeyValues text")
        next_kind, next_value = tokens[index + 1]
        if next_kind == "{":
            child: dict = {}
            stack[-1][value.lower()] = child
            stack.append(child)
        elif next_kind == "s":
            stack[-1][value.lower()] = next_value
        else:
            raise ManifestError("malformed KeyValues text")
        index += 2
    if len(stack) != 1:
        raise ManifestError("unbalanced KeyValues text")
    return root


def installed_manifest(appmanifest: str, depot: int = DEPOT_GLOBAL_WINDOWS) -> int | None:
    """The manifest id Steam installed for ``depot``, from an app manifest's text; None when it lists none."""
    state = parse_keyvalues(appmanifest).get("appstate", {})
    depots = state.get("installeddepots", {}) if isinstance(state, dict) else {}
    entry = depots.get(str(depot)) if isinstance(depots, dict) else None
    value = entry.get("manifest") if isinstance(entry, dict) else None
    return int(value) if isinstance(value, str) and value.isdigit() else None


# --- depot manifests (Steam's ContentManifest) ---

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    for _ in range(10):
        if pos >= len(buf):
            raise ManifestError("truncated varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise ManifestError("varint too long")


def _fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(field number, wire type, value) of a protobuf message: ints for varints, bytes otherwise."""
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ManifestError("unsupported protobuf wire type")
        if pos > len(buf):
            raise ManifestError("truncated protobuf field")
        yield number, wire, value


def _sections(data: bytes) -> dict[int, bytes]:
    sections: dict[int, bytes] = {}
    pos = 0
    while True:
        if pos + 4 > len(data):
            raise ManifestError("depot manifest without its end marker")
        magic = struct.unpack_from("<I", data, pos)[0]
        if magic == _MAGIC_END:
            return sections
        if magic not in (_MAGIC_PAYLOAD, _MAGIC_METADATA, _MAGIC_SIGNATURE) or pos + 8 > len(data):
            raise ManifestError("unknown or truncated depot manifest section")
        length = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise ManifestError("truncated depot manifest section")
        sections[magic] = body
        pos += 8 + length


def parse_depot_manifest(data: bytes) -> tuple[int, int, dict[str, OfficialFile]]:
    """(depot, manifest id, {path below the install: size and SHA-1}) of a depot manifest; the payload's checksum is
    verified. Directories and links are left out."""
    sections = _sections(data)
    if _MAGIC_PAYLOAD not in sections or _MAGIC_METADATA not in sections:
        raise ManifestError("depot manifest without payload or metadata")
    meta = {number: value for number, _wire, value in _fields(sections[_MAGIC_METADATA])}
    payload = sections[_MAGIC_PAYLOAD]
    if int(meta.get(9, 0)) != zlib.crc32(struct.pack("<I", len(payload)) + payload):
        raise ManifestError("depot manifest checksum mismatch")
    if meta.get(4):
        raise ManifestError("depot manifest with encrypted file names")
    files: dict[str, OfficialFile] = {}
    for number, wire, value in _fields(payload):
        if number != 1 or wire != 2:
            continue
        name, size, flags, sha = b"", 0, 0, b""
        for sub, _wire, item in _fields(value):
            if sub == 1:
                name = item
            elif sub == 2:
                size = int(item)
            elif sub == 3:
                flags = int(item)
            elif sub == 5:
                sha = item
        if flags & (_FLAG_DIRECTORY | _FLAG_SYMLINK):
            continue
        try:
            path = name.decode("utf-8").rstrip("\0").replace("\\", "/").strip("/")
        except UnicodeDecodeError as error:
            raise ManifestError("file name not UTF-8") from error
        if not size:
            files[path] = OfficialFile(0, _EMPTY_SHA1)
        elif len(sha) == 20:
            files[path] = OfficialFile(size, sha.hex())
        else:
            raise ManifestError("file without a content SHA-1")
    return int(meta.get(1, 0)), int(meta.get(2, 0)), files


# --- where Steam keeps them ---

def _steam_roots(library: Path) -> Iterator[Path]:
    """The Steam client folders that may hold the depot cache for a library: the library itself (the client's own
    library), the Windows client inside the same Wine prefix, the Windows client's registered folder, the native
    client's folders."""
    yield library
    for ancestor in library.parents:
        if (ancestor / "drive_c").is_dir():
            for folder in game_paths.WINE_STEAM_DIRS:
                if host := game_paths.wine_to_host(ancestor, folder):
                    yield host
            break
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                yield Path(winreg.QueryValueEx(key, "SteamPath")[0])
        except OSError:
            pass
    yield from game_paths._native_steam_dirs()


def depot_manifest_path(game_folder: Path, depot: int = DEPOT_GLOBAL_WINDOWS) -> Path | None:
    """The depot manifest Steam keeps for the build installed at ``game_folder`` (the install root or a folder below
    it); None outside a Steam library, or when Steam lists no such depot or keeps no manifest of it."""
    app = game_paths.steam_app_manifest(Path(game_folder))
    if app is None or not devmode.allows_read(app):
        return None
    try:
        gid = installed_manifest(app.read_text(encoding="utf-8", errors="replace"), depot)
    except (OSError, ManifestError):
        logger.info("Steam app manifest unreadable", exc_info=True)
        return None
    if gid is None:
        return None
    library = app.parent.parent
    for root in dict.fromkeys(_steam_roots(library)):
        candidate = root / "depotcache" / f"{depot}_{gid}.manifest"
        if devmode.allows_read(candidate) and candidate.is_file():
            return candidate
    return None


class OfficialFiles:
    """Steam's file list of each install, by the bundle file names of its resource folders, read when asked and kept
    while Steam's files are unchanged (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[Path, tuple[tuple, dict[str, OfficialFile]]] = {}

    def bundles(self, game_folder: Path) -> dict[str, OfficialFile]:
        """{bundle file name: official size and SHA-1} for the install at ``game_folder``; empty when Steam keeps no
        readable file list of it."""
        manifest = depot_manifest_path(Path(game_folder))
        if manifest is None:
            return {}
        try:
            stat = manifest.stat()
        except OSError:
            return {}
        signature = (stat.st_size, stat.st_mtime_ns)
        with self._lock:
            cached = self._cache.get(manifest)
            if cached and cached[0] == signature:
                return cached[1]
        if stat.st_size > MAX_MANIFEST:
            return {}
        try:
            _depot, _gid, files = parse_depot_manifest(manifest.read_bytes())
        except (OSError, ManifestError):
            logger.warning("Steam's file list of %s is unreadable", manifest.name, exc_info=True)
            files = {}
        bundles: dict[str, OfficialFile] = {}
        for path, entry in files.items():
            for prefix in BUNDLE_FOLDERS:
                if path.startswith(prefix) and "/" not in path[len(prefix):]:
                    bundles[path[len(prefix):]] = entry
        with self._lock:
            self._cache[manifest] = (signature, bundles)
        return bundles
