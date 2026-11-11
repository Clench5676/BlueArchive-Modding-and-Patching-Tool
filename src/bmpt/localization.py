"""Register App translations for the engine's messages; keep package initialization empty."""

from pathlib import Path

from bmpt.engine import register_locale_dir

LOCALES = Path(__file__).resolve().parent / "locales"


def register_locales() -> None:
    register_locale_dir(LOCALES)
