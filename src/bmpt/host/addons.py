"""Locate shipped Addons and prepare the App's writable Classic copies."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

from bmpt.engine import utils

from .upgrade_files import replace_path

CHARACTER_MAP = "BA-Characters-Internal-ID.csv"
logger = logging.getLogger(__name__)


def bundled_addons() -> Path:
    """Find the shipped files in a macOS bundle or a Windows extraction tree."""
    if sys.platform == "darwin":
        return Path(sys.executable).parent / "Addons"
    return utils.PACKAGE_DIR.parent / "Addons"


def prepare_classic_addons(data: Path, artifacts: Path) -> None:
    """Seed writable Addons once, preferring files supplied by the user."""
    target = artifacts / "Addons"
    if target.exists():
        return
    for source in (data / "Addons", bundled_addons()):
        if not source.is_dir():
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=".addons-", dir=artifacts) as scratch:
                staged = Path(scratch) / "Addons"
                shutil.copytree(source, staged, ignore=shutil.ignore_patterns("adb_cache"))
                if not target.exists():
                    replace_path(staged, target)
            return
        except OSError as error:
            logger.warning("Could not prepare Classic Addons: %s", error)


def character_map_path(configured: Path, data: Path) -> Path:
    """Resolve the default character map to packaged data when the local file is absent."""
    if configured == data / "Addons" / CHARACTER_MAP and not configured.is_file():
        bundled = bundled_addons() / CHARACTER_MAP
        if bundled.is_file():
            return bundled
    return configured
