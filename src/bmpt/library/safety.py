"""Checks made immediately before modifying game bundle files.

- The game must not run: ``game_processes`` lists its processes (a Toolhelp snapshot on Windows, ``ps`` elsewhere).
- Steam must not be updating or verifying the install: ``check_steam_idle`` reads ``StateFlags`` from the Steam
  library's app manifest. A folder outside a Steam library is not checked.
- Every destination must be inside the resource folders and lockable. A refusal is named by its cause
  (``write_refusal``): in use, denied, App Management on macOS, disk full, missing, or another failure.

The JP server's launcher, which may update or repair files on its own, is not matched: its process name is not
known yet.

None of these read game files: the process list and Steam's manifests are not game files.
"""

from __future__ import annotations

import csv
import errno
import functools
import io
import logging
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from .. import devmode, game_paths
from ..errors import ServiceError
from ..filelock import IN_USE_WAIT
from ..logging_utils import log_failure

logger = logging.getLogger(__name__)

# File names of the game's executable (native builds, the Windows build, and Linux's 15-character comm)
GAME_NAMES = frozenset({"bluearchive", "blue archive", "bluearchive.exe", "blue archive.exe"})

# Wine and CrossOver: the process is the preloader, and the Windows path of the game's executable is in its arguments
_WINE_GAME = re.compile(r"""(?:^|[\\/ "'])(?:bluearchive|blue archive)\.exe(?=[\s"']|$)""", re.IGNORECASE)

# Without it the windowed build opens a console window at every check
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

PROCESS_TIMEOUT = 5

_WINDOWS = os.name == "nt"

# Bits of an app manifest's StateFlags while Steam changes the install's files
STEAM_BUSY_FLAGS = {
    256: "update running",
    1024: "update started",
    65536: "reconfiguring",
    131072: "validating",
    262144: "adding files",
    524288: "preallocating",
    1048576: "downloading",
    2097152: "staging",
    4194304: "committing",
    8388608: "update stopping",
}
STEAM_BUSY_MASK = sum(STEAM_BUSY_FLAGS)  # distinct bits


# --- game processes ---

def is_game_name(path: str) -> bool:
    """Whether an executable path or name (``ps -o comm``, a Toolhelp exe name) is the game's."""
    return path.strip().replace("\\", "/").rsplit("/", 1)[-1].casefold() in GAME_NAMES


def is_game_command(line: str) -> bool:
    """Whether a command line (``ps -o args``) runs the game's Windows executable, as under Wine or CrossOver."""
    return _WINE_GAME.search(line) is not None


def game_processes() -> list[str]:
    """The game's running processes as the system lists them: empty while the game is not running.

    Windows: exe names from a Toolhelp snapshot, ``tasklist`` as the fallback. Elsewhere: executable paths
    (``ps -o comm``), then, with none of the game's, command lines (``ps -o args``) for Wine and CrossOver.
    This process and its parent are never listed, whatever their command lines hold.
    Raises ``ServiceError("game_status_unavailable")`` when the process list cannot be read.
    """
    own = {os.getpid(), os.getppid()}
    try:
        if _WINDOWS:
            return [name for pid, name in _windows_processes() if pid not in own and is_game_name(name)]
        found = [text for pid, text in _ps("comm") if pid not in own and is_game_name(text)]
        return found or [text for pid, text in _ps("args") if pid not in own and is_game_command(text)]
    except (OSError, subprocess.SubprocessError) as error:
        log_failure(logger, logging.DEBUG, "Process list unavailable", error, code="game_status_unavailable")
        raise ServiceError("game_status_unavailable") from error


def check_game_not_running() -> None:
    """Refuse replacement while the Blue Archive game process is running."""
    if game_processes():
        raise ServiceError("game_running")


def _ps(column: str) -> list[tuple[int | None, str]]:
    """Every process's id and one ``ps`` column; ``-ww`` keeps paths with spaces and long command lines whole."""
    result = subprocess.run(["ps", "-A", "-ww", "-o", f"pid=,{column}="], capture_output=True, check=True,
                            encoding="utf-8", errors="replace", timeout=PROCESS_TIMEOUT)
    rows: list[tuple[int | None, str]] = []
    for line in result.stdout.splitlines():
        pid, _, text = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), text.strip()))
    return rows


def _windows_processes() -> list[tuple[int | None, str]]:
    try:
        return _toolhelp_processes()
    except OSError as error:
        log_failure(logger, logging.INFO, "Process snapshot unavailable, using tasklist", error)
        return _tasklist_processes()


def _tasklist_processes() -> list[tuple[int | None, str]]:
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True,
                            timeout=PROCESS_TIMEOUT, creationflags=CREATE_NO_WINDOW)
    rows: list[tuple[int | None, str]] = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            rows.append((int(row[1]) if len(row) > 1 and row[1].isdigit() else None, row[0]))
    return rows


@functools.cache
def _toolhelp():
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for function in (kernel32.Process32FirstW, kernel32.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return ctypes, kernel32, ProcessEntry


def _toolhelp_processes() -> list[tuple[int | None, str]]:
    """Every process's id and exe name from a Toolhelp snapshot (ctypes, no subprocess, a few milliseconds)."""
    ctypes, kernel32, ProcessEntry = _toolhelp()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        raise ctypes.WinError(ctypes.get_last_error())
    rows: list[tuple[int | None, str]] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            rows.append((int(entry.th32ProcessID), entry.szExeFile))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


# --- Steam ---

def steam_state_flags(game_folder: Path | str) -> int | None:
    """``StateFlags`` of the app manifest of the Steam library holding a game folder; ``None`` outside a Steam
    library or when the manifest cannot be read."""
    manifest = game_paths.steam_app_manifest(Path(game_folder))
    if manifest is None:
        return None
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        log_failure(logger, logging.INFO, "Steam manifest unreadable", error)
        return None
    values = game_paths.vdf_values(text, "StateFlags")
    try:
        return int(values[0]) if values else None
    except ValueError:
        return None


def check_steam_idle(game_folder: Path | str) -> None:
    """Refuse replacement while Steam updates, verifies or otherwise changes the install's files.

    The game folder may be the install root or a resource folder below it. No manifest means no check.
    """
    flags = steam_state_flags(game_folder)
    if flags is not None and flags & STEAM_BUSY_MASK:
        error = ServiceError("steam_busy")
        log_failure(logger, logging.INFO, f"Steam busy: StateFlags {flags}", error)
        raise error


# --- destinations ---

def check_resource_path(path: Path, resource_dirs: list[Path]) -> Path:
    """Resolve a game destination and reject symlinks that escape resource folders."""
    resolved = path.resolve()
    if not any(resolved.is_relative_to(directory.resolve()) for directory in resource_dirs):
        raise ServiceError("game_file_outside_resources", file=path)
    return resolved


def _sandboxed(paths: list[Path]) -> bool:
    """Every path in a dev sandbox (devmode): never the folder the game runs from."""
    return bool(paths) and all(devmode.in_writable(path) for path in paths)


def check_game_for(paths: list[Path]) -> None:
    """game_running for writes to ``paths`` (files or the resource folders holding them), as ``writable_game_files``
    checks it: skipped when every path lies in a dev sandbox. In dev mode a path outside the writable folders is
    left to the write-time check, which refuses it by the file's name."""
    if not all(devmode.allows_write(path) for path in paths):
        return
    if not _sandboxed(paths):
        check_game_not_running()


@contextmanager
def writable_game_files(paths: list[Path], resource_dirs: list[Path], *,
                        game_folder: Path | None = None, allow_missing: bool = False) -> Iterator[None]:
    """Preflight destinations and hold POSIX advisory locks through replacement.

    With ``game_folder`` (the install the files belong to), Steam busy with that install refuses too.
    In dev mode (devmode) every destination must lie inside ``BAMT_DEV_WRITABLE``, and the game process and Steam
    checks are skipped there: a sandbox is never the folder the game runs from.
    """
    for path in paths:
        devmode.check_write(path)
    if not _sandboxed(paths):
        check_game_not_running()
        if game_folder is not None:
            check_steam_idle(game_folder)
    with ExitStack() as stack:
        for path in sorted(set(paths)):
            check_resource_path(path, resource_dirs)
            step = "stat"  # what failed, for the log: the user only sees the reason write_refusal gives
            try:
                if allow_missing and not path.exists():
                    if path.parent.stat().st_mode & 0o222:
                        continue  # a journaled rename may have left this destination absent
                    raise PermissionError(errno.EACCES, "read-only folder")
                if not path.is_file():
                    raise FileNotFoundError(errno.ENOENT, "not a file")
                if not path.stat().st_mode & 0o222:
                    raise PermissionError(errno.EACCES, "read-only file")
                if not path.parent.stat().st_mode & 0o222:
                    raise PermissionError(errno.EACCES, "read-only folder")
                step = "open"
                if os.name == "nt":
                    # Python's Windows file handles do not share delete access;
                    # keeping our probe open would block our own atomic replace.
                    _probe_when_free(path)
                else:
                    import fcntl

                    stream = stack.enter_context(path.open("r+b"))
                    step = "lock"
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                log_failure(logger, logging.WARNING, f"Game file not writable before replacement ({step})", error)
                raise ServiceError(write_refusal(error, path), file=path) from error
        yield


def _probe_when_free(path: Path) -> None:
    """Open a destination for writing on Windows, waiting a moment while another program (a virus scanner reading
    a large bundle) holds it."""
    deadline = time.monotonic() + IN_USE_WAIT
    while True:
        try:
            with path.open("r+b"):
                return
        except OSError as error:
            if getattr(error, "winerror", None) not in _WIN_IN_USE or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


# --- why a write was refused ---

# Windows system error codes (OSError.winerror); Python maps several of them to EACCES, so they are read first
_WIN_IN_USE = {32, 33}  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_WIN_ACCESS_DENIED = 5
_WIN_DENIED = {_WIN_ACCESS_DENIED, 19}  # ERROR_ACCESS_DENIED, ERROR_WRITE_PROTECT
_WIN_DISK_FULL = {39, 112}  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_WIN_NOT_FOUND = {2, 3}  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND

_IN_USE = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EBUSY, getattr(errno, "ETXTBSY", None)} - {None}
_DISK_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", None)} - {None}
_DENIED = {errno.EACCES, errno.EPERM, errno.EROFS}


def _in_app_bundle(path: str | os.PathLike[str] | None) -> bool:
    return path is not None and any(part.casefold().endswith(".app") for part in Path(path).parts)


def write_refusal(error: OSError, path: str | os.PathLike[str] | None = None) -> str:
    """The error name for a game file the system refused to write, by the cause behind ``error``.

    ``path`` is the destination; the error's own file name is used without one. The game process check has passed by
    then, so only a sharing or lock conflict is a file in use (game_file_locked).
    """
    path = path if path is not None else error.filename
    winerror = getattr(error, "winerror", None)
    if winerror in _WIN_IN_USE:
        return "game_file_locked"
    if winerror in _WIN_DISK_FULL:
        return "game_disk_full"
    if winerror in _WIN_NOT_FOUND:
        return "game_file_not_found"
    if winerror in _WIN_DENIED:
        # Replacing a file another program holds open fails with access denied rather than a sharing violation;
        # an os.replace error names two files, and the preflight had opened the destination for writing.
        if winerror == _WIN_ACCESS_DENIED and error.filename2 is not None:
            return "game_file_locked"
        return "game_file_denied"
    number = error.errno
    if number in _IN_USE or isinstance(error, BlockingIOError):
        return "game_file_locked"
    if number in _DISK_FULL:
        return "game_disk_full"
    if number == errno.ENOENT or isinstance(error, FileNotFoundError):
        return "game_file_not_found"
    if number == errno.EPERM and sys.platform == "darwin" and _in_app_bundle(path):
        return "game_app_denied"  # App Management: macOS keeps other apps from changing an app bundle's contents
    if number in _DENIED or isinstance(error, PermissionError):
        return "game_file_denied"
    return "game_file_write_failed"
