"""Register App translations for the engine's messages; keep package initialization empty."""

from pathlib import Path

from bmpt.engine import register_locale_dir

LOCALES = Path(__file__).resolve().parents[1] / "locales"


def register_locales() -> None:
    """Add the App's locale folder to the engine's translation lookup."""
    register_locale_dir(LOCALES)
