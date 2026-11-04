"""Register App translations for the engine's messages; keep package initialization empty."""

from pathlib import Path

from ba_modding_toolkit.i18n import register_locale_dir

LOCALES = Path(__file__).resolve().parent / "locales"


def register_locales() -> None:
    register_locale_dir(LOCALES)
