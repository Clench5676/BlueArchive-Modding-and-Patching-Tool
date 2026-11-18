"""One web UI instance per data directory (see docs/webui/desktop.md, "One instance per data directory").

The running instance holds an OS lock on ``<data dir>/instance.lock`` for its lifetime and names itself in
``<data dir>/instance.json`` (``{pid, port, raise_token}``). A second start finds the lock held, posts
``/api/instance/raise`` with that token (header ``X-BAMT-Raise``), so the first instance brings its window to the
front, and exits. Two instances on one data directory would share its ``webui.json``, its library and its classic
window file; the lock is released by the system when the process ends, however it ends.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCK = "instance.lock"
INFO = "instance.json"
RAISE_PATH = "/api/instance/raise"
RAISE_HEADER = "X-BAMT-Raise"
RAISE_TIMEOUT = 5.0


class InstanceLock:
    """The data directory's instance lock.

    ``acquire()`` takes it without waiting, ``publish(port, token)`` names the instance once the server listens,
    ``release()`` lets go at exit.
    """

    def __init__(self, data_dir: Path, *, runtime: Path | None = None):
        """Point the lock at a data directory; nothing is locked until ``acquire``.

        Args:
            data_dir: Directory holding the interoperable instance lock.
            runtime: Directory holding the current handshake, when separate from the data directory.
        """
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / LOCK
        self.info = (runtime or self.data_dir) / INFO
        self._handle: Any = None

    @property
    def held(self) -> bool:
        """Whether this process holds the lock; False after an unlocked start."""
        return self._handle is not None

    def acquire(self, *, strict: bool = False) -> bool:
        """Take the lock without waiting.

        Without a lock file (an unwritable folder) the start goes ahead unlocked, as the other shared files of the
        data directory do.

        Args:
            strict: Refuse startup if the lock file cannot be opened.

        Returns:
            False while another process holds the lock; True otherwise.
        """
        if self._handle is not None:
            return True
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")  # noqa: SIM115 - held for the process lifetime
        except OSError:
            if strict:
                raise
            logger.warning("cannot open %s, starting without the instance lock", self.path, exc_info=True)
            return True
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def publish(self, port: int, token: str) -> None:
        """Name this instance in instance.json, for a second start to find.

        Args:
            port: The port the server listens on.
            token: The secret a second start sends in ``X-BAMT-Raise`` to be believed.
        """
        tmp = self.info.with_name(f".{self.info.name}.{os.getpid()}.tmp")
        try:
            self.info.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"pid": os.getpid(), "port": int(port), "raise_token": token}), encoding="utf-8")
            os.replace(tmp, self.info)
        except OSError:
            logger.warning("cannot write %s", self.info, exc_info=True)
            tmp.unlink(missing_ok=True)

    def release(self) -> None:
        """Remove instance.json (while it names this process) and let the lock go."""
        if self._handle is None:
            return
        self.clear()
        handle, self._handle = self._handle, None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def clear(self) -> None:
        """Remove this process's handshake while the coordinator still holds the instance lease."""
        info = read_info(self.info.parent)
        if info is not None and info.get("pid") == os.getpid():
            try:
                self.info.unlink(missing_ok=True)
            except OSError:
                logger.debug("cannot remove %s", self.info, exc_info=True)


def read_info(data_dir: Path) -> dict[str, Any] | None:
    """Read what instance.json says about the running instance.

    Args:
        data_dir: The data directory.

    Returns:
        ``{pid, port, raise_token}``; None when the file is missing, unreadable or its port or token is invalid.
    """
    try:
        data = json.loads((Path(data_dir) / INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port, token = data.get("port"), data.get("raise_token")
    if not (
        isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 and isinstance(token, str) and token
    ):
        return None
    return data


def raise_running(data_dir: Path, *, opener: Any = None, timeout: float = RAISE_TIMEOUT) -> bool:
    """Ask the instance running on this data directory to bring its window to the front.

    The request never goes through a proxy: the instance listens on 127.0.0.1.

    Args:
        data_dir: The data directory.
        opener: A ``urllib`` opener to send the request with; None builds one without proxies.
        timeout: Seconds to wait for the answer.

    Returns:
        Whether the instance answered with a success status.
    """
    info = read_info(data_dir)
    if info is None:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}{RAISE_PATH}",
        data=b"{}",
        method="POST",
        headers={RAISE_HEADER: info["raise_token"], "Content-Type": "application/json"},
    )
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except (OSError, urllib.error.URLError, ValueError):
        logger.info("the running instance did not answer", exc_info=True)
        return False
