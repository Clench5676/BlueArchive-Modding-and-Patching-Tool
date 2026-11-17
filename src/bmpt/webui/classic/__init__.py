"""The classic Tk GUI as a window of the web UI (see docs/webui/desktop.md, "Classic window").

- ``ClassicWindow`` starts the classic GUI (``--classic --control``) in its own process, talks to it over the web UI's
  own HTTP server (``GET /api/classic/control``, ``POST /api/classic/report``, with a one-time token), places it over
  the BAMT window, raises it and publishes ``classic.opened``, ``classic.ready`` and ``classic.closed``.
- ``classic.json`` in the data directory names a running classic window, so an instance started later still sees it
  (``find_classic``): the window outlives the instance that started it.
- ``ClassicQueue`` holds the game-folder writes the user chose to apply once the classic window closes.

Standard library only, and nothing of Tk or pywebview at import: the classic child imports the control protocol from
``protocol`` (``gui/control.py``).
"""

from .native import NativeOps, native_ops
from .placement import Rect, centre_rect, cocoa_to_tk, mac_placement, screen_for, win_placement
from .processes import (
    PROC_PIDTBSDINFO,
    SYSTEM_PROCESSES,
    ClassicProcess,
    Processes,
    find_classic,
    mac_created,
    parse_lstart,
    read_classic_json,
    remove_classic_json,
    write_classic_json,
)
from .protocol import (
    CLASSIC_JSON,
    CLASSIC_LOG,
    CLASSIC_SIZE,
    CONTROL_PATH,
    ENV_TOKEN,
    ENV_URL,
    MAC_TITLE_BAR,
    REPORT_PATH,
    SHOW_FALLBACK,
    iter_lines,
    parse_control,
    tab_line,
)
from .queued import QUEUE_METHODS, ClassicQueue, QueuedCall, stat_files
from .window import ClassicWindow

__all__ = [
    "CLASSIC_JSON",
    "CLASSIC_LOG",
    "CLASSIC_SIZE",
    "CONTROL_PATH",
    "ClassicProcess",
    "ClassicQueue",
    "ClassicWindow",
    "ENV_TOKEN",
    "ENV_URL",
    "MAC_TITLE_BAR",
    "NativeOps",
    "PROC_PIDTBSDINFO",
    "Processes",
    "QUEUE_METHODS",
    "QueuedCall",
    "REPORT_PATH",
    "Rect",
    "SHOW_FALLBACK",
    "SYSTEM_PROCESSES",
    "centre_rect",
    "cocoa_to_tk",
    "find_classic",
    "iter_lines",
    "mac_created",
    "mac_placement",
    "native_ops",
    "parse_control",
    "parse_lstart",
    "read_classic_json",
    "remove_classic_json",
    "screen_for",
    "stat_files",
    "tab_line",
    "win_placement",
    "write_classic_json",
]
