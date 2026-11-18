"""Game discovery and isolated data paths, independent of GUI initialization."""
from pathlib import Path
from typing import Literal
from bmpt.core import devmode
from bmpt.host.upgrade_inputs import discover_macos_data
from bmpt.host.game_paths import find_game_dirs

_ba_path_cache: dict[str, str | None] = {}
BARegion = Literal["auto", "global", "japan"]

def get_BA_path(region: BARegion = "auto") -> str | None:
    """获取游戏安装路径，带缓存机制

    Args:
        region: 区服，"auto"（默认）时优先返回 global 的结果，找不到则回退 japan

    Returns:
        游戏安装路径，如果未找到则返回 None
    """
    if region in _ba_path_cache:
        return _ba_path_cache[region]

    if region in ("global", "japan"):
        game_dirs = find_game_dirs(region)
        result = str(game_dirs[0]) if game_dirs else None
    else:  # auto
        result = get_BA_path("global") or get_BA_path("japan")
        return result  # auto 不缓存，保持对注册表变化的响应

    _ba_path_cache[region] = result
    return result

def _get_exe_dir(override: Path | None = None) -> Path:
    """Discover the portable or platform data directory without writing to it."""
    if override is not None:
        return override
    if "__compiled__" not in globals() or not hasattr(__compiled__, "containing_dir"):
        return Path(__file__).resolve().parents[3]
    if getattr(__compiled__, "macos_bundle_mode", False):
        return discover_macos_data(Path.home() / "Library" / "Application Support", strict=False)
    return Path(__compiled__.containing_dir).resolve()

DATA_DIR_OVERRIDE = devmode.data_dir()
EXE_DIR = _get_exe_dir(DATA_DIR_OVERRIDE)
# Module paths stay inside the onefile extraction tree when resources are bundled there.
PACKAGE_DIR = Path(__file__).resolve().parents[1]
