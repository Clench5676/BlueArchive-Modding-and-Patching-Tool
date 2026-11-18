"""Entry point of the app: the web UI, or the classic Tk GUI with ``--classic``, ``--tab <name>`` or ``--settings``."""

from __future__ import annotations

import sys

from .core.localization import register_locales

register_locales()


def main(argv: list[str] | None = None) -> None:
    """Start the web UI, or the classic GUI when the arguments ask for it.

    Args:
        argv: Command-line arguments without the program name; None reads ``sys.argv``. The classic GUI gets them
            without ``--classic``; the web UI gets them unchanged.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--classic" in args or "--tab" in args or "--settings" in args:
        _classic([a for a in args if a != "--classic"])
    else:
        from .webui.main import main as web

        web(args)


def _classic(args: list[str]) -> None:
    import json
    import os
    import urllib.error
    import urllib.parse
    import urllib.request
    from pathlib import Path

    from .engine import EXE_DIR
    from .host.addons import prepare_classic_addons
    from .host.data_upgrade import DataUpgrade
    from .webui.classic.protocol import ENV_TOKEN, ENV_URL, TOKEN_HEADER

    def start(binding: dict) -> None:
        from .engine import classic_main

        artifacts = Path(binding["artifacts"])
        artifacts.mkdir(parents=True, exist_ok=True)
        prepare_classic_addons(Path(EXE_DIR), artifacts)
        settings = {
            "BMPT_CLASSIC_CONFIG": binding["config"],
            "BMPT_CLASSIC_ARTIFACTS": str(artifacts),
            "BMPT_CLASSIC_RUNTIME": binding["runtime"],
            "BMPT_CLASSIC_READ_ONLY": "1" if binding["readOnly"] else "0",
            "BMPT_CLASSIC_PROTECTED": json.dumps(binding.get("protected", [])),
        }
        previous = {key: os.environ.get(key) for key in settings}
        os.environ.update(settings)
        try:
            classic_main(args)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    origin, token = os.environ.get(ENV_URL), os.environ.get(ENV_TOKEN)
    if "--control" in args and origin and token:
        address = urllib.parse.urlsplit(origin)
        if address.scheme == "http" and address.hostname == "127.0.0.1" and address.port and not address.path:
            request = urllib.request.Request(f"{origin}/api/classic/session", headers={TOKEN_HEADER: token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=5) as response:
                    binding = json.load(response)
            except (OSError, ValueError) as error:
                raise SystemExit("The Classic UI could not verify its data session") from error
            start(binding)
            return

    from .webui.main import open_classic_upgrade

    upgrade = DataUpgrade(Path(EXE_DIR))
    try:
        if open_classic_upgrade(upgrade):
            paths = upgrade.paths
            start(
                {
                    "config": str(paths.config),
                    "artifacts": str(paths.artifacts),
                    "runtime": str(paths.runtime),
                    "readOnly": upgrade.classic_read_only(),
                    "protected": upgrade.classic_protected_destinations(),
                }
            )
    finally:
        upgrade.close()


if __name__ == "__main__":
    main()
