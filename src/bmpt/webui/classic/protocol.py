"""The control protocol between the web UI and the classic child: environment, file names and control lines."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Iterable, Iterator

if TYPE_CHECKING:
    from .placement import Rect


logger = logging.getLogger(__package__)


ENV_URL = "BAMT_CLASSIC_URL"  # the web UI's origin, http://127.0.0.1:<port>
ENV_TOKEN = "BAMT_CLASSIC_TOKEN"  # one-time, valid only for /api/classic/*
TOKEN_HEADER = "X-BAMT-Classic"
CONTROL_PATH = "/api/classic/control"
REPORT_PATH = "/api/classic/report"

CONTROL_POLL = 25.0  # seconds a control long-poll waits for a line
READY_TIMEOUT = 30.0  # a cold onefile start (unpacking, a virus scan) can take more than 15 s on Windows
SILENT_EXIT = 60.0  # a child whose launcher returned early: no poll for this long counts as its exit
SHOW_FALLBACK = 5.0  # the child shows itself when no raise came this long after ready
CLASSIC_SIZE = (800, 1000)  # gui/app.py setup_main_window, logical pixels
MAC_TITLE_BAR = 28  # points above the content of a titled NSWindow
MAX_REPORT = 64 * 1024

CLASSIC_JSON = "classic.json"
CLASSIC_LOG = "classic.log"

_TAB_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


def tab_line(tab: str) -> str:
    """Return the control line that shows a tab.

    Args:
        tab: A classic tab name, or ``"settings"`` for the classic settings.
    """
    return "settings" if tab == "settings" else f"tab {tab}"


def place_line(rect: "Rect") -> str:
    """Return the control line that places the child's window.

    Args:
        rect: Where the window goes, in Tk's coordinates; the values are rounded.
    """
    r = rect.rounded()
    return f"place {int(r.x)} {int(r.y)} {int(r.w)} {int(r.h)}"


def parse_control(line: Any) -> tuple | None:
    """Parse a control line into a command.

    Args:
        line: One line the child received; anything but a string is junk.

    Returns:
        ``("tab", name)``, ``("settings",)``, ``("place", x, y, w, h)`` or ``("raise",)``; None for anything else,
        which the child ignores.
    """
    if not isinstance(line, str):
        return None
    parts = line.split()
    if parts == ["raise"]:
        return ("raise",)
    if parts == ["settings"]:
        return ("settings",)
    if len(parts) == 2 and parts[0] == "tab" and _TAB_NAME.fullmatch(parts[1]):
        return ("tab", parts[1])
    if len(parts) == 5 and parts[0] == "place":
        try:
            x, y, w, h = (int(p) for p in parts[1:])
        except ValueError:
            return None
        if w > 0 and h > 0:
            return ("place", x, y, w, h)
    return None


def iter_lines(lines: Iterable[Any]) -> Iterator[tuple]:
    """Parse a batch of control lines, leaving junk out.

    Args:
        lines: The lines received.

    Yields:
        The commands, as ``parse_control`` returns them.
    """
    for line in lines:
        cmd = parse_control(line)
        if cmd is None:
            logger.debug("ignoring control line %r", line)
            continue
        yield cmd
