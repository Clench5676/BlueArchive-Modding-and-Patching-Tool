"""The isolated process environment of the stored-data matrix's drivers."""

from __future__ import annotations

import os
from pathlib import Path

LOCAL_GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file"}


def case_environment(case: Path, cache: Path) -> dict:
    """Build the environment a driver runs in for one scratch case.

    Home, temporary and data folders point into the case, the developer's test settings are dropped, and uv and Git
    stay offline and local.

    Args:
        case: The scratch case folder; its home and temporary folders are created.
        cache: The offline uv cache.

    Returns:
        The environment variables for the driver's process.
    """
    profile, temp = case / "home", case / "tmp"
    for path in (profile, temp):
        path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTHONPATH", "BAMT_TEST_GAME_DIR", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    env.update(
        {
            **LOCAL_GIT,
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_CACHE_DIR": str(cache),
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "APPDATA": str(profile / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(profile / "AppData" / "Local"),
            "XDG_CACHE_HOME": str(profile / ".cache"),
            "XDG_CONFIG_HOME": str(profile / ".config"),
            "TMPDIR": str(temp),
            "TEMP": str(temp),
            "TMP": str(temp),
            "BAMT_DATA_DIR": str(case / "data"),
            "BAMT_GAME_DIRS_GLOBAL": "",
            "BAMT_GAME_DIRS_JAPAN": "",
            "BAMT_DEV_ROOTS": str(case),
            "BAMT_DEV_WRITABLE": str(case),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env
