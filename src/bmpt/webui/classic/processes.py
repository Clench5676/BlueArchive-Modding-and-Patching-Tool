"""The classic window's process: how long a process has run, whether it is alive, and ``classic.json``."""

from __future__ import annotations

import calendar
import ctypes
import functools
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .protocol import CLASSIC_JSON


logger = logging.getLogger(__package__)


@dataclass(frozen=True)
class ClassicProcess:
    """A classic window as ``classic.json`` names it.

    ``created`` is the creation time the system reports, as an integer (Windows: FILETIME in 100 ns units; macOS:
    whole seconds since the epoch; Linux: clock ticks since boot).
    """

    pid: int
    exe: str
    hwnd: int
    created: int

    def to_json(self) -> dict[str, Any]:
        """Return the fields as ``classic.json`` stores them."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> "ClassicProcess | None":
        """Read the contents of ``classic.json``.

        Args:
            data: The parsed JSON.

        Returns:
            The process; None when ``data`` is not an object with a valid ``pid``, ``created``, ``hwnd`` and ``exe``.
        """
        if not isinstance(data, dict):
            return None
        pid, exe, hwnd, created = data.get("pid"), data.get("exe", ""), data.get("hwnd", 0), data.get("created")

        def is_int(v: Any) -> bool:
            return isinstance(v, int) and not isinstance(v, bool)

        if not (is_int(pid) and pid > 0 and is_int(created) and is_int(hwnd) and hwnd >= 0 and isinstance(exe, str)):
            return None
        return cls(pid=pid, exe=exe, hwnd=hwnd, created=created)


_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = {
    m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)
}


def parse_lstart(text: str) -> int | None:
    """Read the output of ``ps -o lstart=`` under ``LC_ALL=C TZ=UTC0`` as seconds since the epoch.

    The day and month names go through a fixed English table, never ``time.strptime``, which reads them in this
    process's ``LC_TIME``.

    Args:
        text: The output, such as "Mon Oct 19 10:23:45 2026".

    Returns:
        The seconds; None when ``text`` is not in that form.
    """
    parts = text.split()
    if len(parts) != 5 or parts[0] not in _DAYS or parts[1] not in _MONTHS:
        return None
    try:
        day, year = int(parts[2]), int(parts[4])
        hour, minute, second = (int(p) for p in parts[3].split(":"))
    except ValueError:
        return None
    return calendar.timegm((year, _MONTHS[parts[1]], day, hour, minute, second, 0, 0, 0))


def ps_env() -> dict[str, str]:
    """Return the environment ``ps`` runs in: C locale and UTC, whatever this process's own.

    Every instance then reads the same creation time (BSD ``ps`` prints ``lstart`` with ``%c`` in local time).
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["TZ"] = "UTC0"
    return env


def _run_ps(args: list[str], run: Callable[..., Any]) -> str | None:
    try:
        out = run(["ps", *args], capture_output=True, text=True, env=ps_env(), timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.debug("ps %s failed", args, exc_info=True)
        return None
    return out.stdout if out.returncode == 0 else None


def ps_created(pid: int, *, run: Callable[..., Any] = subprocess.run) -> int | None:
    """Read a process's start from ``ps``, in seconds since the epoch.

    Args:
        pid: The process id.
        run: Runs the command; tests replace ``subprocess.run``.

    Returns:
        The seconds; None when ``ps`` fails.
    """
    out = _run_ps(["-o", "lstart=", "-p", str(pid)], run)
    return parse_lstart(out) if out else None


class _BsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` (``sys/proc_info.h``)."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


PROC_PIDTBSDINFO = 3


@functools.cache
def _libproc_pidinfo() -> Callable[..., int]:
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")  # part of libSystem; dlopen finds it in the shared cache
    fn = lib.proc_pidinfo
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    return fn


def mac_created(
    pid: int, *, pidinfo: Callable[..., int] | None = None, run: Callable[..., Any] = subprocess.run
) -> int | None:
    """Read a process's start in whole seconds since the epoch on macOS.

    The source is ``proc_pidinfo``'s ``pbi_start_tvsec``, else ``ps`` in the C locale and UTC, which gives the same
    number.

    Args:
        pid: The process id.
        pidinfo: Replaces ``proc_pidinfo``; tests inject one.
        run: Runs ``ps``; tests replace ``subprocess.run``.

    Returns:
        The seconds; None when neither source answers.
    """
    try:
        fn = pidinfo or _libproc_pidinfo()
        info = _BsdInfo()
        size = ctypes.sizeof(info)
        if fn(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) == size:
            return int(info.pbi_start_tvsec)
    except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError):
        logger.debug("proc_pidinfo is unavailable, reading ps", exc_info=True)
    return ps_created(pid, run=run)


# Win32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
TH32CS_SNAPPROCESS = 0x2
ASFW_ANY = -1
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
SW_RESTORE = 9
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0


def _kernel32() -> Any:
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    return k32


def win_alive(pid: int) -> bool:
    """Return whether a process is running on Windows; one that refuses access to its handle counts as running."""
    from ctypes import wintypes

    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # type: ignore[attr-defined]
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def win_created(pid: int) -> int | None:
    """Read a process's creation time on Windows as a FILETIME (100 ns units); None when it cannot be read."""
    from ctypes import wintypes

    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def win_exe_name(pid: int) -> str | None:
    """Read a process's executable file name from a Toolhelp snapshot; None when the process is not listed."""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
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

    k32 = _kernel32()
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return entry.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return None
    finally:
        k32.CloseHandle(snap)


def _posix_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _linux_created(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat[stat.rindex(")") + 2 :].split()[19])  # field 22, starttime
    except (OSError, ValueError, IndexError):
        return None


def _linux_args(pid: int) -> list[str] | None:
    try:
        return [a.decode("utf-8", "replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return None


class Processes:
    """The process table as ``find_classic`` reads it; the tests give it a fake."""

    def own(self) -> set[int]:
        """This process and its parent (the onefile launcher of the Windows build, which runs the same exe)."""
        return {os.getpid(), os.getppid()}

    def alive(self, pid: int) -> bool:
        """Return whether a process with this id is running."""
        if pid <= 0:
            return False
        return win_alive(pid) if sys.platform == "win32" else _posix_alive(pid)

    def created(self, pid: int) -> int | None:
        """Return a process's creation time in the unit of ``ClassicProcess.created``; None when unreadable."""
        if sys.platform == "win32":
            return win_created(pid)
        if sys.platform == "darwin":
            return mac_created(pid)
        return _linux_created(pid)

    def runs_classic(self, pid: int) -> bool:
        """Whether the process runs this program's classic GUI."""
        if sys.platform == "win32":
            # the web UI and the classic child run the same exe name (own() rules out this process and its parent)
            name = win_exe_name(pid)
            ours = {Path(sys.executable).name.lower(), Path(sys.argv[0]).name.lower()}
            return bool(name) and name.lower() in ours
        if sys.platform == "darwin":
            out = _run_ps(["-ww", "-o", "args=", "-p", str(pid)], subprocess.run)
            args = out.split() if out else None
        else:
            args = _linux_args(pid)
        return bool(args) and "--classic" in args


SYSTEM_PROCESSES = Processes()


def classic_json(data_dir: Path) -> Path:
    """Return the path of ``classic.json`` in a data directory."""
    return Path(data_dir) / CLASSIC_JSON


def write_classic_json(data_dir: Path, proc: ClassicProcess) -> None:
    """Name a classic window in ``classic.json``, replacing the file in one step; a failure is logged.

    Args:
        data_dir: The data directory.
        proc: The window to name.
    """
    path = classic_json(data_dir)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(proc.to_json()), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("cannot write %s", path, exc_info=True)
        tmp.unlink(missing_ok=True)


def read_classic_json(data_dir: Path) -> tuple[str, ClassicProcess | None] | None:
    """Read ``classic.json``.

    Args:
        data_dir: The data directory.

    Returns:
        The raw text and the process it names (None when malformed); None without a readable file.
    """
    path = classic_json(data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("cannot read %s", path, exc_info=True)
        return None
    try:
        return raw, ClassicProcess.from_json(json.loads(raw))
    except ValueError:
        return raw, None


def remove_classic_json(data_dir: Path, pid: int | None = None) -> None:
    """Remove ``classic.json``.

    Args:
        data_dir: The data directory.
        pid: When given, the file is removed only while it names that process.
    """
    path = classic_json(data_dir)
    if pid is not None:
        found = read_classic_json(data_dir)
        if found is None or found[1] is None or found[1].pid != pid:
            return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("cannot remove %s", path, exc_info=True)


def find_classic(data_dir: Path, processes: Processes | None = None) -> ClassicProcess | None:
    """Find the classic window ``classic.json`` names, while it runs.

    The pid counts only when it is alive with the creation time the file records (a pid the system reused has a
    later one), runs this program's classic GUI, and is neither this process nor its parent. Otherwise the file is
    stale and removed, so it never blocks writes.

    Args:
        data_dir: The data directory.
        processes: The process table; None reads the system's.

    Returns:
        The window; None when there is no file or it is stale.
    """
    procs = processes or SYSTEM_PROCESSES
    found = read_classic_json(data_dir)
    if found is None:
        return None
    raw, proc = found
    if proc is None:
        why = "malformed"
    elif proc.pid in procs.own():
        why = "this process"
    elif not procs.alive(proc.pid):
        why = "not running"
    elif procs.created(proc.pid) != proc.created:
        why = "a reused pid"
    elif not procs.runs_classic(proc.pid):
        why = "another program"
    else:
        return proc
    path = classic_json(data_dir)
    logger.info("removing stale %s (%s)", path, why)
    again = read_classic_json(data_dir)
    if again is not None and again[0] == raw:  # not rewritten meanwhile
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("cannot remove %s", path, exc_info=True)
    return None
