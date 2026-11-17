"""The web UI's locale strings on the Python side.

They go to the page (``i18n.strings``) and name what the Python side words itself: the install labels of
``mods.state`` and the tray menu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bmpt.engine import fallback_language
from ..core.localization import LOCALES


def get_locale_dir() -> Path:
    """Return the folder of the locale JSON files."""
    return LOCALES


def ui_strings(lang: str, locale_dir: Path | None = None, *, fallback: bool = True) -> dict[str, str]:
    """Load the ``webui.*`` strings of one language, flattened to dotted keys.

    Args:
        lang: Language code of a locale file; a file that is missing or unreadable contributes nothing.
        locale_dir: Folder of the locale files; None uses the App's own.
        fallback: Start from the language's fallback language so that its strings fill what ``lang`` lacks; False
            returns only what ``lang`` itself has, as the locale completeness checks need.

    Returns:
        Dotted key (``webui.settings.title``) mapped to the string.
    """
    folder = locale_dir or get_locale_dir()
    out: dict[str, str] = {}
    codes = dict.fromkeys((fallback_language(lang), lang)) if fallback else (lang,)
    for code in codes:  # fallback first, the language itself wins
        path = folder / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")).get("webui", {})
        except (OSError, ValueError):
            continue
        _flatten(data, "webui", out)
    return out


def _flatten(node: Any, prefix: str, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(v, f"{prefix}.{k}", out)
    elif isinstance(node, str):
        out[prefix] = node


def fill(text: str, **values: Any) -> str:
    """Fill the ``{name}`` placeholders of a string, as the page's ``T`` does.

    Args:
        text: The string, with ``{name}`` placeholders; a placeholder without a value stays as it is.
        **values: Placeholder name mapped to its value, which is converted with ``str``.
    """
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def install_label(strings: dict[str, str], server: str, distinct: str | None) -> str:
    """Name an install by its server and, when needed, the folder that sets it apart.

    Args:
        strings: The ``webui.*`` strings of the UI language.
        server: The install's server key, such as ``global``.
        distinct: The folder name that tells this install from another install of the same server, or None when
            the server has one install; it gives labels such as "Global (BlueArchive)" (``mods.installNamed``).
    """
    name = strings.get(f"webui.settings.server.{server}", server)
    if not distinct:
        return name
    return fill(strings.get("webui.mods.installNamed", "{server} ({folder})"), server=name, folder=distinct)
