"""Run the web UI with ``python -m bmpt.webui``."""

from ..localization import register_locales

register_locales()

from .main import main

if __name__ == "__main__":
    main()
