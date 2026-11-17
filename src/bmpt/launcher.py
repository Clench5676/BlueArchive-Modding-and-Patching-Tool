"""Entry point of the app: the web UI, or the classic Tk GUI with ``--classic``, ``--tab <name>`` or ``--settings``."""

from __future__ import annotations

import sys

from .localization import register_locales

register_locales()


def main(argv: list[str] | None = None) -> None:
    """Start the web UI, or the classic GUI when the arguments ask for it.

    Args:
        argv: Command-line arguments without the program name; None reads ``sys.argv``. The classic GUI gets them
            without ``--classic``; the web UI gets them unchanged.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--classic" in args or "--tab" in args or "--settings" in args:
        from bmpt.engine import classic_main as classic

        classic([a for a in args if a != "--classic"])
    else:
        from .webui.main import main as web

        web(args)


if __name__ == "__main__":
    main()
